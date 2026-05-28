"""通用 API endpoint（M0）：health / stages（空 catalog）/ plugins / integrations。"""
from __future__ import annotations

from fastapi.testclient import TestClient

import app as appmod


def test_m0_endpoints(tmp_db):
    with TestClient(appmod.app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}

        # M0 沒有 stage capability → catalog 為空 list（非硬編碼）
        assert client.get("/api/stages").json() == {"stages": []}

        plugins = client.get("/api/plugins").json()["plugins"]
        bi = next(p for p in plugins if p["id"] == "builtin_integrations")
        assert bi["enabled"] is True
        assert bi["load_error"] is None
        assert set(bi["provides"]["integrations"]) == {"github", "jira", "gitlab"}

        integ = client.get("/api/integrations").json()["integrations"]
        assert {i["target"] for i in integ} == {"github", "jira", "gitlab"}
        # config_schema 供前端自動 render 設定表單
        gh = next(i for i in integ if i["target"] == "github")
        assert "fields" in gh["config_schema"]
