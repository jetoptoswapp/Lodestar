"""PluginHost 實作 + 全域 Registry。

- Registry：所有 capability 的全域登錄處。各 capability 維持各自合適的內部形狀
  （spec §5.3：不硬統一成單一 dict 形狀），只統一「註冊入口 + ownership metadata」。
- PluginHost：每個 plugin 一個 instance（共享同一 Registry），實作 plugin_api.PluginHost
  Protocol 的 register_*。host 不直接寫 DB —— contribution 落地由 loader 在
  兩階段驗證通過後統一做（host owns I/O，但寫 DB 的時機由 loader 控）。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

from plugin_api.harness import ValidatorFn
from plugin_api.host import PluginManifest
from plugin_api.integration import IntegrationSpec
from plugin_api.model import ModelAdapter
from plugin_api.stage import AgentSpec, StageSpec
from plugin_api.workflow import WorkflowSpec

# capability_type 字串（plugin_contributions.capability_type）
CAP_STAGE = "stage"
CAP_WORKFLOW = "workflow"
CAP_AGENT = "agent"
CAP_INTEGRATION = "integration"
CAP_MODEL_ADAPTER = "model_adapter"
CAP_RUNNER = "runner"


@dataclass
class PluginLoadInfo:
    """單一 plugin 的載入結果（供 GET /api/plugins 顯示來源 / 啟用 / 錯誤）。"""
    manifest: PluginManifest
    loaded: bool
    error: str = ""


@dataclass
class Registry:
    """全域 capability registry（內建 + 第三方共用一份）。"""
    stages: dict[str, StageSpec] = field(default_factory=dict)
    workflows: dict[str, WorkflowSpec] = field(default_factory=dict)
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    integrations: dict[str, IntegrationSpec] = field(default_factory=dict)
    model_adapters: dict[str, ModelAdapter] = field(default_factory=dict)
    runners: dict[str, type] = field(default_factory=dict)
    validators: dict[tuple[str, str], ValidatorFn] = field(default_factory=dict)
    hooks: dict[str, list] = field(default_factory=lambda: defaultdict(list))
    # ownership：(plugin_id, capability_type, capability_id)，供 GUI 顯示來源 + 清理
    contributions: list[tuple[str, str, str]] = field(default_factory=list)
    # 每個 discovered plugin 的載入結果（供 GET /api/plugins）
    loaded_plugins: list["PluginLoadInfo"] = field(default_factory=list)

    _CAP_TABLES = (
        (CAP_STAGE, "stages"), (CAP_WORKFLOW, "workflows"), (CAP_AGENT, "agents"),
        (CAP_INTEGRATION, "integrations"), (CAP_MODEL_ADAPTER, "model_adapters"),
        (CAP_RUNNER, "runners"),
    )

    def remove_plugin(self, plugin_id: str) -> None:
        """把某 plugin 的所有貢獻從記憶體 registry 移除（載入失敗 / 驗證失敗 / disable）。"""
        tables = dict(self._CAP_TABLES)
        for (pid, ctype, cid) in [c for c in self.contributions if c[0] == plugin_id]:
            attr = tables.get(ctype)
            if attr:
                getattr(self, attr).pop(cid, None)
        self.contributions = [c for c in self.contributions if c[0] != plugin_id]
        # 註：validator / hook 不記 contribution（綁在提供 stage 的 plugin 上）；
        # M2 引入 stage plugin 後若要精確清理，再補 validator ownership。


class PluginHost:
    """傳給每個 plugin 的 register(host)。實作 plugin_api.PluginHost Protocol。"""

    def __init__(self, plugin_id: str, registry: Registry) -> None:
        self.plugin_id = plugin_id
        self._reg = registry

    def _own(self, ctype: str, cid: str) -> None:
        self._reg.contributions.append((self.plugin_id, ctype, cid))

    def register_stage(self, spec: StageSpec) -> None:
        self._reg.stages[spec.id] = spec
        self._own(CAP_STAGE, spec.id)

    def register_workflow(self, spec: WorkflowSpec) -> None:
        self._reg.workflows[spec.id] = spec
        self._own(CAP_WORKFLOW, spec.id)

    def register_agent(self, spec: AgentSpec) -> None:
        self._reg.agents[spec.agent_id] = spec
        self._own(CAP_AGENT, spec.agent_id)

    def register_integration(self, spec: IntegrationSpec) -> None:
        self._reg.integrations[spec.target] = spec
        self._own(CAP_INTEGRATION, spec.target)

    def register_model_adapter(self, adapter: ModelAdapter) -> None:
        self._reg.model_adapters[adapter.model_choice] = adapter
        self._own(CAP_MODEL_ADAPTER, adapter.model_choice)

    def register_runner(self, choice: str, cls: type) -> None:
        self._reg.runners[choice] = cls  # 存 class 非 instance
        self._own(CAP_RUNNER, choice)

    def register_validator(self, telemetry_stage: str, operation: str, fn: ValidatorFn) -> None:
        self._reg.validators[(telemetry_stage, operation)] = fn

    def register_hook(self, event: str, fn) -> None:
        self._reg.hooks[event].append(fn)

    def make_harness_runner(self, thread_id: str):
        # sync-AI 門面在 M1 實作；M0 只接 integration capability。
        raise NotImplementedError("HarnessRunner 在 M1 實作")
