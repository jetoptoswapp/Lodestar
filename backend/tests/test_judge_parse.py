"""judge_parse.parse_judge_verdict 的 fallback 階梯（純函式，免 DB）。"""
from __future__ import annotations

from judge_parse import parse_judge_verdict


def test_clean_json():
    v = parse_judge_verdict('{"passed": false, "score": 0.3, "issues": ["x"], "fix_hint": "y"}')
    assert v.passed is False and v.score == 0.3 and v.issues == ["x"]
    assert v.fix_hint == "y" and v.parse_ok is True


def test_json_with_fence():
    v = parse_judge_verdict('verdict:\n```json\n{"passed": true, "score": 0.9}\n```\n')
    assert v.passed is True and v.score == 0.9 and v.parse_ok is True


def test_json_with_prose():
    v = parse_judge_verdict('Here is the result: {"passed": false} . done.')
    assert v.passed is False and v.parse_ok is True


def test_keyword_fail_parse_not_ok():
    v = parse_judge_verdict("the artifact does not meet the bar, FAIL")
    assert v.passed is False and v.parse_ok is False


def test_garbage_fail_open():
    v = parse_judge_verdict("@@@ not json at all @@@")
    assert v.passed is True and v.parse_ok is False


def test_empty_fail_open():
    v = parse_judge_verdict("")
    assert v.passed is True and v.parse_ok is False


def test_non_dict_json_falls_through():
    # 合法 JSON 但非物件（list）→ 不當 verdict，落 fallback
    v = parse_judge_verdict("[1, 2, 3]")
    assert v.parse_ok is False
