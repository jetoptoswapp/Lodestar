"""PRD stage（M1）：StageSpec + generate / refine / chat handlers + warn-only validator。

對應 spec 附錄 D PRD prompts：
  generate / chat → sa_chat.md（含 SA system rules 與 PRD Format）
  refine          → prd_refine.md（{PRD_DRAFT} + {INSTRUCTION}）

Chat sentinel：sa_system 規範 model 在 PRD 完成時於結尾附 `[PRD_READY]`。
本檔在 _strip_sentinel 取出後，於 chat handler 將整段視為 updated_artifact，
generate / refine handler 則寫進 state_extra={"prd_ready": True}。
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


# ============================================================
#  Validator —— structural sanity（warn-only；spec §11）
# ============================================================
_HAS_OVERVIEW    = re.compile(r"(?im)^#+\s*\d*\.?\s*(overview|概述)")
_HAS_FR_SECTION  = re.compile(r"(?im)^#+\s*\d*\.?\s*(functional requirements|功能需求)")
_HAS_NFR_SECTION = re.compile(r"(?im)^#+\s*\d*\.?\s*(non-?functional requirements|非功能需求)")
_FR_ITEM_PAT  = re.compile(r"`?FR-\d+`?", re.IGNORECASE)
_NFR_ITEM_PAT = re.compile(r"`?NFR-\d+`?", re.IGNORECASE)


def _prd_structural_validator(artifact: str, _ctx: HarnessContext) -> list[HarnessValidationOutcome]:
    """檢查 PRD 是否含 Overview / FR / NFR 章節與 FR-X / NFR-X 編號。
    全部 warn-only（spec §11）；fix_hint 為祈使句、動詞開頭。
    """
    outcomes: list[HarnessValidationOutcome] = []
    if not _HAS_OVERVIEW.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="prd.has_overview", severity=SEVERITY_WARN,
            message="缺少 Overview / 概述 章節",
            fix_hint="補上 ## 1. Overview 章節，簡述產品目的與範圍",
        ))
    if not _HAS_FR_SECTION.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="prd.has_fr_section", severity=SEVERITY_WARN,
            message="缺少 Functional Requirements / 功能需求 章節",
            fix_hint="新增 ## 3. Functional Requirements 章節並以 FR-1 / FR-2 編號列出",
        ))
    if not _HAS_NFR_SECTION.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="prd.has_nfr_section", severity=SEVERITY_WARN,
            message="缺少 Non-Functional Requirements / 非功能需求 章節",
            fix_hint="新增 ## 4. Non-Functional Requirements 章節並以 NFR-X 編號列出",
        ))
    if not _FR_ITEM_PAT.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="prd.has_fr", severity=SEVERITY_WARN,
            message="缺少 FR-X 編號的功能需求",
            fix_hint="補上至少一條以 FR-1 起編的功能需求",
        ))
    if not _NFR_ITEM_PAT.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="prd.has_nfr", severity=SEVERITY_WARN,
            message="缺少 NFR-X 編號的非功能需求",
            fix_hint="補上至少一條 NFR-1（security / performance / scalability / availability / compliance 擇一）",
        ))
    return outcomes


# ============================================================
#  Helpers
# ============================================================
_PRD_READY_SENTINEL = "[PRD_READY]"


def _strip_sentinel(text: str) -> Tuple[str, bool]:
    """從 model 輸出移除 [PRD_READY]，回 (清洗後內容, 是否標記完成)。"""
    if _PRD_READY_SENTINEL in text:
        return text.replace(_PRD_READY_SENTINEL, "").rstrip(), True
    return text, False


def _format_conversation(conv: tuple) -> str:
    """把 ((role, content), ...) 轉成 prompt 內可讀的對話形式。"""
    if not conv:
        return "(no prior conversation)"
    lines: list[str] = []
    for role, content in conv:
        speaker = "User" if role == "user" else "SA"
        lines.append(f"{speaker}:\n{content}")
    return "\n\n".join(lines)


# ============================================================
#  Handlers
# ============================================================
def _prd_generate(ctx: StageContext, run) -> StageResult:
    """PRD generate：空白或基於對話 → 用 sa_chat 模板呼叫 model。"""
    sa_system = run.render_prompt("sa_system.md", {})
    conversation_text = _format_conversation(ctx.conversation)
    focus_block = f"\n[Focus on section: {ctx.focus_section}]\n" if ctx.focus_section else ""

    prompt = run.render_prompt("sa_chat.md", {
        "SYSTEM_PROMPT": sa_system,
        "CONVERSATION_TEXT": conversation_text,
        "FOCUS_SECTION": focus_block,
    })
    result = run.harnessed_step(
        telemetry_stage="specify", operation="generate_prd",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    artifact, ready = _strip_sentinel(result.raw_output.strip())
    return StageResult(
        artifact=artifact,
        telemetry_metadata={"run_id": result.run_id, "prd_ready": ready},
        state_extra={"prd_ready": True} if ready else {},
    )


def _prd_refine(ctx: StageContext, run) -> StageResult:
    """PRD refine：基於現有 PRD + user instruction，輸出完整更新版。"""
    prompt = run.render_prompt("prd_refine.md", {
        "PRD_DRAFT": ctx.current_artifact or "(empty)",
        "INSTRUCTION": ctx.instruction or "",
    })
    result = run.harnessed_step(
        telemetry_stage="specify", operation="refine_prd",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    artifact, ready = _strip_sentinel(result.raw_output.strip())
    return StageResult(
        artifact=artifact,
        telemetry_metadata={"run_id": result.run_id, "prd_ready": ready},
        state_extra={"prd_ready": True} if ready else {},
    )


def _prd_chat(ctx: StageContext, run) -> StageChatResult:
    """PRD chat：SA discovery。若 PRD 已存在則前綴 amendment_prefix。
    Sentinel `[PRD_READY]` 出現 → 視為 PRD 完成、回 updated_artifact。
    否則只回對話（reply），artifact 不更新。"""
    sa_system = run.render_prompt("sa_system.md", {})
    if ctx.current_artifact and ctx.current_artifact.strip():
        amendment = run.render_prompt("sa_amendment_prefix.md", {
            "CURRENT_PRD": ctx.current_artifact,
        })
        sa_system = sa_system + "\n\n" + amendment

    conversation_text = _format_conversation(ctx.conversation)
    focus_block = f"\n[Focus on section: {ctx.focus_section}]\n" if ctx.focus_section else ""

    prompt = run.render_prompt("sa_chat.md", {
        "SYSTEM_PROMPT": sa_system,
        "CONVERSATION_TEXT": conversation_text,
        "FOCUS_SECTION": focus_block,
    })
    result = run.harnessed_step(
        telemetry_stage="specify", operation="chat_prd",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    reply, ready = _strip_sentinel(result.raw_output.strip())
    return StageChatResult(
        reply=reply,
        updated_artifact=reply if ready else None,
    )


# ============================================================
#  StageSpec + Validators registry
# ============================================================
PRD_STAGE = StageSpec(
    id="prd",
    label="PRD",
    icon="document",
    telemetry_stage="specify",
    generate_operation="generate_prd",
    refine_operation="refine_prd",
    chat_operation="chat_prd",
    depends_on=(),
    artifact_key="prd",
    prompt_keys=("sa_system.md", "sa_chat.md", "sa_amendment_prefix.md", "prd_refine.md"),
    default_agent_role="prd",
    generate=_prd_generate,
    refine=_prd_refine,
    chat=_prd_chat,
    supports_chat=True,
    on_complete_state_extra={},
)


# (telemetry_stage, operation, fn) — host.register_validator 用
VALIDATORS = [
    ("specify", "generate_prd", _prd_structural_validator),
    ("specify", "refine_prd",   _prd_structural_validator),
    # chat 不跑 structural validator（chat 回的可能只是問題，未必是完整 PRD）
]
