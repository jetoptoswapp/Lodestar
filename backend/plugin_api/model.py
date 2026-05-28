from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

# ---- sync one-shot（stage 生成用）----
@dataclass(frozen=True)
class ModelAdapter:
    model_choice: str
    invoke: Callable[[str], str]
    is_available: Callable[[], bool]
    description: str
    max_context_tokens: int
    prompt_budget_tokens: int
    response_budget_tokens: int


# ---- async long-running（M5 實作 agent 用）----
OnLog = Callable[[str], None]
OnEvent = Callable[[object], None]      # TelemetryEvent；M5 細化型別


@dataclass
class RunResult:
    exit_code: int
    last_output: str = ""
    cancelled: bool = False
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.cancelled and not self.timed_out


class HookAbort(Exception):
    """pre_run 拋出以拒絕一次 run（如推受保護分支）。"""
    def __init__(self, hook_name: str, reason: str) -> None:
        super().__init__(f"[{hook_name}] {reason}")
        self.hook_name = hook_name
        self.reason = reason


class ToolHook(ABC):
    """tool hook ABC，全部預設 no-op；子類只覆寫需要的。"""
    name: str = ""

    def pre_run(self, runner_name: str, argv: list[str],
                env: dict[str, str]) -> Optional[list[str]]:
        return None     # None = 原樣通過；list = 改寫 argv；raise HookAbort = 拒絕

    def post_run(self, runner_name: str, result: object) -> None:
        return None

    def on_log_chunk(self, runner_name: str, chunk: str) -> Optional[str]:
        return chunk    # None = 丟棄該行；str = 轉發（可改寫，如 redact）


class AgentRunner(ABC):
    """M5：async 長時 runner（跑 CLI 子程序、串流、可取消）。"""
    name: str = ""
    last_output_max_bytes: int = 64_000

    @abstractmethod
    def build_argv(self, *, cwd: str, prompt: str) -> list[str]: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    async def run(self, *, cwd: str, prompt: str, timeout: int,
                  on_log: OnLog, on_event: Optional[OnEvent] = None,
                  hooks: Optional[list[ToolHook]] = None) -> RunResult:
        """base class 統一驅動子程序 / 串流 / timeout / 取消。"""
        ...

    async def cancel(self) -> None: ...
