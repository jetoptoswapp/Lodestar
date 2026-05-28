"""集中式 schema migration。

M0：schema.sql 全部以 `IF NOT EXISTS` 撰寫，`migrate()` 直接 executescript（冪等）。
日後若需要增量 migration（加欄位 / 改型別），在此加版本表 + 有序 step，
讓所有 schema 變更集中於單一入口。
"""
from __future__ import annotations

from pathlib import Path

from persistence.dal import connect

_SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def migrate() -> None:
    """建立 / 升級 schema。啟動時與測試 setup 都呼叫它。"""
    sql = _SCHEMA.read_text(encoding="utf-8")
    with connect() as conn:
        conn.executescript(sql)
