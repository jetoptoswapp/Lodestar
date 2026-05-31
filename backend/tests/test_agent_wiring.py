"""Agent 接線（單流程）：agent.system_prompt / model_choice 接回 PRD/架構/故事 generate/chat。

證明使用者在 /agents 編輯的欄位在單流程真的生效，且 persona/契約分離下機器契約不被改壞。
fake adapter 捕捉送進 model 的 prompt（仿 test_collab.py 的捕捉模式）。
"""
from __future__ import annotations

import plugin_loader as L
from plugin_api import ModelAdapter
from plugin_api.workflow import AgentBinding
from persistence import dal
from workflow_engine import WorkflowEngine


def _capture(reg, choices=("claude-cli",)):
    """把指定 model_choice 換成捕捉用 fake adapter；回傳記錄 list（每次 invoke 一筆）。"""
    log: list[dict] = []

    def _mk(choice):
        def _invoke(p):
            log.append({"model": choice, "prompt": p})
            return ("# PRD\n## 3. Functional Requirements\nFR-1 x\n"
                    "## 4. Non-Functional Requirements\nNFR-1 y")
        return ModelAdapter(model_choice=choice, invoke=_invoke, is_available=lambda: True,
                            description="cap", max_context_tokens=100000,
                            prompt_budget_tokens=90000, response_budget_tokens=2000)
    for c in choices:
        reg.model_adapters[c] = _mk(c)
    return log


def test_prd_generate_default_persona_keeps_contract(tmp_db):
    """未設 agent（seed lead system_prompt 空）→ 用 stage 內建 default persona，契約字樣全在。"""
    reg = L.load_all()
    log = _capture(reg)
    dal.create_project("t1", "proj")            # default workflow（單流程）
    out = WorkflowEngine(reg).dispatch(thread_id="t1", stage_id="prd", op="generate")
    assert out["error_code"] == ""
    prompt = log[-1]["prompt"]
    assert "strict and meticulous System Analyst" in prompt   # _DEFAULT_SA_PERSONA（完整原文）
    assert "json-questionnaire" in prompt                     # 機器契約：questionnaire 格式
    assert "## 3. Functional Requirements" in prompt          # 機器契約：PRD Format
    assert "[PRD_READY]" in prompt                            # 機器契約：sentinel
    assert "{{PERSONA}}" not in prompt                        # 佔位已被替換


def test_prd_generate_reflects_user_system_prompt(tmp_db):
    """編輯 seed_prd 的 system_prompt（DB 同 id 覆寫）→ 單流程 generate 反映，契約仍在。"""
    reg = L.load_all()
    log = _capture(reg)
    dal.upsert_agent(agent_id="seed_prd", name="SA", role="prd",
                     system_prompt="CUSTOM_PERSONA_XYZ 你是一位超嚴格的需求分析師。",
                     model_choice="claude-cli", max_iterations=1, enabled=True, tools=[])
    dal.create_project("t2", "proj")
    out = WorkflowEngine(reg).dispatch(thread_id="t2", stage_id="prd", op="generate")
    assert out["error_code"] == ""
    prompt = log[-1]["prompt"]
    assert "CUSTOM_PERSONA_XYZ" in prompt                          # user persona 生效
    assert "strict and meticulous System Analyst" not in prompt    # default 被取代
    assert "[PRD_READY]" in prompt and "json-questionnaire" in prompt  # 契約不被 persona 覆蓋


def test_prd_chat_reflects_user_system_prompt(tmp_db):
    """chat 也接 agent.system_prompt（單流程徹底，不只 generate）。"""
    reg = L.load_all()
    log = _capture(reg)
    dal.upsert_agent(agent_id="seed_prd", name="SA", role="prd",
                     system_prompt="CUSTOM_CHAT_PERSONA_ABC", model_choice="claude-cli",
                     max_iterations=1, enabled=True, tools=[])
    dal.create_project("t3", "proj")
    out = WorkflowEngine(reg).dispatch(thread_id="t3", stage_id="prd", op="chat",
                                       user_input="幫我釐清需求")
    assert out["error_code"] == ""
    assert "CUSTOM_CHAT_PERSONA_ABC" in log[-1]["prompt"]


def test_architecture_generate_reflects_persona_keeps_tier(tmp_db):
    """architecture 單流程也接 persona，且 tier 機器契約仍在。"""
    reg = L.load_all()
    log = _capture(reg)
    dal.upsert_agent(agent_id="seed_architect", name="Arch", role="architecture",
                     system_prompt="CUSTOM_ARCH_PERSONA_123", model_choice="claude-cli",
                     max_iterations=1, enabled=True, tools=[])
    dal.create_project("t4", "proj")
    dal.upsert_artifact("t4", "prd", "# PRD\nFR-1 登入")        # architecture depends_on prd
    out = WorkflowEngine(reg).dispatch(thread_id="t4", stage_id="architecture", op="generate")
    assert out["error_code"] == ""
    prompt = log[-1]["prompt"]
    assert "CUSTOM_ARCH_PERSONA_123" in prompt
    assert "Project tier" in prompt                            # tier 契約（architect.md）保留


def test_model_choice_override(tmp_db):
    """agent.model_choice 指定且 adapter 存在 → 單流程改用該 model。"""
    reg = L.load_all()
    log = _capture(reg, choices=("claude-cli", "agy-cli"))
    dal.upsert_agent(agent_id="seed_prd", name="SA", role="prd", system_prompt="X",
                     model_choice="agy-cli", max_iterations=1, enabled=True, tools=[])
    dal.create_project("t5", "proj")
    out = WorkflowEngine(reg).dispatch(thread_id="t5", stage_id="prd", op="generate")
    assert out["error_code"] == ""
    assert log[-1]["model"] == "agy-cli"


def test_resolve_lead_agent_binding_picks_lead(tmp_db):
    """resolve_lead_agent 的 (a) 分支：binding 有 lead → 用該 agent（不靠 role 遍歷反查）。"""
    from agent_resolver import resolve_lead_agent
    reg = L.load_all()
    dal.upsert_agent(agent_id="my_sa", name="My SA", role="prd",
                     system_prompt="BIND_LEAD_777", model_choice="claude-cli",
                     max_iterations=1, enabled=True, tools=[])
    bindings = (AgentBinding("my_sa", "lead"), AgentBinding("seed_prd_pm", "peer"))
    lead = resolve_lead_agent(reg, "prd", default_agent_role="prd", bindings=bindings)
    assert lead is not None and lead.agent_id == "my_sa"
    assert lead.system_prompt == "BIND_LEAD_777"
