"""Skills：實體 CRUD + agent 綁定（sort_order）+ delete cascade + 解析 embed + 注入 + API。"""
from __future__ import annotations

import plugin_loader as L
from persistence import dal


# ============================================================
#  (A) skill 實體 CRUD（DAL 直測）
# ============================================================
def test_skill_crud(tmp_db):
    dal.upsert_skill(skill_id="s1", name="N", description="d", body="B1", version="2.0")
    assert dal.get_skill("s1")["body"] == "B1"
    dal.upsert_skill(skill_id="s1", name="N2", description="d", body="B2")   # update
    assert dal.get_skill("s1")["name"] == "N2" and dal.get_skill("s1")["body"] == "B2"
    assert any(s["skill_id"] == "s1" for s in dal.list_skills())
    assert dal.delete_skill("s1") is True and dal.get_skill("s1") is None


# ============================================================
#  (B) 綁定讀寫 + sort_order
# ============================================================
def test_agent_skill_binding_order(tmp_db):
    for sid in ("a", "b", "c"):
        dal.upsert_skill(skill_id=sid, name=sid, body=f"body_{sid}")
    dal.set_agent_skills("ag1", ["c", "a", "b"])
    assert dal.get_agent_skill_ids("ag1") == [("c", 0), ("a", 1), ("b", 2)]
    dal.set_agent_skills("ag1", ["b"])              # 覆寫
    assert dal.get_agent_skill_ids("ag1") == [("b", 0)]
    dal.set_agent_skills("ag1", [])                 # 清空
    assert dal.get_agent_skill_ids("ag1") == []


# ============================================================
#  (C) delete cascade
# ============================================================
def test_delete_skill_clears_bindings(tmp_db):
    dal.upsert_skill(skill_id="s1", name="s1", body="x")
    dal.set_agent_skills("ag1", ["s1"])
    dal.delete_skill("s1")
    assert dal.get_agent_skill_ids("ag1") == []


def test_delete_agent_clears_bindings(tmp_db):
    dal.upsert_skill(skill_id="s1", name="s1", body="x")
    dal.upsert_agent(agent_id="ag1", name="A", role="prd", system_prompt="p",
                     model_choice="claude-cli", max_iterations=1, enabled=True, tools=[])
    dal.set_agent_skills("ag1", ["s1"])
    dal.delete_agent("ag1")
    assert dal.get_agent_skill_ids("ag1") == []


# ============================================================
#  (D) 解析帶 skills（DB 綁定優先，依 sort_order）
# ============================================================
def test_resolve_agent_embeds_skills_in_order(tmp_db):
    from agent_resolver import resolve_agent
    reg = L.load_all()
    for sid in ("s1", "s2"):
        dal.upsert_skill(skill_id=sid, name=sid.upper(), body=f"BODY_{sid}")
    dal.set_agent_skills("seed_prd", ["s2", "s1"])   # 對 seed agent 綁（DB agent 不存在 → seed 分支）
    spec = resolve_agent(reg, "seed_prd")
    assert [s.skill_id for s in spec.skills] == ["s2", "s1"]   # 依 sort_order
    assert spec.skills[0].body == "BODY_s2"


def test_resolve_agent_no_binding_returns_seed_skills(tmp_db):
    from agent_resolver import resolve_agent
    reg = L.load_all()
    assert resolve_agent(reg, "seed_prd").skills == ()   # seed AgentSpec.skills=()，無綁定 → ()


# ============================================================
#  (E) 注入：綁 skill → prompt 含 body；未綁 → 不含
# ============================================================
from plugin_api import ModelAdapter, SkillSpec          # noqa: E402
from workflow_engine import WorkflowEngine               # noqa: E402


def _capture(reg):
    log: list[str] = []

    def _inv(p, **kw):
        log.append(p)
        return ("# PRD\n## 3. Functional Requirements\nFR-1 x\n"
                "## 4. Non-Functional Requirements\nNFR-1 y")
    reg.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli", invoke=_inv, is_available=lambda: True,
        description="c", max_context_tokens=100000,
        prompt_budget_tokens=90000, response_budget_tokens=2000)
    return log


def test_bound_skill_injected_into_prd_prompt(tmp_db):
    reg = L.load_all(); log = _capture(reg)
    dal.upsert_skill(skill_id="s1", name="Concise", body="SKILL_BODY_MARKER_123")
    dal.set_agent_skills("seed_prd", ["s1"])
    dal.create_project("t1", "proj")
    WorkflowEngine(reg).dispatch(thread_id="t1", stage_id="prd", op="generate")
    p = log[-1]
    assert "SKILL_BODY_MARKER_123" in p                       # skill body 進 prompt
    assert "json-questionnaire" in p and "[PRD_READY]" in p   # 機器契約仍在（SKILLS 在契約前）
    assert "{{SKILLS}}" not in p and "{{PERSONA}}" not in p    # 佔位都替換掉


def test_unbound_skill_not_in_prompt(tmp_db):
    reg = L.load_all(); log = _capture(reg)
    dal.upsert_skill(skill_id="s1", name="Concise", body="SHOULD_NOT_APPEAR")
    dal.create_project("t2", "proj")                          # 未綁
    WorkflowEngine(reg).dispatch(thread_id="t2", stage_id="prd", op="generate")
    assert "SHOULD_NOT_APPEAR" not in log[-1]


# ============================================================
#  (F) R1 守門：未綁 skills 時逐字不變
# ============================================================
def test_render_skills_block_empty_is_noop():
    from plugins.builtin_core_stages._shared import render_skills_block
    assert render_skills_block(()) == ""
    assert render_skills_block((SkillSpec("s", "n", "d", "", "1.0"),)) == ""   # 空 body → ""


def test_R1_md_render_byte_identical_when_no_skills(tmp_db):
    """未綁 skills（SKILLS=""）時 sa_system.md render：PERSONA 與 ## Rules 間恰好一個空行（佔位塌掉）。"""
    reg = L.load_all()
    from harness_runner import HarnessRunner
    r = HarnessRunner(reg, "t", "prd", "claude-cli")
    out = r.render_prompt("sa_system.md", {"PERSONA": "PERSONA_X", "SKILLS": ""})
    assert "PERSONA_X\n\n## Rules" in out                     # 逐字：與接線前相同
    assert "{{SKILLS}}" not in out and "{{PERSONA}}" not in out


# ============================================================
#  (G) API smoke（TestClient）
# ============================================================
def test_api_skill_crud_and_bind(tmp_db):
    import app as appmod
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        assert c.post("/api/skills", json={"skill_id": "s1", "name": "N", "body": "B"}).status_code == 201
        assert c.post("/api/skills", json={"skill_id": "s1", "name": "N", "body": "B"}).status_code == 409  # 重複
        skills = c.get("/api/skills").json()["skills"]
        assert any(s["skill_id"] == "s1" for s in skills)                      # user skill
        assert any(s["skill_id"] == "seed_skill_concise" for s in skills)      # seed skill 也列出
        r = c.put("/api/agents/seed_prd/skills", json={"skill_ids": ["s1"]})   # 綁到 seed agent
        assert r.status_code == 200
        assert [s["skill_id"] for s in r.json()["skills"]] == ["s1"]           # embed + 順序
        assert c.put("/api/agents/seed_prd/skills", json={"skill_ids": ["nope"]}).status_code == 404
        assert c.delete("/api/skills/s1").status_code == 200
