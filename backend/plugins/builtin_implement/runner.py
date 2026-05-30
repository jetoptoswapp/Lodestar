"""具體 AgentRunner —— prompt 一律由 base run() 經 stdin 餵入（故 build_argv 不含 prompt 本體）。

- ClaudeCliRunner：agentic 模式跑 claude，在工作目錄寫 code（真實執行；需 PATH 有 claude）。
- MockRunner：安全 dry-run，跑本機 python 子程序模擬一次實作，永遠 exit 0。
  用於「全機制 + mock 驗證」——驅動 / 串流 / 持久化 / hook 全跑真實路徑，但不碰外部。
"""
from __future__ import annotations

import shutil
import sys

from plugin_api import AgentRunner


class ClaudeCliRunner(AgentRunner):
    """以 claude CLI 的 agentic 模式執行：可在 cwd 編輯檔案（acceptEdits 免互動核可）。

    安全（坎3）：--disallowedTools 在「危險操作真正發生的那層」（agent 的 tool-call）擋下
    git push / remote / gh pr —— 推送與開 PR 一律由 host 於審批後執行（見 async_runtime.github_pr），
    不交給 agent。DenyProtectedBranchHook 留作 argv 層的第二道（縱深防禦）。
    matcher 語法依 claude CLI（已實測支援 `--disallowedTools <tools...>`）。
    """
    name = "claude-cli"

    # agent 一律不得自行推送 / 改 remote / 開 PR（這些由 host 受控執行）
    DISALLOWED_TOOLS = (
        "Bash(git push:*)",
        "Bash(git remote:*)",
        "Bash(gh pr:*)",
        "Bash(gh repo:*)",
    )

    def build_argv(self, *, cwd: str, prompt: str) -> list[str]:
        # prompt 走 stdin；agentic 模式允許在 cwd 讀寫，stream-json 便於前端逐事件呈現。
        return [
            "claude", "-p",
            "--output-format", "stream-json", "--verbose",
            "--permission-mode", "acceptEdits",
            "--add-dir", cwd,
            "--disallowedTools", *self.DISALLOWED_TOOLS,
        ]

    def is_available(self) -> bool:
        return shutil.which("claude") is not None


class MockRunner(AgentRunner):
    """安全 mock：讀 stdin（prompt）→ 印幾行進度 → exit 0。不依賴外部、不寫任何檔案。"""
    name = "mock"

    _SCRIPT = (
        "import sys\n"
        "data = sys.stdin.read()\n"
        "first = (data.splitlines() or [''])[0][:80]\n"
        "print('[mock] received prompt:', first, flush=True)\n"
        "print('[mock] working dir:', sys.argv[1], flush=True)\n"
        "print('[mock] simulated edits: 0 files (dry-run)', flush=True)\n"
        "print('[mock] done', flush=True)\n"
    )

    def build_argv(self, *, cwd: str, prompt: str) -> list[str]:
        return [sys.executable, "-c", self._SCRIPT, cwd]

    def is_available(self) -> bool:
        return True
