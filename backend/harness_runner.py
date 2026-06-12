"""HarnessRunner —— sync one-shot AI 門面，注入給 stage handler。

職責：
- `harnessed_step`：包 ModelAdapter.invoke + validator chain（warn-only 預設）+ 寫遙測
  （harness_runs / harness_validation_results）；`max_iterations` 支援 fix-loop。
- `get_agent_for_stage`：從 registry.agents 找 role 對應 enabled agent（M2 才會非空）。
- `feedback_block`：把上次 validation 的 fix_hint 組成 prompt prefix（M5 才有真正 fail）。
- `render_prompt`：讀 plugin 內 `prompts/<key>` 檔，做 `{{KEY}}` → value 字串替換；
  cache key 含 plugin_id 與 plugin_dir 以免多 plugin 撞快取。

兩層 runtime 隔離：本模組不 import async runtime；plugin 透過 `plugin_api.HarnessRunner`
Protocol 接觸本實作（duck-typed），不直接 import 本模組。
"""
from __future__ import annotations

import inspect
import json
import logging
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from plugin_api import AgentSpec, HarnessResult, HarnessValidationOutcome, JudgeFn, JudgeVerdict
from plugin_api.harness import (
    ERROR_HARNESS_INTERNAL,
    ERROR_MODEL_MALFORMED,
    ERROR_MODEL_UNAVAILABLE,
    ERROR_MODEL_UNKNOWN,
    SEVERITY_FAIL,
    HarnessContext,
)

from agent_resolver import resolve_lead_agent

if TYPE_CHECKING:
    from plugin_host import Registry  # only for typing

log = logging.getLogger("harness_runner")

# capability_type for stage（與 plugin_host.CAP_STAGE 一致，但不直接 import 避循環）
_CAP_STAGE = "stage"


class HarnessRunner:
    """per (thread, stage) 的 sync AI 門面。WorkflowEngine 在 dispatch 時建立。"""

    def __init__(
        self,
        registry: "Registry",
        thread_id: str,
        stage_id: str = "",
        model_choice: str = "claude-cli",
        judge_model_choice: str = "",
        agent: Optional[AgentSpec] = None,
        workspace_dir: str = "",
    ) -> None:
        self._reg = registry
        self.thread_id = thread_id
        self.stage_id = stage_id
        self.model_choice = model_choice
        # 既有 repo clone 絕對路徑（host 在 dispatch 時依 stage.requires 備好）；非空 → 透傳給
        # adapter，讓 model 能 --add-dir 讀既有 codebase。空 → 行為不變。
        self.workspace_dir = workspace_dir
        # 當前 stage 的 lead agent（engine / collab 注入）；供 max_iterations 與 allowed_tools
        # 解析。None → fallback get_agent_for_stage（依 stage_id 反查，向後相容）。
        self.agent = agent
        # judge 用的 model（空字串 = judge 關閉，預設不跑）。設為某 model_choice 才啟用 judge；
        # 可與生成 model 相同或不同（不同 → 增加判決獨立性）。
        self.judge_model_choice = judge_model_choice
        # fix-loop 用：記下上次 run 的 validation outcomes，供 feedback_block 取用
        self._last_validations: list[HarnessValidationOutcome] = []

    # ============ harnessed_step ============
    def harnessed_step(
        self, *, telemetry_stage: str, operation: str, prompt: str,
        metadata: dict, max_iterations: Optional[int] = None,
    ) -> HarnessResult:
        adapter = self._reg.model_adapters.get(self.model_choice)
        if adapter is None:
            return self._fail(telemetry_stage, operation, run_id=str(uuid.uuid4()),
                              code=ERROR_MODEL_UNAVAILABLE,
                              message=f"ModelAdapter '{self.model_choice}' 未註冊")

        validators = self._reg.validators.get((telemetry_stage, operation), [])
        eff_max = self._effective_max_iterations(max_iterations)
        prev_run_id: Optional[str] = None
        prompt_now = prompt
        allowed_tools = self._allowed_tools()   # agent 宣告的工具；跨 fix-loop iteration 不變
        result_this: Optional[HarnessResult] = None

        for iteration in range(eff_max):
            iter_run_id = str(uuid.uuid4())
            started = time.time()
            last_output = ""
            error_code = ""
            error_message = ""
            outcomes: list[HarnessValidationOutcome] = []

            try:
                last_output = _invoke_adapter(adapter, prompt_now, allowed_tools,
                                              workspace_dir=self.workspace_dir)
            except RuntimeError as exc:
                error_code = ERROR_MODEL_UNKNOWN
                error_message = str(exc)
                log.warning("harness model error (run=%s, iter=%d): %s",
                            iter_run_id, iteration, exc)
            except Exception as exc:  # noqa: BLE001
                error_code = ERROR_HARNESS_INTERNAL
                error_message = repr(exc)
                log.exception("harness internal error (run=%s)", iter_run_id)
            else:
                if not last_output or not last_output.strip():
                    error_code = ERROR_MODEL_MALFORMED
                    error_message = "empty model output"
                else:
                    ctx = HarnessContext(
                        thread_id=self.thread_id,
                        stage=telemetry_stage,
                        operation=operation,
                        model_choice=self.model_choice,
                        prompt=prompt_now,
                        metadata=metadata or {},
                        judge=self._make_judge_callable(
                            telemetry_stage, operation, iter_run_id),
                    )
                    for vfn in validators:
                        try:
                            outcomes.extend(vfn(last_output, ctx) or [])
                        except Exception as exc:  # noqa: BLE001
                            log.exception("validator raised; skipping: %s", exc)

            ended = time.time()
            result_this = HarnessResult(
                run_id=iter_run_id,
                raw_output=last_output,
                validations=outcomes,
                error_code=error_code,
                error_message=error_message,
            )
            self._last_validations = outcomes
            # 每輪記一筆 harness_runs，用 parent_run_id 串接 fix-loop（多輪可追蹤）
            self._record_run(iter_run_id, telemetry_stage, operation, started, ended,
                             result_this, parent_run_id=prev_run_id)

            has_fail = any(o.severity == SEVERITY_FAIL for o in outcomes)
            if error_code or not has_fail:
                # model 出錯 / 無 fail（含 warn-only 通過）→ 收工，回本輪
                return result_this
            # 有 fail 且還有額度 → fix-loop：串 parent、把 fix_hint 附在 prompt 後再試一輪
            prev_run_id = iter_run_id
            prompt_now = prompt + "\n\n" + self.feedback_block(
                telemetry_stage=telemetry_stage, operation=operation,
            )

        # 跑滿 max_iterations 仍有 fail：回最後一輪（validations 帶 fail，status=needs_revision）
        return result_this

    # ============ judge（LLM-as-judge） ============
    def _make_judge_callable(self, telemetry_stage: str, operation: str,
                             parent_run_id: str) -> Optional[JudgeFn]:
        """組一個注入給 HarnessContext.judge 的 callable；judge adapter 未註冊 → None
        （judge validator 會因 ctx.judge is None 而靜默跳過）。每次 judge model call 也記一筆
        harness_runs（operation=judge_*、parent=當輪主 run、model_choice=judge model），可追蹤。

        註：judge run 先於當輪主 run 寫入（judge 在 validator 內執行、主 run 在迴圈末記），
        harness_runs.parent_run_id 無 FK 約束，順序不影響關聯查詢。"""
        judge_adapter = self._reg.model_adapters.get(self.judge_model_choice)
        if judge_adapter is None:
            return None

        def _judge(system_instruction: str, judge_user_prompt: str) -> JudgeVerdict:
            from judge_parse import parse_judge_verdict
            full = system_instruction + "\n\n" + judge_user_prompt
            jrun = str(uuid.uuid4())
            started = time.time()
            try:
                out = _invoke_adapter(judge_adapter, full, ())
            except Exception as exc:  # noqa: BLE001 - judge 失敗不該鎖死使用者（fail-open）
                self._record_judge_run(jrun, telemetry_stage, operation, started,
                                       time.time(), parent_run_id, error=repr(exc))
                return JudgeVerdict(passed=True, parse_ok=False, raw="",
                                    issues=[f"judge call failed: {exc}"])
            self._record_judge_run(jrun, telemetry_stage, operation, started,
                                   time.time(), parent_run_id)
            return parse_judge_verdict(out)

        return _judge

    def _record_judge_run(self, run_id: str, stage: str, operation: str,
                          started: float, ended: float, parent_run_id: str,
                          error: str = "") -> None:
        """judge model call 的遙測（薄封裝 _record_run，記 judge model 而非生成 model）。"""
        result = HarnessResult(
            run_id=run_id, raw_output="",
            error_code=(ERROR_MODEL_UNKNOWN if error else ""), error_message=error,
        )
        self._record_run(run_id, stage, f"judge_{operation}", started, ended, result,
                         parent_run_id=parent_run_id,
                         model_choice_override=self.judge_model_choice)

    # ============ agent / feedback ============
    def get_agent_for_stage(self, stage_id: str) -> Optional[AgentSpec]:
        """依 stage_id 找該 stage 的 lead agent（已收斂到 agent_resolver；不再靠遍歷順序）。
        保留此方法供 plugin_api.runner Protocol 與既有呼叫點相容。"""
        return resolve_lead_agent(self._reg, stage_id)

    def _current_agent(self) -> Optional[AgentSpec]:
        """當前生成 agent：engine/collab 注入的 lead 優先；否則依 stage_id 反查（向後相容）。"""
        return self.agent or self.get_agent_for_stage(self.stage_id)

    def _allowed_tools(self) -> tuple[str, ...]:
        """當前生成 agent 宣告的 allowed_tools。無 agent / 無 tools → ()（行為不變）。
        附件隱含需要的 Read 由 adapter 自行補（見 claude_cli），不在此處理。"""
        agent = self._current_agent()
        return tuple(agent.tools) if (agent and agent.tools) else ()

    def _effective_max_iterations(self, requested: Optional[int]) -> int:
        """max_iterations 決議：顯式傳數字 → 用該值（≥1，向後相容 collab/測試）；
        None → 讀本 stage lead agent.max_iterations（fix-loop 通電靠 agent 設定；
        無 agent → 1，行為不變）。"""
        if requested is not None:
            return max(1, requested)
        agent = self._current_agent()
        return max(1, agent.max_iterations) if agent else 1

    def feedback_block(self, *, telemetry_stage: str, operation: str) -> str:
        """把上次 validation 的 fix_hint 組成「上輪未通過，請修正」前綴。

        spec §11：fix_hint 是祈使句、動詞開頭；告訴下一輪 model 修什麼。
        M1 PRD validator 是 warn-only，預期不會 trigger fix-loop。
        """
        hints = [o for o in self._last_validations if o.fix_hint]
        if not hints:
            return ""
        lines = ["前次驗證未通過，請依下列指示修正後重出："]
        for o in hints:
            lines.append(f"- [{o.validator}] {o.fix_hint}")
        return "\n".join(lines)

    # ============ prompt rendering ============
    def render_prompt(self, prompt_key: str, replacements: dict[str, str]) -> str:
        """讀 plugin 內 prompts/<key>，做 {{KEY}} → value 替換。

        plugin 由 self.stage_id 反查 registry.contributions / plugin_dirs。
        cache key 含 plugin_id + dir + key（spec 附錄 D：第三方 plugin 帶自己 prompts/，
        cache key 須含 plugin 目錄以免多 profile 撞快取）。
        """
        plugin_id = self._owner_of_stage(self.stage_id)
        if plugin_id is None:
            raise RuntimeError(f"找不到 stage '{self.stage_id}' 對應的 plugin")
        plugin_dir = self._reg.plugin_dirs.get(plugin_id)
        if plugin_dir is None:
            raise RuntimeError(f"plugin '{plugin_id}' 未登錄目錄")
        text = _read_prompt_cached(plugin_id, str(plugin_dir), prompt_key)
        for k, v in replacements.items():
            text = text.replace("{{" + k + "}}", v)
        return text

    # ============ internals ============
    def _owner_of_stage(self, stage_id: str) -> Optional[str]:
        for (pid, ctype, cid) in self._reg.contributions:
            if ctype == _CAP_STAGE and cid == stage_id:
                return pid
        return None

    def _fail(self, stage: str, operation: str, *, run_id: str, code: str, message: str) -> HarnessResult:
        result = HarnessResult(run_id=run_id, raw_output="", validations=[],
                               error_code=code, error_message=message)
        started = ended = time.time()
        self._record_run(run_id, stage, operation, started, ended, result)
        return result

    def _record_run(
        self, run_id: str, stage: str, operation: str,
        started: float, ended: float, result: HarnessResult,
        *, parent_run_id: Optional[str] = None, model_choice_override: str = "",
    ) -> None:
        from persistence.dal import connect
        has_fail = any(o.severity == SEVERITY_FAIL for o in result.validations)
        if result.error_code:
            status = "failed"
        elif has_fail:
            status = "needs_revision"   # model 有輸出但 validator 判 fail（內容未通過）
        else:
            status = "succeeded"
        model_choice = model_choice_override or self.model_choice
        with connect() as conn:
            conn.execute(
                "INSERT INTO harness_runs (run_id, thread_id, stage, operation, "
                "model_choice, status, error_code, error_message, started_at, ended_at, "
                "parent_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, self.thread_id, stage, operation, model_choice,
                 status, result.error_code or "", result.error_message or "",
                 started, ended, parent_run_id),
            )
            for outcome in result.validations:
                conn.execute(
                    "INSERT INTO harness_validation_results "
                    "(run_id, validator, severity, message, detail, fix_hint) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, outcome.validator, outcome.severity, outcome.message,
                     _json_dumps_safe(outcome.detail), outcome.fix_hint),
                )


# ============ module-level helpers ============
@lru_cache(maxsize=128)
def _invoke_accepts(fn, param: str) -> bool:
    """偵測 adapter.invoke 是否接受某個關鍵字參數（含收 **kwargs）。
    無法內省的 callable → 保守當不接受（退回舊介面）。cache key = (fn, param)（adapter 註冊後固定）。

    用 inspect.signature 而非 try/except TypeError：後者會把 adapter 內部真正的 TypeError
    誤判為「不接受該參數」而靜默降級、吞掉真錯誤。
    """
    try:
        params = inspect.signature(fn).parameters
    except (ValueError, TypeError):
        return False
    if param in params:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def _invoke_adapter(adapter, prompt: str, allowed_tools: tuple,
                    workspace_dir: str = "") -> str:
    """統一 adapter.invoke 呼叫：逐 kwarg 偵測 adapter 是否支援，支援才帶（舊 adapter 自動降級）。
    allowed_tools / workspace_dir 皆空且 adapter 不需要時 → 走只傳 prompt 的舊路徑（零風險）。"""
    kwargs: dict = {}
    if allowed_tools and _invoke_accepts(adapter.invoke, "allowed_tools"):
        kwargs["allowed_tools"] = allowed_tools
    if workspace_dir and _invoke_accepts(adapter.invoke, "workspace_dir"):
        kwargs["workspace_dir"] = workspace_dir
    return adapter.invoke(prompt, **kwargs)


def _json_dumps_safe(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"


@lru_cache(maxsize=256)
def _read_prompt_cached(plugin_id: str, plugin_dir: str, prompt_key: str) -> str:
    """模組級 cache：key = (plugin_id, plugin_dir, prompt_key)。"""
    return (Path(plugin_dir) / "prompts" / prompt_key).read_text(encoding="utf-8")
