from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol

from plugin_api.harness import ValidatorFn
from plugin_api.integration import IntegrationSpec
from plugin_api.model import AgentRunner, ModelAdapter
from plugin_api.runner import HarnessRunner
from plugin_api.stage import AgentSpec, SkillSpec, StageSpec
from plugin_api.workflow import WorkflowSpec


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    description: str
    host_api: str                   # semver range，如 ">=1.0,<2.0"
    entry_module: str               # 單一 import 入口，內含 register(host)
    requires_plugins: tuple[str, ...] = ()
    contributes: dict = field(default_factory=dict)


class PluginHost(Protocol):
    """傳給每個 plugin 的 register(host)。各 register_* 寫進對應 registry，
    並在 plugin_contributions 記一筆 ownership。"""
    plugin_id: str

    def register_stage(self, spec: StageSpec) -> None: ...
    def register_workflow(self, spec: WorkflowSpec) -> None: ...
    def register_agent(self, spec: AgentSpec) -> None: ...           # seed 預設 agent
    def register_skill(self, spec: SkillSpec) -> None: ...           # seed 預設 skill
    def register_integration(self, spec: IntegrationSpec) -> None: ...
    def register_model_adapter(self, adapter: ModelAdapter) -> None: ...
    def register_runner(self, choice: str, cls: type[AgentRunner]) -> None: ...  # 存 class
    def register_validator(self, telemetry_stage: str, operation: str,
                           fn: ValidatorFn) -> None: ...
    def register_hook(self, event: str, fn) -> None: ...
    def make_harness_runner(self, thread_id: str) -> HarnessRunner: ...
