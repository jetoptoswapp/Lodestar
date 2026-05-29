"""builtin_implement register —— 註冊 async runner + tool hooks。

兩階段註冊的第一階段；host 收集後由 M5.2 的 orchestrator 驅動。
hooks 以 event「tool」登記，orchestrator 取 registry.hooks['tool'] 套用到每次 run。
"""
from __future__ import annotations

from plugin_api import PluginHost
from plugin_api.workflow import WorkflowSpec

from plugins.builtin_implement.hooks import DenyProtectedBranchHook, RedactSecretsHook
from plugins.builtin_implement.runner import ClaudeCliRunner, MockRunner
from plugins.builtin_implement.stage import IMPLEMENT_STAGE


def register(host: PluginHost) -> None:
    # runners + tool hooks（M5.1）
    host.register_runner("claude-cli", ClaudeCliRunner)
    host.register_runner("mock", MockRunner)
    host.register_hook("tool", DenyProtectedBranchHook())
    host.register_hook("tool", RedactSecretsHook())

    # implement stage（M5.2）—— 不動 default workflow，另給一個含實作的完整 pipeline。
    # 引用上游 prd/architecture/stories（屬 builtin_core_stages），故 requires_plugins 已宣告依賴，
    # loader 拓樸排序保證它們先註冊、cross-ref 驗證通過。
    host.register_stage(IMPLEMENT_STAGE)
    host.register_workflow(WorkflowSpec(
        id="delivery_pipeline",
        label="Delivery Pipeline",
        description="完整流程：PRD → 架構 → 使用者故事 → 自動實作",
        stages=("prd", "architecture", "stories", "implement"),
        source_plugin="builtin_implement",
    ))
