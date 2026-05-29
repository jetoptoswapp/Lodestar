"""刪除 RCA 示範 / 種子 thread（含 artifacts / 對話 / 附件 / 上傳檔），把工具還原乾淨。

只刪「本工具建立的示範 thread」（硬編碼清單），不碰任何其他 thread / workflow。
與 seed_rca.py 對稱：reset → （可選）seed → 重新乾淨示範。

用法（在 backend/ 下）：
    python -m scripts.reset_rca_demo
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from persistence import dal  # noqa: E402

# 只刪這些（seed_rca 的 3 個情境 + live demo 的 2 個）。不誤刪使用者既有 thread。
DEMO_THREAD_IDS = [
    "rca-yield-drop",
    "rca-param-drift",
    "rca-signal-anomaly",
    "demo-planner",
    "demo-panel",
]


def reset() -> None:
    uploads_root = dal.uploads_dir()
    deleted = 0
    for tid in DEMO_THREAD_IDS:
        paths = dal.delete_project_cascade(tid)   # None = 不存在
        if paths is None:
            print(f"[reset] {tid} 不存在（skip）")
            continue
        for rel in paths:
            try:
                (uploads_root / rel).unlink(missing_ok=True)
            except OSError:
                pass
        # 清掉該 thread 的 uploads 子目錄
        shutil.rmtree(uploads_root / tid, ignore_errors=True)
        # 一併刪掉 planner apply 產生的 per-thread workflow（若有）
        dal.delete_workflow_definition(f"rca_plan_{tid}")
        deleted += 1
        print(f"[reset] 已刪除 {tid}（含 artifacts / 對話 / 附件 / 上傳檔）")

    print(f"[reset] done — 共刪 {deleted} 個示範 thread。其他 thread 與 workflow 未受影響。")


if __name__ == "__main__":
    reset()
