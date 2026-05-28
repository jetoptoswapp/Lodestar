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
    for sid in ("prd", "architecture", "stories"):
        spec = reg.stages[sid]
        key = (spec.telemetry_stage, spec.generate_operation)
        assert key in reg.validators, (
            f"stage '{sid}' 自報 ({spec.telemetry_stage}, {spec.generate_operation})，"
            f"但 validator registry 沒這把 key（雙詞彙偏移會讓 validator 靜默不跑）"
        )


def test_dual_vocab_constants(tmp_db):
    """雙詞彙映射（id ↔ telemetry_stage）固定為 spec §11 規範值，防止 typo 漂移。"""
    reg = L.load_all()
    expected = {"prd": "specify", "architecture": "design", "stories": "deliver"}
    for sid, expected_tel in expected.items():
        spec = reg.stages[sid]
        assert spec.telemetry_stage == expected_tel, (
            f"stage '{sid}'.telemetry_stage 預期 '{expected_tel}'，實際 '{spec.telemetry_stage}'"
        )


def test_builtin_agents_seed_loaded(tmp_db):
    """builtin_agents plugin 應該載入並 seed 三個 agent，role 對齊 stage id。"""
    reg = L.load_all()
    by_role = {a.role: a for a in reg.agents.values()}
    assert {"prd", "architecture", "stories"} <= set(by_role)
    for role in ("prd", "architecture", "stories"):
        agent = by_role[role]
        assert agent.enabled is True
        assert agent.system_prompt.strip()
        assert agent.model_choice == "claude-cli"


def test_default_workflow_is_three_stage(tmp_db):
    """default workflow 必須是 (prd, architecture, stories)。"""
    reg = L.load_all()
    wf = reg.workflows["default"]
    assert wf.stages == ("prd", "architecture", "stories")
