"""M3：Workflow / Agent CRUD + per-thread workflow + multi-binding 解析。"""
from __future__ import annotations

from fastapi.testclient import TestClient

import app as appmod
from persistence import dal
from plugin_api import AgentBinding, normalize_bindings
from workflow_engine import WorkflowEngine


# ============================================================
#  spec §6.4 normalize_bindings：1:N + collab role
# ============================================================
def test_normalize_bindings_accepts_str():
    out = normalize_bindings("system_analyst")
    assert out == (AgentBinding(agent_id="system_analyst", role="lead"),)


def test_normalize_bindings_accepts_list():
    out = normalize_bindings(["sa", "pm"])
    assert [b.agent_id for b in out] == ["sa", "pm"]
    assert all(b.role == "lead" for b in out)


def test_normalize_bindings_accepts_dict():
    out = normalize_bindings([
        {"agent_id": "sa", "role": "lead"},
        {"agent_id": "pm", "role": "peer"},
    ])
    assert out[0].role == "lead" and out[1].role == "peer"


def test_normalize_bindings_drops_invalid_role():
    out = normalize_bindings([{"agent_id": "x", "role": "junk"}])
    assert out[0].role == "lead"   # fallback to lead


def test_normalize_bindings_drops_missing_agent_id():
    out = normalize_bindings([{"role": "lead"}, {"agent_id": "ok"}])
    assert len(out) == 1 and out[0].agent_id == "ok"


def test_normalize_bindings_empty():
    assert normalize_bindings(None) == ()
    assert normalize_bindings([]) == ()


# ============================================================
#  Workflow CRUD
# ============================================================
_SAMPLE_WORKFLOW = {
    "id": "my-flow",
    "label": "My Custom Flow",
    "description": "PRD only，無下游",
    "stages": [
        {
            "stage_id": "prd",
            "depends_on": [],
            "agent_bindings": [{"agent_id": "system_analyst", "role": "lead"}],
            "collab_mode": "single",
        },
    ],
}


def test_create_workflow_then_list(tmp_db):
    with TestClient(appmod.app) as c:
        r = c.post("/api/workflows", json=_SAMPLE_WORKFLOW)
        assert r.status_code == 201, r.json()
        body = r.json()
        assert body["id"] == "my-flow"
        assert body["source"] == "user"
        assert len(body["stages"]) == 1

        listing = c.get("/api/workflows").json()
        ids = [w["id"] for w in listing["workflows"]]
        assert "my-flow" in ids
        assert "default" in ids   # builtin 仍在


def test_create_workflow_rejects_builtin_id(tmp_db):
    with TestClient(appmod.app) as c:
        bad = {**_SAMPLE_WORKFLOW, "id": "default"}
        r = c.post("/api/workflows", json=bad)
        assert r.status_code == 409
        assert r.json()["detail"]["category"] == "workflow_is_builtin"


def test_create_workflow_rejects_unknown_stage(tmp_db):
    with TestClient(appmod.app) as c:
        bad = {
            **_SAMPLE_WORKFLOW,
            "stages": [{
                "stage_id": "nonexistent_stage",
                "depends_on": [],
                "agent_bindings": [],
                "collab_mode": "single",
            }],
        }
        r = c.post("/api/workflows", json=bad)
        assert r.status_code == 400
        assert r.json()["detail"]["category"] == "workflow_unknown_stage"


def test_create_workflow_rejects_forward_dependency(tmp_db):
    """spec §2：depends_on 必須是「排在前面」的 stage（防環的天然門檻）。"""
    with TestClient(appmod.app) as c:
        bad = {
            "id": "wrong-order",
            "label": "x",
            "description": "",
            "stages": [
                {"stage_id": "prd", "depends_on": ["architecture"],
                 "agent_bindings": [], "collab_mode": "single"},
                {"stage_id": "architecture", "depends_on": [],
                 "agent_bindings": [], "collab_mode": "single"},
            ],
        }
        r = c.post("/api/workflows", json=bad)
        assert r.status_code == 400
        assert r.json()["detail"]["category"] == "workflow_invalid_dependency"


def test_create_workflow_rejects_duplicate_stage(tmp_db):
    with TestClient(appmod.app) as c:
        bad = {
            "id": "dup",
            "label": "dup",
            "description": "",
            "stages": [
                {"stage_id": "prd", "depends_on": [],
                 "agent_bindings": [], "collab_mode": "single"},
                {"stage_id": "prd", "depends_on": [],
                 "agent_bindings": [], "collab_mode": "single"},
            ],
        }
        r = c.post("/api/workflows", json=bad)
        assert r.status_code == 400
        assert r.json()["detail"]["category"] == "workflow_duplicate_stage"


def test_update_workflow_replaces_stages(tmp_db):
    with TestClient(appmod.app) as c:
        c.post("/api/workflows", json=_SAMPLE_WORKFLOW)
        updated = {
            **_SAMPLE_WORKFLOW,
            "label": "Renamed",
            "stages": [
                {"stage_id": "prd", "depends_on": [],
                 "agent_bindings": [{"agent_id": "sa", "role": "lead"},
                                    {"agent_id": "pm", "role": "peer"}],
                 "collab_mode": "discussion"},
                {"stage_id": "architecture", "depends_on": ["prd"],
                 "agent_bindings": [{"agent_id": "arch", "role": "lead"}],
                 "collab_mode": "single"},
            ],
        }
        r = c.put("/api/workflows/my-flow", json=updated)
        assert r.status_code == 200
        body = r.json()
        assert body["label"] == "Renamed"
        assert len(body["stages"]) == 2
        # multi-binding 保留
        assert len(body["stages"][0]["agent_bindings"]) == 2
        assert body["stages"][0]["collab_mode"] == "discussion"


def test_delete_workflow(tmp_db):
    with TestClient(appmod.app) as c:
        c.post("/api/workflows", json=_SAMPLE_WORKFLOW)
        r = c.delete("/api/workflows/my-flow")
        assert r.status_code == 200
        assert r.json()["deleted"] == "my-flow"
        # 已不在 list
        listing = c.get("/api/workflows").json()
        assert all(w["id"] != "my-flow" for w in listing["workflows"])


def test_delete_builtin_workflow_rejected(tmp_db):
    with TestClient(appmod.app) as c:
        r = c.delete("/api/workflows/default")
        assert r.status_code == 409
        assert r.json()["detail"]["category"] == "workflow_is_builtin"


# ============================================================
#  Agent CRUD
# ============================================================
_SAMPLE_AGENT = {
    "agent_id": "custom_sa",
    "name": "Custom SA",
    "role": "prd",
    "system_prompt": "你是一個資深 SA，專注電商領域。",
    "model_choice": "claude-cli",
    "max_iterations": 1,
    "enabled": True,
    "tools": [],
}


def test_create_then_list_agent(tmp_db):
    with TestClient(appmod.app) as c:
        r = c.post("/api/agents", json=_SAMPLE_AGENT)
        assert r.status_code == 201, r.json()
        body = r.json()
        assert body["agent_id"] == "custom_sa"
        assert body["source"] == "user"

        listing = c.get("/api/agents").json()
        ids = [a["agent_id"] for a in listing["agents"]]
        # builtin seed + user 都在
        assert "custom_sa" in ids
        assert "seed_prd" in ids


def test_update_agent(tmp_db):
    with TestClient(appmod.app) as c:
        c.post("/api/agents", json=_SAMPLE_AGENT)
        updated = {**_SAMPLE_AGENT, "name": "Renamed SA", "max_iterations": 3}
        r = c.put("/api/agents/custom_sa", json=updated)
        assert r.status_code == 200
        assert r.json()["name"] == "Renamed SA"
        assert r.json()["max_iterations"] == 3


def test_user_agent_overrides_builtin_by_id(tmp_db):
    """user 用同 id 覆寫 builtin seed → list 內顯示 user 版本。"""
    with TestClient(appmod.app) as c:
        c.post("/api/agents", json={**_SAMPLE_AGENT, "agent_id": "seed_prd", "name": "User PRD"})
        listing = c.get("/api/agents").json()
        seed = next(a for a in listing["agents"] if a["agent_id"] == "seed_prd")
        assert seed["source"] == "user"
        assert seed["name"] == "User PRD"


def test_delete_user_agent(tmp_db):
    with TestClient(appmod.app) as c:
        c.post("/api/agents", json=_SAMPLE_AGENT)
        r = c.delete("/api/agents/custom_sa")
        assert r.status_code == 200
        assert r.json()["deleted"] == "custom_sa"
        # builtin seed 還在
        listing = c.get("/api/agents").json()
        ids = [a["agent_id"] for a in listing["agents"]]
        assert "custom_sa" not in ids
        assert "seed_prd" in ids


def test_invalid_iterations_rejected(tmp_db):
    with TestClient(appmod.app) as c:
        bad = {**_SAMPLE_AGENT, "max_iterations": 0}
        r = c.post("/api/agents", json=bad)
        assert r.status_code == 400
        assert r.json()["detail"]["category"] == "invalid_iterations"


# ============================================================
#  per-thread workflow + WorkflowEngine 解析
# ============================================================
def test_set_thread_workflow_and_engine_resolution(tmp_db):
    """user-defined workflow 綁 thread → engine.active_workflow_for 回該 user workflow。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        c.post("/api/workflows", json=_SAMPLE_WORKFLOW)
        r = c.post(f"/api/projects/{tid}/workflow", json={"workflow_id": "my-flow"})
        assert r.status_code == 200
        assert r.json()["workflow_id"] == "my-flow"

        # engine 解析正確
        engine: WorkflowEngine = appmod.app.state.engine
        wf = engine.active_workflow_for(tid)
        assert wf.id == "my-flow"
        assert wf.stages == ("prd",)


def test_unbind_thread_workflow_falls_back_to_default(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        c.post("/api/workflows", json=_SAMPLE_WORKFLOW)
        c.post(f"/api/projects/{tid}/workflow", json={"workflow_id": "my-flow"})
        # 解除
        r = c.post(f"/api/projects/{tid}/workflow", json={"workflow_id": None})
        assert r.status_code == 200
        # engine fallback default
        wf = appmod.app.state.engine.active_workflow_for(tid)
        assert wf.id == "default"


def test_set_thread_workflow_unknown_id_404(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        r = c.post(f"/api/projects/{tid}/workflow", json={"workflow_id": "nonexistent"})
        assert r.status_code == 404
        assert r.json()["detail"]["category"] == "workflow_not_found"


def test_user_workflow_multi_binding_preserved_in_engine(tmp_db):
    """user workflow with multi-binding → engine 解析時保留 agent_bindings 1:N。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        c.post("/api/workflows", json={
            "id": "discuss-flow",
            "label": "Discussion Flow",
            "description": "",
            "stages": [{
                "stage_id": "prd",
                "depends_on": [],
                "agent_bindings": [
                    {"agent_id": "sa", "role": "lead"},
                    {"agent_id": "pm", "role": "peer"},
                ],
                "collab_mode": "discussion",
            }],
        })
        c.post(f"/api/projects/{tid}/workflow", json={"workflow_id": "discuss-flow"})

        engine: WorkflowEngine = appmod.app.state.engine
        wf = engine.active_workflow_for(tid)
        assert wf.id == "discuss-flow"
        bindings = wf.agent_bindings.get("prd", ())
        assert len(bindings) == 2
        assert bindings[0].agent_id == "sa" and bindings[0].role == "lead"
        assert bindings[1].agent_id == "pm" and bindings[1].role == "peer"
        assert wf.collab_mode.get("prd") == "discussion"
