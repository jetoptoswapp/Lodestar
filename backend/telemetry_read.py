"""harness 遙測的唯讀分析（把只寫不讀的 harness_runs / harness_validation_results 變可度量）。

host 層模組（非 plugin），透過 persistence.dal.connect 讀庫（與 harness_runner._record_run 同管道）。
指標供 /api/telemetry/harness 與 eval 報表使用，是「驗證會擋 → 數據可度量 → 反調」閉環的讀側。

主 run vs judge run：judge model call 記成 operation='judge_*'，不計入主 run 指標（避免污染
fix-loop 迭代數與 needs_revision 率），但單獨計數於 judge_runs。
"""
from __future__ import annotations

from persistence.dal import connect


def _is_judge(op: str) -> bool:
    return op.startswith("judge_")


def harness_metrics(*, since: float = 0.0, stage: str = "") -> dict:
    """聚合 harness 遙測指標。since=epoch 秒（0=全部）；stage 篩選 telemetry_stage。"""
    where = "WHERE started_at >= ?"
    params: list = [since]
    if stage:
        where += " AND stage = ?"
        params.append(stage)
    with connect() as conn:
        runs = [dict(r) for r in conn.execute(
            f"SELECT run_id, operation, status, started_at, ended_at, parent_run_id "
            f"FROM harness_runs {where}", params).fetchall()]
        vals = [dict(r) for r in conn.execute(
            f"SELECT v.validator AS validator, v.severity AS severity "
            f"FROM harness_validation_results v "
            f"JOIN harness_runs r ON v.run_id = r.run_id {where}", params).fetchall()]

    main = [r for r in runs if not _is_judge(r["operation"])]
    judge = [r for r in runs if _is_judge(r["operation"])]

    by_status: dict[str, int] = {}
    for r in main:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    # fix-loop 鏈：root = 主 run 且 parent_run_id 為 NULL；鏈長 = 該 root 往下的主 run 數。
    children: dict = {}
    for r in main:
        children.setdefault(r["parent_run_id"], []).append(r["run_id"])
    roots = [r for r in main if r["parent_run_id"] is None]

    def _chain_len(root_id: str) -> int:
        n, frontier = 1, [root_id]
        while frontier:
            nxt: list = []
            for rid in frontier:
                nxt.extend(children.get(rid, []))
            n += len(nxt)
            frontier = nxt
        return n

    lengths = [_chain_len(r["run_id"]) for r in roots]

    val_agg: dict = {}
    for v in vals:
        agg = val_agg.setdefault(v["validator"], {"warn": 0, "fail": 0})
        if v["severity"] in agg:
            agg[v["severity"]] += 1

    lat = sorted((r["ended_at"] - r["started_at"]) * 1000.0
                 for r in main if r["ended_at"] and r["started_at"])

    def _pct(p: float) -> float:
        if not lat:
            return 0.0
        idx = min(len(lat) - 1, int(p * len(lat)))
        return round(lat[idx], 1)

    n_main = len(main)
    return {
        "total_runs": n_main,
        "by_status": by_status,
        "needs_revision_rate": round(by_status.get("needs_revision", 0) / n_main, 3) if n_main else 0.0,
        "fail_rate": round(by_status.get("failed", 0) / n_main, 3) if n_main else 0.0,
        "judge_runs": len(judge),
        "fix_loop": {
            "chains": len(roots),
            "avg_iterations": round(sum(lengths) / len(roots), 2) if roots else 0.0,
            "max_iterations": max(lengths) if lengths else 0,
        },
        "validators": [{"validator": k, **val_agg[k]} for k in sorted(val_agg)],
        "latency_ms": {"p50": _pct(0.5), "p95": _pct(0.95)},
    }


def harness_run_tree(run_id: str) -> list[dict]:
    """還原一條 fix-loop 鏈（從 run_id 上溯至 root，再 BFS 收所有後代，含 judge_* run）。
    供 debug / 前端展開「這次生成重試了幾輪、judge 怎麼判」。"""
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT run_id, operation, status, model_choice, parent_run_id, "
            "started_at, ended_at FROM harness_runs").fetchall()]
    by_id = {r["run_id"]: r for r in rows}
    cur = by_id.get(run_id)
    if cur is None:
        return []
    while cur["parent_run_id"] and cur["parent_run_id"] in by_id:
        cur = by_id[cur["parent_run_id"]]
    children: dict = {}
    for r in rows:
        children.setdefault(r["parent_run_id"], []).append(r)
    tree: list[dict] = []
    frontier = [cur]
    while frontier:
        nxt: list = []
        for r in frontier:
            tree.append(r)
            nxt.extend(sorted(children.get(r["run_id"], []), key=lambda x: x["started_at"]))
        frontier = nxt
    return tree
