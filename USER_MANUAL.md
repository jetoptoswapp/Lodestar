# Lodestar 使用手冊

Lodestar 是以 plugin 為核心的 AI 需求工程平台。它把一個模糊的想法，沿著有「審核閘門」的管線推到可交付的程式碼：

> **想法 → PRD → 架構 ∥ UI 設計 → 使用者故事 → 交付（GitHub/GitLab issue）→ 自動實作（開 PR/MR）**

由兩個服務組成：FastAPI 後端（Python，:8723）＋ Next.js 前端（Node，:8724）。另內建「製造業 RCA 根因分析 Copilot」領域 plugin 作為替代用途。

> [!WARNING]
> **引擎是 AI CLI（Claude CLI）。沒有安裝 `claude` 並登入，整個工具產不出任何東西。**
> 每個階段生成、對話、自動實作都會 shell out 到你電腦上的 `claude`。App 仍可啟動、UI 仍會開，但一按生成就報錯（`RuntimeError: claude CLI 不在 PATH`，或自動實作 exit 127）。
> 認證請用 **`claude auth login`**（OAuth，搭配 Claude 訂閱）；Lodestar 不讀 `ANTHROPIC_API_KEY`。

## 目錄

1. [前置需求](#前置需求)
2. [安裝](#安裝)（macOS / Linux / Windows）
3. [啟動與停止](#啟動與停止)
4. [一鍵執行](#一鍵執行)
5. [使用教學](#使用教學)
6. [資料與備份](#資料與備份)（哪些在 git、哪些在 DB）
7. [設定與憑證](#設定與憑證)
8. [疑難排解](#疑難排解)
9. [附錄](#附錄)

---

## 前置需求

| 需求 | 必備？ | 為什麼 | 驗證 |
|---|---|---|---|
| **Claude CLI（`claude`）** | **必備（核心引擎）** | 每個階段生成、對話、自動實作都靠它；**且必須已登入** | `which claude && claude --version`，再 `claude auth status` |
| **Python 3.11+（建議 3.12）** | 必備 | 跑 FastAPI 後端；請用 `python3.12` 明確建 venv | `python3.12 --version` |
| **Node 22+** | 必備 | 跑 Next 16 + React 19 前端 | `node --version` |
| **npm** | 必備 | 裝前端依賴（隨 Node 附帶） | `npm --version` |
| **git** | 必備 | 交付 / 自動實作 / 文件發佈 / 規格同步都會 shell out 到 git | `git --version` |
| codex CLI / agy CLI | 可選 | 改選 `codex-cli` / `agy-cli` model adapter 時才要 | `which codex` / `which agy` |
| tesseract | 可選 | 圖片附件本地 OCR；缺了會優雅跳過（`claude` 本就能原生讀圖） | `which tesseract` |
| GitHub / GitLab token | 交付時要 | 發 issue、開 PR/MR、auto-merge；存加密 keystore | UI 的 INTEGRATIONS |

> [!CAUTION]
> **系統 Node 16 不相容**（Next 16 / React 19 跑不起來）。`package.json` 沒 `engines`，npm 安裝不會擋，是 `next dev`/`build` 才爆。請切到 Node 22+（`nvm use 22`）。
>
> **登入提醒**：shell 用 `claude auth login`；`/login` 只在互動式 `claude` session 內有效。Lodestar 不傳 `--model`，實際模型由你登入的 CLI 預設決定。
>
> **安全**：自動實作 runner 以 `--permission-mode bypassPermissions` 啟動 agent，會在隔離 clone 內非互動執行任意 Bash（npm/pytest/build）。請只對信任的 repo 使用。

---

## 安裝

三平台首次設定最後都是同兩步：建 Python venv + 裝後端依賴、`npm install` 裝前端依賴（可用 repo 附的 `setup.*` 一鍵完成，見[一鍵執行](#一鍵執行)）。

### 全新機器：一行指令（最省事）

不想先 clone source？這條一行指令會自動 **裝 git → clone 公開 repo（預設 `~/Lodestar`）→ 裝 Python/Node/claude/依賴 → 啟動**。只需要這一步（外加首次的 `claude auth login`）。

```powershell
# Windows（PowerShell）
irm https://raw.githubusercontent.com/jetoptoswapp/Lodestar/main/Install-Lodestar.ps1 | iex
```

```bash
# macOS / Linux（用 bash <(...) 才能正常跳出 sudo / 登入提示）
bash <(curl -fsSL https://raw.githubusercontent.com/jetoptoswapp/Lodestar/main/install.sh)
```

> 安裝位置可覆寫：Windows `$env:LODESTAR_DIR="D:\Lodestar"`；macOS/Linux `LODESTAR_DIR=/path bash <(curl …)`。之後日常啟動進到該資料夾跑 `./start.sh`（Windows 雙擊 `start.bat`）即可，不會重裝。
>
> 偏好手動、或要看每一步在做什麼，往下看各 OS 章節。

### macOS

```bash
xcode-select --install                       # git / compilers
brew install python@3.12 node git
brew install tesseract tesseract-lang        # 可選，OCR
claude auth login                            # 核心引擎，登入（claude auth status 確認）

python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

> 用 nvm 管 node 的話，啟動前 `nvm use 22`（或用 `Lodestar.command`，會自動補最新 nvm Node 22）。

### Linux

```bash
# Debian / Ubuntu（python3.12-venv 是獨立套件，務必一起裝；lsof 給 start.sh 用）
sudo apt update && sudo apt install -y python3.12 python3.12-venv git lsof
sudo apt install -y tesseract-ocr tesseract-ocr-chi-tra   # 可選
# apt 的 nodejs 常太舊；用 NodeSource 或 nvm 取得 Node 22+：
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs

claude auth login
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

Fedora / RHEL：`sudo dnf install -y python3.12 git lsof`、`tesseract tesseract-langpack-chi_tra`（可選）；Node 22+ 建議 nvm / NodeSource。

### Windows

> [!IMPORTANT]
> **`start.sh` 是 Unix 專屬（bash + `lsof` + `trap`），cmd/PowerShell 跑不動。** 三條路（由易到難）：

**路線 A：一鍵自動安裝（推薦給非開發者）**

雙擊專案根目錄的 **`Lodestar-Install.bat`**（底層 `windows-bootstrap.ps1`）。它會：

1. 偵測環境、**只裝缺的**：用 `winget` 裝 Python 3.12 / Node（LTS≥22）/ git，用 `npm` 裝 `claude`。
2. 檢查登入狀態，**沒登入才**開瀏覽器 `claude auth login`。
3. 建 `backend\.venv` + 裝前後端依賴（已有就略過）。
4. 啟動服務，數秒後自動開 http://localhost:8724。

> 需要 Windows 10+ 內建 **winget**（缺則到 Microsoft Store 裝「App Installer」）。安裝可能跳 **UAC**。**可重複執行**：若剛裝完工具本視窗抓不到（PATH 未生效），它會請你關閉重跑，第二次會略過已裝的。**唯一不能省的是第一次 `claude auth login`。** 設定好後，日後只要雙擊 `start.bat`。

**路線 B：WSL2** — `wsl --install -d Ubuntu`，進 Ubuntu 後照上面 **Linux** 章節做（含 `claude` 安裝/登入在 WSL 內），最後 `./start.sh` 原樣可跑。

**路線 C：原生 PowerShell（已自備環境者）** — venv python 在 `backend\.venv\Scripts\python.exe`：

```powershell
claude auth login
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\pip install -r backend\requirements.txt
npm --prefix frontend install
```

啟動見[一鍵執行](#一鍵執行)的 `start.ps1` / `start.bat`，或下節手動兩終端機。

> uvicorn 綁 `0.0.0.0`，首次會跳 Windows 防火牆提示，請允許。dev keystore 金鑰檔在 Windows 無法套 POSIX `0600`（程式靜默略過），正式環境請改用 `LODESTAR_KEYSTORE_KEY`。

---

## 啟動與停止

**快樂路徑（macOS / Linux）**：`./start.sh` — 先用 `lsof` 釋放 8723/8724（**會 kill 佔用者，含前一個 Lodestar 實例**），再背景起後端 uvicorn 與前端 `npm run dev`。

> `start.sh` 只「檢查」`backend/.venv` 與 `frontend/node_modules`，**不會建立**；缺了會報錯，請先 `./setup.sh`。

**手動兩終端機（start.sh 跑不動時，例如 Windows 原生）**

```bash
# 終端機 1 — 後端（macOS / Linux）
cd backend && .venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8723 --log-level info
# 終端機 2 — 前端（先確認 node >= 22）
cd frontend && npm run dev          # = next dev -p 8724
```

```powershell
# Windows 原生
backend\.venv\Scripts\python -m uvicorn app:app --host 0.0.0.0 --port 8723 --app-dir backend
npm --prefix frontend run dev
```

**網址**

| 用途 | 網址 |
|---|---|
| 前端 UI | http://localhost:8724 |
| 後端 API | http://localhost:8723 |
| Swagger | http://localhost:8723/docs |
| 健康檢查 | http://localhost:8723/api/health（`curl` 可測） |

**停止**：`./start.sh` 啟動的按 `Ctrl-C`（trap 一併關閉）；手動的各自 `Ctrl-C`。重跑 `./start.sh` 會先釋放 port 再重啟。

---

## 一鍵執行

> [!TIP]
> repo 已附下列啟動器，可直接用。前提：`claude` 已安裝且登入。

| 作業系統 | 一次性設定 | 啟動 |
|---|---|---|
| **Windows（全自動，雙擊）** | `Lodestar-Install.bat`（連 runtime 都自動裝） | 之後雙擊 `start.bat` |
| macOS（雙擊） | `setup.command` | `Lodestar.command` |
| macOS / Linux（終端機） | `./setup.sh` | `./start.sh` |
| Linux（桌面捷徑） | `./setup.sh` | `Lodestar.desktop`（範本見下） |
| Windows（已自備環境） | `setup.ps1` | `start.bat` 或 `start.ps1` |

- **`Lodestar-Install.bat` + `windows-bootstrap.ps1`**：最接近「一鍵」—— 冪等偵測環境，用 winget/npm 補裝 Python/Node/git/claude，確認登入後建環境並啟動（詳見[安裝 → Windows → 路線 A](#windows)）。
- **`setup.*`**：建 venv + `pip install` + `npm install`，完成後提示 `claude auth login`。只需一次。
- **`Lodestar.command`（macOS 雙擊）**：自動補最新 nvm Node 22 → `./start.sh`（沒用 nvm 也能跑）。
- **`start.ps1` / `start.bat`（Windows）**：`start.sh` 的 PowerShell 對應版（釋放 port、起前後端、Ctrl-C 關閉）。

**Linux 桌面捷徑 `Lodestar.desktop`**（放 `~/.local/share/applications/`，路徑換成你的）：

```ini
[Desktop Entry]
Type=Application
Name=Lodestar
Exec=bash -lc 'cd /path/to/ai-tool-v3 && ./start.sh'
Terminal=true
Categories=Development;
```

> [!NOTE]
> **完全打包（Docker / Electron / PyInstaller）不切實際**：核心依賴外部 AI CLI，且每個使用者都要自己互動式 `claude auth login`（OAuth），這步打包不進去。所以務實的「一鍵」就是上述啟動器。Windows 的 `.ps1` 均存成 **UTF-8 with BOM**，避免 PowerShell 5.1 中文亂碼。

---

## 使用教學

前端是單頁多視圖，頂列：**WORKSPACE**（主工作區）/ **FLIGHT LOG**（專案總覽）/ **WORKFLOWS** / **AGENTS** / **SKILLS** / **PLUGINS**。

### 一條龍流程

1. **建立專案**：左側欄「＋」（或 `⌘N`/`Ctrl+N`）填名稱。每個 thread 是一條獨立管線。
2. **選 model / workflow**：頂列 MODEL 下拉（來自 `/api/models`，標可用/不可用，預設 `claude-cli`，選擇存 localStorage；不可用仍可選但送出回 `model.unavailable`）。stage 軌的 WorkflowSwitcher 切該專案 workflow：`default`（prd→ui_design→architecture→stories）/ `requirements_panel`（PRD 多 agent 討論）/ `modify_existing`（改既有 repo）/ `delivery_pipeline`（含 implement）/ RCA 系列。
3. **PRD**：可拖放上傳參考文件（md/txt/csv/json/pdf/docx/png/jpg…，後端把附件絕對路徑注入 prompt 讓 `claude` 用 Read tool 直接讀原檔，免本地 OCR）。按「✦ chart PRD」生成（約 30–60 秒）；SA agent 收斂成 Overview / Delivery Surface / FR-X / NFR-X，完成時附 `[PRD_READY]` 哨符。
4. **修訂 / 核准（閘門）**：修訂 = 一句指令讓 AI 輸出完整更新版；核准要求 artifact 非空。
   > [!IMPORTANT]
   > 下游階段在上游核准前 locked。上游重生成/改動 → 已核准下游自動退回 `needs_revision`（顯示 REVISE 徽章）。
5. **架構 ∥ UI 設計**：`default` 中 `ui_design` 在 `architecture` 前；架構以 `soft_depends_on` 參考 UI（純後端專案無 UI 也不被擋）。UI Designer 產設計理念 + design tokens + 每畫面自包含 HTML 原型，前端沙箱 iframe 預覽，可切 document/preview/code；去 HTML 後的設計稿餵給 stories。
6. **使用者故事**：須以 `# <專案> — User Stories` 起手、首故事 `Story 1.1`。
   > [!WARNING]
   > 前段截斷（如從 Story 5.3 才開始）或缺 Epic/Story 結構 → UI 橘色警告要求重生成，且 batch 自動實作會被擋。必須拿到完整 stories 才能發佈/實作。
7. **交付**：stories 標題列三顆按鈕 ——
   - **發佈到 tracker…**：用確定性 regex 把每個 `Story N.M` 拆成一個 issue（body/estimate/Epic group/labels），發 GitHub/GitLab，可先 preview，**重發只補缺漏**（冪等比對）。
   - **發佈文件…**：PRD/架構/UI 發到 Wiki。
   - **同步規格到 repo…**：規格 + `CLAUDE.md` commit 進 code repo 給實作 agent 讀。

   | 整合 | 狀態 |
   |---|---|
   | GitHub | 真實：發 issue、開 PR、auto-merge（含 rate-limit 退避） |
   | GitLab | 真實：發 issue、開 MR、merge |
   | Jira | 佔位 stub（publish 永遠回 `success=False`） |

### 設定憑證與交付目標

- **憑證**：頂列「⚙ INTEGRATIONS」→ 選 github/jira/gitlab → 填 token（GitHub 填 PAT，需含 **repo push + pull request** 權限；GitLab 填 Access Token，self-hosted 可填 Base URL）→ 加密存 server-side keystore（Fernet），明文不留瀏覽器。**同一把 GitHub token 同時供「發 issue」與「自動實作開 PR」。**
- **交付目標**：ProjectDeliveryModal 設該專案的 github/gitlab、`repo_mode`（new 開新 repo / 既有）、`repo_full_name`/owner/visibility，或 `local` 本機路徑。

### 自動實作（M5）

Stories 核准後解鎖。控制列可選：**runner**（`mock` 安全 dry-run / `claude-cli` 用 Claude 真跑 / `codex-cli` 用 Codex 真跑，來自 `/api/runners`）、**執行方式**（逐 issue batch / 整份 session）、**模式**（單一 fix-loop / 多角色 roles）、**過 gate 自動 merge**（batch 才有，預設開）、**target repo**。

> [!IMPORTANT]
> 真跑前必須：(1) INTEGRATIONS 存好 token（含 repo push + PR）；(2) 專案 delivery 指定 repo。否則回 `token_missing` / `delivery_not_configured`。

- **roles QA gate 回圈**：`lead` 拆計畫 →（`RD` 實作 → `tester` 跑 lint/type/測試 → `reviewer` 審）回圈，**硬上限 3 次**；reviewer `APPROVED` 且 tester 過才開 PR。agent 以 `bypassPermissions` 真跑 build/test，每 story 切乾淨 work branch、共用一份 working copy。
- **batch + auto-merge**：依 Story 編號（1.1→7.3）一次一個跑（過 gate 才換下一個，預設 continue-on-failure）；每 story 開 branch/PR（body 帶 `Closes #N` 並在 issue 留言）；過 gate 即 squash-merge 進 default branch，下一個從更新後 main 切；冪等重跑跳過已關 issue / 已有 open PR。
- **嘗試 chips / log**：狀態 pill + 每次嘗試一個 chip，可展開 stream-json 整理的事件 + 統計（files/tools/turns/elapsed/cost）。成功顯示 PR banner，可隨時取消。

> [!NOTE]
> 同專案共用一份 working copy，同時只能跑一個實作（兩分頁同按只放行一個，另一個回 `409 impl_in_progress`）。

### 其他

- **Flight Log**：每專案一份唯讀總覽（階段時間軸、逐 story 現況/耗時/重試/成本、token·$；來自 `/api/projects/{tid}/summary`）。目前成本只含 implement 側。
- **修改既有 repo（`modify_existing`）**：單 stage `change_request`（讀 repo → 談變更/解 bug → 產實作 brief）；brief 可直接當 single implement 的 story，用 `claude-cli` 開一個 PR；也可從既有 repo 匯入 issue 當起點。
- **RCA Copilot**：開新 thread → 選 RCA workflow（單代理 intake→analysis／多代理鏈 intake→baseline→causal→knowledge→synthesis／Agentic 規劃）；在 RCA Analysis（單）或 Baseline（多）上傳製程/良率資料，Generate 得候選根因表（信心/證據/下一步）。範例：`cd backend && python -m scripts.seed_rca`（建合成範例）、`reset_rca_demo`（還原）。**它是 Copilot 不是 Judge**，輸出為候選根因，需現場確認。
- **管理**：**PLUGINS** 裝/停用能力（內建 `builtin_*` 不可停；裝法：丟進 `backend/plugins/` 重啟，或 `pip install` 宣告 `lodestar.plugins` entry-point）。**WORKFLOWS** 編 per-thread workflow。**AGENTS** 編 agent（system_prompt/persona/model/skills/tools）+ 多角色綁定（lead/peer/subagent）。全表單優先，不做拖拉 DAG。

---

## 資料與備份

> [!IMPORTANT]
> Lodestar 是「**流程即資料**」：agent / workflow 的**定義存在 DB**，不是程式碼。這決定了「哪些東西在 git、哪些不在」。

| 東西 | 存哪 | 在 git？ |
|---|---|---|
| **Stage 定義**（PRD/架構/UI/Stories/Change Request/Build Verify） | plugin 程式碼（無定義表，不能 UI 自訂） | ✅ 是 |
| **內建 workflow/agent 的「預設版」** | 程式碼 `backend/plugins/builtin_*`，開機 seed 進 DB | ✅ 是（預設版） |
| **你新建 / 編輯過的 workflow、agent、技能綁定** | DB（`backend/data/app.db`） | ❌ 否（被 gitignore） |
| 各專案 PRD/Stories/架構/實作 log、附件、憑證密文 | DB / `backend/data/` | ❌ 否 |

**資料位置**（皆在 gitignore 的 `backend/data/`）：

| 內容 | 路徑 |
|---|---|
| SQLite DB（WAL） | `backend/data/app.db`（+ `-wal`/`-shm`） |
| 加密金鑰（後備） | `backend/data/.keystore.key`（0600） |
| 附件上傳 | `backend/data/uploads/` |
| 實作 working copy | `backend/data/impl_work/` |

> [!CAUTION]
> **絕對不要把 `backend/data/` 整包丟進 git**，尤其公開 repo —— 裡面有 `.keystore.key`（解密金鑰）＋ `app.db`（含加密的 GitHub/GitLab token 與全部專案內容）。兩者一起公開 = 憑證外洩。DB 也很大（數百 MB），不適合進 git 歷史。

### 備份自訂 agent / workflow

repo 附的 **`config-export/`** 是 DB 中自訂設定的 JSON 快照（**不含憑證/金鑰**，可安全進 git）：`agents.json`（agent + 技能綁定）、`workflows.json`、`skills.json`。改過設定後重新匯出再 commit：

```bash
backend/.venv/bin/python - <<'PY'
import sqlite3, json
con = sqlite3.connect("file:backend/data/app.db?mode=ro", uri=True); con.row_factory = sqlite3.Row
r = lambda q: [dict(x) for x in con.execute(q)]
ag = r("SELECT * FROM agents ORDER BY role, agent_id")
for a in ag: a["tools"] = json.loads(a.get("tools") or "[]")
wf = r("SELECT * FROM workflow_definitions ORDER BY created_at")
for w in wf: w["stages"] = json.loads(w.pop("stages_json") or "[]")
d = lambda n,o: open(f"config-export/{n}","w",encoding="utf-8").write(json.dumps(o,ensure_ascii=False,indent=2)+"\n")
d("agents.json", {"agents": ag, "agent_skills": r("SELECT * FROM agent_skills ORDER BY agent_id, sort_order")})
d("workflows.json", {"workflows": wf})
d("skills.json", {"skills": r("SELECT * FROM skills ORDER BY skill_id")})
print("re-exported")
PY
```

還原：目前無自動匯入端點，請以 JSON 為真實來源，在 AGENTS / WORKFLOWS 頁手動重建（細節見 `config-export/README.md`）。若要讓自訂定義「變成程式碼、teammates clone 就有」，正規做法是寫成 plugin 裡的 `WorkflowSpec` / `AgentSpec`，開機自動 seed。

> 要完整備份「所有專案內容」，請複製整顆 `app.db` 到 git 以外的位置（雲端/本機備份），別進 git。

---

## 設定與憑證

整合憑證（GitHub PAT、GitLab token）在 server 端以 Fernet 加密。金鑰來源依序：

1. 環境變數 `LODESTAR_KEYSTORE_KEY`（base64 Fernet key，**正式環境建議**）。
2. 否則 `backend/data/.keystore.key`（首次自動產生，`chmod 0600`）。

產生正式金鑰：

```bash
export LODESTAR_KEYSTORE_KEY="$(backend/.venv/bin/python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
```

> [!CAUTION]
> **輪替金鑰會使既有密文無法解密** —— keystore 被當成空的，需在 INTEGRATIONS 重新輸入所有憑證。

---

## 疑難排解

> [!WARNING]
> **生成全部失敗 / `claude CLI 不在 PATH` / 自動實作 exit 127** → `claude` 沒裝、不在 PATH、或沒登入。`which claude && claude --version` 確認在 PATH，`claude auth status` 確認登入（沒登入就 `claude auth login`）。設 `ANTHROPIC_API_KEY` 沒用，程式碼不讀它。

| 症狀 | 處理 |
|---|---|
| 前端跑不起來 / `next dev` 報錯 | Node 太舊（16 不相容）。`node --version` ≥ 22；nvm 先 `nvm use 22`。 |
| `start.sh` 說缺 venv / node_modules | 還沒首次設定。跑 `./setup.sh`（或手動建 venv + `npm install`）。 |
| venv 用錯 Python（建出 3.9） | 用 `python3.12` 明確建；Debian/Ubuntu 另裝 `python3.12-venv`。 |
| Port 被佔用（8723/8724） | `start.sh`/`start.ps1` 會自動釋放。手動：`lsof -nP -tiTCP:8723 -sTCP:LISTEN`（mac/Linux）/ `Get-NetTCPConnection -LocalPort 8723 -State Listen`（Win），再結束程序。 |
| Windows `start.sh` 跑不動 | 正常（Unix 專屬）。改用 WSL2 或 `start.bat` / `start.ps1`。 |
| 圖片附件沒 OCR | `tesseract` 沒裝（`brew install tesseract tesseract-lang`）；多數情況 `claude` 原生讀圖即可。 |
| 輪替 keystore 金鑰後憑證壞了 | 舊密文無法解密。在 INTEGRATIONS 重新輸入 token。 |
| 自動實作回 `token_missing` / `delivery_not_configured` | 先在 INTEGRATIONS 存 token（含 repo push + PR），並在專案 delivery 指定 repo。 |
| 第二個實作回 `409 impl_in_progress` | 同專案共用一份 working copy，同時只能一個；等前一個完成或取消。 |
| Stories 一直被警告 / batch 被擋 | 前段截斷或不符 `## Epic N:` / `### Story N.M —` 格式，需重生成完整 stories。 |
| 某 story 標 `failed` | fix-loop 硬上限 3 次；超過仍失敗該 story 標 failed，batch 預設續跑下一個。 |
| 限流後會自動續跑嗎？ | 會（針對 5 小時訂閱用量上限）。等待參數見[附錄](#附錄)的 `LODESTAR_RATELIMIT_*`。 |
| 前端連不到後端 / CORS | 前端 API base 寫死 `http://localhost:8723`（唯一覆寫是 runtime 全域 `window.__LODESTAR_API__`，無 `NEXT_PUBLIC_*`）。請維持預設 port。 |

---

## 附錄

### A. Ports / URLs

前端 :8724、後端 :8723（綁 `0.0.0.0`）、Swagger `/docs`、健康檢查 `/api/health`。Port 刻意避開 3000/8000/8080/5173。

### B. 環境變數

| 變數 | 預設 | 用途 |
|---|---|---|
| `AITOOL_DB` | `backend/data/app.db` | 覆寫 DB 路徑；uploads/ 與金鑰檔跟著其父目錄 |
| `LODESTAR_KEYSTORE_KEY` | （無，後備到 key 檔） | Fernet 加密金鑰（正式環境建議注入） |
| `LODESTAR_UPLOADS_DIR` | `<DB 目錄>/uploads` | 附件根目錄；開機 `setdefault`，供 `claude` `--add-dir` |
| `LODESTAR_IMPL_BASE_REPO` | （空） | 本地 base repo 供 implement 準備 worktree |
| `LODESTAR_RATELIMIT_DEFAULT_WAIT` / `_MAX_WAIT` / `_BUFFER` / `_MAX_CYCLES` | `3600` / `21600` / `60` / `6` | 限流後自動等待續跑參數（秒 / 次） |

> `ANTHROPIC_API_KEY` 不被程式讀取（僅出現在註解描述一條未接上的 `--bare` 路徑）。

### C. 跑測試

```bash
backend/.venv/bin/python -m pytest      # 後端，repo 根執行（約 440 個測試 / 46 檔）
cd frontend && npx tsc --noEmit         # 前端型別檢查
cd frontend && npm run lint             # eslint
```

### D. repo 附帶啟動器 / 工具

| 檔案 | 平台 | 作用 |
|---|---|---|
| `Install-Lodestar.ps1` / `install.sh` | Windows / macOS·Linux | **單檔 standalone 安裝器**：clone 公開 repo → 裝環境 → 啟動（見[全新機器一行指令](#全新機器一行指令最省事)） |
| `start.sh` / `setup.sh` | macOS / Linux | 啟動 / 一次性設定 |
| `setup.command` / `Lodestar.command` | macOS（雙擊） | 設定 / 啟動 |
| `Lodestar-Install.bat` + `windows-bootstrap.ps1` | Windows（雙擊） | **全自動**：偵測補裝環境 → 確認登入 → 建環境 → 啟動 |
| `setup.ps1` / `start.ps1` / `start.bat` | Windows | 設定 / 啟動（已自備環境） |
| `config-export/` | — | 自訂 agent/workflow 的 JSON 快照（版控備份，不含憑證） |

### E. 文件連結

| 文件 | 路徑 |
|---|---|
| 專案 README | `README.md` |
| Plugin 開發指南 | `docs/PLUGIN_GUIDE.md` |
| RCA 快速上手 | `docs/RCA_QUICKSTART.md` |
| 建構規格書 | `ai-agent-plug-in-magical-brooks.md` |
| 自訂設定快照說明 | `config-export/README.md` |

> `frontend/README.md` 是 create-next-app 樣板（提到 port 3000），**非**本專案啟動說明，請以 `start.sh` / `package.json` 為準（:8724）。
