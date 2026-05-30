"""RCA-4 collab 執行（§6.4）：resolve_agent / discussion / dispatch，經 WorkflowEngine.dispatch。

全部 mock adapter；驗證 collab 分支真的跑（peer/subagent 發言 + lead 合成 artifact），
且 single 模式不受影響。
"""
from __future__ import annotations

from plugin_api import ModelAdapter
from workflow_engine import WorkflowEngine
import collab_coordinator as cc


_MOCK_OUT = """## Candidate Root Causes
| Rank | Candidate root cause | Confidence | Evidence | Suggested next check |
|------|----------------------|------------|----------|----------------------|
| 1 | ETCH-03 drift | high | L2231 76% | pull ETCH-03 log |
| 2 | consumable | medium | single tool | check PM |
| 3 | material | low | recipe同 | check lot |

> 候選假設 · 待工程師確認，非結論。
"""


def _mock(reg, response=_MOCK_OUT):
    reg.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli", invoke=lambda p: response, is_available=lambda: True,
        description="mock", max_context_tokens=1000, prompt_budget_tokens=900, response_budget_tokens=100)


def test_collab_catalog(tmp_db):
    import plugin_loader as L
    reg = L.load_all()
    assert "rca_panel" in reg.workflows and "rca_dispatch" in reg.workflows
    assert reg.workflows["rca_panel"].collab_mode.get("rca_analysis") == "discussion"
    assert reg.workflows["rca_dispatch"].collab_mode.get("rca_analysis") == "dispatch"
    # panel：lead + 2 peer
    binds = reg.workflows["rca_panel"].agent_bindings["rca_analysis"]
    assert sorted(b.role for b in binds) == ["lead", "peer", "peer"]


def test_resolve_agent(tmp_db):
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    assert cc.resolve_agent(reg, "rca_assistant").role == "rca_analysis"   # registry seed
    assert cc.resolve_agent(reg, "no_such_agent") is None
    # GAP A fallback：DB agent
    dal.upsert_agent(agent_id="db_agent", name="DB Agent", role="rca_analysis",
                     system_prompt="x", model_choice="claude-cli", max_iterations=1,
                     enabled=True, tools=[])
    got = cc.resolve_agent(reg, "db_agent")
    assert got is not None and got.name == "DB Agent"


def test_discussion_mode(tmp_db):
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    _mock(reg)
    dal.create_project("t1", "panel", workflow_id="rca_panel")
    dal.upsert_artifact("t1", "rca_intake", "Line-3 良率步階下降")
    engine = WorkflowEngine(reg)
    out = engine.dispatch(thread_id="t1", stage_id="rca_analysis", op="generate")

    assert out["error_code"] == ""
    assert "Candidate Root Causes" in out["artifact"]          # lead 合成結果
    msgs = dal.list_messages("t1", "rca_analysis")
    peer_msgs = [m for m in msgs if "peer" in m["content"]]
    assert len(peer_msgs) == 2                                  # 2 個 peer 各發言一次
    assert dal.get_stage_status("t1", "rca_analysis") == "draft"


def test_dispatch_mode(tmp_db):
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    _mock(reg)
    dal.create_project("t1", "dispatch", workflow_id="rca_dispatch")
    dal.upsert_artifact("t1", "rca_intake", "Line-3 良率步階下降")
    engine = WorkflowEngine(reg)
    out = engine.dispatch(thread_id="t1", stage_id="rca_analysis", op="generate")

    assert out["error_code"] == ""
    assert out["artifact"]                                      # lead 合併結果
    msgs = dal.list_messages("t1", "rca_analysis")
    sub_msgs = [m for m in msgs if "subagent" in m["content"]]
    assert len(sub_msgs) == 2                                   # 2 個 subagent 平行各產出


def test_discussion_prd_is_stage_aware(tmp_db):
    """§6.4 stage-aware：PRD 以 discussion 跑 → peer 發言 + lead 走「PRD 自己的 generate」彙整。

    驗證：(1) requirements_panel 綁定正確；(2) 2 個 peer 各發言；(3) lead 合成的 generate
    prompt 吸收了多方討論（peer 發言被當 conversation 注入）；(4) 產出非 RCA 根因表。
    """
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    assert reg.workflows["requirements_panel"].collab_mode.get("prd") == "discussion"
    binds = reg.workflows["requirements_panel"].agent_bindings["prd"]
    assert sorted(b.role for b in binds) == ["lead", "peer", "peer"]

    captured: list[str] = []

    def _invoke(p):
        captured.append(p)
        return "# PRD\n## 1. 功能需求\nFR-1 使用者可登入。\n## 2. 非功能需求\nNFR-1 並發 1000。"

    reg.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli", invoke=_invoke, is_available=lambda: True,
        description="mock", max_context_tokens=100000, prompt_budget_tokens=90000,
        response_budget_tokens=2000)

    dal.create_project("tp", "panel", workflow_id="requirements_panel")
    engine = WorkflowEngine(reg)
    out = engine.dispatch(thread_id="tp", stage_id="prd", op="generate")

    assert out["error_code"] == ""
    assert "PRD" in out["artifact"] and "Candidate Root Causes" not in out["artifact"]
    peer_msgs = [m for m in dal.list_messages("tp", "prd") if "peer" in m["content"]]
    assert len(peer_msgs) == 2                       # PM + Security peers 各發言
    # 最後一次 invoke = lead 的 PRD generate；應已含被注入為 conversation 的多方發言
    assert "specialist" in captured[-1].lower()


def test_single_mode_unaffected(tmp_db):
    """rca_single 的 rca_analysis 仍走 stage handler（非 collab）→ 不產 peer/subagent 訊息。"""
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    _mock(reg)
    dal.create_project("t1", "single", workflow_id="rca_single")
    dal.upsert_artifact("t1", "rca_intake", "anomaly")
    engine = WorkflowEngine(reg)
    out = engine.dispatch(thread_id="t1", stage_id="rca_analysis", op="generate")
    assert out["error_code"] == ""
    msgs = dal.list_messages("t1", "rca_analysis")
    assert msgs == []                                          # 單代理 generate 不寫對話訊息
