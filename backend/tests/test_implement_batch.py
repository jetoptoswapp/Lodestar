"""逐 issue 依序實作（batch）測試。

純函式（排序 / story↔issue 比對）以單元測試覆蓋；_run_batch 的依序 + continue-on-failure /
stop-on-failure 以注入 fake run_session_to_terminal 驗證（不跑真實 runner / 子程序）。
HTTP 層用 TestClient + mock runner poll 到完成。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from fastapi.testclient import TestClient

import app as appmod
from async_runtime import batch, impl_dal, orchestrator
from delivery_parser import parse_stories_to_delivery_items
from persistence import dal


STORIES_MD = """# Demo — User Stories

## Epic 1: 基礎
### Story 1.10 — 後做的高編號
**As a** dev I want x so that y
**Acceptance Criteria**
- a
**Senior RD Estimate** - 2

### Story 1.2 — 先做的低編號
**As a** dev I want x so that y
**Acceptance Criteria**
- a
**Senior RD Estimate** - 1

## Epic 2: 進階
### Story 2.1 — 第三個
**As a** dev I want x so that y
**Acceptance Criteria**
- a
**Senior RD Estimate** - 1
"""


# ---- 純函式 ----------------------------------------------------------------

def test_sort_and_story_key():
    assert batch._story_key("Story 1.10 — x") == "1.10"
    assert batch._story_key("無編號") == ""
    keys = sorted(["Story 2.1 — c", "Story 1.10 — b", "Story 1.2 — a"], key=batch._sort_key)
    assert keys == ["Story 1.2 — a", "Story 1.10 — b", "Story 2.1 — c"]


def test_match_issues_by_number_tolerates_dash_variants():
    items = parse_stories_to_delivery_items(STORIES_MD)
    # issue title 用不同 dash（- / – / —）與多餘空白，仍應以編號對上
    open_issues = [
        (5, "Story 1.2 - 先做的低編號"),
        (9, "Story 1.10  —  後做的高編號"),
        (7, "Story 2.1 – 第三個"),
        (99, "完全無關的 issue"),
    ]
    m = batch.match_issues(items, open_issues)
    assert m == {"1.10": 9, "1.2": 5, "2.1": 7}


# ---- _run_batch 依序 / 失敗策略（注入 fake driver）-------------------------

def _make_session_items(n):
    items = []
    for i in range(1, n + 1):
        sid = impl_dal.create_session(
            thread_id="t", title=f"Story 1.{i} — s{i}", target_repo="o/r",
            runner="mock", batch_id=None, issue_number=i, story_key=f"1.{i}")
        items.append(batch._SessionItem(session_id=sid, story_key=f"1.{i}",
                                        title=f"Story 1.{i}", body="b", issue_number=i))
    return items


def test_run_batch_sequential_continue_on_failure(tmp_db, monkeypatch):
    dal.create_project("t", "demo")
    batch_id = impl_dal.create_batch(thread_id="t", target_repo="o/r", runner="mock",
                                     mode="roles", total=3)
    items = _make_session_items(3)

    order = []
    # 第 2 個 story 失敗，其餘成功 → continue-on-failure → 全部都跑、batch=partial
    async def fake_driver(*, session_id, **kw):
        order.append(session_id)
        status = "failed" if session_id == items[1].session_id else "succeeded"
        impl_dal.update_session(session_id, status=status)
        return {"status": status}
    monkeypatch.setattr(orchestrator, "run_session_to_terminal", fake_driver)

    asyncio.run(batch._run_batch(
        batch_id=batch_id, thread_id="t", session_items=items, runner_factory=lambda: object(),
        mode="roles", target_repo="o/r", clone_url="", open_pr=None,
        hooks=[], timeout=10, stop_on_failure=False))

    assert order == [s.session_id for s in items]       # 依序、全部都跑
    assert impl_dal.get_batch(batch_id)["status"] == "partial"


def test_run_batch_stop_on_failure_halts(tmp_db, monkeypatch):
    dal.create_project("t", "demo")
    batch_id = impl_dal.create_batch(thread_id="t", target_repo="o/r", runner="mock",
                                     mode="roles", total=3, stop_on_failure=True)
    items = _make_session_items(3)

    order = []
    async def fake_driver(*, session_id, **kw):
        order.append(session_id)
        status = "failed" if session_id == items[0].session_id else "succeeded"
        impl_dal.update_session(session_id, status=status)
        return {"status": status}
    monkeypatch.setattr(orchestrator, "run_session_to_terminal", fake_driver)

    asyncio.run(batch._run_batch(
        batch_id=batch_id, thread_id="t", session_items=items, runner_factory=lambda: object(),
        mode="roles", target_repo="o/r", clone_url="", open_pr=None,
        hooks=[], timeout=10, stop_on_failure=True))

    assert order == [items[0].session_id]               # 第一個失敗即停
    assert impl_dal.get_batch(batch_id)["status"] == "failed"
    # 後續未開跑的 session 標 cancelled，不留孤兒 pending
    assert impl_dal.get_session(items[2].session_id)["status"] == "cancelled"


# ---- 策略 A：過 gate 即依序 merge ------------------------------------------

def test_run_batch_auto_merge_on_success_only(tmp_db, monkeypatch):
    """merge_pr 只在成功的 story 後呼叫、依序；失敗的 story 不 merge。"""
    dal.create_project("t", "demo")
    batch_id = impl_dal.create_batch(thread_id="t", target_repo="o/r", runner="mock",
                                     mode="roles", total=3)
    items = _make_session_items(3)

    async def fake_driver(*, session_id, **kw):
        status = "failed" if session_id == items[1].session_id else "succeeded"
        impl_dal.update_session(session_id, status=status)
        return {"status": status}
    monkeypatch.setattr(orchestrator, "run_session_to_terminal", fake_driver)

    merged = []
    asyncio.run(batch._run_batch(
        batch_id=batch_id, thread_id="t", session_items=items, runner_factory=lambda: object(),
        mode="roles", target_repo="o/r", clone_url="", open_pr=None,
        hooks=[], timeout=10, stop_on_failure=False,
        merge_pr=lambda sid: (merged.append(sid) or True)))

    # 只有成功的 story 1、3 被 merge（失敗的 2 跳過），且依序
    assert merged == [items[0].session_id, items[2].session_id]


def test_run_batch_merge_failure_is_nonfatal(tmp_db, monkeypatch):
    """merge_pr 回 False（衝突）或丟例外 → 只 log、batch 照常往下，不中斷。"""
    dal.create_project("t", "demo")
    batch_id = impl_dal.create_batch(thread_id="t", target_repo="o/r", runner="mock",
                                     mode="roles", total=2)
    items = _make_session_items(2)

    async def fake_driver(*, session_id, **kw):
        impl_dal.update_session(session_id, status="succeeded")
        return {"status": "succeeded"}
    monkeypatch.setattr(orchestrator, "run_session_to_terminal", fake_driver)

    def flaky_merge(sid):
        if sid == items[0].session_id:
            raise RuntimeError("conflict")   # 第一個丟例外
        return False                         # 第二個回 False（不可 merge）
    asyncio.run(batch._run_batch(
        batch_id=batch_id, thread_id="t", session_items=items, runner_factory=lambda: object(),
        mode="roles", target_repo="o/r", clone_url="", open_pr=None,
        hooks=[], timeout=10, stop_on_failure=False, merge_pr=flaky_merge))

    # 兩個 story 都跑完、batch 成功；merge 失敗不影響流程
    assert [s for s in (impl_dal.get_session(i.session_id)["status"] for i in items)] == ["succeeded", "succeeded"]
    assert impl_dal.get_batch(batch_id)["status"] == "succeeded"


def test_make_github_pr_merger_parses_pr_number(monkeypatch):
    """make_github_pr_merger：pr_url → 解析 PR 號 → 呼叫 merge_pr；無 url → 不呼叫、回 False。"""
    from async_runtime import github_pr
    calls = []
    monkeypatch.setattr(github_pr, "merge_pr",
                        lambda repo, token, n, method="squash": calls.append((repo, n, method)) or True)
    urls = {7: "https://github.com/o/r/pull/45", 8: ""}
    merger = github_pr.make_github_pr_merger(
        get_token=lambda: "tok", repo="o/r", pr_url_for=lambda sid: urls.get(sid, ""))
    assert merger(7) is True
    assert calls == [("o/r", 45, "squash")]
    assert merger(8) is False            # 無 pr_url → 不呼叫 merge_pr
    assert len(calls) == 1


# ---- 冪等重跑：跳過已完成 / 進行中的 story --------------------------------

def test_start_batch_skips_done_keys(tmp_db, monkeypatch):
    """skip_keys 內的 story 不建 session；total=剩餘、skipped 正確。"""
    dal.create_project("t", "demo")
    monkeypatch.setattr(batch.task_registry, "spawn", lambda coro, **k: coro.close())  # 不真跑、close 掉 coroutine
    res = batch.start_batch(
        thread_id="t", story_artifact=STORIES_MD,
        runner_factory=lambda: object(), runner_choice="mock", mode="roles",
        skip_keys={"1.2"})                       # STORIES_MD 有 1.10 / 1.2 / 2.1
    assert res["skipped"] == 1 and res["total"] == 2
    assert {it["story_key"] for it in res["items"]} == {"1.10", "2.1"}   # 1.2 被跳過


def test_start_batch_all_skipped_raises(tmp_db, monkeypatch):
    """全部 story 都已完成/進行中 → BatchError（無待實作）。"""
    dal.create_project("t2", "demo")
    monkeypatch.setattr(batch.task_registry, "spawn", lambda coro, **k: coro.close())
    with pytest.raises(batch.BatchError):
        batch.start_batch(
            thread_id="t2", story_artifact=STORIES_MD,
            runner_factory=lambda: object(), runner_choice="mock",
            skip_keys={"1.10", "1.2", "2.1"})


def test_closes_regex_parses_keywords():
    """list_active_pr_issue_numbers 用的 Closes/Fixes/Resolves 關鍵字解析。"""
    from async_runtime.github_pr import _CLOSES_RE
    body = "Automated.\n\nCloses #45\nfixes #7\nResolved #9\nsee #3"
    nums = {int(m.group(1)) for m in _CLOSES_RE.finditer(body)}
    assert nums == {45, 7, 9}                    # 'see #3' 非關鍵字、不算


# ---- 一個專案一個目錄 + 並行保護 -------------------------------------------

def test_project_dir_keyed_by_thread(tmp_db):
    # 同一 thread 的 clone/work 目錄固定，不隨 session/batch 變
    assert orchestrator.project_clone_dir("abc").parent.name == "abc"
    assert orchestrator.project_clone_dir("abc") == orchestrator.project_clone_dir("abc")
    assert orchestrator.project_work_dir("abc").exists()


def test_has_active_for_thread(tmp_db):
    dal.create_project("t", "demo")
    assert impl_dal.has_active_for_thread("t") is False
    sid = impl_dal.create_session(thread_id="t", title="x", target_repo="o/r", runner="mock")
    impl_dal.update_session(sid, status="running")
    assert impl_dal.has_active_for_thread("t") is True          # running session
    impl_dal.update_session(sid, status="succeeded")
    assert impl_dal.has_active_for_thread("t") is False
    bid = impl_dal.create_batch(thread_id="t", target_repo="o/r", runner="mock", mode="roles", total=1)
    assert impl_dal.has_active_for_thread("t") is True          # running batch
    impl_dal.update_batch(bid, status="succeeded")
    assert impl_dal.has_active_for_thread("t") is False


def test_start_batch_409_when_active(tmp_db):
    with TestClient(appmod.app) as client:
        tid = client.post("/api/projects", json={"name": "demo"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", STORIES_MD)
        # 先塞一個 running batch 佔住該專案
        impl_dal.create_batch(thread_id=tid, target_repo="o/r", runner="mock", mode="roles", total=1)
        r = client.post("/api/implement/start-batch",
                        json={"thread_id": tid, "runner": "mock", "mode": "single"})
        assert r.status_code == 409, r.text
        assert "進行中" in r.json()["detail"]["message"]


# ---- HTTP 層（mock runner，無 clone）---------------------------------------

def test_start_batch_http_orders_by_story_number(tmp_db):
    with TestClient(appmod.app) as client:
        tid = client.post("/api/projects", json={"name": "demo"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", STORIES_MD)

        r = client.post("/api/implement/start-batch",
                        json={"thread_id": tid, "runner": "mock", "mode": "single"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["total"] == 3
        # items 依 story 編號排序：1.2 → 1.10 → 2.1
        assert [it["story_key"] for it in data["items"]] == ["1.2", "1.10", "2.1"]
        batch_id = data["batch_id"]

        # poll 到 batch 終局
        deadline = time.time() + 10
        final = None
        while time.time() < deadline:
            b = client.get(f"/api/implement/batches/{batch_id}").json()
            if b["status"] in ("succeeded", "failed", "partial", "cancelled"):
                final = b
                break
            time.sleep(0.05)
        assert final is not None, "batch 未在期限內完成"
        assert final["status"] == "succeeded"
        assert len(final["items"]) == 3
        assert all(it["status"] == "succeeded" for it in final["items"])
        # 列在 thread 下
        batches = client.get(f"/api/implement/threads/{tid}/batches").json()["batches"]
        assert [b["batch_id"] for b in batches] == [batch_id]
