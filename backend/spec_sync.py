"""規格同步到 code repo（host 層）：把 PRD / 架構 / UI 設計 + CLAUDE.md 規則 commit 進
專案的 code repo 根目錄，讓自動實作 agent（claude-cli 在 clone 內工作）讀得到設計與規矩。

與 docs_publisher 的差異：
- docs_publisher 推到 **Wiki repo**（{repo}.wiki.git，給人看）；本模組推到 **code repo**
  （{repo}.git 的 default branch，給實作 agent 讀）。兩者並存、互不取代。
- code repo 必已存在（new-mode 由 resolve_project_repo lazy 建），故無需 wiki 的 empty-init bootstrap。

設計鐵則（spec §2）：host owns all I/O；plugin 不得 import。token 由呼叫端經 keystore 組進
remote url（delivery_repo.clone_url），錯誤訊息一律不回顯 stderr（含 token url）。

CLAUDE.md 用 delimited managed block（LODESTAR:BEGIN/END）寫入：既有檔只換 block、不清掉
使用者既有內容（modify_existing 既有 repo 可能已有自己的 CLAUDE.md）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import repo_workspace

_BLOCK_BEGIN = "<!-- LODESTAR:BEGIN -->"
_BLOCK_END = "<!-- LODESTAR:END -->"
_MANAGED_BLOCK_RE = re.compile(
    re.escape(_BLOCK_BEGIN) + r".*?" + re.escape(_BLOCK_END),
    re.DOTALL,
)


class SpecSyncError(RuntimeError):
    """規格同步失敗（缺 token / clone / push / 保護分支）。訊息保證不含 token。"""


# ============================================================
#  git helpers（自帶以保模組解耦；風格對齊 docs_publisher）
# ============================================================
def _git(cwd: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=180)
    if check and proc.returncode != 0:
        raise SpecSyncError(f"git {args[0]} failed (exit {proc.returncode})")  # 不回顯 stderr
    return proc


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content or "", encoding="utf-8")


def _fresh_dir(thread_id: str) -> Path:
    """每次同步用乾淨工作目錄（避免殘留 / 上次失敗的半成品）。"""
    dest = repo_workspace.project_dir(thread_id) / "spec_sync"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def merge_managed_block(existing: str, block_body: str) -> str:
    """把 LODESTAR managed block 併入既有 CLAUDE.md：有 block 換之、無則接在最前。

    block_body 不含 BEGIN/END 標記；本函式負責包裹。既有檔的非 block 內容原封保留。"""
    block = f"{_BLOCK_BEGIN}\n{block_body.strip()}\n{_BLOCK_END}"
    existing = existing or ""
    if _MANAGED_BLOCK_RE.search(existing):
        return _MANAGED_BLOCK_RE.sub(block, existing, count=1).strip() + "\n"
    if existing.strip():
        return f"{block}\n\n{existing.strip()}\n"
    return block + "\n"


# ============================================================
#  分派入口
# ============================================================
def sync_specs(
    thread_id: str, remote: str, files: dict[str, str], *,
    web_url: str, commit_message: str = "docs: 同步 Lodestar 規格到 repo",
    claude_md_block: str = "", readme: str = "",
) -> dict:
    """clone code repo → 寫規格檔（+ CLAUDE.md managed block + README）→ commit → push default branch。

    files：{相對路徑: 內容}，如 {".lodestar/PRD.md": ...}。
    claude_md_block：非空 → 寫/併入根 CLAUDE.md 的 managed block（既有內容保留）。
    readme：非空 → repo 無 README 則寫；已有但仍 Lodestar 受管則升級覆寫；已被人工充實則不動。
    回 {"ok", "url", "note", "files": [...]}。失敗 raise SpecSyncError（訊息不含 token）。
    """
    if not remote:
        raise SpecSyncError("缺 token（先到 INTEGRATIONS 設定該 target 的憑證）")

    dest = _fresh_dir(thread_id)
    clone = subprocess.run(["git", "clone", "--depth", "1", remote, str(dest)],
                           capture_output=True, text=True, timeout=180)
    if clone.returncode != 0:
        # 不回顯 stderr（含 token url）。最常見：repo 不存在 / 無權限。
        raise SpecSyncError("無法 clone code repo（確認 repo 已建立且 token 有寫入權限）")

    written: list[str] = []
    for rel, content in files.items():
        _write(dest / rel, content)
        written.append(rel)

    if claude_md_block:
        claude_path = dest / "CLAUDE.md"
        existing = claude_path.read_text(encoding="utf-8") if claude_path.exists() else ""
        _write(claude_path, merge_managed_block(existing, claude_md_block))
        written.append("CLAUDE.md")

    # README：repo 尚無 README → 寫；已有但仍是 Lodestar 受管（含 managed marker 或舊 stub 簽名）
    # → 升級覆寫（每次 re-sync 反映最新 PRD/架構）；已被人工/實作 agent 充實（無 marker）→ 絕不覆寫。
    readme_path = dest / "README.md"
    if readme:
        existing_readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else None
        if existing_readme is None or _readme_is_managed(existing_readme):
            _write(readme_path, readme)
            written.append("README.md")

    _git(dest, ["add", "-A"])
    if _git(dest, ["diff", "--cached", "--quiet"], check=False).returncode == 0:
        # 無變更（冪等重同步、內容相同）→ 不產空 commit，視為成功
        return {"ok": True, "url": web_url, "note": "規格已是最新，無變更。", "files": written}

    _git(dest, ["-c", "user.email=lodestar@local", "-c", "user.name=Lodestar",
                "commit", "-m", commit_message])
    branch = (_git(dest, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout or "main").strip()
    push = subprocess.run(["git", "-C", str(dest), "push", remote, f"HEAD:{branch}"],
                          capture_output=True, text=True, timeout=180)
    if push.returncode != 0:
        # 最常見：default branch 受保護（push 被拒）。不回顯 stderr（含 token url）。
        raise SpecSyncError(
            f"推送 repo 失敗，請確認 default branch（{branch}）允許直接 push，或暫時放寬保護規則")
    return {
        "ok": True,
        "url": web_url,
        "note": f"已同步 {len(written)} 個檔到 {branch}，實作 agent clone 後即可讀取。",
        "files": written,
    }


# ============================================================
#  內容生成（CLAUDE.md 規則 / 專案記憶）
# ============================================================
def build_claude_md_block(project_name: str, *, has_ui: bool) -> str:
    """產生 CLAUDE.md 的 LODESTAR managed block 內容（不含 BEGIN/END 標記）。

    這是「專案記憶 + 實作規矩」：實作 agent（claude-cli）clone 後會自動讀 CLAUDE.md。"""
    ui_line = (
        "- 任何畫面/UI：對齊 `.lodestar/UI-DESIGN.md` 的 design tokens 與版面；"
        "沿用既有視覺語言，不要自創一套。\n"
        if has_ui else ""
    )
    ui_doc = "\n- `.lodestar/UI-DESIGN.md` — UI 設計稿（design tokens + 各畫面 HTML 原型）" if has_ui else ""
    return (
        f"# {project_name}\n"
        "\n"
        "> 本 repo 由 **Lodestar** 的自動化實作 agent 開發。下列規格與規矩為實作依據。\n"
        "\n"
        "## 規格（先讀再動手）\n"
        "- `.lodestar/PRD.md` — 產品需求（FR / NFR / OPS）\n"
        "- `.lodestar/ARCHITECTURE.md` — 系統架構（tier / tech stack / 模組）"
        f"{ui_doc}\n"
        "\n"
        "## 實作規矩\n"
        "- 只實作被指派的那一個 user story，不要順手做別的；變更範圍最小化。\n"
        "- 動手前先讀上述 `.lodestar/` 規格與本檔；story 內的 `Reference: UI Design — Screen: X` 指向設計稿對應畫面。\n"
        f"{ui_line}"
        "- 遵循 repo 既有的程式風格、命名與目錄慣例；不要引入無關的重構。\n"
        "- 驗收條件（AC）要能跑得過；新增功能要附對應測試。\n"
        "- 維護根目錄 `README.md`：依 `.lodestar/PRD.md` 寫出本專案的用途、主要功能、技術棧、"
        "安裝與啟動方式。README 必須反映實際專案內容，不可留樣板或佔位字。\n"
        "- 絕不直接 push 到保護分支（main / master / release / production）——一律開 PR / MR。\n"
    )


# 舊版 build_readme_starter 寫的 stub 簽名（無 marker）——用來把既有專案的一行 stub 也認成受管、可升級。
_LEGACY_README_SIGNATURE = "本 README 由實作 agent 依"


def _readme_is_managed(text: str) -> bool:
    """README 是否仍由 Lodestar 受管 / 仍是空殼（可安全升級覆寫）。

    受管 = 含 managed marker（新版）或舊 stub 簽名（legacy）或只有一行 H1 標題的空殼
    （多為 GitHub/GitLab 建 repo 時 auto-init 的 `# RepoName`）。一旦人工/實作 agent 寫成
    有內容的正式 README（移除 marker、加了實質內容），即視為非受管，re-sync 絕不覆寫。"""
    t = text or ""
    if _BLOCK_BEGIN in t or _LEGACY_README_SIGNATURE in t:
        return True
    # host auto-init 空殼：去掉空行後只剩單一行 H1（`# Foo`）
    lines = [ln for ln in t.splitlines() if ln.strip()]
    return len(lines) == 1 and lines[0].lstrip().startswith("#")


def _prd_features(prd: str, limit: int = 16) -> list[str]:
    """從 PRD 抽功能群名稱：`### FR-001 Workspace Management **[P0]**` → `Workspace Management`。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"(?m)^#{2,4}\s+`?FR-[\d.]+`?\s+(.+?)\s*$", prd or ""):
        name = re.sub(r"\*\*\[[^\]]*\]\*\*|`", "", m.group(1)).strip()
        name = re.sub(r"\s+", " ", name)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
        if len(out) >= limit:
            break
    return out


def _arch_tier(arch: str) -> str:
    """抽架構文件的 tier 宣告整句（`**Project tier**: T1 — …`）。無則回 ""。"""
    m = re.search(r"(?im)^\s*\*\*Project tier\*\*:\s*(.+?)\s*$", arch or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _arch_tech_stack(arch: str, limit: int = 8) -> list[str]:
    """抽架構文件「技術棧 / Tech Stack」章節下的 bullet（找得到才有，否則回空）。"""
    m = re.search(r"(?im)^#+\s*.*(tech\s*stack|技術選型|技術棧|technology stack).*$", arch or "")
    if not m:
        return []
    out: list[str] = []
    for raw in arch[m.end():].splitlines():
        ln = raw.strip()
        if ln.startswith("#"):
            break
        if ln.startswith(("-", "*")):
            item = re.sub(r"\s+", " ", ln.lstrip("-* ").strip())
            if item:
                out.append(item)
        if len(out) >= limit:
            break
    return out


def _prd_overview(prd: str, limit: int = 400) -> str:
    """從 PRD 抽一段簡短概述給 README starter：取第一段非標題、非清單的內文。"""
    if not prd or not prd.strip():
        return ""
    para: list[str] = []
    for raw in prd.splitlines():
        ln = raw.strip()
        if not ln:
            if para:
                break          # 收完第一段就停
            continue
        if ln.startswith(("#", "-", "*", ">", "|", "```")):
            if para:
                break
            continue           # 跳過開頭的標題 / 清單，找第一段散文
        para.append(ln)
    text = " ".join(para).strip()
    return (text[:limit].rstrip() + "…") if len(text) > limit else text


def build_readme(project_name: str, prd: str, architecture: str = "") -> str:
    """依 PRD + 架構確定性產出有內容的 README（用途/功能/技術/規格/開發）。

    用 managed marker 包起來：re-sync 會升級這份受管 README，但人工/實作 agent 改寫（移除 marker）
    後就不再覆寫。內容全部從 PRD/架構抽取，不靠 LLM、不留樣板佔位字。"""
    parts: list[str] = [f"# {project_name}", ""]

    overview = _prd_overview(prd)
    if overview:
        parts += [overview, ""]

    features = _prd_features(prd)
    if features:
        parts.append("## 主要功能")
        parts += [f"- {f}" for f in features]
        parts.append("")

    tier = _arch_tier(architecture)
    stack = _arch_tech_stack(architecture)
    if tier or stack:
        parts.append("## 技術")
        if tier:
            parts.append(f"- 架構層級：{tier}")
        parts += [f"- {s}" for s in stack]
        parts.append("")

    parts += [
        "## 規格文件",
        "完整規格見 `.lodestar/`：`.lodestar/PRD.md`（需求）、"
        "`.lodestar/ARCHITECTURE.md`（架構）、`.lodestar/UI-DESIGN.md`（UI 設計，若有）。",
        "",
        "## 開發",
        "本 README 由 **Lodestar** 依規格自動生成。安裝與啟動方式請以實際程式碼為準——"
        "實作 agent 會在動工後依專案內容補上（屆時本檔即由 agent 接手維護）。",
    ]

    body = "\n".join(parts).rstrip() + "\n"
    # 包 managed marker（HTML 註解，render 時不顯示）：標記為 Lodestar 受管、可被 re-sync 升級。
    return f"{_BLOCK_BEGIN}\n{body}{_BLOCK_END}\n"
