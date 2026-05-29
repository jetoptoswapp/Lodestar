"""M5.1 async 基礎層測試：AgentRunner 驅動 / ToolHook / task_registry / impl_dal。

全用 mock / 本機 python 子程序驗證機制，不呼叫 claude、不碰外部。
async 測試以 asyncio.run() 驅動（不依賴 pytest-asyncio）。
"""
from __future__ import annotations

import asyncio
import gc
import sys

import pytest

from async_runtime import impl_dal, task_registry
from plugin_api import AgentRunner, HookAbort, ToolHook
from plugins.builtin_implement.hooks import DenyProtectedBranchHook, RedactSecretsHook
from plugins.builtin_implement.runner import MockRunner


# ============ 測試用 runner ============
class ScriptRunner(AgentRunner):
    """跑一段 python；行為由 body + args 控制。prompt 由 base run() 經 stdin 餵入。"""
    name = "script"

    def __init__(self, body: str, args=()):
        self._body = body
        self._args = [str(a) for a in args]

    def build_argv(self, *, cwd: str, prompt: str) -> list[str]:
        return [sys.executable, "-c", self._body, *self._args]

    def is_available(self) -> bool:
        return True


class FixedArgvRunner(AgentRunner):
    """argv 固定（給 deny-branch hook 掃描用）；不真的 spawn（hook 在 pre_run 就擋掉）。"""
    name = "git"

    def __init__(self, argv):
        self._argv = list(argv)

    def build_argv(self, *, cwd: str, prompt: str) -> list[str]:
        return self._argv

    def is_available(self) -> bool:
        return True


_ECHO = "import sys; sys.stdout.write(sys.stdin.read())"


def _run(runner, *, cwd, prompt="", timeout=10, hooks=None):
    """同步包裝：跑 runner.run 並收集 log。回傳 (result, logs)。"""
    logs: list[str] = []

    async def main():
        res = await runner.run(cwd=cwd, prompt=prompt, timeout=timeout,
                               on_log=logs.append, hooks=hooks)
        return res

    return asyncio.run(main()), logs


# ============ 驅動：基本 / stdin / exit code ============
def test_run_streams_and_captures(tmp_path):
    runner = ScriptRunner("print('L1'); print('L2')")
    res, logs = _run(runner, cwd=str(tmp_path))
    assert res.ok and res.exit_code == 0
    assert "L1\n" in logs and "L2\n" in logs
    assert "L1" in res.last_output and "L2" in res.last_output


def test_run_feeds_prompt_via_stdin(tmp_path):
    runner = ScriptRunner(_ECHO)
    res, _ = _run(runner, cwd=str(tmp_path), prompt="hello-from-stdin\n")
    assert res.ok
    assert "hello-from-stdin" in res.last_output


def test_run_nonzero_exit_not_ok(tmp_path):
    runner = ScriptRunner("import sys; print('boom'); sys.exit(3)")
    res, _ = _run(runner, cwd=str(tmp_path))
    assert res.exit_code == 3
    assert res.ok is False


def test_run_spawn_failure_returns_127(tmp_path):
    class BadRunner(AgentRunner):
        name = "bad"
        def build_argv(self, *, cwd, prompt):
            return ["/nonexistent/binary/xyzzy"]
        def is_available(self):
            return False
    res, _ = _run(BadRunner(), cwd=str(tmp_path))
    assert res.exit_code == 127 and res.ok is False


# ============ 驅動：timeout / cancel ============
def test_run_timeout_kills(tmp_path):
    runner = ScriptRunner("import time; time.sleep(30)")
    res, _ = _run(runner, cwd=str(tmp_path), timeout=1)
    assert res.timed_out is True
    assert res.ok is False


def test_run_cancel_marks_cancelled(tmp_path):
    runner = ScriptRunner("import time; time.sleep(30)")

    async def main():
        task = asyncio.ensure_future(
            runner.run(cwd=str(tmp_path), prompt="", timeout=30, on_log=lambda s: None)
        )
        await asyncio.sleep(0.2)          # 讓子程序起來
        await runner.cancel()             # 要求取消 → 殺子程序
        return await task

    res = asyncio.run(main())
    assert res.cancelled is True
    assert res.ok is False


# ============ ToolHook：pre_run abort / rewrite ============
def test_hook_abort_propagates_and_no_spawn(tmp_path):
    class AlwaysAbort(ToolHook):
        name = "boom"
        def pre_run(self, runner_name, argv, env):
            raise HookAbort(self.name, "拒絕")

    runner = ScriptRunner("print('should-not-run')")
    with pytest.raises(HookAbort):
        _run(runner, cwd=str(tmp_path), hooks=[AlwaysAbort()])


def test_hook_rewrites_argv(tmp_path):
    class RewriteHook(ToolHook):
        name = "rw"
        def pre_run(self, runner_name, argv, env):
            return [sys.executable, "-c", "print('REWRITTEN')"]

    runner = ScriptRunner("print('ORIGINAL')")
    res, _ = _run(runner, cwd=str(tmp_path), hooks=[RewriteHook()])
    assert "REWRITTEN" in res.last_output
    assert "ORIGINAL" not in res.last_output


# ============ ToolHook：on_log_chunk redact / drop ============
def test_hook_redacts_secrets(tmp_path):
    body = (
        "print('safe header')\n"
        "print('ghp_ABCDEFGHIJKLMNOP1234567')\n"
        "print('token=supersecretvalue')\n"
        "print('Authorization: Bearer abc.def.ghi')\n"
    )
    res, logs = _run(ScriptRunner(body), cwd=str(tmp_path), hooks=[RedactSecretsHook()])
    out = res.last_output
    assert "ghp_ABCDEFGHIJKLMNOP1234567" not in out
    assert "supersecretvalue" not in out
    assert "abc.def.ghi" not in out
    assert "[REDACTED]" in out
    assert "token=[REDACTED]" in out      # key 保留、value 塗黑
    assert "safe header" in out           # 非秘密原樣保留


def test_hook_drops_chunk_when_none(tmp_path):
    class DropHook(ToolHook):
        name = "drop"
        def on_log_chunk(self, runner_name, chunk):
            return None if "DROPME" in chunk else chunk

    runner = ScriptRunner("print('KEEP'); print('DROPME')")
    res, logs = _run(runner, cwd=str(tmp_path), hooks=[DropHook()])
    joined = "".join(logs)
    assert "KEEP" in joined
    assert "DROPME" not in joined and "DROPME" not in res.last_output


# ============ deny-branch hook 單元 ============
def test_deny_hook_blocks_protected_branches():
    h = DenyProtectedBranchHook()
    for br in ("main", "master", "release", "production"):
        with pytest.raises(HookAbort):
            h.pre_run("git", ["git", "push", "origin", br], {})


def test_deny_hook_allows_feature_branch():
    h = DenyProtectedBranchHook()
    assert h.pre_run("git", ["git", "push", "origin", "feature/x"], {}) is None
    assert h.pre_run("git", ["claude", "-p", "--add-dir", "/tmp"], {}) is None


def test_deny_hook_blocks_via_run_driver(tmp_path):
    """整條路徑：run() 套 deny hook → 對 main push → HookAbort 往上拋（不 spawn）。"""
    runner = FixedArgvRunner(["git", "push", "origin", "main"])
    with pytest.raises(HookAbort):
        _run(runner, cwd=str(tmp_path), hooks=[DenyProtectedBranchHook()])


# ============ registered MockRunner 端到端 ============
def test_mock_runner_end_to_end(tmp_path):
    res, logs = _run(MockRunner(), cwd=str(tmp_path), prompt="Build feature X")
    assert res.ok and res.exit_code == 0
    joined = "".join(logs)
    assert "[mock]" in joined and "done" in joined
    assert "Build feature X" in joined


# ============ task_registry：強引用 + discard ============
def test_spawn_holds_strong_ref_through_gc():
    """不持有 task 本地引用 + gc.collect()，task 仍須完成（強引用在 _TASKS）。"""
    async def main():
        done: list[bool] = []

        async def work():
            await asyncio.sleep(0.05)
            done.append(True)

        task_registry.spawn(work(), name="gc-probe")   # 故意不接回傳值
        gc.collect()                                    # 若只有 weak ref 會被回收
        assert task_registry.active_count() >= 1
        for _ in range(200):                            # 等它跑完（最多 ~2s）
            if done:
                break
            await asyncio.sleep(0.01)
        assert done == [True], "背景 task 在無本地引用 + GC 後消失了（強引用失效）"
        await asyncio.sleep(0)                           # 讓 done_callback 跑
        assert task_registry.active_count() == 0         # 完成後已 discard

    asyncio.run(main())


def test_cancel_all_cancels_running_tasks():
    async def main():
        async def forever():
            await asyncio.sleep(60)
        task_registry.spawn(forever(), name="f1")
        task_registry.spawn(forever(), name="f2")
        assert task_registry.active_count() == 2
        await task_registry.cancel_all()
        assert task_registry.active_count() == 0

    asyncio.run(main())


# ============ impl_dal roundtrip ============
def test_impl_dal_session_run_message_roundtrip(tmp_db):
    sid = impl_dal.create_session(thread_id="t1", title="Story FR-1",
                                  target_repo="o/r", runner="mock")
    rid = impl_dal.create_run(session_id=sid, attempt=1, runner="mock")
    s0 = impl_dal.append_message(rid, content="line A\n")
    s1 = impl_dal.append_message(rid, content="evt\n", kind="event")
    assert (s0, s1) == (0, 1)                            # seq 自 0 遞增

    impl_dal.finish_run(rid, status="succeeded", exit_code=0,
                        cancelled=False, timed_out=False, last_output="done")
    impl_dal.update_session(sid, status="succeeded", pr_url="https://mock/pr/1")

    sess = impl_dal.get_session(sid)
    run = impl_dal.get_run(rid)
    assert sess["status"] == "succeeded" and sess["pr_url"] == "https://mock/pr/1"
    assert run["status"] == "succeeded" and run["exit_code"] == 0

    msgs = impl_dal.list_messages(rid)
    assert [m["seq"] for m in msgs] == [0, 1]
    assert impl_dal.list_messages(rid, after_seq=0) == msgs[1:]   # SSE 補播
    assert [r["run_id"] for r in impl_dal.list_runs(sid)] == [rid]
    assert [x["session_id"] for x in impl_dal.list_sessions("t1")] == [sid]


def test_impl_run_id_is_integer_namespace(tmp_db):
    """impl_runs.run_id 是 INTEGER（與 harness_* 的 TEXT run_id 刻意不同命名空間）。"""
    sid = impl_dal.create_session(thread_id="t", title="x", target_repo="o/r", runner="mock")
    rid = impl_dal.create_run(session_id=sid, attempt=1, runner="mock")
    assert isinstance(rid, int)


def test_impl_message_cascade_delete(tmp_db):
    """刪 session → run / message 連動刪（ON DELETE CASCADE + foreign_keys=ON）。"""
    from persistence.dal import connect
    sid = impl_dal.create_session(thread_id="t", title="x", target_repo="o/r", runner="mock")
    rid = impl_dal.create_run(session_id=sid, attempt=1, runner="mock")
    impl_dal.append_message(rid, content="hi\n")
    with connect() as conn:
        conn.execute("DELETE FROM impl_sessions WHERE session_id = ?", (sid,))
    assert impl_dal.get_run(rid) is None
    assert impl_dal.list_messages(rid) == []
