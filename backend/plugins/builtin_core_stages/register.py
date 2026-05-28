"""builtin_core_stages：註冊 PRD / Architecture / Stories stage + default workflow + validators。

M2 完成版：default workflow = (prd, architecture, stories)，雙詞彙：
  prd ↔ specify、architecture ↔ design、stories ↔ deliver。
"""
from __future__ import annotations

from plugin_api import PluginHost
from plugin_api.workflow import WorkflowSpec

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
