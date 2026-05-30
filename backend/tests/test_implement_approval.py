"""坎2 審批：auto_approve 路徑（awaiting_approval vs 直接開 PR）+ session_workdir。

orchestrator 層直接測（不需 TestClient）；endpoint 的 approve/reject 狀態檢查另由 test_api 類測涵蓋。
"""
from __future__ import annotations

import asyncio

from async_runtime import impl_dal, orchestrator
from plugin_api import AgentRunner, RunResult


class _OkRunner(AgentRunner):
    name = "fake"

    def build_argv(self, *, cwd, prompt):
        return ["true"]

    def is_available(self):
        return True

    async def run(self, *, cwd, prompt, timeout, on_log, on_event=None, hooks=None):
        on_log("done")
        return RunResult(exit_code=0, last_output="ok")


def test_auto_approve_false_awaits(tmp_db):
    sid = impl_dal.create_session(thread_id="t", title="x", target_repo="o/r", runner="fake")
    out = asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=_OkRunner(), story="s",
        cwd=str(orchestrator.work_dir_for(sid)), target_repo="o/r", auto_approve=False))
    assert out["status"] == "awaiting_approval"
    assert impl_dal.get_session(sid)["status"] == "awaiting_approval"


def test_auto_approve_true_opens_pr(tmp_db):
    sid = impl_dal.create_session(thread_id="t", title="x", target_repo="o/r", runner="fake")
    out = asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=_OkRunner(), story="s",
        cwd=str(orchestrator.work_dir_for(sid)), target_repo="o/r", auto_approve=True,
        open_pr=lambda s, r, o: f"https://github.com/{r}/pull/MOCK-{s}"))
    assert out["status"] == "succeeded" and "MOCK" in out["pr_url"]
    assert impl_dal.get_session(sid)["status"] == "succeeded"


def test_diff_preview_recorded_on_await(tmp_db):
    sid = impl_dal.create_session(thread_id="t", title="x", target_repo="o/r", runner="fake")
    asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=_OkRunner(), story="s",
        cwd=str(orchestrator.work_dir_for(sid)), target_repo="o/r", auto_approve=False))
    msgs = impl_dal.list_session_messages(sid)
    assert any("diff preview" in m["content"] for m in msgs)


def test_session_workdir_paths(tmp_db):
    assert orchestrator.session_workdir(5, "").name == "5"
    assert orchestrator.session_workdir(5, "/some/repo").name == "wt"
