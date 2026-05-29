"""rca_intake stage：把工程師回報的異常整理成結構化 intake brief。

- generate：依對話 + 附件產出簡潔的異常摘要（不推測根因）。
- chat    ：discovery 問答；資訊足夠時用 [CONTENT_START]..[CONTENT_END] 更新 brief。

persona 在 prompts/intake*.md；AgentSpec 只放短描述（沿用 builtin 慣例）。
"""
from __future__ import annotations

from plugin_api import StageChatResult, StageContext, StageResult, StageSpec

from ._shared import (
    extract_content_block,
    format_attachments as _format_attachments,
    format_conversation as _format_conversation,
)


def _intake_generate(ctx: StageContext, run) -> StageResult:
    prompt = run.render_prompt("intake.md", {
        "CONVERSATION": _format_conversation(ctx.conversation),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="rca_intake", operation="generate_rca_intake",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    return StageResult(
        artifact=result.raw_output.strip(),
        telemetry_metadata={"run_id": result.run_id},
    )


def _intake_chat(ctx: StageContext, run) -> StageChatResult:
    prompt = run.render_prompt("intake_chat.md", {
        "INTAKE": ctx.current_artifact or "(empty)",
        "CONVERSATION": _format_conversation(ctx.conversation),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="rca_intake", operation="chat_rca_intake",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    reply, updated = extract_content_block(result.raw_output.strip())
    return StageChatResult(reply=reply, updated_artifact=updated)


INTAKE_STAGE = StageSpec(
    id="rca_intake",
    label="Anomaly Intake",
    description="把回報的異常整理成結構化 intake brief（症狀／時間／範圍／可用資料），不推測根因。",
    icon="document",
    telemetry_stage="rca_intake",
    generate_operation="generate_rca_intake",
    chat_operation="chat_rca_intake",
    depends_on=(),
    artifact_key="rca_intake",
    prompt_keys=("intake.md", "intake_chat.md"),
    default_agent_role="rca_intake",
    generate=_intake_generate,
    chat=_intake_chat,
    supports_chat=True,
)
