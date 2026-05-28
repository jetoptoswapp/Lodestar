"""兩層 runtime 隔離 guard（spec §11）：AST 掃 plugins/*，斷言 plugin 只 import
plugin_api（+ 標準庫 / 第三方），不得 import host 內部模組（含未來 M5 的 async 實作
runtime）。這是「plugin 拿不到 conn / async runtime」鐵則的自動化防線。"""
from __future__ import annotations

import ast
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_PLUGINS = _BACKEND / "plugins"

# plugin 不得 import 的 host 內部 top-level 模組（含 M5 的 async 實作 runtime）
FORBIDDEN_HOST_MODULES = {
    "plugin_host",
    "plugin_loader",
    "persistence",
    "workflow_engine",
    "harness_runner",
    "app",
    "api_models",
    "api_errors",
    "model_adapters",
    "implement_runtime",   # M5：async long-running runtime（預留）
    "impl_runtime",
}


def _imported_top_modules(py: Path) -> set[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                mods.add(node.module.split(".")[0])
    return mods


def test_plugins_only_import_plugin_api():
    offenders: dict[str, list[str]] = {}
    for py in _PLUGINS.rglob("*.py"):
        bad = _imported_top_modules(py) & FORBIDDEN_HOST_MODULES
        if bad:
            offenders[str(py.relative_to(_BACKEND))] = sorted(bad)
    assert not offenders, f"plugin 不得 import host 內部模組: {offenders}"
