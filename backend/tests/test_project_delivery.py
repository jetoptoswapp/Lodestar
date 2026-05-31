"""Project delivery repo 設定（P1）：POST 帶 delivery / PATCH 改 delivery / 預設值 / partial merge。"""
from __future__ import annotations

from fastapi.testclient import TestClient

import app as appmod


def test_create_with_delivery(tmp_db):
    with TestClient(appmod.app) as c:
        body = c.post("/api/projects", json={
            "name": "P", "delivery_target": "github", "repo_mode": "new",
            "repo_owner": "myorg", "repo_visibility": "public"}).json()
        assert body["delivery_target"] == "github" and body["repo_mode"] == "new"
        assert body["repo_owner"] == "myorg" and body["repo_visibility"] == "public"
        assert body["repo_created"] is False


def test_create_defaults(tmp_db):
    with TestClient(appmod.app) as c:
        body = c.post("/api/projects", json={"name": "P"}).json()
        assert body["delivery_target"] == "" and body["repo_mode"] == ""
        assert body["repo_visibility"] == "private" and body["repo_created"] is False


def test_patch_delivery_only_keeps_name(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "P"}).json()["thread_id"]
        r = c.patch(f"/api/projects/{tid}", json={
            "delivery_target": "github", "repo_mode": "existing", "repo_full_name": "o/r"})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "P"                                  # name 不變
        assert body["delivery_target"] == "github" and body["repo_full_name"] == "o/r"


def test_patch_name_keeps_delivery(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={
            "name": "P", "delivery_target": "github", "repo_mode": "new"}).json()["thread_id"]
        c.patch(f"/api/projects/{tid}", json={"name": "P2"})
        body = c.get(f"/api/projects/{tid}").json()
        assert body["name"] == "P2"
        assert body["delivery_target"] == "github" and body["repo_mode"] == "new"   # delivery 保留


def test_patch_partial_delivery_merges(tmp_db):
    """只帶部分 delivery 欄 → 沒帶的沿用現值。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={
            "name": "P", "delivery_target": "github", "repo_mode": "new",
            "repo_owner": "org1", "repo_visibility": "private"}).json()["thread_id"]
        c.patch(f"/api/projects/{tid}", json={"repo_visibility": "public"})
        body = c.get(f"/api/projects/{tid}").json()
        assert body["repo_visibility"] == "public"
        assert body["repo_owner"] == "org1" and body["repo_mode"] == "new"          # 其他沿用
