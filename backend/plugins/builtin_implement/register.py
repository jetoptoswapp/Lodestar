"""builtin_implement register —— 註冊 async runner + tool hooks。

兩階段註冊的第一階段；host 收集後由 M5.2 的 orchestrator 驅動。
hooks 以 event「tool」登記，orchestrator 取 registry.hooks['tool'] 套用到每次 run。
"""
from __future__ import annotations

from plugin_api import PluginHost

from plugins.builtin_implement.hooks import DenyProtectedBranchHook, RedactSecretsHook
from plugins.builtin_implement.runner import ClaudeCliRunner, MockRunner


def register(host: PluginHost) -> None:
    host.register_runner("claude-cli", ClaudeCliRunner)
    host.register_runner("mock", MockRunner)
    host.register_hook("tool", DenyProtectedBranchHook())
    host.register_hook("tool", RedactSecretsHook())
