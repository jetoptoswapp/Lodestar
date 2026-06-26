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

## 2. Delivery Surface
- **Human Web UI**: In — main product surface.
- **Programmatic API**: In — integrations.

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

## 2. 交付面
- **Human Web UI**: In — 結帳前端。
- **Programmatic API**: In — 金流串接。

## 3. 功能需求
- `FR-1`: 訪客結帳

## 4. 非功能需求
- `NFR-1`: PCI-DSS L1
"""
    assert _prd_structural_validator(prd, _ctx()) == []


# ============ dispatch chat：當前 user_input 必須進 model prompt（回歸）============
def test_prd_chat_first_message_reaches_model(tmp_db):
    """回歸：第一次 chat 的 user_input 必須出現在送給 model 的 prompt 裡。

    bug：dispatch 原本在 handler 之後才 append user_input，導致 handler 收到空對話，
    SA 誤回「目前還沒收到任何需求」。修法：chat 時先把 user_input 併進 in-memory conv。
    """
    import plugin_loader as L
    from persistence import dal
    from plugin_api import ModelAdapter
    from workflow_engine import WorkflowEngine

    reg = L.load_all()
    captured: list[str] = []
    reg.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli",
        invoke=lambda p: (captured.append(p) or "我來釐清幾個問題。"),
        is_available=lambda: True, description="", max_context_tokens=100000,
        prompt_budget_tokens=90000, response_budget_tokens=2000)
    dal.create_project("t1", "proj")
    out = WorkflowEngine(reg).dispatch(
        thread_id="t1", stage_id="prd", op="chat",
        user_input="一個用本地滑鼠鍵盤控制遠端電腦的工具，wifi 連線")
    assert out["error_code"] == ""
    assert captured, "model 未被呼叫"
    assert "本地滑鼠鍵盤控制遠端電腦" in captured[-1]   # 當前需求確實進了 prompt
