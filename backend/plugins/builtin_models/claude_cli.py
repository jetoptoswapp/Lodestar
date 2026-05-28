"""claude-cli ModelAdapter（plugin 內部實作）。

subprocess 一次性呼叫 Claude Code CLI（non-interactive print mode）：

    claude -p --output-format text --no-session-persistence \
           [--add-dir <uploads>] [--allowedTools Read] [--permission-mode acceptEdits]

M1.3 path-passing：env `LODESTAR_UPLOADS_DIR` 指向 host 統一的附件根目錄；
存在時 cmd 加上 Read 工具能力，Claude 可直接讀附件原檔（圖片走 native vision、
PDF / DOCX 走 Read tool），不必本地 OCR / parse。env 未設時退回最小 cmd，
adapter 仍可生成（但無法讀附件原檔，仰賴 plugin 端的 inline fallback）。

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


def _read_tool_flags() -> list[str]:
    """組 Read 工具相關 flags；uploads dir 未設或不存在 → 回空 list。"""
    uploads = os.environ.get(_UPLOADS_ENV)
    if not uploads:
        return []
    uploads_path = Path(uploads)
    if not uploads_path.exists():
        return []
    return [
        "--add-dir", str(uploads_path),
        "--allowedTools", "Read",
        "--permission-mode", "acceptEdits",
    ]


def _build_cmd() -> list[str]:
    return [
        "claude", "-p",
        "--output-format", "text",
        "--no-session-persistence",
        *_read_tool_flags(),
    ]


def _invoke(prompt: str) -> str:
    """同步呼叫 claude CLI。回 stdout 字串；non-zero exit → RuntimeError。"""
    cmd = _build_cmd()
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
