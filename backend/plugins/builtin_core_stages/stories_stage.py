"""Stories stage（M2）：generate / refine / chat handlers + warn-only structural validator。

對應 spec 附錄 D：
  generate → user_stories.md（含 Epic / Story heading shape + tier propagation）
  refine   → user_stories_refine.md
  chat     → stories_chat.md（[CONTENT_START]/[CONTENT_END] 包整份更新後 artifact）

雙詞彙：id="stories" / telemetry_stage="deliver"。
依賴：depends_on=("architecture", "ui_design")；handler 透過 upstream_artifacts 取
prd / architecture / ui_design（UI 設計稿 strip 掉 HTML 原型後當 brief 餵入）。

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
    collab_discussion_prefix,
    effective_persona,
    extract_content_block,
    format_attachments,
    format_conversation,
    format_focus_section,
    render_skills_block,
    strip_html_prototypes,
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

# UI 設計畫面 heading（`## Screen: <name>`）—— coverage 檢查用
_UI_SCREEN_HEADING = re.compile(r"(?im)^##\s+Screen\s*[:：]\s*(.+?)\s*$")

# Vertical story checks
_EPIC_BOUNDARY = re.compile(r"(?m)^(##\s+Epic\s+\d+\s*[:：].+)$")
_STORY_TITLE = re.compile(r"(?m)^###\s+Story\s+\d+\.\d+\s+[—–-]\s+(.+)$")
_LAUNCH_CMD = re.compile(
    r"(?i)(cargo\s+run|python\s+-m|\.\/gradlew|npm\s+(start|run)\b|go\s+run|"
    r"dotnet\s+run|--headless|HeadlessRunner)"
)


def _check_vertical_stories(artifact: str) -> list[HarnessValidationOutcome]:
    """每個 Epic 必須有一個不帶 [prereq] 的 vertical story，且其 AC 含啟動指令。warn-only。"""
    outcomes: list[HarnessValidationOutcome] = []
    positions = [(m.start(), m.group(1)) for m in _EPIC_BOUNDARY.finditer(artifact)]
    if not positions:
        return outcomes

    sections = [
        (title.strip(), artifact[pos: positions[i + 1][0] if i + 1 < len(positions) else len(artifact)])
        for i, (pos, title) in enumerate(positions)
    ]

    for epic_title, content in sections:
        stories = _STORY_TITLE.findall(content)
        if not stories:
            continue
        epic_num_m = re.search(r"Epic\s+(\d+)", epic_title)
        num = epic_num_m.group(1) if epic_num_m else "?"

        has_vertical = any(not s.strip().lower().startswith("[prereq]") for s in stories)
        if not has_vertical:
            outcomes.append(HarnessValidationOutcome(
                validator="stories.missing_vertical", severity=SEVERITY_WARN,
                message=f"Epic {num} 所有 story 都帶 `[prereq]`，缺少 vertical story — 功能不會被接進 runtime",
                fix_hint=f"在 Epic {num} 最後加一個不帶 `[prereq]` 的 story，AC 含具體啟動指令（如 `cargo run --bin X`）",
            ))
        elif not _LAUNCH_CMD.search(content):
            outcomes.append(HarnessValidationOutcome(
                validator="stories.vertical_no_launch_cmd", severity=SEVERITY_WARN,
                message=f"Epic {num} 有 vertical story 但 AC 缺少具體啟動指令 — agent 可能用 unit test 繞過 runtime",
                fix_hint=f"在 Epic {num} 的 vertical story AC 加上啟動指令（如 `cargo run --bin X`、`python -m Y`、`./gradlew connectedAndroidTest`）",
            ))
    return outcomes


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
    outcomes.extend(_check_vertical_stories(artifact))
    return outcomes


def _extract_screen_names(ui_brief: str) -> list[str]:
    """從 UI 設計 brief 抽出 `## Screen: <name>` 的畫面名清單。"""
    return [m.group(1).strip() for m in _UI_SCREEN_HEADING.finditer(ui_brief or "")]


def _stories_coverage_validator(
    artifact: str, ctx: HarnessContext,
) -> list[HarnessValidationOutcome]:
    """前端覆蓋檢查：UI 設計的每個畫面都該有對應 story。warn-only。

    畫面清單由 handler 透過 metadata['ui_screens'] 傳入（validator 拿不到 upstream）。
    無畫面（headless / 未做 UI 設計）→ 不檢查。這是「設計了 UI 卻只生後端」的最後防線。"""
    outcomes: list[HarnessValidationOutcome] = []
    screens = ctx.metadata.get("ui_screens") or []
    if not screens:
        return outcomes
    low = artifact.lower()
    uncovered = [s for s in screens if s.lower() not in low]
    if not uncovered:
        return outcomes
    if len(uncovered) == len(screens):
        outcomes.append(HarnessValidationOutcome(
            validator="stories.frontend_uncovered", severity=SEVERITY_WARN,
            message=(f"UI 設計有 {len(screens)} 個畫面，但 stories 一個都沒對應 —— "
                     f"前端會被整個漏掉（「設計了 UI 卻只生後端」的災情）"),
            fix_hint=("為每個 `## Screen: <name>` 至少建一個 story，body 寫 "
                      "`**Reference**: UI Design — Screen: <name>`；並加一個前端 Epic（含 scaffold + vertical story）"),
            detail={"screens": screens},
        ))
    else:
        outcomes.append(HarnessValidationOutcome(
            validator="stories.screens_uncovered", severity=SEVERITY_WARN,
            message=f"UI 設計有 {len(uncovered)} 個畫面沒有對應 story：{', '.join(uncovered)}",
            fix_hint=("為這些畫面各補一個 story，body 寫 "
                      "`**Reference**: UI Design — Screen: <name>` 並含視覺一致性 AC"),
            detail={"uncovered": uncovered},
        ))
    return outcomes


# ============================================================
#  Helpers
# ============================================================
def _upstream(ctx: StageContext) -> tuple[str, str, str]:
    """回 (prd, architecture, ui_design_brief)；缺則回空字串（engine 已在 dispatch 前擋過）。

    ui_design 取出後先 strip HTML 原型（只留理念 / tokens / Screen 名稱與描述）。"""
    return (
        ctx.upstream_artifacts.get("prd", ""),
        ctx.upstream_artifacts.get("architecture", ""),
        strip_html_prototypes(ctx.upstream_artifacts.get("ui_design", "")),
    )


# stage 內建 persona（agent.system_prompt 未設時的 default）；機器契約（Epic/Story heading shape
# 等 parser 依賴）留在 user_stories.md / stories_chat.md。R1：逐字搬自重構前的開頭人設段。
_DEFAULT_PM_PERSONA = (
    "You are a Senior Product Manager and Agile Coach. Based on the following PRD and "
    "System Architecture, produce a complete set of User Stories organized by Epic."
)
_DEFAULT_PM_CHAT_PERSONA = (
    "You are a Senior Product Manager in a discussion about the user stories backlog "
    "for a software project."
)


# ============================================================
#  Handlers
# ============================================================
def _stories_generate(ctx: StageContext, run) -> StageResult:
    """stories generate：PRD + Architecture + UI 設計稿 → user_stories.md → invoke。"""
    prd, arch, ui = _upstream(ctx)
    prompt = run.render_prompt("user_stories.md", {
        "PERSONA": effective_persona(ctx, _DEFAULT_PM_PERSONA),
        "SKILLS": render_skills_block(ctx.agent.skills if ctx.agent else ()),
        "PRD_DRAFT": prd,
        "ARCHITECTURE_DRAFT": arch,
        "UI_DESIGN_BRIEF": ui or "(not provided)",
    })
    prompt = collab_discussion_prefix(ctx.conversation) + prompt  # collab：注入多方討論（單模式 no-op）
    result = run.harnessed_step(
        telemetry_stage="deliver", operation="generate_user_stories",
        prompt=prompt,
        metadata={"thread_id": ctx.thread_id, "ui_screens": _extract_screen_names(ui)},
        max_iterations=1,
    )
    return StageResult(
        artifact=result.raw_output.strip(),
        telemetry_metadata={"run_id": result.run_id},
    )


def _stories_refine(ctx: StageContext, run) -> StageResult:
    """stories refine：PRD + Architecture + UI 設計稿 + 現有 stories + instruction → 完整更新版。"""
    prd, arch, ui = _upstream(ctx)
    prompt = run.render_prompt("user_stories_refine.md", {
        "PRD_DRAFT": prd,
        "ARCHITECTURE_DRAFT": arch,
        "UI_DESIGN_BRIEF": ui or "(not provided)",
        "USER_STORIES_DRAFT": ctx.current_artifact or "(empty)",
        "INSTRUCTION": ctx.instruction or "",
        "ATTACHMENTS": format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="deliver", operation="refine_user_stories",
        prompt=prompt,
        metadata={"thread_id": ctx.thread_id, "ui_screens": _extract_screen_names(ui)},
        max_iterations=1,
    )
    return StageResult(
        artifact=result.raw_output.strip(),
        telemetry_metadata={"run_id": result.run_id},
    )


def _stories_chat(ctx: StageContext, run) -> StageChatResult:
    """stories chat：含上游 artifacts + 對話歷史。[CONTENT_START]/[CONTENT_END] 是更新訊號。"""
    prd, arch, ui = _upstream(ctx)
    prompt = run.render_prompt("stories_chat.md", {
        "PERSONA": effective_persona(ctx, _DEFAULT_PM_CHAT_PERSONA),
        "SKILLS": render_skills_block(ctx.agent.skills if ctx.agent else ()),
        "PRD_DRAFT": prd,
        "ARCHITECTURE_DRAFT": arch,
        "UI_DESIGN_BRIEF": ui or "(not provided)",
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
    description="把架構拆成可交付、可估時的使用者故事（Epic／Story）。",
    icon="list",
    telemetry_stage="deliver",
    generate_operation="generate_user_stories",
    refine_operation="refine_user_stories",
    chat_operation="chat_user_stories",
    depends_on=("architecture", "ui_design"),
    artifact_key="stories",
    prompt_keys=("user_stories.md", "user_stories_refine.md", "stories_chat.md"),
    default_agent_role="stories",
    generate=_stories_generate,
    refine=_stories_refine,
    chat=_stories_chat,
    supports_chat=True,
    on_complete_state_extra={},
)


VALIDATORS = [
    ("deliver", "generate_user_stories", _stories_structural_validator),
    ("deliver", "refine_user_stories",   _stories_structural_validator),
    # 前端覆蓋（UI 設計畫面 → story）：warn-only，畫面清單由 handler 經 metadata 傳入
    ("deliver", "generate_user_stories", _stories_coverage_validator),
    ("deliver", "refine_user_stories",   _stories_coverage_validator),
]
