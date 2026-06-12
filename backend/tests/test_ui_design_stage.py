"""ui_design stage：註冊 / 依賴拓樸 / validator / strip helper / dispatch 接線。

證明：
- ui_design stage + seed_ui_designer agent 有註冊；default workflow 四 stage；
  依賴拓樸 ui_design←prd、stories←(architecture, ui_design)。
- structural validator 五條規則（Screen / html fence / fence<Screen / tokens / 禁用字體）。
- strip_html_prototypes 去原型留結構。
- dispatch generate：PRD 流進 prompt、[UI_READY] sentinel strip、state_extra。
- 缺上游擋關（ui_design 缺 prd；stories 缺 ui_design）。
- stories prompt 收到 strip 過的 UI brief（含 Screen 名稱、不含 HTML body）。
- chat amendment 前綴；/agents persona 覆寫生效。
"""
from __future__ import annotations

import pytest

import plugin_loader as L
from plugin_api import ModelAdapter
from persistence import dal
from workflow_engine import MissingDependencyError, WorkflowEngine, compute_dependencies


# ============================================================
#  fixtures / helpers
# ============================================================
_FAKE_PRD = "# 訂便當 App — PRD\n\nFR-1 使用者可以瀏覽今日菜單\nFR-2 使用者可以下單"

_FAKE_UI_DESIGN = """# 訂便當 App — UI Design

## Design Direction
溫暖食堂感：暖紙底色 × 粗獷 display serif。

## Design Tokens
| token | 值 |
|---|---|
| --ink | #2b2118 |

```css
:root { --ink: #2b2118; --accent: #d96c2c; }
```

## Screen: 今日菜單
瀏覽今日菜單（FR-1）。

```html
<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>:root { --ink: #2b2118; --accent: #d96c2c; } body { font-family: 'Fraunces', serif; }</style>
</head><body><h1>今日菜單</h1></body></html>
```

## Screen: 下單
下單流程（FR-2）。

```html
<!DOCTYPE html>
<html><head><style>:root { --ink: #2b2118; }</style></head><body><h1>下單</h1></body></html>
```
"""

_FAKE_ARCH = "**Project tier**: T0 — demo\n\n# 架構\n單體 app。"


def _capture(reg, output, choice="claude-cli"):
    """把 model_choice 換成捕捉 fake adapter；回記錄 list。"""
    log: list[dict] = []

    def _invoke(p, *, allowed_tools=(), workspace_dir=""):
        log.append({"prompt": p, "allowed_tools": allowed_tools, "workspace_dir": workspace_dir})
        return output

    reg.model_adapters[choice] = ModelAdapter(
        model_choice=choice, invoke=_invoke, is_available=lambda: True,
        description="cap", max_context_tokens=100000,
        prompt_budget_tokens=90000, response_budget_tokens=2000)
    return log


# ============================================================
#  註冊與依賴拓樸
# ============================================================
def test_stage_agent_and_workflow_registered(tmp_db):
    reg = L.load_all()
    assert "ui_design" in reg.stages
    s = reg.stages["ui_design"]
    assert s.telemetry_stage == "design"
    assert s.depends_on == ("prd",)
    assert s.supports_chat is True
    assert reg.workflows["default"].stages == ("prd", "architecture", "ui_design", "stories")
    by_role = {a.role: a for a in reg.agents.values()}
    assert by_role["ui_design"].agent_id == "seed_ui_designer"
    assert by_role["ui_design"].system_prompt == ""    # 空 = 用 stage default persona


def test_dependency_topology(tmp_db):
    """ui_design←prd 與 architecture 平行；stories←(architecture, ui_design)。"""
    reg = L.load_all()
    deps = compute_dependencies(reg.workflows["default"], reg.stages)
    assert deps["ui_design"] == ["prd"]
    assert deps["architecture"] == ["prd"]
    assert deps["stories"] == ["architecture", "ui_design"]


# ============================================================
#  structural validator（warn-only）
# ============================================================
def test_validator_complete_document_no_warnings(tmp_db):
    from plugins.builtin_core_stages.ui_design_stage import _ui_design_structural_validator
    assert _ui_design_structural_validator(_FAKE_UI_DESIGN, None) == []


def test_validator_rules_trigger(tmp_db):
    from plugins.builtin_core_stages.ui_design_stage import _ui_design_structural_validator

    def names(artifact):
        outs = _ui_design_structural_validator(artifact, None)
        assert all(o.severity == "warn" and o.fix_hint for o in outs)
        return {o.validator for o in outs}

    # 缺 Screen + 缺 html fence + 缺 tokens
    got = names("# X — UI Design\n\n隨便寫")
    assert {"ui_design.has_screen", "ui_design.has_html_fence",
            "ui_design.has_design_tokens"} <= got
    # Screen 數 > fence 數（一個畫面沒原型）
    got = names("## Screen: A\n```html\n<!DOCTYPE html>x\n```\n## Screen: B\n沒原型\n:root {}")
    assert "ui_design.screen_missing_html" in got
    # 禁用字體
    got = names(_FAKE_UI_DESIGN.replace("'Fraunces', serif", "Inter, sans-serif"))
    assert "ui_design.generic_font" in got


# ============================================================
#  strip_html_prototypes
# ============================================================
def test_strip_html_prototypes_keeps_structure(tmp_db):
    from plugins.builtin_core_stages._shared import strip_html_prototypes
    out = strip_html_prototypes(_FAKE_UI_DESIGN)
    assert "## Screen: 今日菜單" in out and "## Screen: 下單" in out   # 畫面名保留
    assert "Design Direction" in out and "--accent: #d96c2c" in out   # 理念/tokens 保留
    assert "<!DOCTYPE html>" not in out                               # 原型移除
    assert out.count("[HTML prototype omitted") == 2
    assert strip_html_prototypes("") == ""


# ============================================================
#  dispatch：generate / 擋關 / stories 餵料
# ============================================================
def test_generate_strips_sentinel_and_feeds_prd(tmp_db):
    reg = L.load_all()
    log = _capture(reg, _FAKE_UI_DESIGN + "\n[UI_READY]")
    dal.create_project("u1", "proj")
    dal.upsert_artifact("u1", "prd", _FAKE_PRD)
    out = WorkflowEngine(reg).dispatch(thread_id="u1", stage_id="ui_design", op="generate")
    assert out["error_code"] == ""
    saved = dal.get_artifact("u1", "ui_design")
    assert "[UI_READY]" not in saved and "## Screen: 今日菜單" in saved
    prompt = log[-1]["prompt"]
    assert "FR-1 使用者可以瀏覽今日菜單" in prompt        # PRD 流進 prompt
    assert "資深 UI/UX 設計師" in prompt                  # default persona
    assert "json-questionnaire" in prompt                 # 機器契約（system md）


def test_generate_blocked_without_prd(tmp_db):
    reg = L.load_all()
    _capture(reg, _FAKE_UI_DESIGN)
    dal.create_project("u2", "proj")
    with pytest.raises(MissingDependencyError) as ei:
        WorkflowEngine(reg).dispatch(thread_id="u2", stage_id="ui_design", op="generate")
    assert ei.value.missing_upstream == "prd"


def test_stories_blocked_without_ui_design(tmp_db):
    reg = L.load_all()
    _capture(reg, "x")
    dal.create_project("u3", "proj")
    dal.upsert_artifact("u3", "prd", _FAKE_PRD)
    dal.upsert_artifact("u3", "architecture", _FAKE_ARCH)
    with pytest.raises(MissingDependencyError) as ei:
        WorkflowEngine(reg).dispatch(thread_id="u3", stage_id="stories", op="generate")
    assert ei.value.missing_upstream == "ui_design"


def test_stories_prompt_gets_stripped_ui_brief(tmp_db):
    reg = L.load_all()
    log = _capture(reg, "# proj — User Stories\n## Epic 1: x\n### Story 1.1 — y")
    dal.create_project("u4", "proj")
    dal.upsert_artifact("u4", "prd", _FAKE_PRD)
    dal.upsert_artifact("u4", "architecture", _FAKE_ARCH)
    dal.upsert_artifact("u4", "ui_design", _FAKE_UI_DESIGN)
    out = WorkflowEngine(reg).dispatch(thread_id="u4", stage_id="stories", op="generate")
    assert out["error_code"] == ""
    prompt = log[-1]["prompt"]
    assert "## Screen: 今日菜單" in prompt                # 畫面結構餵進 stories
    assert "<!DOCTYPE html>" not in prompt                # 原型 HTML 已 strip
    assert "UI alignment" in prompt                       # user_stories.md 的 HARD RULE


# ============================================================
#  chat amendment + persona 覆寫
# ============================================================
def test_chat_amendment_prefix_when_design_exists(tmp_db):
    reg = L.load_all()
    log = _capture(reg, "好的，我把配色改成暗色系。")
    dal.create_project("u5", "proj")
    dal.upsert_artifact("u5", "prd", _FAKE_PRD)
    dal.upsert_artifact("u5", "ui_design", _FAKE_UI_DESIGN)
    out = WorkflowEngine(reg).dispatch(
        thread_id="u5", stage_id="ui_design", op="chat", user_input="改暗色系")
    assert out["error_code"] == ""
    assert "AMENDMENT MODE" in log[-1]["prompt"]


def test_persona_override_via_agents(tmp_db):
    """DB 覆寫 seed_ui_designer 的 system_prompt → generate 用覆寫 persona，契約仍在。"""
    reg0 = L.load_all()
    dal.upsert_agent(agent_id="seed_ui_designer", name="UI Designer", role="ui_design",
                     system_prompt="CUSTOM_UI_PERSONA_999 你是極簡主義設計大師。",
                     model_choice="claude-cli", enabled=True)
    reg = L.load_all()
    log = _capture(reg, _FAKE_UI_DESIGN)
    dal.create_project("u6", "proj")
    dal.upsert_artifact("u6", "prd", _FAKE_PRD)
    WorkflowEngine(reg).dispatch(thread_id="u6", stage_id="ui_design", op="generate")
    prompt = log[-1]["prompt"]
    assert "CUSTOM_UI_PERSONA_999" in prompt              # 覆寫 persona 生效
    assert "資深 UI/UX 設計師" not in prompt              # default persona 不再注入
    assert "json-questionnaire" in prompt                 # 機器契約不受 persona 影響
