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


def uploads_dir() -> Path:
    """附件儲存根目錄。預設 `<DB 所在資料夾>/uploads/`，跟著 AITOOL_DB 走。"""
    return Path(db_path()).parent / "uploads"


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


# ---- integration credential keystore（密文存放層；加解密在 host 層 keystore.py）----
def set_integration_secret(target: str, ciphertext: str, updated_at: float) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO integration_secrets (target, ciphertext, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(target) DO UPDATE SET ciphertext = excluded.ciphertext, "
            "updated_at = excluded.updated_at",
            (target, ciphertext, updated_at),
        )


def get_integration_secret(target: str) -> Optional[str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT ciphertext FROM integration_secrets WHERE target = ?", (target,)
        ).fetchone()
    return row["ciphertext"] if row else None


def delete_integration_secret(target: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM integration_secrets WHERE target = ?", (target,))
    return cur.rowcount > 0


# ============================================================
#  Projects（thread = project entry）
# ============================================================
def create_project(thread_id: str, name: str, workflow_id: Optional[str] = None, *,
                   delivery_target: str = "", repo_mode: str = "",
                   repo_full_name: str = "", repo_owner: str = "",
                   repo_visibility: str = "private") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO projects (thread_id, name, workflow_id, "
            "delivery_target, repo_mode, repo_full_name, repo_owner, repo_visibility) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (thread_id, name, workflow_id, delivery_target, repo_mode,
             repo_full_name, repo_owner, repo_visibility),
        )


def get_project(thread_id: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    return dict(row) if row else None


def list_projects() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def set_project_workflow(thread_id: str, workflow_id: Optional[str]) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET workflow_id = ? WHERE thread_id = ?",
            (workflow_id, thread_id),
        )


def update_project_name(thread_id: str, name: str) -> bool:
    """改 project 顯示名稱。回傳是否真的更新到（False = 找不到 thread）。"""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE projects SET name = ? WHERE thread_id = ?",
            (name, thread_id),
        )
        return cur.rowcount > 0


def update_project_delivery(thread_id: str, *, delivery_target: str, repo_mode: str,
                            repo_full_name: str, repo_owner: str,
                            repo_visibility: str) -> bool:
    """設定/更新 project 的 delivery repo。改設定 → 重置 repo_created=0（new mode 需重新 resolve/建）。"""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE projects SET delivery_target=?, repo_mode=?, repo_full_name=?, "
            "repo_owner=?, repo_visibility=?, repo_created=0 WHERE thread_id=?",
            (delivery_target, repo_mode, repo_full_name, repo_owner, repo_visibility, thread_id),
        )
        return cur.rowcount > 0


def set_project_repo_created(thread_id: str, repo_full_name: str) -> None:
    """lazy 建好 repo 後回填 full_name + 標記 created（resolve_project_repo 用）。"""
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET repo_full_name=?, repo_created=1 WHERE thread_id=?",
            (repo_full_name, thread_id),
        )


def delete_project_cascade(thread_id: str) -> Optional[list[str]]:
    """刪 project + 全部相關 row（attachments / artifacts / status / messages / events /
    revisions / comments / harness_runs / harness_validation_results）。
    SQLite 無 declared FK CASCADE，這裡手動掃。

    回傳被刪掉的 attachment content_path（caller 用來清磁碟檔案）；
    project 不存在 → 回 None。
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT thread_id FROM projects WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None

        # 收 attachment 檔案路徑供 caller 清檔案
        attach_paths = [
            r["content_path"]
            for r in conn.execute(
                "SELECT content_path FROM stage_attachments WHERE thread_id = ?",
                (thread_id,),
            ).fetchall()
        ]

        # harness_validation_results 透過 harness_runs.run_id 關聯
        conn.execute(
            "DELETE FROM harness_validation_results WHERE run_id IN "
            "(SELECT run_id FROM harness_runs WHERE thread_id = ?)",
            (thread_id,),
        )
        conn.execute(
            "DELETE FROM harness_events WHERE run_id IN "
            "(SELECT run_id FROM harness_runs WHERE thread_id = ?)",
            (thread_id,),
        )

        # 其餘表直接以 thread_id 砍
        for table in (
            "harness_runs",
            "stage_attachments",
            "stage_comments",
            "stage_revisions",
            "stage_events",
            "stage_messages",
            "stage_status",
            "stage_artifacts",
        ):
            conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))

        conn.execute("DELETE FROM projects WHERE thread_id = ?", (thread_id,))
        return attach_paths


# ============================================================
#  Stage artifacts（artifact 正文直接存表 —— 取代 ver2 checkpoint blob）
# ============================================================
def get_artifact(thread_id: str, stage_id: str) -> Optional[str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT content FROM stage_artifacts WHERE thread_id = ? AND stage_id = ?",
            (thread_id, stage_id),
        ).fetchone()
    return row["content"] if row else None


def upsert_artifact(thread_id: str, stage_id: str, content: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO stage_artifacts (thread_id, stage_id, content) VALUES (?, ?, ?) "
            "ON CONFLICT(thread_id, stage_id) DO UPDATE SET "
            "content = excluded.content, updated_at = strftime('%s','now')",
            (thread_id, stage_id, content),
        )


def get_artifact_meta(thread_id: str, stage_id: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT updated_at, length(content) AS content_length FROM stage_artifacts "
            "WHERE thread_id = ? AND stage_id = ?",
            (thread_id, stage_id),
        ).fetchone()
    return dict(row) if row else None


# ============================================================
#  Stage status（draft / approved / needs_revision）
# ============================================================
def get_stage_status(thread_id: str, stage_id: str) -> str:
    """未設過 → 預設 'draft'。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM stage_status WHERE thread_id = ? AND stage_id = ?",
            (thread_id, stage_id),
        ).fetchone()
    return row["status"] if row else "draft"


def set_stage_status(thread_id: str, stage_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO stage_status (thread_id, stage_id, status) VALUES (?, ?, ?) "
            "ON CONFLICT(thread_id, stage_id) DO UPDATE SET "
            "status = excluded.status, updated_at = strftime('%s','now')",
            (thread_id, stage_id, status),
        )


def list_stage_statuses(thread_id: str) -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT stage_id, status FROM stage_status WHERE thread_id = ?",
            (thread_id,),
        ).fetchall()
    return {r["stage_id"]: r["status"] for r in rows}


# ============================================================
#  Stage revisions（每次 artifact 變更記一筆 + downstream_reset JSON）
# ============================================================
def add_revision(
    thread_id: str, stage_id: str, source: str,
    *, instruction: str = "", summary: str = "",
    downstream_reset: Optional[list[str]] = None,
    content_length: int = 0, reviewed: bool = False,
) -> int:
    import json as _json
    payload = _json.dumps(downstream_reset or [], ensure_ascii=False)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO stage_revisions (thread_id, stage_id, source, summary, "
            "instruction, reviewed, downstream_reset, content_length) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (thread_id, stage_id, source, summary, instruction,
             1 if reviewed else 0, payload, content_length),
        )
        return int(cur.lastrowid or 0)


def list_revisions(thread_id: str, stage_id: str, limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, source, summary, instruction, reviewed, downstream_reset, "
            "content_length, created_at FROM stage_revisions "
            "WHERE thread_id = ? AND stage_id = ? ORDER BY id DESC LIMIT ?",
            (thread_id, stage_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
#  Stage events（SSE 進度事件來源）
# ============================================================
def append_event(thread_id: str, stage_id: str, event_type: str, detail: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO stage_events (thread_id, stage_id, event_type, detail) "
            "VALUES (?, ?, ?, ?)",
            (thread_id, stage_id, event_type, detail),
        )


def list_events(thread_id: str, since_id: int = 0, limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, stage_id, event_type, detail, created_at FROM stage_events "
            "WHERE thread_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (thread_id, since_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
#  Stage messages（chat 對話歷史）
# ============================================================
def append_message(thread_id: str, stage_id: str, role: str, content: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO stage_messages (thread_id, stage_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            (thread_id, stage_id, role, content),
        )


def list_messages(thread_id: str, stage_id: str, limit: int = 200) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM stage_messages "
            "WHERE thread_id = ? AND stage_id = ? ORDER BY id ASC LIMIT ?",
            (thread_id, stage_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
#  Stage attachments（M1.1：上傳檔案 inline 進 SA prompt）
# ============================================================
def add_attachment(
    file_id: str, thread_id: str, stage_id: str, filename: str,
    mime: str, size_bytes: int, content_path: str,
    parsed_text: Optional[str], parse_error: str = "",
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO stage_attachments "
            "(file_id, thread_id, stage_id, filename, mime, size_bytes, "
            " content_path, parsed_text, parse_error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, thread_id, stage_id, filename, mime, size_bytes,
             content_path, parsed_text, parse_error),
        )


def list_attachments(thread_id: str, stage_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT file_id, filename, mime, size_bytes, content_path, "
            "       parsed_text, parse_error, created_at "
            "FROM stage_attachments "
            "WHERE thread_id = ? AND stage_id = ? "
            "ORDER BY created_at ASC",
            (thread_id, stage_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get_attachment(file_id: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT file_id, thread_id, stage_id, filename, mime, size_bytes, "
            "       content_path, parsed_text, parse_error, created_at "
            "FROM stage_attachments WHERE file_id = ?",
            (file_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_attachment(file_id: str) -> Optional[dict]:
    """刪 DB row 回傳被刪 row（caller 用 content_path 清檔）。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM stage_attachments WHERE file_id = ?", (file_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM stage_attachments WHERE file_id = ?", (file_id,))
    return dict(row)


# ============================================================
#  M3：workflow_definitions / agents CRUD（user-defined）
# ============================================================
import json as _json


def list_workflow_definitions() -> list[dict]:
    """列 user-defined workflows（不含 builtin plugin 提供的）。

    回傳 dict 帶 stages_json 解析後的 list（前端用）。
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, label, description, stages_json, source_plugin, created_at "
            "FROM workflow_definitions ORDER BY created_at ASC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["stages"] = _json.loads(d.pop("stages_json") or "[]")
        except Exception:
            d["stages"] = []
        out.append(d)
    return out


def get_workflow_definition(wf_id: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, label, description, stages_json, source_plugin, created_at "
            "FROM workflow_definitions WHERE id = ?",
            (wf_id,),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["stages"] = _json.loads(d.pop("stages_json") or "[]")
    except Exception:
        d["stages"] = []
    return d


def upsert_workflow_definition(
    *, wf_id: str, label: str, description: str,
    stages: list[dict], source_plugin: str = "user",
) -> None:
    """插入或更新 workflow。stages 是 list[{stage_id, depends_on[], agent_bindings[{agent_id, role}], collab_mode}]。"""
    payload = _json.dumps(stages, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO workflow_definitions (id, label, description, stages_json, source_plugin) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "label = excluded.label, description = excluded.description, "
            "stages_json = excluded.stages_json, source_plugin = excluded.source_plugin",
            (wf_id, label, description, payload, source_plugin),
        )


def delete_workflow_definition(wf_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM workflow_definitions WHERE id = ?", (wf_id,))
        return cur.rowcount > 0


def list_agents() -> list[dict]:
    """列 user-saved agents（不含純 in-memory 的 builtin seed）。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT agent_id, name, role, system_prompt, model_choice, max_iterations, "
            "       enabled, tools, created_at, updated_at "
            "FROM agents ORDER BY created_at ASC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["enabled"] = bool(d["enabled"])
        try:
            d["tools"] = _json.loads(d.pop("tools") or "[]")
        except Exception:
            d["tools"] = []
        out.append(d)
    return out


def get_agent(agent_id: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT agent_id, name, role, system_prompt, model_choice, max_iterations, "
            "       enabled, tools, created_at, updated_at FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    try:
        d["tools"] = _json.loads(d.pop("tools") or "[]")
    except Exception:
        d["tools"] = []
    return d


def upsert_agent(
    *, agent_id: str, name: str, role: str, system_prompt: str,
    model_choice: str = "claude-cli", max_iterations: int = 1,
    enabled: bool = True, tools: Optional[list[str]] = None,
) -> None:
    payload = _json.dumps(tools or [], ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO agents (agent_id, name, role, system_prompt, model_choice, "
            "                   max_iterations, enabled, tools) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET "
            "name = excluded.name, role = excluded.role, system_prompt = excluded.system_prompt, "
            "model_choice = excluded.model_choice, max_iterations = excluded.max_iterations, "
            "enabled = excluded.enabled, tools = excluded.tools, "
            "updated_at = strftime('%s','now')",
            (agent_id, name, role, system_prompt, model_choice, max_iterations,
             1 if enabled else 0, payload),
        )


def delete_agent(agent_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        return cur.rowcount > 0
