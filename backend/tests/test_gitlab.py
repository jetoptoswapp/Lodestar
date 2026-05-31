"""GitLab（P4）：_create_gitlab_repo（個人/group、visibility）+ make_gitlab_mr_opener（push/MR/rollback）。全 mock。"""
from __future__ import annotations

import json

import pytest

import plugins.builtin_integrations.register as reg
from async_runtime import gitlab_mr
from async_runtime.gitlab_mr import MrError, make_gitlab_mr_opener
from plugins.builtin_integrations.register import _create_gitlab_repo


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_create_gitlab_personal(monkeypatch):
    seen = []

    def fake(req, timeout=20):
        seen.append((req.full_url, json.loads(req.data) if req.data else None))
        return _Resp({"path_with_namespace": "me/proj"})

    monkeypatch.setattr(reg.urllib.request, "urlopen", fake)
    out = _create_gitlab_repo({"token": "t"}, "proj", "private", "")
    assert out == "me/proj"
    url, payload = seen[-1]
    assert url.endswith("/api/v4/projects") and payload == {"name": "proj", "visibility": "private"}


def test_create_gitlab_group_with_namespace(monkeypatch):
    def fake(req, timeout=20):
        if req.full_url.endswith("/namespaces/mygroup"):
            return _Resp({"id": 42})
        return _Resp({"path_with_namespace": "mygroup/proj"})

    monkeypatch.setattr(reg.urllib.request, "urlopen", fake)
    assert _create_gitlab_repo({"token": "t"}, "proj", "public", "mygroup") == "mygroup/proj"


def test_create_gitlab_missing_token():
    with pytest.raises(RuntimeError, match="token"):
        _create_gitlab_repo({}, "proj", "private", "")


class _P:
    def __init__(self, rc=0, out=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def _patch_git(monkeypatch, has_changes=True, rec=None):
    def fake_run(args, **kw):
        if rec is not None:
            rec.append(args)
        if "diff" in args and "--quiet" in args:
            return _P(1 if has_changes else 0)
        if "rev-parse" in args:
            return _P(0, "main\n")
        return _P(0)

    monkeypatch.setattr(gitlab_mr.subprocess, "run", fake_run)


def test_gitlab_mr_happy(monkeypatch):
    rec = []
    _patch_git(monkeypatch, rec=rec)
    monkeypatch.setattr(gitlab_mr, "_create_mr",
                        lambda *a, **k: "https://gitlab.com/g/p/-/merge_requests/1")
    opener = make_gitlab_mr_opener(get_token=lambda: "tok", workdir_for=lambda sid: "/tmp/x")
    assert opener(7, "g/p", "") == "https://gitlab.com/g/p/-/merge_requests/1"
    joined = " ".join(" ".join(a) for a in rec)
    assert "oauth2:tok@gitlab.com/g/p.git" in joined and "HEAD:lodestar/impl-7" in joined


def test_gitlab_mr_no_changes(monkeypatch):
    _patch_git(monkeypatch, has_changes=False)
    opener = make_gitlab_mr_opener(get_token=lambda: "tok", workdir_for=lambda sid: "/tmp/x")
    with pytest.raises(MrError, match="無變更"):
        opener(7, "g/p", "")


def test_gitlab_mr_rollback(monkeypatch):
    rec = []
    _patch_git(monkeypatch, rec=rec)

    def boom(*a, **k):
        raise RuntimeError("422")
    monkeypatch.setattr(gitlab_mr, "_create_mr", boom)
    opener = make_gitlab_mr_opener(get_token=lambda: "tok", workdir_for=lambda sid: "/tmp/x")
    with pytest.raises(MrError, match="回滾"):
        opener(7, "g/p", "")
    assert any("--delete" in a for a in rec)
