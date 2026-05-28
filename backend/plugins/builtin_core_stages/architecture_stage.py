"""Architecture stage（M2）：generate / refine / chat handlers + warn-only validator。

對應 spec 附錄 D：
  generate → architect.md（含 tier classification + Step 2 architecture doc）
  refine   → architecture_refine.md（PRD + ARCHITECTURE_DRAFT + INSTRUCTION）
  chat     → arch_chat.md（[CONTENT_START]/[CONTENT_END] 包整份更新後 artifact）

雙詞彙：id="architecture" / telemetry_stage="design"。
依賴：depends_on=("prd",) —— 上游 PRD 缺則 engine 直接 4xx。
"""
from __future__ import annotations

import re
from typing import Tuple

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
# 第一行 tier 宣告（HARD RULE in prompts/architect.md）
_TIER_LINE = re.compile(r"^\s*\*\*Project tier\*\*:\s*T\d+\s*[—–-]", re.MULTILINE)
# Mermaid code fence
_MERMAID_FENCE = re.compile(r"```\s*mermaid\b", re.IGNORECASE)
# Module / package layout 章節（中英）
_MODULE_HEADING = re.compile(
    r"(?im)^#+\s*(module|package|模組|套件|模塊).{0,20}(layout|架構|結構)?",
)


def _architecture_structural_validator(
    artifact: str, _ctx: HarnessContext,
) -> list[HarnessValidationOutcome]:
    """檢查架構文件含 tier 行 / Mermaid / module layout。warn-only。"""
    outcomes: list[HarnessValidationOutcome] = []
    if not _TIER_LINE.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="architecture.has_tier_line", severity=SEVERITY_WARN,
            message="缺少第一行 tier 宣告（**Project tier**: T<N> — …）",
            fix_hint="在文件第一行加上「**Project tier**: T0/T1/T2 — <一句根據 PRD 的判斷>」",
        ))
    if not _MERMAID_FENCE.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="architecture.has_mermaid", severity=SEVERITY_WARN,
            message="缺少 Mermaid 架構圖（```mermaid code fence）",
            fix_hint="補上至少一張 Mermaid 圖，用 ```mermaid 包起來，呈現主要元件與依賴方向",
        ))
    if not _MODULE_HEADING.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="architecture.has_module_layout", severity=SEVERITY_WARN,
            message="缺少 Module / Package layout 章節",
            fix_hint="新增「## Module / Package Layout」章節，給出具體目錄樹（依 tier 預設）",
        ))
    return outcomes


# ============================================================
#  Helpers
# ============================================================
def _upstream_prd(ctx: StageContext) -> str:
    return ctx.upstream_artifacts.get("prd", "")


# ============================================================
#  Handlers
# ============================================================
def _arch_generate(ctx: StageContext, run) -> StageResult:
    """architecture generate：PRD → architect.md → invoke。"""
    prompt = run.render_prompt("architect.md", {
        "PRD_DRAFT": _upstream_prd(ctx),
    })
    result = run.harnessed_step(
        telemetry_stage="design", operation="generate_architecture",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    return StageResult(
        artifact=result.raw_output.strip(),
        telemetry_metadata={"run_id": result.run_id},
    )


def _arch_refine(ctx: StageContext, run) -> StageResult:
    """architecture refine：PRD + 現有 architecture + instruction → 完整更新版。"""
    prompt = run.render_prompt("architecture_refine.md", {
        "PRD_DRAFT": _upstream_prd(ctx),
        "ARCHITECTURE_DRAFT": ctx.current_artifact or "(empty)",
        "INSTRUCTION": ctx.instruction or "",
        "ATTACHMENTS": format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="design", operation="refine_architecture",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    return StageResult(
        artifact=result.raw_output.strip(),
        telemetry_metadata={"run_id": result.run_id},
    )


def _arch_chat(ctx: StageContext, run) -> StageChatResult:
    """architecture chat：含 PRD + 現有 architecture + 對話歷史。

    Sentinel `[CONTENT_START]...[CONTENT_END]` 出現 → block 內容視為新 artifact，
    否則純對話、artifact 不更新。
    """
    prompt = run.render_prompt("arch_chat.md", {
        "PRD_DRAFT": _upstream_prd(ctx),
        "ARCHITECTURE_DRAFT": ctx.current_artifact or "(empty)",
        "CONVERSATION_TEXT": format_conversation(ctx.conversation, ai_label="Architect"),
        "FOCUS_SECTION": format_focus_section(ctx.focus_section),
        "ATTACHMENTS": format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="design", operation="chat_architecture",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    reply, updated = extract_content_block(result.raw_output)
    return StageChatResult(reply=reply, updated_artifact=updated)


# ============================================================
#  StageSpec + Validators registry
# ============================================================
ARCHITECTURE_STAGE = StageSpec(
    id="architecture",
    label="架構",
    icon="diagram",
    telemetry_stage="design",
    generate_operation="generate_architecture",
    refine_operation="refine_architecture",
    chat_operation="chat_architecture",
    depends_on=("prd",),
    artifact_key="architecture",
    prompt_keys=("architect.md", "architecture_refine.md", "arch_chat.md"),
    default_agent_role="architect",
    generate=_arch_generate,
    refine=_arch_refine,
    chat=_arch_chat,
    supports_chat=True,
    on_complete_state_extra={},
)


# (telemetry_stage, operation, fn) — host.register_validator 用
VALIDATORS = [
    ("design", "generate_architecture", _architecture_structural_validator),
    ("design", "refine_architecture",   _architecture_structural_validator),
    # chat 不跑 structural（回的可能只是討論，未必更新 artifact）
]
