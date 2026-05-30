"""telemetry_read.harness_metrics / harness_run_tree 聚合正確性（用真實 harnessed_step 產資料）。"""
from __future__ import annotations

from harness_runner import HarnessRunner
from plugin_api import (
    HarnessValidationOutcome, ModelAdapter, make_judge_validator,
    SEVERITY_FAIL, SEVERITY_WARN,
)
from plugin_host import Registry
from telemetry_read import harness_metrics, harness_run_tree


def _adapter(choice, fn):
    return ModelAdapter(model_choice=choice, invoke=fn, is_available=lambda: True,
                        description="", max_context_tokens=1000,
                        prompt_budget_tokens=900, response_budget_tokens=100)


def _fail_unless_good():
    def v(output, ctx):
        sev = SEVERITY_WARN if "good" in output else SEVERITY_FAIL
        return [HarnessValidationOutcome(validator="t", severity=sev, message="m",
                fix_hint=None if sev == SEVERITY_WARN else "fix")]
    return v


def test_metrics_fixloop_and_status(tmp_db):
    reg = Registry()
    gen = iter(["bad", "good"])
    reg.model_adapters["gen"] = _adapter("gen", lambda p: next(gen))
    reg.validators[("specify", "generate_prd")] = [_fail_unless_good()]
    HarnessRunner(reg, "th", "prd", "gen").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=3)
    m = harness_metrics()
    assert m["total_runs"] == 2
    assert m["by_status"].get("needs_revision") == 1 and m["by_status"].get("succeeded") == 1
    assert m["fix_loop"] == {"chains": 1, "avg_iterations": 2.0, "max_iterations": 2}
    assert m["needs_revision_rate"] == 0.5


def test_judge_runs_excluded_from_main(tmp_db):
    reg = Registry()
    reg.model_adapters["gen"] = _adapter("gen", lambda p: "content")
    reg.model_adapters["judge"] = _adapter("judge", lambda p: '{"passed": true}')
    reg.validators[("specify", "generate_prd")] = [make_judge_validator(rubric="R", name="prd.judge")]
    HarnessRunner(reg, "th", "prd", "gen", judge_model_choice="judge").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=2)
    m = harness_metrics()
    assert m["total_runs"] == 1 and m["judge_runs"] == 1   # judge run 不污染主 run 指標
    vmap = {v["validator"]: v for v in m["validators"]}
    assert vmap["prd.judge"]["warn"] == 1


def test_validators_warn_fail_counts(tmp_db):
    reg = Registry()
    reg.model_adapters["gen"] = _adapter("gen", lambda p: "bad")
    reg.validators[("specify", "generate_prd")] = [_fail_unless_good()]
    HarnessRunner(reg, "th", "prd", "gen").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=2)
    vmap = {v["validator"]: v for v in harness_metrics()["validators"]}
    assert vmap["t"]["fail"] == 2


def test_stage_filter(tmp_db):
    reg = Registry()
    reg.model_adapters["gen"] = _adapter("gen", lambda p: "good")
    reg.validators[("specify", "generate_prd")] = [_fail_unless_good()]
    reg.validators[("design", "generate_arch")] = [_fail_unless_good()]
    HarnessRunner(reg, "th", "prd", "gen").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=1)
    HarnessRunner(reg, "th", "arch", "gen").harnessed_step(
        telemetry_stage="design", operation="generate_arch",
        prompt="p", metadata={}, max_iterations=1)
    assert harness_metrics(stage="specify")["total_runs"] == 1
    assert harness_metrics(stage="design")["total_runs"] == 1
    assert harness_metrics()["total_runs"] == 2


def test_run_tree_upstream_from_last(tmp_db):
    reg = Registry()
    gen = iter(["bad", "good"])
    reg.model_adapters["gen"] = _adapter("gen", lambda p: next(gen))
    reg.validators[("specify", "generate_prd")] = [_fail_unless_good()]
    res = HarnessRunner(reg, "th", "prd", "gen").harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt="p", metadata={}, max_iterations=3)
    tree = harness_run_tree(res.run_id)   # 從最後一輪上溯整條鏈
    assert len(tree) == 2 and tree[0]["parent_run_id"] is None
