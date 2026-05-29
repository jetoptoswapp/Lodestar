"""Stories markdown → DeliveryItem[] parser（M2.5 delivery publish）。

對應 spec 附錄 D `delivery_items.md` prompt 的功能，但用確定性 regex 解析
（取代 LLM 解析）— 更穩定、更快、test-friendly。前端 lib/parse.ts 是對稱版。

Heading shape（spec 附錄 D HARD RULE）：
- `# <project> — User Stories`（title）
- `## Epic N: <title>` (epic)
- `### Story N.M — <title>` (story)
- body 含 `**As a** ... I want ... so that ...` + `**Acceptance Criteria**` bullets +
  `**Requirement IDs**: FR-1, NFR-2` + `**Senior RD Estimate** - 2`

每個 Story 變一個 DeliveryItem：
- title = `Story N.M — <title>`
- body = 整個 story 原始 markdown（preserve AC heading + bullet shape，給 verifier regex 抓）
- group = `Epic N: <title>`
- estimate = round(senior_rd_hours)  # tracker estimate（整數），可由 caller 改 mapping
- senior_rd_days = senior_rd_hours / 8  # 8h/day
- requirement_refs = [FR-1, NFR-2, ...]
- labels = ["story", "epic-N", + 每個 requirement_ref 的 prefix]
"""
from __future__ import annotations

import re

from plugin_api import DeliveryItem


_TITLE_RE = re.compile(
    r"(?im)^#\s+(.+?)\s+[—–-]\s+(?:user\s+stories|使用者故事)\s*$",
)
_EPIC_RE = re.compile(r"(?m)^##\s+Epic\s+(\d+)\s*[:：]\s*(.+?)\s*$", re.IGNORECASE)
_STORY_RE = re.compile(r"(?m)^###\s+Story\s+(\d+\.\d+)\s+[—–-]\s+(.+?)\s*$", re.IGNORECASE)
_REQUIREMENT_RE = re.compile(r"(?i)(FR-\d+|NFR-\d+|OPS-\d+|AC-\d+)")
_ESTIMATE_RE = re.compile(
    r"\*\*Senior\s+RD\s+Estimate\*\*\s*\n?\s*-?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_REQ_LINE_RE = re.compile(
    r"\*\*Requirement\s+IDs?\*\*\s*[:：]?\s*([^\n]+)",
    re.IGNORECASE,
)


def parse_stories_to_delivery_items(
    artifact: str,
    *,
    target_project: str = "",
) -> list[DeliveryItem]:
    """把 stories artifact 解析成 DeliveryItem[]。

    Parameters
    ----------
    artifact : str
        stories stage 的 markdown 全文。
    target_project : str
        該 thread 對應的 tracker target（如 GitHub repo "owner/repo"），
        塞進 DeliveryItem.target_project。caller 可不填，在 publish 時再覆寫。
    """
    items: list[DeliveryItem] = []

    # 先找出所有 epic 起點（line offset），切 stories 進 epic
    epic_matches = list(_EPIC_RE.finditer(artifact))
    story_matches = list(_STORY_RE.finditer(artifact))
    if not story_matches:
        return items

    # 為每個 story 找它屬於哪個 epic（最近一個 epic.start < story.start）
    def epic_for(story_start: int) -> tuple[str, str]:
        """回 (epic_num, epic_title)；找不到 → ('', '')"""
        match: re.Match[str] | None = None
        for m in epic_matches:
            if m.start() < story_start:
                match = m
            else:
                break
        if match is None:
            return ("", "")
        return (match.group(1), match.group(2).strip())

    for i, sm in enumerate(story_matches):
        num = sm.group(1)
        title = sm.group(2).strip()
        # body 從 story heading 到下一個 ### Story 或 ## Epic 之前
        next_start = story_matches[i + 1].start() if i + 1 < len(story_matches) else len(artifact)
        # 也要切到下一個 ## Epic 之前
        for em in epic_matches:
            if em.start() > sm.start() and em.start() < next_start:
                next_start = em.start()
        body = artifact[sm.start():next_start].rstrip()

        epic_num, epic_title = epic_for(sm.start())
        group = f"Epic {epic_num}: {epic_title}" if epic_num else "(no epic)"

        # estimate（小時）
        est_match = _ESTIMATE_RE.search(body)
        senior_rd_hours = float(est_match.group(1)) if est_match else 0.0
        senior_rd_days = round(senior_rd_hours / 8.0, 2)
        tracker_estimate = max(1, round(senior_rd_hours))  # 至少 1，tracker 整數

        # requirement IDs
        req_refs: list[str] = []
        req_line = _REQ_LINE_RE.search(body)
        if req_line:
            for m in _REQUIREMENT_RE.finditer(req_line.group(1)):
                code = m.group(1).upper()
                if code not in req_refs and not code.startswith("AC-"):
                    req_refs.append(code)

        # labels：固定加 "story" + "epic-N" + 每個 req prefix（fr / nfr / ops）
        labels = ["story"]
        if epic_num:
            labels.append(f"epic-{epic_num}")
        for r in req_refs:
            prefix = r.split("-")[0].lower()
            if prefix not in labels:
                labels.append(prefix)

        items.append(DeliveryItem(
            title=f"Story {num} — {title}",
            body=body,
            estimate=tracker_estimate,
            group=group,
            labels=labels,
            target_project=target_project,
            senior_rd_days=senior_rd_days,
            requirement_refs=req_refs,
            requirement_source="parsed" if req_refs else "unmapped",
        ))

    return items


def stories_doc_title(artifact: str) -> str:
    """抽 `# <name> — User Stories` 的 name；找不到回 'User Stories'。"""
    m = _TITLE_RE.search(artifact)
    return m.group(1).strip() if m else "User Stories"
