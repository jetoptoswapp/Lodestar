"""claude-cli ModelAdapter（plugin 內部實作）。

subprocess 一次性呼叫 Claude Code CLI（non-interactive print mode）：

    claude -p --output-format text --no-session-persistence \
           [--add-dir <uploads>] [--allowedTools <tools>] [--permission-mode acceptEdits]

`--allowedTools` 由 agent 宣告的 allowed_tools ∪ 附件隱含的 Read 決定（見 _tool_flags）；
兩者皆空則純文字模式、不帶工具。

M1.3 path-passing：env `LODESTAR_UPLOADS_DIR` 指向 host 統一的附件根目錄；存在時
_tool_flags 自動把 Read 併入工具集（即使 agent.tools=()），Claude 可直接讀附件原檔
（圖片走 native vision、PDF / DOCX 走 Read tool），不必本地 OCR / parse。env 未設時
退回最小 cmd，adapter 仍可生成（但無法讀附件原檔，仰賴 plugin 端的 inline fallback）。

不加 `--bare`：bare 模式跳過 OAuth keychain；本機開發者多以 keychain 登入，
故走非 bare 路徑。需 stateless 行為時，提供 ANTHROPIC_API_KEY env 走 --bare。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from plugin_api import ModelAdapter

# Read tool 可能多輪 → 比 240s 留更多 headroom（一張高解析圖片可能需 30–60s）
TIMEOUT_SECONDS = 360

_UPLOADS_ENV = "LODESTAR_UPLOADS_DIR"


def _tool_flags(allowed_tools: tuple[str, ...]) -> list[str]:
    """組工具相關 flags。

    - effective = allowed_tools（agent 宣告）∪ 附件隱含的 Read（uploads dir 存在時）。
    - effective 為空 → 回 []（純文字模式，不帶 --allowedTools）。
    - 非空 → --allowedTools <逗號串>；需讀附件（uploads 存在且含 Read）才加 --add-dir + acceptEdits。

    附件回歸守門：即使 agent.tools=()，只要 uploads dir 存在就補 Read，否則 prompt 內
    「用 Read 讀附件」的指令會因 model 沒有 Read 能力而失效。
    """
    uploads = os.environ.get(_UPLOADS_ENV)
    uploads_path = Path(uploads) if uploads else None
    uploads_ok = bool(uploads_path and uploads_path.exists())

    tools: list[str] = list(allowed_tools)
    if uploads_ok and "Read" not in tools:
        tools.append("Read")

    if not tools:
        return []

    flags = ["--allowedTools", ",".join(tools)]
    if uploads_ok and "Read" in tools:
        flags += ["--add-dir", str(uploads_path)]
    flags += ["--permission-mode", "acceptEdits"]
    return flags


def _build_cmd(allowed_tools: tuple[str, ...] = ()) -> list[str]:
    return [
        "claude", "-p",
        "--output-format", "text",
        "--no-session-persistence",
        *_tool_flags(allowed_tools),
    ]


def _invoke(prompt: str, *, allowed_tools: tuple[str, ...] = ()) -> str:
    """同步呼叫 claude CLI。回 stdout 字串；non-zero exit → RuntimeError。
    allowed_tools：agent 宣告的工具（如 Read / Bash）；附件隱含的 Read 由 _tool_flags 自動補。"""
    cmd = _build_cmd(allowed_tools)
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
    description="Anthropic Claude via Claude Code CLI (non-interactive + Read tool for attachments).",
    max_context_tokens=200_000,    # Claude Sonnet / Opus 4.x context window
    prompt_budget_tokens=180_000,
    response_budget_tokens=8_000,
    # M1.3：claude-cli + Read tool 已能吃 image / PDF / DOCX 原檔。
    # invoke_messages 保留 None：path-passing 走 prompt 字串列 path（README 寫明），
    # 未來若要原生 Anthropic Messages API multimodal，再寫一個 claude-api adapter。
    supports_multimodal=True,
)
