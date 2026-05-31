"""agy-cli ModelAdapter（plugin 內部實作）。

agy CLI 的非互動模式：

    agy --print "<prompt>"

- `--print`（= `-p` / `--prompt`）：跑單一 prompt 非互動、直接印回覆到 stdout。
- prompt 以「命令列引數」傳入（Go-style flag，非 stdin）；一般 prompt 長度遠低於 ARG_MAX。
- 不開 `--add-dir` / `--dangerously-skip-permissions`：當作純文字生成器，不讓它碰檔案（安全）。
"""
from __future__ import annotations

import logging
import shutil
import subprocess

from plugin_api import ModelAdapter

TIMEOUT_SECONDS = 600   # agy --print 預設 5m wait，放寬到 10m

_log = logging.getLogger("plugin.agy_cli")


def _invoke(prompt: str, *, allowed_tools: tuple[str, ...] = ()) -> str:
    if allowed_tools:
        _log.warning("agy-cli 是純文字生成器，忽略 allowed_tools=%s"
                     "（需要工具的 agent 請改綁支援工具的 model，如 claude-cli）", allowed_tools)
    cmd = ["agy", "--print", prompt]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("agy CLI 不在 PATH") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(f"agy-cli exited {proc.returncode}: {stderr[:500]}")
    return proc.stdout.strip()


def _is_available() -> bool:
    return shutil.which("agy") is not None


agy_cli_adapter = ModelAdapter(
    model_choice="agy-cli",
    invoke=_invoke,
    is_available=_is_available,
    description="agy CLI (agy --print, single-shot non-interactive).",
    max_context_tokens=200_000,
    prompt_budget_tokens=180_000,
    response_budget_tokens=8_000,
    supports_multimodal=False,
)
