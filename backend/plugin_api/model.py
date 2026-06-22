from __future__ import annotations
import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

# 直接以子模組路徑匯入（非 `from plugin_api import rate_limit`）：本檔在 plugin_api/__init__
# 初始化途中被載入，走子模組路徑可避開「__init__ 尚未設好屬性」的迴圈匯入陷阱。rate_limit 僅依賴 stdlib。
import plugin_api.rate_limit as rate_limit

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
    # invoke(prompt, *, allowed_tools=(), workspace_dir="") -> str
    #   allowed_tools：agent 宣告允許的工具名 tuple（如 ("Read", "Bash")）。
    #   workspace_dir：既有 repo clone 絕對路徑（讀碼 stage 用）；非空時 adapter 應把它
    #     --add-dir 給 model 並補上 Read/Grep/Glob，讓 model 直接讀既有 codebase。
    #   相容契約：新 adapter 應接受 keyword-only allowed_tools / workspace_dir 並給預設值；
    #     舊 adapter 只收 (prompt) 仍合法 —— HarnessRunner 偵測簽章後逐 kwarg 決定是否帶（見 _invoke_adapter）。
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
        """base class 統一驅動子程序 / 串流 / timeout / 取消 / 用量上限自動續跑。

        紀律：
        - prompt 經 stdin 餵入後關閉（避免命令列長度上限、避免 CLI 卡在讀 stdin）
        - pre_run hook：None=原樣、list=改寫 argv、raise HookAbort=拒絕（向上拋給 caller）；只跑一次
        - on_log_chunk hook：None=丟棄該行、str=轉發（可 redact）；逐行串流
        - timeout / cancel 皆 graceful terminate→5s→kill
        - 子程序 spawn 失敗（FileNotFoundError 等）回傳 exit_code=127 而非例外
        - 用量上限（5hr 訂閱方案）：子類 _detect_rate_limit 命中 → 算解鎖時間 → 可取消地等到那刻 →
          重啟同一子程序自動續跑（封頂 cfg.max_cycles 輪，防無限迴圈）。等待對 caller 透明，
          回傳的是「等完並續跑後的最終 result」。post_run 只在最終 result 跑一次。
        """
        hooks = hooks or []
        self._cancel_requested = False
        self._proc = None
        self._cancel_event = asyncio.Event()   # 供等待用量重置期間被 cancel 即時喚醒

        argv = self.build_argv(cwd=cwd, prompt=prompt)
        env = dict(os.environ)
        # pre_run：HookAbort 直接往上拋（caller 負責記錄為拒絕）。只在進迴圈前跑一次（argv 不變）。
        for hook in hooks:
            rewritten = hook.pre_run(self.name, argv, env)
            if rewritten is not None:
                argv = rewritten

        cfg = rate_limit.load_config()
        result = RunResult(exit_code=-1)
        for cycle in range(cfg.max_cycles + 1):
            self._last_scan = ""
            result = await self._spawn_once(
                argv=argv, env=env, cwd=cwd, prompt=prompt, timeout=timeout,
                on_log=on_log, on_event=on_event, hooks=hooks,
            )
            # 終局（成功 / 取消 / 逾時）→ 收工
            if result.ok or result.cancelled or result.timed_out:
                break
            # 純失敗 → 是否為用量上限？（用 head∪tail 偵測，避免大型輸出漏看最後的上限事件）
            # 非上限 → 交回 caller 既有重試策略
            signal = self._detect_rate_limit(getattr(self, "_last_scan", "") or result.last_output)
            if signal is None:
                break
            if cycle >= cfg.max_cycles:
                on_log(f"[rate-limit] 已續跑 {cfg.max_cycles} 輪仍受限，停止等待\n")
                break
            wait_s = rate_limit.seconds_until(signal, now=datetime.now(), cfg=cfg)
            on_log(f"⏸ {signal.message}；約 {rate_limit.humanize(wait_s)} 後自動續跑\n")
            if on_event is not None:
                on_event({"type": "rate_limit_wait", "runner": self.name,
                          "reset_at": signal.reset_at.isoformat() if signal.reset_at else None,
                          "seconds": wait_s})
            interrupted = await self._sleep(wait_s)
            if interrupted:                       # 等待中被 cancel → 視為取消，不再續跑
                result.cancelled = True
                break
            on_log("▶ 用量已重置，續跑中\n")
            if on_event is not None:
                on_event({"type": "rate_limit_resume", "runner": self.name})

        for hook in hooks:
            hook.post_run(self.name, result)
        return result

    async def _spawn_once(self, *, argv: list[str], env: dict, cwd: str, prompt: str,
                          timeout: int, on_log: OnLog, on_event: Optional[OnEvent],
                          hooks: list[ToolHook]) -> RunResult:
        """跑一次子程序（spawn + 串流 + timeout/cancel），回 RunResult。
        不跑 pre_run/post_run（由 run() 統籌）；可被 run() 在用量上限續跑時重複呼叫。"""
        if on_event is not None:
            on_event({"type": "start", "runner": self.name, "argv": argv})

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                # StreamReader 行緩衝上限：預設 64KB，stream-json 單行可塞大 blob（檔案內容 / 長輸出）→
                # 超過會丟 "Separator is not found, and chunk exceed the limit"、整個 run 崩。放寬到 16MB。
                limit=16 * 1024 * 1024,
            )
        except (OSError, ValueError) as exc:
            _log.warning("runner %s spawn failed: %s", self.name, exc)
            return RunResult(exit_code=127, last_output=f"spawn failed: {exc}")

        self._proc = proc
        captured: list[str] = []
        captured_bytes = 0
        # 另留一段「尾段」緩衝：captured 只收前 64KB（head），但用量上限 / result 事件在輸出的最後，
        # 大型輸出時 head 收不到。尾段環狀緩衝確保偵測看得到最後的事件（與 head 合併供偵測用）。
        tail: list[str] = []
        tail_bytes = 0
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
            nonlocal captured_bytes, tail_bytes
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
                tail.append(line)
                tail_bytes += len(line.encode("utf-8"))
                while tail_bytes > self.last_output_max_bytes and len(tail) > 1:
                    tail_bytes -= len(tail.pop(0).encode("utf-8"))

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
        # 偵測用文字 = head ∪ tail（小型輸出兩者重疊，無害；大型輸出確保看得到最後的 result/上限事件）。
        self._last_scan = result.last_output + "".join(tail)
        if on_event is not None:
            on_event({"type": "exit", "runner": self.name, "code": exit_code,
                      "cancelled": cancelled, "timed_out": timed_out})
        return result

    def _detect_rate_limit(self, output: str) -> Optional[rate_limit.RateLimitSignal]:
        """子類覆寫：從輸出判斷是否撞到用量上限並解析解鎖時間。base 不判（回 None）。"""
        return None

    async def _sleep(self, seconds: float) -> bool:
        """睡 seconds 秒；期間被 cancel() 喚醒則提早回 True（中斷），睡滿回 False。
        抽成 method 便於測試覆寫（不真的睡）。"""
        if seconds <= 0:
            return self._cancel_requested
        ev = getattr(self, "_cancel_event", None)
        if ev is None:
            await asyncio.sleep(seconds)
            return self._cancel_requested
        try:
            await asyncio.wait_for(ev.wait(), timeout=seconds)
            return True                          # event 被 set → 等待中遭取消
        except asyncio.TimeoutError:
            return False                         # 睡滿，未被取消

    async def cancel(self) -> None:
        """要求取消：標記旗標 + 喚醒等待中的睡眠 + graceful terminate 已存子程序。"""
        self._cancel_requested = True
        ev = getattr(self, "_cancel_event", None)
        if ev is not None:
            ev.set()
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
