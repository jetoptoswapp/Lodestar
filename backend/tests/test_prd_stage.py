"""PRD stage helpers：validator / sentinel / conversation formatter。"""
from __future__ import annotations

from plugin_api.harness import HarnessContext
from plugins.builtin_core_stages.prd_stage import (
    _format_conversation,
    _prd_structural_validator,
    _strip_sentinel,
)


def _ctx() -> HarnessContext:
    return HarnessContext(thread_id="t", stage="specify", operation="x",
                          model_choice="m", prompt="", metadata={})


# ============ sentinel ============
def test_strip_sentinel_present():
    text, ready = _strip_sentinel("hello world [PRD_READY]")
    assert ready is True
    assert text == "hello world"


def test_strip_sentinel_absent():
    text, ready = _strip_sentinel("hello world")
    assert ready is False
    assert text == "hello world"


# ============ conversation formatter ============
def test_format_conversation_empty():
    assert "no prior conversation" in _format_conversation(())


def test_format_conversation_messages():
    out = _format_conversation((("user", "hi"), ("assistant", "hello")))
    assert "User:\nhi" in out and "SA:\nhello" in out


# ============ structural validator（warn-only）============
def test_validator_passes_complete_prd():
    prd = """# Product Requirements Document

## 1. Overview
Foo.

## 3. Functional Requirements
- `FR-1`: x

## 4. Non-Functional Requirements
- `NFR-1`: y
"""
    assert _prd_structural_validator(prd, _ctx()) == []


def test_validator_warns_on_missing_sections():
    """非完整 PRD → 全部 outcomes 應該是 warn（不可升級 fail）+ 都帶 fix_hint。"""
    outcomes = _prd_structural_validator("just a chat reply, no PRD content", _ctx())
    assert len(outcomes) >= 4
    for o in outcomes:
        assert o.severity == "warn", f"{o.validator} 不應升級 fail（spec §11 warn-only）"
        assert o.fix_hint, f"{o.validator} 缺 fix_hint（spec §11 要求祈使句、動詞開頭）"
    validators = {o.validator for o in outcomes}
    assert "prd.has_overview" in validators
    assert "prd.has_fr" in validators or "prd.has_fr_section" in validators
    assert "prd.has_nfr" in validators or "prd.has_nfr_section" in validators


def test_validator_chinese_prd_passes():
    """中文 PRD 也應該通過（label 同時匹配「概述」「功能需求」「非功能需求」）。"""
    prd = """# 產品需求文件

## 1. 概述
電商結帳重構。

## 3. 功能需求
- `FR-1`: 訪客結帳

## 4. 非功能需求
- `NFR-1`: PCI-DSS L1
"""
    assert _prd_structural_validator(prd, _ctx()) == []
