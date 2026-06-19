"""repo_mode=local 的交付 opener（PrOpener 形狀，但**不 push、不開 PR**）。

在本機快照切好的 work branch 上 commit agent 的改動，計算 baseline..HEAD 的乾淨 diff
（baseline = 快照當下含使用者 WIP 的狀態，由 orchestrator.prepare_local_branch 打 tag），
把 diff 記進 impl_messages（kind=event，前端既有 log 顯示）。回傳一段人類可讀的本機交付
描述字串（存進 impl_sessions.pr_url）。

git 操作帶 _SAFE_GIT_CFG + --no-verify，避開使用者本機 hooks / GPG 簽章 / 缺 user.identity。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from async_runtime import impl_dal

PrOpener = Callable[[int, str, str], str]

_SAFE_GIT_CFG = ["-c", "user.email=lodestar@local", "-c", "user.name=Lodestar",
                 "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
_DIFF_CAP = 20000  # 記進 log 的 diff 上限（過大截斷，避免塞爆 log channel）


def _git(wt: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(wt), *args], capture_output=True, text=True)


def make_local_delivery_opener(*, workdir_for: Callable[[int], Path],
                               baseline_tag_for: Callable[[int], str]) -> PrOpener:
    """回 PrOpener：(session_id, target_repo, last_output) -> 本機交付描述字串。"""
    def open_local(session_id: int, target_repo: str, last_output: str) -> str:
        wt = Path(workdir_for(session_id))
        baseline = baseline_tag_for(session_id)

        # **不**釘回 lodestar/impl-{id}：agent（bypassPermissions）常自己 `git checkout -b` 開 feature
        # branch 並 commit 在那；釘回會切走、漏掉成果。直接在「agent 當前所在 branch」收尾，diff 仍以
        # baseline tag 為基準（agent 的 branch 由 baseline 切出 → baseline..HEAD = agent 全部改動）。
        _git(wt, ["add", "-A"])
        _git(wt, _SAFE_GIT_CFG + ["commit", "--no-verify", "-m", f"lodestar impl (session {session_id})"])
        cur = (_git(wt, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout or "").strip() or "(detached)"

        rng = f"{baseline}..HEAD"
        diff = _git(wt, ["diff", rng]).stdout or ""
        stat = (_git(wt, ["diff", "--stat", rng]).stdout or "").strip()

        # 記進 log（kind=event）：前端既有 event 顯示邏輯會渲染
        runs = impl_dal.list_runs(session_id)
        if runs:
            run_id = runs[-1]["run_id"]
            body = diff[:_DIFF_CAP] if diff.strip() else "(baseline..HEAD 無變更)"
            if diff and len(diff) > _DIFF_CAP:
                body += f"\n…（diff 過長，已截斷，完整內容見快照 {wt}）"
            impl_dal.append_message(
                run_id, kind="event",
                content=f"[local delivery] branch {cur} @ {wt}\n{stat}\n\n{body}")

        summary = stat.splitlines()[-1].strip() if stat else "無變更"
        return f"local: {cur} @ {wt} ({summary})"
    return open_local
