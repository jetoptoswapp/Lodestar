"""Stories stage（M2）：generate / refine / chat handlers + warn-only structural validator。

對應 spec 附錄 D：
  generate → user_stories.md（含 Epic / Story heading shape + tier propagation）
  refine   → user_stories_refine.md
  chat     → stories_chat.md（[CONTENT_START]/[CONTENT_END] 包整份更新後 artifact）

雙詞彙：id="stories" / telemetry_stage="deliver"。
依賴：depends_on=("architecture",)；handler 透過 upstream_artifacts 取 prd / architecture。

Structural validator 警告 heading shape 偏差（spec 附錄 D HARD RULE：parser 依賴
`## Epic N:` / `### Story N.M —` 否則 publish pipeline 零產出）。
"""
from __future__ import annotations

import re

from plugin_api import (
    HarnessValidationOutcome,
    SEVERITY_WARN,
    StageChatResult,
    StageContext,
    StageResult,
    StageSpec,
)
from plugin_api.harness import HarnessContext

from ._shared import (
    extract_content_block,
    format_attachments,
    format_conversation,
    format_focus_section,
)


# ============================================================
#  Validator —— structural sanity（warn-only；spec §11）
# ============================================================
# # <title> — User Stories  /  使用者故事
_TITLE_LINE = re.compile(
    r"(?im)^#\s+.+\s+[—–-]\s+(user\s+stories|使用者故事)\s*$"
)
# `## Epic N: <title>` —— H2、Epic、阿拉伯數字、冒號
_EPIC_HEADING = re.compile(r"(?m)^##\s+Epic\s+\d+\s*[:：]")
# `### Story N.M — <title>` —— H3、Story、N.M、em-dash
_STORY_HEADING = re.compile(r"(?m)^###\s+Story\s+\d+\.\d+\s+[—–-]")
# Acceptance Criteria（英中）
_AC_HEADING = re.compile(
    r"(?im)(\*\*Acceptance\s+Criteria\*\*|"
    r"^#+\s*Acceptance\s+Criteria|"
    r"\*\*驗收條件\*\*|^#+\s*驗收條件)"
)
# Senior RD Estimate
_ESTIMATE = re.compile(r"(?i)Senior\s+RD\s+Estimate")
# 反模式：`**Story 1.1**`（bold 非 heading）/ `#### Story` / `### Epic` 等
_BAD_STORY_BOLD = re.compile(r"(?m)^\s*\*\*Story\s+\d+\.\d+")
_BAD_STORY_H4 = re.compile(r"(?m)^####\s+Story\s+\d+\.\d+")
_BAD_EPIC_H3 = re.compile(r"(?m)^###\s+Epic\s+\d+\s*[:：]")


def _stories_structural_validator(
    artifact: str, _ctx: HarnessContext,
) -> list[HarnessValidationOutcome]:
    """檢查 Stories 文件 heading shape / AC / Estimate。warn-only。"""
    outcomes: list[HarnessValidationOutcome] = []

    if not _TITLE_LINE.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="stories.has_title", severity=SEVERITY_WARN,
            message="缺少文件標題（# <name> — User Stories / 使用者故事）",
            fix_hint="第一行加上「# <專案名> — User Stories」或中文「# <專案名> — 使用者故事」",
        ))
    if not _EPIC_HEADING.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="stories.has_epic", severity=SEVERITY_WARN,
            message="缺少 `## Epic N: <title>` 章節（parser 必需）",
            fix_hint="把分組 heading 改成「## Epic 1: <主題>」這個 shape（H2、阿拉伯數字、冒號）",
        ))
    if not _STORY_HEADING.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="stories.has_story", severity=SEVERITY_WARN,
            message="缺少 `### Story N.M — <title>` 故事 heading（parser 必需）",
            fix_hint="把每個故事 heading 改成「### Story 1.1 — <標題>」（H3、N.M、em-dash 分隔）",
        ))
    if not _AC_HEADING.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="stories.has_acceptance_criteria", severity=SEVERITY_WARN,
            message="缺少 Acceptance Criteria / 驗收條件 區段",
            fix_hint="每個故事補上「**Acceptance Criteria**」加 bullet 條列（推薦 Gherkin AC-N 格式）",
        ))
    if not _ESTIMATE.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="stories.has_estimate", severity=SEVERITY_WARN,
            message="缺少 Senior RD Estimate（時數）",
            fix_hint="每個故事補上「**Senior RD Estimate**」加 0.5–4 之間的整數/小數",
        ))
    if _BAD_STORY_BOLD.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="stories.bad_story_bold", severity=SEVERITY_WARN,
            message="偵測到 `**Story N.M**` bold 段落 —— parser 看不到",
            fix_hint="把 `**Story 1.1**` 改成 `### Story 1.1 — <標題>`（H3 + em-dash）",
        ))
    if _BAD_STORY_H4.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="stories.bad_story_h4", severity=SEVERITY_WARN,
            message="偵測到 `#### Story N.M`（H4）—— parser 期待 H3",
            fix_hint="把 `#### Story 1.1` 降一層為 `### Story 1.1`",
        ))
    if _BAD_EPIC_H3.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="stories.bad_epic_h3", severity=SEVERITY_WARN,
            message="偵測到 `### Epic N:`（H3）—— parser 期待 H2",
            fix_hint="把 `### Epic 1:` 升一層為 `## Epic 1:`",
        ))
    return outcomes


# ============================================================
#  Helpers
# ============================================================
def _upstream(ctx: StageContext) -> tuple[str, str]:
    """回 (prd, architecture)；缺則回空字串（engine 已經在 dispatch 前擋過）。"""
    return (
        ctx.upstream_artifacts.get("prd", ""),
        ctx.upstream_artifacts.get("architecture", ""),
    )


# ============================================================
#  Handlers
# ============================================================
def _stories_generate(ctx: StageContext, run) -> StageResult:
    """stories generate：PRD + Architecture → user_stories.md → invoke。"""
    prd, arch = _upstream(ctx)
    prompt = run.render_prompt("user_stories.md", {
        "PRD_DRAFT": prd,
        "ARCHITECTURE_DRAFT": arch,
    })
    result = run.harnessed_step(
        telemetry_stage="deliver", operation="generate_user_stories",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    return StageResult(
        artifact=result.raw_output.strip(),
        telemetry_metadata={"run_id": result.run_id},
    )


def _stories_refine(ctx: StageContext, run) -> StageResult:
    """stories refine：PRD + Architecture + 現有 stories + instruction → 完整更新版。"""
    prd, arch = _upstream(ctx)
    prompt = run.render_prompt("user_stories_refine.md", {
        "PRD_DRAFT": prd,
        "ARCHITECTURE_DRAFT": arch,
        "USER_STORIES_DRAFT": ctx.current_artifact or "(empty)",
        "INSTRUCTION": ctx.instruction or "",
        "ATTACHMENTS": format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="deliver", operation="refine_user_stories",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    return StageResult(
        artifact=result.raw_output.strip(),
        telemetry_metadata={"run_id": result.run_id},
    )


def _stories_chat(ctx: StageContext, run) -> StageChatResult:
    """stories chat：含三 artifact + 對話歷史。[CONTENT_START]/[CONTENT_END] 是更新訊號。"""
    prd, arch = _upstream(ctx)
    prompt = run.render_prompt("stories_chat.md", {
        "PRD_DRAFT": prd,
        "ARCHITECTURE_DRAFT": arch,
        "USER_STORIES_DRAFT": ctx.current_artifact or "(empty)",
        "CONVERSATION_TEXT": format_conversation(ctx.conversation, ai_label="PM"),
        "FOCUS_SECTION": format_focus_section(ctx.focus_section),
        "ATTACHMENTS": format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="deliver", operation="chat_user_stories",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    reply, updated = extract_content_block(result.raw_output)
    return StageChatResult(reply=reply, updated_artifact=updated)


# ============================================================
#  StageSpec + Validators registry
# ============================================================
STORIES_STAGE = StageSpec(
    id="stories",
    label="使用者故事",
    icon="list",
    telemetry_stage="deliver",
    generate_operation="generate_user_stories",
    refine_operation="refine_user_stories",
    chat_operation="chat_user_stories",
    depends_on=("architecture",),
    artifact_key="stories",
    prompt_keys=("user_stories.md", "user_stories_refine.md", "stories_chat.md"),
    default_agent_role="pm",
    generate=_stories_generate,
    refine=_stories_refine,
    chat=_stories_chat,
    supports_chat=True,
    on_complete_state_extra={},
)


VALIDATORS = [
    ("deliver", "generate_user_stories", _stories_structural_validator),
    ("deliver", "refine_user_stories",   _stories_structural_validator),
]
