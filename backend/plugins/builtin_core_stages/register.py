"""builtin_core_stages：註冊 PRD / Architecture / Stories stage + default workflow + validators。

M2 完成版：default workflow = (prd, architecture, stories)，雙詞彙：
  prd ↔ specify、architecture ↔ design、stories ↔ deliver。
"""
from __future__ import annotations

from plugin_api import PluginHost
from plugin_api.workflow import AgentBinding, WorkflowSpec

from .architecture_stage import ARCHITECTURE_STAGE, VALIDATORS as ARCH_VALIDATORS
from .change_request_stage import CHANGE_REQUEST_STAGE
from .prd_stage import PRD_STAGE, VALIDATORS as PRD_VALIDATORS
from .stories_stage import STORIES_STAGE, VALIDATORS as STORIES_VALIDATORS
from .ui_design_stage import UI_DESIGN_STAGE, VALIDATORS as UI_VALIDATORS


def register(host: PluginHost) -> None:
    # 1. stages —— 四個 builtin stage + change_request（修改既有專案）
    host.register_stage(PRD_STAGE)
    host.register_stage(ARCHITECTURE_STAGE)
    host.register_stage(UI_DESIGN_STAGE)
    host.register_stage(STORIES_STAGE)
    host.register_stage(CHANGE_REQUEST_STAGE)

    # 2. validators —— 雙詞彙 (telemetry_stage, operation) 對應 registry
    for vlist in (PRD_VALIDATORS, ARCH_VALIDATORS, UI_VALIDATORS, STORIES_VALIDATORS):
        for telemetry_stage, operation, fn in vlist:
            host.register_validator(telemetry_stage, operation, fn)

    # 3. default workflow —— PRD → (Architecture ∥ UI 設計) → Stories
    #    stages 順序只是顯示/拓樸序；平行性由 depends_on 表達（architecture 與 ui_design 都只依賴 prd）。
    host.register_workflow(WorkflowSpec(
        id="default",
        label="Standard Pipeline",
        description="Lodestar default：想法 → PRD → 架構 ∥ UI 設計 → 使用者故事",
        stages=("prd", "architecture", "ui_design", "stories"),
        source_plugin="builtin_core_stages",
    ))

    # 4. requirements_panel —— PRD 以「多 agent 討論」產出（§6.4 discussion）：
    #    SA 主筆（lead）+ PM／資安視角（peer）先各自評論，再由 lead 彙整成正式 PRD。
    #    架構、故事維持單一 agent（如需可在 /workflows 編輯器自行加綁）。
    host.register_workflow(WorkflowSpec(
        id="requirements_panel",
        label="Requirements Panel（討論）",
        description="PRD 多 agent 討論：SA 主筆 + PM／資安 peer 評論後彙整；架構、UI 設計、故事維持單一 agent。",
        stages=("prd", "architecture", "ui_design", "stories"),
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

    # 5. modify_existing —— 修改既有專案：讀既有 repo → 談變更/解 bug → 產出實作 brief。
    #    單 stage（change_request, requires=workspace）；brief 直接當 single implement 的 story 開 PR。
    host.register_workflow(WorkflowSpec(
        id="modify_existing",
        label="修改既有專案",
        description="既有 repo → AI 讀碼 → 談變更/解 bug → 產出實作 brief → implement 開 PR",
        stages=("change_request",),
        agent_bindings={
            "change_request": (AgentBinding("change_planner", "lead"),),
        },
        source_plugin="builtin_core_stages",
    ))
