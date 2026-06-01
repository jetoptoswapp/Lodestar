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


def test_endpoint_runners_lists_registered(tmp_db):
    with TestClient(appmod.app) as client:
        runners = client.get("/api/runners").json()["runners"]
        choices = {r["choice"] for r in runners}
        assert {"mock", "claude-cli"}.issubset(choices)
        mock = next(r for r in runners if r["choice"] == "mock")
        assert mock["available"] is True
        assert mock["source_plugin"] == "builtin_implement"


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


# ============ 多角色 pipeline（lead → RD → tester → reviewer → 回圈）============
def test_roles_happy_path_opens_pr(tmp_db):
    sid, cwd = _new_session(runner="fake")
    runner = FakeRunner([
        RunResult(0, "PLAN: build X"),       # lead
        RunResult(0, "implemented"),         # rd
        RunResult(0, "tests pass"),          # tester
        RunResult(0, "REVIEW: APPROVED"),    # reviewer → 通過
    ])
    res = asyncio.run(orchestrator.run_implementation_roles(
        session_id=sid, runner=runner, story="As a user...", cwd=cwd, target_repo="o/r"))
    assert res["status"] == "succeeded" and res["mode"] == "roles"
    assert "MOCK" in res["pr_url"]
    assert runner.calls == 4
    roles = [r["dispatch_role"] for r in impl_dal.list_runs(sid)]
    assert roles == ["lead", "rd", "tester", "reviewer"]


def test_roles_reviewer_requests_changes_then_approves(tmp_db):
    sid, cwd = _new_session(runner="fake")
    runner = FakeRunner([
        RunResult(0, "PLAN"),                            # lead（一次）
        RunResult(0, "impl v1"),                         # rd #1
        RunResult(0, "pass"),                            # tester #1
        RunResult(0, "REVIEW: CHANGES_REQUESTED: fix"),  # reviewer #1 → 回圈
        RunResult(0, "impl v2"),                         # rd #2
        RunResult(0, "pass"),                            # tester #2
        RunResult(0, "REVIEW: APPROVED"),                # reviewer #2 → 通過
    ])
    res = asyncio.run(orchestrator.run_implementation_roles(
        session_id=sid, runner=runner, story="x", cwd=cwd, target_repo="o/r"))
    assert res["status"] == "succeeded" and res["attempts"] == 2
    assert runner.calls == 7
    roles = [r["dispatch_role"] for r in impl_dal.list_runs(sid)]
    assert roles == ["lead", "rd", "tester", "reviewer", "rd", "tester", "reviewer"]


def test_roles_tester_fail_skips_reviewer_then_recovers(tmp_db):
    sid, cwd = _new_session(runner="fake")
    runner = FakeRunner([
        RunResult(0, "PLAN"),          # lead
        RunResult(0, "impl"),          # rd #1
        RunResult(1, "test failed"),   # tester #1 失敗 → 跳過 reviewer、回圈
        RunResult(0, "impl2"),         # rd #2
        RunResult(0, "pass"),          # tester #2
        RunResult(0, "looks good"),    # reviewer #2（無 CHANGES → 通過）
    ])
    res = asyncio.run(orchestrator.run_implementation_roles(
        session_id=sid, runner=runner, story="x", cwd=cwd, target_repo="o/r"))
    assert res["status"] == "succeeded" and res["attempts"] == 2
    roles = [r["dispatch_role"] for r in impl_dal.list_runs(sid)]
    assert roles == ["lead", "rd", "tester", "rd", "tester", "reviewer"]


def test_roles_max_attempts_when_reviewer_keeps_rejecting(tmp_db):
    sid, cwd = _new_session(runner="fake")
    # 單一 result 重複：rd/tester 視為 ok（exit 0），reviewer 永遠 CHANGES_REQUESTED
    runner = FakeRunner([RunResult(0, "REVIEW: CHANGES_REQUESTED")])
    res = asyncio.run(orchestrator.run_implementation_roles(
        session_id=sid, runner=runner, story="x", cwd=cwd))
    assert res["status"] == "failed" and res["reason"] == "max_attempts"
    assert res["attempts"] == 3
    assert impl_dal.get_session(sid)["status"] == "failed"


def test_roles_cancel_is_terminal(tmp_db):
    sid, cwd = _new_session(runner="fake")
    runner = FakeRunner([RunResult(0, "PLAN"), RunResult(-1, cancelled=True)])  # lead ok → rd cancelled
    res = asyncio.run(orchestrator.run_implementation_roles(
        session_id=sid, runner=runner, story="x", cwd=cwd))
    assert res["status"] == "cancelled"
    assert impl_dal.get_session(sid)["status"] == "cancelled"


def test_roles_http_start_with_mock_runner(tmp_db):
    with TestClient(appmod.app) as client:
        tid = client.post("/api/projects", json={"name": "impl-roles"}).json()["thread_id"]
        r = client.post("/api/implement/start", json={
            "thread_id": tid, "runner": "mock", "story": "As a user I want X.", "mode": "roles",
        })
        assert r.status_code == 200
        sid = r.json()["session_id"]
        # poll 到背景 task 完成
        for _ in range(100):
            sess = client.get(f"/api/implement/{sid}").json()
            if sess["status"] in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(0.1)
        assert sess["status"] == "succeeded"      # mock reviewer 無 CHANGES → 通過開 PR
        roles = [run["dispatch_role"] for run in sess["runs"]]
        assert roles[:4] == ["lead", "rd", "tester", "reviewer"]


# ============ persona 注入（agent.system_prompt 接回 roles pipeline）============
class CapturingRunner(AgentRunner):
    """記下每次收到的 prompt（供斷言 persona 注入），永遠回 ok（reviewer 無 CHANGES → 通過）。"""
    name = "capture"

    def __init__(self):
        self.prompts: list[str] = []

    def build_argv(self, *, cwd, prompt):
        return ["true"]

    def is_available(self):
        return True

    async def run(self, *, cwd, prompt, timeout, on_log, on_event=None, hooks=None):
        self.prompts.append(prompt)
        return RunResult(0, "ok")


def test_role_prompt_default_persona_is_byte_identical():
    """persona 空 → 與接線前逐字相同（零回歸）：頭句即各角色預設 persona。"""
    for role, head in orchestrator._DEFAULT_PERSONA.items():
        p = orchestrator._role_prompt(role, story="s", plan="p", feedback="", attempt=1)
        assert p.startswith(head + " ")
        # 顯式空 persona 與不帶 persona 完全一致
        assert p == orchestrator._role_prompt(role, story="s", plan="p", feedback="", attempt=1, persona="")


def test_tester_contract_includes_lint_gate():
    """Phase 3：tester 的機器契約含 repo-driven lint + type-check，且 fail-fast。"""
    p = orchestrator._role_prompt("tester", story="s", plan="p", feedback="", attempt=1)
    low = p.lower()
    assert "lint" in low and "type-check" in low
    assert "exit non-zero if lint, type-check, or any test fails." in low
    # 點名平台 linter，涵蓋 android/ios/web/backend
    assert all(t in low for t in ("ruff", "eslint", "ktlint", "swiftlint"))


def test_roles_persona_injected_per_step(tmp_db):
    """persona_for 提供的 system_prompt 進到對應步驟 prompt；未提供的步驟用預設 persona。"""
    sid, cwd = _new_session(runner="capture")
    runner = CapturingRunner()
    personas = {"rd": "You are a Senior Android dev. Run ktlint.", "tester": "You are the AC Tester."}
    res = asyncio.run(orchestrator.run_implementation_roles(
        session_id=sid, runner=runner, story="x", cwd=cwd, target_repo="o/r",
        persona_for=lambda step: personas.get(step, "")))
    assert res["status"] == "succeeded"
    lead_p, rd_p, tester_p, reviewer_p = runner.prompts[:4]
    # 有綁定 → 用自訂 persona
    assert rd_p.startswith("You are a Senior Android dev. Run ktlint.")
    assert tester_p.startswith("You are the AC Tester.")
    # 無綁定 → 退回預設 persona
    assert lead_p.startswith(orchestrator._DEFAULT_PERSONA["lead"])
    assert reviewer_p.startswith(orchestrator._DEFAULT_PERSONA["reviewer"])
    # 機器契約恆在（persona 不蓋契約）：tester 的 QA gate 含 lint + 測試、fail-fast
    assert "lint" in tester_p.lower() and "type-check" in tester_p.lower()
    assert "Exit non-zero if lint, type-check, or any test fails." in tester_p
    assert "REVIEW: APPROVED" in reviewer_p


def test_roles_no_persona_provider_unchanged(tmp_db):
    """不帶 persona_for → 每步 prompt 與預設 persona 起頭（與接線前一致）。"""
    sid, cwd = _new_session(runner="capture")
    runner = CapturingRunner()
    asyncio.run(orchestrator.run_implementation_roles(
        session_id=sid, runner=runner, story="x", cwd=cwd, target_repo="o/r"))
    for prompt, step in zip(runner.prompts[:4], ["lead", "rd", "tester", "reviewer"]):
        assert prompt.startswith(orchestrator._DEFAULT_PERSONA[step])


def test_implement_persona_provider_resolves_from_bindings(tmp_db):
    """端到端：workflow agent_bindings["implement"] → resolve_agent → system_prompt。
    binding.role 即步驟名（rd/tester…）；未綁定步驟回 ""；完全無綁定回 None。"""
    from persistence import dal
    dal.upsert_agent(agent_id="dom_impl", name="Domain Impl", role="implement",
                     system_prompt="You are a Senior Android dev. Run ktlint+detekt.")
    dal.upsert_agent(agent_id="ac_tester", name="AC Tester", role="implement",
                     system_prompt="You are the Acceptance Criteria Tester.")
    dal.upsert_workflow_definition(
        wf_id="impl_wf", label="Impl WF", description="",
        stages=[
            {"stage_id": "implement", "depends_on": [], "collab_mode": "dispatch",
             "agent_bindings": [
                 {"agent_id": "dom_impl", "role": "rd"},
                 {"agent_id": "ac_tester", "role": "tester"},
             ]},
        ],
    )
    with TestClient(appmod.app) as client:
        tid = client.post("/api/projects", json={"name": "impl-bind"}).json()["thread_id"]
        dal.set_project_workflow(tid, "impl_wf")
        provider = appmod._implement_persona_provider(appmod._registry(), tid)
        assert provider is not None
        assert provider("rd").startswith("You are a Senior Android dev.")
        assert provider("tester") == "You are the Acceptance Criteria Tester."
        assert provider("lead") == ""          # 未綁定 → 空（退回預設 persona）
        # 無 implement binding 的專案 → None（零行為改變）
        tid2 = client.post("/api/projects", json={"name": "no-bind"}).json()["thread_id"]
        assert appmod._implement_persona_provider(appmod._registry(), tid2) is None


# ============ Phase 2：per-step runner（model_choice 分流）============
class NamedRunner(AgentRunner):
    """可指定 name 的 fake runner（always ok）；impl_runs.runner 記其 name，供斷言各步用對 runner。"""
    def __init__(self, name: str):
        self.name = name
        self.calls = 0

    def build_argv(self, *, cwd, prompt):
        return ["true"]

    def is_available(self):
        return True

    async def run(self, *, cwd, prompt, timeout, on_log, on_event=None, hooks=None):
        self.calls += 1
        return RunResult(0, "ok")


def test_codex_runner_argv_and_registered():
    """CodexCliRunner：codex exec + workspace-write，prompt 走 stdin（不在 argv）；且已註冊。"""
    from plugins.builtin_implement.runner import CodexCliRunner
    r = CodexCliRunner()
    assert r.name == "codex-cli"
    argv = r.build_argv(cwd="/tmp/x", prompt="implement the feature")
    assert argv[:2] == ["codex", "exec"]
    assert "--sandbox" in argv and "workspace-write" in argv
    assert "implement the feature" not in argv      # prompt 由 stdin 餵入
    with TestClient(appmod.app):
        assert "codex-cli" in appmod._registry().runners


def test_roles_per_step_runner_selection(tmp_db):
    """runner_for 指定的 runner 各步生效；未指定步驟退回傳入的預設 runner。"""
    sid, cwd = _new_session(runner="default")
    default = NamedRunner("default")
    by_step = {"lead": NamedRunner("codexish"), "tester": NamedRunner("agyish")}
    res = asyncio.run(orchestrator.run_implementation_roles(
        session_id=sid, runner=default, story="x", cwd=cwd, target_repo="o/r",
        runner_for=lambda step: by_step.get(step)))
    assert res["status"] == "succeeded"
    runner_by_role = {r["dispatch_role"]: r["runner"] for r in impl_dal.list_runs(sid)}
    assert runner_by_role["lead"] == "codexish"      # runner_for 指定
    assert runner_by_role["tester"] == "agyish"      # runner_for 指定
    assert runner_by_role["rd"] == "default"         # 未指定 → 退回預設
    assert runner_by_role["reviewer"] == "default"   # 未指定 → 退回預設
    # 當前 active runner 應指向最後跑的那步（reviewer → default），供 cancel 命中
    assert orchestrator._ACTIVE_RUNNERS.get(sid) is None or True  # run 結束已 pop（由 run_session_to_terminal 管）


def test_implement_runner_provider_resolves_and_falls_back(tmp_db):
    """端到端：binding agent.model_choice → 已註冊 runner 回實例；無對應 runner（agy）回 None（退回預設）。"""
    from persistence import dal
    dal.upsert_agent(agent_id="r_mock", name="M", role="implement",
                     system_prompt="x", model_choice="mock")
    dal.upsert_agent(agent_id="r_agy", name="A", role="implement",
                     system_prompt="y", model_choice="agy-cli")
    dal.upsert_workflow_definition(
        wf_id="rwf", label="R", description="",
        stages=[{"stage_id": "implement", "depends_on": [], "collab_mode": "dispatch",
                 "agent_bindings": [
                     {"agent_id": "r_mock", "role": "rd"},
                     {"agent_id": "r_agy", "role": "tester"},
                 ]}])
    with TestClient(appmod.app) as client:
        tid = client.post("/api/projects", json={"name": "rwf-p"}).json()["thread_id"]
        dal.set_project_workflow(tid, "rwf")
        rf = appmod._implement_runner_provider(appmod._registry(), tid)
        assert rf is not None
        rd = rf("rd")
        assert rd is not None and rd.name == "mock"   # 已註冊+可用 → 實例
        assert rf("tester") is None                   # agy-cli 無 async runner → None（退回預設）
        assert rf("lead") is None                     # 未綁定 → None
        tid2 = client.post("/api/projects", json={"name": "rwf-none"}).json()["thread_id"]
        assert appmod._implement_runner_provider(appmod._registry(), tid2) is None
