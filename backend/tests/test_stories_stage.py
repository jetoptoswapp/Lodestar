"""Stories stage helpers：structural validator —— heading shape / AC / Estimate / 反模式偵測。"""
from __future__ import annotations

from plugin_api.harness import HarnessContext
from plugins.builtin_core_stages.stories_stage import _stories_structural_validator


def _ctx() -> HarnessContext:
    return HarnessContext(thread_id="t", stage="deliver", operation="x",
                          model_choice="m", prompt="", metadata={})


# ============ 完整通過 ============
def test_validator_passes_complete_stories():
    stories = """# Sample Project — User Stories

## Milestone 1 — 首屏可視

## Epic 1: 專案骨架

### Story 1.1 — Gradle 骨架

**As a** developer, **I want** ... **so that** ...

**Acceptance Criteria**
- AC-1: Given x, When y, Then z.

**Requirement IDs**: FR-1

**Senior RD Estimate**
- 2

### Story 1.2 — Theme 基底

**Acceptance Criteria**
- ...

**Senior RD Estimate**
- 3
"""
    assert _stories_structural_validator(stories, _ctx()) == []


def test_validator_chinese_title_passes():
    """中文標題（使用者故事）也應通過。"""
    stories = """# 範例專案 — 使用者故事

## Epic 1: 主要功能

### Story 1.1 — 登入

**Acceptance Criteria**
- 驗收條件 1

**Senior RD Estimate**
- 1
"""
    assert _stories_structural_validator(stories, _ctx()) == []


# ============ 缺項警告 ============
def test_validator_warns_on_missing_title():
    stories = """## Epic 1: x

### Story 1.1 — y

**Acceptance Criteria**
- 1

**Senior RD Estimate**
- 1
"""
    outcomes = _stories_structural_validator(stories, _ctx())
    validators = {o.validator for o in outcomes}
    assert "stories.has_title" in validators


def test_validator_warns_on_missing_epic():
    stories = """# X — User Stories

### Story 1.1 — y

**Acceptance Criteria**
- 1

**Senior RD Estimate**
- 1
"""
    outcomes = _stories_structural_validator(stories, _ctx())
    validators = {o.validator for o in outcomes}
    assert "stories.has_epic" in validators


def test_validator_warns_on_missing_story_heading():
    stories = """# X — User Stories

## Epic 1: y

**Acceptance Criteria**
- 1

**Senior RD Estimate**
- 1
"""
    outcomes = _stories_structural_validator(stories, _ctx())
    validators = {o.validator for o in outcomes}
    assert "stories.has_story" in validators


def test_validator_warns_on_missing_ac():
    stories = """# X — User Stories

## Epic 1: y

### Story 1.1 — z

**Senior RD Estimate**
- 2
"""
    outcomes = _stories_structural_validator(stories, _ctx())
    validators = {o.validator for o in outcomes}
    assert "stories.has_acceptance_criteria" in validators


def test_validator_warns_on_missing_estimate():
    stories = """# X — User Stories

## Epic 1: y

### Story 1.1 — z

**Acceptance Criteria**
- 1
"""
    outcomes = _stories_structural_validator(stories, _ctx())
    validators = {o.validator for o in outcomes}
    assert "stories.has_estimate" in validators


# ============ 反模式偵測 ============
def test_validator_detects_bold_story_anti_pattern():
    """`**Story 1.1**` bold 非 heading → parser 看不到，應該被警告。"""
    stories = """# X — User Stories

## Epic 1: y

**Story 1.1** — z

**Acceptance Criteria**
- 1

**Senior RD Estimate**
- 1
"""
    outcomes = _stories_structural_validator(stories, _ctx())
    validators = {o.validator for o in outcomes}
    assert "stories.bad_story_bold" in validators
    # 同時應該 trigger has_story（因為沒有 H3）
    assert "stories.has_story" in validators


def test_validator_detects_h4_story_anti_pattern():
    stories = """# X — User Stories

## Epic 1: y

#### Story 1.1 — z

**Acceptance Criteria**
- 1

**Senior RD Estimate**
- 1
"""
    outcomes = _stories_structural_validator(stories, _ctx())
    validators = {o.validator for o in outcomes}
    assert "stories.bad_story_h4" in validators


def test_validator_detects_h3_epic_anti_pattern():
    stories = """# X — User Stories

### Epic 1: y

### Story 1.1 — z

**Acceptance Criteria**
- 1

**Senior RD Estimate**
- 1
"""
    outcomes = _stories_structural_validator(stories, _ctx())
    validators = {o.validator for o in outcomes}
    assert "stories.bad_epic_h3" in validators
    # 同時應該 trigger has_epic（因為沒有 H2 Epic）
    assert "stories.has_epic" in validators


# ============ warn-only + fix_hint ============
def test_all_outcomes_are_warn_with_fix_hint():
    """spec §11：所有 outcome 都是 warn-only 且 fix_hint 是祈使句、不能為空。"""
    outcomes = _stories_structural_validator("garbage", _ctx())
    assert len(outcomes) >= 4
    for o in outcomes:
        assert o.severity == "warn"
        assert o.fix_hint and o.fix_hint.strip()
