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

import json
import logging
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from plugin_api import AgentSpec, HarnessResult, HarnessValidationOutcome
from plugin_api.harness import (
    ERROR_HARNESS_INTERNAL,
    ERROR_MODEL_MALFORMED,
    ERROR_MODEL_UNAVAILABLE,
    ERROR_MODEL_UNKNOWN,
    SEVERITY_FAIL,
    HarnessContext,
)

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
    ) -> None:
        self._reg = registry
        self.thread_id = thread_id
        self.stage_id = stage_id
        self.model_choice = model_choice
        # fix-loop 用：記下上次 run 的 validation outcomes，供 feedback_block 取用
        self._last_validations: list[HarnessValidationOutcome] = []

    # ============ harnessed_step ============
    def harnessed_step(
        self, *, telemetry_stage: str, operation: str, prompt: str,
        metadata: dict, max_iterations: int = 1,
    ) -> HarnessResult:
        adapter = self._reg.model_adapters.get(self.model_choice)
        if adapter is None:
            return self._fail(telemetry_stage, operation, run_id=str(uuid.uuid4()),
                              code=ERROR_MODEL_UNAVAILABLE,
                              message=f"ModelAdapter '{self.model_choice}' 未註冊")

        validators = self._reg.validators.get((telemetry_stage, operation), [])
        run_id = str(uuid.uuid4())
        started = time.time()
        last_output = ""
        last_validations: list[HarnessValidationOutcome] = []
        error_code = ""
        error_message = ""
        prompt_now = prompt

        for iteration in range(max(1, max_iterations)):
            try:
                last_output = adapter.invoke(prompt_now)
            except RuntimeError as exc:
                error_code = ERROR_MODEL_UNKNOWN
                error_message = str(exc)
                log.warning("harness model error (run=%s, iter=%d): %s",
                            run_id, iteration, exc)
                break
            except Exception as exc:  # noqa: BLE001
                error_code = ERROR_HARNESS_INTERNAL
                error_message = repr(exc)
                log.exception("harness internal error (run=%s)", run_id)
                break

            if not last_output or not last_output.strip():
                error_code = ERROR_MODEL_MALFORMED
                error_message = "empty model output"
                break

            ctx = HarnessContext(
                thread_id=self.thread_id,
                stage=telemetry_stage,
                operation=operation,
                model_choice=self.model_choice,
                prompt=prompt_now,
                metadata=metadata or {},
            )
            outcomes: list[HarnessValidationOutcome] = []
            for vfn in validators:
                try:
                    outcomes.extend(vfn(last_output, ctx) or [])
                except Exception as exc:  # noqa: BLE001
                    log.exception("validator raised; skipping: %s", exc)
            last_validations = outcomes

            has_fail = any(o.severity == SEVERITY_FAIL for o in outcomes)
            if not has_fail:
                # spec §11：validator 預設 warn-only → 通過，break loop
                break
            # 有 fail → fix-loop：把 fix_hint 附在 prompt 後再試一輪
            self._last_validations = outcomes
            prompt_now = prompt + "\n\n" + self.feedback_block(
                telemetry_stage=telemetry_stage, operation=operation,
            )

        ended = time.time()
        result = HarnessResult(
            run_id=run_id,
            raw_output=last_output,
            validations=last_validations,
            error_code=error_code,
            error_message=error_message,
        )
        self._last_validations = last_validations
        self._record_run(run_id, telemetry_stage, operation, started, ended, result)
        return result

    # ============ agent / feedback ============
    def get_agent_for_stage(self, stage_id: str) -> Optional[AgentSpec]:
        for agent in self._reg.agents.values():
            if agent.role == stage_id and agent.enabled:
                return agent
        return None

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
    ) -> None:
        from persistence.dal import connect
        with connect() as conn:
            status = "succeeded" if not result.error_code else "failed"
            conn.execute(
                "INSERT INTO harness_runs (run_id, thread_id, stage, operation, "
                "model_choice, status, error_code, error_message, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, self.thread_id, stage, operation, self.model_choice,
                 status, result.error_code or "", result.error_message or "",
                 started, ended),
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
def _json_dumps_safe(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"


@lru_cache(maxsize=256)
def _read_prompt_cached(plugin_id: str, plugin_dir: str, prompt_key: str) -> str:
    """模組級 cache：key = (plugin_id, plugin_dir, prompt_key)。"""
    return (Path(plugin_dir) / "prompts" / prompt_key).read_text(encoding="utf-8")
