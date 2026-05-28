from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# severity —— 沿用 ver2：目前只發 warn，fail 保留給未來強制層（鐵則：預設 warn-only）
SEVERITY_WARN = "warn"
SEVERITY_FAIL = "fail"
SEVERITIES = frozenset({SEVERITY_WARN, SEVERITY_FAIL})

# 錯誤碼 —— 沿用 ver2 closed set（host 把例外 map 成這些）
ERROR_MODEL_TIMEOUT = "model.timeout"
ERROR_MODEL_NETWORK = "model.network"
ERROR_MODEL_MALFORMED = "model.malformed_output"
ERROR_MODEL_UNAVAILABLE = "model.unavailable"
ERROR_MODEL_UNKNOWN = "model.unknown"
ERROR_HARNESS_CANCELED = "harness.canceled"
ERROR_HARNESS_INTERNAL = "harness.internal"
ERROR_CODES = frozenset({
    ERROR_MODEL_TIMEOUT, ERROR_MODEL_NETWORK, ERROR_MODEL_MALFORMED,
    ERROR_MODEL_UNAVAILABLE, ERROR_MODEL_UNKNOWN,
    ERROR_HARNESS_CANCELED, ERROR_HARNESS_INTERNAL,
})


@dataclass(frozen=True)
class HarnessContext:
    """一次 harnessed AI step 的輸入。prompt 必須是已 render 的最終字串
    —— harness 不做模板渲染（模板留在 prompt 資產，見附錄 D）。"""
    thread_id: str
    stage: str          # 遙測 stage：specify / design / deliver
    operation: str      # generate_architecture / refine_prd / ...
    model_choice: str
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessValidationOutcome:
    """一個 validator 對 model 輸出的判決。fix_hint 是祈使句、動詞開頭，
    告訴下一輪 model 怎麼修；None = 該 validator 不給 hint。"""
    validator: str
    severity: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    fix_hint: Optional[str] = None


@dataclass(frozen=True)
class HarnessResult:
    """一次 harnessed step 的輸出。"""
    run_id: str
    raw_output: str
    validations: list[HarnessValidationOutcome] = field(default_factory=list)
    error_code: str = ""
    error_message: str = ""


# validator 純函式簽名；registry key = (telemetry_stage, operation)
ValidatorFn = Callable[[str, HarnessContext], "list[HarnessValidationOutcome]"]
