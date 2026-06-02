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

# 大型單次生成（如 stories 拆完整 Epic/Story + AC/Gherkin/估點，或大 PRD 的架構）opus 可能
# 跑 10 分鐘以上；Read tool 多輪 / 高解析圖片再加碼。前端 fetch 無 client timeout，等多久都收，
# 故放寬到 15 分鐘避免大專案生成被腰斬（症狀：stories TimeoutExpired(360) → 產出空）。
TIMEOUT_SECONDS = 900

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


# single-shot 產生器守門：禁止 claude 開子代理 / 啟動 workflow。
# 否則對「架構」這類大任務，claude（受使用者全域 CLAUDE.md「多用 subagent」影響）會嘗試派並行
# subagent / 動態 workflow → 非互動模式給不了核准 → 只回「等待核准」meta 訊息、產不出真正內容
# （症狀：architecture 跑完僅 162 chars、0 sections）。配合 system prompt 明示「直接 inline 寫出」。
_NO_DELEGATE_TOOLS = ("Task", "Agent", "Workflow")
_INLINE_DIRECTIVE = (
    "You are a single-shot document generator. Write the FULL requested artifact directly and inline "
    "in this reply. Do NOT spawn subagents or sub-tasks, do NOT create/launch/await any workflow, do "
    "NOT ask for approval or delegation — produce the complete deliverable now in your text output."
)


def _build_cmd(allowed_tools: tuple[str, ...] = ()) -> list[str]:
    return [
        "claude", "-p",
        "--output-format", "text",
        "--no-session-persistence",
        "--append-system-prompt", _INLINE_DIRECTIVE,
        *_tool_flags(allowed_tools),
        "--disallowedTools", *_NO_DELEGATE_TOOLS,   # 置於末端：variadic 不誤吞後續 flag
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
