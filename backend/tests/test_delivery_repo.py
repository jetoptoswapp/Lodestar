"""resolve_project_repo + prepare_clone（P3）：existing / new-lazy建 / 冪等 / 未設定 / 缺 token / clone。"""
from __future__ import annotations

import pytest

import keystore
from delivery_repo import DeliveryRepoError, resolve_project_repo
from persistence import dal


class _Integ:
    def __init__(self, create_repo=None):
        self.create_repo = create_repo


class _Reg:
    def __init__(self, integrations):
        self.integrations = integrations


def test_existing(tmp_db):
    dal.create_project("t1", "P", delivery_target="github", repo_mode="existing", repo_full_name="o/r")
    assert resolve_project_repo(_Reg({"github": _Integ()}), "t1") == ("github", "o/r")


def test_new_lazy_creates_and_backfills(tmp_db):
    keystore.reset_cache()
    keystore.set_credentials("github", {"token": "tok"})
    seen = {}

    def create(creds, name, visibility, owner):
        seen.update(creds=creds, name=name, visibility=visibility, owner=owner)
        return f"{owner or 'me'}/{name}"

    dal.create_project("t1", "My Proj", delivery_target="github", repo_mode="new",
                       repo_owner="acme", repo_visibility="public")
    target, full = resolve_project_repo(_Reg({"github": _Integ(create_repo=create)}), "t1")
    assert target == "github" and full == "acme/my-proj"            # name slug
    assert seen["owner"] == "acme" and seen["visibility"] == "public" and seen["creds"]["token"] == "tok"
    p = dal.get_project("t1")
    assert p["repo_full_name"] == "acme/my-proj" and p["repo_created"] == 1   # 回填


def test_new_idempotent(tmp_db):
    keystore.reset_cache()
    keystore.set_credentials("github", {"token": "tok"})
    n = {"c": 0}

    def create(creds, name, visibility, owner):
        n["c"] += 1
        return "me/r"

    dal.create_project("t1", "P", delivery_target="github", repo_mode="new")
    reg = _Reg({"github": _Integ(create_repo=create)})
    resolve_project_repo(reg, "t1")
    resolve_project_repo(reg, "t1")
    assert n["c"] == 1                                              # 第二次冪等不重建


def test_not_configured(tmp_db):
    dal.create_project("t1", "P")
    with pytest.raises(DeliveryRepoError, match="未設定"):
        resolve_project_repo(_Reg({}), "t1")


def test_new_missing_token(tmp_db):
    keystore.reset_cache()
    keystore.delete_credentials("github")
    dal.create_project("t1", "P", delivery_target="github", repo_mode="new")
    with pytest.raises(DeliveryRepoError, match="token"):
        resolve_project_repo(_Reg({"github": _Integ(create_repo=lambda *a: "x/y")}), "t1")


def test_existing_missing_repo(tmp_db):
    dal.create_project("t1", "P", delivery_target="github", repo_mode="existing")
    with pytest.raises(DeliveryRepoError, match="repo"):
        resolve_project_repo(_Reg({"github": _Integ()}), "t1")


def test_prepare_clone_uses_token_url(tmp_db, monkeypatch):
    from async_runtime import orchestrator

    class _P:
        returncode = 0
        stdout = ""
        stderr = ""

    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return _P()

    monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
    dest = orchestrator.prepare_clone(5, "o/r", "tok")
    assert dest == orchestrator.clone_dir(5)
    joined = " ".join(seen["args"])
    assert "clone" in joined and "x-access-token:tok@github.com/o/r.git" in joined
