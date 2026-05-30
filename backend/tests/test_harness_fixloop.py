"""harnessed_step fix-loop 通電：每輪一筆 run + parent 串接 + judge 注入 + needs_revision。

全部用 tmp_db + 假 ModelAdapter（不碰真實 CLI），直接建 HarnessRunner。
"""
from __future__ import annotations

from harness_runner import HarnessRunner
from persistence.dal import connect
from plugin_api import (
    HarnessValidationOutcome, ModelAdapter, make_judge_validator,
    SEVERITY_FAIL, SEVERITY_WARN,
)
from plugin_host import Registry


def _adapter(choice, fn):
    return ModelAdapter(model_choice=choice, invoke=fn, is_available=lambda: True,
                        description="", max_context_tokens=1000,
                        prompt_budget_tokens=900, response_budget_tokens=100)


def _runs():
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT run_id, operation, status, model_choice, parent_run_id "
            "FROM harness_runs ORDER BY started_at, rowid").fetchall()]


def _fail_unless_good():
    """output 含 'good' → warn（通過）；否則 fail（觸發 fix-loop）。"""
    def v(output, ctx):
        sev = SEVERITY_WARN if "good" in output else SEVERITY_FAIL
        return [HarnessValidationOutcome(
            validator="t", severity=sev, message="m",
            fix_hint=None if sev == SEVERITY_WARN else "improve")]
    return v


def test_pass_single_run(tmp_db):
    reg = Registry()
    reg.model_adapters["gen"] = _adapter("gen", lambda p: "good")
    reg.validators[("specify", "generate_prd")] = [_fail_unless_good()]
    res = HarnessRunner(reg, "th", "prd", "gen").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=3)
    runs = _runs()
    assert len(runs) == 1 and runs[0]["status"] == "succeeded"
    assert runs[0]["parent_run_id"] is None
    assert all(o.severity == SEVERITY_WARN for o in res.validations)


def test_fail_then_pass_parent_chain(tmp_db):
    reg = Registry()
    gen = iter(["bad", "good"])
    reg.model_adapters["gen"] = _adapter("gen", lambda p: next(gen))
    reg.validators[("specify", "generate_prd")] = [_fail_unless_good()]
    HarnessRunner(reg, "th", "prd", "gen").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=3)
    runs = _runs()
    assert len(runs) == 2
    assert runs[0]["status"] == "needs_revision" and runs[0]["parent_run_id"] is None
    assert runs[1]["status"] == "succeeded" and runs[1]["parent_run_id"] == runs[0]["run_id"]


def test_exhausted_still_fail(tmp_db):
    reg = Registry()
    reg.model_adapters["gen"] = _adapter("gen", lambda p: "bad")
    reg.validators[("specify", "generate_prd")] = [_fail_unless_good()]
    res = HarnessRunner(reg, "th", "prd", "gen").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=2)
    runs = _runs()
    assert len(runs) == 2 and runs[1]["status"] == "needs_revision"
    assert any(o.severity == SEVERITY_FAIL for o in res.validations)


def test_judge_injected_and_recorded(tmp_db):
    reg = Registry()
    reg.model_adapters["gen"] = _adapter("gen", lambda p: "content")
    reg.model_adapters["judge"] = _adapter("judge", lambda p: '{"passed": true, "score": 0.9}')
    reg.validators[("specify", "generate_prd")] = [
        make_judge_validator(rubric="R", name="prd.judge", fail_on_reject=True)]
    res = HarnessRunner(reg, "th", "prd", "gen", judge_model_choice="judge").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=2)
    ops = [(x["operation"], x["status"], x["model_choice"]) for x in _runs()]
    assert ("generate_prd", "succeeded", "gen") in ops
    assert ("judge_generate_prd", "succeeded", "judge") in ops    # judge model call 也記一筆
    assert [o.severity for o in res.validations] == [SEVERITY_WARN]


def test_judge_disabled_by_default(tmp_db):
    """不傳 judge_model_choice → judge 關 → judge validator 跳過、零 judge run（預設零成本）。"""
    reg = Registry()
    reg.model_adapters["gen"] = _adapter("gen", lambda p: "content")
    reg.validators[("specify", "generate_prd")] = [
        make_judge_validator(rubric="R", name="prd.judge", fail_on_reject=True)]
    res = HarnessRunner(reg, "th", "prd", "gen").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=2)
    runs = _runs()
    assert res.validations == []
    assert len(runs) == 1 and all(x["operation"] != "judge_generate_prd" for x in runs)


def test_judge_exception_fail_open(tmp_db):
    reg = Registry()
    reg.model_adapters["gen"] = _adapter("gen", lambda p: "content")

    def boom(p):
        raise RuntimeError("judge down")

    reg.model_adapters["judge"] = _adapter("judge", boom)
    reg.validators[("specify", "generate_prd")] = [
        make_judge_validator(rubric="R", name="prd.judge", fail_on_reject=True)]
    res = HarnessRunner(reg, "th", "prd", "gen", judge_model_choice="judge").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=2)
    assert [o.severity for o in res.validations] == [SEVERITY_WARN]   # fail-open 降 warn
    jr = [x for x in _runs() if x["operation"] == "judge_generate_prd"]
    assert len(jr) == 1 and jr[0]["status"] == "failed"


def test_explicit_max_iterations_respected(tmp_db):
    """顯式傳數字 → 用該值（向後相容 collab/測試），不讀 agent。"""
    reg = Registry()
    reg.model_adapters["gen"] = _adapter("gen", lambda p: "bad")
    reg.validators[("specify", "generate_prd")] = [_fail_unless_good()]
    HarnessRunner(reg, "th", "prd", "gen").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=1)
    assert len(_runs()) == 1   # 顯式 1 → 不重試


def test_max_iterations_from_agent(tmp_db):
    """不傳 max_iterations → 讀 stage 綁的 agent.max_iterations（fix-loop 通電靠 agent 設定）。"""
    from plugin_api import AgentSpec
    reg = Registry()
    reg.model_adapters["gen"] = _adapter("gen", lambda p: "bad")
    reg.validators[("specify", "generate_prd")] = [_fail_unless_good()]
    reg.agents["a"] = AgentSpec(agent_id="a", name="A", role="prd",
                                system_prompt="", max_iterations=3)
    HarnessRunner(reg, "th", "prd", "gen").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={})   # 不傳 → 讀 agent.max_iterations=3
    assert len(_runs()) == 3   # 跑滿 3 輪（恆 fail）
