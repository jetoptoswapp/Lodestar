# Lodestar — Plugin-First AI 需求工程平台

把「想法 → PRD → 架構 → 使用者故事 → 交付 →（可選）自動實作」串成 pipeline。每個 stage / agent / delivery target 從第一天起都是可獨立打包安裝的 plugin。

> **狀態**：M0 完成（plugin framework 地基 + dogfood integration + 前端 mock）。
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

Integration 憑證（GitHub PAT 等）以 **server-side keystore（Fernet 加密）** 儲存，明文不留在瀏覽器。

- **開發**：金鑰自動生成於 `backend/data/.keystore.key`（權限 0600，已 gitignore），免設定。
- **正式部署**：建議用環境變數 `LODESTAR_KEYSTORE_KEY` 注入金鑰（base64 Fernet key），由外部秘密管理（vault / k8s secret）統一保管、不落專案目錄：
  ```bash
  export LODESTAR_KEYSTORE_KEY="$(backend/.venv/bin/python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
  ```
  > 換金鑰會使既有密文無法解密（keystore 視為無憑證、需重新輸入），請妥善保存。

---

## 專案結構

```
ai-tool-v3/
├── start.sh                              # 一鍵起 backend + frontend
├── pyproject.toml                        # pytest 設定（pythonpath = backend）
├── ai-agent-plug-in-magical-brooks.md    # 建構規格書（spec）
├── backend/
│   ├── plugin_api/                       # plugin 與 host 的唯一介面（plugin 只 import 這裡）
│   │   ├── stage.py / workflow.py / integration.py / model.py
│   │   ├── runner.py / harness.py / host.py / common.py
│   ├── persistence/                      # 唯一碰 DB 的層（SQLite WAL）
│   │   ├── schema.sql / dal.py / migrations.py
│   ├── plugin_loader.py                  # discover / semver / 拓樸排序 / 兩階段註冊 / 隔離失敗
│   ├── plugin_host.py                    # Registry + PluginHost 實作 + PluginLoadInfo
│   ├── app.py                            # FastAPI app + lifespan（migrate + load_all）+ 通用 endpoint
│   ├── api_models.py / api_errors.py     # pydantic schema + 結構化錯誤
│   ├── plugins/
│   │   └── builtin_integrations/         # dogfood：GitHub / Jira / GitLab IntegrationSpec
│   │       ├── plugin.toml / register.py
│   ├── tests/                            # pytest（6 tests）
│   │   ├── test_loader.py                # semver、載入 builtin、壞 plugin 隔離、host_api skip
│   │   ├── test_api.py                   # /api/health, /api/stages, /api/plugins, /api/integrations
│   │   ├── test_isolation.py             # AST guard：plugin 不得 import host 內部模組
│   ├── requirements.txt
│   └── .venv/                            # ignored
└── frontend/
    ├── app/
    │   ├── layout.tsx                    # Fraunces × Geist Sans / Mono 字型
    │   ├── globals.css                   # Industrial Cobalt × Drafting Dusk tokens
    │   └── page.tsx                      # M0 mock（多 view + multi-agent）
    └── package.json                      # `dev / start` 都鎖 port 8724
```

---

## M0 完成判準

- [x] `./start.sh` 起得來
- [x] backend lifespan log 顯示 `loaded plugin: builtin_integrations v1.0.0`
- [x] `/api/plugins` 回報 builtin_integrations enabled / provides github,jira,gitlab
- [x] 前端 http://localhost:8724 打得開（靜態 mock）
- [x] `python -m pytest` 全綠（6 tests）

---

## 測試

```bash
# Backend（從專案根；pyproject 已設 pythonpath=backend, testpaths=backend/tests）
backend/.venv/bin/python -m pytest

# Frontend typecheck
cd frontend && npx tsc --noEmit
```

關鍵測試（M0 必綠）：
- **plugin 載入隔離**：壞 plugin 不影響其他 plugin 或 app 啟動
- **host_api semver**：不相容版本被 skip + log warn
- **AST guard**：`plugins/*` 不得 import host 內部模組（兩層 AI runtime 隔離防線）
- **通用 endpoint**：catalog / plugins / integrations 回傳形狀正確

---

## 設計鐵則（spec §2，貫穿全系統）

1. **Plugin-first**：core 只提供 framework + contracts，所有「能力」都是 plugin
2. **Data-driven flow**：流程是資料（DB/config），不是程式碼
3. **Host owns all I/O**：plugin 永遠拿不到 DB connection / 檔案系統 raw access
4. **兩種 AI runtime 嚴格分離**：sync one-shot（stage 生成）vs async long-running（M5 實作）不可 cross-import
5. **表單優先 UI**：所有客製化（建 workflow、編 agent、裝 plugin）用表單／清單／選單；**不做** 視覺化拖拉 DAG

---

## 技術棧

| 層 | 選型 |
|---|---|
| Backend | Python 3.12, FastAPI, SQLite WAL |
| 流程編排 | 自製輕量 `WorkflowEngine`（純 Python）—— **不用** LangGraph |
| Manifest 解析 | 內建 `tomllib`（零新依賴） |
| Model 接入 | CLI adapters（claude-cli / codex-cli）+ local Ollama，registry 模式（M1 起） |
| Frontend | Next 16 (App Router) + TypeScript + Tailwind v4 |
| 字型 | Fraunces (display, italic) × Geist Sans × Geist Mono |
| 測試 | pytest（backend）、tsc typecheck（frontend） |

---

## 下一步（依里程碑）

- **M1** 一個 stage 端到端：`builtin_core_stages` 先做 `prd`、`WorkflowEngine.dispatch`、`HarnessRunner`、`builtin_models` 註冊 claude-cli `ModelAdapter`、前端 `useStageCatalog()` 真實接 `/api/stages`
- **M2** 補齊 architecture / stories + `default` workflow + `builtin_agents` seed
- **M3** Workflow / Agent 編輯器 + per-thread workflow（含 [Multi-agent stage binding](#) 擴展：1:N + collaboration role）
- **M4** 第三方 plugin 打包 / 分發 + plugin 管理 UI
- **M5**（可選）交付發佈 + 自動實作 agent（dispatch runtime：lead 拆任務 + subagent 並行 worker）
