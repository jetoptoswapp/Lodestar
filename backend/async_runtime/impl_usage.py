"""implement 的 token / 成本聚合（唯讀）。

usage 不另存欄位——它早已夾在 impl_messages 的 claude-cli stream-json 裡（每個 run 一個終局
`result` 事件帶 total_cost_usd + usage）。這裡按 session / batch / thread 把這些 result 事件
解析加總，把「只寫不讀」的花費變成可查。只算 result（權威的每-run 累計），不疊加 assistant
逐回合 usage，避免重複計。LIKE '%cost_usd%' 先濾掉雜訊列（result 才有），少 parse 大量 log。
"""
from __future__ import annotations

import json

from persistence.dal import connect

_FIELDS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")


def _zero() -> dict:
    return {"cost_usd": 0.0, "result_events": 0,
            **{f: 0 for f in _FIELDS}, "total_tokens": 0}


def _accumulate(contents) -> dict:
    agg = _zero()
    for content in contents:
        if "cost_usd" not in content:
            continue
        try:
            o = json.loads(content)
        except (ValueError, TypeError):
            continue
        if o.get("type") != "result":
            continue
        agg["result_events"] += 1
        agg["cost_usd"] += o.get("total_cost_usd") or 0.0
        u = o.get("usage") or {}
        agg["input_tokens"] += u.get("input_tokens", 0) or 0
        agg["output_tokens"] += u.get("output_tokens", 0) or 0
        agg["cache_creation_tokens"] += u.get("cache_creation_input_tokens", 0) or 0
        agg["cache_read_tokens"] += u.get("cache_read_input_tokens", 0) or 0
    agg["total_tokens"] = sum(agg[f] for f in _FIELDS)
    agg["cost_usd"] = round(agg["cost_usd"], 4)
    return agg


def _usage_where(where: str, params: tuple) -> dict:
    sql = ("SELECT m.content FROM impl_messages m "
           "JOIN impl_runs r ON m.run_id = r.run_id "
           "JOIN impl_sessions s ON r.session_id = s.session_id "
           f"WHERE {where} AND m.content LIKE '%cost_usd%'")
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return _accumulate([row[0] for row in rows])


def session_usage(session_id: int) -> dict:
    """單一 session 的 token / 成本累計（含其所有 run / 重試輪）。"""
    return _usage_where("s.session_id = ?", (session_id,))


def batch_usage(batch_id: int) -> dict:
    """整個 batch（跨 story）的 token / 成本累計。"""
    return _usage_where("s.batch_id = ?", (batch_id,))


def usage_by_session(thread_id: str) -> dict:
    """一次查完整個 thread，回 {session_id: usage}（給總結頁逐 story 加總，避免 N 次查詢）。"""
    sql = ("SELECT r.session_id AS sid, m.content FROM impl_messages m "
           "JOIN impl_runs r ON m.run_id = r.run_id "
           "JOIN impl_sessions s ON r.session_id = s.session_id "
           "WHERE s.thread_id = ? AND m.content LIKE '%cost_usd%'")
    buckets: dict[int, list] = {}
    with connect() as conn:
        for row in conn.execute(sql, (thread_id,)).fetchall():
            buckets.setdefault(row["sid"], []).append(row["content"])
    return {sid: _accumulate(contents) for sid, contents in buckets.items()}


def thread_usage(thread_id: str) -> dict:
    """整個專案 implement 側的 token / 成本累計（所有 session、含重跑）。

    注意：只涵蓋 implement（claude-cli）；prd/架構/stories（harness 側）目前未記 usage，不在內。"""
    return _usage_where("s.thread_id = ?", (thread_id,))
