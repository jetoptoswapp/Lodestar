"""claude CLI 用量上限（5hr 訂閱方案）偵測 + 解鎖時間解析（純函式、僅 stdlib）。

async runner（plugin_api.model.AgentRunner）與 sync adapter（builtin_models.claude_cli）共用同一套
偵測，撞到上限時「抓出何時解鎖 → 等到那刻 → 自動續跑」。

為何字串解析：claude CLI 的 5hr 上限是訂閱方案層級（非 API 429），CLI 並未提供結構化的 reset 欄位，
只有人讀訊息（且格式隨版本變），故容錯多種格式；解析不到時間就退回固定預設等待，仍能自動續跑。

判定刻意保守——只在出現「明確的上限片語」時才命中，避免 agent 輸出剛好提到 "usage limit" 被誤判。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# 只認這些「CLI 自己吐的上限訊息」片語（小寫比對）。涵蓋已知 / 文件化的多種措辭：
#   - "Claude AI usage limit reached|<epoch>"（pipe + epoch；舊/某些路徑）
#   - "You've hit your session limit · resets 3:45pm"（文件化現行）
#   - weekly / Opus 變體
_PHRASES = (
    "usage limit reached",
    "claude ai usage limit",
    "claude usage limit",
    "hit your session limit",
    "hit your weekly limit",
    "hit your opus limit",
    "hit your usage limit",
)

# 1) epoch 形式： "...usage limit reached|1718900000"（秒或毫秒）
_EPOCH_RE = re.compile(r"usage limit reached\s*\|\s*(\d{10,13})", re.IGNORECASE)

# 2) "resets <time>" 形式（可帶星期幾）："resets 3:45pm" / "resets 3pm" / "resets 15:45"
#    / "resets Mon 12:00am"。· 分隔與大小寫皆容忍。
_RESETS_RE = re.compile(
    r"reset(?:s|ting)?\s+(?:(?:at|on)\s+)?"
    r"(?:(?P<dow>mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s+)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?",
    re.IGNORECASE,
)

_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# CLI 每次 run 都會吐 `{"type":"rate_limit_event","rate_limit_info":{"status":...,"resetsAt":<epoch>,
# "rateLimitType":"five_hour",...}}`。status="allowed"（或 allow* / warning）＝正常，其餘＝受限。
# 這是 CLI 權威的結構化訊號，優先於人讀字串猜測。
_OK_STATUSES = {"allowed", "allowed_warning", "warning", "ok"}


@dataclass
class RateLimitSignal:
    """偵測到用量上限。reset_at 為本地（naive）解鎖時刻；None = 命中上限但解析不到時間。"""
    reset_at: Optional[datetime]
    message: str


@dataclass
class WaitConfig:
    default_wait: float     # 解析不到 reset 時間時的固定等待（秒）
    max_wait: float         # 單次等待封頂（秒），防解析錯誤導致超長等待
    buffer: float           # reset 時刻後再多等的緩衝（秒）
    max_cycles: int         # 一次 run 內最多續跑幾輪（防無限迴圈）


def load_config() -> WaitConfig:
    """從 env 讀等待參數（皆可調，方便測試 / 場景微調）。"""
    def _num(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, "") or default)
        except (TypeError, ValueError):
            return default

    return WaitConfig(
        default_wait=_num("LODESTAR_RATELIMIT_DEFAULT_WAIT", 3600.0),
        max_wait=_num("LODESTAR_RATELIMIT_MAX_WAIT", 6 * 3600.0),
        buffer=_num("LODESTAR_RATELIMIT_BUFFER", 60.0),
        max_cycles=int(_num("LODESTAR_RATELIMIT_MAX_CYCLES", 6)),
    )


def _hour_24(h: int, ap: Optional[str]) -> Optional[int]:
    """把 12 小時制 + am/pm 轉 24 小時制；無 am/pm 則視為已是 24 小時制。回 None = 不合法。"""
    if ap:
        ap = ap.lower()
        if not 1 <= h <= 12:
            return None
        if ap == "am":
            return 0 if h == 12 else h
        return 12 if h == 12 else h + 12
    return h if 0 <= h <= 23 else None


def _next_occurrence(now: datetime, m: "re.Match[str]") -> Optional[datetime]:
    """由 "resets ..." 比對結果算出「下一次該時刻」（本地時間）。"""
    hour = _hour_24(int(m.group("h")), m.group("ap"))
    if hour is None:
        return None
    minute = int(m.group("m") or 0)
    if not 0 <= minute <= 59:
        return None

    dow = m.group("dow")
    base = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if dow:
        target = _DOW[dow.lower()[:3]]
        days = (target - now.weekday()) % 7
        cand = base + timedelta(days=days)
        if days == 0 and cand <= now:          # 今天就是該星期幾但時刻已過 → 下週
            cand += timedelta(days=7)
        return cand
    # 無星期幾：今天該時刻；已過則明天
    return base if base > now else base + timedelta(days=1)


def _error_context(output: str) -> str:
    """從 stream-json 混雜輸出抽出「CLI 自己的錯誤/狀態文字」，排除 assistant 內容與成功 result。

    防誤判關鍵：產出物（PRD / stories…）本身可能合法提到 "usage limit"。這些字會出現在
    `assistant` 事件、或成功 `result` 事件的 `result` 欄位裡 —— 一律排除；只看 stderr 純文字、
    壞 JSON、以及 system / error / **錯誤的** result 事件。"""
    parts: list[str] = []
    for line in output.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("{") and s.endswith("}"):
            try:
                ev = json.loads(s)
            except (ValueError, TypeError):
                parts.append(s)                  # 壞 JSON 當純文字看
                continue
            if not isinstance(ev, dict):
                parts.append(s)
                continue
            etype = ev.get("type")
            if etype == "assistant":
                continue                         # assistant 內容＝產出物，略過
            if etype == "result":
                # 成功 result 的 `result` 欄位內含產出物 → 不看；只有錯誤結果才看
                if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                    parts.append(s)
                continue
            parts.append(s)                      # system / error / 其他 → 看其文字
        else:
            parts.append(s)                      # 純文字（多為 stderr）
    return "\n".join(parts)


def _structured_signal(output: str) -> Optional[RateLimitSignal]:
    """掃 stream-json 的 `rate_limit_event`：status 非 allowed 系 → 受限，用 resetsAt 當解鎖時間。
    這是 CLI 權威的結構化訊號（優先於字串猜測）。status=allowed（每次 run 都有）→ 不觸發。"""
    for line in output.splitlines():
        s = line.strip()
        if '"rate_limit_event"' not in s:
            continue
        try:
            ev = json.loads(s)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict) or ev.get("type") != "rate_limit_event":
            continue
        info = ev.get("rate_limit_info") or {}
        status = str(info.get("status", "")).lower()
        if not status or status in _OK_STATUSES or status.startswith("allow"):
            continue
        rtype = info.get("rateLimitType") or "rate_limit"
        resets = info.get("resetsAt")
        reset_at = None
        if isinstance(resets, (int, float)) and resets > 0:
            try:
                reset_at = datetime.fromtimestamp(int(resets))
            except (OverflowError, OSError, ValueError):
                reset_at = None
        msg = f"用量上限（{rtype}，status={status}）"
        if reset_at:
            msg += f"，解鎖時間 {reset_at:%Y-%m-%d %H:%M}"
        return RateLimitSignal(reset_at, msg)
    return None


def detect(output: str, *, now: datetime) -> Optional[RateLimitSignal]:
    """從 CLI 輸出（stdout/stderr 皆可）判斷是否撞到用量上限並解析解鎖時間。
    優先用結構化 `rate_limit_event`（權威），退而求其次用人讀字串。
    未命中 → None。命中但無法解析時間 → RateLimitSignal(reset_at=None)。"""
    if not output:
        return None
    structured = _structured_signal(output)
    if structured is not None:
        return structured
    hay = _error_context(output)
    low = hay.lower()
    if not any(p in low for p in _PHRASES):
        return None

    m = _EPOCH_RE.search(hay)
    if m:
        ts = int(m.group(1))
        if ts >= 10 ** 12:                       # 毫秒 → 秒
            ts //= 1000
        try:
            reset = datetime.fromtimestamp(ts)
            return RateLimitSignal(reset, f"用量上限，解鎖時間 {reset:%Y-%m-%d %H:%M}")
        except (OverflowError, OSError, ValueError):
            pass

    m = _RESETS_RE.search(hay)
    if m:
        reset = _next_occurrence(now, m)
        if reset:
            return RateLimitSignal(reset, f"用量上限，解鎖時間 {reset:%Y-%m-%d %H:%M}")

    return RateLimitSignal(None, "用量上限（解析不到解鎖時間，採預設等待）")


def seconds_until(signal: RateLimitSignal, *, now: datetime,
                  cfg: Optional[WaitConfig] = None) -> float:
    """算「該睡多久」（秒）。reset 後加 buffer、封頂 max_wait；reset 已過或無時間時的退路見內文。"""
    cfg = cfg or load_config()
    if signal.reset_at is None:
        return min(cfg.default_wait, cfg.max_wait)
    delta = (signal.reset_at - now).total_seconds() + cfg.buffer
    if delta <= 0:                               # 解鎖時刻已過 → 極短等待後即重試
        return min(cfg.buffer, cfg.max_wait)
    return min(delta, cfg.max_wait)


def humanize(seconds: float) -> str:
    """把秒數轉成簡短中文時長（給 log 用），如 "2h12m" / "8m" / "45s"。"""
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    mnt, sec = divmod(rem, 60)
    if h:
        return f"{h}h{mnt:02d}m"
    if mnt:
        return f"{mnt}m{sec:02d}s"
    return f"{sec}s"
