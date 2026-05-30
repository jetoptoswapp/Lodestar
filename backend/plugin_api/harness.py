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
    # LLM-as-judge：由 HarnessRunner 注入的「再呼叫一次 model 下判決」能力。
    # None = judge 不可用（直接單測 validator、或 judge adapter 未註冊）→ judge validator 應靜默跳過。
    judge: Optional["JudgeFn"] = None


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


# ============ LLM-as-judge（語義驗證） ============
@dataclass(frozen=True)
class JudgeVerdict:
    """judge model 輸出 parse 後的結構化判決。

    parse_ok=False（judge 自身失敗 / 無法解析輸出）時，judge validator 一律 fail-open
    降級為 warn —— 絕不因 judge 失靈把使用者鎖死（warn-only 鐵則的延伸）。"""
    passed: bool
    score: Optional[float] = None
    issues: list[str] = field(default_factory=list)
    fix_hint: Optional[str] = None
    raw: str = ""
    parse_ok: bool = True


# judge 呼叫簽名：(system_instruction, judge_user_prompt) -> JudgeVerdict。
# HarnessRunner 注入進 HarnessContext.judge；judge validator 以 ctx.judge(system, user) 取語義判決。
JudgeFn = Callable[[str, str], "JudgeVerdict"]


def make_judge_validator(*, rubric: str, name: str,
                         fail_on_reject: bool = True) -> ValidatorFn:
    """造一個 LLM-as-judge validator（語義驗證），與既有 structural validator 並存於同一 chain。

    rubric：給 judge 的審查標準（祈使、可量化）。
    fail_on_reject=True → judge 判 not passed 時發 SEVERITY_FAIL（觸發 fix-loop，需 max_iterations>1）；
                  False → 永遠 warn（純觀測，不通電 fix-loop）。

    鐵則守護：
      - ctx.judge 不可用（judge 關閉 / adapter 缺）→ 靜默跳過（回 []），預設零成本、零行為改變。
      - judge 自身解析失敗（parse_ok=False）→ 一律降級 warn，絕不因 judge 失靈鎖死使用者。
    """
    def _judge_validator(output: str, ctx: "HarnessContext") -> "list[HarnessValidationOutcome]":
        if ctx.judge is None:
            return []
        system = (
            "You are a strict reviewer. Judge the ARTIFACT against the RUBRIC. "
            'Reply ONLY a JSON object: {"passed": bool, "score": number 0..1, '
            '"issues": [string], "fix_hint": string}. No prose outside the JSON.'
        )
        user = f"# RUBRIC\n{rubric}\n\n# ARTIFACT\n{output}"
        v = ctx.judge(system, user)
        if v.passed:
            return [HarnessValidationOutcome(
                validator=name, severity=SEVERITY_WARN,
                message=f"judge 通過（score={v.score}）",
                detail={"score": v.score, "parse_ok": v.parse_ok})]
        severity = SEVERITY_FAIL if (fail_on_reject and v.parse_ok) else SEVERITY_WARN
        return [HarnessValidationOutcome(
            validator=name, severity=severity,
            message="; ".join(v.issues) or "judge 判定未通過",
            detail={"score": v.score, "issues": v.issues, "parse_ok": v.parse_ok},
            fix_hint=v.fix_hint or "依 judge issues 逐項修正後重出完整內容")]
    return _judge_validator
