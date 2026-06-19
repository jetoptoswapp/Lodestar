"""project_summary（Flight Log 資料源）去重語意。

重點：同一 story 被多個 batch 重跑時，「現況」要以成功那次為準——story 一旦 merge 就是 done，
晚於它的失敗多半是後續重跑「已 merge → 空 diff」而 failed，不該把現況蓋成 failed。
"""
from __future__ import annotations

import project_summary
from async_runtime import impl_dal
from persistence import dal


def _sess(thread, key, batch, status, pr=""):
    sid = impl_dal.create_session(thread_id=thread, title=f"Story {key} — x",
                                  target_repo="o/r", runner="mock", batch_id=batch, story_key=key)
    impl_dal.update_session(sid, status=status, pr_url=pr or None)
    return sid


def test_summary_prefers_succeeded_over_later_failed(tmp_db):
    dal.create_project("t", "demo")
    _sess("t", "5.1", 1, "failed")                          # 早期失敗
    _sess("t", "5.1", 2, "succeeded", "http://gl/mr/10")    # 成功並開 MR
    _sess("t", "5.1", 3, "failed")                          # 之後重跑「無變更」failed
    story = next(x for x in project_summary.project_summary("t")["implement"]["stories"]
                 if x["story_key"] == "5.1")
    assert story["status"] == "succeeded"                   # 不被晚的 failed 蓋掉
    assert story["pr_url"] == "http://gl/mr/10"
    assert story["batch_runs"] == 3                         # 仍計入全部重跑


def test_summary_totals_count_delivered(tmp_db):
    dal.create_project("t", "demo")
    _sess("t", "5.1", 1, "succeeded", "http://gl/mr/10")
    _sess("t", "5.1", 2, "failed")                          # 同 story 重跑失敗
    _sess("t", "9.9", 1, "failed")                          # 從未成功
    totals = project_summary.project_summary("t")["totals"]
    assert totals["stories_total"] == 2                     # 去重：5.1 + 9.9
    assert totals["stories_with_mr"] == 1                   # 只有 5.1 交付
    assert totals["stories_failed"] == 1                    # 9.9 仍 failed


def test_summary_failed_when_never_succeeded(tmp_db):
    dal.create_project("t", "demo")
    _sess("t", "9.9", 1, "failed")
    _sess("t", "9.9", 2, "cancelled")
    story = next(x for x in project_summary.project_summary("t")["implement"]["stories"]
                 if x["story_key"] == "9.9")
    assert story["status"] in ("failed", "cancelled")       # 沒成功過 → 維持最新非成功狀態
    assert story["pr_url"] == ""


def test_active_sec_excludes_idle_and_dedups_parallel(tmp_db):
    """實際耗時＝合併 run 區間：排除閒置空檔、平行執行不重複計（牆鐘跨度仍含閒置）。"""
    dal.create_project("t", "demo")
    sid = _sess("t", "1.1", 1, "succeeded")
    with dal.connect() as conn:
        # 一個批次橫跨整段（讓牆鐘跨度有對照值）
        conn.execute("INSERT INTO impl_batches (thread_id, total, created_at, updated_at) "
                     "VALUES ('t', 1, 1000, 2030)")
        # pre-impl harness run：1000–1010（10s）
        conn.execute(
            "INSERT INTO harness_runs (run_id, thread_id, stage, operation, status, started_at, ended_at) "
            "VALUES ('r1', 't', 'specify', 'prd', 'succeeded', 1000, 1010)")
        # implement run 與 harness 重疊：1005–1020 → 合併成 [1000,1020]=20s（平行不重複計）
        conn.execute("INSERT INTO impl_runs (session_id, status, started_at, ended_at) "
                     "VALUES (?, 'succeeded', 1005, 1020)", (sid,))
        # 閒置一大段後再跑：2000–2030（30s）
        conn.execute("INSERT INTO impl_runs (session_id, status, started_at, ended_at) "
                     "VALUES (?, 'succeeded', 2000, 2030)", (sid,))

    totals = project_summary.project_summary("t")["totals"]
    assert totals["active_sec"] == 50.0          # 20 + 30，不含 1020–2000 的閒置
    assert totals["span_sec"] >= 1000            # 牆鐘跨度仍把閒置算進去（對照）
