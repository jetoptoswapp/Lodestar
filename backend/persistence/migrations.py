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
    ("projects", "local_path", "TEXT NOT NULL DEFAULT ''"),  # repo_mode=local：本機資料夾絕對路徑
    ("projects", "build_command", "TEXT NOT NULL DEFAULT ''"),      # build_verify stage 的編譯指令
    ("projects", "build_env_script", "TEXT NOT NULL DEFAULT ''"),   # build 前 source 的 env script
    # batch orchestration（逐 issue 依序實作）：既有 DB 升級。新 DB 由 schema.sql 建好。
    ("impl_sessions", "batch_id", "INTEGER"),
    ("impl_sessions", "issue_number", "INTEGER"),
    ("impl_sessions", "story_key", "TEXT NOT NULL DEFAULT ''"),
    ("impl_batches", "auto_merge", "INTEGER NOT NULL DEFAULT 0"),
    # 嘗試串接：重跑 session 指向被它接續的前次 session（中斷/失敗）。鏈尾 = 任務現況。
    ("impl_sessions", "retry_of", "INTEGER"),
]


def migrate() -> None:
    """建立 / 升級 schema。啟動時與測試 setup 都呼叫它。

    先 ALTER 既有表補欄，再 executescript：因 schema.sql 的 CREATE INDEX 可能引用新欄，
    既有 DB 必須先把欄位加上去，索引才建得起來。新 DB 的 ALTER 因表尚未存在而略過，
    隨後 executescript 以 schema.sql 建好含新欄的表。"""
    sql = _SCHEMA.read_text(encoding="utf-8")
    with connect() as conn:
        for table, col, decl in _ADD_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # 欄位已存在，或新 DB 表尚未建（由下方 schema.sql 建好）
        conn.executescript(sql)
