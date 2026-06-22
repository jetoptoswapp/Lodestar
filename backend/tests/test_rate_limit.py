"""用量上限（5hr）偵測 + 解鎖時間解析 + 自動續跑 測試。

三層：
1. 純解析（plugin_api.rate_limit.detect / seconds_until）—— 多格式 + 不誤判。
2. async 路徑（AgentRunner.run）—— 等待→重啟→成功、續跑封頂、等待中取消。
3. sync 路徑（builtin_models.claude_cli._invoke）—— 重試→成功、非限流仍 raise、正常不受影響。

async 測試以 asyncio.run() 驅動（不依賴 pytest-asyncio）；等待一律覆寫 _sleep，不真的睡。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime

import pytest

from plugin_api import AgentRunner, rate_limit
from plugin_api.rate_limit import RateLimitSignal, WaitConfig

# 固定「現在」：2026-06-21（週日）13:00，所有解析測試以此為基準
NOW = datetime(2026, 6, 21, 13, 0, 0)


# ============ 1. 純解析：detect ============
def _result_err(text: str) -> str:
    """包成一行錯誤 result 事件（is_error=true）。"""
    return '{"type":"result","is_error":true,"result":"%s"}' % text


def test_detect_epoch_seconds():
    sig = rate_limit.detect(_result_err("Claude AI usage limit reached|1782564300"), now=NOW)
    assert sig and sig.reset_at == datetime.fromtimestamp(1782564300)


def test_detect_epoch_millis():
    sig = rate_limit.detect(_result_err("Claude AI usage limit reached|1782564300000"), now=NOW)
    assert sig and sig.reset_at == datetime.fromtimestamp(1782564300)


def test_detect_resets_pm_same_day():
    # 13:00 → 「resets 3:45pm」= 今天 15:45
    sig = rate_limit.detect("You've hit your session limit · resets 3:45pm", now=NOW)
    assert sig and sig.reset_at == datetime(2026, 6, 21, 15, 45)


def test_detect_resets_am_next_day():
    # 13:00 → 「resets 9am」已過 → 明天 09:00
    sig = rate_limit.detect("You've hit your session limit · resets 9am", now=NOW)
    assert sig and sig.reset_at == datetime(2026, 6, 22, 9, 0)


def test_detect_resets_24h():
    sig = rate_limit.detect("usage limit reached, resets 18:30", now=NOW)
    assert sig and sig.reset_at == datetime(2026, 6, 21, 18, 30)


def test_detect_weekly_weekday():
    # 週日 → 「resets Mon 12:00am」= 隔天（週一）00:00
    sig = rate_limit.detect("You've hit your weekly limit · resets Mon 12:00am", now=NOW)
    assert sig and sig.reset_at == datetime(2026, 6, 22, 0, 0)


def test_detect_opus_limit_phrase():
    sig = rate_limit.detect("You've hit your Opus limit · resets 3pm", now=NOW)
    assert sig and sig.reset_at == datetime(2026, 6, 21, 15, 0)


def test_detect_phrase_without_time():
    sig = rate_limit.detect(_result_err("claude usage limit reached, please try later"), now=NOW)
    assert sig is not None and sig.reset_at is None


def test_detect_no_false_positive_on_assistant_text():
    # 產出物（assistant 內容）合法提到 usage limit → 不可誤判
    art = ('{"type":"assistant","message":{"content":[{"type":"text",'
           '"text":"When the usage limit reached, show a banner. resets 3:45pm later."}]}}')
    assert rate_limit.detect(art, now=NOW) is None


def test_detect_no_false_positive_on_successful_result():
    sr = ('{"type":"result","subtype":"success","is_error":false,'
          '"result":"Story 1.2: handle usage limit reached state, resets 9am"}')
    assert rate_limit.detect(sr, now=NOW) is None


def test_detect_plain_failure_is_none():
    assert rate_limit.detect("Error: build failed with exit code 1", now=NOW) is None


def test_detect_empty_is_none():
    assert rate_limit.detect("", now=NOW) is None
    assert rate_limit.detect(None, now=NOW) is None  # type: ignore[arg-type]


def test_detect_stderr_plaintext():
    # 純文字 stderr（非 JSON）也要認得
    sig = rate_limit.detect("Claude AI usage limit reached|1782564300", now=NOW)
    assert sig and sig.reset_at == datetime.fromtimestamp(1782564300)


# ---- 結構化 rate_limit_event（CLI 權威訊號；真實 shape） ----
def test_detect_structured_blocked_uses_resetsAt():
    ev = ('{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
          '"resetsAt":1782094800,"rateLimitType":"five_hour","isUsingOverage":false}}')
    sig = rate_limit.detect(ev, now=NOW)
    assert sig and sig.reset_at == datetime.fromtimestamp(1782094800)
    assert "five_hour" in sig.message


def test_detect_structured_allowed_is_none():
    # 真實 allowed 事件（每次 run 都有；overageStatus=rejected 但 status=allowed）→ 不可觸發
    ev = ('{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
          '"resetsAt":1782094800,"rateLimitType":"five_hour","overageStatus":"rejected",'
          '"overageDisabledReason":"org_level_disabled","isUsingOverage":false}}')
    assert rate_limit.detect(ev, now=NOW) is None


def test_detect_structured_allowed_warning_is_none():
    ev = ('{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning",'
          '"resetsAt":1782094800,"rateLimitType":"five_hour"}}')
    assert rate_limit.detect(ev, now=NOW) is None


def test_detect_structured_takes_priority_over_strings():
    # 結構化事件 + 同時 stdout 有人讀字串 → 用結構化的 resetsAt
    mixed = (
        '{"type":"rate_limit_event","rate_limit_info":{"status":"blocked","resetsAt":1782094800,"rateLimitType":"five_hour"}}\n'
        "You've hit your session limit · resets 3:45pm\n"
    )
    sig = rate_limit.detect(mixed, now=NOW)
    assert sig and sig.reset_at == datetime.fromtimestamp(1782094800)


def test_detect_structured_blocked_no_resets():
    ev = '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","rateLimitType":"weekly"}}'
    sig = rate_limit.detect(ev, now=NOW)
    assert sig is not None and sig.reset_at is None


# ============ 1b. seconds_until ============
_CFG = WaitConfig(default_wait=3600, max_wait=6 * 3600, buffer=60, max_cycles=6)


def test_seconds_until_future_adds_buffer():
    sig = RateLimitSignal(reset_at=datetime(2026, 6, 21, 13, 10, 0), message="x")  # +600s
    assert rate_limit.seconds_until(sig, now=NOW, cfg=_CFG) == 600 + 60


def test_seconds_until_past_returns_buffer():
    sig = RateLimitSignal(reset_at=datetime(2026, 6, 21, 12, 0, 0), message="x")  # 已過
    assert rate_limit.seconds_until(sig, now=NOW, cfg=_CFG) == 60


def test_seconds_until_unknown_uses_default():
    sig = RateLimitSignal(reset_at=None, message="x")
    assert rate_limit.seconds_until(sig, now=NOW, cfg=_CFG) == 3600


def test_seconds_until_capped_by_max_wait():
    sig = RateLimitSignal(reset_at=datetime(2026, 6, 25, 13, 0, 0), message="x")  # 數天後
    assert rate_limit.seconds_until(sig, now=NOW, cfg=_CFG) == 6 * 3600


def test_humanize():
    assert rate_limit.humanize(7945) == "2h12m"
    assert rate_limit.humanize(500) == "8m20s"
    assert rate_limit.humanize(45) == "45s"


# ============ 2. async 路徑：AgentRunner.run ============
class _ScriptRunner(AgentRunner):
    """跑一段 python（每次 spawn 是新子程序，跨輪狀態靠 cwd 內的計數檔）。"""
    name = "rl-script"

    def __init__(self, body: str):
        self._body = body

    def build_argv(self, *, cwd: str, prompt: str) -> list[str]:
        return [sys.executable, "-c", self._body]

    def is_available(self) -> bool:
        return True

    # 用真 detect（固定 now），確保走的是與 production 同一條偵測
    def _detect_rate_limit(self, output: str):
        return rate_limit.detect(output, now=NOW)


# 第 1 次 spawn：吐限流 result + exit 1；第 2 次起：成功 exit 0。靠 cwd/_rl_counter 跨輪計數。
_LIMIT_THEN_OK = (
    "import sys, os\n"
    "p = os.path.join(os.getcwd(), '_rl_counter')\n"
    "n = 0\n"
    "try:\n"
    "    n = int(open(p).read() or '0')\n"
    "except Exception:\n"
    "    n = 0\n"
    "open(p, 'w').write(str(n + 1))\n"
    "sys.stdin.read()\n"
    "if n == 0:\n"
    "    print('{\"type\":\"result\",\"is_error\":true,\"result\":\"You hit your session limit · resets 3:45pm\"}')\n"
    "    sys.exit(1)\n"
    "print('done ok')\n"
    "sys.exit(0)\n"
)

# 永遠吐限流 + exit 1（測續跑封頂）
_ALWAYS_LIMIT = (
    "import sys\n"
    "sys.stdin.read()\n"
    "print('{\"type\":\"result\",\"is_error\":true,\"result\":\"usage limit reached, resets 3:45pm\"}')\n"
    "sys.exit(1)\n"
)


def _drive(runner, *, cwd, sleep_returns=False, monkeypatch=None, timeout=10):
    """跑 runner.run，覆寫 _sleep 不真的睡（回 sleep_returns：False=睡滿、True=被取消）。
    回 (result, logs, events, waits)。"""
    logs: list[str] = []
    events: list = []
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)
        return sleep_returns

    runner._sleep = fake_sleep  # type: ignore[assignment]

    async def main():
        return await runner.run(cwd=cwd, prompt="hi", timeout=timeout,
                                on_log=logs.append, on_event=events.append)

    return asyncio.run(main()), logs, events, waits


def test_async_waits_then_resumes_to_success(tmp_path):
    runner = _ScriptRunner(_LIMIT_THEN_OK)
    res, logs, events, waits = _drive(runner, cwd=str(tmp_path))
    assert res.ok and res.exit_code == 0
    assert len(waits) == 1                                  # 等待一次
    starts = [e for e in events if e.get("type") == "start"]
    assert len(starts) == 2                                 # spawn 兩次（限流 + 續跑）
    assert any(e.get("type") == "rate_limit_wait" for e in events)
    assert any(e.get("type") == "rate_limit_resume" for e in events)
    assert any("自動續跑" in l for l in logs) and any("續跑中" in l for l in logs)


def test_async_resume_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("LODESTAR_RATELIMIT_MAX_CYCLES", "2")
    runner = _ScriptRunner(_ALWAYS_LIMIT)
    res, logs, events, waits = _drive(runner, cwd=str(tmp_path))
    assert not res.ok                                       # 始終受限 → 最終失敗
    starts = [e for e in events if e.get("type") == "start"]
    assert len(starts) == 3                                 # max_cycles(2) + 1
    assert len(waits) == 2                                  # 只在前兩輪之間等待
    assert any("停止等待" in l for l in logs)


def test_async_cancel_during_wait(tmp_path):
    runner = _ScriptRunner(_LIMIT_THEN_OK)
    res, logs, events, waits = _drive(runner, cwd=str(tmp_path), sleep_returns=True)
    assert res.cancelled                                    # 等待中被取消 → cancelled
    starts = [e for e in events if e.get("type") == "start"]
    assert len(starts) == 1                                 # 只跑了第一輪，未續跑
    assert not any(e.get("type") == "rate_limit_resume" for e in events)


def test_async_non_limit_failure_not_retried(tmp_path):
    runner = _ScriptRunner("import sys; sys.stdin.read(); print('boom'); sys.exit(3)")
    res, logs, events, waits = _drive(runner, cwd=str(tmp_path))
    assert not res.ok and res.exit_code == 3
    assert waits == []                                      # 非限流 → 不等待、不續跑
    assert len([e for e in events if e.get("type") == "start"]) == 1


# ---- base _sleep 的可取消性（真睡，但極短） ----
def test_base_sleep_full_duration_returns_false():
    runner = _ScriptRunner(_LIMIT_THEN_OK)

    async def main():
        runner._cancel_requested = False
        runner._cancel_event = asyncio.Event()
        return await runner._sleep(0.01)

    assert asyncio.run(main()) is False


def test_base_sleep_interrupted_by_cancel():
    runner = _ScriptRunner(_LIMIT_THEN_OK)

    async def main():
        runner._cancel_requested = False
        runner._cancel_event = asyncio.Event()
        runner._proc = None
        task = asyncio.ensure_future(runner._sleep(30))
        await asyncio.sleep(0.05)
        await runner.cancel()                               # set event → 喚醒睡眠
        return await task

    assert asyncio.run(main()) is True


# ============ 3. sync 路徑：claude_cli._invoke ============
class _FakeProc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_sync_invoke_waits_then_succeeds(monkeypatch):
    from plugins.builtin_models import claude_cli

    calls = {"n": 0}
    waits: list[float] = []

    def fake_run(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeProc(1, stdout=_result_err("usage limit reached, resets 3:45pm"))
        return _FakeProc(0, stdout='{"type":"assistant","message":{"content":[{"type":"text","text":"HELLO"}]}}')

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_cli, "_sleep", lambda s: waits.append(s))

    out = claude_cli._invoke("prompt")
    assert out == "HELLO"
    assert calls["n"] == 2 and len(waits) == 1


def test_sync_invoke_non_limit_raises(monkeypatch):
    from plugins.builtin_models import claude_cli

    monkeypatch.setattr(claude_cli.subprocess, "run",
                        lambda cmd, **kw: _FakeProc(2, stderr="compile error"))
    monkeypatch.setattr(claude_cli, "_sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="exited 2"):
        claude_cli._invoke("prompt")


def test_sync_invoke_normal_unaffected(monkeypatch):
    from plugins.builtin_models import claude_cli

    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        return _FakeProc(0, stdout='{"type":"assistant","message":{"content":[{"type":"text","text":"OK"}]}}')

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_cli, "_sleep", lambda s: pytest.fail("不該等待"))
    assert claude_cli._invoke("p") == "OK"
    assert calls["n"] == 1


def test_extract_error_from_stdout_result():
    from plugins.builtin_models import claude_cli
    stdout = (
        '{"type":"system","subtype":"init"}\n'
        '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"context limit exceeded"}\n'
    )
    assert claude_cli._extract_error(stdout) == "context limit exceeded"


def test_extract_error_api_retry():
    from plugins.builtin_models import claude_cli
    stdout = '{"type":"system","subtype":"api_retry","attempt":1,"error_status":529,"error":"overloaded"}'
    assert "529" in claude_cli._extract_error(stdout) and "overloaded" in claude_cli._extract_error(stdout)


def test_invoke_nonzero_surfaces_stdout_error(monkeypatch):
    # 回歸：claude exit 1、stderr 空、真正原因在 stdout → RuntimeError 要帶得出原因（非空白）
    from plugins.builtin_models import claude_cli
    stdout = '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"boom from stdout"}'
    monkeypatch.setattr(claude_cli.subprocess, "run",
                        lambda cmd, **kw: _FakeProc(1, stdout=stdout, stderr=""))
    monkeypatch.setattr(claude_cli, "_sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="boom from stdout"):
        claude_cli._invoke("p")


def test_sync_invoke_cap_then_raises(monkeypatch):
    from plugins.builtin_models import claude_cli

    monkeypatch.setenv("LODESTAR_RATELIMIT_MAX_CYCLES", "2")
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        return _FakeProc(1, stdout=_result_err("usage limit reached, resets 3:45pm"))

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_cli, "_sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="用量上限"):
        claude_cli._invoke("p")
    assert calls["n"] == 3                                   # max_cycles(2) + 1
