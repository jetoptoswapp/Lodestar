"""真實開 GitLab MR 的 PrOpener（P4）：clone 內 commit → push branch → POST /merge_requests。

對應 github_pr，但走 GitLab API（PRIVATE-TOKEN header、push 用 oauth2:{token}）。
token 不外流（只在 remote url，錯誤訊息不回顯）。冪等（already_opened）+ rollback（push 失敗刪遠端 branch）。
"""
from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

PrOpener = Callable[[int, str, str], str]


class MrError(RuntimeError):
    """開 MR 流程的任何失敗（缺 token / 無變更 / push / API）。訊息保證不含 token。"""


def _git(wt: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(wt), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise MrError(f"git {args[0]} failed (exit {proc.returncode}): {proc.stderr[:200]}")
    return proc


def _create_mr(base: str, token: str, repo: str, head: str, target: str, session_id: int) -> str:
    pid = urllib.parse.quote(repo, safe="")
    url = f"{base}/api/v4/projects/{pid}/merge_requests"
    payload = {
        "source_branch": head, "target_branch": target,
        "title": f"Lodestar implementation (session {session_id})",
        "description": "Automated implementation by Lodestar. Review before merge.",
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json",
                 "User-Agent": "lodestar-impl"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    web = body.get("web_url")
    if not web:
        raise MrError("MR API 回應無 web_url")
    return web


def make_gitlab_mr_opener(*, get_token: Callable[[], str],
                          workdir_for: Callable[[int], Path],
                          base_url: str = "https://gitlab.com",
                          already_opened: Optional[Callable[[int], str]] = None) -> PrOpener:
    base = base_url.rstrip("/")
    host = base.split("://")[-1]

    def open_mr(session_id: int, target_repo: str, last_output: str) -> str:
        if already_opened:
            ex = (already_opened(session_id) or "").strip()
            if ex:
                return ex
        repo = (target_repo or "").strip()
        if "/" not in repo:
            raise MrError(f"target_repo 無效：'{repo}'")
        token = (get_token() or "").strip()
        if not token:
            raise MrError("缺 GitLab token")
        wt = workdir_for(session_id)
        branch = f"lodestar/impl-{session_id}"

        _git(wt, ["add", "-A"])
        if _git(wt, ["diff", "--cached", "--quiet"], check=False).returncode == 0:
            raise MrError("worktree 無變更，不開空 MR")
        _git(wt, ["commit", "-m", f"Lodestar impl session {session_id}"])

        cur = (_git(wt, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout or "").strip()
        base_branch = cur if (cur and cur != branch) else "main"
        remote = f"https://oauth2:{token}@{host}/{repo}.git"
        push = subprocess.run(
            ["git", "-C", str(wt), "push", remote, f"HEAD:{branch}", "--force"],
            capture_output=True, text=True)
        if push.returncode != 0:
            raise MrError(f"git push failed (exit {push.returncode})")   # 不回顯 stderr（含 token url）

        try:
            return _create_mr(base, token, repo, branch, base_branch, session_id)
        except Exception as exc:                       # rollback：刪遠端 branch
            subprocess.run(["git", "-C", str(wt), "push", remote, "--delete", branch],
                           capture_output=True)
            raise MrError(f"開 MR 失敗，已回滾遠端 branch：{exc}") from exc

    return open_mr
