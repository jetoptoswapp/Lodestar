"""實作 agent 編排（host 層）。

一個 session = 對某 story 的實作請求。內含 fix-loop：跑 runner → 失敗則帶回饋重試，
**硬上限 MAX_ATTEMPTS=3**（spec §11，避免無限燒）。成功才開 PR（mock 階段回示意 url）。

狀態機（session）：pending → running → succeeded / failed / cancelled。
每次嘗試寫一筆 impl_runs；runner 串流的每行寫進 impl_messages（poll log channel 的來源）。

runner 以「實例注入」方式傳入，故核心 run_implementation 完全可用 mock runner 單元測試；
endpoint 層負責 choice→class→instance 解析 + 背景 task 生命週期。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from plugin_api import AgentRunner, HookAbort, ToolHook
from async_runtime import impl_dal, task_registry
from persistence import dal

_log = logging.getLogger("async_runtime.orchestrator")

MAX_ATTEMPTS = 3            # fix-loop 硬上限（spec §11）
DEFAULT_TIMEOUT = 1800      # 單次 run 上限（秒）

# session_id → 正在跑的 runner 實例，供 cancel 用。完成後 pop。
_ACTIVE_RUNNERS: "dict[int, AgentRunner]" = {}

# 可注入的 PR opener：(session_id, target_repo, last_output) -> pr_url。預設回 mock url。
PrOpener = Callable[[int, str, str], str]


def _default_open_pr(session_id: int, target_repo: str, last_output: str) -> str:
    """mock 階段：不開真實 PR，回示意 url（明確標 MOCK，避免誤認）。"""
    repo = target_repo or "owner/repo"
    return f"https://github.com/{repo}/pull/MOCK-{session_id}"


def work_dir_for(session_id: int) -> Path:
    """每個 session 的工作目錄（mock 不真的寫檔，但子程序 cwd 需存在）。"""
    d = dal.uploads_dir().parent / "impl_work" / str(session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_impl_prompt(story: str, attempt: int, prev_output: str = "") -> str:
    base = (
        "You are an implementation agent. Implement the following user story in the "
        "working directory, then open a pull request. Never push to protected branches "
        "(main/master/release/production).\n\n"
        f"--- STORY ---\n{story.strip() or '(no story provided)'}\n--- END STORY ---\n"
    )
    if attempt > 1 and prev_output:
        base += (
            f"\n[Retry {attempt}/{MAX_ATTEMPTS}] The previous attempt failed. "
            "Fix the problems and try again. Previous output tail:\n"
            f"{prev_output[-1000:]}\n"
        )
    return base


def _status_from_result(result) -> str:
    if result.cancelled:
        return "cancelled"
    if result.timed_out:
        return "timed_out"
    return "succeeded" if result.ok else "failed"


async def run_implementation(
    *,
    session_id: int,
    runner: AgentRunner,
    story: str,
    cwd: str,
    target_repo: str = "",
    hooks: Optional[list[ToolHook]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    open_pr: Optional[PrOpener] = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """fix-loop 核心。回 summary dict（status / attempts / pr_url / reason）。

    重試規則：只有「跑完但失敗（exit≠0）」才重試；cancelled / timed_out 視為終局；
    成功立刻開 PR 收工。HookAbort（如推受保護分支）→ 該 run 記 rejected、session failed。
    """
    hooks = hooks or []
    open_pr = open_pr or _default_open_pr
    impl_dal.update_session(session_id, status="running")

    parent: Optional[int] = None
    prev_output = ""

    for attempt in range(1, max_attempts + 1):
        run_id = impl_dal.create_run(
            session_id=session_id, attempt=attempt,
            runner=runner.name, parent_run_id=parent,
        )

        def _on_log(chunk: str, _rid: int = run_id) -> None:
            impl_dal.append_message(_rid, content=chunk)

        prompt = build_impl_prompt(story, attempt, prev_output)

        try:
            result = await runner.run(
                cwd=cwd, prompt=prompt, timeout=timeout,
                on_log=_on_log, hooks=hooks,
            )
        except HookAbort as exc:
            impl_dal.append_message(run_id, kind="system", content=f"[hook:{exc.hook_name}] {exc.reason}")
            impl_dal.finish_run(run_id, status="rejected", exit_code=None,
                                cancelled=False, timed_out=False, last_output=str(exc))
            impl_dal.update_session(session_id, status="failed", error_message=str(exc))
            _log.info("session %s rejected by hook %s", session_id, exc.hook_name)
            return {"status": "failed", "reason": "hook_abort",
                    "hook": exc.hook_name, "attempts": attempt}

        run_status = _status_from_result(result)
        impl_dal.finish_run(run_id, status=run_status, exit_code=result.exit_code,
                            cancelled=result.cancelled, timed_out=result.timed_out,
                            last_output=result.last_output)

        if result.ok:
            pr_url = ""
            try:
                pr_url = open_pr(session_id, target_repo, result.last_output)
            except Exception as exc:  # noqa: BLE001 - PR 失敗不該炸掉整個 run
                impl_dal.append_message(run_id, kind="system", content=f"[pr] open failed: {exc}")
                impl_dal.update_session(session_id, status="failed",
                                        error_message=f"PR open failed: {exc}")
                return {"status": "failed", "reason": "pr_failed", "attempts": attempt}
            impl_dal.update_session(session_id, status="succeeded", pr_url=pr_url)
            _log.info("session %s succeeded on attempt %s → %s", session_id, attempt, pr_url)
            return {"status": "succeeded", "attempts": attempt, "pr_url": pr_url}

        if result.cancelled:
            impl_dal.update_session(session_id, status="cancelled")
            return {"status": "cancelled", "attempts": attempt}

        if result.timed_out:
            impl_dal.update_session(session_id, status="failed", error_message="timed out")
            return {"status": "failed", "reason": "timed_out", "attempts": attempt}

        # 純失敗 → 帶回饋重試（若還有額度）
        parent = run_id
        prev_output = result.last_output

    impl_dal.update_session(session_id, status="failed",
                            error_message=f"{max_attempts} 次嘗試後仍失敗")
    _log.info("session %s failed after %s attempts", session_id, max_attempts)
    return {"status": "failed", "reason": "max_attempts", "attempts": max_attempts}


def start_session(
    *,
    thread_id: str,
    story: str,
    runner: AgentRunner,
    runner_choice: str,
    target_repo: str = "",
    title: str = "",
    hooks: Optional[list[ToolHook]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    open_pr: Optional[PrOpener] = None,
) -> int:
    """建立 session + 背景跑 fix-loop。立刻回 session_id（非阻塞）。

    runner 先登記到 _ACTIVE_RUNNERS（讓 cancel 在整個生命週期都能找到它），
    背景 task 收尾時於 finally pop。task 強引用由 task_registry 持有（防 GC）。
    """
    session_id = impl_dal.create_session(
        thread_id=thread_id, title=title or "(implementation)",
        target_repo=target_repo, runner=runner_choice,
    )
    cwd = str(work_dir_for(session_id))
    _ACTIVE_RUNNERS[session_id] = runner

    async def _supervised() -> dict:
        try:
            return await run_implementation(
                session_id=session_id, runner=runner, story=story, cwd=cwd,
                target_repo=target_repo, hooks=hooks, timeout=timeout, open_pr=open_pr,
            )
        finally:
            _ACTIVE_RUNNERS.pop(session_id, None)

    task_registry.spawn(_supervised(), name=f"impl-{session_id}")
    return session_id


async def request_cancel(session_id: int) -> bool:
    """要求取消正在跑的 session。回傳是否找到 active runner。"""
    runner = _ACTIVE_RUNNERS.get(session_id)
    if runner is None:
        return False
    await runner.cancel()
    return True
