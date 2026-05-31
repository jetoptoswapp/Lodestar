"""集中式 schema migration。

M0：schema.sql 全部以 `IF NOT EXISTS` 撰寫，`migrate()` 直接 executescript（冪等）。
日後若需要增量 migration（加欄位 / 改型別），在此加版本表 + 有序 step，
讓所有 schema 變更集中於單一入口。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from persistence.dal import connect

_SCHEMA = Path(__file__).resolve().parent / "schema.sql"

# 增量加欄：schema.sql 的 CREATE TABLE IF NOT EXISTS 不會 ALTER 既有表，
# 故對既有 DB 逐欄 ALTER（try/except OperationalError 冪等：欄位已存在則略過）。
_ADD_COLUMNS = [
    ("projects", "delivery_target", "TEXT NOT NULL DEFAULT ''"),
    ("projects", "repo_mode", "TEXT NOT NULL DEFAULT ''"),
    ("projects", "repo_full_name", "TEXT NOT NULL DEFAULT ''"),
    ("projects", "repo_owner", "TEXT NOT NULL DEFAULT ''"),
    ("projects", "repo_visibility", "TEXT NOT NULL DEFAULT 'private'"),
    ("projects", "repo_created", "INTEGER NOT NULL DEFAULT 0"),
]


def migrate() -> None:
    """建立 / 升級 schema。啟動時與測試 setup 都呼叫它。"""
    sql = _SCHEMA.read_text(encoding="utf-8")
    with connect() as conn:
        conn.executescript(sql)
        for table, col, decl in _ADD_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # 欄位已存在（新 DB 由 schema.sql 建好）
