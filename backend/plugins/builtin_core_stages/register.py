"""builtin_core_stages：註冊 PRD stage + default workflow + validators。"""
from __future__ import annotations

from plugin_api import PluginHost
from plugin_api.workflow import WorkflowSpec

from .prd_stage import PRD_STAGE, VALIDATORS


def register(host: PluginHost) -> None:
    host.register_stage(PRD_STAGE)
    for telemetry_stage, operation, fn in VALIDATORS:
        host.register_validator(telemetry_stage, operation, fn)
    # default workflow（M1 只含 PRD；M2 補架構 / 故事）
    host.register_workflow(WorkflowSpec(
        id="default",
        label="Standard Pipeline",
        description="Lodestar default workflow（M1 範圍：PRD only；M2 補完架構與故事）",
        stages=("prd",),
        source_plugin="builtin_core_stages",
    ))
