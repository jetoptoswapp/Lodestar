"""Project CRUD：PATCH rename / DELETE cascade / POST approve。"""
from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient

import app as appmod
from persistence import dal


# ============================================================
#  PATCH /api/projects/{tid} —— rename
# ============================================================
def test_rename_project(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "舊名稱"}).json()["thread_id"]
        r = c.patch(f"/api/projects/{tid}", json={"name": "新名稱"})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "新名稱"
        assert body["thread_id"] == tid

        # GET 再讀回確認 persisted
        again = c.get(f"/api/projects/{tid}").json()
        assert again["name"] == "新名稱"


def test_rename_empty_name_400(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        for payload in ({"name": ""}, {"name": "   "}, {}):
            r = c.patch(f"/api/projects/{tid}", json=payload)
            assert r.status_code == 400, payload
            assert r.json()["detail"]["category"] == "invalid_name"


def test_rename_unknown_thread_404(tmp_db):
    with TestClient(appmod.app) as c:
        r = c.patch("/api/projects/nonexistent", json={"name": "x"})
        assert r.status_code == 404


# ============================================================
#  DELETE /api/projects/{tid} —— cascade
# ============================================================
def test_delete_project_cascade(tmp_db):
    """刪 thread 後：projects / artifacts / status / messages / events / attachments / 檔案 全部清乾淨。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "to-delete"}).json()["thread_id"]

        # 種一份完整 thread 資料
        dal.upsert_artifact(tid, "prd", "fake prd content")
        dal.set_stage_status(tid, "prd", "approved")
        dal.append_message(tid, "prd", "user", "hello")
        dal.append_message(tid, "prd", "assistant", "world")
        dal.append_event(tid, "prd", "generated", "")
        dal.add_revision(tid, "prd", "generate_prd", content_length=10)

        # 上傳一個附件（驗檔案 cleanup）
        files = {"file": ("a.md", io.BytesIO(b"hello"), "text/markdown")}
        upload_r = c.post(f"/api/stage/prd/{tid}/attachments", files=files)
        assert upload_r.status_code == 200
        file_id = upload_r.json()["file_id"]
        att_row = dal.get_attachment(file_id)
        assert att_row is not None
        att_path = dal.uploads_dir() / att_row["content_path"]
        assert att_path.exists(), "附件檔案在硬碟上應該存在"

        # delete
        r = c.delete(f"/api/projects/{tid}")
        assert r.status_code == 200
        assert r.json()["deleted"] == tid
        assert r.json()["files_removed"] >= 1

        # 全表 cascade 確認
        assert dal.get_project(tid) is None
        assert dal.get_artifact(tid, "prd") is None
        assert dal.list_messages(tid, "prd") == []
        assert dal.list_events(tid) == []
        assert dal.list_revisions(tid, "prd") == []
        assert dal.list_attachments(tid, "prd") == []
        assert dal.get_attachment(file_id) is None
        assert dal.list_stage_statuses(tid) == {}

        # 檔案系統 cleanup
        assert not att_path.exists(), "附件檔案應該已被清"
        thread_dir = dal.uploads_dir() / tid
        assert not thread_dir.exists(), "thread uploads dir 應該已被清"


def test_delete_unknown_thread_404(tmp_db):
    with TestClient(appmod.app) as c:
        r = c.delete("/api/projects/nonexistent_thread")
        assert r.status_code == 404


def test_delete_does_not_affect_other_threads(tmp_db):
    """確認 cascade scope 不會誤刪別人的 row（thread_id 是 partition）。"""
    with TestClient(appmod.app) as c:
        a = c.post("/api/projects", json={"name": "A"}).json()["thread_id"]
        b = c.post("/api/projects", json={"name": "B"}).json()["thread_id"]
        dal.upsert_artifact(a, "prd", "A's prd")
        dal.upsert_artifact(b, "prd", "B's prd")

        c.delete(f"/api/projects/{a}")
        # A 全沒、B 完整
        assert dal.get_project(a) is None and dal.get_artifact(a, "prd") is None
        assert dal.get_project(b) is not None
        assert dal.get_artifact(b, "prd") == "B's prd"


# ============================================================
#  POST /api/stage/{sid}/{tid}/approve
# ============================================================
def test_approve_stage(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        # 沒 artifact 不能 approve
        r = c.post(f"/api/stage/prd/{tid}/approve")
        assert r.status_code == 400
        assert r.json()["detail"]["category"] == "artifact_empty"

        # 寫 artifact 後可 approve
        dal.upsert_artifact(tid, "prd", "real prd content")
        r = c.post(f"/api/stage/prd/{tid}/approve")
        assert r.status_code == 200
        body = r.json()
        assert body["stage_id"] == "prd"
        assert body["status"] == "approved"
        assert body["has_content"] is True
        # DB 持久化
        assert dal.get_stage_status(tid, "prd") == "approved"


def test_approve_unknown_stage_404(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        r = c.post(f"/api/stage/non_existent_stage/{tid}/approve")
        assert r.status_code == 404
        assert r.json()["detail"]["category"] == "stage_not_found"


def test_approve_unknown_thread_404(tmp_db):
    with TestClient(appmod.app) as c:
        r = c.post("/api/stage/prd/nonexistent_thread/approve")
        assert r.status_code == 404
        assert r.json()["detail"]["category"] == "thread_not_found"
