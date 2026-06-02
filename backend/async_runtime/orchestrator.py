"""實作 agent 編排（host 層）。

一個 session = 對某 story 的實作請求。內含 fix-loop：跑 runner → 失敗則帶回饋重試，
**硬上限 MAX_ATTEMPTS=3**（spec §11，避免無限燒）。成功才開 PR（mock 階段回示意 url）。

狀態機（session）：pending → running → succeeded / failed / cancelled。
每次嘗試寫一筆 impl_runs；runner 串流的每行寫進 impl_messages（poll log channel 的來源）。

runner 以「實例注入」方式傳入，故核心 run_implementation 完全可用 mock runner 單元測試；
endpoint 層負責 choice→class→instance 解析 + 背景 task 生命週期。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
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

# 可注入的 persona 提供者：(role_step) -> persona 文字（無綁定回 ""）。
# 由 endpoint 層依該 thread 生效 workflow 的 agent_bindings["implement"] 組好；
# None 或回空字串 → 該步驟用內建預設 persona（零行為改變）。
PersonaProvider = Callable[[str], str]

# 可注入的 per-step runner 提供者：(role_step) -> AgentRunner（依該步綁定 agent 的 model_choice）。
# 回 None → 該步驟用傳入的預設 runner（model_choice 無對應已註冊/可用 runner 時亦退回，見 endpoint）。
RunnerProvider = Callable[[str], Optional[AgentRunner]]


def _default_open_pr(session_id: int, target_repo: str, last_output: str) -> str:
    """mock 階段：不開真實 PR，回示意 url（明確標 MOCK，避免誤認）。"""
    repo = target_repo or "owner/repo"
    return f"https://github.com/{repo}/pull/MOCK-{session_id}"


def work_dir_for(session_id: int) -> Path:
    """每個 session 的工作目錄（mock 不真的寫檔，但子程序 cwd 需存在）。"""
    d = dal.uploads_dir().parent / "impl_work" / str(session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def prepare_worktree(session_id: int, *, base_repo: str = "") -> Path:
    """準備隔離工作目錄。base_repo（本地 git repo 路徑）非空 → git worktree add 到
    impl_work/{id}/wt（branch lodestar/impl-{id}），agent 只能動此 worktree、推送由 host 控；
    空 → 退回 work_dir_for（mock/dry 的空目錄，預設行為不變）。

    OS 級隔離（坎3）：worktree 把 agent 的寫入侷限在獨立工作樹，不污染主 checkout；
    搭配 --add-dir 限定可寫範圍 + --disallowedTools 禁 push。"""
    if not base_repo:
        return work_dir_for(session_id)
    wt = dal.uploads_dir().parent / "impl_work" / str(session_id) / "wt"
    wt.parent.mkdir(parents=True, exist_ok=True)
    branch = f"lodestar/impl-{session_id}"
    if wt.exists():   # 重試冪等：先移除既有 worktree
        subprocess.run(["git", "-C", base_repo, "worktree", "remove", "--force", str(wt)],
                       capture_output=True)
    subprocess.run(["git", "-C", base_repo, "worktree", "add", "-b", branch, str(wt)],
                   check=True, capture_output=True, text=True)
    return wt


def session_workdir(session_id: int, base_repo: str = "") -> Path:
    """回 session 的工作目錄路徑（不建立/不重建）——供 open_pr 在審批後定位 worktree。
    與 prepare_worktree 對齊：base_repo 非空 → impl_work/{id}/wt；空 → impl_work/{id}。"""
    root = dal.uploads_dir().parent / "impl_work" / str(session_id)
    return root / "wt" if base_repo else root


def clone_dir(session_id: int) -> Path:
    """clone 模式（真實 GitHub repo）的工作目錄路徑（不 clone，只回路徑；供 open_pr 定位）。"""
    return dal.uploads_dir().parent / "impl_work" / str(session_id) / "repo"


def prepare_clone(session_id: int, remote_url: str) -> Path:
    """git clone 到 clone_dir（remote_url 已含 token，呼叫端依 target 組 github/gitlab url）。
    agent 在此 working copy 改 code。重試先移除重 clone（冪等）。token 在 url，錯誤訊息不回顯。"""
    dest = clone_dir(session_id)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "clone", remote_url, str(dest)],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed (exit {proc.returncode})")   # 不回顯 stderr（含 token url）
    return dest


def project_dir(thread_id: str) -> Path:
    """一個專案（thread）共用一個工作根目錄：impl_work/{thread_id}。"""
    return dal.uploads_dir().parent / "impl_work" / thread_id


def project_clone_dir(thread_id: str) -> Path:
    """專案共用 clone 目錄（整個專案 clone 一次，所有 batch / story 在此切 branch 沿用）。"""
    return project_dir(thread_id) / "repo"


def project_work_dir(thread_id: str) -> Path:
    """專案共用的空工作目錄（mock / dry-run 用；子程序 cwd 需存在但不寫真實 repo）。"""
    d = project_dir(thread_id) / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prepare_project_clone(thread_id: str, remote_url: str) -> Path:
    """專案 clone：不存在 → git clone；已存在 → git fetch 沿用（一個專案一個目錄，重跑不重 clone）。

    remote_url 已含 token（呼叫端依 target 組 github/gitlab url）；token 在 url，錯誤訊息不回顯。
    既有 clone 損毀（非 git repo）→ 移除重 clone（自癒）。"""
    dest = project_clone_dir(thread_id)
    is_repo = dest.exists() and (dest / ".git").exists()
    if is_repo:
        fetch = subprocess.run(["git", "-C", str(dest), "fetch", "origin"],
                               capture_output=True, text=True, timeout=180)
        if fetch.returncode == 0:
            return dest
        shutil.rmtree(dest, ignore_errors=True)        # fetch 失敗（remote 變動等）→ 重 clone
    elif dest.exists():
        shutil.rmtree(dest, ignore_errors=True)        # 殘留非 git 目錄 → 清掉重 clone
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "clone", remote_url, str(dest)],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed (exit {proc.returncode})")   # 不回顯 stderr（含 token url）
    return dest


def prepare_branch_in_clone(clone_path: Path, session_id: int) -> Path:
    """在共用 clone 內為某 story 切一條乾淨的 work branch（每 story off origin/main，獨立 PR）。

    fetch origin → 偵測 default branch（origin/HEAD，退回 main/master）→
    `git checkout -B lodestar/impl-{session_id} <default>`（-B 覆寫既有 branch，重跑冪等）。
    前一個 story 的變更已在它自己的 branch，這裡乾淨重切，彼此不堆疊。"""
    branch = f"lodestar/impl-{session_id}"
    # 清掉前一個 story 可能殘留的未提交變更，確保 checkout -B 不被擋
    subprocess.run(["git", "-C", str(clone_path), "reset", "--hard"], capture_output=True)
    subprocess.run(["git", "-C", str(clone_path), "clean", "-fd"], capture_output=True)
    subprocess.run(["git", "-C", str(clone_path), "fetch", "origin"],
                   capture_output=True, text=True, timeout=120)
    head = subprocess.run(
        ["git", "-C", str(clone_path), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True)
    default = (head.stdout or "").strip() or "origin/main"
    # symbolic-ref 失敗（淺 clone / 無 origin/HEAD）→ 試 origin/main 再 origin/master
    if not head.returncode == 0:
        for cand in ("origin/main", "origin/master"):
            if subprocess.run(["git", "-C", str(clone_path), "rev-parse", "--verify", cand],
                              capture_output=True).returncode == 0:
                default = cand
                break
    subprocess.run(["git", "-C", str(clone_path), "checkout", "-B", branch, default],
                   check=True, capture_output=True, text=True)
    return clone_path


def _record_diff_preview(session_id: int, run_id: int, cwd: str) -> None:
    """dry-run：把 worktree 的 git diff 存進 impl_messages（kind=event），供 /approve 前預覽。
    cwd 非 git repo（mock）→ 記註記、不報錯。"""
    try:
        proc = subprocess.run(["git", "-C", cwd, "diff", "HEAD"],
                              capture_output=True, text=True, timeout=15)
        diff = proc.stdout if proc.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        diff = ""
    impl_dal.append_message(
        run_id, kind="event",
        content=f"[diff preview]\n{diff[:8000]}" if diff else "[diff preview] (無 git diff)")


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
    auto_approve: bool = True,
) -> dict:
    """fix-loop 核心。回 summary dict（status / attempts / pr_url / reason）。

    重試規則：只有「跑完但失敗（exit≠0）」才重試；cancelled / timed_out 視為終局。
    成功後：auto_approve=True → 立刻開 PR；False → 存 diff 預覽、status=awaiting_approval，
    等 /approve 才開 PR（真實執行的人工審批 gate）。HookAbort → 該 run 記 rejected、session failed。
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
            if not auto_approve:
                _record_diff_preview(session_id, run_id, cwd)
                impl_dal.update_session(session_id, status="awaiting_approval")
                _log.info("session %s awaiting approval on attempt %s", session_id, attempt)
                return {"status": "awaiting_approval", "attempts": attempt}
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


# ============================================================
#  多角色 pipeline（§6.4 dispatch 的實作面）：lead → RD → tester → reviewer → 失敗回圈
# ============================================================
ROLE_PIPELINE = ("lead", "rd", "tester", "reviewer")

_CHANGES_REQUESTED_RE = re.compile(r"CHANGES[_\s]?REQUESTED", re.IGNORECASE)


# 各角色「身分句」預設（persona）。可被該步驟綁定 agent 的 system_prompt 覆寫；
# 其後的機器契約（plan 格式 / exit≠0 / reviewer verdict 行）永遠來自 code，不放進 persona，
# 使用者改人設不會改壞 fix-loop 的判定與 PR 流程。比照 builtin_core_stages 的 persona/契約分離。
_DEFAULT_PERSONA = {
    "lead": "You are the LEAD engineer.",
    "rd": "You are the RD (developer).",
    "tester": "You are the TESTER.",
    "reviewer": "You are the REVIEWER.",
}


def _role_prompt(role: str, *, story: str, plan: str, feedback: str, attempt: int,
                 persona: str = "") -> str:
    """組某角色的 prompt：身分句（persona，可覆寫）+ 機器契約（恆來自 code）。

    persona 空 → 用 _DEFAULT_PERSONA[role]，render 結果與接線前逐字相同（零回歸）。
    """
    story = story.strip() or "(no story provided)"
    head = persona.strip() or _DEFAULT_PERSONA[role]
    if role == "lead":
        return (
            f"{head} Break the user story into a concrete, ordered "
            "implementation plan: the modules/files to create or change and the tests to add. "
            "Be specific and concise — the RD will implement exactly this plan.\n\n"
            f"--- STORY ---\n{story}\n--- END STORY ---\n"
        )
    if role == "rd":
        p = (
            f"{head} Implement the plan in the working directory. "
            "Never push to protected branches (main/master/release/production).\n\n"
            f"--- STORY ---\n{story}\n\n--- PLAN ---\n{plan or '(no plan)'}\n"
        )
        if feedback:
            p += f"\n[Attempt {attempt}] Address this review/test feedback:\n{feedback[-1200:]}\n"
        return p
    if role == "tester":
        return (
            f"{head} Run the project's quality gate in the working directory, in this order and "
            "stopping at the first failure:\n"
            "  1) lint / format check + type-check, using the repo's OWN config — detect and run what "
            "the project already defines (e.g. package.json scripts, Makefile, .pre-commit-config, "
            "ruff/eslint/prettier/tsc, ktlint/detekt, swiftlint, golangci-lint); skip a tool only if "
            "the repo clearly has none.\n"
            "  2) write and run the tests covering the implemented story.\n"
            "Exit non-zero if lint, type-check, or any test fails.\n\n"
            f"--- STORY ---\n{story}\n\n--- PLAN ---\n{plan or '(no plan)'}\n"
        )
    # reviewer
    return (
        f"{head} Review the implementation and tests for correctness, "
        "security, and completeness.\n"
        "IMPORTANT — commit/PR is the host's job, not yours: after this review the host runs "
        "`git add -A`, commits the ENTIRE working tree, and opens the PR. The tree is intentionally "
        "uncommitted right now, so do NOT gate on git tracking/commit/staging state — e.g. files "
        "showing as untracked in `git status`, or `git ls-files` being empty, are EXPECTED and will be "
        "committed. Judge only the code/test content; assume all working-tree files ship in the PR.\n"
        "Finish your reply with EXACTLY one verdict line:\n"
        "  REVIEW: APPROVED\n"
        "or\n"
        "  REVIEW: CHANGES_REQUESTED: <one-line reason>\n\n"
        f"--- STORY ---\n{story}\n"
    )


def _reviewer_approved(output: str) -> bool:
    """reviewer 通過判定：明確 CHANGES_REQUESTED → 不通過；否則（含無明確標記）→ 通過。"""
    return not _CHANGES_REQUESTED_RE.search(output or "")


def _pin_head_to_work_branch(cwd: str, session_id: int) -> None:
    """每個 role 跑完後把 HEAD 釘回 work branch。agent（bypassPermissions）可能 git checkout
    把 HEAD 切離 lodestar/impl-{id}（如 tester 為驗 base 切過去後沒切回 → detached），
    害下游 role 看到 7.2 之前的舊樹、且 host 開 PR 時以當前 HEAD 為準漏掉工作 commit。
    best-effort：非 git / branch 不存在 / 已在該 branch → 略過、不丟未提交變更。"""
    branch = f"lodestar/impl-{session_id}"
    cur = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                         capture_output=True, text=True)
    if cur.returncode != 0 or (cur.stdout or "").strip() == branch:
        return
    if subprocess.run(["git", "-C", cwd, "rev-parse", "--verify", branch],
                      capture_output=True).returncode != 0:
        return
    subprocess.run(["git", "-C", cwd, "checkout", branch], capture_output=True)


async def _run_role(
    *, session_id: int, runner: AgentRunner, role: str, attempt: int,
    prompt: str, cwd: str, timeout: int, hooks: list, parent: Optional[int],
):
    """跑單一角色一次：建 run（標 dispatch_role）→ runner.run → finish_run。

    回 (run_id, result)。HookAbort 直接上拋給呼叫端做終局處理。
    """
    run_id = impl_dal.create_run(
        session_id=session_id, attempt=attempt, runner=runner.name,
        parent_run_id=parent, dispatch_role=role,
    )
    impl_dal.append_message(run_id, kind="system", content=f"[{role}] attempt {attempt}")

    def _on_log(chunk: str, _rid: int = run_id) -> None:
        impl_dal.append_message(_rid, content=chunk)

    result = await runner.run(cwd=cwd, prompt=prompt, timeout=timeout, on_log=_on_log, hooks=hooks)
    impl_dal.finish_run(run_id, status=_status_from_result(result), exit_code=result.exit_code,
                        cancelled=result.cancelled, timed_out=result.timed_out,
                        last_output=result.last_output)
    _pin_head_to_work_branch(cwd, session_id)  # agent 可能切走 HEAD → 釘回，保下游 role 與開 PR 看到正確的樹
    return run_id, result


async def run_implementation_roles(
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
    auto_approve: bool = True,
    persona_for: Optional[PersonaProvider] = None,
    runner_for: Optional[RunnerProvider] = None,
) -> dict:
    """多角色 pipeline：lead 拆計畫 → (RD 實作 → tester 測 → reviewer 審) 回圈。

    回圈條件（硬上限 max_attempts）：RD/tester 失敗（exit≠0）或 reviewer CHANGES_REQUESTED →
    帶回饋重做 RD。reviewer APPROVED 且 tester 通過 → 開 PR 收工。
    cancelled / timed_out / HookAbort 任一角色觸發 → 終局（與單一 fix-loop 一致）。
    各步驟可用各自的 runner（runner_for 依綁定 agent 的 model_choice；未指定則共用傳入的預設
    runner），每個角色一筆 impl_runs（dispatch_role 標記）。
    """
    hooks = hooks or []
    open_pr = open_pr or _default_open_pr
    impl_dal.update_session(session_id, status="running")

    def _persona(step: str) -> str:
        """該步驟綁定 agent 的 system_prompt（無 provider / 無綁定 → ""，走預設 persona）。"""
        return (persona_for(step) or "") if persona_for else ""

    _step_runner: dict[str, AgentRunner] = {}

    def _runner(step: str) -> AgentRunner:
        """該步驟的 runner：runner_for(step) 指定者優先，否則用傳入的預設 runner（per step memoize，
        fix-loop 多輪沿用同實例）。並把它登記為當前 active runner，讓 cancel 命中正在跑的這步。"""
        if step not in _step_runner:
            _step_runner[step] = (runner_for(step) if runner_for else None) or runner
        r = _step_runner[step]
        _ACTIVE_RUNNERS[session_id] = r
        return r

    def _terminal(result, attempt: int) -> Optional[dict]:
        """cancelled / timed_out → 回終局 summary；否則 None（非終局）。"""
        if result.cancelled:
            impl_dal.update_session(session_id, status="cancelled")
            return {"status": "cancelled", "attempts": attempt}
        if result.timed_out:
            impl_dal.update_session(session_id, status="failed", error_message="timed out")
            return {"status": "failed", "reason": "timed_out", "attempts": attempt}
        return None

    try:
        # 0) lead 拆計畫（一次）
        _, lead_res = await _run_role(
            session_id=session_id, runner=_runner("lead"), role="lead", attempt=1,
            prompt=_role_prompt("lead", story=story, plan="", feedback="", attempt=1,
                                persona=_persona("lead")),
            cwd=cwd, timeout=timeout, hooks=hooks, parent=None,
        )
        term = _terminal(lead_res, 1)
        if term:
            return term
        plan = lead_res.last_output if lead_res.ok else ""

        # 1) RD → tester → reviewer 回圈
        feedback = ""
        parent: Optional[int] = None
        for attempt in range(1, max_attempts + 1):
            rd_id, rd_res = await _run_role(
                session_id=session_id, runner=_runner("rd"), role="rd", attempt=attempt,
                prompt=_role_prompt("rd", story=story, plan=plan, feedback=feedback, attempt=attempt,
                                    persona=_persona("rd")),
                cwd=cwd, timeout=timeout, hooks=hooks, parent=parent,
            )
            parent = rd_id
            term = _terminal(rd_res, attempt)
            if term:
                return term
            if not rd_res.ok:
                feedback = f"RD 實作失敗：\n{rd_res.last_output}"
                continue

            _, test_res = await _run_role(
                session_id=session_id, runner=_runner("tester"), role="tester", attempt=attempt,
                prompt=_role_prompt("tester", story=story, plan=plan, feedback="", attempt=attempt,
                                    persona=_persona("tester")),
                cwd=cwd, timeout=timeout, hooks=hooks, parent=parent,
            )
            term = _terminal(test_res, attempt)
            if term:
                return term
            if not test_res.ok:
                feedback = f"測試失敗：\n{test_res.last_output}"
                continue

            _, rev_res = await _run_role(
                session_id=session_id, runner=_runner("reviewer"), role="reviewer", attempt=attempt,
                prompt=_role_prompt("reviewer", story=story, plan=plan, feedback="", attempt=attempt,
                                    persona=_persona("reviewer")),
                cwd=cwd, timeout=timeout, hooks=hooks, parent=parent,
            )
            term = _terminal(rev_res, attempt)
            if term:
                return term
            if rev_res.ok and _reviewer_approved(rev_res.last_output):
                if not auto_approve:
                    _record_diff_preview(session_id, rd_id, cwd)
                    impl_dal.update_session(session_id, status="awaiting_approval")
                    _log.info("session %s (roles) awaiting approval on attempt %s", session_id, attempt)
                    return {"status": "awaiting_approval", "attempts": attempt, "mode": "roles"}
                pr_url = open_pr(session_id, target_repo, rd_res.last_output)
                impl_dal.update_session(session_id, status="succeeded", pr_url=pr_url)
                _log.info("session %s (roles) approved on attempt %s → %s", session_id, attempt, pr_url)
                return {"status": "succeeded", "attempts": attempt, "pr_url": pr_url, "mode": "roles"}
            feedback = f"Reviewer 要求修改：\n{rev_res.last_output}"

    except HookAbort as exc:
        impl_dal.update_session(session_id, status="failed", error_message=str(exc))
        _log.info("session %s (roles) rejected by hook %s", session_id, exc.hook_name)
        return {"status": "failed", "reason": "hook_abort", "hook": exc.hook_name, "mode": "roles"}

    impl_dal.update_session(session_id, status="failed",
                            error_message=f"{max_attempts} 輪後 reviewer 仍未通過")
    return {"status": "failed", "reason": "max_attempts", "attempts": max_attempts, "mode": "roles"}


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
    mode: str = "single",
    auto_approve: bool = True,
    clone_url: str = "",
    persona_for: Optional[PersonaProvider] = None,
    runner_for: Optional[RunnerProvider] = None,
) -> int:
    """建立 session + 背景跑 fix-loop。立刻回 session_id（非阻塞）。

    工作目錄在背景 task 內準備（避免 git clone 等 I/O block endpoint）：
    clone_url 非空 → git clone 該 repo（github/gitlab，url 已含 token）；
    否則 prepare_worktree（LODESTAR_IMPL_BASE_REPO 本地 base，或空目錄）。
    runner 登記到 _ACTIVE_RUNNERS 供 cancel；task 強引用由 task_registry 持有（防 GC）。
    """
    # 原子守衛：endpoint 已先擋一次，但 check→此處之間隔著 await；這裡建 row 前同步再查一次，
    # 關掉「兩個分頁同時按 → 雙雙放行 → 共用 worktree 互相 checkout 打架」的 race。
    if impl_dal.has_active_for_thread(thread_id):
        raise impl_dal.ImplActiveError(thread_id)
    session_id = impl_dal.create_session(
        thread_id=thread_id, title=title or "(implementation)",
        target_repo=target_repo, runner=runner_choice,
    )
    base_repo = os.environ.get("LODESTAR_IMPL_BASE_REPO", "")

    async def _supervised() -> dict:
        if clone_url:
            # 專案共用 clone（一個專案一個目錄）→ 為本 session 切乾淨 branch
            def cwd_provider() -> Path:
                cwd = prepare_project_clone(thread_id, clone_url)
                return prepare_branch_in_clone(cwd, session_id)
        elif base_repo:
            cwd_provider = lambda: prepare_worktree(session_id, base_repo=base_repo)
        else:
            cwd_provider = lambda: project_work_dir(thread_id)
        return await run_session_to_terminal(
            session_id=session_id, runner=runner, story=story, cwd_provider=cwd_provider,
            target_repo=target_repo, hooks=hooks, timeout=timeout, open_pr=open_pr,
            mode=mode, auto_approve=auto_approve, persona_for=persona_for, runner_for=runner_for,
        )

    task_registry.spawn(_supervised(), name=f"impl-{session_id}")
    return session_id


async def run_session_to_terminal(
    *,
    session_id: int,
    runner: AgentRunner,
    story: str,
    cwd_provider: Callable[[], Path],
    target_repo: str = "",
    hooks: Optional[list[ToolHook]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    open_pr: Optional[PrOpener] = None,
    mode: str = "single",
    auto_approve: bool = True,
    persona_for: Optional[PersonaProvider] = None,
    runner_for: Optional[RunnerProvider] = None,
) -> dict:
    """跑單一 session 到終局並回 summary dict。可被 start_session（背景 task）或 batch 直接 await。

    cwd_provider 在 thread pool 準備工作目錄（clone / worktree / batch 內切 branch），
    失敗 → session failed（不讓 task 無聲死）。runner 登記 _ACTIVE_RUNNERS 供 cancel，
    結束務必 pop。driver 依 mode 選單一 fix-loop 或多角色 pipeline。
    persona_for / runner_for 僅 roles 模式使用（依步驟注入綁定 agent 的 system_prompt 與 runner）。"""
    hooks = hooks or []
    _ACTIVE_RUNNERS[session_id] = runner
    driver = run_implementation_roles if mode == "roles" else run_implementation
    try:
        cwd = await asyncio.to_thread(cwd_provider)
        kwargs = dict(
            session_id=session_id, runner=runner, story=story, cwd=str(cwd),
            target_repo=target_repo, hooks=hooks, timeout=timeout, open_pr=open_pr,
            auto_approve=auto_approve,
        )
        if mode == "roles":
            kwargs["persona_for"] = persona_for
            kwargs["runner_for"] = runner_for
        return await driver(**kwargs)
    except Exception as exc:  # noqa: BLE001 - 準備工作目錄失敗 → session failed，不讓 task 無聲死
        impl_dal.update_session(session_id, status="failed",
                                error_message=f"prepare workdir failed: {exc}")
        _log.exception("session %s prepare workdir failed", session_id)
        return {"status": "failed", "reason": "prepare_failed"}
    finally:
        _ACTIVE_RUNNERS.pop(session_id, None)


async def request_cancel(session_id: int) -> bool:
    """要求取消正在跑的 session。回傳是否找到 active runner。"""
    runner = _ACTIVE_RUNNERS.get(session_id)
    if runner is None:
        return False
    await runner.cancel()
    return True
