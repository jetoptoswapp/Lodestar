"""文件發佈（host 層）：把 PRD / Architecture 推到 GitHub Wiki / GitLab Wiki。

設計鐵則（spec §2）：host owns all I/O。plugin 不得 import。與 delivery_repo /
repo_workspace 同級的純 host 模組（stdlib + git subprocess，無 runtime 依賴）。

兩個 target 都走「Wiki」——push 完即時可在介面直接看 markdown，免 build / pipeline：
- GitHub → {repo}.wiki.git，介面 /wiki。
- GitLab → {repo}.wiki.git，介面 /-/wikis/home。
wiki 是獨立 git repo；空 wiki（從未建頁）則自己 init 一個再 push 來 bootstrap。

token 由呼叫端（endpoint）經 keystore 提供，組進 remote url；token 只在 url，
錯誤訊息一律不回顯 stderr（含 token url），沿用 repo_workspace / github_pr 慣例。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import repo_workspace


class DocsPublishError(RuntimeError):
    """文件發佈失敗（缺 token / clone / push / wiki 未啟用）。訊息保證不含 token。"""


# ============================================================
#  git helpers
# ============================================================
def _git(cwd: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=180)
    if check and proc.returncode != 0:
        raise DocsPublishError(f"git {args[0]} failed (exit {proc.returncode})")  # 不回顯 stderr
    return proc


def _commit_all(dest: Path, message: str) -> None:
    """stage 全部並 commit；無變更則略過（冪等重發、內容相同時不產空 commit）。"""
    _git(dest, ["add", "-A"])
    if _git(dest, ["diff", "--cached", "--quiet"], check=False).returncode == 0:
        return
    _git(dest, ["-c", "user.email=lodestar@local", "-c", "user.name=Lodestar",
                "commit", "-m", message])


def _fresh_dir(thread_id: str, name: str) -> Path:
    """每次發佈用乾淨工作目錄（避免殘留 / 上次失敗的半成品）。"""
    dest = repo_workspace.project_dir(thread_id) / name
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content or "", encoding="utf-8")


# ============================================================
#  分派入口
# ============================================================
def publish_docs(thread_id: str, target: str, repo: str, creds: dict, docs: dict[str, str]) -> dict:
    """依 target 把文件發到對應 Wiki。

    docs：{頁名: <markdown>}，如 {"PRD": ..., "Architecture": ..., "UI-Design": ...}；
    每個 key 寫成 wiki 一頁（{key}.md），Home 自動列出連結。
    回 {"ok": bool, "url": str, "note": str}。失敗 raise DocsPublishError（訊息不含 token）。
    """
    token = (creds.get("token") or "").strip()
    if not token:
        raise DocsPublishError(f"缺 {target} token（先到 INTEGRATIONS 設定）")
    if "/" not in repo:
        raise DocsPublishError(f"repo 格式無效：'{repo}'（需 owner/repo 或 group/project）")

    if target == "github":
        remote = f"https://x-access-token:{token}@github.com/{repo}.wiki.git"
        return _publish_wiki(
            thread_id, remote, docs, home_name="Home.md",
            web_url=f"https://github.com/{repo}/wiki",
            settings_hint="Settings → Features → Wikis",
            can_bootstrap=False)   # GitHub wiki 須先在網頁建第一頁，push 無法憑空建
    if target == "gitlab":
        host = (creds.get("base_url") or "https://gitlab.com").rstrip("/")
        netloc = host.split("://")[-1]
        remote = f"https://oauth2:{token}@{netloc}/{repo}.wiki.git"
        return _publish_wiki(
            thread_id, remote, docs, home_name="home.md",
            web_url=f"{host}/{repo}/-/wikis/home",
            settings_hint="Settings → General → Visibility → Wiki",
            can_bootstrap=True)    # GitLab：push 即可建立 wiki repo
    raise DocsPublishError(f"文件發佈僅支援 github / gitlab（Wiki），不支援 '{target}'")


# ============================================================
#  Wiki（github / gitlab 共用：兩者都是 {repo}.wiki.git）
# ============================================================
def _publish_wiki(thread_id: str, remote: str, docs: dict[str, str], *,
                  home_name: str, web_url: str, settings_hint: str,
                  can_bootstrap: bool = True) -> dict:
    """clone wiki → 寫頁 → push。push 完即時可在介面直接看。

    can_bootstrap：clone 失敗（wiki git repo 不存在）時可否用 init+push 憑空建立。
      - GitLab=True：push 即可建立 wiki repo。
      - GitHub=False：GitHub 的 .wiki.git 必須先在網頁建第一頁才存在，push 無法憑空建
        （症狀：remote: Repository not found）→ 直接回精準錯誤，不做無用的 init+push。
    """
    dest = _fresh_dir(thread_id, "wiki")
    clone = subprocess.run(["git", "clone", remote, str(dest)],
                           capture_output=True, text=True, timeout=120)
    if clone.returncode != 0:
        if not can_bootstrap:
            # GitHub wiki 尚未初始化：給可直接照做的指引（不回顯 stderr / token）。
            raise DocsPublishError(
                f"GitHub Wiki 尚未建立，無法發佈。請先到 {web_url} 點「Create the first page」"
                f"手動建一頁（隨意內容，之後會被覆寫）；若連 Wiki 分頁都沒有，先到 {settings_hint} "
                "開啟 Wikis。建好第一頁後再回來發佈即可。")
        # GitLab：wiki 從未建頁（git repo 尚未初始化）→ 自己 init 一個再 push 來 bootstrap
        dest.mkdir(parents=True, exist_ok=True)
        _git(dest, ["init", "-b", "master"])
        _git(dest, ["remote", "add", "origin", remote])

    # wiki 連結用「頁名」（無 .md）；Home/home 為 landing page
    for name, content in docs.items():
        _write(dest / f"{name}.md", content or "")
    links = "\n".join(f"- [{name}]({name})" for name in docs)
    _write(dest / home_name, f"# 專案文件\n\n由 Lodestar 自動發佈。\n\n{links}\n")

    _commit_all(dest, "docs: 更新專案文件（Lodestar）")
    branch = (_git(dest, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout or "master").strip()
    push = subprocess.run(["git", "-C", str(dest), "push", remote, f"HEAD:{branch}"],
                          capture_output=True, text=True, timeout=120)
    if push.returncode != 0:
        # 最常見原因：repo 未啟用 Wiki（push 被拒）。不回顯 stderr（含 token url）。
        raise DocsPublishError(f"推送 Wiki 失敗，請確認該 repo 已啟用 Wiki（{settings_hint}）")
    return {
        "ok": True,
        "url": web_url,
        "note": "已發佈到 Wiki，可直接點開查看。",
    }
