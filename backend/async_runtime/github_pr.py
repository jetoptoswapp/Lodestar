"""真實開 PR 的 PrOpener（坎2）：隔離 worktree commit → push branch → POST /pulls。

複用 builtin_integrations._publish_github 的 urllib + Bearer PAT 模式（改打 /pulls）。
token 由 keystore 提供（不外流：push 用 token 注入 url，錯誤訊息不回顯 url）。

安全與韌性：
- 無變更 → 不開空 PR（PrError）。
- 冪等：already_opened 回非空 → 直接回該 url（重按 approve 不重複開）。
- rollback：push 成功但 PR API 失敗 → 刪遠端 branch、PrError。
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable, Optional

PrOpener = Callable[[int, str, str], str]


class PrError(RuntimeError):
    """開 PR 流程的任何失敗（缺 token / 無變更 / push / API）。訊息保證不含 token。"""


def _git(wt: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(wt), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise PrError(f"git {args[0]} failed (exit {proc.returncode}): {proc.stderr[:200]}")
    return proc


def _create_pr(repo: str, token: str, head: str, base: str, session_id: int) -> str:
    url = f"https://api.github.com/repos/{repo}/pulls"
    payload = {
        "title": f"Lodestar implementation (session {session_id})",
        "head": head, "base": base,
        "body": "Automated implementation by Lodestar. Review before merge.",
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lodestar-impl",
            "Content-Type": "application/json",
        }, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    pr_url = body.get("html_url")
    if not pr_url:
        raise PrError("PR API 回應無 html_url")
    return pr_url


def make_github_pr_opener(*, get_token: Callable[[], str],
                          workdir_for: Callable[[int], Path],
                          base_branch: str = "main",
                          already_opened: Optional[Callable[[int], str]] = None) -> PrOpener:
    """組一個真實開 PR 的 PrOpener。

    get_token：回 GitHub PAT（通常 keystore.get_credentials('github')['token']）。
    workdir_for：session_id → worktree 路徑（agent 改檔處，已在 branch lodestar/impl-{id}）。
    """
    def open_pr(session_id: int, target_repo: str, last_output: str) -> str:
        if already_opened:
            existing = (already_opened(session_id) or "").strip()
            if existing:
                return existing                       # 冪等
        repo = (target_repo or "").strip()
        if "/" not in repo:
            raise PrError(f"target_repo 無效：'{repo}'")
        token = (get_token() or "").strip()
        if not token:
            raise PrError("缺 GitHub token（keystore 無 github 憑證）")
        wt = workdir_for(session_id)
        branch = f"lodestar/impl-{session_id}"

        _git(wt, ["add", "-A"])
        if _git(wt, ["diff", "--cached", "--quiet"], check=False).returncode == 0:
            raise PrError("worktree 無變更，不開空 PR")
        _git(wt, ["commit", "-m", f"Lodestar impl session {session_id}"])

        # base：clone 模式本地在 default branch（main/master）→ 當 base；worktree 模式本地已在
        # work branch（== branch）→ 用參數 base_branch。head 一律推當前 commit 上去（HEAD:）。
        cur = (_git(wt, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout or "").strip()
        base = base_branch if (not cur or cur == branch) else cur
        remote = f"https://x-access-token:{token}@github.com/{repo}.git"
        push = subprocess.run(
            ["git", "-C", str(wt), "push", remote, f"HEAD:{branch}", "--force"],
            capture_output=True, text=True)
        if push.returncode != 0:
            raise PrError(f"git push failed (exit {push.returncode})")   # 不回顯 stderr（含 token url）

        try:
            return _create_pr(repo, token, branch, base, session_id)
        except Exception as exc:                       # rollback：刪遠端 branch
            subprocess.run(["git", "-C", str(wt), "push", remote, "--delete", branch],
                           capture_output=True)
            raise PrError(f"開 PR 失敗，已回滾遠端 branch：{exc}") from exc

    return open_pr
