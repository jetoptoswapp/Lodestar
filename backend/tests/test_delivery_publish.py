"""Delivery publish（M2.5）：stories markdown → DeliveryItem[] → preview / publish。

GitHub real publish 用 monkeypatch 攔截 urlopen，不真打外部 API。
"""
from __future__ import annotations

import io
import json
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


def test_publish_real_api_call_intercepted(tmp_db):
    """攔截 urlopen 模擬 GitHub 回 200 + html_url；驗證 payload 正確。"""
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "x"}).json()["thread_id"]
        dal.upsert_artifact(tid, "stories", _SAMPLE_STORIES)

        captured_payloads: list[dict] = []
        def fake_urlopen(req, timeout=20):
            payload = json.loads(req.data.decode("utf-8"))
            captured_payloads.append(payload)
            n = len(captured_payloads)
            mock = MagicMock()
            mock.read.return_value = json.dumps({
                "number": n,
                "html_url": f"https://github.com/acme/checkout/issues/{n}",
            }).encode("utf-8")
            mock.__enter__ = lambda s: mock
            mock.__exit__ = lambda s, *a: False
            return mock

        with patch("plugins.builtin_integrations.register.urllib.request.urlopen", fake_urlopen):
            r = c.post(
                f"/api/stage/stories/{tid}/publish",
                json={"target": "github", "config": {
                    "repo": "acme/checkout",
                    "token": "ghp_fake_token",
                }},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["count"] == 3
        assert len(body["created"]) == 3
        assert all("github.com/acme/checkout/issues/" in u for u in body["created"])

        # 驗證 payload 內容
        assert len(captured_payloads) == 3
        p1 = captured_payloads[0]
        assert "Story 1.1 — pnpm workspace scaffold" == p1["title"]
        assert "story" in p1["labels"]
        assert "epic-1" in p1["labels"]
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
