"""impl_* 資料存取（async 實作 agent 專用，獨立命名空間）。

連線一律走 persistence.dal.connect()（唯一連線入口、WAL、foreign_keys=ON），
但 SQL 留在本模組，與 sync 遙測的 harness_* 完全分離（run_id 形狀也刻意不同：
這裡是 INTEGER AUTOINCREMENT，harness_* 是 TEXT）。
"""
from __future__ import annotations

from typing import Optional

from persistence.dal import connect

# ---- impl_sessions ----------------------------------------------------------

def create_session(*, thread_id: str, title: str, target_repo: str,
                    runner: str, stage: str = "implement") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO impl_sessions (thread_id, stage, title, target_repo, runner, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (thread_id, stage, title, target_repo, runner),
        )
        return int(cur.lastrowid)


def update_session(session_id: int, *, status: Optional[str] = None,
                   pr_url: Optional[str] = None,
                   error_message: Optional[str] = None) -> None:
    sets, params = ["updated_at = strftime('%s','now')"], []
    if status is not None:
        sets.append("status = ?"); params.append(status)
    if pr_url is not None:
        sets.append("pr_url = ?"); params.append(pr_url)
    if error_message is not None:
        sets.append("error_message = ?"); params.append(error_message)
    params.append(session_id)
    with connect() as conn:
        conn.execute(f"UPDATE impl_sessions SET {', '.join(sets)} WHERE session_id = ?", params)


def get_session(session_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM impl_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def list_sessions(thread_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM impl_sessions WHERE thread_id = ? ORDER BY created_at DESC",
            (thread_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---- impl_runs --------------------------------------------------------------

def create_run(*, session_id: int, attempt: int, runner: str,
               parent_run_id: Optional[int] = None,
               dispatch_role: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO impl_runs (session_id, attempt, runner, status, parent_run_id, dispatch_role) "
            "VALUES (?, ?, ?, 'running', ?, ?)",
            (session_id, attempt, runner, parent_run_id, dispatch_role),
        )
        return int(cur.lastrowid)


def finish_run(run_id: int, *, status: str, exit_code: Optional[int],
               cancelled: bool, timed_out: bool, last_output: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE impl_runs SET status = ?, exit_code = ?, cancelled = ?, timed_out = ?, "
            "last_output = ?, ended_at = strftime('%s','now') WHERE run_id = ?",
            (status, exit_code, 1 if cancelled else 0, 1 if timed_out else 0,
             last_output, run_id),
        )


def get_run(run_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM impl_runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(session_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM impl_runs WHERE session_id = ? ORDER BY attempt", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---- impl_messages（SSE log channel 持久化）--------------------------------

def append_message(run_id: int, *, content: str, kind: str = "log") -> int:
    """append 一則 log/event，回傳該則的 seq（run 內遞增）。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS next FROM impl_messages WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        seq = int(row["next"])
        conn.execute(
            "INSERT INTO impl_messages (run_id, seq, kind, content) VALUES (?, ?, ?, ?)",
            (run_id, seq, kind, content),
        )
        return seq


def list_messages(run_id: int, *, after_seq: int = -1) -> list[dict]:
    """取 run 的 log（after_seq 之後的，供 SSE 補播）。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT seq, kind, content, created_at FROM impl_messages "
            "WHERE run_id = ? AND seq > ? ORDER BY seq",
            (run_id, after_seq),
        ).fetchall()
        return [dict(r) for r in rows]


def list_session_messages(session_id: int, *, after_id: int = 0) -> list[dict]:
    """跨 run 取整個 session 的 log，用全域 message id 當單調游標（poll log channel 用）。

    每列帶 id / run_id / attempt，前端可分段顯示。after_id 之後才回傳。
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT m.id, m.run_id, r.attempt, m.seq, m.kind, m.content, m.created_at "
            "FROM impl_messages m JOIN impl_runs r ON m.run_id = r.run_id "
            "WHERE r.session_id = ? AND m.id > ? ORDER BY m.id",
            (session_id, after_id),
        ).fetchall()
        return [dict(r) for r in rows]
