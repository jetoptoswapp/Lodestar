# Lodestar — Plugin-First AI 需求工程平台

把「想法 → PRD → 架構 → 使用者故事 → 交付 → 自動實作」串成 pipeline。每個 stage / agent / delivery target 從第一天起都是可獨立打包安裝的 plugin。

> **狀態**：需求工程主線已端到端打通（PRD → 架構 → Stories → 交付 → 自動實作 batch），里程碑 M1–M5 皆已落地，並有專案級 **Flight Log** 總覽。
> 完整施工計畫：`~/.claude/plans/md-dreamy-hanrahan.md`。
> 建構規格：[`ai-agent-plug-in-magical-brooks.md`](./ai-agent-plug-in-magical-brooks.md)。

> 🧭 **製造業 RCA Copilot**（領域 plugin，與需求工程並存）：異常根因分析助手。
> 快速上手 → [`docs/RCA_QUICKSTART.md`](./docs/RCA_QUICKSTART.md)；架構說明 → [`docs/RCA.md`](./docs/RCA.md)。

---

## 快速開始

**需求**：Python 3.11+（建議 3.12）、Node 22+、npm。

```bash
./start.sh
```

| 服務 | URL |
|---|---|
| Frontend（Next.js） | http://localhost:8724 |
| Backend API（FastAPI） | http://localhost:8723 |
| API docs（Swagger） | http://localhost:8723/docs |
| Health check | http://localhost:8723/api/health |

Port 刻意避開常用值（3000 / 8000 / 8080 / 5173）以免與本機其他服務衝突。

### 首次設定（環境若還沒備好）

```bash
# Backend venv（一次）
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt

# Frontend 依賴（一次）
cd frontend && npm install && cd ..
```

### 機密 / 憑證（credential keystore）

Integration 憑證（GitHub PAT、GitLab token 等）以 **server-side keystore（Fernet 加密）** 儲存，明文不留在瀏覽器。

- **開發**：金鑰自動生成於 `backend/data/.keystore.key`（權限 0600，已 gitignore），免設定。
- **正式部署**：建議用環境變數 `LODESTAR_KEYSTORE_KEY` 注入金鑰（base64 Fernet key），由外部秘密管理（vault / k8s secret）統一保管、不落專案目錄：
  ```bash
  export LODESTAR_KEYSTORE_KEY="$(backend/.venv/bin/python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
  ```
  > 換金鑰會使既有密文無法解密（keystore 視為無憑證、需重新輸入），請妥善保存。

---

## 現況：pipeline 已端到端打通

需求工程主線全部可跑，每一步都是 plugin 提供的能力：

- **Stage 生成**（`prd` / `architecture` / `stories`）：sync harness fix-loop（生成 + validator + 對話補充），核准後解鎖下游 stage。
- **交付**：stories 解析成逐一 issue，發佈到 GitHub / GitLab。
- **自動實作（M5）**：逐 story 依序跑（QA gate，做完才換下一個）；`roles` 模式為 **lead → RD → tester → reviewer** 回圈，過關自動開 PR / MR、可 auto-merge。共用一份 working copy + 每 story 切乾淨 work branch；agent 以 bypassPermissions 實跑 build/test。
- **Flight Log（專案總結）**：**每個專案各一份**唯讀總覽——階段時間軸、逐 story 現況 / 耗時 / 重試輪數 / 成本、token·$ 聚合（`GET /api/projects/{thread_id}/summary`）。
- **Workflow / Agent 編輯器**：per-thread workflow、多角色 agent 綁定（1:N + collaboration role）。
- **Plugin 管理 UI**：裝 / 卸能力像手機 App。

---

## 專案結構

```
ai-tool-v3/
├── start.sh                              # 一鍵起 backend + frontend
├── pyproject.toml                        # pytest 設定（pythonpath=backend）
├── ai-agent-plug-in-magical-brooks.md    # 建構規格書（spec）
├── backend/
│   ├── plugin_api/                       # plugin↔host 唯一介面（stage/workflow/integration/model/runner/harness/host/common）
│   ├── persistence/                      # 唯一碰 DB 的層：schema.sql / dal.py / migrations.py（SQLite WAL）
│   ├── plugin_loader.py · plugin_host.py # discover / semver / 拓樸排序 / 兩階段註冊 / 隔離失敗
│   ├── app.py                            # FastAPI app + lifespan（migrate + load_all + 孤兒恢復）+ 所有 HTTP endpoint
│   ├── workflow_engine.py                # 自製輕量流程編排（純 Python，不用 LangGraph）
│   ├── harness_runner.py · judge_parse.py# sync 生成 harness：fix-loop + validator + judge + 遙測
│   ├── agent_resolver.py · collab_coordinator.py   # 多角色 agent 綁定 / 協作（discussion / dispatch）
│   ├── delivery_parser.py · delivery_repo.py       # stories→issue、交付 repo 解析
│   ├── project_summary.py · telemetry_read.py      # Flight Log 聚合 / harness 遙測讀取
│   ├── keystore.py                       # Fernet 加密憑證
│   ├── async_runtime/                    # async 長時實作（與 sync 生成嚴格分離、不可 cross-import）
│   │   ├── orchestrator.py               #   fix-loop + 多角色 pipeline（lead→RD→tester→reviewer）
│   │   ├── batch.py                      #   逐 story 依序實作（QA gate）
│   │   ├── github_pr.py · gitlab_mr.py   #   開 PR/MR + auto-merge + work-branch 釘回
│   │   ├── impl_dal.py · impl_usage.py   #   實作資料存取 / token·成本聚合
│   │   └── task_registry.py
│   ├── plugins/                          # 能力都在這
│   │   ├── builtin_core_stages/          #   prd / architecture / stories
│   │   ├── builtin_implement/            #   M5 實作 runner + 安全 hooks
│   │   ├── builtin_agents/               #   seed agents（lead / frontend / backend …）
│   │   ├── builtin_models/               #   ModelAdapter（claude-cli / codex-cli）
│   │   ├── builtin_integrations/         #   GitHub / Jira / GitLab IntegrationSpec
│   │   └── rca_domain/                   #   製造業 RCA copilot 領域 plugin
│   └── tests/                            # pytest（327 tests / 39 檔）
└── frontend/
    ├── app/layout.tsx                    # Fraunces × Geist Sans / Mono 字型
    ├── app/globals.css                   # Industrial Cobalt × Drafting Dusk tokens
    └── app/page.tsx                      # 單頁多 view：Workspace / Flight Log / Workflows / Agents / Skills / Plugins
```

---

## 測試

```bash
# Backend（從專案根；pyproject 已設 pythonpath=backend, testpaths=backend/tests）
backend/.venv/bin/python -m pytest          # 327 tests

# Frontend typecheck
cd frontend && npx tsc --noEmit
```

關鍵測試：
- **plugin 載入隔離**：壞 plugin 不影響其他 plugin 或 app 啟動；`host_api` semver 不相容被 skip。
- **AST guard**：`plugins/*` 不得 import host 內部模組（兩層 AI runtime 隔離防線）。
- **實作韌性**：work-branch HEAD 釘回（agent git-checkout 切走也不丟工作）、並行守衛原子化（兩分頁同時啟動只放行一個）、孤兒 run 啟動恢復。
- **batch / roles**：依序 + continue/stop-on-failure、冪等重跑（跳過已 merge 的 story）、reviewer verdict 解析。

---

## 設計鐵則（spec §2，貫穿全系統）

1. **Plugin-first**：core 只提供 framework + contracts，所有「能力」都是 plugin。
2. **Data-driven flow**：流程是資料（DB/config），不是程式碼。
3. **Host owns all I/O**：plugin 永遠拿不到 DB connection / 檔案系統 raw access。
4. **兩種 AI runtime 嚴格分離**：sync one-shot（stage 生成）vs async long-running（M5 實作）不可 cross-import。
5. **表單優先 UI**：所有客製化（建 workflow、編 agent、裝 plugin）用表單／清單／選單；**不做** 視覺化拖拉 DAG。

---

## 技術棧

| 層 | 選型 |
|---|---|
| Backend | Python 3.12, FastAPI, SQLite WAL |
| 流程編排 | 自製輕量 `WorkflowEngine`（純 Python）—— **不用** LangGraph |
| Manifest 解析 | 內建 `tomllib`（零新依賴） |
| Model 接入 | CLI adapters（claude-cli / codex-cli），registry 模式 |
| Frontend | Next 16 (App Router) + TypeScript + Tailwind v4 |
| 字型 | Fraunces (display, italic) × Geist Sans × Geist Mono |
| 測試 | pytest（backend, 327 tests）、tsc typecheck（frontend） |

---

## 未來可選

- **harness 三階段記 token / 成本**：目前 Flight Log 成本只含 implement 側；prd / 架構 / stories 要記 usage 需擴充 `ModelAdapter` 契約 + `harness_runs` schema。
- **claude-api ModelAdapter**：目前以 CLI adapter 為主，API adapter 為未來可選。
