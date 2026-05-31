from __future__ import annotations
import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

_log = logging.getLogger("plugin_api.runner")

# ---- sync one-shot（stage 生成用）----
#
# Multimodal extension（spec §6.7 擴展，M1.2 落地 contract，先預備、不強制走）:
#   adapter 若支援原生 multimodal（image / PDF / docx 等內容直接給 model 讀），
#   設 supports_multimodal=True 並提供 invoke_messages。Dispatcher 在 ctx 有附件時
#   偵測 capability：True → 走 invoke_messages(list[content_blocks])，False → 退回
#   本地 parse 後 inline 進 invoke(prompt_string)。
@dataclass(frozen=True)
class ModelAdapter:
    model_choice: str
    # invoke(prompt, *, allowed_tools=()) -> str
    #   allowed_tools：agent 宣告允許的工具名 tuple（如 ("Read", "Bash")）。
    #   相容契約：新 adapter 應接受 keyword-only allowed_tools 並給預設值；舊 adapter 只收
    #   (prompt) 仍合法 —— HarnessRunner 偵測簽章後決定是否帶 allowed_tools（見 _invoke_adapter）。
    invoke: Callable[..., str]
    is_available: Callable[[], bool]
    description: str
    max_context_tokens: int
    prompt_budget_tokens: int
    response_budget_tokens: int
    # ---- multimodal（可選） ----
    supports_multimodal: bool = False
    invoke_messages: Optional[Callable[[list], str]] = None


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
    def build_argv(self, *, cwd: str, prompt: str) -> list[str]:
        """回傳子程序 argv。prompt 由 base run() 經 stdin 餵入（避免 ARG_MAX），
        故 argv 通常不含 prompt 本體；收到 prompt 僅供子類彈性使用。"""
        ...

    @abstractmethod
    def is_available(self) -> bool: ...

    async def run(self, *, cwd: str, prompt: str, timeout: int,
                  on_log: OnLog, on_event: Optional[OnEvent] = None,
                  hooks: Optional[list[ToolHook]] = None) -> RunResult:
        """base class 統一驅動子程序 / 串流 / timeout / 取消。

        紀律：
        - prompt 經 stdin 餵入後關閉（避免命令列長度上限、避免 CLI 卡在讀 stdin）
        - pre_run hook：None=原樣、list=改寫 argv、raise HookAbort=拒絕（向上拋給 caller）
        - on_log_chunk hook：None=丟棄該行、str=轉發（可 redact）；逐行串流
        - timeout / cancel 皆 graceful terminate→5s→kill
        - 子程序 spawn 失敗（FileNotFoundError 等）回傳 exit_code=127 而非例外
        """
        hooks = hooks or []
        self._cancel_requested = False
        self._proc = None

        argv = self.build_argv(cwd=cwd, prompt=prompt)
        env = dict(os.environ)
        # pre_run：HookAbort 直接往上拋（caller 負責記錄為拒絕）
        for hook in hooks:
            rewritten = hook.pre_run(self.name, argv, env)
            if rewritten is not None:
                argv = rewritten

        if on_event is not None:
            on_event({"type": "start", "runner": self.name, "argv": argv})

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, ValueError) as exc:
            _log.warning("runner %s spawn failed: %s", self.name, exc)
            result = RunResult(exit_code=127, last_output=f"spawn failed: {exc}")
            for hook in hooks:
                hook.post_run(self.name, result)
            return result

        self._proc = proc
        captured: list[str] = []
        captured_bytes = 0
        timed_out = False

        async def _feed_stdin() -> None:
            if proc.stdin is None:
                return
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001 - close best-effort
                    pass

        async def _read_stdout() -> None:
            nonlocal captured_bytes
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line: Optional[str] = raw.decode("utf-8", errors="replace")
                for hook in hooks:
                    if line is None:
                        break
                    line = hook.on_log_chunk(self.name, line)
                if line is None:
                    continue
                on_log(line)
                if captured_bytes < self.last_output_max_bytes:
                    captured.append(line)
                    captured_bytes += len(line.encode("utf-8"))

        async def _drive() -> None:
            # 並行餵 stdin + 讀 stdout：避免「大 prompt × 早期大量輸出」的管線死結
            # （若先寫完整 stdin 再讀，子程序可能因 stdout buffer 滿而卡住、雙方互等）。
            await asyncio.gather(_feed_stdin(), _read_stdout())
            await proc.wait()

        try:
            await asyncio.wait_for(_drive(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await self._terminate(proc)
        except asyncio.CancelledError:
            # run() 所屬 task 被取消：清掉子程序後依 asyncio 禮儀往上拋
            await self._terminate(proc)
            raise

        cancelled = bool(getattr(self, "_cancel_requested", False))
        exit_code = proc.returncode if proc.returncode is not None else -1
        result = RunResult(
            exit_code=exit_code,
            last_output="".join(captured),
            cancelled=cancelled,
            timed_out=timed_out,
        )
        if on_event is not None:
            on_event({"type": "exit", "runner": self.name, "code": exit_code,
                      "cancelled": cancelled, "timed_out": timed_out})
        for hook in hooks:
            hook.post_run(self.name, result)
        return result

    async def cancel(self) -> None:
        """要求取消：標記旗標 + graceful terminate 已存子程序。"""
        self._cancel_requested = True
        proc = getattr(self, "_proc", None)
        if proc is not None:
            await self._terminate(proc)

    @staticmethod
    async def _terminate(proc) -> None:
        """terminate → 等 5s → kill；已結束則 no-op。"""
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
