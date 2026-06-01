-- ai-tool-v3 DB schema（SQLite 單檔 WAL）。唯一碰 DB 的是 DAL 層。
-- 對應 spec 附錄 C：核心 / agent 客製化 / sync-AI 遙測。
-- async 實作 agent 表（impl_*）見檔尾：獨立命名空間，run_id 用 INTEGER（與 sync 遙測 TEXT 形狀刻意不同）。

-- ===== 核心（host / stage / workflow / plugin）=====
CREATE TABLE IF NOT EXISTS projects (
    thread_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    workflow_id TEXT,                                   -- NULL → lazy default
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    -- delivery repo（per-project；lazy 建立。見 tasks/delivery-repo-plan.md）
    delivery_target TEXT NOT NULL DEFAULT '',           -- github / gitlab / ''
    repo_mode       TEXT NOT NULL DEFAULT '',           -- new / existing / ''
    repo_full_name  TEXT NOT NULL DEFAULT '',           -- owner/repo（既有，或開新後回填）
    repo_owner      TEXT NOT NULL DEFAULT '',           -- 開新的 org/group（空=個人帳號）
    repo_visibility TEXT NOT NULL DEFAULT 'private',    -- public / private / internal
    repo_created    INTEGER NOT NULL DEFAULT 0          -- lazy：repo 是否已建/確認
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

-- Integration credential keystore（server-side，取代瀏覽器 localStorage 存 token）。
-- ciphertext = Fernet 加密後的 JSON config（含 PAT 等機密）；明文永不落地、也不回傳前端。
CREATE TABLE IF NOT EXISTS integration_secrets (
    target     TEXT PRIMARY KEY,        -- github / jira / gitlab
    ciphertext TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- M1.1：stage 上傳附件（inline 進 SA prompt 用）
CREATE TABLE IF NOT EXISTS stage_attachments (
    file_id       TEXT PRIMARY KEY,                 -- uuid hex
    thread_id     TEXT NOT NULL,
    stage_id      TEXT NOT NULL,
    filename      TEXT NOT NULL,
    mime          TEXT NOT NULL DEFAULT '',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    content_path  TEXT NOT NULL,                    -- 相對 backend/data/uploads/
    parsed_text   TEXT,                             -- 解析後純文字（PDF/DOCX/OCR）；NULL = 未解析
    parse_error   TEXT NOT NULL DEFAULT '',         -- 解析失敗訊息（如「tesseract 未安裝」）
    created_at    REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_stage_attachments
    ON stage_attachments (thread_id, stage_id, created_at DESC);

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

-- ===== async 實作 agent（M5，獨立命名空間）=====
-- 與 sync 遙測（harness_*）完全分離：run_id 用 INTEGER AUTOINCREMENT，不共用形狀。
-- host 的 async_runtime 層是唯一寫入者；plugin 永遠拿不到 connection。

-- 一批「逐 issue 依序實作」。一個 batch 內含 N 個 session（一 story 一 session 一 PR）。
CREATE TABLE IF NOT EXISTS impl_batches (
    batch_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id       TEXT NOT NULL,
    target_repo     TEXT NOT NULL DEFAULT '',              -- owner/repo
    runner          TEXT NOT NULL DEFAULT '',
    mode            TEXT NOT NULL DEFAULT 'roles',         -- single / roles
    total           INTEGER NOT NULL DEFAULT 0,            -- 共幾個 story
    status          TEXT NOT NULL DEFAULT 'running',       -- running/succeeded/failed/cancelled/partial
    stop_on_failure INTEGER NOT NULL DEFAULT 0,            -- 1=遇錯即停；0=continue-on-failure（預設）
    error_message   TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at      REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_impl_batches_thread ON impl_batches (thread_id, created_at DESC);

-- 一次「對某 story 的實作請求」。一個 session 內含 ≤3 次 fix-loop 嘗試（impl_runs）。
CREATE TABLE IF NOT EXISTS impl_sessions (
    session_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    TEXT NOT NULL,
    stage        TEXT NOT NULL DEFAULT 'implement',       -- 觸發此 session 的 stage
    title        TEXT NOT NULL DEFAULT '',                -- story / 目標標題
    target_repo  TEXT NOT NULL DEFAULT '',                -- owner/repo（mock 階段為示意值）
    runner       TEXT NOT NULL DEFAULT '',                -- runner choice（claude-cli / mock）
    status       TEXT NOT NULL DEFAULT 'pending',         -- pending/running/succeeded/failed/cancelled
    pr_url       TEXT NOT NULL DEFAULT '',                -- 開 PR 後填（mock 階段為示意 url）
    error_message TEXT NOT NULL DEFAULT '',
    batch_id     INTEGER,                                 -- 屬於哪個 batch（NULL = 舊的單 story session）
    issue_number INTEGER,                                 -- 對應的 GitHub/GitLab issue 編號
    story_key    TEXT NOT NULL DEFAULT '',                -- story 編號 N.M（排序 / 顯示）
    created_at   REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at   REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_impl_sessions_thread ON impl_sessions (thread_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_impl_sessions_batch ON impl_sessions (batch_id, session_id);

-- session 內單次嘗試（attempt 1..3）。parent_run_id 串 fix-loop；dispatch_role 預留 §6.4 dispatch。
CREATE TABLE IF NOT EXISTS impl_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES impl_sessions (session_id) ON DELETE CASCADE,
    attempt       INTEGER NOT NULL DEFAULT 1,             -- fix-loop 第幾次（硬上限 3）
    runner        TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'running',        -- running/succeeded/failed/cancelled/timed_out
    exit_code     INTEGER,
    cancelled     INTEGER NOT NULL DEFAULT 0,
    timed_out     INTEGER NOT NULL DEFAULT 0,
    last_output   TEXT NOT NULL DEFAULT '',
    parent_run_id INTEGER,                                -- 上一 attempt 的 run_id（fix-loop / dispatch）
    dispatch_role TEXT NOT NULL DEFAULT '',               -- '' / lead / subagent（§6.4 dispatch 預留）
    started_at    REAL NOT NULL DEFAULT (strftime('%s','now')),
    ended_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_impl_runs_session ON impl_runs (session_id, attempt);

-- 每次 run 的串流 log / 事件（SSE log channel 的持久化）。seq 保序。
CREATE TABLE IF NOT EXISTS impl_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES impl_runs (run_id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL DEFAULT 0,                -- run 內排序
    kind       TEXT NOT NULL DEFAULT 'log',              -- log / event / system
    content    TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_impl_messages_run ON impl_messages (run_id, seq);
