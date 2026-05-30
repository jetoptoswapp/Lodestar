"""builtin_core_stages：註冊 PRD / Architecture / Stories stage + default workflow + validators。

M2 完成版：default workflow = (prd, architecture, stories)，雙詞彙：
  prd ↔ specify、architecture ↔ design、stories ↔ deliver。
"""
from __future__ import annotations

from plugin_api import PluginHost
from plugin_api.workflow import AgentBinding, WorkflowSpec

from .architecture_stage import ARCHITECTURE_STAGE, VALIDATORS as ARCH_VALIDATORS
from .prd_stage import PRD_STAGE, VALIDATORS as PRD_VALIDATORS
from .stories_stage import STORIES_STAGE, VALIDATORS as STORIES_VALIDATORS


def register(host: PluginHost) -> None:
    # 1. stages —— 三個 builtin stage
    host.register_stage(PRD_STAGE)
    host.register_stage(ARCHITECTURE_STAGE)
    host.register_stage(STORIES_STAGE)

    # 2. validators —— 雙詞彙 (telemetry_stage, operation) 對應 registry
    for vlist in (PRD_VALIDATORS, ARCH_VALIDATORS, STORIES_VALIDATORS):
        for telemetry_stage, operation, fn in vlist:
            host.register_validator(telemetry_stage, operation, fn)

    # 3. default workflow —— PRD → Architecture → Stories
    host.register_workflow(WorkflowSpec(
        id="default",
        label="Standard Pipeline",
        description="Lodestar default：想法 → PRD → 架構 → 使用者故事",
        stages=("prd", "architecture", "stories"),
        source_plugin="builtin_core_stages",
    ))

    # 4. requirements_panel —— PRD 以「多 agent 討論」產出（§6.4 discussion）：
    #    SA 主筆（lead）+ PM／資安視角（peer）先各自評論，再由 lead 彙整成正式 PRD。
    #    架構、故事維持單一 agent（如需可在 /workflows 編輯器自行加綁）。
    host.register_workflow(WorkflowSpec(
        id="requirements_panel",
        label="Requirements Panel（討論）",
        description="PRD 多 agent 討論：SA 主筆 + PM／資安 peer 評論後彙整；架構、故事維持單一 agent。",
        stages=("prd", "architecture", "stories"),
        agent_bindings={
            "prd": (
                AgentBinding("seed_prd", "lead"),
                AgentBinding("seed_prd_pm", "peer"),
                AgentBinding("seed_prd_security", "peer"),
            ),
        },
        collab_mode={"prd": "discussion"},
        source_plugin="builtin_core_stages",
    ))
