"""通用 API endpoint（M0）：health / stages（空 catalog）/ plugins / integrations。"""
from __future__ import annotations

from fastapi.testclient import TestClient

import app as appmod


def test_endpoints(tmp_db):
    with TestClient(appmod.app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}

        # M1+：PRD stage 已註冊；catalog 至少含 prd（catalog-driven，非硬編碼）
        stages = client.get("/api/stages").json()["stages"]
        prd = next((s for s in stages if s["id"] == "prd"), None)
        assert prd is not None, f"expected 'prd' in catalog, got {stages}"
        assert prd["supports_chat"] is True
        assert {"generate", "refine", "chat"}.issubset(set(prd["operations"]))
        assert prd["source"] == "builtin" and prd["plugin_id"] == "builtin_core_stages"
        assert prd["telemetry_stage"] == "specify"

        plugins = client.get("/api/plugins").json()["plugins"]
        bi = next(p for p in plugins if p["id"] == "builtin_integrations")
        assert bi["enabled"] is True and bi["load_error"] is None
        assert set(bi["provides"]["integrations"]) == {"github", "jira", "gitlab"}
        core = next(p for p in plugins if p["id"] == "builtin_core_stages")
        assert core["enabled"] is True and "prd" in core["provides"]["stages"]

        integ = client.get("/api/integrations").json()["integrations"]
        assert {i["target"] for i in integ} == {"github", "jira", "gitlab"}
        gh = next(i for i in integ if i["target"] == "github")
        assert "fields" in gh["config_schema"]

        # M1：default workflow 含 prd；新 thread → statuses 回 prd:draft
        tid = client.post("/api/projects", json={"name": "test"}).json()["thread_id"]
        statuses = client.get(f"/api/stage/statuses/{tid}").json()["statuses"]
        assert statuses == [{"stage_id": "prd", "status": "draft"}]
