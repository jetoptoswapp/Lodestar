"""Architecture stage helpers：structural validator + content-block 解析。"""
from __future__ import annotations

from plugin_api.harness import HarnessContext
from plugins.builtin_core_stages._shared import (
    autofix_mermaid,
    extract_content_block,
    lint_mermaid,
)
from plugins.builtin_core_stages.architecture_stage import (
    _architecture_structural_validator,
)


def _ctx() -> HarnessContext:
    return HarnessContext(thread_id="t", stage="design", operation="x",
                          model_choice="m", prompt="", metadata={})


# ============ structural validator（warn-only）============
def test_validator_passes_complete_architecture():
    arch = """**Project tier**: T1 — MVP, app + one core + 3 features.

# System Architecture

## Module Layout

```
app/
  core/
  feature/login
  feature/dashboard
```

## Mermaid

```mermaid
graph TD
  app --> feature/login
  app --> feature/dashboard
  feature --> core
```
"""
    assert _architecture_structural_validator(arch, _ctx()) == []


def test_validator_warns_on_missing_tier_line():
    arch = "# Some architecture\n\n```mermaid\nA --> B\n```\n\n## Module Layout\nx"
    outcomes = _architecture_structural_validator(arch, _ctx())
    validators = {o.validator for o in outcomes}
    assert "architecture.has_tier_line" in validators
    for o in outcomes:
        assert o.severity == "warn"
        assert o.fix_hint


def test_validator_warns_on_missing_mermaid():
    arch = (
        "**Project tier**: T0 — small prototype.\n\n"
        "## Module Layout\n\nsingle module\n"
    )
    outcomes = _architecture_structural_validator(arch, _ctx())
    validators = {o.validator for o in outcomes}
    assert "architecture.has_mermaid" in validators


def test_validator_warns_on_missing_module_layout():
    arch = (
        "**Project tier**: T0 — small prototype.\n\n"
        "```mermaid\nA --> B\n```\n"
    )
    outcomes = _architecture_structural_validator(arch, _ctx())
    validators = {o.validator for o in outcomes}
    assert "architecture.has_module_layout" in validators


def test_validator_chinese_passes():
    """中文 heading（模組架構）也應通過。"""
    arch = """**Project tier**: T1 — MVP，前端 + 後端。

## 模組架構

```
src/
  api/
  ui/
```

```mermaid
graph TD
  ui --> api
```
"""
    assert _architecture_structural_validator(arch, _ctx()) == []


# ============ content-block 解析 ============
def test_extract_content_block_present():
    text = "好的，我來更新。\n[CONTENT_START]\n# New Arch\nfoo\n[CONTENT_END]\n以上更新完成。"
    reply, updated = extract_content_block(text)
    assert updated == "# New Arch\nfoo"
    assert "好的，我來更新" in reply
    assert "以上更新完成" in reply
    assert "[CONTENT_START]" not in reply
    assert "[CONTENT_END]" not in reply


def test_extract_content_block_absent():
    text = "純對話、沒有更新內容。"
    reply, updated = extract_content_block(text)
    assert updated is None
    assert reply == "純對話、沒有更新內容。"


# ============ mermaid focused lint（改完先驗證）============
_SEQ_BAD = (
    "# arch\n```mermaid\nsequenceDiagram\n"
    "W->>OB: retry w/ backoff; mark index=STALE\n"
    "App->>FSM: assert Approved to Published\n```\n"
)


def test_lint_mermaid_catches_sequence_semicolon():
    findings = lint_mermaid(_SEQ_BAD)
    assert len(findings) == 1
    assert findings[0].rule == "sequence.semicolon_in_label"
    assert findings[0].block_index == 1
    assert findings[0].autofix is not None


def test_autofix_mermaid_fixes_and_clears():
    fixed_md, fixed, unfixable = autofix_mermaid(_SEQ_BAD)
    assert len(fixed) == 1 and unfixable == []
    assert ";" not in [ln for ln in fixed_md.splitlines() if "retry" in ln][0]
    assert lint_mermaid(fixed_md) == []          # 修完不再有 findings


def test_lint_mermaid_no_false_positive_on_flowchart():
    # flowchart 的 `;` 是合法語句終止符，不該誤報（規則只限 sequenceDiagram）
    flow = "```mermaid\ngraph TD\nA-->B; B-->C;\n```"
    assert lint_mermaid(flow) == []
