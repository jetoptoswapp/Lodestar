"""change_request stage（修改既有專案）：StageSpec + generate / refine / chat handlers。

孿生自 prd_stage，差別：
  - requires=("workspace",)：host 在 dispatch 時 clone 既有 repo，ctx.workspace_dir 帶 clone 路徑。
  - prompt 注入 workspace block（_shared.format_workspace），指示 model 先 Read/Grep/Glob 既有 codebase。
  - 產出「Implementation Brief」（要改的檔/區域、變更項、驗收）而非 PRD；sentinel `[BRIEF_READY]`。
  - 無 structural / judge validator（brief 結構較自由；warn-only PRD 那套不適用）。

此 stage 的 artifact 直接當 single implement 的 `story`：談完 → implement 開一個 PR。
"""
from __future__ import annotations

from typing import Tuple

from plugin_api import (
    StageChatResult,
    StageContext,
    StageResult,
    StageSpec,
)

from ._shared import (
    effective_persona,
    format_attachments as _format_attachments,
    format_conversation as _shared_format_conversation,
    format_focus_section,
    format_workspace,
    render_skills_block,
)


# stage 內建 persona（agent.system_prompt 未設時的 default）。
_DEFAULT_PLANNER_PERSONA = (
    "You are a senior software engineer doing codebase archaeology on an EXISTING project.\n"
    "\n"
    "Your job is to turn a change request or bug report into a concrete, code-grounded Implementation Brief: "
    "read the actual repository, locate exactly where the change lands, and specify what to change and how to "
    "verify it — so an implementation agent can execute it and open one pull request."
)

_BRIEF_READY_SENTINEL = "[BRIEF_READY]"


def _strip_sentinel(text: str) -> Tuple[str, bool]:
    """從 model 輸出移除 [BRIEF_READY]，回 (清洗後內容, 是否標記完成)。"""
    if _BRIEF_READY_SENTINEL in text:
        return text.replace(_BRIEF_READY_SENTINEL, "").rstrip(), True
    return text, False


def _format_conversation(conv: tuple) -> str:
    return _shared_format_conversation(conv, ai_label="Engineer")


def _system_prompt(ctx: StageContext, run) -> str:
    return run.render_prompt("change_request_system.md", {
        "PERSONA": effective_persona(ctx, _DEFAULT_PLANNER_PERSONA),
        "SKILLS": render_skills_block(ctx.agent.skills if ctx.agent else ()),
    })


# ============================================================
#  Handlers
# ============================================================
def _change_request_generate(ctx: StageContext, run) -> StageResult:
    """generate：讀既有 codebase + 對話 → 產出 Implementation Brief。"""
    prompt = run.render_prompt("change_request_chat.md", {
        "SYSTEM_PROMPT": _system_prompt(ctx, run),
        "WORKSPACE": format_workspace(ctx.workspace_dir),
        "CONVERSATION_TEXT": _format_conversation(ctx.conversation),
        "FOCUS_SECTION": format_focus_section(ctx.focus_section),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="specify", operation="generate_change_request",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
    )
    artifact, ready = _strip_sentinel(result.raw_output.strip())
    return StageResult(
        artifact=artifact,
        telemetry_metadata={"run_id": result.run_id, "brief_ready": ready},
        state_extra={"brief_ready": True} if ready else {},
    )


def _change_request_refine(ctx: StageContext, run) -> StageResult:
    """refine：基於現有 brief + instruction，重讀相關 code 後輸出完整更新版。"""
    prompt = run.render_prompt("change_request_refine.md", {
        "WORKSPACE": format_workspace(ctx.workspace_dir),
        "BRIEF_DRAFT": ctx.current_artifact or "(empty)",
        "INSTRUCTION": ctx.instruction or "",
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="specify", operation="refine_change_request",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
    )
    artifact, ready = _strip_sentinel(result.raw_output.strip())
    return StageResult(
        artifact=artifact,
        telemetry_metadata={"run_id": result.run_id, "brief_ready": ready},
        state_extra={"brief_ready": True} if ready else {},
    )


def _change_request_chat(ctx: StageContext, run) -> StageChatResult:
    """chat：讀碼討論釐清。若 brief 已存在則前綴 amendment_prefix。
    Sentinel `[BRIEF_READY]` 出現 → 視為 brief 完成、回 updated_artifact。"""
    sys_prompt = _system_prompt(ctx, run)
    if ctx.current_artifact and ctx.current_artifact.strip():
        amendment = run.render_prompt("change_request_amendment_prefix.md", {
            "CURRENT_BRIEF": ctx.current_artifact,
        })
        sys_prompt = sys_prompt + "\n\n" + amendment

    prompt = run.render_prompt("change_request_chat.md", {
        "SYSTEM_PROMPT": sys_prompt,
        "WORKSPACE": format_workspace(ctx.workspace_dir),
        "CONVERSATION_TEXT": _format_conversation(ctx.conversation),
        "FOCUS_SECTION": format_focus_section(ctx.focus_section),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="specify", operation="chat_change_request",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    reply, ready = _strip_sentinel(result.raw_output.strip())
    return StageChatResult(
        reply=reply,
        updated_artifact=reply if ready else None,
    )


# ============================================================
#  StageSpec
# ============================================================
CHANGE_REQUEST_STAGE = StageSpec(
    id="change_request",
    label="變更需求",
    description="讀既有 codebase，與你談要改什麼/解哪個 bug，產出具體實作 brief（供 implement 開 PR）。",
    icon="document",
    telemetry_stage="specify",
    generate_operation="generate_change_request",
    refine_operation="refine_change_request",
    chat_operation="chat_change_request",
    depends_on=(),
    requires=("workspace",),     # host 在 dispatch 時 clone 既有 repo → ctx.workspace_dir
    artifact_key="change_request",
    prompt_keys=(
        "change_request_system.md", "change_request_chat.md",
        "change_request_refine.md", "change_request_amendment_prefix.md",
    ),
    default_agent_role="change_request",
    generate=_change_request_generate,
    refine=_change_request_refine,
    chat=_change_request_chat,
    supports_chat=True,
    on_complete_state_extra={},
)


VALIDATORS: list = []   # brief 結構自由，不跑 structural / judge validator
