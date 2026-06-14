# 第三階段：生成截斷根因修復（claude-cli stream-json）✅

## 根因（用 RedmineCopy 真實 prompt 重現確認）
- 大型輸出（完整 stories >50k 字）→ 模型回應超過單輪 max output tokens → claude-cli 自動續寫成**多個 assistant 輪次**（實測 4 輪）。
- `--output-format text` 只回**最後一輪** → 前段（標題 + Epic 1-4 + Story 1.1-4.5）遺失、尾段完整。症狀：stories 變空 / implement 從 Story 5.3 開始。
- 對照實驗：受控 49k 英文（單輪）不截斷；真實 42k prompt（text + tools / text 無 tools）都截斷；**stream-json 串接 4 輪 → 完整（標題 + Epic 1-10 + Story 1.1）**。

## 修法
- [x] `claude_cli.py`：`_build_cmd` 改 `--output-format stream-json --verbose`；新增 `_parse_stream_json` 串接所有 assistant 輪次的 text block（跳過 tool_use/result）；`_invoke` 改回串接結果。
- [x] 單元測試 `test_agent_tools.py`：`_parse_stream_json` 多輪串接 + noise 容錯（不需真模型）。
- [x] 全套 376 passed（2 failed 仍為使用者 stories WIP）。
- [x] end-to-end live 確認：更新後 `_invoke` 跑真實 RedmineCopy prompt → 完整文件（開頭含標題 + Epic 1-9 + Story 1.1）✅
- 防線保留：detect_truncated_stories（後端 batch + 前端橫幅）作為 belt-and-suspenders。

---

# 第二階段：Stories 空白容錯 + 規格同步到 code repo ✅

計畫：`~/.claude/plans/atomic-dreaming-catmull.md`（第二階段）

## 問題 1：Stories 變空（RedmineCopy）
- [x] 診斷：artifact 仍在（4717 字）但 claude-cli 吐壞輸出（開頭截斷、用 `### Task` 非 `## Epic`/`### Story`）；其他同類專案正常 → 一次性生成異常。真 bug 是前端缺容錯。
- [x] 修：StoriesWorkspace `hasContent && epics.length===0` → 顯示琥珀警告 + 原始 markdown（不再空白）
- [x] 驗證：tsc 乾淨 + 隔離環境（複製真實 DB）截圖確認 FR-42/Cross-Cutting Tasks 都顯示

## 問題 2：規格同步到 code repo（給實作 agent 讀）
- [x] 後端 `spec_sync.py`：clone code repo → 寫 `.lodestar/{PRD,ARCHITECTURE,UI-DESIGN}.md` + 根 `CLAUDE.md`（managed block 不蓋既有）→ commit → push default branch；SpecSyncError 不洩 token
- [x] 端點 `POST /api/specs/{tid}/sync`（比照 docs_publish；缺 prd/arch → 400 specs_not_ready）
- [x] `build_impl_prompt` 加指引：先讀根 CLAUDE.md、UI 對齊 `.lodestar/UI-DESIGN.md`
- [x] `api_models.SpecSyncResponse`
- [x] 前端 `SpecSyncModal.tsx`（clone DocsPublishModal）+ Stories「同步規格到 repo…」按鈕 + 接線
- [x] 測試 `test_spec_sync.py` 13 案（managed block / fake git / 端點 / build_impl_prompt）全過
- [x] 驗證：隔離後端煙囪測（無 repo→400、無 prd→400）；前端截圖確認按鈕+modal+無 target 警告

## 狀態
- 本階段測試全綠；全套 `pytest backend` = **372 passed, 2 failed**。
- **2 個 failed 是使用者自己未 commit 的 stories WIP**（`_check_vertical_stories` validator vs 舊 test sample 缺 launch 指令），**與本階段無關**（phase 2 未碰 stories）。
- 尚未 commit（等使用者指示）。commit 時只加本階段檔案，不掃進使用者的 stories WIP。

---

# UI Designer Agent + ui_design stage ✅

計畫：`~/.claude/plans/atomic-dreaming-catmull.md`
目標：新增 ui_design stage（depends_on=prd，與 architecture 平行）+ UI Designer agent（frontend-design 設計原則 persona），產出每畫面自包含 HTML 的設計稿；stories 接 UI 上游（strip HTML）。

## Backend
- [x] ui_design_stage.py（StageSpec + 3 handlers + warn-only validator ×5 + 繁中 persona）
- [x] prompts：ui_design_system / ui_design_chat / ui_design_refine / ui_design_amendment_prefix（sentinel [UI_READY]）
- [x] _shared.py：strip_html_prototypes helper
- [x] stories_stage.py：depends_on=("architecture","ui_design")、_upstream 三元組、UI_DESIGN_BRIEF
- [x] stories prompts：三檔加 {{UI_DESIGN_BRIEF}} + UI alignment HARD RULE
- [x] register.py：register stage + validators + default/requirements_panel workflow 改四 stage
- [x] builtin_agents/register.py：seed_ui_designer（system_prompt 空 = stage default persona）+ 兩個 plugin.toml contributes
- [x] project_summary.py：_PRE_IMPL_STAGES 加 ui_design + operation-aware 歸戶（design 共用問題）
- [x] docs publish：app.py 撈 ui_design（選配不擋 400）、docs_publisher loop docs dict、DocsPublishModal 文案

## Frontend
- [x] lib/parse.ts：parseUiDesign（sections + screens，缺 html → null 容錯）
- [x] page.tsx：state/refresh/handlers/StageHeader/UiDesignWorkspace（document/preview/code 三 view + 畫面 tabs + sandboxed iframe）/Stories gating（archReady && uiReady）

## 測試
- [x] 既有測試連鎖：test_dual_vocab / test_plugin_distribution / test_workflow_engine / test_api
- [x] 新檔 test_ui_design_stage.py（10 案）+ test_docs_publisher 加 UI-Design 案例
- [x] pytest backend 全綠：**361 passed**（原 349 + 新 12，零回歸）；前端 `tsc --noEmit` 乾淨

## Review
**驗證**：
- pytest 361 passed；tsc 乾淨。
- 隔離後端（8725 + 暫存 DB）API 煙囪測：`/api/stages` ui_design item（depends_on=[prd]、downstream=[stories]、design/palette）✓；default workflow 四 stage ✓；新 thread statuses 四筆 draft ✓；無 PRD generate → 400 missing_prd ✓；有 PRD+架構缺 UI → stories 400 missing_ui_design ✓。
- 隔離前端（/tmp 副本 + next dev --webpack + rewrites 代理，已清理）瀏覽器實測：stepper 03 UI Design（狀態徽章正確）、iframe sandbox="allow-scripts" 正常渲染原型（含 Google Fonts/CSS 動效）、畫面 tabs 切換、document/preview/code 三 view、Stories 顯示 depends_on architecture + ui_design 且上游齊才 enabled。console 零錯誤。
- **注意**：使用者執行中的後端（8723）是舊 code，需重啟 start.sh 才會載入新 stage（嘗試代為重啟被權限擋下，留給使用者）。

**已知行為**：舊 default thread 重跑 stories 會 400「'ui_design' 必須先完成」（同缺 PRD 跑架構的語意；補生成 UI 設計即可）。

---

# RCA PoC todo（已完成，存檔）

計畫全文：`~/.claude/plans/ai-rca-memoized-bengio.md`
做法：與既有需求工程領域**並存**的 `rca_domain` plugin；核心引擎零修改承載模式 1/2/3。

## 指令更新（使用者）
完整實作 1→4 全部（含 RCA-4）+ 完整測試 + 前端真實整合（不走 mock-gate），最後用結果討論。

## RCA-1 — 單代理薄垂直切片（基本完成；live generate 驗證中）
- [x] `backend/plugins/rca_domain/` 骨架：`__init__.py`、`plugin.toml`、`register.py`、`_shared.py`
- [x] `intake_stage.py`（rca_intake：generate + chat）
- [x] `analysis_stage.py`（rca_analysis：generate + refine + chat）+ validators（candidate causes / copilot disclaimer）
- [x] prompts：`intake.md`、`intake_chat.md`、`rca_single.md`、`rca_refine.md`、`rca_chat.md`
- [x] agents：`rca_intake_helper`(role rca_intake)、`rca_assistant`(role rca_analysis)
- [x] workflow：`rca_single`（rca_intake → rca_analysis）
- [x] fixtures：`fixtures/yield_drop/{scenario.md, yield_by_lot.csv}`
- [x] `backend/scripts/seed_rca.py`（seed yield_drop，idempotent）
- [x] test：`backend/tests/test_rca_stage.py` 7 passed；全套 162 passed（零回歸）
- [x] 驗證：`/api/stages` 含 rca_*、`/api/workflows` 含 rca_single、seed 後 thread 綁定就緒、依賴擋關
- [x] live generate（真 claude-cli 讀 CSV）— 正確抓出 ETCH-03 / L2231 步階訊號，產 4 候選根因含證據+下一步，validators 無觸發
- [ ] 前端（RCA-1 標記為可選）— 延後，見下方前端區塊

## RCA-2 — 多代理鏈 ✅（後端完成）
- [x] `chain_stages.py`：4 specialist stage（工廠函式）+ agents + 9 prompts；workflow `rca_chain`；causal-graph validator
- [x] fixtures `param_drift`、`signal_anomaly`（背景 agent 生成）；seed 擴三情境
- [x] test_rca_chain.py（catalog/依賴/缺上游/mock 端到端/causal validator/下游 reset）

## RCA-3 — Agentic planner ✅（後端完成）
- [x] `planner_stage.py`（rca_plan + parse_plan + plan-shape validator）+ planner prompts + agent `rca_planner` + workflow `rca_planner`
- [x] `POST /api/projects/{tid}/rca/apply-plan`（重用 `_save_workflow` + `set_project_workflow`；未含 intake 自動前置）
- [x] test_rca_planner.py（catalog/parse/validator/mock generate/apply-plan 流程/409/壞 stage 400）

## RCA-4 — 真 collab 執行 ✅（後端完成，唯一動核心）
- [x] `backend/collab_coordinator.py`：resolve_agent（GAP A）+ per-agent prompt（GAP B）+ discussion（sync）+ dispatch（ThreadPoolExecutor 平行）
- [x] `workflow_engine.dispatch()` 加 collab_mode 分支（僅 mode≠single 且 binding>1 觸發；single 不受影響）
- [x] 示範 workflow `rca_panel`（discussion）/ `rca_dispatch`（dispatch）
- [x] test_collab.py（resolve+DB fallback/discussion/dispatch/single 不受影響）

## 後端整體 ✅ 178 passed；seed 3 情境；catalog 7 stage / 5 workflow / 7 agent，load_error None

## 前端整合 ✅（真實整合，非 mock）
- [x] Next 16.2.6 / React 19.2.4；遵 frontend/AGENTS.md（沿用既有 page.tsx 模式、未引入新 Next API）
- [x] `components/RcaWorkspace.tsx`：自足 catalog-driven RCA 工作區（workflow 切換器 + stepper + 通用 generate/refine/approve）
- [x] `RcaArtifactView`：候選根因表 + 信心色階 chip + 護欄帶 + 每列 Confirm/Reject/Needs-more-data（local 諮詢）；自帶輕量 markdown renderer；causal 用既有 Mermaid；rca_plan 顯示提案 stages + 「核准並套用」
- [x] page.tsx 最小接線（3 處）：import、`isRcaThread` 分支渲染 RcaWorkspace、WorkflowsView availableStages 改 catalog-driven
- [x] lib/api.ts：通用 stage helpers + `applyRcaPlan`
- [x] 驗證：`tsc --noEmit` 乾淨；既有 dev server（8724）回 200 無錯誤 overlay（已 hot-reload 我的改動）
- 註：為不干擾使用者正在跑的 start.sh，未另起會搶 .next 的第二 dev server；可在 8724 選 RCA thread 直接看

## 最後：live demo（真模型）✅ 全數成功
- [x] RCA-2 鏈（param_drift）：baseline 精準抓 ETCH-03 chamber_pressure 線性漂移破 spec → causal（Mermaid 7 假設）→ knowledge（已知失效模式對照）→ synthesis（合併排序候選表 + 須先排除 manometer 假象）
- [x] RCA-3 planner：產出合法 plan JSON（完整鏈 + rationale + 每 stage why）
- [x] RCA-4 panel discussion：2 peer 發言寫入 stage_messages → lead 合成候選表（含否證條件）
- [x] docs/RCA.md；結果已寫入 rca-param-drift / demo-planner / demo-panel，8724 可看

## 全部完成 ✅ — 後端 178 測試、前端編譯驗證、三模式真模型 live 驗證

## UX 改進 2（使用者回饋：plugin 看不懂 / 太多很像）✅
- [x] 刪除沒用的範例 plugin `example_notes`（刪目錄 + 清 DB contributions）；`test_plugin_distribution.py` 改用 rca_domain 證明同樣機制（178 測試維持綠）
- [x] Plugins 頁重組：分「你的功能」（提供 stage/流程：RCA / 需求工程 / 自動實作）與可收合的「系統零件」（agent / 模型 / 交付，預設收起、標明平常不用管）；標題改「Plugins · 擴充功能」+ 手機 App 比喻
- 註：後端需重啟才會讓 example_notes 從清單消失（Python 無 hot-reload）

## UX 改進 1（使用者回饋）✅
- [x] Stage 用途說明：StageSpec 加 `description` 欄位 + `_build_catalog` 帶出；填了 11 個 stage（7 RCA + prd/arch/stories/implement）；Workflows 編輯器每個 stage 列顯示 label + 用途，加 stage 選單 hover 顯示用途
- [x] Plugins 頁說明改白話（plugin = 功能套件，提供 stages/agents/workflows，停用即移除）
- [x] 驗證：後端 178 測試綠、前端 tsc 乾淨、catalog 確認帶出描述
- 註：後端需重啟（Python 無 hot-reload）才會在 API 看到新 description；前端已 hot-reload

## 收尾（使用者要求）✅
- [x] 清除測試資料：`scripts/reset_rca_demo.py` 刪 5 個 RCA 示範 thread（含 artifacts/對話/附件/上傳檔）；使用者既有 10 個舊 thread 與 workflow 完全未動
- [x] 使用者快速上手教學：`docs/RCA_QUICKSTART.md`（產線工程師導向：啟動→3 分鐘首次分析→三模式選擇→載入範例→重點提醒）
- [x] README 加 RCA Copilot 指引連結

## 前端（RCA-1 — 進行中）
- [x] 靜態 mock：`frontend/mocks/rca-analysis.html`（frontend-design skill；貼合 Industrial Cobalt 主題、用真實 live 資料、ETCH-03 良率折線、護欄帶、信心色階、Confirm/Reject 諮詢鈕）→ 已 preview 驗證渲染正確
- [ ] 等使用者確認方向後：整合進 Next app（`page.tsx` 依 active workflow 偵測 RCA thread 走通用 stage 面板 + `RcaArtifactView`）+ 接 `/api/stage/rca_analysis/*`（先讀 `node_modules/next/dist/docs/` 遵守 frontend/AGENTS.md）
- [ ] 每列 Confirm/Reject/Needs-more-data 寫 `stage_comments`

## Review — RCA-1（完成）
**做了什麼**：新增 `backend/plugins/rca_domain/`（與需求工程並存、非 builtin、可開關），含 rca_intake / rca_analysis 兩 stage、兩 agent、rca_single workflow、5 個 prompt（copilot-not-judge 框架）、2 個 warn-only validator（候選根因 / 免責聲明）、yield_drop 合成 fixture、`scripts/seed_rca.py`、`tests/test_rca_stage.py`。**核心引擎零修改**（plugin 自動探索）。

**驗證**：
- pytest：新測 7 passed；全套 162 passed（零回歸）
- 真實 app：rca_domain 乾淨載入、`/api/stages` 含 rca_*、`/api/workflows` 含 rca_single、seed thread 綁定就緒、依賴擋關（缺 intake → 4xx）
- live generate（真 claude-cli）：讀 CSV、正確抓 ETCH-03 / 05-21(L2231) 步階訊號、產 4 候選根因（信心+證據+下一步），validators 無觸發

**後續**：前端（RcaArtifactView + 通用 stage 面板）RCA-1 標記可選、延後；接著可走 RCA-2（多代理鏈）或先補前端讓使用者看到 UI。
