"""rca_analysis stage（單代理 RCA）：讀 intake brief + 附件資料 → 候選根因分析。

定位：AI 是 Copilot 不是 Judge —— 輸出候選根因 + 證據 + 下一步檢查，由工程師確認。

handlers：
- generate：依 upstream rca_intake + 本 stage 附件（CSV/資料）產出候選根因表。
- refine  ：依工程師指示重出完整分析。
- chat    ：就分析問答；必要時用 [CONTENT_START]..[CONTENT_END] 更新 artifact。

validators（warn-only，spec §11）：
- _candidate_causes_validator：至少 3 個候選根因，且含 evidence / confidence / next-check。
- _copilot_disclaimer_validator：需有「候選、待工程師確認、非結論」聲明。
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
    format_attachments as _format_attachments,
    format_conversation as _format_conversation,
)


# ============================================================
#  Validators（warn-only）
# ============================================================
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")          # |---|:--:| 之類分隔列
_CONF_RE = re.compile(r"\b(low|medium|high|低|中|高)\b", re.IGNORECASE)
_EVIDENCE_RE = re.compile(r"evidence|證據|數據|data", re.IGNORECASE)
_NEXTCHECK_RE = re.compile(r"next[\s-]?check|下一步|檢查|查核|verify|confirm", re.IGNORECASE)
# 免責聲明：英文 candidate+confirm/conclusion，或中文 候選/候選假設 + 確認/非結論
_DISCLAIMER_RE = re.compile(
    r"(candidate.*(confirm|not\s+a?\s*conclusion|hypothes))"
    r"|((confirm|hypothes|not\s+a?\s*conclusion).*candidate)"
    r"|(候選.*(確認|非結論|假設))"
    r"|((確認|非結論).*候選)",
    re.IGNORECASE | re.DOTALL,
)


def _count_candidate_rows(artifact: str) -> int:
    """估候選根因數量：取「表格資料列」與「confidence 關鍵字出現數」較大者。"""
    table_rows = 0
    for line in artifact.splitlines():
        s = line.strip()
        if s.startswith("|") and s.count("|") >= 2 and not _TABLE_SEP_RE.match(s):
            table_rows += 1
    if table_rows >= 1:
        table_rows -= 1  # 扣掉 header 列
    conf_hits = len(_CONF_RE.findall(artifact))
    return max(table_rows, conf_hits)


def _candidate_causes_validator(artifact: str, _ctx: HarnessContext) -> list[HarnessValidationOutcome]:
    outcomes: list[HarnessValidationOutcome] = []
    if _count_candidate_rows(artifact) < 3:
        outcomes.append(HarnessValidationOutcome(
            validator="rca.min_candidates", severity=SEVERITY_WARN,
            message="候選根因少於 3 個",
            fix_hint="列出至少 3 個候選根因，每個附證據、信心程度（low/medium/high）與一項可執行的下一步檢查",
        ))
    if not _EVIDENCE_RE.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="rca.has_evidence", severity=SEVERITY_WARN,
            message="候選根因缺少證據引用",
            fix_hint="為每個候選根因引用資料中的具體列/欄位/趨勢作為證據",
        ))
    if not _NEXTCHECK_RE.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="rca.has_next_check", severity=SEVERITY_WARN,
            message="缺少『下一步檢查』建議",
            fix_hint="為每個候選根因給一項工程師可在現場執行、用以確認或排除的下一步檢查",
        ))
    return outcomes


def _copilot_disclaimer_validator(artifact: str, _ctx: HarnessContext) -> list[HarnessValidationOutcome]:
    outcomes: list[HarnessValidationOutcome] = []
    if not _DISCLAIMER_RE.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="rca.copilot_disclaimer", severity=SEVERITY_WARN,
            message="缺少『候選假設、待工程師確認、非結論』的 copilot 聲明",
            fix_hint="於結尾加一句明確聲明：這些是供工程師確認的候選假設，非最終結論",
        ))
    return outcomes


# ============================================================
#  Handlers
# ============================================================
def _analysis_generate(ctx: StageContext, run) -> StageResult:
    prompt = run.render_prompt("rca_single.md", {
        "INTAKE": ctx.upstream_artifacts.get("rca_intake", "(empty)"),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="rca_analysis", operation="generate_rca_analysis",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    return StageResult(
        artifact=result.raw_output.strip(),
        telemetry_metadata={"run_id": result.run_id},
    )


def _analysis_refine(ctx: StageContext, run) -> StageResult:
    prompt = run.render_prompt("rca_refine.md", {
        "ANALYSIS_DRAFT": ctx.current_artifact or "(empty)",
        "INSTRUCTION": ctx.instruction or "",
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="rca_analysis", operation="refine_rca_analysis",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    return StageResult(
        artifact=result.raw_output.strip(),
        telemetry_metadata={"run_id": result.run_id},
    )


def _analysis_chat(ctx: StageContext, run) -> StageChatResult:
    prompt = run.render_prompt("rca_chat.md", {
        "ANALYSIS": ctx.current_artifact or "(empty)",
        "CONVERSATION": _format_conversation(ctx.conversation),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="rca_analysis", operation="chat_rca_analysis",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    reply, updated = extract_content_block(result.raw_output.strip())
    return StageChatResult(reply=reply, updated_artifact=updated)


# ============================================================
#  StageSpec + Validators registry
# ============================================================
ANALYSIS_STAGE = StageSpec(
    id="rca_analysis",
    label="RCA Analysis",
    description="單代理讀資料，直接產出候選根因表（信心／證據／下一步檢查）。適合快速初判。",
    icon="search",
    telemetry_stage="rca_analysis",
    generate_operation="generate_rca_analysis",
    refine_operation="refine_rca_analysis",
    chat_operation="chat_rca_analysis",
    depends_on=("rca_intake",),
    artifact_key="rca_analysis",
    prompt_keys=("rca_single.md", "rca_refine.md", "rca_chat.md"),
    default_agent_role="rca_analysis",
    generate=_analysis_generate,
    refine=_analysis_refine,
    chat=_analysis_chat,
    supports_chat=True,
)


# (telemetry_stage, operation, fn) — host.register_validator 用
VALIDATORS = [
    ("rca_analysis", "generate_rca_analysis", _candidate_causes_validator),
    ("rca_analysis", "generate_rca_analysis", _copilot_disclaimer_validator),
    ("rca_analysis", "refine_rca_analysis", _candidate_causes_validator),
    ("rca_analysis", "refine_rca_analysis", _copilot_disclaimer_validator),
]
