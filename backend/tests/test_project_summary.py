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
