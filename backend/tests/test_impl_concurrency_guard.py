"""原子並行守衛：同一 thread 共用一份 working copy，第二個實作啟動必須被擋。

endpoint 先擋一次，但 check→start 之間隔著 await（resolve repo / 列 issue），兩個分頁可能
雙雙通過。start_session / start_batch 在建 row 前同步再查一次 → 第二個競爭者拿到 ImplActiveError。
這裡直接驗那道 backstop（建好第一個後，第二個啟動拋例外）。
"""
from __future__ import annotations

import pytest

from async_runtime import batch, impl_dal, orchestrator
from persistence import dal

STORIES_MD = (
    "# Demo — User Stories\n\n## Epic 1\n### Story 1.1 — a\n"
    "**As a** dev I want x so that y\n**Acceptance Criteria**\n- a\n**Senior RD Estimate** - 1\n"
)


def _noop_spawn(coro, **_k):
    coro.close()  # 不真跑背景 task，只測同步守衛


def test_start_session_blocks_second_for_same_thread(tmp_db, monkeypatch):
    dal.create_project("t", "demo")
    monkeypatch.setattr(orchestrator.task_registry, "spawn", _noop_spawn)
    # 第一次：建立 pending session
    sid = orchestrator.start_session(thread_id="t", story="x", runner=object(), runner_choice="mock")
    assert isinstance(sid, int)
    # 第二次（同 thread）：再查一次 → 被擋
    with pytest.raises(impl_dal.ImplActiveError):
        orchestrator.start_session(thread_id="t", story="x", runner=object(), runner_choice="mock")


def test_start_batch_blocks_second_for_same_thread(tmp_db, monkeypatch):
    dal.create_project("t", "demo")
    monkeypatch.setattr(batch.task_registry, "spawn", _noop_spawn)
    res = batch.start_batch(thread_id="t", story_artifact=STORIES_MD,
                            runner_factory=lambda: object(), runner_choice="mock", mode="roles")
    assert res["batch_id"]
    with pytest.raises(impl_dal.ImplActiveError):
        batch.start_batch(thread_id="t", story_artifact=STORIES_MD,
                          runner_factory=lambda: object(), runner_choice="mock", mode="roles")


def test_start_batch_blocks_when_session_active(tmp_db, monkeypatch):
    """跨型別：已有 running session 時，batch 也要被擋（反之亦然）。"""
    dal.create_project("t", "demo")
    monkeypatch.setattr(orchestrator.task_registry, "spawn", _noop_spawn)
    monkeypatch.setattr(batch.task_registry, "spawn", _noop_spawn)
    orchestrator.start_session(thread_id="t", story="x", runner=object(), runner_choice="mock")
    with pytest.raises(impl_dal.ImplActiveError):
        batch.start_batch(thread_id="t", story_artifact=STORIES_MD,
                          runner_factory=lambda: object(), runner_choice="mock", mode="roles")


def test_different_threads_do_not_block(tmp_db, monkeypatch):
    dal.create_project("t1", "demo"); dal.create_project("t2", "demo")
    monkeypatch.setattr(orchestrator.task_registry, "spawn", _noop_spawn)
    orchestrator.start_session(thread_id="t1", story="x", runner=object(), runner_choice="mock")
    orchestrator.start_session(thread_id="t2", story="x", runner=object(), runner_choice="mock")  # 不同 thread → OK
