"""async_runtime —— M5 host 層：長時實作 agent 的編排 / 持久化 / 任務管理 / SSE。

設計鐵則（spec §2 第 ④ 條）：兩種 AI runtime 嚴格隔離。
- sync one-shot（HarnessRunner，stage 生成）走 plugin_api + harness_runner。
- async long-running（本套件，實作 agent）跑子程序、串流、可取消、fix-loop。

**plugin 永遠不得 import 本套件**（AST guard test 強制；見 tests/test_isolation.py）。
plugin 只看得到 plugin_api 裡的 AgentRunner / ToolHook 契約；具體 runner / hook
由 plugin 實作並 register，host 的 orchestrator 才把它們驅動起來。

本套件只被 host（app.py / endpoints）使用，可碰 persistence（經 dal.connect 單一連線入口）。
"""
from __future__ import annotations

from async_runtime import impl_dal, orchestrator, task_registry

__all__ = ["impl_dal", "orchestrator", "task_registry"]
