"""DAL —— 唯一碰 DB 的層。

設計鐵則（spec §2）：host owns all I/O。plugin 永遠拿不到這裡的 connection；
所有 DB 讀寫都經由本模組的函式。SQLite 單檔 WAL；每次操作開一條短連線
（WAL 支援並行讀，連線輕量），交由 context manager 統一 commit/rollback/close。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

# DB 路徑可由環境變數覆寫（測試 / 多環境）；預設 backend/data/app.db
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "app.db"


def db_path() -> str:
    return os.environ.get("AITOOL_DB", str(_DEFAULT_DB))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """開一條 DB 連線（WAL、Row factory、foreign_keys）。唯一的連線入口。

    用法：
        with connect() as conn:
            conn.execute(...)
    正常離開 → commit；例外 → rollback；最後一律 close。
    """
    path = Path(db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---- plugin ownership / state ----
def record_contribution(plugin_id: str, capability_type: str, capability_id: str) -> None:
    """記一筆「某 plugin 貢獻了某 capability」（供 GUI 顯示來源 + disable 時清理）。"""
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO plugin_contributions "
            "(plugin_id, capability_type, capability_id) VALUES (?, ?, ?)",
            (plugin_id, capability_type, capability_id),
        )


def contributions(plugin_id: Optional[str] = None) -> list[dict]:
    sql = "SELECT plugin_id, capability_type, capability_id FROM plugin_contributions"
    params: tuple = ()
    if plugin_id is not None:
        sql += " WHERE plugin_id = ?"
        params = (plugin_id,)
    sql += " ORDER BY plugin_id, capability_type, capability_id"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def clear_contributions(plugin_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM plugin_contributions WHERE plugin_id = ?", (plugin_id,))


def set_plugin_enabled(plugin_id: str, enabled: bool) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO plugin_state (plugin_id, enabled) VALUES (?, ?) "
            "ON CONFLICT(plugin_id) DO UPDATE SET enabled = excluded.enabled",
            (plugin_id, 1 if enabled else 0),
        )


def plugin_enabled(plugin_id: str) -> bool:
    """未登錄者預設啟用（裝進目錄就載入的寬鬆信任模型）。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT enabled FROM plugin_state WHERE plugin_id = ?", (plugin_id,)
        ).fetchone()
    return True if row is None else bool(row["enabled"])


def plugin_states() -> dict[str, bool]:
    with connect() as conn:
        rows = conn.execute("SELECT plugin_id, enabled FROM plugin_state").fetchall()
    return {r["plugin_id"]: bool(r["enabled"]) for r in rows}
