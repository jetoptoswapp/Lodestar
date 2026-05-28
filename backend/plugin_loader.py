"""PluginLoader —— 啟動時、DB migration 後執行（spec §5.2）。

流程：
  1. discover：掃 backend/plugins/*/plugin.toml（內建與第三方同機制；不給內建開後門）。
  2. host_api 檢查：semver range 不符 → skip + warn，不打掛 app。
  3. 拓樸排序：依 requires_plugins；遇環 / 缺依賴 → skip + 明確 log。
  4. 兩階段註冊：先對所有 plugin 呼叫 register(host) 收集 spec，再 cross-reference
     驗證（workflow 引用的 stage_id 必須都已註冊）；失敗者 skip + warn。
  5. 隔離失敗：單一 plugin 的 import / register 例外不影響其他 plugin 或 app 啟動。

零新依賴：manifest 用內建 tomllib 解析；semver range 自己比對。
"""
from __future__ import annotations

import importlib
import logging
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from persistence import dal
from plugin_api.host import PluginManifest
from plugin_host import CAP_WORKFLOW, PluginHost, PluginLoadInfo, Registry

log = logging.getLogger("plugin_loader")

HOST_API_VERSION = (1, 0, 0)
_BACKEND_DIR = Path(__file__).resolve().parent
_PLUGINS_DIR = _BACKEND_DIR / "plugins"

# 內建 plugin（固定清單，僅用於標記來源 / UI badge；載入機制與第三方完全相同）
BUILTIN_PLUGIN_IDS = {"builtin_integrations", "builtin_core_stages", "builtin_agents", "builtin_models"}


@dataclass
class DiscoveredPlugin:
    manifest: PluginManifest
    directory: Path


def _ensure_backend_on_path() -> None:
    p = str(_BACKEND_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------- semver range（零新依賴）----------
def _parse_version(v: str) -> tuple[int, ...]:
    nums = tuple(int(x) for x in v.strip().split(".") if x.strip().isdigit())
    return nums or (0,)


def _cmp(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def host_api_satisfied(spec: str, version: tuple[int, ...] = HOST_API_VERSION) -> bool:
    """spec 形如 '>=1.0,<2.0'，逐條件 AND。支援 >= <= == > <。無法解析 → 視為不符。"""
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        for op in (">=", "<=", "==", ">", "<"):
            if clause.startswith(op):
                c = _cmp(version, _parse_version(clause[len(op):]))
                ok = {">=": c >= 0, "<=": c <= 0, "==": c == 0, ">": c > 0, "<": c < 0}[op]
                if not ok:
                    return False
                break
        else:
            log.warning("無法解析 host_api 條件 '%s'", clause)
            return False
    return True


# ---------- discover ----------
def _read_manifest(toml_path: Path) -> PluginManifest:
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    p = data["plugin"]
    return PluginManifest(
        id=p["id"],
        name=p.get("name", p["id"]),
        version=p.get("version", "0.0.0"),
        description=p.get("description", ""),
        host_api=p.get("host_api", ">=1.0,<2.0"),
        entry_module=p["entry_module"],
        requires_plugins=tuple(p.get("requires_plugins", [])),
        contributes=data.get("contributes", {}),
    )


def discover() -> list[DiscoveredPlugin]:
    found: list[DiscoveredPlugin] = []
    if not _PLUGINS_DIR.exists():
        return found
    for d in sorted(_PLUGINS_DIR.iterdir()):
        toml_path = d / "plugin.toml"
        if not toml_path.is_file():
            continue
        try:
            found.append(DiscoveredPlugin(_read_manifest(toml_path), d))
        except Exception as e:  # manifest 壞掉不影響其他
            log.warning("plugin manifest 解析失敗，skip '%s'：%s", d.name, e)
    return found


# ---------- 拓樸排序 ----------
def _toposort(plugins: list[DiscoveredPlugin]) -> list[DiscoveredPlugin]:
    by_id = {p.manifest.id: p for p in plugins}
    order: list[DiscoveredPlugin] = []
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(pid: str, stack: list[str]) -> bool:
        if pid in done:
            return True
        if pid in visiting:
            log.warning("plugin 依賴成環，skip：%s", " -> ".join(stack + [pid]))
            return False
        if pid not in by_id:
            log.warning("plugin '%s' 缺少依賴 '%s'，skip", stack[-1] if stack else pid, pid)
            return False
        visiting.add(pid)
        for dep in by_id[pid].manifest.requires_plugins:
            if not visit(dep, stack + [pid]):
                visiting.discard(pid)
                return False
        visiting.discard(pid)
        done.add(pid)
        order.append(by_id[pid])
        return True

    for p in plugins:
        visit(p.manifest.id, [])
    return order


# ---------- load ----------
def load_all(registry: Registry | None = None) -> Registry:
    """執行完整載入流程，回傳填好的 Registry（含每個 plugin 的載入報告）。
    失敗的 plugin 被隔離，不影響其他 plugin 或 app 啟動。"""
    _ensure_backend_on_path()
    registry = registry if registry is not None else Registry()

    discovered = discover()
    info: dict[str, PluginLoadInfo] = {
        p.manifest.id: PluginLoadInfo(manifest=p.manifest, loaded=False) for p in discovered
    }

    # 2. host_api 檢查
    eligible = []
    for p in discovered:
        if host_api_satisfied(p.manifest.host_api):
            eligible.append(p)
        else:
            msg = "host_api '%s' 與 host %s 不相容" % (
                p.manifest.host_api, ".".join(map(str, HOST_API_VERSION)))
            log.warning("skip '%s'：%s", p.manifest.id, msg)
            info[p.manifest.id].error = msg

    # 3. 拓樸排序（遇環 / 缺依賴者不在 ordered 中）
    ordered = _toposort(eligible)
    ordered_ids = {p.manifest.id for p in ordered}
    for p in eligible:
        if p.manifest.id not in ordered_ids and not info[p.manifest.id].error:
            info[p.manifest.id].error = "依賴無法解析（成環或缺依賴）"

    # 4a. 第一階段：register(host) 收集 spec（單一失敗隔離）
    for p in ordered:
        try:
            module = importlib.import_module(p.manifest.entry_module)
            register = getattr(module, "register", None)
            if register is None:
                raise AttributeError(f"entry_module '{p.manifest.entry_module}' 缺 register(host)")
            register(PluginHost(p.manifest.id, registry))
            info[p.manifest.id].loaded = True
            log.info("loaded plugin: %s v%s (%s)", p.manifest.id, p.manifest.version, p.manifest.name)
        except Exception as e:
            log.warning("plugin '%s' 載入失敗，已隔離（不影響其他）：%s", p.manifest.id, e)
            info[p.manifest.id].error = str(e)
            registry.remove_plugin(p.manifest.id)

    # 4b. 第二階段：cross-reference 驗證（workflow 引用的 stage 必須都已註冊）
    for wid in list(registry.workflows.keys()):
        wf = registry.workflows[wid]
        missing = [s for s in wf.stages if s not in registry.stages]
        if missing:
            log.warning("workflow '%s' 引用未註冊 stage %s → skip 該 workflow", wid, missing)
            registry.workflows.pop(wid, None)
            registry.contributions = [
                c for c in registry.contributions
                if not (c[1] == CAP_WORKFLOW and c[2] == wid)
            ]

    # 5. contribution 落地到 DB（驗證後）：先清掉本輪涉及的 plugin 舊紀錄再寫
    for pid in {c[0] for c in registry.contributions}:
        dal.clear_contributions(pid)
    for (pid, ctype, cid) in registry.contributions:
        dal.record_contribution(pid, ctype, cid)

    registry.loaded_plugins = list(info.values())
    return registry
