"""rca_plan（RCA-3 Agentic planner）：catalog / parse / validator / mock generate / apply-plan 端到端。

apply-plan 直接 pre-seed plan artifact（不呼叫模型）；generate 路徑用 mock adapter。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from plugin_api import ModelAdapter
from plugin_api.harness import HarnessContext
from workflow_engine import WorkflowEngine

import app as appmod
from plugins.rca_domain.planner_stage import _plan_shape_validator, parse_plan


_VALID_PLAN = """這是我的規劃理由。

[PLAN_START]
{"label":"RCA plan: 良率下降","description":"用全鏈","rationale":"步階單機事件",
 "stages":[
   {"stage_id":"rca_intake","depends_on":[],"agent_bindings":[{"agent_id":"rca_intake_helper","role":"lead"}],"collab_mode":"single"},
   {"stage_id":"rca_baseline","depends_on":["rca_intake"],"agent_bindings":[{"agent_id":"rca_baseline_analyst","role":"lead"}],"collab_mode":"single"},
   {"stage_id":"rca_causal","depends_on":["rca_baseline"],"agent_bindings":[{"agent_id":"rca_causal_reasoner","role":"lead"}],"collab_mode":"single"},
   {"stage_id":"rca_knowledge","depends_on":["rca_baseline"],"agent_bindings":[{"agent_id":"rca_knowledge_agent","role":"lead"}],"collab_mode":"single"},
   {"stage_id":"rca_synthesis","depends_on":["rca_causal","rca_knowledge"],"agent_bindings":[{"agent_id":"rca_synthesizer","role":"lead"}],"collab_mode":"single"}
 ]}
[PLAN_END]
"""


def _mock(reg, response):
    reg.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli", invoke=lambda p: response, is_available=lambda: True,
        description="mock", max_context_tokens=1000, prompt_budget_tokens=900, response_budget_tokens=100)


def test_planner_catalog(tmp_db):
    import plugin_loader as L
    reg = L.load_all()
    assert "rca_plan" in reg.stages
    assert reg.stages["rca_plan"].depends_on == ("rca_intake",)
    assert "rca_planner" in reg.workflows
    assert reg.workflows["rca_planner"].stages == ("rca_intake", "rca_plan")
    assert "rca_planner" in reg.agents and reg.agents["rca_planner"].role == "rca_plan"


def test_parse_plan_and_validator():
    plan = parse_plan(_VALID_PLAN)
    assert plan is not None and len(plan["stages"]) == 5
    ctx = HarnessContext(thread_id="t", stage="rca_plan", operation="generate_rca_plan",
                         model_choice="mock", prompt="")
    assert _plan_shape_validator(_VALID_PLAN, ctx) == []
    # 壞：無法 parse
    assert parse_plan("no json here") is None
    assert _plan_shape_validator("no json", ctx)[0].validator == "rca.plan_parseable"
    # 壞：parse 出來但無已知 stage
    bad = '[PLAN_START]{"stages":[{"stage_id":"foo"}]}[PLAN_END]'
    assert _plan_shape_validator(bad, ctx)[0].validator == "rca.plan_known_stages"


def test_planner_generate_mock(tmp_db):
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    _mock(reg, _VALID_PLAN)
    dal.create_project("t1", "plan", workflow_id="rca_planner")
    dal.upsert_artifact("t1", "rca_intake", "Line-3 良率步階下降")
    engine = WorkflowEngine(reg)
    out = engine.dispatch(thread_id="t1", stage_id="rca_plan", op="generate")
    assert out["error_code"] == ""
    assert "[PLAN_START]" in out["artifact"]
    assert parse_plan(dal.get_artifact("t1", "rca_plan")) is not None


def test_apply_plan_flow(tmp_db):
    from persistence import dal
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "plan demo"}).json()["thread_id"]
        dal.upsert_artifact(tid, "rca_intake", "anomaly")
        dal.upsert_artifact(tid, "rca_plan", _VALID_PLAN)

        # 未核准 → 409
        r = c.post(f"/api/projects/{tid}/rca/apply-plan")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["category"] == "plan_not_approved"

        # 核准後 → 建 workflow + 綁定
        c.post(f"/api/stage/rca_plan/{tid}/approve")
        r = c.post(f"/api/projects/{tid}/rca/apply-plan")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == f"rca_plan_{tid}"
        assert [s["stage_id"] for s in body["stages"]] == [
            "rca_intake", "rca_baseline", "rca_causal", "rca_knowledge", "rca_synthesis"]

        # workflow 已存在 catalog、thread 已綁
        wf_ids = {w["id"] for w in c.get("/api/workflows").json()["workflows"]}
        assert f"rca_plan_{tid}" in wf_ids
        proj = c.get(f"/api/projects").json()["projects"]
        bound = next(p for p in proj if p["thread_id"] == tid)
        assert bound["workflow_id"] == f"rca_plan_{tid}"


def test_apply_plan_rejects_unknown_stage(tmp_db):
    from persistence import dal
    bad_plan = ('[PLAN_START]{"label":"x","stages":['
                '{"stage_id":"rca_intake","depends_on":[],"agent_bindings":[{"agent_id":"rca_intake_helper"}]},'
                '{"stage_id":"nonexistent_stage","depends_on":["rca_intake"],"agent_bindings":[]}'
                ']}[PLAN_END]')
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "bad plan"}).json()["thread_id"]
        dal.upsert_artifact(tid, "rca_plan", bad_plan)
        c.post(f"/api/stage/rca_plan/{tid}/approve")
        r = c.post(f"/api/projects/{tid}/rca/apply-plan")
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["category"] == "workflow_unknown_stage"
