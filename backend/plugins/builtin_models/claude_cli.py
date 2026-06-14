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

import json
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


# 讀碼 stage 在 workspace 存在時隱含補的唯讀工具（讓 model 能探索既有 codebase）。
_WORKSPACE_TOOLS = ("Read", "Grep", "Glob")


def _tool_flags(allowed_tools: tuple[str, ...], workspace_dir: str = "") -> list[str]:
    """組工具相關 flags。

    - effective = allowed_tools（agent 宣告）∪ 附件隱含的 Read（uploads dir 存在時）
      ∪ workspace 隱含的 Read/Grep/Glob（workspace_dir 存在時）。
    - effective 為空 → 回 []（純文字模式，不帶 --allowedTools）。
    - 非空 → --allowedTools <逗號串>；uploads / workspace 各自存在時 --add-dir 該目錄。

    回歸守門：即使 agent.tools=()，只要 uploads / workspace 存在就補對應工具，否則 prompt 內
    「用 Read 讀附件 / codebase」的指令會因 model 沒有該能力而失效。
    workspace（既有 repo）是唯讀讀碼，不主動寫；acceptEdits 只在讀附件路徑沿用既有行為。
    """
    uploads = os.environ.get(_UPLOADS_ENV)
    uploads_path = Path(uploads) if uploads else None
    uploads_ok = bool(uploads_path and uploads_path.exists())

    ws_path = Path(workspace_dir) if workspace_dir else None
    ws_ok = bool(ws_path and ws_path.exists())

    tools: list[str] = list(allowed_tools)
    if uploads_ok and "Read" not in tools:
        tools.append("Read")
    if ws_ok:
        for t in _WORKSPACE_TOOLS:
            if t not in tools:
                tools.append(t)

    if not tools:
        return []

    flags = ["--allowedTools", ",".join(tools)]
    if uploads_ok and "Read" in tools:
        flags += ["--add-dir", str(uploads_path)]
    if ws_ok:
        flags += ["--add-dir", str(ws_path)]
    if uploads_ok and "Read" in tools:
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


def _build_cmd(allowed_tools: tuple[str, ...] = (), workspace_dir: str = "") -> list[str]:
    # stream-json + --verbose：大型輸出（如完整 stories）模型回應會跨多個 assistant 輪次
    # （超過單輪 max output tokens 自動續寫）。`--output-format text` 只回「最後一輪」→ 前段
    # （標題 + 前面 Epic/Story）整段遺失（症狀：implement 默默從中段 story 開始）。改 stream-json
    # 後 _parse_stream_json 串接「所有」assistant 輪次的文字，重建完整輸出。
    return [
        "claude", "-p",
        "--output-format", "stream-json",
        "--verbose",                                 # -p 下 stream-json 需 --verbose
        "--no-session-persistence",
        "--append-system-prompt", _INLINE_DIRECTIVE,
        *_tool_flags(allowed_tools, workspace_dir),
        "--disallowedTools", *_NO_DELEGATE_TOOLS,    # 置於末端：variadic 不誤吞後續 flag
    ]


def _parse_stream_json(stdout: str) -> str:
    """從 claude-cli stream-json（JSONL）串接「所有」assistant 輪次的 text block。

    跨多輪的大型輸出，逐輪 assistant message 的 content[].text 依序接起來＝完整成品。
    非 JSON 行 / 非 assistant 事件略過；tool_use block 不取（只要文字）。
    回空字串 → caller（harness / stage）以「空輸出」處理。"""
    parts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if ev.get("type") != "assistant":
            continue
        for blk in ev.get("message", {}).get("content", []):
            if isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(blk.get("text", ""))
    return "".join(parts)


def _invoke(prompt: str, *, allowed_tools: tuple[str, ...] = (), workspace_dir: str = "") -> str:
    """同步呼叫 claude CLI（stream-json）。回串接後完整輸出；non-zero exit → RuntimeError。
    allowed_tools：agent 宣告的工具（如 Read / Bash）；附件隱含的 Read 由 _tool_flags 自動補。
    workspace_dir：既有 repo clone 絕對路徑（讀碼 stage 用），非空時 --add-dir + 補 Read/Grep/Glob。"""
    cmd = _build_cmd(allowed_tools, workspace_dir)
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
    return _parse_stream_json(proc.stdout)


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
