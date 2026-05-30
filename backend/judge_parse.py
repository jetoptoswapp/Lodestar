"""judge model 輸出的穩健 parse（LLM-as-judge 的 verdict 解析）。

judge 也是 LLM，輸出未必是乾淨 JSON。fallback 階梯（模仿 collab_coordinator._parse_subtasks
的 regex + try/except）：
  1. 整段 json.loads
  2. regex 抓第一個 {...} 再 loads（處理 ```json fence / 前後散文）
  3. 關鍵字嗅探（fail / 不通過 / reject → passed=False），但 parse_ok=False
  4. 全失敗 → JudgeVerdict(passed=True, parse_ok=False)（fail-open：judge 壞掉不鎖死使用者）

註：第 3、4 步都標 parse_ok=False；judge validator 對 parse_ok=False 一律降級為 warn，
故關鍵字嗅探只用於「盡量填 verdict 內容供遙測/除錯」，不會真的觸發 fix-loop 的 fail。
"""
from __future__ import annotations

import json
import re

from plugin_api import JudgeVerdict

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)   # 抓第一個 JSON 物件（含換行）
_FAIL_WORDS = ("fail", "不通過", "未通過", "拒絕", "reject")


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _from_dict(d: dict, raw: str) -> JudgeVerdict:
    return JudgeVerdict(
        passed=bool(d.get("passed", True)),
        score=_safe_float(d.get("score")),
        issues=[str(x) for x in (d.get("issues") or [])],
        fix_hint=(str(d["fix_hint"]) if d.get("fix_hint") else None),
        raw=raw,
        parse_ok=True,
    )


def parse_judge_verdict(text: str) -> JudgeVerdict:
    """把 judge model 的原始輸出解析成 JudgeVerdict。永不拋例外（最壞回 fail-open verdict）。"""
    raw = text or ""
    candidates = [raw.strip()]
    m = _JSON_OBJ_RE.search(raw)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        if not cand:
            continue
        try:
            d = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(d, dict):
            return _from_dict(d, raw)
    # 關鍵字 fallback（parse_ok=False → caller 會降級 warn）
    low = raw.lower()
    if any(w in low for w in _FAIL_WORDS):
        return JudgeVerdict(
            passed=False,
            issues=["judge 以文字判定未通過（JSON 解析失敗）"],
            fix_hint="依 judge 文字回饋逐項修正後重出完整內容",
            raw=raw,
            parse_ok=False,
        )
    return JudgeVerdict(passed=True, raw=raw, parse_ok=False)
