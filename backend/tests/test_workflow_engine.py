"""WorkflowEngine 測試：compute_dependencies / downstream_of + dispatch with mock adapter。"""
from __future__ import annotations

import pytest

from plugin_api import ModelAdapter
from plugin_api.stage import StageSpec
from plugin_api.workflow import WorkflowSpec
from workflow_engine import (
    MissingDependencyError,
    StageNotFoundError,
    WorkflowEngine,
    compute_dependencies,
    downstream_of,
)


# ============ Pure helpers ============
def test_compute_dependencies_basic():
    wf = WorkflowSpec(id="x", label="x", stages=("a", "b", "c"))
    specs = {
        "a": StageSpec(id="a", label="A", depends_on=()),
        "b": StageSpec(id="b", label="B", depends_on=("a",)),
        "c": StageSpec(id="c", label="C", depends_on=("a", "b")),
    }
    assert compute_dependencies(wf, specs) == {"a": [], "b": ["a"], "c": ["a", "b"]}


def test_compute_dependencies_edges_override():
    """edges_override 優先；StageSpec.depends_on 被覆寫。"""
    wf = WorkflowSpec(id="x", label="x", stages=("a", "b"), edges_override={"b": ("a",)})
    specs = {
        "a": StageSpec(id="a", label="A", depends_on=()),
        "b": StageSpec(id="b", label="B", depends_on=("c",)),
    }
    assert compute_dependencies(wf, specs) == {"a": [], "b": ["a"]}


def test_compute_dependencies_filters_non_workflow_stages():
    """spec.depends_on 中不在 workflow.stages 內的依賴被過濾。"""
    wf = WorkflowSpec(id="x", label="x", stages=("b",))
    specs = {"b": StageSpec(id="b", label="B", depends_on=("a", "c"))}
    assert compute_dependencies(wf, specs) == {"b": []}


def test_downstream_of_transitive():
    deps = {"a": [], "b": ["a"], "c": ["b"], "d": ["b"]}
    assert sorted(downstream_of("a", deps)) == ["b", "c", "d"]
    assert sorted(downstream_of("b", deps)) == ["c", "d"]
    assert downstream_of("d", deps) == []


# ============ dispatch（mock adapter）============
def _install_mock_adapter(registry, response: str) -> None:
    """把 claude-cli adapter 換成回固定字串的 mock。"""
    registry.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli",
        invoke=lambda prompt: response,
        is_available=lambda: True,
        description="mock", max_context_tokens=1000,
        prompt_budget_tokens=900, response_budget_tokens=100,
    )


_FAKE_PRD = (
    "# Product Requirements Document\n\n"
    "## 1. Overview\nFake PRD.\n\n"
    "## 3. Functional Requirements\n- `FR-1`: do x\n\n"
    "## 4. Non-Functional Requirements\n- `NFR-1`: secure\n\n"
    "[PRD_READY]"
)


def test_dispatch_generate_writes_artifact_and_status(tmp_db):
    import plugin_loader as L
    from persistence import dal

    reg = L.load_all()
    _install_mock_adapter(reg, _FAKE_PRD)
    dal.create_project("t1", "test")

    engine = WorkflowEngine(reg)
    out = engine.dispatch(thread_id="t1", stage_id="prd", op="generate")

    # sentinel stripped；state_extra 記 prd_ready
    assert "Product Requirements Document" in out["artifact"]
    assert "[PRD_READY]" not in out["artifact"]
    assert out["state_extra"].get("prd_ready") is True
    assert out["error_code"] == ""

    # DB writes
    art = dal.get_artifact("t1", "prd")
    assert art is not None and "Product Requirements Document" in art
    assert dal.get_stage_status("t1", "prd") == "draft"
    revs = dal.list_revisions("t1", "prd")
    assert len(revs) == 1 and revs[0]["source"] == "generate_prd"


def test_dispatch_refine_updates_artifact_and_records_revision(tmp_db):
    import plugin_loader as L
    from persistence import dal

    reg = L.load_all()
    dal.create_project("t1", "test")
    dal.upsert_artifact("t1", "prd", "old prd")

    _install_mock_adapter(reg, _FAKE_PRD.replace("Fake PRD.", "Refined PRD."))
    engine = WorkflowEngine(reg)
    out = engine.dispatch(
        thread_id="t1", stage_id="prd", op="refine",
        instruction="加上一條安全需求",
    )
    assert "Refined PRD" in out["artifact"]
    revs = dal.list_revisions("t1", "prd")
    assert revs[0]["source"] == "refine_prd"
    assert revs[0]["instruction"] == "加上一條安全需求"


def test_dispatch_chat_surfaces_model_error(tmp_db):
    """回歸：harness 把 model 錯誤吞進 result（不 raise）、handler 只取 raw_output → 錯誤不可被默默吃掉，
    否則前端只收到空回覆（使用者看到「沒反應」）。dispatch 要把 runner 記下的錯誤如實上報。"""
    import plugin_loader as L
    from persistence import dal

    reg = L.load_all()

    def _boom(prompt):
        raise RuntimeError("claude-cli exited 1: boom from stdout")

    reg.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli", invoke=_boom, is_available=lambda: True,
        description="mock", max_context_tokens=1000,
        prompt_budget_tokens=900, response_budget_tokens=100,
    )
    dal.create_project("t1", "test")
    engine = WorkflowEngine(reg)
    out = engine.dispatch(thread_id="t1", stage_id="prd", op="chat", user_input="做一個 terminal")

    assert out["error_code"].startswith("model.")          # 不再是空 error_code
    assert "boom from stdout" in (out["error_message"] or "")
    assert not out["reply"]


def test_dispatch_stage_not_found_raises(tmp_db):
    import plugin_loader as L
    from persistence import dal

    reg = L.load_all()
    dal.create_project("t1", "test")
    engine = WorkflowEngine(reg)
    with pytest.raises(StageNotFoundError):
        engine.dispatch(thread_id="t1", stage_id="unknown_stage", op="generate")


def test_dispatch_chat_appends_messages_and_optional_artifact(tmp_db):
    import plugin_loader as L
    from persistence import dal

    reg = L.load_all()
    dal.create_project("t1", "test")
    # 1) chat 回對話（無 sentinel）→ 不更新 artifact
    _install_mock_adapter(reg, "請告訴我預期的尖峰並發等級？")
    engine = WorkflowEngine(reg)
    out = engine.dispatch(thread_id="t1", stage_id="prd", op="chat",
                          user_input="我要重構結帳")
    assert out["reply"] == "請告訴我預期的尖峰並發等級？"
    assert dal.get_artifact("t1", "prd") in (None, "")
    msgs = dal.list_messages("t1", "prd")
    assert [m["role"] for m in msgs] == ["user", "assistant"]

    # 2) chat 回含 sentinel → updated_artifact 寫入
    _install_mock_adapter(reg, _FAKE_PRD)
    out = engine.dispatch(thread_id="t1", stage_id="prd", op="chat",
                          user_input="尖峰 5000")
    assert "Product Requirements Document" in (dal.get_artifact("t1", "prd") or "")


# ============ M2：下游 reset（PRD → architecture → stories）============
_FAKE_ARCH = """**Project tier**: T1 — fake arch.

## Module Layout

```
app/
```

```mermaid
graph TD
  A --> B
```
"""

_FAKE_STORIES = """# X — User Stories

## Epic 1: y

### Story 1.1 — z

**Acceptance Criteria**
- 1

**Senior RD Estimate**
- 1
"""


def test_dispatch_blocks_when_upstream_missing(tmp_db):
    """architecture generate 在 PRD 缺失時應該 raise MissingDependencyError（spec §11）。"""
    import plugin_loader as L
    from persistence import dal

    reg = L.load_all()
    _install_mock_adapter(reg, _FAKE_ARCH)
    dal.create_project("t1", "test")
    engine = WorkflowEngine(reg)
    with pytest.raises(MissingDependencyError) as exc_info:
        engine.dispatch(thread_id="t1", stage_id="architecture", op="generate")
    assert exc_info.value.missing_upstream == "prd"


def test_dispatch_downstream_reset_chain(tmp_db):
    """改 PRD → 已 approved 的 architecture / ui_design / stories 都 reset 為 needs_revision。"""
    import plugin_loader as L
    from persistence import dal

    reg = L.load_all()
    dal.create_project("t1", "test")
    # 先讓 PRD / Arch / UI / Stories 都 approved
    dal.upsert_artifact("t1", "prd", _FAKE_PRD)
    dal.set_stage_status("t1", "prd", "approved")
    dal.upsert_artifact("t1", "architecture", _FAKE_ARCH)
    dal.set_stage_status("t1", "architecture", "approved")
    dal.upsert_artifact("t1", "ui_design", "# Fake — UI Design\n\n## Screen: Home\nfake")
    dal.set_stage_status("t1", "ui_design", "approved")
    dal.upsert_artifact("t1", "stories", _FAKE_STORIES)
    dal.set_stage_status("t1", "stories", "approved")

    # 改 PRD（refine）→ architecture / ui_design / stories 應自動降為 needs_revision
    _install_mock_adapter(reg, _FAKE_PRD.replace("Fake PRD", "Edited PRD"))
    engine = WorkflowEngine(reg)
    out = engine.dispatch(
        thread_id="t1", stage_id="prd", op="refine",
        instruction="加一條 NFR",
    )
    assert set(out["downstream_reset"]) == {"architecture", "ui_design", "stories"}
    assert dal.get_stage_status("t1", "architecture") == "needs_revision"
    assert dal.get_stage_status("t1", "ui_design") == "needs_revision"
    assert dal.get_stage_status("t1", "stories") == "needs_revision"
    # PRD 本身原本 approved → refine 後 status 保留 approved（spec §11：approved 不自動降 draft）
    assert dal.get_stage_status("t1", "prd") == "approved"


def test_dispatch_intermediate_change_resets_only_downstream(tmp_db):
    """改 architecture（不動 PRD）→ 只有 stories reset，PRD 不動。"""
    import plugin_loader as L
    from persistence import dal

    reg = L.load_all()
    dal.create_project("t1", "test")
    dal.upsert_artifact("t1", "prd", _FAKE_PRD)
    dal.set_stage_status("t1", "prd", "approved")
    dal.upsert_artifact("t1", "architecture", _FAKE_ARCH)
    dal.set_stage_status("t1", "architecture", "approved")
    dal.upsert_artifact("t1", "stories", _FAKE_STORIES)
    dal.set_stage_status("t1", "stories", "approved")

    _install_mock_adapter(reg, _FAKE_ARCH.replace("fake arch", "edited arch"))
    engine = WorkflowEngine(reg)
    out = engine.dispatch(
        thread_id="t1", stage_id="architecture", op="refine",
        instruction="加 cache layer",
    )
    assert out["downstream_reset"] == ["stories"]
    assert dal.get_stage_status("t1", "stories") == "needs_revision"
    # 上游 PRD 不動
    assert dal.get_stage_status("t1", "prd") == "approved"
