"""通用 API endpoint（M0 / M1 / M2）：health / stages / plugins / integrations / workflow statuses。"""
from __future__ import annotations

from fastapi.testclient import TestClient

import app as appmod


def test_endpoints(tmp_db):
    with TestClient(appmod.app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}

        # M2：catalog 含 prd / architecture / stories 三個 builtin stage
        stages = client.get("/api/stages").json()["stages"]
        by_id = {s["id"]: s for s in stages}
        assert {"prd", "architecture", "stories"}.issubset(by_id), \
            f"expected prd/architecture/stories, got {sorted(by_id)}"

        prd = by_id["prd"]
        assert prd["supports_chat"] is True
        assert {"generate", "refine", "chat"}.issubset(set(prd["operations"]))
        assert prd["source"] == "builtin" and prd["plugin_id"] == "builtin_core_stages"
        assert prd["telemetry_stage"] == "specify"
        assert prd["depends_on"] == []

        arch = by_id["architecture"]
        assert arch["telemetry_stage"] == "design"
        assert arch["depends_on"] == ["prd"]
        assert arch["downstream"] == ["stories"]
        assert arch["supports_chat"] is True

        sto = by_id["stories"]
        assert sto["telemetry_stage"] == "deliver"
        assert sto["depends_on"] == ["architecture"]
        assert sto["downstream"] == []
        assert sto["supports_chat"] is True

        plugins = client.get("/api/plugins").json()["plugins"]
        bi = next(p for p in plugins if p["id"] == "builtin_integrations")
        assert bi["enabled"] is True and bi["load_error"] is None
        assert set(bi["provides"]["integrations"]) == {"github", "jira", "gitlab"}
        core = next(p for p in plugins if p["id"] == "builtin_core_stages")
        assert core["enabled"] is True
        assert {"prd", "architecture", "stories"}.issubset(set(core["provides"]["stages"]))

        integ = client.get("/api/integrations").json()["integrations"]
        assert {i["target"] for i in integ} == {"github", "jira", "gitlab"}
        gh = next(i for i in integ if i["target"] == "github")
        assert "fields" in gh["config_schema"]

        # M2：default workflow = (prd, architecture, stories)；新 thread → 三個 stage 全 draft
        tid = client.post("/api/projects", json={"name": "test"}).json()["thread_id"]
        statuses = client.get(f"/api/stage/statuses/{tid}").json()["statuses"]
        assert statuses == [
            {"stage_id": "prd", "status": "draft"},
            {"stage_id": "architecture", "status": "draft"},
            {"stage_id": "stories", "status": "draft"},
        ]
