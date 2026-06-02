"""專案總結（唯讀 Flight Log 資料源）。

把三處本來各自為政的紀錄縫成一份時間軸 + story 表 + 總計：
  - 階段遙測：stage_status（狀態）、stage_events（generate/refine/approve 時間軸）、
    harness_runs（prd/架構/stories 的 agent 執行起訖；其 stage 名 specify/design/deliver 需映回 workflow id）。
  - implement：impl_batches / impl_sessions / impl_runs（逐 story、逐 role、重試輪、MR）。
  - 花費：impl_usage（從 log 解析的 token / 成本）。

純讀、純聚合，不寫不改。順手把「session 已終局但 run 還掛 running」的孤兒列標成 orphaned
（每次 server 重啟殘留的假 running），讓畫面不再誤導。
"""
from __future__ import annotations

from typing import Optional

from async_runtime import impl_usage
from persistence.dal import connect

# harness_runs.stage（specify/design/deliver）→ workflow stage_id
_HARNESS_TO_STAGE = {"specify": "prd", "design": "architecture", "deliver": "stories"}
_PRE_IMPL_STAGES = ("prd", "architecture", "stories")
_TERMINAL = ("succeeded", "failed", "cancelled")


def _stage_section(conn, thread_id: str) -> list[dict]:
    status = {r["stage_id"]: r["status"]
              for r in conn.execute("SELECT stage_id, status FROM stage_status WHERE thread_id = ?",
                                    (thread_id,)).fetchall()}
    events: dict[str, list] = {}
    for e in conn.execute(
            "SELECT stage_id, event_type, created_at FROM stage_events WHERE thread_id = ? ORDER BY created_at",
            (thread_id,)).fetchall():
        events.setdefault(e["stage_id"], []).append({"event": e["event_type"], "at": e["created_at"]})
    runs: dict[str, list] = {}
    for r in conn.execute(
            "SELECT stage, operation, status, started_at, ended_at FROM harness_runs WHERE thread_id = ?",
            (thread_id,)).fetchall():
        sid = _HARNESS_TO_STAGE.get(r["stage"], r["stage"])
        runs.setdefault(sid, []).append(dict(r))

    out = []
    for sid in _PRE_IMPL_STAGES:
        evs, rns = events.get(sid, []), runs.get(sid, [])
        secs = sum((x["ended_at"] - x["started_at"]) for x in rns if x["ended_at"] and x["started_at"])
        starts = [e["at"] for e in evs] + [x["started_at"] for x in rns if x["started_at"]]
        ends = [e["at"] for e in evs] + [x["ended_at"] for x in rns if x["ended_at"]]
        out.append({
            "stage_id": sid,
            "status": status.get(sid),
            "first_at": min(starts) if starts else None,
            "last_at": max(ends) if ends else None,
            "agent_runs": len(rns),
            "agent_seconds": round(secs, 1),
            "regens": sum(1 for e in evs if e["event"] in ("generate", "refine")),
            "events": evs,
        })
    return out


def _implement_section(conn, thread_id: str) -> dict:
    batches = [dict(r) for r in conn.execute(
        "SELECT batch_id, status, total, mode, auto_merge, created_at, updated_at "
        "FROM impl_batches WHERE thread_id = ? ORDER BY batch_id", (thread_id,)).fetchall()]
    sessions = [dict(r) for r in conn.execute(
        "SELECT session_id, batch_id, story_key, title, status, pr_url, created_at, updated_at, error_message "
        "FROM impl_sessions WHERE thread_id = ? ORDER BY session_id", (thread_id,)).fetchall()]
    runs_by_session: dict[int, list] = {}
    for r in conn.execute(
            "SELECT r.run_id, r.session_id, r.dispatch_role, r.attempt, r.status, r.started_at, r.ended_at "
            "FROM impl_runs r JOIN impl_sessions s ON r.session_id = s.session_id "
            "WHERE s.thread_id = ? ORDER BY r.run_id", (thread_id,)).fetchall():
        runs_by_session.setdefault(r["session_id"], []).append(dict(r))
    usage_map = impl_usage.usage_by_session(thread_id)

    # 按 story_key 去重：同一 story 被多個 batch 重跑過，取「最新 session（最大 session_id）」為現況；
    # 成本/嘗試數跨所有 session 加總（重跑也是花費）。story_key 為空者各自獨立（用 session_id 當鍵）。
    by_key: dict[str, list] = {}
    for s in sessions:
        key = s["story_key"] or f"#sid{s['session_id']}"
        by_key.setdefault(key, []).append(s)

    stories = []
    for key, group in by_key.items():
        group.sort(key=lambda s: s["session_id"])
        current = group[-1]                                   # 最新一次 = 現況
        sruns = runs_by_session.get(current["session_id"], [])
        terminal = current["status"] in _TERMINAL
        starts = [x["started_at"] for x in sruns if x["started_at"]]
        ends = [x["ended_at"] for x in sruns if x["ended_at"]]
        roles = []
        for x in sruns:
            st = x["status"]
            if st in ("running", "pending") and terminal:
                st = "orphaned"   # session 已終局但 run 沒收尾 = 重啟殘留的假 running
            roles.append({
                "role": x["dispatch_role"], "attempt": x["attempt"], "status": st,
                "seconds": round(x["ended_at"] - x["started_at"], 1)
                if x["ended_at"] and x["started_at"] else None,
            })
        cost = round(sum((usage_map.get(s["session_id"], {}).get("cost_usd", 0.0)) for s in group), 4)
        tokens = sum((usage_map.get(s["session_id"], {}).get("total_tokens", 0)) for s in group)
        stories.append({
            "story_key": current["story_key"] or "", "title": current["title"],
            "session_id": current["session_id"], "batch_id": current["batch_id"],
            "status": current["status"], "pr_url": current["pr_url"] or "",
            "error_message": current["error_message"] or "",
            "duration_sec": round(max(ends) - min(starts), 1) if starts and ends else None,
            "attempts": max((x["attempt"] for x in sruns), default=0),     # 現況這次的 RD 重做輪數
            "batch_runs": len(group),                                      # 被幾個 batch 重跑過
            "roles": roles,
            "cost_usd": cost, "total_tokens": tokens,                      # 跨所有重跑加總
        })
    stories.sort(key=_story_sort_key)
    total_runs = sum(len(v) for v in runs_by_session.values())   # 所有 session/重試的 run 總數
    return {"batches": batches, "stories": stories, "total_runs": total_runs}


def _story_sort_key(s: dict):
    """依 story 編號排序（1.2 在 1.10 前）；無編號殿後。"""
    key = s.get("story_key") or ""
    parts = key.split(".")
    try:
        return (0, [int(p) for p in parts])
    except ValueError:
        return (1, [], key)


def _span(values: list[Optional[float]]) -> tuple[Optional[float], Optional[float]]:
    vals = [v for v in values if v]
    return (min(vals), max(vals)) if vals else (None, None)


def project_summary(thread_id: str) -> dict:
    with connect() as conn:
        stages = _stage_section(conn, thread_id)
        implement = _implement_section(conn, thread_id)
    usage = impl_usage.thread_usage(thread_id)

    # 整體跨度：stage 時間軸 + impl batch 起訖
    impl_times = []
    for b in implement["batches"]:
        impl_times += [b.get("created_at"), b.get("updated_at")]
    first, last = _span([s["first_at"] for s in stages] + [s["last_at"] for s in stages] + impl_times)

    stories = implement["stories"]
    return {
        "thread_id": thread_id,
        "stages": stages,
        "implement": implement,
        "usage": usage,
        "totals": {
            "first_activity": first,
            "last_activity": last,
            "span_sec": round(last - first, 1) if first and last else None,
            "stories_total": len(stories),                       # 去重後的 unique story 數
            "stories_with_mr": sum(1 for s in stories if s["pr_url"]),
            "stories_failed": sum(1 for s in stories if s["status"] == "failed"),
            "agent_runs": sum(s["agent_runs"] for s in stages) + implement["total_runs"],
            "cost_usd": usage["cost_usd"],
            "total_tokens": usage["total_tokens"],
        },
    }
