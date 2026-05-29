"""example_notes register —— 註冊一個 notes stage（generate / chat）。

對照 builtin_core_stages：同一套 plugin_api，handler 走 run.harnessed_step。
notes stage depends_on=()（無上游），所以任何 thread 都能直接生成。
"""
from __future__ import annotations

from plugin_api import (
    PluginHost,
    StageChatResult,
    StageContext,
    StageResult,
    StageSpec,
)


def _format_conversation(conv: tuple) -> str:
    if not conv:
        return "(no notes yet)"
    lines = []
    for role, content in conv:
        speaker = "User" if role == "user" else "Organizer"
        lines.append(f"{speaker}:\n{content}")
    return "\n\n".join(lines)


def _format_attachments(attachments: list) -> str:
    if not attachments:
        return "(no attached files)"
    if all(a.get("abs_path") for a in attachments):
        lines = ["READ these files with the Read tool before organizing:"]
        for a in attachments:
            lines.append(f"- {a['abs_path']}  ·  {a.get('filename', '?')}")
        return "\n".join(lines)
    # fallback：inline parsed_text
    return "\n\n".join(
        f"<<< {a.get('filename', '?')} >>>\n{a.get('parsed_text') or '(未解析)'}"
        for a in attachments
    )


def _notes_generate(ctx: StageContext, run) -> StageResult:
    prompt = run.render_prompt("notes.md", {
        "CONVERSATION_TEXT": _format_conversation(ctx.conversation),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
        "FOCUS_SECTION": f"\n[Focus: {ctx.focus_section}]\n" if ctx.focus_section else "",
    })
    result = run.harnessed_step(
        telemetry_stage="notes", operation="generate_notes",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    return StageResult(artifact=result.raw_output.strip(),
                       telemetry_metadata={"run_id": result.run_id})


def _notes_chat(ctx: StageContext, run) -> StageChatResult:
    prompt = run.render_prompt("notes.md", {
        "CONVERSATION_TEXT": _format_conversation(ctx.conversation),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
        "FOCUS_SECTION": f"\n[Focus: {ctx.focus_section}]\n" if ctx.focus_section else "",
    })
    result = run.harnessed_step(
        telemetry_stage="notes", operation="chat_notes",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    # 簡化：每次 chat 回的都當更新後的整理結果
    return StageChatResult(reply=result.raw_output.strip(),
                           updated_artifact=result.raw_output.strip())


NOTES_STAGE = StageSpec(
    id="notes",
    label="筆記整理",
    icon="note",
    telemetry_stage="notes",
    generate_operation="generate_notes",
    chat_operation="chat_notes",
    depends_on=(),
    artifact_key="notes",
    prompt_keys=("notes.md",),
    default_agent_role="notes",
    generate=_notes_generate,
    chat=_notes_chat,
    supports_chat=True,
    on_complete_state_extra={},
)


def register(host: PluginHost) -> None:
    host.register_stage(NOTES_STAGE)
    # 無 validator —— 自由筆記不做結構檢查（示範「validator 可選」）
