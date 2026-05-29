"""rca_plan stage（RCA-3 Agentic planner）：AI 依 intake 產出一份 workflow plan（JSON）。

定位仍是 copilot：planner 提案「該跑哪些 stage、由哪個 agent、什麼順序」，
artifact 就是 plan JSON（包在 [PLAN_START]..[PLAN_END]）；人核准後再由
`POST /api/projects/{tid}/rca/apply-plan` 轉成真 workflow 並綁定執行。

plan 只能從已知 RCA 可執行 stage / agent 選（prompt 內嵌 catalog）；
plan-shape validator（warn）+ apply 時 `_save_workflow` 硬驗（精確 4xx）為雙重防護。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from plugin_api import (
    HarnessValidationOutcome,
    SEVERITY_WARN,
    StageContext,
    StageResult,
    StageSpec,
)
from plugin_api.harness import HarnessContext

from ._shared import format_attachments as _format_attachments


# planner 可選用的可執行 stage（rca_intake/rca_plan 不列入 plan）
KNOWN_PLAN_STAGES = {"rca_analysis", "rca_baseline", "rca_causal", "rca_knowledge", "rca_synthesis"}

_PLAN_RE = re.compile(r"\[PLAN_START\]\s*(.*?)\s*\[PLAN_END\]", re.DOTALL)


def parse_plan(text: str) -> Optional[dict]:
    """從 artifact 取出 [PLAN_START]..[PLAN_END] 內的 JSON；失敗回 None。

    退路：無 sentinel 時，試抓第一個看起來像 plan 的 {...} 區塊。
    """
    if not text:
        return None
    m = _PLAN_RE.search(text)
    candidate = m.group(1) if m else None
    if candidate is None:
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e <= s:
            return None
        candidate = text[s:e + 1]
    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _plan_shape_validator(artifact: str, _ctx: HarnessContext) -> list[HarnessValidationOutcome]:
    plan = parse_plan(artifact)
    if plan is None:
        return [HarnessValidationOutcome(
            validator="rca.plan_parseable", severity=SEVERITY_WARN,
            message="無法解析 plan JSON",
            fix_hint="把 workflow plan 以 JSON 包在 [PLAN_START] 與 [PLAN_END] 之間輸出",
        )]
    stages = plan.get("stages") or []
    known = [s for s in stages if isinstance(s, dict) and s.get("stage_id") in KNOWN_PLAN_STAGES]
    if not known:
        return [HarnessValidationOutcome(
            validator="rca.plan_known_stages", severity=SEVERITY_WARN,
            message="plan 未引用任何已知 RCA 可執行 stage",
            fix_hint=f"stages 至少含一個：{sorted(KNOWN_PLAN_STAGES)}",
        )]
    return []


def _planner_generate(ctx: StageContext, run) -> StageResult:
    prompt = run.render_prompt("planner.md", {
        "INTAKE": ctx.upstream_artifacts.get("rca_intake", "(empty)"),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    res = run.harnessed_step(
        telemetry_stage="rca_plan", operation="generate_rca_plan",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    return StageResult(artifact=res.raw_output.strip(), telemetry_metadata={"run_id": res.run_id})


def _planner_refine(ctx: StageContext, run) -> StageResult:
    prompt = run.render_prompt("planner_refine.md", {
        "INTAKE": ctx.upstream_artifacts.get("rca_intake", "(empty)"),
        "DRAFT": ctx.current_artifact or "(empty)",
        "INSTRUCTION": ctx.instruction or "",
    })
    res = run.harnessed_step(
        telemetry_stage="rca_plan", operation="refine_rca_plan",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    return StageResult(artifact=res.raw_output.strip(), telemetry_metadata={"run_id": res.run_id})


PLAN_STAGE = StageSpec(
    id="rca_plan", label="RCA Plan（agentic）",
    description="由 AI 依異常提案『要跑哪些 RCA 步驟、由哪個 agent』的 workflow plan；核准後可一鍵套用執行。",
    icon="diagram",
    telemetry_stage="rca_plan",
    generate_operation="generate_rca_plan", refine_operation="refine_rca_plan",
    depends_on=("rca_intake",), artifact_key="rca_plan",
    prompt_keys=("planner.md", "planner_refine.md"), default_agent_role="rca_plan",
    generate=_planner_generate, refine=_planner_refine, supports_chat=False,
)

VALIDATORS = [
    ("rca_plan", "generate_rca_plan", _plan_shape_validator),
    ("rca_plan", "refine_rca_plan", _plan_shape_validator),
]
