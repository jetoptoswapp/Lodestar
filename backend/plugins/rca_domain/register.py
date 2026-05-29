"""rca_domain register —— 製造業 RCA 領域（與需求工程並存）。

模式：
  RCA-1 單代理   ：rca_intake → rca_analysis（workflow rca_single）
  RCA-2 多代理鏈 ：rca_intake → rca_baseline → rca_causal → rca_knowledge → rca_synthesis
                   （workflow rca_chain，各 stage 綁一位 specialist）

agent.system_prompt 只放短角色描述；完整 persona 在 prompts/*.md（沿用 builtin 慣例）。
agent.role == stage_id，HarnessRunner.get_agent_for_stage 以此解析。
"""
from __future__ import annotations

from plugin_api import AgentSpec, PluginHost
from plugin_api.workflow import AgentBinding, WorkflowSpec

from .analysis_stage import ANALYSIS_STAGE, VALIDATORS as ANALYSIS_VALIDATORS
from .chain_stages import CHAIN_STAGES, VALIDATORS as CHAIN_VALIDATORS
from .intake_stage import INTAKE_STAGE
from .planner_stage import PLAN_STAGE, VALIDATORS as PLAN_VALIDATORS


_INTAKE_SYSTEM_PROMPT = (
    "You are an RCA intake assistant for a manufacturing engineer. Turn a reported "
    "anomaly (yield drop, parameter drift, signal anomaly) and any attached data into "
    "a clean, factual intake brief. Do not speculate about root causes at intake."
)
_ASSISTANT_SYSTEM_PROMPT = (
    "You are an RCA copilot for a manufacturing process engineer — NOT a judge. Read the "
    "anomaly and the attached process/yield data, then propose CANDIDATE root causes, each "
    "with cited evidence, a confidence level, and a concrete next check. The engineer "
    "confirms the true cause on the floor; your output is hypotheses, not conclusions."
)
_BASELINE_SYSTEM_PROMPT = (
    "You are the baseline / data-profiling specialist in an RCA chain. Quantify the anomaly "
    "vs. the normal window from the data. Descriptive only — no root causes."
)
_CAUSAL_SYSTEM_PROMPT = (
    "You are the causal-graph reasoning specialist in an RCA chain. Propose candidate "
    "cause→effect hypotheses (with a Mermaid graph), separating correlation from causation."
)
_KNOWLEDGE_SYSTEM_PROMPT = (
    "You are the knowledge / SOP-matching specialist in an RCA chain. Map the anomaly "
    "signature onto known failure modes and SOPs, and the checks they imply."
)
_SYNTHESIS_SYSTEM_PROMPT = (
    "You are the synthesis lead in an RCA chain — NOT a judge. Merge causal and knowledge "
    "findings into a ranked set of CANDIDATE root causes with evidence and next checks for "
    "the engineer to confirm on the floor."
)
_PLANNER_SYSTEM_PROMPT = (
    "You are the RCA planning agent. Given an anomaly intake, propose a workflow plan "
    "(which RCA stages / agents / order) as JSON for the engineer to approve before it runs."
)


def _agent(agent_id: str, name: str, role: str, system_prompt: str) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id, name=name, role=role, system_prompt=system_prompt,
        model_choice="claude-cli", tools=("Read",), max_iterations=1, enabled=True,
    )


def register(host: PluginHost) -> None:
    # 1. stages —— 單代理 + 鏈式 + planner
    host.register_stage(INTAKE_STAGE)
    host.register_stage(ANALYSIS_STAGE)
    for stage in CHAIN_STAGES:
        host.register_stage(stage)
    host.register_stage(PLAN_STAGE)

    # 2. validators（warn-only）
    for telemetry_stage, operation, fn in (*ANALYSIS_VALIDATORS, *CHAIN_VALIDATORS, *PLAN_VALIDATORS):
        host.register_validator(telemetry_stage, operation, fn)

    # 3. agents（role == stage_id）
    host.register_agent(_agent("rca_intake_helper", "RCA Intake Helper", "rca_intake", _INTAKE_SYSTEM_PROMPT))
    host.register_agent(_agent("rca_assistant", "RCA Copilot", "rca_analysis", _ASSISTANT_SYSTEM_PROMPT))
    host.register_agent(_agent("rca_baseline_analyst", "Baseline Analyst", "rca_baseline", _BASELINE_SYSTEM_PROMPT))
    host.register_agent(_agent("rca_causal_reasoner", "Causal Reasoner", "rca_causal", _CAUSAL_SYSTEM_PROMPT))
    host.register_agent(_agent("rca_knowledge_agent", "Knowledge / SOP Agent", "rca_knowledge", _KNOWLEDGE_SYSTEM_PROMPT))
    host.register_agent(_agent("rca_synthesizer", "Synthesis Lead", "rca_synthesis", _SYNTHESIS_SYSTEM_PROMPT))
    host.register_agent(_agent("rca_planner", "RCA Planner", "rca_plan", _PLANNER_SYSTEM_PROMPT))

    # 4a. workflow —— 單代理 RCA
    host.register_workflow(WorkflowSpec(
        id="rca_single",
        label="Single-agent RCA",
        description="製造異常 → intake → 單代理候選根因分析（copilot, not judge）",
        stages=("rca_intake", "rca_analysis"),
        agent_bindings={
            "rca_intake": (AgentBinding("rca_intake_helper", "lead"),),
            "rca_analysis": (AgentBinding("rca_assistant", "lead"),),
        },
        source_plugin="rca_domain",
    ))

    # 4b. workflow —— 多代理 RCA 鏈
    host.register_workflow(WorkflowSpec(
        id="rca_chain",
        label="Multi-agent RCA chain",
        description="intake → 基線 → 因果圖 → 知識/SOP → 彙整（多 specialist 分工，copilot, not judge）",
        stages=("rca_intake", "rca_baseline", "rca_causal", "rca_knowledge", "rca_synthesis"),
        agent_bindings={
            "rca_intake": (AgentBinding("rca_intake_helper", "lead"),),
            "rca_baseline": (AgentBinding("rca_baseline_analyst", "lead"),),
            "rca_causal": (AgentBinding("rca_causal_reasoner", "lead"),),
            "rca_knowledge": (AgentBinding("rca_knowledge_agent", "lead"),),
            "rca_synthesis": (AgentBinding("rca_synthesizer", "lead"),),
        },
        source_plugin="rca_domain",
    ))

    # 4c. workflow —— Agentic planner（模式 3 進入點）
    #     intake → rca_plan（AI 產 plan）；核准後 POST /api/projects/{tid}/rca/apply-plan
    #     會把 plan 轉成真 workflow（rca_plan_{tid}）並 rebind 執行。
    host.register_workflow(WorkflowSpec(
        id="rca_planner",
        label="Agentic RCA planner",
        description="intake → AI 規劃 workflow plan → 人核准 → apply 成真 workflow 執行（可規劃/可派工/可追蹤）",
        stages=("rca_intake", "rca_plan"),
        agent_bindings={
            "rca_intake": (AgentBinding("rca_intake_helper", "lead"),),
            "rca_plan": (AgentBinding("rca_planner", "lead"),),
        },
        source_plugin="rca_domain",
    ))

    # 4d. workflow —— collab 示範（§6.4）：rca_analysis 由多 agent 協作執行
    #     discussion：lead + 2 peer 輪流發言 → lead 合成
    host.register_workflow(WorkflowSpec(
        id="rca_panel",
        label="RCA panel（discussion）",
        description="rca_analysis 以多 specialist 討論模式執行：lead + peers 輪流發言、lead 合成候選根因",
        stages=("rca_intake", "rca_analysis"),
        agent_bindings={
            "rca_intake": (AgentBinding("rca_intake_helper", "lead"),),
            "rca_analysis": (
                AgentBinding("rca_assistant", "lead"),
                AgentBinding("rca_causal_reasoner", "peer"),
                AgentBinding("rca_knowledge_agent", "peer"),
            ),
        },
        collab_mode={"rca_analysis": "discussion"},
        source_plugin="rca_domain",
    ))

    # 4e. workflow —— collab 示範（§6.4）：dispatch（lead 拆 → subagent 平行 → lead 合併）
    host.register_workflow(WorkflowSpec(
        id="rca_dispatch",
        label="RCA dispatch（lead + subagents）",
        description="rca_analysis 以 dispatch 模式執行：lead 拆任務分派多 subagent 平行分析、lead 合併",
        stages=("rca_intake", "rca_analysis"),
        agent_bindings={
            "rca_intake": (AgentBinding("rca_intake_helper", "lead"),),
            "rca_analysis": (
                AgentBinding("rca_assistant", "lead"),
                AgentBinding("rca_causal_reasoner", "subagent"),
                AgentBinding("rca_knowledge_agent", "subagent"),
            ),
        },
        collab_mode={"rca_analysis": "dispatch"},
        source_plugin="rca_domain",
    ))
