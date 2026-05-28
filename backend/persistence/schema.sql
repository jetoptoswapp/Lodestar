-- ai-tool-v3 DB schema（SQLite 單檔 WAL）。唯一碰 DB 的是 DAL 層。
-- 對應 spec 附錄 C：核心 / agent 客製化 / sync-AI 遙測。
-- async 實作 agent 表（impl_*）M5 才建，刻意不在此（與 sync 遙測不共用 run_id 形狀）。

-- ===== 核心（host / stage / workflow / plugin）=====
CREATE TABLE IF NOT EXISTS projects (
    thread_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    workflow_id TEXT,                                   -- NULL → lazy default
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now'))
);

-- artifact 正文直接存表（取代 ver2 LangGraph checkpoint blob）
CREATE TABLE IF NOT EXISTS stage_artifacts (
    thread_id  TEXT NOT NULL,
    stage_id   TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (thread_id, stage_id)
);

CREATE TABLE IF NOT EXISTS stage_status (
    thread_id  TEXT NOT NULL,
    stage_id   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft',           -- draft/approved/needs_revision
    updated_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (thread_id, stage_id)
);

CREATE TABLE IF NOT EXISTS stage_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  TEXT NOT NULL,
    stage_id   TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_stage_messages ON stage_messages (thread_id, stage_id, id);

CREATE TABLE IF NOT EXISTS stage_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  TEXT NOT NULL,
    stage_id   TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail     TEXT,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_stage_events ON stage_events (thread_id, stage_id, id);

CREATE TABLE IF NOT EXISTS stage_revisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id        TEXT NOT NULL,
    stage_id         TEXT NOT NULL,
    source           TEXT NOT NULL,                     -- ai_revision / manual_edit / generated
    summary          TEXT NOT NULL DEFAULT '',
    instruction      TEXT NOT NULL DEFAULT '',
    reviewed         INTEGER NOT NULL DEFAULT 0,
    downstream_reset TEXT NOT NULL DEFAULT '',          -- JSON list of stage_id
    content_length   INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_stage_revisions ON stage_revisions (thread_id, stage_id, id);

CREATE TABLE IF NOT EXISTS stage_comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL,
    stage_id    TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_stage_comments ON stage_comments (thread_id, stage_id, id);

CREATE TABLE IF NOT EXISTS workflow_definitions (
    id            TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    stages_json   TEXT NOT NULL DEFAULT '[]',           -- JSON: [{stage_id, depends_on[], agent_id?}]
    source_plugin TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS plugin_contributions (
    plugin_id       TEXT NOT NULL,
    capability_type TEXT NOT NULL,                      -- stage/workflow/agent/integration/...
    capability_id   TEXT NOT NULL,
    PRIMARY KEY (plugin_id, capability_type, capability_id)
);

CREATE TABLE IF NOT EXISTS plugin_state (
    plugin_id TEXT PRIMARY KEY,
    enabled   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ===== Agent 客製化（agents 加 tools）=====
CREATE TABLE IF NOT EXISTS agents (
    agent_id       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    role           TEXT NOT NULL,
    system_prompt  TEXT NOT NULL DEFAULT '',
    model_choice   TEXT NOT NULL DEFAULT 'claude-cli',
    max_iterations INTEGER NOT NULL DEFAULT 1,
    enabled        INTEGER NOT NULL DEFAULT 1,
    tools          TEXT NOT NULL DEFAULT '[]',          -- JSON list（給 tool-using agent）
    created_at     REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at     REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_agents_role ON agents (role);

CREATE TABLE IF NOT EXISTS skills (
    skill_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    version     TEXT NOT NULL DEFAULT '1.0',
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at  REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS agent_skills (
    agent_id   TEXT NOT NULL,
    skill_id   TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_skills_agent ON agent_skills (agent_id, sort_order);

-- ===== Sync-AI 遙測（generic harness）=====
CREATE TABLE IF NOT EXISTS harness_runs (
    run_id        TEXT PRIMARY KEY,
    thread_id     TEXT NOT NULL,
    stage         TEXT NOT NULL,                        -- 遙測 stage（specify/design/deliver）
    operation     TEXT NOT NULL,
    model_choice  TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,                        -- succeeded/failed
    error_code    TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    started_at    REAL NOT NULL,
    ended_at      REAL NOT NULL,
    parent_run_id TEXT,                                 -- fix-loop 串接
    created_at    REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_harness_runs_thread ON harness_runs (thread_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_harness_runs_stage ON harness_runs (thread_id, stage, operation);

CREATE TABLE IF NOT EXISTS harness_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_harness_events_run ON harness_events (run_id, event_id);

CREATE TABLE IF NOT EXISTS harness_validation_results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    validator  TEXT NOT NULL,
    severity   TEXT NOT NULL,                           -- warn / fail
    message    TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '{}',
    fix_hint   TEXT,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_harness_validation_run ON harness_validation_results (run_id, id);
