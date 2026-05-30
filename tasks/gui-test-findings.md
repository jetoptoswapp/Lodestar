# Lodestar GUI 全功能測試 — 發現記錄

> 測試日期：2026-05-30
> 方式：preview（Playwright 驅動真實 UI）+ 真實 backend（8723）/ frontend（8724）
> 既有資料：1 個 thread「新需求」(0ec142d38fef)，PRD 已有對話/狀態

## 測試安全邊界（避免造成副作用）
- ❌ **不實際發佈到 GitHub**（會建立真實 issue）— Publish 流程只測到 Preview，不按「確認發佈」
- ❌ **不刪除既有「新需求」thread** — 刪除測試另建拋棄式 thread
- ✅ Implement 用 **mock runner**（安全 dry-run）
- ⚠️ AI 生成（PRD/Arch/Stories generate）會真的呼叫 claude-cli（慢、有成本）— 驗證觸發 + loading 狀態，必要時讓 1 條跑完驗 happy path

## 嚴重度分級
- 🔴 **Bug**：功能壞掉 / 報錯 / 結果錯誤
- 🟠 **UX 問題**：能用但體驗不對（誤導、缺回饋、流程卡頓）
- 🟡 **Polish**：小瑕疵（文案、對齊、一致性）
- 🔵 **觀察 / 建議**

---

## 測試方法論註記（非產品 bug）
- preview_click（Playwright trusted click）在此 Next16/React19 app **回報成功但觸發不到 React onClick**（全域，連側欄收合鈕、nav 都一樣）。
- 已驗證 app 本身正常：onClick handler 標準、`elementFromPoint` 證實按鈕在最上層無浮層遮擋、原生 `.click()` 能正確切換。
- 因此改用 `preview_eval` 原生 click 觸發 handler + `elementFromPoint` 檢查可點性（抓真實浮層 bug）+ screenshot/network 驗證結果。

## 發現清單

### A. TopBar 導航 + Model selector
- ✅ 四個 nav（WORKSPACE / WORKFLOWS / AGENTS / PLUGINS）皆正確切換主內容；active 樣式（亮白 + 底線）正確。
- ✅ Model selector popover 正常：opaque（bg #1f2733、opacity 1、z 40），三取樣點最上層皆在 popover 內，**無透出主內容**（lessons.md 的 stacking 修復有效）。
- ✅ 三個 adapter（agy-cli / claude-cli「MULTIMODAL」/ codex-cli）顯示說明 + CTX/PROMPT/REPLY 參數；選 codex-cli → TopBar 更新、popover 關閉；切回 claude-cli OK。
- 🟡 **A-1 Polish**：AGENTS 視圖分組標題單複數未處理，恆為複數（「ROLE / ARCHITECTURE · 1 AGENTS」應為 1 AGENT）。待細查是否多處。

### ⭐ C. PRD / Architecture / Stories stage
- ✅ Stage stepper 切換正常（01/02/03/04）。
- ✅ **依賴鎖定完整**：空 PRD → Architecture/Stories 全按鈕 disabled + 顯示「尚未具備上游需求 / 尚未就緒」；Implement(04) tab 在 stories 未核准前鎖定（disabled, opacity 0.55）。
- ✅ PRD 空狀態按鈕邏輯：Refine disabled、生成 PRD enabled、核准 disabled。
- ✅ **生成 PRD**：觸發後鈕變「Generating…」+ disabled；完成後 artifact 渲染（markdown 粗體標題）、底部狀態列（CHARS / CHARTED BY / DEPENDS_ON）、鈕變「重新生成」、Refine/核准 轉 enabled。
- ✅ **核准**：stepper badge CHARTING→CHARTED、鈕變「已核准 ✓」+ disabled；**下游解鎖串聯**正確（PRD 核准後 Architecture 生成鈕 enabled）。
- ✅ **PRD Chat 接真實 claude-cli**：送訊息 → 使用者訊息即時顯示(2→3)、thinking 狀態、輸入清空、送出鈕 disabled 防重送；回覆正確返回(→4)、**自動捲到底**。lessons.md 的 chat mock 問題已修復 ✓
- ✅ **附件**：上傳（透過真實 file input onChange + 後端 200，.md 正確解析 has_parsed_text）、UI 顯示計數與檔名/大小、× 刪除（2→1→0）皆正常。
- ✅ **全螢幕閱讀**：點按鈕開啟 fixed 全幅 overlay 顯示 artifact、ESC 關閉、body overflow 正確還原。
- 🔵 **C-2 觀察（測試工具）**：preview_screenshot 在 `window.location.reload()` 後會 render 破圖（內容擠左上角），但 DOM layout 實際正常（header/aside/main 尺寸正確）；`preview_resize` 可重置。非 app 問題。
- 🔴 **C-1 BUG（功能壞 + 靜默失敗）：PRD 的「Refine…」完全壞掉。**
  - **現象**：開 PRD refine modal，標題顯示「修訂 **undefined**」、副標「POST /api/stage/**[object Object]**/refine」；輸入指令按「送出修訂」→ modal 異常、**不發任何 refine 請求**、無錯誤提示（使用者以為送出了，其實什麼都沒發生）。
  - **運行時證據**：network 無 refine 請求，但出現 `POST /__nextjs_original-stack-frames`（Next dev 抓 JS 例外 stack）= submit 丟 TypeError。
  - **根因**：[app/page.tsx:904](frontend/app/page.tsx:904) PRD 傳的是原始 `onRefine={onRefine}`（吃 stageId 參數），而 Arch/Stories 傳包好的 wrapper（[:922](frontend/app/page.tsx:922) `onRefineArch` / [:936](frontend/app/page.tsx:936) `onRefineStories`）。按鈕 [:1661](frontend/app/page.tsx:1661) `onClick={onRefine}` 把 click event 當 `stageId` → `STAGE_LABEL[event]`=undefined、`/api/stage/${event}`=`[object Object]`、`setBusyFor[event]("refine")` 丟 TypeError。
  - **修法（一行）**：page.tsx:904 改 `onRefine={onRefinePrd}`（wrapper 已存在於 [:821](frontend/app/page.tsx:821)）。
  - **影響**：PRD 是 pipeline 入口、最常用 stage，其 Refine 是核心修訂功能，目前 100% 不可用且無回饋。Arch/Stories 的 Refine 不受影響（程式碼用正確 wrapper）。

### D. Publish 流程 + Implement workspace（M2.5 / M5）
- ✅ **Publish config step**：3 個 target（github / jira / gitlab）；github config 欄位依 schema 渲染（Repository owner/repo* + Personal Access Token*，必填星號）；localStorage token 警示文案有顯示。
- ✅ **Publish preview**：「下一步：預覽」→ POST /preview-delivery；當 stories 無可解析項目時**優雅報錯**（非 crash）：「stories 解析不出任何 DeliveryItem；檢查 heading shape：## Epic N: / ### Story N.M」——錯誤訊息含預期格式提示，modal 停在 config step。
- ⚠️ 安全考量未測「確認發佈」（會建真實 GitHub issue）；issue 卡片渲染因測試 stories 為「拒絕」內容無真實故事而未能驗證。
- ✅ **Implement runner picker**：data-driven（GET /api/runners → claude-cli / mock）。
- ✅ **Implement M5 流程（mock runner）端到端**：開始自動實作 → POST /api/implement/start（session 2）→ status poll（GET /api/implement/2）→ **log poll cursor 遞增**（after_id 0→8）→ orchestrator「succeeded on attempt 1」→ UI 顯示 attempt chip + **PR banner 連結** `https://github.com/owner/repo/pull/MOCK-2`（mock，無外部副作用）；按鈕轉「重新實作」。

### E. 工作流完整性 / 一致性觀察
- 🟠 **E-1（UX 困惑）**：在 PRD chat 送出明確需求（且 SA 有回覆）後，按「重新生成」產出的 PRD **仍是「我還沒收到任何需求」的拒絕內容**。後端 generate handler [prd_stage.py:107](backend/plugins/builtin_core_stages/prd_stage.py:107) **確實有把 conversation 帶入 prompt**，故較可能是模型行為不一致而非 wiring bug；但對使用者是明確困惑點（給了需求卻說沒需求）。建議：generate 前若 conversation 已有需求應更穩定地採用，或在 UI 提示「generate 會根據左側對話」。待多次重現確認。
- 🟠 **E-2（工作流完整性）**：「重新生成 PRD」會把 PRD status reset 為 draft（CHARTING），但**下游 architecture 仍保持 CHARTED（已核准）**。架構其實已基於舊 PRD 過時卻仍顯示核准，使用者可能誤以為架構仍對齊。對照：refine 有「上游→下游 reset」（page.tsx:784），但 generate 似乎沒有同等的下游失效。建議確認 generate 是否也該標記下游 stale。
- 🟡 **E-3（i18n 一致性）**：同一發佈動作在兩處標籤不同——header 區「Publish to tracker」vs 動作列「發佈到 tracker…」。建議統一。

### F. 管理類視圖（Workflows / Agents / Plugins）
- ✅ **Agents CRUD**：new agent modal 表單完整（agent_id*/name*/role(stage_id)/system_prompt/model select/max_iter/tools chip editor/enabled）；tools chip editor（打字+Enter 加入）正常；建立 → USER 卡片含 編輯+刪除；編輯 → agent_id **鎖定**(immutable)、其餘預填、submit「儲存變更」；刪除 → 確認 modal（正確 agent 名）→ 移除。
- ✅ **Workflows CRUD**：new workflow modal（id*「建立後不能改」/label/description）→「建立並編輯」進編輯模式；stage palette 可加（PRD/架構/使用者故事/自動實作/RCA stages）；stage editor 控制項齊全（上下移、移除、collab 模式 single/discussion/dispatch、depends_on）；儲存 → POST /api/workflows **201 Created**；刪除 → 確認 modal → 移除，builtin 不受影響。
- ✅ **Plugins toggle**：rca_domain(3rd-party) 停用 → count 6→5 + 後端「disabled → skip register」hot-reload；啟用 → count 5→6 + 「loaded plugin」re-register；topbar count **即時同步**；builtin plugin toggle 為 disabled（不可停用）✓。
- 🟡 **F-1（UX）**：停用一個「feature」plugin（如 rca_domain）後，它從顯眼的「你的功能」區**移到收合的「系統零件」區**（因停用後不顯示 provides → 被歸類為系統零件）。使用者要重新啟用得先展開系統零件，較難找。建議停用後仍留在原區（標示 disabled）以便切回。

### G. RCA workspace（rca_domain plugin）
- ✅ Thread 綁定 rca_* workflow 後正確渲染 RcaWorkspace（「Manufacturing RCA · Copilot」）。
- ✅ RCA workflow 快切 `<select>`（單代理 RCA / 多代理鏈 / Agentic 規劃 / Panel（討論）/ Dispatch（派工））；切 rca_single（2 stage）→ rca_chain 後 stepper 正確變 5 stage（Anomaly Intake → Baseline & Profiling → Causal-Graph Reasoning → Knowledge/SOP 對照 → Synthesis 候選根因）= data-driven 渲染正確。
- ✅ **RCA Generate**：intake 產出**結構化內容**（Anomaly Summary: Symptom/When/Where、Known Facts、Data Provided、Open Questions）；無資料時正確標「尚未提供」+ 列 Open Questions，**不幻想** = copilot 行為良好；按鈕轉「重新生成 + Approve」；copilot disclaimer 有顯示。
- ✅ RCA 用批次狀態端點 `GET /api/stage/statuses/{tid}`。

### H. 響應式 / 視覺 / 全域錯誤掃描
- ✅ **後端整輪零錯誤**：287× 200、2× 201、1× 400（preview-delivery 對無可解析 stories 的**預期優雅錯誤**），**無任何 5xx / exception / traceback**。
- ✅ **瀏覽器 console 全程無 error**（唯一例外是 C-1 PRD refine 的 TypeError，走 Next dev overlay）。
- ✅ Desktop（1280–1512）三欄式佈局正常；tablet（768）main 480px 可用；各尺寸**皆無水平破版/溢出**。
- 🔵 **H-1 觀察（響應式）**：mobile（375）sidebar 固定 288px 不收合 → main 僅剩 87px 不可用。app 為桌面導向三欄工具（dark-only，無 light mode 切換），mobile 響應式應屬範圍外；若要支援需在窄螢幕自動收合 sidebar / 改 overlay。
- ✅ Model popover / 各 modal 的 stacking 全部正確（opaque、在最上層、無透出）。

### B. Sidebar（thread CRUD + 收合）
- ✅ 收合/展開（寬 288 ↔ 56），收合後顯示 thread 縮圖 + ＋ 鈕。
- ✅ 新增 thread：modal 欄位完整（標題「開新專案」、subtitle「POST /api/projects」、輸入預填 + 自動 focus + 全選、helper「↵ 提交 · esc 取消」）；ESC 關閉；確定 → POST 200 → 新 thread 建立並**自動切換**。
- ✅ Rename：modal 預填當前名稱、確定 → 側欄即時更新。
- ✅ Thread 切換：切到有資料的「新需求」載入真實 2 則對話 + SA 內容；切到空 thread → 0 MSGS、**無殘留**前一 thread 內容（lessons.md 的 mock 殘留問題已修復 ✓）。
- ✅ Delete：確認 modal 列出「將刪除 PRD/架構/故事 artifact、對話、附件、遙測」+ 正確 thread 名 + 紅色「刪除」鈕；刪除後自動切換到剩餘 thread。
- ✅ ⌘N 快捷鍵開啟新 thread modal。
- 🔵 **B-1 觀察（效率）**：初始/切換載入時同一 endpoint 被重複 fetch（prd/architecture history 各 2–4 次）。部分屬 Next.js dev StrictMode double-invoke（production 減半），但 architecture history 4× 偏多，值得查是否有 effect 相依重複觸發。
- 🔵 **B-2 觀察（設計取向）**：modal subtitle 直接顯示 raw API 路徑（`POST /api/projects`）。屬本工具「開發者導向」風格的刻意設計，但若面向非技術使用者會顯突兀。

---

## 測試覆蓋追蹤
- [x] TopBar 導航 + Model selector
- [x] Sidebar（thread 切換 / 收合 / new / rename / delete / ⌘N）
- [x] PRD stage（generate / refine⚠ / approve / attachments / chat / fullscreen）
- [x] Architecture stage（generate / approve / Mermaid 元件確認）
- [x] Stories stage（generate / approve / refine）
- [x] Publish 流程（config → preview，含優雅錯誤；未測實際發佈）
- [x] Implement workspace（mock runner 端到端 + PR banner）
- [x] Workflows view（CRUD + stage editor）
- [x] Agents view（CRUD + editor modal + tools chip）
- [x] Plugins view（toggle + hot-reload）
- [x] RCA workspace（render / workflow 快切 / generate）
- [x] 響應式（desktop/tablet/mobile）/ console & 後端錯誤掃描

## ✅ 修復記錄（2026-05-30，全部已驗證）
> backend `pytest 182 passed`、frontend `tsc` 綠、瀏覽器逐項回歸通過。

| # | 項目 | 修法 | 驗證 |
|---|---|---|---|
| C-1 🔴 | PRD Refine 壞掉 | [page.tsx:904](frontend/app/page.tsx:904) `onRefine={onRefine}`→`onRefinePrd` | modal 顯示「修訂 PRD」、submit → `POST /api/stage/prd/refine 200`、無 TypeError |
| E-2 🟠 | 重生 PRD 不刷新下游 | `onGenerate` 加 `refreshArchitecture/refreshStories`（對齊 arch/refine） | tsc + 程式對稱（後端本就 cascade） |
| A-1 🟡 | 「1 AGENTS」單複數 | `{n===1?'agent':'agents'}`（+ chat 的 msg/msgs） | 顯示「2 agents / 1 agent」 |
| E-3 🟡 | Publish 標籤不一致 | header chip「Publish to tracker」→「發佈到 tracker…」 | 兩處皆「發佈到 tracker…」 |
| F-1 🟡 | 停用 plugin 移到收合區 | 後端 `get_plugins` 對 disabled 回報 manifest 宣告的 provides（[app.py](backend/app.py) `_declared_provides`） | 停用 rca_domain 後系統零件數不變、isFeature=true 留在「你的功能」 |
| 🔐 | **Token 安全（server-side keystore）** | 新增 `keystore.py`（Fernet）+ `integration_secrets` 表 + `PUT/GET/DELETE /api/integrations/{t}/credentials`；PublishModal 改走 keystore、移除 localStorage、清除 legacy key | PUT 200、GET 不回明文、DB 密文不含明文、重開顯示「✓ 已儲存」、localStorage 無 token |
| 📱 | **響應式最小防護** | 窄螢幕（<1024）mount/resize 自動收合 sidebar（SSR-safe，無 hydration mismatch） | mobile 375 main 87px→319px、console 零警告 |
| 🔎 | C-1 同類 bug 稽核 | 全 `onClick={裸fn}` 檢查 | 僅 C-1 一處，其餘皆 arrow wrapper |

> **未改（刻意）**：E-1（模型行為非 wiring bug）、B-1（dev StrictMode 重複 fetch，prod 減半）、B-2（raw API 路徑屬刻意 blueprint 風格）。
> **部署注意**：production 應設環境變數 `LODESTAR_KEYSTORE_KEY`（base64 Fernet key）由外部秘密管理注入；未設時後端自動生成 `backend/data/.keystore.key`（0600，已 gitignore）。

## 總覽（issue 統計）
- 🔴 **Bug ×1**：[C-1] PRD「Refine…」完全壞掉（顯示 undefined/[object Object]、submit 丟 TypeError、不發請求、靜默失敗）。**一行可修**（page.tsx:904 `onRefine={onRefine}`→`onRefinePrd}`）。
- 🟠 **UX ×2**：[E-1] PRD chat 給需求後重生仍「無需求」拒絕；[E-2] 重生 PRD 不使下游 architecture 失效（仍顯示 approved）。
- 🟡 **Polish ×3**：[A-1] 「1 AGENTS」單複數；[E-3] Publish 兩處標籤不一致；[F-1] 停用 plugin 後移到收合區難找回。
- 🔵 **觀察 ×4**：[B-1] dev 重複 fetch；[B-2] modal 顯示 raw API 路徑（刻意風格）；[C-2/screenshot 工具] ；[H-1] 無 mobile 響應式。
- ✅ **整體品質高**：核心 pipeline（生成/核准/依賴鎖定/chat 真實接 API/附件/全螢幕）、thread CRUD、Workflow/Agent/Plugin 管理、Publish 流程、M5 mock 實作、RCA、stacking、錯誤處理 全部正常；後端零 5xx、console 零 error。
