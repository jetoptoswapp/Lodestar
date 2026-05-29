"""builtin_implement —— M5 async 實作 agent 的 plugin 側貢獻。

只 import plugin_api（AgentRunner / ToolHook 契約），**絕不** import host 的
async_runtime / persistence / workflow_engine（AST guard test 強制）。

貢獻：
- runner「claude-cli」：agentic 模式跑 claude，在工作目錄寫 code（真實執行，需 PATH 有 claude）
- runner「mock」：安全 dry-run，跑一個本機 python 子程序模擬實作（驗證機制用，不碰外部）
- tool hook「deny_protected_branch」：pre_run 擋對受保護分支的危險 git 操作
- tool hook「redact_secrets」：on_log_chunk 把串流輸出裡的疑似秘密塗黑
"""
