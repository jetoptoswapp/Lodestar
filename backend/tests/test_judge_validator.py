"""make_judge_validator：opt-in 跳過、pass→warn、fail→fail、parse_ok=False 降 warn。"""
from __future__ import annotations

from plugin_api import (
    HarnessContext, JudgeVerdict, make_judge_validator,
    SEVERITY_FAIL, SEVERITY_WARN,
)


def _ctx(judge=None):
    return HarnessContext(thread_id="t", stage="specify", operation="generate_prd",
                          model_choice="m", prompt="p", judge=judge)


def test_judge_none_skips():
    v = make_judge_validator(rubric="R", name="j")
    assert v("artifact", _ctx(judge=None)) == []


def test_judge_pass_emits_warn():
    v = make_judge_validator(rubric="R", name="j")
    out = v("artifact", _ctx(judge=lambda s, u: JudgeVerdict(passed=True, score=0.9)))
    assert len(out) == 1 and out[0].severity == SEVERITY_WARN


def test_judge_fail_emits_fail():
    v = make_judge_validator(rubric="R", name="j", fail_on_reject=True)
    out = v("a", _ctx(judge=lambda s, u: JudgeVerdict(
        passed=False, parse_ok=True, issues=["bad"], fix_hint="fix it")))
    assert out[0].severity == SEVERITY_FAIL and out[0].fix_hint == "fix it"


def test_parse_failed_degrades_warn():
    v = make_judge_validator(rubric="R", name="j", fail_on_reject=True)
    out = v("a", _ctx(judge=lambda s, u: JudgeVerdict(passed=False, parse_ok=False)))
    assert out[0].severity == SEVERITY_WARN   # 鐵則：judge 失靈不鎖死


def test_fail_on_reject_false_always_warn():
    v = make_judge_validator(rubric="R", name="j", fail_on_reject=False)
    out = v("a", _ctx(judge=lambda s, u: JudgeVerdict(passed=False, parse_ok=True)))
    assert out[0].severity == SEVERITY_WARN


def test_judge_receives_rubric_and_artifact():
    seen = {}

    def jf(system, user):
        seen["user"] = user
        return JudgeVerdict(passed=True)

    make_judge_validator(rubric="MY_RUBRIC", name="j")("MY_ARTIFACT", _ctx(judge=jf))
    assert "MY_RUBRIC" in seen["user"] and "MY_ARTIFACT" in seen["user"]
