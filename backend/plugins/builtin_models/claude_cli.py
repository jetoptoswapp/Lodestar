"""claude-cli ModelAdapter（plugin 內部實作）。

subprocess 一次性呼叫 Claude Code CLI（non-interactive print mode）：
    claude -p --output-format text --no-session-persistence < prompt

不加 `--bare`：bare mode 只讀 ANTHROPIC_API_KEY / apiKeyHelper，跳過 OAuth keychain；
本機開發者多以 keychain 登入，故走非 bare 路徑。需要極簡 stateless 行為時，
可改在 plugin 設定提供 ANTHROPIC_API_KEY env + --bare（M3 再做設定 UI）。
"""
from __future__ import annotations

import shutil
import subprocess

from plugin_api import ModelAdapter

TIMEOUT_SECONDS = 240  # PRD generate 通常 30–60s，留充裕 headroom


def _invoke(prompt: str) -> str:
    """同步呼叫 claude CLI。回 stdout 字串；non-zero exit → RuntimeError。"""
    cmd = ["claude", "-p", "--output-format", "text", "--no-session-persistence"]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("claude CLI 不在 PATH") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(
            f"claude-cli exited {proc.returncode}: {stderr[:500]}"
        )
    return proc.stdout


def _is_available() -> bool:
    return shutil.which("claude") is not None


claude_cli_adapter = ModelAdapter(
    model_choice="claude-cli",
    invoke=_invoke,
    is_available=_is_available,
    description="Anthropic Claude via Claude Code CLI (non-interactive print mode).",
    max_context_tokens=200_000,    # Claude Sonnet / Opus 4.x context window
    prompt_budget_tokens=180_000,
    response_budget_tokens=8_000,
)
