"""builtin_models：註冊 sync one-shot ModelAdapter。"""
from __future__ import annotations

from plugin_api import PluginHost

from .agy_cli import agy_cli_adapter
from .claude_cli import claude_cli_adapter
from .codex_cli import codex_cli_adapter


def register(host: PluginHost) -> None:
    host.register_model_adapter(claude_cli_adapter)
    host.register_model_adapter(codex_cli_adapter)
    host.register_model_adapter(agy_cli_adapter)
