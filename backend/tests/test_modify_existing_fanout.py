"""modify_existing 多 story fan-out 缺口修補。

驗證：
- change_request brief 用 canonical Epic/Story（大改）→ parser 拆得出 per-story 交付項。
- brief 用 CH-N 條列（小改）→ parser 回 0 筆，不誤觸 fan-out（仍走 single implement）。
- `_delivery_story_artifact` fallback：無 stories artifact 時用 change_request；有 stories 則優先。
"""
from __future__ import annotations

from delivery_parser import detect_truncated_stories, parse_stories_to_delivery_items
from persistence import dal


# 大改：brief 內含 canonical Epic/Story（包在 brief prose 中）
_BRIEF_CANONICAL = """# Implementation Brief — 補前端

## 1. Summary
為純後端專案補上 Web 前端。

## 2. Affected Code
frontend/（新增），讀 app/api/* 契約。

## 3. Changes

## Epic 1: JetBook Web Frontend

### Story 1.1 — [scaffold] Vite + React 骨架

**As a** developer, **I want** a buildable scaffold **so that** CI passes.

**Acceptance Criteria**
- AC-1: Given a clean checkout, When `npm run build`, Then exit code is 0.

**Requirement IDs**: FR-1

**Senior RD Estimate**
- 3

### Story 1.2 — Dashboard 畫面（串真實 API）

**As a** user, **I want** a dashboard **so that** I see my documents.

**Acceptance Criteria**
- AC-1: Given `npm run dev`, When I open `/`, Then it renders from `GET /api/v1/dashboard`.

**Requirement IDs**: FR-11

**Senior RD Estimate**
- 4

## 4. Acceptance Criteria
- AC-1: npm run build 通過。

## 6. Out of Scope
- 後端不改。
"""

# 小改：傳統 CH-N 條列，無 canonical Epic/Story
_BRIEF_CH_LIST = """# Implementation Brief — 修 bug

## 1. Summary
修正登入後 302 導向錯誤。

## 2. Affected Code
app/api/auth.py:42 redirect 邏輯。

## 3. Changes
- `CH-1`: app/api/auth.py:42 改成導向 `/dashboard`。
- `CH-2`: 補一條 redirect 測試。

## 4. Acceptance Criteria
- AC-1: 登入後到 /dashboard。
"""


def test_canonical_brief_fans_out_into_stories():
    """大改 brief（canonical Epic/Story）→ 拆得出逐 story 交付項。"""
    items = parse_stories_to_delivery_items(_BRIEF_CANONICAL, target_project="acme/jetbook")
    titles = [it.title for it in items]
    assert len(items) == 2, f"應拆出 2 個 story，實得 {titles}"
    assert "Story 1.1 — [scaffold] Vite + React 骨架" in titles
    assert "Story 1.2 — Dashboard 畫面（串真實 API）" in titles


def test_ch_list_brief_does_not_fan_out():
    """小改 brief（CH-N 條列）→ 0 個 story，不誤觸 fan-out（維持 single implement）。"""
    items = parse_stories_to_delivery_items(_BRIEF_CH_LIST, target_project="acme/jetbook")
    assert items == [], "CH-N 小改不應被當成多 story 來 fan-out"


def test_delivery_story_artifact_fallback(tmp_db):
    """無 stories artifact → fallback 用 change_request；有 stories → 優先 stories。"""
    from app import _delivery_story_artifact

    dal.create_project("t-fanout", "proj")
    # 只有 change_request（modify_existing 的情境）
    dal.upsert_artifact("t-fanout", "change_request", _BRIEF_CANONICAL)
    assert _delivery_story_artifact("t-fanout") == _BRIEF_CANONICAL

    # 一旦有 stories artifact，應優先用它（生成 pipeline 不受影響）
    dal.upsert_artifact("t-fanout", "stories", "# X — User Stories\n\n## Epic 1: A\n\n### Story 1.1 — B\n")
    assert _delivery_story_artifact("t-fanout").startswith("# X — User Stories")


# 接續編號（Epic 13 起）的 brief：detect_truncated_stories 不可誤殺（D4↔截斷守門衝突修復）
_BRIEF_EPIC13 = (_BRIEF_CANONICAL
                 .replace("## Epic 1:", "## Epic 13:")
                 .replace("### Story 1.1", "### Story 13.1")
                 .replace("### Story 1.2", "### Story 13.2"))


def test_renumbered_brief_not_truncated_with_allow_renumbered():
    """modify_existing 接續編號 brief（Epic 13 起）：allow_renumbered=True 不誤判截斷；
    預設（greenfield）仍嚴格視為截斷——保護生成 pipeline 的砍頭偵測不被弱化。"""
    assert detect_truncated_stories(_BRIEF_EPIC13, allow_renumbered=True) is None
    assert detect_truncated_stories(_BRIEF_EPIC13, allow_renumbered=False) is not None
    # 能正常拆出 2 個 story（內容完好，純守門誤判才是 bug）
    items = parse_stories_to_delivery_items(_BRIEF_EPIC13, target_project="acme/jetbook")
    assert len(items) == 2


def test_renumbered_head_loss_still_caught():
    """continuation 模式仍要抓真砍頭：最低 story 非該 epic 的 .1（首段遺失）→ 判截斷。"""
    headless = (_BRIEF_CANONICAL
                .replace("## Epic 1:", "## Epic 13:")
                .replace("### Story 1.1", "### Story 13.4")   # 首個 story 變 13.4（缺 13.1–13.3）
                .replace("### Story 1.2", "### Story 13.5"))
    assert detect_truncated_stories(headless, allow_renumbered=True) is not None
