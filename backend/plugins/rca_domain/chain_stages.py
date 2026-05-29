"""rca_chain（多代理 RCA 鏈）：基線 → 因果圖 → 知識/SOP → 彙整。

4 個依序 stage，各綁一位 specialist agent（role == stage_id）。行為高度相似，
故用工廠函式（_gen / _refine）建 handler、減少重複。彙整 stage 重用
analysis_stage 的 candidate/disclaimer validator；因果 stage 加 Mermaid 圖 validator。

資料流：CSV 掛在 rca_baseline（讀資料的 stage）；下游各 stage 經 depends_on 串接，
host 自動擋缺上游 + reset 下游。persona 在 prompts/*.md。
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
from .analysis_stage import _candidate_causes_validator, _copilot_disclaimer_validator


# ============================================================
#  Causal-graph validator（warn-only）
# ============================================================
_MERMAID_RE = re.compile(r"```\s*mermaid", re.IGNORECASE)


def _causal_graph_validator(artifact: str, _ctx: HarnessContext) -> list[HarnessValidationOutcome]:
    if not _MERMAID_RE.search(artifact):
        return [HarnessValidationOutcome(
            validator="rca.causal_graph", severity=SEVERITY_WARN,
            message="缺少 Mermaid 因果圖",
            fix_hint="加入一個 ```mermaid 因果（cause→effect）圖，標出可能因果鏈與 confounder",
        )]
    return []


# ============================================================
#  Handler 工廠（generate / refine 行為一致，只差 prompt 與上游）
# ============================================================
def _gen(prompt_key: str, telemetry_stage: str, operation: str, upstream_map: dict[str, str]):
    def handler(ctx: StageContext, run) -> StageResult:
        repl = {"ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", []))}
        for placeholder, sid in upstream_map.items():
            repl[placeholder] = ctx.upstream_artifacts.get(sid, "(empty)")
        res = run.harnessed_step(
            telemetry_stage=telemetry_stage, operation=operation,
            prompt=run.render_prompt(prompt_key, repl),
            metadata={"thread_id": ctx.thread_id}, max_iterations=1,
        )
        return StageResult(artifact=res.raw_output.strip(),
                           telemetry_metadata={"run_id": res.run_id})
    return handler


def _refine(prompt_key: str, telemetry_stage: str, operation: str, upstream_map: dict[str, str]):
    def handler(ctx: StageContext, run) -> StageResult:
        repl = {
            "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
            "DRAFT": ctx.current_artifact or "(empty)",
            "INSTRUCTION": ctx.instruction or "",
        }
        for placeholder, sid in upstream_map.items():
            repl[placeholder] = ctx.upstream_artifacts.get(sid, "(empty)")
        res = run.harnessed_step(
            telemetry_stage=telemetry_stage, operation=operation,
            prompt=run.render_prompt(prompt_key, repl),
            metadata={"thread_id": ctx.thread_id}, max_iterations=1,
        )
        return StageResult(artifact=res.raw_output.strip(),
                           telemetry_metadata={"run_id": res.run_id})
    return handler


def _synthesis_chat(ctx: StageContext, run) -> StageChatResult:
    prompt = run.render_prompt("synthesis_chat.md", {
        "SYNTHESIS": ctx.current_artifact or "(empty)",
        "CONVERSATION": _format_conversation(ctx.conversation),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    res = run.harnessed_step(
        telemetry_stage="rca_synthesis", operation="chat_rca_synthesis",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    reply, updated = extract_content_block(res.raw_output.strip())
    return StageChatResult(reply=reply, updated_artifact=updated)


# ============================================================
#  Stages
# ============================================================
BASELINE_STAGE = StageSpec(
    id="rca_baseline", label="Baseline & Profiling",
    description="量化異常 vs 正常基線：偏移幅度、起始時間、受影響的機台／批次／訊號。只描述、不推因。",
    icon="search",
    telemetry_stage="rca_baseline",
    generate_operation="generate_rca_baseline", refine_operation="refine_rca_baseline",
    depends_on=("rca_intake",), artifact_key="rca_baseline",
    prompt_keys=("baseline.md", "baseline_refine.md"), default_agent_role="rca_baseline",
    generate=_gen("baseline.md", "rca_baseline", "generate_rca_baseline", {"INTAKE": "rca_intake"}),
    refine=_refine("baseline_refine.md", "rca_baseline", "refine_rca_baseline", {"INTAKE": "rca_intake"}),
    supports_chat=False,
)

CAUSAL_STAGE = StageSpec(
    id="rca_causal", label="Causal-Graph Reasoning",
    description="因果圖推理：候選因→機制→效應（畫 Mermaid 圖），明確區分相關與因果、標出 confounder。",
    icon="diagram",
    telemetry_stage="rca_causal",
    generate_operation="generate_rca_causal", refine_operation="refine_rca_causal",
    depends_on=("rca_baseline",), artifact_key="rca_causal",
    prompt_keys=("causal.md", "causal_refine.md"), default_agent_role="rca_causal",
    generate=_gen("causal.md", "rca_causal", "generate_rca_causal", {"BASELINE": "rca_baseline"}),
    refine=_refine("causal_refine.md", "rca_causal", "refine_rca_causal", {"BASELINE": "rca_baseline"}),
    supports_chat=False,
)

KNOWLEDGE_STAGE = StageSpec(
    id="rca_knowledge", label="Knowledge / SOP 對照",
    description="把異常特徵對照已知失效模式與 SOP／過往案例，列出各自隱含的檢查項。",
    icon="book",
    telemetry_stage="rca_knowledge",
    generate_operation="generate_rca_knowledge", refine_operation="refine_rca_knowledge",
    depends_on=("rca_baseline",), artifact_key="rca_knowledge",
    prompt_keys=("knowledge.md", "knowledge_refine.md"), default_agent_role="rca_knowledge",
    generate=_gen("knowledge.md", "rca_knowledge", "generate_rca_knowledge", {"BASELINE": "rca_baseline"}),
    refine=_refine("knowledge_refine.md", "rca_knowledge", "refine_rca_knowledge", {"BASELINE": "rca_baseline"}),
    supports_chat=False,
)

SYNTHESIS_STAGE = StageSpec(
    id="rca_synthesis", label="Synthesis（候選根因）",
    description="彙整因果與知識兩路，輸出排序的候選根因表（信心／證據／下一步），供工程師確認。",
    icon="search",
    telemetry_stage="rca_synthesis",
    generate_operation="generate_rca_synthesis", refine_operation="refine_rca_synthesis",
    chat_operation="chat_rca_synthesis",
    depends_on=("rca_causal", "rca_knowledge"), artifact_key="rca_synthesis",
    prompt_keys=("synthesis.md", "synthesis_refine.md", "synthesis_chat.md"),
    default_agent_role="rca_synthesis",
    generate=_gen("synthesis.md", "rca_synthesis", "generate_rca_synthesis",
                  {"CAUSAL": "rca_causal", "KNOWLEDGE": "rca_knowledge"}),
    refine=_refine("synthesis_refine.md", "rca_synthesis", "refine_rca_synthesis",
                   {"CAUSAL": "rca_causal", "KNOWLEDGE": "rca_knowledge"}),
    chat=_synthesis_chat,
    supports_chat=True,
)

CHAIN_STAGES = [BASELINE_STAGE, CAUSAL_STAGE, KNOWLEDGE_STAGE, SYNTHESIS_STAGE]

# (telemetry_stage, operation, fn)
VALIDATORS = [
    ("rca_causal", "generate_rca_causal", _causal_graph_validator),
    ("rca_causal", "refine_rca_causal", _causal_graph_validator),
    ("rca_synthesis", "generate_rca_synthesis", _candidate_causes_validator),
    ("rca_synthesis", "generate_rca_synthesis", _copilot_disclaimer_validator),
    ("rca_synthesis", "refine_rca_synthesis", _candidate_causes_validator),
    ("rca_synthesis", "refine_rca_synthesis", _copilot_disclaimer_validator),
]
