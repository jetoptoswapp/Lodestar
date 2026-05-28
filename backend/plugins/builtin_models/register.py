"""builtin_models：註冊 sync one-shot ModelAdapter。"""
from __future__ import annotations

from plugin_api import PluginHost

from .claude_cli import claude_cli_adapter


def register(host: PluginHost) -> None:
    host.register_model_adapter(claude_cli_adapter)
