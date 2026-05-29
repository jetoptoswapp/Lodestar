"""背景 asyncio task 強引用登記表。

spec §11 血淚紀律：`asyncio.create_task` 的回傳值若沒有人持有強引用，
event loop 只保有 weak ref —— 長 await 期間 task 可能被 GC 靜默回收、
實作 agent 半路無聲消失。對策：module-level set 持有強引用，
`add_done_callback` 在完成時 discard（避免永久洩漏）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Optional

_log = logging.getLogger("async_runtime.tasks")

# 模組級強引用：task 在跑完前不會被 GC。完成後由 _on_done discard。
_TASKS: "set[asyncio.Task]" = set()


def spawn(coro: Awaitable, *, name: Optional[str] = None) -> asyncio.Task:
    """建立背景 task 並登記強引用。必須在 running loop 內呼叫。"""
    task = asyncio.create_task(coro, name=name)  # type: ignore[arg-type]
    _TASKS.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: "asyncio.Task") -> None:
    _TASKS.discard(task)
    if task.cancelled():
        _log.info("background task %s cancelled", task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        _log.error("background task %s failed: %r", task.get_name(), exc)


def active_count() -> int:
    """目前在跑的背景 task 數（測試 / 健康檢查用）。"""
    return len(_TASKS)


async def cancel_all() -> None:
    """取消所有背景 task 並等待收斂（app shutdown 用）。"""
    tasks = list(_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
