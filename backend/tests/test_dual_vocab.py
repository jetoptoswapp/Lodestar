"""雙詞彙對齊（spec §11）：每個內建 stage 在 validator registry 必須有對應註冊。

「stage 沒帶對 telemetry_stage / operation → validator 靜默不跑」是 ver2 的血淚經驗。
本測試斷言所有 builtin stage 都有 generate / refine 對應 validator chain（chat 可選）。
"""
from __future__ import annotations

import plugin_loader as L


def test_each_builtin_stage_has_generate_refine_validator(tmp_db):
    reg = L.load_all()

    expected_pairs = {
        # (telemetry_stage, operation) → 至少存在於 validators registry
        ("specify", "generate_prd"),
        ("specify", "refine_prd"),
        ("design", "generate_architecture"),
        ("design", "refine_architecture"),
        ("design", "generate_ui_design"),
        ("design", "refine_ui_design"),
        ("deliver", "generate_user_stories"),
        ("deliver", "refine_user_stories"),
    }

    missing = []
    for pair in expected_pairs:
        chain = reg.validators.get(pair, [])
        if not chain:
            missing.append(pair)
    assert not missing, (
        f"以下 (telemetry_stage, operation) 缺 validator registry 註冊（雙詞彙未對齊）：{missing}"
    )


def test_stage_spec_telemetry_stage_alignment(tmp_db):
    """builtin stage 的 telemetry_stage 必須對齊 generate_operation 那筆 validator key。"""
    reg = L.load_all()
    for sid in ("prd", "architecture", "ui_design", "stories"):
        spec = reg.stages[sid]
        key = (spec.telemetry_stage, spec.generate_operation)
        assert key in reg.validators, (
            f"stage '{sid}' 自報 ({spec.telemetry_stage}, {spec.generate_operation})，"
            f"但 validator registry 沒這把 key（雙詞彙偏移會讓 validator 靜默不跑）"
        )


def test_dual_vocab_constants(tmp_db):
    """雙詞彙映射（id ↔ telemetry_stage）固定為 spec §11 規範值，防止 typo 漂移。"""
    reg = L.load_all()
    expected = {"prd": "specify", "architecture": "design", "ui_design": "design", "stories": "deliver"}
    for sid, expected_tel in expected.items():
        spec = reg.stages[sid]
        assert spec.telemetry_stage == expected_tel, (
            f"stage '{sid}'.telemetry_stage 預期 '{expected_tel}'，實際 '{spec.telemetry_stage}'"
        )


def test_builtin_agents_seed_loaded(tmp_db):
    """builtin_agents plugin 應該載入並 seed 四個 lead agent，role 對齊 stage id。"""
    reg = L.load_all()
    by_role = {a.role: a for a in reg.agents.values()}
    assert {"prd", "architecture", "ui_design", "stories"} <= set(by_role)
    for role in ("prd", "architecture", "ui_design", "stories"):
        agent = by_role[role]
        assert agent.enabled is True
        # lead 的 system_prompt 留空 → 單流程用 stage 內建 default persona（見 *_stage.py）；
        # 使用者在 /agents 填入後才覆寫。
        assert agent.system_prompt == ""
        assert agent.model_choice == "claude-cli"


def test_default_workflow_is_four_stage(tmp_db):
    """default workflow 必須是 (prd, ui_design, architecture, stories)。"""
    reg = L.load_all()
    wf = reg.workflows["default"]
    assert wf.stages == ("prd", "ui_design", "architecture", "stories")


def test_default_agent_role_resolves_unique_lead(tmp_db):
    """core stage 的 default_agent_role 必須解析到唯一 lead seed。

    同時鎖死三件事：(1) default_agent_role 命名對齊 seed.role（防孤立值如舊的
    "architect"/"pm"）；(2) 解析到唯一 lead；(3) PRD 的 peer（prd_peer）不再污染
    lead 解析——若 peer 仍 role=="prd"，resolve_lead_agent 會因多命中回 None 而 fail。
    """
    from agent_resolver import resolve_lead_agent
    reg = L.load_all()
    expected_lead = {"prd": "seed_prd", "architecture": "seed_architect",
                     "ui_design": "seed_ui_designer", "stories": "seed_pm"}
    for sid, agent_id in expected_lead.items():
        spec = reg.stages[sid]
        lead = resolve_lead_agent(reg, sid, default_agent_role=spec.default_agent_role)
        assert lead is not None and lead.agent_id == agent_id, (
            f"stage '{sid}'（default_agent_role='{spec.default_agent_role}'）"
            f"應解析到唯一 lead '{agent_id}'，實際 {lead and lead.agent_id}"
        )
