"""Seed 合成的製造異常情境給 rca_domain plugin demo。

用法（在 backend/ 下）：
    python -m scripts.seed_rca

行為（idempotent）：
- 每個情境建立一個 thread（已存在則沿用）並綁定對應 workflow
- 預填 rca_intake artifact = 情境的 scenario.md
- 把資料檔（CSV）複製進 uploads，並掛到「會讀它的 stage」（單代理 = rca_analysis）

資料檔掛在讀它的 stage：engine 的 list_attachments 只給本 stage 附件，
故 CSV 需掛在 rca_analysis（而非 rca_intake）才會被分析時讀到。

host owns I/O：本 script 是 host 端工具（非 plugin），可直接用 dal。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from persistence import dal, migrations  # noqa: E402

_FIXTURES = _BACKEND / "plugins" / "rca_domain" / "fixtures"


# 情境定義：
#   thread_id, 顯示名, workflow_id, fixture 目錄, [(資料檔名, 掛載 stage_id, mime)]
# 資料檔掛在「會讀它的 stage」：單代理 = rca_analysis；鏈 = rca_baseline。
SCENARIOS = [
    {
        "thread_id": "rca-yield-drop",
        "name": "RCA：Line-3 良率下降（單代理）",
        "workflow_id": "rca_single",
        "fixture": "yield_drop",
        "data_files": [("yield_by_lot.csv", "rca_analysis", "text/csv")],
    },
    {
        "thread_id": "rca-param-drift",
        "name": "RCA：ETCH 參數漂移（多代理鏈）",
        "workflow_id": "rca_chain",
        "fixture": "param_drift",
        "data_files": [("tool_params_timeseries.csv", "rca_baseline", "text/csv")],
    },
    {
        "thread_id": "rca-signal-anomaly",
        "name": "RCA：製程訊號間歇異常（多代理鏈）",
        "workflow_id": "rca_chain",
        "fixture": "signal_anomaly",
        "data_files": [("process_trace.csv", "rca_baseline", "text/csv")],
    },
]


def _attach(thread_id: str, stage_id: str, src: Path, mime: str) -> str:
    """複製 fixture 檔進 uploads 並建 attachment row（沿用 upload_attachment 慣例）。"""
    file_id = uuid.uuid4().hex[:12]
    ext = src.suffix.lower() or ".bin"
    uploads_root = dal.uploads_dir() / thread_id
    uploads_root.mkdir(parents=True, exist_ok=True)
    rel_path = f"{thread_id}/{file_id}{ext}"
    abs_path = uploads_root / f"{file_id}{ext}"
    data = src.read_bytes()
    abs_path.write_bytes(data)
    dal.add_attachment(
        file_id=file_id, thread_id=thread_id, stage_id=stage_id,
        filename=src.name, mime=mime, size_bytes=len(data),
        content_path=rel_path,
        parsed_text=data.decode("utf-8", errors="replace"),  # CSV 純文字 → inline fallback
        parse_error="",
    )
    return file_id


def seed() -> None:
    migrations.migrate()
    for sc in SCENARIOS:
        tid = sc["thread_id"]
        fixture_dir = _FIXTURES / sc["fixture"]
        scenario_md = (fixture_dir / "scenario.md").read_text(encoding="utf-8")

        if dal.get_project(tid) is None:
            dal.create_project(tid, sc["name"], workflow_id=sc["workflow_id"])
            print(f"[seed] created thread {tid} → workflow {sc['workflow_id']}")
        else:
            dal.set_project_workflow(tid, sc["workflow_id"])
            print(f"[seed] thread {tid} 已存在 → 重綁 workflow {sc['workflow_id']}")

        dal.upsert_artifact(tid, "rca_intake", scenario_md)
        print(f"[seed]   rca_intake artifact 已填入（{len(scenario_md)} chars）")

        for fname, stage_id, mime in sc["data_files"]:
            existing = [a for a in dal.list_attachments(tid, stage_id) if a["filename"] == fname]
            if existing:
                print(f"[seed]   {fname} 已掛於 {stage_id}（skip）")
                continue
            fid = _attach(tid, stage_id, fixture_dir / fname, mime)
            print(f"[seed]   attached {fname} → stage {stage_id} (file_id={fid})")

    print("[seed] done.")


if __name__ == "__main__":
    seed()
