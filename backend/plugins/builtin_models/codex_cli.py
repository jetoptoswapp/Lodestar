"""codex-cli ModelAdapter（plugin 內部實作）。

OpenAI Codex CLI 的非互動模式：

    codex exec --skip-git-repo-check --sandbox read-only --ephemeral --color never

- `exec`：non-interactive，prompt 由 stdin 餵入；最終答覆走 stdout，啟動 preamble 走 stderr。
- `--sandbox read-only`：當作純文字生成器用，禁止寫檔（安全）；它仍可在 sandbox 內讀取。
- `--ephemeral`：不留 session 檔。
- `--skip-git-repo-check`：允許在非 git 目錄執行（在系統暫存目錄跑，避免碰專案）。

注意：codex 預設 reasoning effort 偏高，一次生成可能數十秒到數分鐘，故 timeout 放寬。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile

from plugin_api import ModelAdapter

TIMEOUT_SECONDS = 600


def _invoke(prompt: str) -> str:
    cmd = [
        "codex", "exec",
        "--skip-git-repo-check",
        "--sandbox", "read-only",
        "--ephemeral",
        "--color", "never",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=tempfile.gettempdir(),   # 中性工作目錄；read-only sandbox 不寫檔
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("codex CLI 不在 PATH") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(f"codex-cli exited {proc.returncode}: {stderr[:500]}")
    return proc.stdout.strip()


def _is_available() -> bool:
    return shutil.which("codex") is not None


codex_cli_adapter = ModelAdapter(
    model_choice="codex-cli",
    invoke=_invoke,
    is_available=_is_available,
    description="OpenAI Codex via codex CLI (codex exec, read-only sandbox, non-interactive).",
    max_context_tokens=200_000,
    prompt_budget_tokens=180_000,
    response_budget_tokens=8_000,
    supports_multimodal=False,   # 純文字生成模式（未開檔案 / 圖片工具）
)
