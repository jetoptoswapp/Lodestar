"""Server-side credential keystore：加密 round-trip + 憑證 endpoints + 機密不外洩 + config 合併。"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import app as appmod
import keystore
from persistence import dal


@pytest.fixture
def ks_db(tmp_db, monkeypatch):
    """在 tmp_db 之上固定一把 Fernet key 並清掉快取，確保加密確定且測試隔離。"""
    monkeypatch.setenv("LODESTAR_KEYSTORE_KEY", Fernet.generate_key().decode("ascii"))
    keystore.reset_cache()
    yield tmp_db
    keystore.reset_cache()


def test_keystore_roundtrip_and_ciphertext_at_rest(ks_db):
    secret = {"repo": "owner/repo", "token": "ghp_SUPERSECRET_value"}
    keystore.set_credentials("github", secret)

    # 讀回一致
    assert keystore.get_credentials("github") == secret
    assert keystore.has_credentials("github") is True

    # DB 內是密文，且不含明文 token（加密落地）
    raw = dal.get_integration_secret("github")
    assert raw and "ghp_SUPERSECRET_value" not in raw
    assert "owner/repo" not in raw

    # 刪除
    assert keystore.delete_credentials("github") is True
    assert keystore.get_credentials("github") == {}
    assert keystore.has_credentials("github") is False


def test_get_credentials_invalid_token_returns_empty(ks_db, monkeypatch):
    keystore.set_credentials("github", {"token": "x"})
    # 換一把 key → 舊密文解不開 → 回 {} 而非爆掉
    monkeypatch.setenv("LODESTAR_KEYSTORE_KEY", Fernet.generate_key().decode("ascii"))
    keystore.reset_cache()
    assert keystore.get_credentials("github") == {}


def test_credentials_endpoints_do_not_leak_secret(ks_db):
    with TestClient(appmod.app) as client:
        # 初始：無憑證
        r = client.get("/api/integrations/github/credentials")
        assert r.status_code == 200
        assert r.json()["has_credentials"] is False

        # PUT 儲存（含機密 token + 非機密 repo）
        r = client.put(
            "/api/integrations/github/credentials",
            json={"repo": "owner/repo", "token": "ghp_secret123"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["has_credentials"] is True
        # 機密欄只回「已設定」清單，不回明文；非機密欄回實際值
        assert "token" in body["secret_fields_set"]
        assert "token" not in body["values"]
        assert body["values"].get("repo") == "owner/repo"

        # GET 同樣不外洩 token 明文（整個 response 不含明文）
        r = client.get("/api/integrations/github/credentials")
        assert "ghp_secret123" not in r.text

        # 空字串不覆寫既有機密（只更新 repo）
        client.put("/api/integrations/github/credentials", json={"repo": "owner/repo2", "token": ""})
        assert keystore.get_credentials("github")["token"] == "ghp_secret123"
        assert keystore.get_credentials("github")["repo"] == "owner/repo2"

        # DELETE
        assert client.delete("/api/integrations/github/credentials").status_code == 204
        assert client.get("/api/integrations/github/credentials").json()["has_credentials"] is False

        # 未註冊 target → 404
        assert client.get("/api/integrations/nope/credentials").status_code == 404


def test_effective_config_merges_keystore_secret(ks_db):
    # keystore 存了 token；request 只送非機密 repo → 合併後仍含 token
    keystore.set_credentials("github", {"token": "ghp_fromstore"})
    merged = appmod._effective_config("github", {"repo": "owner/repo"})
    assert merged == {"token": "ghp_fromstore", "repo": "owner/repo"}

    # request 的非空值覆蓋 keystore；空值不覆蓋
    merged2 = appmod._effective_config("github", {"token": "ghp_override", "repo": ""})
    assert merged2["token"] == "ghp_override"
