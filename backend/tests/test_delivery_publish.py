"""Delivery publish（M2.5）：stories markdown → DeliveryItem[] → preview / publish。

GitHub real publish 用 monkeypatch 攔截 urlopen，不真打外部 API。
"""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app as appmod
from delivery_parser import parse_stories_to_delivery_items, stories_doc_title
from persistence import dal


_SAMPLE_STORIES = """# 範例專案 — User Stories

## Milestone 1 — 基礎建設

## Epic 1: 專案骨架

### Story 1.1 — pnpm workspace scaffold

**As a** developer, **I want** a working monorepo **so that** I can dev quickly.

**Acceptance Criteria**
- AC-1: Given a clean checkout, When I run `pnpm install --frozen-lockfile`, Then exit code is 0.
- AC-2: Given the repo root, When I list it, Then `pnpm-lock.yaml` and `turbo.json` exist.

**Requirement IDs**: FR-1, NFR-1

**Senior RD Estimate**
- 3

### Story 1.2 — CI workflow

**As a** developer, **I want** CI on every PR **so that** broken code can't merge.

**Acceptance Criteria**
- AC-1: Given a PR, When CI runs, Then it executes lint + test + build.

**Requirement IDs**: OPS-1

**Senior RD Estimate**
- 2

## Epic 2: Auth

### Story 2.1 — Login with Google OAuth

**As a** user, **I want** Google login **so that** I don't manage passwords.

**Acceptance Criteria**
- AC-1: Given Google credentials, When I click "Login with Google", Then session is created.

**Requirement IDs**: FR-7, NFR-4

**Senior RD Estimate**
- 4
"""


# ============================================================
#  Parser
# ============================================================
def test_parse_basic_three_stories():
    items = parse_stories_to_delivery_items(_SAMPLE_STORIES, target_project="acme/checkout")
    assert len(items) == 3
    titles = [it.title for it in items]
    assert "Story 1.1 — pnpm workspace scaffold" in titles
    assert "Story 1.2 — CI workflow" in titles
    assert "Story 2.1 — Login with Google OAuth" in titles


def test_parse_extracts_requirement_refs():
    items = parse_stories_to_delivery_items(_SAMPLE_STORIES)
    by_title = {it.title: it for it in items}
    assert by_title["Story 1.1 — pnpm workspace scaffold"].requirement_refs == ["FR-1", "NFR-1"]
    assert by_title["Story 1.2 — CI workflow"].requirement_refs == ["OPS-1"]
    assert by_title["Story 2.1 — Login with Google OAuth"].requirement_refs == ["FR-7", "NFR-4"]


def test_parse_groups_by_epic():
    items = parse_stories_to_delivery_items(_SAMPLE_STORIES)
    by_title = {it.title: it for it in items}
    assert "Epic 1" in by_title["Story 1.1 — pnpm workspace scaffold"].group
    assert "Epic 1" in by_title["Story 1.2 — CI workflow"].group
    assert "Epic 2" in by_title["Story 2.1 — Login with Google OAuth"].group


def test_parse_estimates_in_hours():
    items = parse_stories_to_delivery_items(_SAMPLE_STORIES)
    by_title = {it.title: it for it in items}
    # estimate 是整數（tracker 用），senior_rd_days 是小時/8
    assert by_title["Story 1.1 — pnpm workspace scaffold"].estimate == 3
    assert by_title["Story 1.1 — pnpm workspace scaffold"].senior_rd_days == round(3 / 8, 2)
    assert by_title["Story 2.1 — Login with Google OAuth"].estimate == 4


def test_parse_labels_include_epic_and_req_prefix():
    items = parse_stories_to_delivery_items(_SAMPLE_STORIES)
    by_title = {it.title: it for it in items}
    s11 = by_title["Story 1.1 — pnpm workspace scaffold"]
    assert "story" in s11.labels
    assert "epic-1" in s11.labels
    assert "fr" in s11.labels and "nfr" in s11.labels


def test_parse_empty_returns_empty():
    assert parse_stories_to_delivery_items("") == []
    assert parse_stories_to_delivery_items("# Just a title — User Stories\n\nNo content.") == []


def test_parse_body_preserves_AC_section():
    """spec 附錄 D：parse_delivery_items 必須保留 AC heading + bullet shape 供 verifier regex 抓。"""
    items = parse_stories_to_delivery_items(_SAMPLE_STORIES)
    by_title = {it.title: it for it in items}
    assert "**Acceptance Criteria**" in by_title["Story 1.1 — pnpm workspace scaffold"].body
    assert "AC-1:" in by_title["Story 1.1 — pnpm workspace scaffold"].body


def test_doc_title():
    assert stories_doc_title(_SAMPLE_STORIES) == "範例專案"
    assert stories_doc_title("# Demo Project — User Stories\n") == "Demo Project"


# ============================================================
#  preview endpoint
# ============================================================
def test_preview_delivery_happy(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "delivery test"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)

        r = c.post(
            f"/api/stage/stories/{tid}/preview-delivery",
            json={"target": "github", "config": {"repo": "acme/checkout"}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["target"] == "github"
        assert body["item_count"] == 3
        assert len(body["items"]) == 3
        first = body["items"][0]
        assert first["destination"] == "acme/checkout"
        assert first["title"].startswith("Story 1.1 —")
        assert "story" in first["labels"]


def test_preview_unknown_integration_404(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)
        r = c.post(
            f"/api/stage/stories/{tid}/preview-delivery",
            json={"target": "nonexistent", "config": {}},
        )
        assert r.status_code == 404
        assert r.json()["detail"]["category"] == "integration_not_found"


def test_preview_empty_stories_400(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        r = c.post(
            f"/api/stage/stories/{tid}/preview-delivery",
            json={"target": "github", "config": {"repo": "x/y"}},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["category"] == "stories_empty"


def test_preview_unparseable_stories_400(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", "# No epic / no story headings here.")
        r = c.post(
            f"/api/stage/stories/{tid}/preview-delivery",
            json={"target": "github", "config": {}},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["category"] == "stories_unparseable"


# ============================================================
#  publish endpoint —— dry_run + monkeypatched real publish
# ============================================================
def test_publish_dry_run(tmp_db):
    """dry_run=True → 不打外部 API，回 placeholder URLs。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)
        r = c.post(
            f"/api/stage/stories/{tid}/publish",
            json={"target": "github", "config": {
                "repo": "acme/checkout",
                "token": "fake",
                "dry_run": True,
            }},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["count"] == 3
        assert len(body["created"]) == 3
        for url in body["created"]:
            assert url.startswith("https://github.com/acme/checkout/issues/dry-run-")


def _mock_resp(obj):
    """組一個 urlopen context-manager mock，read() 回 json(obj)。obj 可為 dict 或 list。"""
    m = MagicMock()
    m.read.return_value = json.dumps(obj).encode("utf-8")
    m.__enter__ = lambda s: m
    m.__exit__ = lambda s, *a: False
    return m


def _http_error(code: int, *, retry_after: str | None = None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", headers,
                                  io.BytesIO(b'{"message":"boom"}'))


def _gh_fake(existing: list[tuple[int, str]], post_handler):
    """fake_urlopen：GET（list_all_issues）回 existing；POST 交給 post_handler(req)。
    同一個 fake 同時 patch register 與 github_pr 兩個 urllib。"""
    def fake(req, timeout=20):
        if req.get_method() == "GET":
            return _mock_resp([{"number": n, "title": t} for n, t in existing])
        return post_handler(req)
    return fake


def test_publish_real_api_call_intercepted(tmp_db):
    """攔截 urlopen 模擬 GitHub 回 200 + html_url；驗證 payload + 冪等過濾（既有為空 → 全建）。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)

        captured_payloads: list[dict] = []
        def post_handler(req):
            payload = json.loads(req.data.decode("utf-8"))
            captured_payloads.append(payload)
            n = len(captured_payloads)
            return _mock_resp({"number": n, "html_url": f"https://github.com/acme/checkout/issues/{n}"})

        fake = _gh_fake([], post_handler)   # 既有 issue 為空
        with patch("plugins.builtin_integrations.register.urllib.request.urlopen", fake), \
             patch("async_runtime.github_pr.urllib.request.urlopen", fake):
            r = c.post(
                f"/api/stage/stories/{tid}/publish",
                json={"target": "github", "config": {"repo": "acme/checkout", "token": "ghp_fake_token"}},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["count"] == 3
        assert len(body["created"]) == 3
        assert body["skipped"] == 0 and body["failed"] == []
        assert all("github.com/acme/checkout/issues/" in u for u in body["created"])
        assert len(captured_payloads) == 3
        p1 = captured_payloads[0]
        assert "Story 1.1 — pnpm workspace scaffold" == p1["title"]
        assert "story" in p1["labels"] and "epic-1" in p1["labels"]
        assert "**Acceptance Criteria**" in p1["body"]


def test_publish_missing_token_fails(tmp_db):
    """token 缺失 → success=False（不真打、不 raise）。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)
        r = c.post(
            f"/api/stage/stories/{tid}/publish",
            json={"target": "github", "config": {"repo": "acme/checkout"}},
        )
        assert r.status_code == 200  # endpoint OK，但 success=False
        body = r.json()
        assert body["success"] is False
        assert body["created"] == []


def test_publish_jira_remains_stub(tmp_db):
    """Jira / GitLab 仍 stub，回 success=False。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)
        r = c.post(
            f"/api/stage/stories/{tid}/publish",
            json={"target": "jira", "config": {"project_key": "ACME"}},
        )
        assert r.status_code == 200
        assert r.json()["success"] is False


# ============================================================
#  冪等重推（只補缺漏、不重複）+ 逐項失敗 + 限流退避 + 列舉失敗中止
# ============================================================
def test_publish_idempotent_skips_existing(tmp_db):
    """repo 已有 Story 1.1 的 issue → 重發只建其餘 story，1.1 跳過、不重複。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)

        posted: list[dict] = []
        def post_handler(req):
            posted.append(json.loads(req.data.decode("utf-8")))
            n = 100 + len(posted)
            return _mock_resp({"number": n, "html_url": f"https://github.com/acme/checkout/issues/{n}"})

        existing = [(1, "Story 1.1 — pnpm workspace scaffold")]   # 1.1 已存在
        fake = _gh_fake(existing, post_handler)
        with patch("plugins.builtin_integrations.register.urllib.request.urlopen", fake), \
             patch("async_runtime.github_pr.urllib.request.urlopen", fake):
            r = c.post(f"/api/stage/stories/{tid}/publish",
                       json={"target": "github", "config": {"repo": "acme/checkout", "token": "t"}})
        body = r.json()
        assert r.status_code == 200 and body["success"] is True
        assert body["count"] == 3            # 預計總數不變
        assert body["skipped"] == 1          # 1.1 已存在 → 跳過
        assert len(body["created"]) == 2     # 只建其餘 2 個
        # 沒有任何 POST 帶 Story 1.1（沒重複建）
        assert not any("Story 1.1" in p["title"] for p in posted)


def test_publish_surfaces_per_item_failure(tmp_db):
    """某個 story 建立失敗 → failed 帶 (title, reason)，其餘照常建立。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)

        n = {"i": 0}
        def post_handler(req):
            n["i"] += 1
            if n["i"] == 2:                  # 第 2 個失敗（422，非限流 → 不重試）
                raise _http_error(422)
            return _mock_resp({"number": n["i"], "html_url": f"https://github.com/acme/checkout/issues/{n['i']}"})

        fake = _gh_fake([], post_handler)
        with patch("plugins.builtin_integrations.register.urllib.request.urlopen", fake), \
             patch("async_runtime.github_pr.urllib.request.urlopen", fake):
            r = c.post(f"/api/stage/stories/{tid}/publish",
                       json={"target": "github", "config": {"repo": "acme/checkout", "token": "t"}})
        body = r.json()
        assert r.status_code == 200 and body["success"] is False
        assert len(body["created"]) == 2 and len(body["failed"]) == 1
        assert "422" in body["failed"][0]["reason"] and body["failed"][0]["title"]


def test_publish_backoff_retries_rate_limit(tmp_db):
    """secondary rate limit（403）→ 讀 Retry-After 退避重試 → 最終成功，不算失敗。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)

        calls = {"post": 0}
        def post_handler(req):
            calls["post"] += 1
            if calls["post"] == 1:           # 第一次 POST 撞限流，重試後成功
                raise _http_error(403, retry_after="0")
            n = calls["post"]
            return _mock_resp({"number": n, "html_url": f"https://github.com/acme/checkout/issues/{n}"})

        fake = _gh_fake([], post_handler)
        with patch("plugins.builtin_integrations.register.urllib.request.urlopen", fake), \
             patch("async_runtime.github_pr.urllib.request.urlopen", fake), \
             patch("plugins.builtin_integrations.register.time.sleep", lambda *_: None):
            r = c.post(f"/api/stage/stories/{tid}/publish",
                       json={"target": "github", "config": {"repo": "acme/checkout", "token": "t"}})
        body = r.json()
        assert r.status_code == 200 and body["success"] is True
        assert len(body["created"]) == 3 and body["failed"] == []
        assert calls["post"] == 4            # 3 個 story + 1 次重試


def test_publish_aborts_when_existing_unverifiable(tmp_db):
    """列既有 issue 失敗（GET 500）→ 中止（502），不冒重複發佈的險。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)

        def fake(req, timeout=20):
            if req.get_method() == "GET":
                raise _http_error(500)
            raise AssertionError("列舉失敗時不應該再 POST 建 issue")

        with patch("plugins.builtin_integrations.register.urllib.request.urlopen", fake), \
             patch("async_runtime.github_pr.urllib.request.urlopen", fake):
            r = c.post(f"/api/stage/stories/{tid}/publish",
                       json={"target": "github", "config": {"repo": "acme/checkout", "token": "t"}})
        assert r.status_code == 502
        assert "existing_issues_unverified" in json.dumps(r.json())
