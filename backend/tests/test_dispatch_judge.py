"""WorkflowEngine.dispatch e2e：judge 接通、needs_revision 終局、validations 回 response、回歸。

judge 預設關（_judge_model_choice 回 ""），測試以 _enable_judge 顯式啟用。
沿用 test_collab 的 load_all + 假 adapter + WorkflowEngine(reg) 模式。
"""
from __future__ import annotations

from plugin_api import AgentSpec, ModelAdapter
from workflow_engine import WorkflowEngine

_PRD_OK = ("# PRD\n## 1. Overview\n目的。\n"
           "## 3. Functional Requirements\nFR-1 使用者可登入。\n"
           "## 4. Non-Functional Requirements\nNFR-1 並發 1000。")


def _adapter(choice, fn):
    return ModelAdapter(model_choice=choice, invoke=fn, is_available=lambda: True,
                        description="", max_context_tokens=100000,
                        prompt_budget_tokens=90000, response_budget_tokens=2000)


def _set_prd_agent(reg, max_iter):
    """確保 prd stage 綁的 agent.max_iterations 確定（移除既有 role==prd seed）。"""
    for aid in [a for a, s in reg.agents.items() if s.role == "prd"]:
        del reg.agents[aid]
    reg.agents["test_prd"] = AgentSpec(agent_id="test_prd", name="T", role="prd",
                                       system_prompt="", max_iterations=max_iter)


def _enable_judge(engine, choice="judge"):
    engine._judge_model_choice = lambda: choice


def test_judge_pass_writes_draft(tmp_db):
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    reg.model_adapters["claude-cli"] = _adapter("claude-cli", lambda p: _PRD_OK)
    reg.model_adapters["judge"] = _adapter("judge", lambda p: '{"passed": true, "score": 0.95}')
    _set_prd_agent(reg, 2)
    dal.create_project("t1", "p", workflow_id="default")
    engine = WorkflowEngine(reg)
    _enable_judge(engine)
    out = engine.dispatch(thread_id="t1", stage_id="prd", op="generate")
    assert out["error_code"] == ""
    assert dal.get_stage_status("t1", "prd") == "draft"
    sevs = [v["severity"] for v in out["validations"]]
    assert "fail" not in sevs
    assert any(v["validator"] == "prd.judge" for v in out["validations"])


def test_judge_fail_sets_needs_revision(tmp_db):
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    reg.model_adapters["claude-cli"] = _adapter("claude-cli", lambda p: _PRD_OK)
    reg.model_adapters["judge"] = _adapter(
        "judge", lambda p: '{"passed": false, "issues": ["NFR 不可測"], "fix_hint": "加數字"}')
    _set_prd_agent(reg, 2)   # 重試 2 輪仍 fail
    dal.create_project("t1", "p", workflow_id="default")
    engine = WorkflowEngine(reg)
    _enable_judge(engine)
    out = engine.dispatch(thread_id="t1", stage_id="prd", op="generate")
    assert out["error_code"] == ""
    assert out["artifact"]                                  # 內容仍寫入
    assert dal.get_stage_status("t1", "prd") == "needs_revision"
    assert any(v["severity"] == "fail" and v["validator"] == "prd.judge"
               for v in out["validations"])


def test_dispatch_to_response_carries_validations(tmp_db):
    import plugin_loader as L
    from persistence import dal
    from app import _dispatch_to_response
    reg = L.load_all()
    reg.model_adapters["claude-cli"] = _adapter("claude-cli", lambda p: _PRD_OK)
    reg.model_adapters["judge"] = _adapter(
        "judge", lambda p: '{"passed": false, "issues": ["x"], "fix_hint": "y"}')
    _set_prd_agent(reg, 1)
    dal.create_project("t1", "p", workflow_id="default")
    engine = WorkflowEngine(reg)
    _enable_judge(engine)
    out = engine.dispatch(thread_id="t1", stage_id="prd", op="generate")
    resp = _dispatch_to_response("prd", out)
    assert resp.needs_revision is True
    assert any(v.severity == "fail" for v in resp.validations)


def test_no_judge_unchanged(tmp_db):
    """不開 judge → judge validator 跳過；structural 仍跑；status=draft、無 fail（回歸保護）。"""
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    # 殘缺 PRD（缺 FR/NFR）→ structural 發 warn，但不 fail
    reg.model_adapters["claude-cli"] = _adapter("claude-cli", lambda p: "# PRD\n## 1. Overview\n目的")
    _set_prd_agent(reg, 2)
    dal.create_project("t1", "p", workflow_id="default")
    engine = WorkflowEngine(reg)   # 不 enable judge（預設關）
    out = engine.dispatch(thread_id="t1", stage_id="prd", op="generate")
    assert out["error_code"] == ""
    assert dal.get_stage_status("t1", "prd") == "draft"
    assert all(v["severity"] != "fail" for v in out["validations"])
    assert not any(v["validator"] == "prd.judge" for v in out["validations"])   # judge 跳過
