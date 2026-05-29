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

    注意：真實寫 code / 開 PR 屬使用者明確排除的範圍；本 runner 提供正確 argv 與
    is_available 檢查，實際長跑留待使用者授權真實環境時啟用。
    """
    name = "claude-cli"

    def build_argv(self, *, cwd: str, prompt: str) -> list[str]:
        # prompt 走 stdin；agentic 模式允許在 cwd 讀寫，stream-json 便於前端逐事件呈現。
        return [
            "claude", "-p",
            "--output-format", "stream-json", "--verbose",
            "--permission-mode", "acceptEdits",
            "--add-dir", cwd,
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
