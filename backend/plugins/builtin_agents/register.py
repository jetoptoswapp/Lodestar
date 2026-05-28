"""builtin_agents：註冊三個 seed AgentSpec。

`role` 對齊 builtin_core_stages 的 stage id（prd / architecture / stories），
HarnessRunner.get_agent_for_stage(stage_id) 會撈 role==stage_id 的 enabled agent。

`system_prompt` 是簡短「角色描述」前綴；真正完整的 prompt 在各 stage handler
透過 `run.render_prompt(...)` 從 builtin_core_stages/prompts/ 注入。
M3 後使用者可在 /agents UI 編輯 system_prompt / skills / tools / model 覆蓋 seed。
"""
from __future__ import annotations

from plugin_api import AgentSpec, PluginHost


_SA_SYSTEM_PROMPT = (
    "You are a strict and meticulous System Analyst (SA). Your job is to turn vague "
    "user requirements into an unambiguous Product Requirements Document (PRD). "
    "Always ask discovery questions before writing the PRD, and emit `[PRD_READY]` "
    "at the very end of your final reply only when the PRD is complete."
)

_ARCHITECT_SYSTEM_PROMPT = (
    "You are a Staff Software Architect. Produce architectures that are proportional "
    "to the actual scope the PRD describes — neither under-engineered nor "
    "over-engineered. Always classify the project tier (T0 / T1 / T2) on the first "
    "line and trace every decision back to a PRD requirement or the tier's defaults."
)

_PM_SYSTEM_PROMPT = (
    "You are a Senior Product Manager and Agile Coach. Produce user stories that the "
    "implementation agent can finish in a 10–15 minute fixed-budget loop. Keep each "
    "story ≤ 4 engineering hours, one concrete subsystem per story, with parser-strict "
    "heading shapes (`## Epic N:` / `### Story N.M — `)."
)


def register(host: PluginHost) -> None:
    host.register_agent(AgentSpec(
        agent_id="seed_prd",
        name="SA Agent (PRD)",
        role="prd",
        system_prompt=_SA_SYSTEM_PROMPT,
        model_choice="claude-cli",
        skills=(),
        tools=(),
        max_iterations=1,
        enabled=True,
    ))
    host.register_agent(AgentSpec(
        agent_id="seed_architect",
        name="Architect Agent",
        role="architecture",
        system_prompt=_ARCHITECT_SYSTEM_PROMPT,
        model_choice="claude-cli",
        skills=(),
        tools=(),
        max_iterations=1,
        enabled=True,
    ))
    host.register_agent(AgentSpec(
        agent_id="seed_pm",
        name="PM Agent (Stories)",
        role="stories",
        system_prompt=_PM_SYSTEM_PROMPT,
        model_choice="claude-cli",
        skills=(),
        tools=(),
        max_iterations=1,
        enabled=True,
    ))
