"""_create_github_repo（P2）：個人/org endpoint、public/private、auto_init、token 缺、HTTP 錯誤。全 mock urllib。"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

import plugins.builtin_integrations.register as reg
from plugins.builtin_integrations.register import _create_github_repo


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch, body=None, exc=None):
    calls = {}

    def fake_urlopen(req, timeout=20):
        calls["url"] = req.full_url
        calls["payload"] = json.loads(req.data.decode("utf-8"))
        calls["auth"] = req.headers.get("Authorization")
        if exc:
            raise exc
        return _Resp(body if body is not None else {"full_name": "owner/repo"})

    monkeypatch.setattr(reg.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_personal_repo_private(monkeypatch):
    calls = _capture(monkeypatch, {"full_name": "me/proj"})
    out = _create_github_repo({"token": "t"}, "proj", "private", "")
    assert out == "me/proj"
    assert calls["url"] == "https://api.github.com/user/repos"
    assert calls["payload"] == {"name": "proj", "private": True, "auto_init": True}
    assert calls["auth"] == "Bearer t"


def test_org_repo_public(monkeypatch):
    calls = _capture(monkeypatch, {"full_name": "acme/proj"})
    out = _create_github_repo({"token": "t"}, "proj", "public", "acme")
    assert out == "acme/proj"
    assert calls["url"] == "https://api.github.com/orgs/acme/repos"
    assert calls["payload"]["private"] is False


def test_missing_token():
    with pytest.raises(RuntimeError, match="token"):
        _create_github_repo({}, "proj", "private", "")


def test_http_error_surfaces_code(monkeypatch):
    err = urllib.error.HTTPError("u", 422, "Unprocessable", {},
                                 io.BytesIO(b'{"message":"name already exists"}'))
    _capture(monkeypatch, exc=err)
    with pytest.raises(RuntimeError, match="422"):
        _create_github_repo({"token": "t"}, "proj", "private", "")


def test_no_full_name_in_response(monkeypatch):
    _capture(monkeypatch, {"id": 1})   # 回應缺 full_name
    with pytest.raises(RuntimeError, match="full_name"):
        _create_github_repo({"token": "t"}, "proj", "private", "")
