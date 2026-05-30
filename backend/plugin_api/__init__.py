"""Plugin-facing API surface. Plugins import ONLY from here."""
from plugin_api.common import DeliveryItem, DeliveryPublishResult
from plugin_api.harness import (
    HarnessContext, HarnessResult, HarnessValidationOutcome, ValidatorFn,
    SEVERITY_WARN, SEVERITY_FAIL,
    JudgeFn, JudgeVerdict, make_judge_validator,
)
from plugin_api.stage import (
    AgentSpec, SkillSpec, StageContext, StageResult, StageChatResult,
    StageSpec, StageGenerateFn, StageRefineFn, StageChatFn,
)
from plugin_api.workflow import AgentBinding, CollabMode, CollabRole, WorkflowSpec, normalize_bindings
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
    "JudgeFn", "JudgeVerdict", "make_judge_validator",
    "AgentSpec", "SkillSpec", "StageContext", "StageResult", "StageChatResult",
    "StageSpec", "StageGenerateFn", "StageRefineFn", "StageChatFn",
    "WorkflowSpec", "AgentBinding", "CollabMode", "CollabRole", "normalize_bindings",
    "IntegrationSpec",
    "ModelAdapter", "AgentRunner", "RunResult", "ToolHook", "HookAbort",
    "OnLog", "OnEvent", "HarnessRunner", "PluginHost", "PluginManifest",
]
