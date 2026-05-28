from __future__ import annotations
from typing import Optional, Protocol

from plugin_api.harness import HarnessResult
from plugin_api.stage import AgentSpec


class HarnessRunner(Protocol):
    """host 注入給 stage handler 的唯一 AI 入口（只通 sync one-shot harness）。
    Stage 5 async runtime 的任何符號永不出現在這個 Protocol —— 這是兩層
    runtime 隔離的主要防線（plugin 連 conn 都拿不到）。"""

    def harnessed_step(self, *, telemetry_stage: str, operation: str,
                       prompt: str, metadata: dict,
                       max_iterations: int = 1) -> HarnessResult: ...

    def get_agent_for_stage(self, stage_id: str) -> Optional[AgentSpec]: ...

    def feedback_block(self, *, telemetry_stage: str, operation: str) -> str: ...

    def render_prompt(self, prompt_key: str,
                      replacements: dict[str, str]) -> str: ...
