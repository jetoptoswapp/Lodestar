"""Plugin-facing API surface. Plugins import ONLY from here."""
from plugin_api.common import DeliveryItem, DeliveryPublishResult
from plugin_api.harness import (
    HarnessContext, HarnessResult, HarnessValidationOutcome, ValidatorFn,
    SEVERITY_WARN, SEVERITY_FAIL,
)
from plugin_api.stage import (
    AgentSpec, SkillSpec, StageContext, StageResult, StageChatResult,
    StageSpec, StageGenerateFn, StageRefineFn, StageChatFn,
)
from plugin_api.workflow import WorkflowSpec
from plugin_api.integration import IntegrationSpec
from plugin_api.model import (
    ModelAdapter, AgentRunner, RunResult, ToolHook, HookAbort, OnLog, OnEvent,
)
from plugin_api.runner import HarnessRunner
from plugin_api.host import PluginHost, PluginManifest

__all__ = [
    "DeliveryItem", "DeliveryPublishResult",
    "HarnessContext", "HarnessResult", "HarnessValidationOutcome", "ValidatorFn",
    "SEVERITY_WARN", "SEVERITY_FAIL",
    "AgentSpec", "SkillSpec", "StageContext", "StageResult", "StageChatResult",
    "StageSpec", "StageGenerateFn", "StageRefineFn", "StageChatFn",
    "WorkflowSpec", "IntegrationSpec",
    "ModelAdapter", "AgentRunner", "RunResult", "ToolHook", "HookAbort",
    "OnLog", "OnEvent", "HarnessRunner", "PluginHost", "PluginManifest",
]
