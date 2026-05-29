"""M5.2 implement 編排測試：orchestrator fix-loop 狀態機 + HTTP endpoints（mock runner）。

orchestrator 單元測試以注入 runner（覆寫 run() 回傳 scripted RunResult）驗證狀態機，
不跑子程序、deterministic。redact 持久化用真實 subprocess 路徑驗證。
HTTP 測試用 TestClient + mock runner，poll 到背景 task 完成。
"""
from __future__ import annotations

import asyncio
import sys
import time

from fastapi.testclient import TestClient

import app as appmod
from async_runtime import impl_dal, orchestrator
from plugin_api import AgentRunner, HookAbort, RunResult, ToolHook
from plugins.builtin_implement.hooks import RedactSecretsHook


# ============ 注入用 runner ============
class FakeRunner(AgentRunner):
    """覆寫 run()：每次回傳 scripted RunResult（最後一個用於超出長度的後續呼叫）。"""
    name = "fake"

    def __init__(self, results, log_lines=None):
        self._results = list(results)
        self._log_lines = log_lines or []
        self.calls = 0

    def build_argv(self, *, cwd, prompt):
        return ["true"]

    def is_available(self):
        return True

    async def run(self, *, cwd, prompt, timeout, on_log, on_event=None, hooks=None):
        self.calls += 1
        for ln in self._log_lines:
            on_log(ln)
        return self._results[min(self.calls - 1, len(self._results) - 1)]


class AbortRunner(AgentRunner):
    """模擬 pre_run hook 擋下：run() 直接 raise HookAbort。"""
    name = "abort"

    def build_argv(self, *, cwd, prompt):
        return ["true"]

    def is_available(self):
        return True

    async def run(self, *, cwd, prompt, timeout, on_log, on_event=None, hooks=None):
        raise HookAbort("deny_protected_branch", "拒絕對受保護分支 'main' 的危險操作")


class SecretRunner(AgentRunner):
    """真實 subprocess：印出一個假 token（走 base run() + hook + 持久化全路徑）。"""
    name = "secret"

    def build_argv(self, *, cwd, prompt):
        return [sys.executable, "-c", "print('ghp_AAAABBBBCCCCDDDDEEEE1234'); print('built ok')"]

    def is_available(self):
        return True


def _new_session(thread_id="t-impl", title="S", repo="o/r", runner="fake"):
    sid = impl_dal.create_session(thread_id=thread_id, title=title, target_repo=repo, runner=runner)
    return sid, str(orchestrator.work_dir_for(sid))


# ============ orchestrator 狀態機（注入 runner）============
def test_happy_path_opens_pr(tmp_db):
    sid, cwd = _new_session()
    res = asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=FakeRunner([RunResult(0, "done")]),
        story="As a user...", cwd=cwd, target_repo="o/r"))
    assert res["status"] == "succeeded" and res["attempts"] == 1
    assert "MOCK" in res["pr_url"]
    sess = impl_dal.get_session(sid)
    assert sess["status"] == "succeeded" and "MOCK" in sess["pr_url"]
    assert [r["status"] for r in impl_dal.list_runs(sid)] == ["succeeded"]


def test_fix_loop_hard_cap_three(tmp_db):
    sid, cwd = _new_session()
    runner = FakeRunner([RunResult(1, "fail")])      # 永遠失敗
    res = asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=runner, story="x", cwd=cwd))
    assert res["status"] == "failed" and res["reason"] == "max_attempts"
    assert res["attempts"] == 3
    assert runner.calls == 3                           # 硬上限：不超過 3 次
    runs = impl_dal.list_runs(sid)
    assert len(runs) == 3
    # parent_run_id 串成 fix-loop chain
    assert runs[0]["parent_run_id"] is None
    assert runs[1]["parent_run_id"] == runs[0]["run_id"]
    assert runs[2]["parent_run_id"] == runs[1]["run_id"]
    assert impl_dal.get_session(sid)["status"] == "failed"


def test_fix_loop_recovers_then_succeeds(tmp_db):
    sid, cwd = _new_session()
    runner = FakeRunner([RunResult(1), RunResult(1), RunResult(0, "fixed")])
    res = asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=runner, story="x", cwd=cwd, target_repo="o/r"))
    assert res["status"] == "succeeded" and res["attempts"] == 3
    assert runner.calls == 3
    assert [r["status"] for r in impl_dal.list_runs(sid)] == ["failed", "failed", "succeeded"]
    assert "MOCK" in impl_dal.get_session(sid)["pr_url"]


def test_cancelled_is_terminal_no_pr(tmp_db):
    sid, cwd = _new_session()
    res = asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=FakeRunner([RunResult(-1, cancelled=True)]),
        story="x", cwd=cwd))
    assert res["status"] == "cancelled" and res["attempts"] == 1
    sess = impl_dal.get_session(sid)
    assert sess["status"] == "cancelled" and sess["pr_url"] == ""


def test_timeout_is_terminal_failure(tmp_db):
    sid, cwd = _new_session()
    runner = FakeRunner([RunResult(-1, timed_out=True)])
    res = asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=runner, story="x", cwd=cwd))
    assert res["status"] == "failed" and res["reason"] == "timed_out"
    assert runner.calls == 1                           # timeout 不重試
    assert impl_dal.get_session(sid)["status"] == "failed"


def test_hook_abort_marks_rejected(tmp_db):
    sid, cwd = _new_session()
    res = asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=AbortRunner(), story="x", cwd=cwd))
    assert res["status"] == "failed" and res["reason"] == "hook_abort"
    assert res["hook"] == "deny_protected_branch"
    runs = impl_dal.list_runs(sid)
    assert runs[0]["status"] == "rejected"
    assert impl_dal.get_session(sid)["status"] == "failed"


def test_custom_open_pr_injected(tmp_db):
    sid, cwd = _new_session()
    seen = {}

    def opener(session_id, repo, output):
        seen["args"] = (session_id, repo, output)
        return "https://example/pr/42"

    res = asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=FakeRunner([RunResult(0, "out")]),
        story="x", cwd=cwd, target_repo="o/r", open_pr=opener))
    assert res["pr_url"] == "https://example/pr/42"
    assert seen["args"][0] == sid and seen["args"][1] == "o/r"


def test_redact_secrets_persisted(tmp_db):
    """真實 subprocess + RedactSecretsHook：persisted log 不得含 token。"""
    sid, cwd = _new_session(runner="secret")
    res = asyncio.run(orchestrator.run_implementation(
        session_id=sid, runner=SecretRunner(), story="x", cwd=cwd,
        hooks=[RedactSecretsHook()]))
    assert res["status"] == "succeeded"
    blob = "".join(m["content"] for m in impl_dal.list_session_messages(sid))
    assert "ghp_AAAABBBBCCCCDDDDEEEE1234" not in blob
    assert "[REDACTED]" in blob
    assert "built ok" in blob


# ============ HTTP endpoints（TestClient + mock runner）============
def _poll_done(client, sid, timeout_s=20):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = client.get(f"/api/implement/{sid}").json()
        if r["status"] not in ("pending", "running"):
            return r
        time.sleep(0.1)
    return client.get(f"/api/implement/{sid}").json()


def test_endpoint_start_mock_runs_to_pr(tmp_db):
    with TestClient(appmod.app) as client:
        tid = client.post("/api/projects", json={"name": "impl"}).json()["thread_id"]
        r = client.post("/api/implement/start", json={
            "thread_id": tid, "runner": "mock", "target_repo": "o/r",
            "story": "As a user I want X", "title": "Build X",
        })
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]

        done = _poll_done(client, sid)
        assert done["status"] == "succeeded"
        assert "MOCK" in done["pr_url"]
        assert len(done["runs"]) == 1 and done["runs"][0]["status"] == "succeeded"

        # log channel：有行 + 游標單調
        log = client.get(f"/api/implement/{sid}/log").json()
        assert log["status"] == "succeeded"
        assert log["next_cursor"] > 0
        assert any("[mock]" in ln["content"] for ln in log["lines"])
        # after_id 補播：用 next_cursor 再 poll → 無新行
        log2 = client.get(f"/api/implement/{sid}/log", params={"after_id": log["next_cursor"]}).json()
        assert log2["lines"] == []

        # session 列在 thread 下
        sessions = client.get(f"/api/implement/threads/{tid}/sessions").json()["sessions"]
        assert [s["session_id"] for s in sessions] == [sid]


def test_endpoint_start_validation(tmp_db):
    with TestClient(appmod.app) as client:
        tid = client.post("/api/projects", json={"name": "impl"}).json()["thread_id"]
        # 未知 runner → 400
        r = client.post("/api/implement/start", json={"thread_id": tid, "runner": "nope", "story": "x"})
        assert r.status_code == 400 and r.json()["detail"]["category"] == "runner_not_found"
        # 無 story 且無 stories artifact → 400
        r = client.post("/api/implement/start", json={"thread_id": tid, "runner": "mock"})
        assert r.status_code == 400 and r.json()["detail"]["category"] == "story_empty"
        # 不存在的 thread → 404
        r = client.post("/api/implement/start", json={"thread_id": "ghost", "runner": "mock", "story": "x"})
        assert r.status_code == 404


def test_endpoint_cancel_finished_session(tmp_db):
    with TestClient(appmod.app) as client:
        tid = client.post("/api/projects", json={"name": "impl"}).json()["thread_id"]
        sid = client.post("/api/implement/start", json={
            "thread_id": tid, "runner": "mock", "story": "x"}).json()["session_id"]
        _poll_done(client, sid)
        # 已結束 → cancel 回 200 但 cancel_requested False（無 active runner）
        r = client.post(f"/api/implement/{sid}/cancel")
        assert r.status_code == 200
        assert r.json()["cancel_requested"] is False
        # 不存在 session → 404
        assert client.post("/api/implement/999999/cancel").status_code == 404
        assert client.get("/api/implement/999999").status_code == 404
