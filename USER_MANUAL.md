# Lodestar 使用手冊

## 這是什麼

Lodestar 是一個以 plugin 為核心的 AI 需求工程平台。它把一個模糊的想法，沿著一條有「審核閘門（approval gate）」的管線，一路推到可交付的程式碼：

> **想法 → PRD → 架構 ∥ UI 設計 → 使用者故事 → 交付（GitHub/GitLab issue）→ 自動實作（開 PR/MR）**

它由兩個服務組成：一個 FastAPI 後端（Python，port 8723）與一個 Next.js 前端（Node，port 8724）。除了需求工程，它還內建一個「製造業 RCA（根因分析）Copilot」領域 plugin 作為替代用途。

> [!WARNING]
> **Lodestar 的引擎是 AI CLI（Claude CLI）。沒有安裝 `claude` 並完成登入，整個工具無法產生任何東西。**
>
> 每一個階段的生成、每一次對話、以及自動實作的 runner，預設都是透過你電腦上的 `claude` 命令列工具來執行。App 本身可以啟動、UI 也會打開、甚至會在 model 清單裡列出 `claude-cli`（但標記為「不可用」），可是只要你按下生成，後端就會直接報錯（`RuntimeError: claude CLI 不在 PATH`，或自動實作回傳 exit code 127）。
>
> 認證方式是「登入 Claude CLI」——用 `claude auth login`（OAuth keychain，搭配 Claude 訂閱）。Lodestar 的程式碼本身不會讀取 `ANTHROPIC_API_KEY`，所以請以「讓 `claude` 自己能正常跑」為準。

---

## 必備條件 / 前置需求

下表列出每個前置需求、為什麼需要它、以及如何驗證它已安裝。

| 需求 | 是否必備 | 為什麼需要 | 驗證指令 |
|---|---|---|---|
| **Claude CLI（`claude`）** | **必備（核心引擎）** | 預設 model `claude-cli` 會 shell out 到 `claude` 二進位檔，用於每個階段生成、對話、與自動實作。沒有它就什麼都產不出來。**而且必須已登入。** | `which claude && claude --version`，再 `claude auth status` |
| **Python 3.11+（建議 / 已驗證 3.12）** | 必備 | 執行 FastAPI 後端。請用 `python3.12` 明確建立 venv（系統 `python3` 可能是舊版）。 | `python3.12 --version` |
| **Node 22+** | 必備 | 執行 / 建置 Next.js 前端（Next 16.2.6 + React 19.2.4）。 | `node --version` |
| **npm** | 必備 | 安裝前端依賴；隨 Node 一起附帶。 | `npm --version` |
| **git** | 必備 | 所有「交付 / 自動實作 / 文件發佈 / 規格同步」流程都會 shell out 到 git（clone、worktree、commit、push）。 | `which git && git --version` |
| codex CLI（`codex`） | 可選 | 只有在你改選 `codex-cli` model adapter 時才需要。 | `which codex && codex --version` |
| agy CLI（`agy`） | 可選 | 只有在你改選 `agy-cli` model adapter 時才需要。 | `which agy` |
| tesseract | 可選 | 上傳圖片附件時做本地 OCR。沒有它會優雅降級（跳過 OCR）；而且 `claude-cli` 本來就能用原生視覺直接讀圖片，所以這只是備援路徑。 | `which tesseract` |
| GitHub / GitLab token | 可選（交付時才需要） | 發佈 issue、開 PR/MR、auto-merge 時用。存在加密 keystore，不是用 `gh`/`glab` CLI。 | 在 UI 的 INTEGRATIONS 設定 |

> [!CAUTION]
> **系統 Node 16 不相容。** Next 16 / React 19 無法在 Node 16 上跑。`package.json` 沒有 `engines` 欄位，所以 npm **不會**在安裝階段擋你 —— 它會在 `next dev` / `build` 時才失敗。如果你的系統是 Node 16（macOS 常見），請先切換到 Node 22+（例如 `nvm use 22`）。

> [!WARNING]
> 再次強調 `claude` 必須**已安裝且已登入**：
> - 安裝後執行 `claude auth login` 完成登入（OAuth，搭配 Claude 訂閱）。
> - 用 `claude auth status` 確認登入狀態。
> - 註：`/login` 是「互動式 `claude` session 內」的 slash 指令；在一般 shell 要登入請用子命令 `claude auth login`。
>
> Lodestar 不會傳 `--model` 旗標，所以實際用到的模型（Sonnet/Opus 等）是你登入的 Claude CLI 自己的預設值。

### 安全須知（自動實作）

自動實作的 runner 會以 `--permission-mode bypassPermissions` 啟動 agent，讓它可以在隔離的 clone 內**非互動地執行任意 Bash（npm/cargo/pytest/build/test）**。這是設計使然，但請只在你信任的 repo 上使用。

---

## 安裝（依作業系統）

> 三個平台的「首次設定」最後都是同樣兩步：建立 Python venv + 安裝後端依賴、`npm install` 安裝前端依賴。這兩步可以用 repo 附的 `setup.*` 腳本一鍵完成（見「一鍵執行」）。

### macOS

```bash
# 1. 安裝工具鏈（Xcode CLT 提供 git/compilers，Homebrew 裝其餘）
xcode-select --install
brew install python@3.12 node git
brew install tesseract tesseract-lang   # 可選，圖片 OCR

# 2. 安裝 Claude CLI 並登入（核心引擎，缺它無法運作）
#    依官方說明安裝 claude，然後：
claude auth login          # 登入；claude auth status 可確認

# 3. 首次設定（在專案根目錄執行，只需一次）
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

> 若你的 `node` 是用 nvm 管理，啟動前請確認 `nvm use 22`（或用 repo 附的 `Lodestar.command`，它會自動把最新的 nvm Node 22 加進 PATH，見「一鍵執行」）。

### Linux

Debian / Ubuntu：

```bash
# 1. 工具鏈（python3.12-venv 是「獨立套件」，務必一起裝；lsof 給 start.sh 用）
sudo apt update
sudo apt install -y python3.12 python3.12-venv git lsof
sudo apt install -y tesseract-ocr tesseract-ocr-chi-tra   # 可選，OCR
#    apt 的 nodejs 可能太舊 —— 建議用 NodeSource (setup_22.x) 或 nvm 取得 Node 22+
#    例如：curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs

# 2. 安裝 Claude CLI 並登入
claude auth login          # claude auth status 可確認

# 3. 首次設定（只需一次）
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

Fedora / RHEL：

```bash
sudo dnf install -y python3.12 git lsof
sudo dnf install -y tesseract tesseract-langpack-chi_tra   # 可選
# Node 從 dnf 可能落後，建議 nvm / NodeSource 取得 Node 22+
```

### Windows

> [!IMPORTANT]
> **`start.sh` 是 Unix 專屬（bash + `lsof` + `trap`），無法在 cmd 或 PowerShell 原生執行。** Windows 有三條路（由易到難）：
> 1. **一鍵自動安裝（最簡單，推薦給非開發者）** —— 雙擊 `Lodestar-Install.bat`，自動偵測並安裝缺少的環境，再啟動。
> 2. **WSL2** —— 在 WSL2 裡有真正的 bash，`start.sh` 可原封不動執行。
> 3. **原生 PowerShell（用 repo 附的 `start.ps1` / `start.bat`）** —— 不依賴 `start.sh`。

**路線 A：一鍵自動安裝（推薦）**

雙擊專案根目錄的 **`Lodestar-Install.bat`**（底層是 `windows-bootstrap.ps1`）。它會：

1. 偵測機器環境，**已安裝的略過、只裝缺的**：用內建的 `winget` 裝 Python 3.12 / Node（LTS，≥22）/ git，再用 `npm` 裝 `claude` CLI。
2. 檢查 `claude` 登入狀態；**沒登入才**開瀏覽器跑 `claude auth login`（用你自己的 Claude 帳號）。
3. 建立 `backend\.venv` + 安裝前後端依賴（已存在就略過）。
4. 啟動前後端，並在數秒後自動開啟 http://localhost:8724。

> [!NOTE]
> - 需要 **Windows 10+ 內建的 winget**（「應用程式安裝程式 / App Installer」）。沒有的話到 Microsoft Store 裝一下，或改走路線 B / C。
> - 安裝工具時可能跳 **UAC**，請允許。
> - 整個過程**可重複執行**：若剛裝完某個工具、本視窗還抓不到（PATH 尚未生效），它會請你「關閉視窗再雙擊一次」，第二次會略過已裝好的、很快跑完。
> - **唯一無法省略的步驟是第一次的 `claude auth login`**（綁個人 Claude 訂閱的瀏覽器登入），這是設計上打不進安裝包的。

設定好之後，之後每次啟動只要雙擊 `start.bat`（不會再重裝）即可。

**路線 B：WSL2**

```powershell
# 在 Windows PowerShell（系統管理員）
wsl --install -d Ubuntu
```

進入 Ubuntu 後，照上面的 **Linux** 章節安裝工具鏈、`claude auth login` 登入、做首次設定，最後 `./start.sh`。所有東西（`start.sh`、`lsof`、`trap`、`&`/`wait`）都原樣運作。請把 `claude` 安裝並登入在 **WSL distro 內**。

**路線 C：原生 PowerShell（不用 start.sh，手動裝好環境者）**

注意 Windows venv 的 python 在 `backend\.venv\Scripts\python.exe`（不是 `bin/python`）。

```powershell
# 1. 安裝 Python 3.12、Node 22+、git（例如用官方安裝檔或 winget），並安裝 + 登入 claude
claude auth login

# 2. 首次設定（只需一次；或直接跑 repo 附的 .\setup.ps1）
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\pip install -r backend\requirements.txt
npm --prefix frontend install
```

啟動方式見下一節「啟動 / 停止」的手動兩終端機路徑（Windows 版），或直接用「一鍵執行」的 `start.bat`。

> [!NOTE]
> uvicorn 綁定 `0.0.0.0`，首次啟動 Windows 防火牆會跳出提示，請允許。若你只要本機使用，也可以改綁 `127.0.0.1`。
>
> 另外，dev 用的 keystore 金鑰檔在 Windows 上無法套用 POSIX 的 `0600` 權限（程式會靜默略過），所以該檔不會被權限保護。正式環境請改用 `LODESTAR_KEYSTORE_KEY` 環境變數。

---

## 啟動 / 停止

### 快樂路徑：`./start.sh`（macOS / Linux）

```bash
./start.sh
```

`start.sh` 會：先用 `lsof` 釋放 8723 與 8724 兩個 port（**會 kill 掉佔用該 port 的任何程序，包括前一個還在跑的 Lodestar**），然後背景啟動後端 uvicorn 與前端 `npm run dev`。

> [!NOTE]
> `start.sh` 只會「檢查」`backend/.venv` 與 `frontend/node_modules` 是否存在，**不會**幫你建立。缺了它會直接報錯並提示你先做首次設定（跑 `./setup.sh`）。

### 手動兩終端機路徑（當 `start.sh` 不能跑時，例如 Windows 原生）

開兩個終端機，各跑一個服務。

終端機 1 —— 後端：

```bash
# macOS / Linux
cd backend && .venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8723 --log-level info
```

```powershell
# Windows 原生 PowerShell（注意 Scripts\，且用 --app-dir 指到 backend）
backend\.venv\Scripts\python -m uvicorn app:app --host 0.0.0.0 --port 8723 --app-dir backend
```

終端機 2 —— 前端（先確認 `node --version` >= 22）：

```bash
# macOS / Linux
cd frontend && npm run dev
```

```powershell
# Windows
npm --prefix frontend run dev
```

`npm run dev` 等同於 `next dev -p 8724`。開發時後端可加 `--reload`。

### 網址一覽

| 用途 | 網址 |
|---|---|
| 前端 UI | http://localhost:8724 |
| 後端 API | http://localhost:8723 |
| Swagger / API 文件 | http://localhost:8723/docs |
| 健康檢查 | http://localhost:8723/api/health |

健康檢查：

```bash
curl http://localhost:8723/api/health
```

### 停止

- **`./start.sh` 啟動的**：在該終端機按 `Ctrl-C`，會透過 trap 一併關閉前後端。
- **手動啟動的**：在各自終端機按 `Ctrl-C`。
- 重新跑 `./start.sh` 也會先釋放 port（kill 掉舊的實例）再重啟。

---

## 一鍵執行

> [!TIP]
> **repo 已附下列啟動器，可直接使用**（macOS 可雙擊、Windows 可雙擊、Linux 用終端機或桌面捷徑）。前提仍是：`claude` 已安裝且已登入、首次設定已跑過。

| 作業系統 | 一次性設定 | 啟動 |
|---|---|---|
| **Windows（全自動，雙擊）** | `Lodestar-Install.bat`（連 runtime 都自動裝） | 之後雙擊 `start.bat` |
| macOS（雙擊） | `setup.command` | `Lodestar.command` |
| macOS / Linux（終端機） | `./setup.sh` | `./start.sh` |
| Linux（桌面捷徑） | `./setup.sh` | `Lodestar.desktop`（範本見下，需填絕對路徑） |
| Windows（已自備環境） | `setup.ps1` | `start.bat`（雙擊）或 `start.ps1` |

各啟動器的行為：

- **`Lodestar-Install.bat` + `windows-bootstrap.ps1`（Windows 全自動，最接近「一鍵」）**：冪等偵測機器環境，用 `winget` / `npm` 補裝缺少的 Python 3.12 / Node / git / claude，確認 `claude` 登入後建環境並啟動。詳見上面「安裝 → Windows → 路線 A」。**唯一不能省的是第一次 `claude auth login`。**
- **`setup.sh` / `setup.command` / `setup.ps1`**：建立 `backend/.venv`、`pip install -r backend/requirements.txt`、`npm install`，完成後提示你 `claude auth login`。只需跑一次。（前提：runtime 已自備。）
- **`Lodestar.command`（macOS 雙擊）**：自動把最新的 nvm Node 22 加進 PATH，再呼叫 `./start.sh`。沒用 nvm 也能跑（那段會略過）。
- **`start.ps1` / `start.bat`（Windows）**：`start.sh` 的 PowerShell 對應版 —— 釋放 8723/8724、起後端 uvicorn + 前端 next、`Ctrl-C` 一併關閉。`start.bat` 只是用合適執行原則包一層呼叫 `start.ps1`，方便雙擊。

### Linux 桌面捷徑 `Lodestar.desktop`（範本）

放到 `~/.local/share/applications/Lodestar.desktop`，把路徑換成你的專案絕對路徑：

```ini
[Desktop Entry]
Type=Application
Name=Lodestar
Comment=AI 需求工程平台
Exec=bash -lc 'cd /path/to/ai-tool-v3 && ./start.sh'
Terminal=true
Categories=Development;
```

> `Terminal=true` 讓它在終端機視窗執行，方便看 log 與按 Ctrl-C 停止；`bash -lc` 用 login shell，方便載入 nvm 等環境。

> [!IMPORTANT]
> **關於「完全打包」（Docker / Electron / PyInstaller）的誠實說明：不切實際。** Lodestar 的核心 model adapter 依賴外部 AI CLI（`claude`，可選 `codex`/`agy`），而每個使用者都必須**自己安裝該 CLI 並互動式登入（OAuth keychain）**。這個登入需求無法被打包進映像檔或執行檔，所以無法做出「下載即用」的真正 turnkey 產品。程式碼裡有提到一條 `ANTHROPIC_API_KEY` + `--bare`（免 keychain）的路徑，但**目前並未接上**（adapter 不會真的傳 `--bare`），所以 headless 認證需要改程式。因此務實的「一鍵」做法就是上面這些啟動器腳本。

---

## 使用教學

以下是從零到交付的完整流程。

### 主導覽

前端是單頁多視圖 app，頂列有六個視圖：

| 視圖 | 用途 |
|---|---|
| WORKSPACE | 做專案（主要工作區） |
| FLIGHT LOG | 專案總覽 |
| WORKFLOWS | 新建 / 編輯 workflow |
| AGENTS | 編輯 agent（system prompt、persona、model、tools、多角色綁定） |
| SKILLS | 技能 |
| PLUGINS | 安裝 / 停用能力 |

### 1. 建立專案（thread）

在左側欄按「＋」（或 `⌘N` / `Ctrl+N`）開「開新專案」對話框，填專案名稱即可。每個 thread 是一條獨立的需求管線。

### 2. 選 model 與 workflow

- **選 model**：頂列 MODEL 下拉，清單來自 `GET /api/models`（標記可用/不可用）。選擇會存進 localStorage，之後每次生成/修訂自動帶上。預設 `claude-cli`。不可用的 model 仍可選，但送出時會回 `model.unavailable`。
- **選 workflow**：在 stage 軌的「workflow /」旁用 WorkflowSwitcher 切換該專案的 workflow。內建：
  - `default`（Standard Pipeline：prd → ui_design → architecture → stories）
  - `requirements_panel`（PRD 多 agent 討論）
  - `modify_existing`（改既有 repo）
  - `delivery_pipeline`（含 implement）
  - RCA 系列（見後面 RCA Copilot）

### 3. PRD 階段

- **上傳參考文件（可選）**：PRD 階段有拖放區/「＋」加檔案。接受 md / txt / csv / json / pdf / docx / png / jpg 等。後端會把每個附件的絕對路徑注入 prompt，並指示 `claude` 用 Read tool 直接讀原檔（圖片走原生視覺、PDF/DOCX 走 Read），不必本地 OCR。
- **生成**：按「✦ chart PRD」（忙碌時顯示 charting…，約 30–60 秒）。SA agent 會把模糊想法收斂成含 Overview / Delivery Surface / FR-X / NFR-X 的 PRD（結構驗證是 warn-only）。也可以用 chat 描述需求；PRD 完成時模型會在結尾附 `[PRD_READY]` 哨符。

### 4. 修訂（Refine）與核准（Approve）—— 閘門機制

- **修訂**：用一句指令讓 AI 輸出「完整的更新版」。
- **核准**：核准要求該階段 artifact 非空；核准後狀態變 approved（已核准 ✓）。

> [!IMPORTANT]
> **閘門順序**：下游階段在上游核准前是 locked。若上游被重新生成或手動修改，已核准的下游會被自動打回 `needs_revision`（顯示 REVISE 徽章），需要重做下游。

### 5. 架構 ∥ UI 設計（並行）

在 `default` workflow 中，`ui_design` 排在 `architecture` 之前；`architecture` 以 `soft_depends_on` 參考 UI 設計（純後端專案沒有 UI 設計也不會被擋）。UI Designer 產出設計理念、design tokens 與每個畫面自包含的 HTML 原型，前端用沙箱 iframe 直接預覽，可切換畫面與 document / preview / code 視圖；去掉 HTML 後的設計稿會餵給 stories。

### 6. 使用者故事（Stories）

Stories 必須以 `# <專案> — User Stories` 起手、首故事為 `Story 1.1`。

> [!WARNING]
> **防截斷**：若前段被截斷（例如從 Story 5.3 才開始）或缺少 Epic/Story 結構，UI 會顯示橘色警告要求重新生成，而且 batch 自動實作會擋下。必須拿到完整的 stories 才能發佈或實作。

### 7. 交付（發佈到 tracker / 文件 / 規格）

Stories 標題列有三個發佈按鈕：

- **發佈到 tracker…**：把 stories 用確定性 regex 拆成逐一 DeliveryItem（每個 `Story N.M` 一個 issue，含 body、estimate、Epic group、labels），發到 GitHub / GitLab。可先 preview。**重發只補缺漏**（與既有 issue 冪等比對），不會重複。
- **發佈文件…**：把 PRD / 架構 / UI 發到 Wiki。
- **同步規格到 repo…**：把規格 + `CLAUDE.md` commit 進 code repo，給實作 agent 讀。

哪些整合是真的：

| 整合 | 狀態 |
|---|---|
| GitHub | 真實：發 issue、開 PR、auto-merge（含 secondary rate-limit 退避重試） |
| GitLab | 真實：發 issue、開 MR、merge |
| Jira | 佔位 stub：publish 永遠回 `success=False` |

### 8. 設定憑證與專案交付目標

- **憑證入口**：頂列「⚙ INTEGRATIONS」開 IntegrationsModal → 選 github / jira / gitlab → 填欄位（GitHub 填 Personal Access Token；GitLab 填 Access Token，self-hosted 可填 Base URL）→ 儲存後加密存在 server-side keystore（Fernet），明文不留在瀏覽器。已存只顯示「✓ 已儲存」，留空沿用。**同一把 GitHub token 同時供「發佈 issue」與「自動實作開 PR」**，所以需要含 repo push + pull request 權限。
- **專案交付目標**：在 ProjectDeliveryModal 設定該專案的交付目標（github/gitlab、`repo_mode=new` 開新 repo 或既有、`repo_full_name`/owner/visibility，或 `local` 本機路徑）。真跑自動實作時，`claude-cli` 以此設定的 repo 為準（clone_url 由 keystore 憑證 + repo 組成）。

### 9. 自動實作（auto-implement）

Stories 核准後，implement 階段解鎖。控制列可選：

- **runner**：`mock`（安全 dry-run，不真改 repo）/ `claude-cli`（用 Claude 真跑、開 PR）/ `codex-cli`（用 OpenAI Codex `codex exec` 真跑）。清單來自 `GET /api/runners`。
- **執行方式**：「逐 issue 依序」（batch）/「整份一次」（session）。
- **模式**：單一 fix-loop / 多角色 pipeline（預設 roles）。
- **過 gate 自動 merge**（batch 才有，autoMerge 預設開，只在 github/gitlab 真跑時生效）。
- **target repo**。

> [!IMPORTANT]
> 真跑前必須：(1) 在 INTEGRATIONS 存好 GitHub/GitLab token（含 repo push + PR 權限）；(2) 在專案 delivery 設定指定 repo。否則 batch 會回 `token_missing` / `delivery_not_configured`。

**多角色 QA gate 回圈（roles 模式）**：`lead` 拆計畫 → (`RD` 實作 → `tester` 跑 lint/type-check/測試 → `reviewer` 審核) 回圈，**硬上限 3 次嘗試**。RD/tester 失敗或 reviewer 回 `CHANGES_REQUESTED` 就帶回饋重做；只有 reviewer `APPROVED` 且 tester 過才開 PR。agent 以 `bypassPermissions` 真跑 build/test，每個 story 切乾淨的 work branch、共用一份 working copy。

**逐 story batch + 自動 PR/MR + auto-merge**：batch 把整份 stories 依 Story 編號（1.1 → 7.3）依序、一次一個 issue 跑（過 gate 才換下一個），預設 continue-on-failure。每個 story 各開 branch/PR，PR body 帶 `Closes #N` 並在該 issue 留言。auto-merge：過 gate 即把 PR squash-merge 進 default branch（先輪詢 mergeability 避免亂序），下一個 story 從更新後的 main 切。冪等重跑會跳過已關 issue / 已有 open PR 的 story。

> [!NOTE]
> 同一專案共用一份實作 working copy，所以同時間只能有一個實作在跑（兩分頁同按只放行一個，另一個回 `409 impl_in_progress`）。

### 10. 嘗試 chips 與實作 log

實作面板顯示狀態 pill + 每次嘗試一個 ImplAttemptChip，可展開 log（stream-json 整理成事件 + 統計：files/tools/turns/elapsed/cost）。成功顯示 PR 連結 banner，失敗顯示 error banner，可隨時「取消」。batch 有逐 issue 進度表（每列一個 story，可點開看該 session log），並標示 auto-merge ON/OFF。

### 11. Flight Log（專案總覽）

每個專案一份唯讀總覽：階段時間軸、逐 story 現況/耗時/重試輪數/成本、token·$ 聚合。來自 `GET /api/projects/{tid}/summary`。

> [!NOTE]
> 目前成本聚合只含 implement 側；PRD / 架構 / stories 的 usage 尚未記錄。

### 12. Plugin / Workflow / Agent 管理

- **PLUGINS 頁**：可像手機 App 一樣裝 / 停用能力。所有 discovered plugin 預設啟用；停用後該 plugin 不註冊，其 stage/workflow/agent 從 catalog 消失（hot-reload 免重啟）。內建 plugin（`builtin_*`）不可停用。安裝方式：把 plugin 丟進 `backend/plugins/` 重啟，或 `pip install` 宣告 `lodestar.plugins` entry-point。
- **WORKFLOWS 頁**：新建/編輯 per-thread workflow（選 stages、排序、加 BUILD_VERIFY 等）。
- **AGENTS 頁**：編輯 agent（system_prompt/persona、model、skills、tools）並做多角色綁定（1:N + collaboration role：lead 主筆、peer 平行討論、subagent 被分派）。表單優先，不做視覺化拖拉 DAG。

### 修改既有 repo（modify_existing）

選 `modify_existing` workflow：單一 stage `change_request`（讀既有 repo → 談變更/解 bug → 產出實作 brief）。brief 可直接當 single implement 的 story，用 `claude-cli` 開一個 PR（「Implement → PR」按鈕），狀態就近顯示在下方內嵌實作面板。也可從既有 repo 匯入 issue 當討論起點（issue picker）。

### 替代用途：製造業 RCA Copilot

這是一個與需求工程並存的領域 plugin。流程：開新 thread → WORKFLOW 選下列之一：

- **單代理 RCA**（intake → analysis）
- **多代理鏈**（intake → baseline → causal → knowledge → synthesis，會畫因果圖）
- **Agentic 規劃**（AI 先提案分析計畫，核准後 apply 成真 workflow 執行）

在「RCA Analysis」（單代理）或「Baseline」（多代理）步驟上傳製程/良率資料，按 Generate 得到候選根因表（信心 / 證據 / 下一步檢查）。可載入合成範例：

```bash
cd backend && python -m scripts.seed_rca       # 建三個合成假資料 RCA 專案
cd backend && python -m scripts.reset_rca_demo  # 只刪範例還原
```

> [!NOTE]
> RCA 是 Copilot 不是 Judge：輸出是候選根因（含信心與證據），不是定論，仍需現場確認。資料要掛在會讀它的步驟上 —— 單代理掛在 RCA Analysis、多代理鏈掛在 Baseline，每個步驟只讀掛在它自己身上的附件。

---

## 設定 / 憑證

整合憑證（GitHub PAT、GitLab token）在 server 端以 Fernet 加密存放。金鑰來源依序：

1. 環境變數 `LODESTAR_KEYSTORE_KEY`（base64 Fernet key，**正式環境建議**）。
2. 否則用 `backend/data/.keystore.key`（首次使用自動產生，並 `chmod 0600`）。

產生一把正式用的 keystore 金鑰：

```bash
export LODESTAR_KEYSTORE_KEY="$(backend/.venv/bin/python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
```

| 項目 | 位置 / 變數 |
|---|---|
| 加密金鑰（環境變數，優先） | `LODESTAR_KEYSTORE_KEY` |
| 加密金鑰（檔案，後備） | `backend/data/.keystore.key`（0600） |
| 加密後的憑證 | 存在 DB（`backend/data/app.db`） |
| 憑證設定入口 | UI 頂列「⚙ INTEGRATIONS」 |

> [!CAUTION]
> **金鑰輪替（rotation）會使既有密文無法解密** —— keystore 會被當成空的，你需要重新輸入所有憑證。

---

## 疑難排解（FAQ）

> [!WARNING]
> **生成全部失敗 / 報 `claude CLI 不在 PATH` / 自動實作 exit code 127**
> 最常見原因：`claude` 沒裝、不在 PATH、或沒登入。先 `which claude && claude --version` 確認在 PATH，再 `claude auth status` 確認已登入（沒登入就 `claude auth login`）。提醒：設 `ANTHROPIC_API_KEY` 對 Lodestar 沒用，程式碼不會讀它。

**前端跑不起來 / `next dev` 報錯**
多半是 Node 版本太舊（系統 Node 16 不相容 Next 16 / React 19）。`node --version` 應 >= 22；用 nvm 的話先 `nvm use 22`。注意 `package.json` 沒有 `engines`，npm 安裝階段不會擋你，是 `next dev`/`build` 時才爆。

**`start.sh` 說缺 venv 或 node_modules**
你還沒做首次設定。`start.sh` 只檢查不建立。執行 `./setup.sh`，或手動：
```bash
python3.12 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install
```

**venv 用錯 Python（建出 3.9 之類）**
務必用 `python3.12` 明確建立（系統 `python3` 可能是 3.9）。Debian/Ubuntu 還要另裝 `python3.12-venv` 套件。

**Port 已被佔用（8723 / 8724）**
`start.sh` / `start.ps1` 會自動釋放這兩個 port（kill 佔用者，包括前一個 Lodestar 實例）。手動啟動時若遇到 port 衝突，先找出佔用程序：macOS/Linux 用 `lsof -nP -tiTCP:8723 -sTCP:LISTEN`；Windows 用 `Get-NetTCPConnection -LocalPort 8723 -State Listen`，再結束該程序。

**Windows 上 `start.sh` 跑不起來**
這是正常的 —— `start.sh` 是 Unix 專屬。請改用 WSL2（原樣執行 `start.sh`），或用 repo 附的 `start.bat` / `start.ps1`（見「啟動 / 停止」與「一鍵執行」的 Windows 章節）。

**圖片附件沒做 OCR**
`tesseract` 沒安裝。OCR 會被優雅跳過並顯示提示（`brew install tesseract tesseract-lang`）。注意 `claude-cli` 本來就能用原生視覺直接讀圖片，所以多數情況不需要本地 OCR。

**輪替 keystore 金鑰後憑證壞了**
換金鑰會使舊密文無法解密，keystore 被視為空。請在 INTEGRATIONS 重新輸入所有 token。

**自動實作回 `token_missing` / `delivery_not_configured`**
真跑前要先：在 INTEGRATIONS 存好對應的 GitHub/GitLab token（含 repo push + PR 權限），並在專案 delivery 設定指定 repo。

**第二個實作按不動 / 回 `409 impl_in_progress`**
同一專案共用一份 working copy，同時只能跑一個實作。等前一個跑完或取消它。

**Stories 一直被警告 / batch 被擋**
Stories 前段被截斷或不符 `## Epic N:` / `### Story N.M —` 格式。必須重新生成完整 stories 才能發佈/實作。

**自動實作某個 story 標 `failed`**
fix-loop 硬上限 3 次嘗試；3 次後仍失敗該 story 標 failed。batch 預設 continue-on-failure，會繼續下一個。

**限流（rate limit）後是否會自動續跑？**
會。系統針對訂閱用量上限（5 小時方案）有自動等待續跑機制，等待參數可由環境變數調整（見附錄 B 的 `LODESTAR_RATELIMIT_*`）。

**前端連不到後端 / CORS 錯誤**
前端 API base 寫死 `http://localhost:8723`（唯一覆寫是 runtime 全域 `window.__LODESTAR_API__`，沒有 `NEXT_PUBLIC_*` 之類的 env 鉤子）。把任一服務跑在非預設 host/port 需要改程式碼，不是改 env 就好。請維持預設 port。

---

## 附錄

### A. Ports / URLs

| 用途 | 位址 |
|---|---|
| 前端 UI | http://localhost:8724 |
| 後端 API | http://localhost:8723 |
| Swagger 文件 | http://localhost:8723/docs |
| 健康檢查 | http://localhost:8723/api/health |

後端綁 `0.0.0.0`（所有介面），port 刻意避開 3000/8000/8080/5173。

### B. 環境變數

| 變數 | 預設 | 用途 |
|---|---|---|
| `AITOOL_DB` | `backend/data/app.db` | 覆寫整個 DB 路徑；uploads/ 與 .keystore.key 會跟著它的父目錄走 |
| `LODESTAR_KEYSTORE_KEY` | （無，後備到 key 檔） | Fernet 加密金鑰（正式環境建議注入） |
| `LODESTAR_UPLOADS_DIR` | `<DB 目錄>/uploads` | 附件根目錄；開機時以 `setdefault` 設定（外部已設則沿用），供 `claude-cli` adapter `--add-dir` |
| `LODESTAR_IMPL_BASE_REPO` | （空） | 指向本地 base repo 供 implement 準備 worktree；空則從空目錄開始 |
| `LODESTAR_RATELIMIT_DEFAULT_WAIT` | `3600`（秒） | 限流後預設等待 |
| `LODESTAR_RATELIMIT_MAX_WAIT` | `21600`（秒，6h） | 限流等待上限 |
| `LODESTAR_RATELIMIT_BUFFER` | `60`（秒） | 等待緩衝 |
| `LODESTAR_RATELIMIT_MAX_CYCLES` | `6` | 最大續跑循環次數 |

> 程式碼不讀 `ANTHROPIC_API_KEY`（僅出現在註解中描述一條尚未接上的 `--bare` 路徑）。設定它對目前版本沒有作用。

### C. 資料 / DB 檔案位置

所有持久化狀態都在（gitignored 的）`backend/data/` 下：

| 內容 | 路徑 |
|---|---|
| SQLite DB（WAL 模式） | `backend/data/app.db`（+ `-wal` / `-shm` sidecar） |
| 加密金鑰檔（後備） | `backend/data/.keystore.key`（0600） |
| 附件上傳 | `backend/data/uploads/` |

> 整個 `backend/data/` 都不入 git。刪掉它等於重置 app；備份必須包含它。開機時的 migration 是冪等的（schema.sql 全為 `IF NOT EXISTS` + 增量 ALTER），重跑開機是安全的。若用 `AITOOL_DB` 覆寫，uploads 與金鑰檔會跟著新 DB 的父目錄走。

### D. 如何跑測試

```bash
# 後端測試（在 repo 根目錄執行，pyproject 已設 pythonpath=backend、testpaths=backend/tests）
backend/.venv/bin/python -m pytest          # 約 430 個測試（46 個測試檔）

# 前端型別檢查（無 unit test runner）
cd frontend && npx tsc --noEmit
cd frontend && npm run lint                  # eslint
```

### E. 專案附帶的啟動器檔案

| 檔案 | 平台 | 作用 |
|---|---|---|
| `start.sh` | macOS / Linux | 一鍵起前後端（既有） |
| `setup.sh` | macOS / Linux | 一次性環境設定（venv + pip + npm） |
| `setup.command` | macOS（雙擊） | 雙擊執行 `setup.sh` |
| `Lodestar.command` | macOS（雙擊） | 雙擊啟動（自動補 nvm Node 22 → `start.sh`） |
| `Lodestar-Install.bat` | Windows（雙擊） | **全自動**：偵測並補裝 Python/Node/git/claude → 確認登入 → 建環境 → 啟動（雙擊呼叫 `windows-bootstrap.ps1`） |
| `windows-bootstrap.ps1` | Windows | 上者的實際邏輯（冪等，可重複執行） |
| `setup.ps1` | Windows | 一次性環境設定（runtime 已自備時用） |
| `start.ps1` | Windows | 原生啟動（釋放 port + 起前後端） |
| `start.bat` | Windows（雙擊） | 雙擊呼叫 `start.ps1`（已設定好後的日常啟動） |

> Windows 的 `.ps1` 檔均存成 **UTF-8 with BOM**，確保在 Windows PowerShell 5.1（預設）下中文訊息不會變亂碼。

### F. 專案連結與文件

| 文件 | 路徑 |
|---|---|
| 專案 README（安裝/總覽） | `README.md` |
| Plugin 開發指南 | `docs/PLUGIN_GUIDE.md` |
| RCA 快速上手 | `docs/RCA_QUICKSTART.md` |
| 建構規格書（spec） | `ai-agent-plug-in-magical-brooks.md` |

> 注意：`frontend/README.md` 是 create-next-app 的原始樣板（提到 port 3000），**並非**本專案的正確啟動說明 —— 請以 `start.sh` / `package.json` 為準（port 8724）。`frontend/AGENTS.md` 提醒此 Next.js（v16）與舊版有破壞性差異，改前端前請先查 `node_modules/next/dist/docs/`。
