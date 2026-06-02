"""逐 issue 依序實作的 batch 編排（host 層）。

一個 batch = 對某 thread 的整份 stories「依 story 編號依序、一次一個 issue」實作。
把現成零件串起來：delivery_parser（拆 + 排序）→ run_session_to_terminal（單 story 的 fix-loop /
roles QA gate）→ PrOpener（每 issue 各自 branch/PR + Closes 該 issue + 在 issue 留言）。

設計：不動單一 session 的核心邏輯，只在上面疊一層「依序 await、做完才換下一個」。
- 排序：story 編號 N.M（1.1 → 1.2 → … → 7.3）。
- 對應：用編號比對 open issue（容錯 dash / 空白），找不到仍實作但 PR 不帶 Closes。
- 失敗策略：預設 continue-on-failure（記 failed 後續做下一個）；stop_on_failure=True 則遇錯即停。
- clone：整批 clone 一次，每 story 在共用 clone 內切乾淨 branch（off default branch，獨立 PR、不堆疊）。

外部相依（github/gitlab + keystore）以 callable 注入（list_issues / build_opener），保持本模組可單測。
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

from plugin_api import AgentRunner, DeliveryItem, ToolHook
from async_runtime import impl_dal, orchestrator, task_registry
from delivery_parser import parse_stories_to_delivery_items

_log = logging.getLogger("async_runtime.batch")

DEFAULT_TIMEOUT = orchestrator.DEFAULT_TIMEOUT

# batch_id → 是否被要求取消 / 當前正在跑的 session_id（供 cancel 用）
_BATCH_CANCEL: "set[int]" = set()
_BATCH_CURRENT: "dict[int, int]" = {}

_STORY_NUM_RE = re.compile(r"Story\s+(\d+\.\d+)", re.IGNORECASE)


class BatchError(RuntimeError):
    """batch 啟動前置失敗（無 story 可實作等）。"""


def _story_key(title: str) -> str:
    """從 title 抽 story 編號 N.M（抽不到回 ''）。"""
    m = _STORY_NUM_RE.search(title or "")
    return m.group(1) if m else ""


def _sort_key(title: str) -> tuple[int, int]:
    """story 排序鍵：(epic, story)；無編號者排最後。"""
    key = _story_key(title)
    if not key:
        return (10**6, 10**6)
    a, b = key.split(".")
    return (int(a), int(b))


def match_issues(items: list[DeliveryItem],
                 open_issues: list[tuple[int, str]]) -> dict[str, int]:
    """用 story 編號 N.M 把 open issue (number, title) 對到 story。回 {story_key: issue_number}。

    用編號而非整串標題，避免全形/半形 dash 與空白差異造成 miss。"""
    by_key: dict[str, int] = {}
    for number, title in open_issues:
        key = _story_key(title)
        if key and key not in by_key:        # 同編號取第一個
            by_key[key] = number
    return by_key


@dataclass
class _SessionItem:
    session_id: int
    story_key: str
    title: str
    body: str
    issue_number: Optional[int]


def start_batch(
    *,
    thread_id: str,
    story_artifact: str,
    runner_factory: Callable[[], AgentRunner],
    runner_choice: str,
    mode: str = "roles",
    target_repo: str = "",
    clone_url: str = "",
    list_issues: Optional[Callable[[], list[tuple[int, str]]]] = None,
    build_opener: Optional[Callable[..., orchestrator.PrOpener]] = None,
    hooks: Optional[list[ToolHook]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    stop_on_failure: bool = False,
    persona_for: Optional[orchestrator.PersonaProvider] = None,
    runner_for: Optional[orchestrator.RunnerProvider] = None,
    merge_pr: Optional[Callable[[int], bool]] = None,
    skip_keys: Optional[set[str]] = None,
) -> dict:
    """解析 stories → 排序 → 對應 issue → 建 batch + 逐 story 的 session → 背景依序跑。

    立刻回 {batch_id, total, skipped, items}（非阻塞，比照 orchestrator.start_session）。

    list_issues：（真實執行）回 repo 的 open issue (number, title)，供比對；mock 可不給（None → 不對應）。
    build_opener：（真實執行）build_opener(batch_id=, issue_number_for=, pr_title_for=) → PrOpener；
        需 batch_id 以指向共用 clone dir。None → mock（run_implementation 用示意 url）。
    runner_factory：每個 session 開新 runner 實例（cancel 以 session_id 找 _ACTIVE_RUNNERS）。
    skip_keys：冪等重跑——已完成（issue 已關）或進行中（已有 open PR）的 story 編號集，跳過不重做。
    """
    items = parse_stories_to_delivery_items(story_artifact)
    if not items:
        raise BatchError("stories 解析不出任何 story，無法實作")
    items.sort(key=lambda it: _sort_key(it.title))

    open_issues = list_issues() if list_issues else []
    issue_map = match_issues(items, open_issues)

    # 冪等：跳過已完成 / 進行中的 story（依 GitHub issue/PR 狀態，由 endpoint 算好）
    skip_keys = skip_keys or set()
    to_run = [it for it in items if _story_key(it.title) not in skip_keys]
    skipped = len(items) - len(to_run)
    if not to_run:
        raise BatchError(
            f"全部 {len(items)} 個 story 的 issue 皆已關閉或已有 PR 進行中，無待實作；"
            "若要重做某 story，請先 reopen 其 issue 或關掉其 PR")
    if skipped:
        _log.info("batch 冪等跳過 %d 個已完成/進行中 story，實作其餘 %d 個", skipped, len(to_run))

    batch_id = impl_dal.create_batch(
        thread_id=thread_id, target_repo=target_repo, runner=runner_choice,
        mode=mode, total=len(to_run), stop_on_failure=stop_on_failure,
        auto_merge=merge_pr is not None,           # 有 merger = 本批 auto-merge on（存進紀錄供 UI 顯示）
    )

    session_items: list[_SessionItem] = []
    for it in to_run:
        skey = _story_key(it.title)
        issue_no = issue_map.get(skey)
        if list_issues and issue_no is None:
            _log.warning("batch %s: story %s 無對應 open issue，PR 不帶 Closes", batch_id, skey or it.title)
        sid = impl_dal.create_session(
            thread_id=thread_id, title=it.title, target_repo=target_repo,
            runner=runner_choice, batch_id=batch_id, issue_number=issue_no, story_key=skey,
        )
        session_items.append(_SessionItem(
            session_id=sid, story_key=skey, title=it.title, body=it.body, issue_number=issue_no))

    issue_for = {s.session_id: s.issue_number for s in session_items}
    title_for = {s.session_id: s.title for s in session_items}
    open_pr: Optional[orchestrator.PrOpener] = None
    if build_opener:
        open_pr = build_opener(
            batch_id=batch_id,
            issue_number_for=lambda sid: issue_for.get(sid),
            pr_title_for=lambda sid: title_for.get(sid, ""),
        )

    task_registry.spawn(
        _run_batch(
            batch_id=batch_id, thread_id=thread_id, session_items=session_items,
            runner_factory=runner_factory, mode=mode, target_repo=target_repo,
            clone_url=clone_url, open_pr=open_pr, hooks=hooks or [], timeout=timeout,
            stop_on_failure=stop_on_failure, persona_for=persona_for, runner_for=runner_for,
            merge_pr=merge_pr,
        ),
        name=f"impl-batch-{batch_id}",
    )
    return {
        "batch_id": batch_id,
        "total": len(session_items),
        "skipped": skipped,
        "items": [
            {"session_id": s.session_id, "story_key": s.story_key,
             "title": s.title, "issue_number": s.issue_number}
            for s in session_items
        ],
    }


def _fail_remaining(session_items: list[_SessionItem], reason: str) -> None:
    """把仍未開跑（pending）的 session 標終局，避免孤兒 pending。"""
    for s in session_items:
        cur = impl_dal.get_session(s.session_id)
        if cur and cur["status"] == "pending":
            impl_dal.update_session(s.session_id, status="cancelled", error_message=reason)


async def _run_batch(
    *,
    batch_id: int,
    thread_id: str,
    session_items: list[_SessionItem],
    runner_factory: Callable[[], AgentRunner],
    mode: str,
    target_repo: str,
    clone_url: str,
    open_pr: Optional[orchestrator.PrOpener],
    hooks: list[ToolHook],
    timeout: int,
    stop_on_failure: bool,
    persona_for: Optional[orchestrator.PersonaProvider] = None,
    runner_for: Optional[orchestrator.RunnerProvider] = None,
    merge_pr: Optional[Callable[[int], bool]] = None,
) -> None:
    """依序跑每個 session 到終局，做完才換下一個。continue-on-failure（預設）。

    merge_pr（策略 A）：story 過 QA gate（succeeded、已開 PR）後，依序把該 PR merge 進 default branch，
    下一個 story 的 prepare_branch_in_clone 從更新後的 origin/main 切 → 後者吃得到前者、避免衝突。
    merge 失敗（衝突/不可merge）只 log 不中斷（PR 仍開著，留人處理）。

    工作目錄一個專案共用一份（project_clone_dir(thread_id)）：整個專案 clone 一次、
    各 story 在此切乾淨 branch 沿用，重跑不重 clone。"""
    clone_path = None
    if clone_url:
        try:
            clone_path = await asyncio.to_thread(orchestrator.prepare_project_clone, thread_id, clone_url)
        except Exception as exc:  # noqa: BLE001
            impl_dal.update_batch(batch_id, status="failed", error_message=f"clone failed: {exc}")
            _fail_remaining(session_items, "batch clone failed")
            _log.exception("batch %s clone failed", batch_id)
            return

    succeeded = failed = 0
    try:
        for s in session_items:
            if batch_id in _BATCH_CANCEL:
                impl_dal.update_batch(batch_id, status="cancelled")
                _fail_remaining(session_items, "batch cancelled")
                return

            if clone_path is not None:
                cwd_provider = (lambda cp=clone_path, sid=s.session_id:
                                orchestrator.prepare_branch_in_clone(cp, sid))
            else:
                cwd_provider = (lambda: orchestrator.project_work_dir(thread_id))

            _BATCH_CURRENT[batch_id] = s.session_id
            runner = runner_factory()
            summary = await orchestrator.run_session_to_terminal(
                session_id=s.session_id, runner=runner, story=s.body, cwd_provider=cwd_provider,
                target_repo=target_repo, hooks=hooks, timeout=timeout, open_pr=open_pr,
                mode=mode, auto_approve=True, persona_for=persona_for, runner_for=runner_for,
            )
            status = summary.get("status")
            if status == "succeeded":
                succeeded += 1
                # 策略 A：過 gate 即依序 merge，下個 story 從更新後的 main 切（後者吃得到前者）
                if merge_pr is not None:
                    try:
                        merged = await asyncio.to_thread(merge_pr, s.session_id)
                    except Exception as exc:  # noqa: BLE001 - merge 失敗不該炸掉整批
                        merged = False
                        _log.warning("batch %s story %s auto-merge 例外：%s", batch_id, s.story_key, exc)
                    if not merged:
                        _log.warning("batch %s story %s PR 未自動 merge（衝突/不可merge/無PR）；"
                                     "後續 story 將不含此變更，需人工處理", batch_id, s.story_key or s.title)
            elif status == "cancelled":                # session 被 cancel → 整個 batch 視為 cancelled
                impl_dal.update_batch(batch_id, status="cancelled")
                _fail_remaining(session_items, "batch cancelled")
                return
            else:
                failed += 1
                if stop_on_failure:
                    impl_dal.update_batch(
                        batch_id, status="failed",
                        error_message=f"stopped at story {s.story_key or s.title}")
                    _fail_remaining(session_items, "stopped after earlier failure")
                    return
            _log.info("batch %s progress: %d ok / %d failed / %d total",
                      batch_id, succeeded, failed, len(session_items))
    finally:
        _BATCH_CURRENT.pop(batch_id, None)
        _BATCH_CANCEL.discard(batch_id)

    final = "succeeded" if failed == 0 else ("failed" if succeeded == 0 else "partial")
    impl_dal.update_batch(batch_id, status=final)
    _log.info("batch %s done: %s (%d ok / %d failed)", batch_id, final, succeeded, failed)


async def request_cancel(batch_id: int) -> bool:
    """要求取消 batch：停起新 session + cancel 當前正在跑的 session。回是否有在跑的 batch。"""
    running = batch_id in _BATCH_CURRENT
    _BATCH_CANCEL.add(batch_id)
    cur = _BATCH_CURRENT.get(batch_id)
    if cur is not None:
        await orchestrator.request_cancel(cur)
    return running
