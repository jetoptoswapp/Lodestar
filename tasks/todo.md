# 治本：跑批前對抗式稽核挖出的 5 個缺陷（已修）

「全程用 UI 跑到專案完成、lodestar 不出問題」→ 先用 workflow 對抗式稽核 modify_existing→batch→MR→merge，
跑批前挖出剩餘雷（11 confirmed，歸併為 5）：

- [x] **D5（BLOCKER）截斷守門誤殺 renumber**：`detect_truncated_stories` 寫死「從 Epic 1 起」，D4 把 brief 改成 Epic 13 → 誤判截斷 → `start_batch` raise → HTTP 400 → 0 MR。
      修：`delivery_parser.detect_truncated_stories(allow_renumbered=)`——continuation 模式不要求起點=1，但仍抓「最低 story 非 .1（砍頭）」「epic 不連續」；`start_batch(allow_renumbered=)` + `implement_start_batch` 依「stories 為空→來自 change_request brief」傳旗標。greenfield 路徑不變（test_implement_batch 既有斷言仍綠）。test：`test_renumbered_brief_not_truncated_with_allow_renumbered` / `test_renumbered_head_loss_still_caught`。
- [x] **D6（HIGH）相依 story + auto_merge off → 7 個壞 MR**：13.x 全疊在 13.1 scaffold 上，但 auto_merge off 時每個 story 從乾淨 origin/main 切、看不到 scaffold。
      修：modify_existing 按鈕加 auto-merge toggle（預設開）→ 策略 A 過 gate 即依序 merge、下個 story 從更新後 main 切。`onImplementBatch(autoMerge)` + `startBatch({auto_merge})`。
- [x] **D7（HIGH）全程無「合進 main」入口 → 專案無法完成**：同 D6 的 auto-merge toggle 解（過 gate 即 merge 進 main = 專案完成路徑）。
- [x] **D8（MED）batch 輪詢遇單次 fetch 失敗永久凍結**：`ChangeImplementStatus` 兩個 tick 的 catch 不重排 → 加有界退避續輪詢（errs<30）。
- [x] **D9（MED）issue 連結寫死 gitlab.com**：self-hosted gitlab.saikah.com 連結 404。修：`ProjectResponse.base_url`（get_project 由 keystore 帶出）→ 前端 `issueUrlFor(…, baseUrl)` / `ImplBatchProgress baseUrl`。

## 驗證
- [x] 後端 pytest 439 全綠；前端 `tsc --noEmit` 乾淨。
- [ ] 重啟後端載入 → 全程 UI 跑 13.1–13.7（auto-merge on）到 7 MR 合進 main = 專案完成。

---

# 治本：modify_existing live 跑出來的三個缺陷（已修）

起因：對 JetBook 跑 batch（7-story fan-out）時，GitLab 當下不通，暴露三問題。

- [x] **D1（最致命）阻塞 git push 凍死後端**：`orchestrator.run_implementation` / `run_implementation_roles`
      的 `open_pr(...)` 是同步呼叫，遠端不通時 push 阻塞 → 凍住整個 event loop（cancel/所有 API 失應答）。
      修：兩處改 `await asyncio.to_thread(open_pr, ...)`；`gitlab_mr.py`/`github_pr.py` 的 push 加 `timeout=120`。
      測：`test_implement_orchestration.py::test_open_pr_offloaded_off_event_loop`（open_pr 不在 loop thread 跑）。
- [x] **D2 batch 沒先發 issue**：modify_existing brief 無「stories→issues publish」步驟 → PR 不帶 `Closes #issue`。
      修：`app._ensure_batch_issues_published`（parse → 跳過既有 → 冪等 publish），在 `implement_start_batch`
      的 `list_issues` 前呼叫；best-effort（列/發失敗只 warn 不中止）。stories 路徑因冪等不受影響。
- [x] **D3 modify_existing 看不到 batch 進度**：`ChangeImplementStatus` 只顯示單一 session。
      修：加 batch 水合+輪詢，有 batch 時 render 既有 `ImplBatchProgress`（逐 story 進度表、點列看該 session log），
      cancel 改為整批取消；render 對 session=null 做 null-safe。

- [x] **D4 故事編號撞既有 repo 歷史**（live batch 39 暴露）：fan-out brief 從 Story 1.1 重起編，
      與既有 repo 已 closed 的 Story 1.x issue 撞號 → `_batch_skip_keys`（只比對 N.M 編號）誤判「已完成」
      → 靜默跳過 1.1–1.5（只跑了沒撞的 1.6/1.7）。
      修：`change_request_system.md` 加「編號必須接續既有 repo 最大號、不從 Epic 1 重起」；
      JetBook 這份 brief 已就地 renumber 成 Epic 13（13.1–13.7）避開後端 1–12。

## 驗證
- [x] 後端 pytest `tests/` 436 全綠（含新 offload 回歸測試）。
- [x] 前端 `tsc --noEmit` 乾淨。
- [x] live：batch 39 經 UI 跑成功（D1 不凍/D2 發 issue/D3 進度 + 2 真實 MR !69/!70），並據此抓出 D4。
- [ ] 未在執行中的後端生效（需重啟載入）；未對 JetBook 真實重跑（且 GitLab 當時不通）。

## Review
D1 是真 bug（同步阻塞凍 event loop），最該修、影響所有 implement 路徑——已 off-loop + timeout 雙保險。
D2/D3 是 modify_existing 這條路的完整性缺口。三項皆向後相容、不破既有 stories/single 路徑。

---

# 治本：modify_existing 支援多 story fan-out（per-story issues + batch MR）

## 根因
`modify_existing` 只產 `change_request` brief（`CH-N` 條列、非 canonical），而 publish / implement-batch / 前端 ImplementWorkspace 都只吃 `stories` artifact 的 canonical heading（`## Epic N:` / `### Story N.M —`）。
→ 大型新增（如補整個前端）只能走 single implement（整份 brief = 1 story = 1 PR），塞不進 10–15 分鐘預算、做不完；也無法 per-story 各開 MR。
（已查證：前端 `app/page.tsx:864` 註解明寫 batch/ImplementWorkspace「以 stories 為前提鎖死」；`implement_start_batch`/`stories_publish`/`preview-delivery` 都 `get_artifact(tid,"stories")`。）

## 設計（向後相容）
小改維持 `CH-N` → single PR（現狀不動）；**大改改用 canonical Epic/Story** → 交付端偵測到就 fan-out 成 issues + batch MR。

- [x] **A 生成端**：`change_request_system.md` §3 Changes 分流：小改 `CH-N`、大改 canonical `## Epic N:` + `### Story N.M —`（同 user_stories 契約）；`change_request_refine.md` 保留不降級。
- [x] **B 交付端**：`app._delivery_story_artifact(tid) = stories ∥ change_request`，套用 preview-delivery / stories_publish / implement_start_batch / implement_start。brief 無 canonical story → parse 0 筆 → 不誤觸 fan-out。
- [x] **C 測試**：`tests/test_modify_existing_fanout.py` — canonical brief→2 story、CH-N→0、fallback 正確。
- [x] **D（Phase 2，前端）**：`ChangeRequestWorkspace` 偵測 brief 含 ≥2 canonical story（`countStoriesAndEstimate`）→ 顯示「逐 story 實作 → N MR」按鈕（`startBatch`, `auto_merge:false`）+「整包一個 PR」並列；CH-N 小改維持單一 Implement 按鈕。讀過 Next 16 docs、改動比照既有 pattern。

## 驗證
- [x] 後端 pytest：`tests/` 435 全綠（delivery/batch/publish 既有不破 + 新 fallback + fan-out 新測試）。
- [x] 前端 `tsc --noEmit` 乾淨；gating 用既有對稱 parser（canonical→≥2、CH-N→0），與後端測試一致。
- [ ] 互動 render 未做（需隔離 stack + canonical-brief thread，不擾動使用者 8724）。
- [ ] 對 JetBook 真實重生 brief → publish 7 issues → batch per-story MR：**未做**。需 (1) 重啟 Lodestar 後端載入新 prompt+code（render_prompt 快取），(2) 跑 ~2hr batch。auto_merge 預設關，不自動合進 main。

## Review
**缺口**：modify_existing 只會「brief → 1 PR（single）」，大型新增（如補整個前端）塞不進單次預算、也無法 per-story 各開 MR。
**修法（向後相容）**：
- 生成端：change_request brief 大改改用 canonical Epic/Story（小改維持 CH-N）。
- 交付端：`_delivery_story_artifact` 讓 publish/implement 在無 stories 時 fallback 讀 change_request；無 canonical story → parse 0 筆 → 不誤觸 fan-out。
- 前端：modify_existing 面板偵測到多 story 時，開放 batch 入口（auto_merge 關）。
**影響面**：後端 change_request_system.md / change_request_refine.md / app.py(+helper, 4 讀取點) + 新測試；前端 page.tsx（handler + ChangeRequestWorkspace 按鈕）。全 warn/可選，不破既有 single 路徑。
**已知限制**：未在使用者執行中的 instance 生效（需重啟）；未跑真實端到端；前端未互動 render。auto_merge 刻意關閉，避免自動合未審 AI code。

---

# 治本：交付面契約 —— 讓「含 UI 的完整專案」不再被砍成純後端

## 根因（已用 JetBook 實際 artifact 證實）
前端在整條契約鏈裡**沒有任何結構性錨點**：
1. **PRD 無「交付面」宣告** —— `sa_system.md` PRD 格式只有 Overview/FR/NFR/OPS，
   「要不要前端」只能藏在 FR 描述裡，不是第一級、會往下傳的契約。
2. **架構這關是斷點** —— `_arch_generate` 只餵 `PRD_DRAFT`、看不到 UI 設計；
   `architect.md` 的 tier 框架 100% Android/Gradle（單一 app 心智模型），
   面對 fullstack PRD 時模型自行「鎖定單一領域」並用 `cwd=backend` 當藉口砍掉前端。
   （JetBook arch 開頭明寫「我以 Backend 為目標領域」，ADR 有 `Target Domain: Backend`。）
3. **故事 UI 規則是軟的** —— stories 已收到 UI 設計 brief，但只「reference if provided」，
   無「前端 epic 必須存在 / 每個 Screen 必須有 story」的硬閘門；架構說後端就跟著後端。
4. 證據：JetBook ui_design = 48KB / 5 個畫面（完整產出），stories = 58KB 但前端關鍵字命中 1 次 ≈ 零前端。
   → 5 個設計畫面全成孤兒，實作純後端。前一輪「by design 沒前端」判斷有誤。

## A. PRD：加「交付面 / Delivery Surface」第一級宣告（源頭釘死）
- [x] `sa_system.md`：Rules 加「2b. 必須釘死交付面」+ PRD format 插入 `## 2. Delivery Surface`（FR→4、NFR→5…重編號）。
- [x] `prd_refine.md`：保留並可修訂此節；refine 不得默默砍層；legacy PRD 自動補。
- [x] `prd_stage.py`：warn-only validator `prd.has_delivery_surface`。

## B. 架構：殺單一領域陷阱 + 平台泛化 + 看得到 UI
- [x] `architect.md`：新增 Step 0「Cover the full delivery surface」HARD RULE —— 覆蓋每個 In-scope 觸面、
      禁止以 cwd/工作目錄為由縮成單一領域；多觸面→fullstack（各層 stack + layout + API 契約）。
- [x] `architect.md`：tier 框架去 Android 化（platform-neutral）+「Default layout by platform」
      （fullstack-web / backend-service / mobile / CLI）；Gradle/NIA 降為 mobile 範例。anti-patterns 泛化。
- [x] `architect.md` + `_arch_generate`：`{{UI_DESIGN_BRIEF}}` 餵進架構（strip HTML，缺則 fallback 文字）。
- [x] 軟依賴機制：`StageSpec.soft_depends_on`（新欄位）+ `workflow_engine.run_stage` 有就餵、缺不擋、不參與 gating/拓樸。
      `architecture` 加 `soft_depends_on=("ui_design",)`；register 顯示序改 `prd → ui_design → architecture → stories`。
      → 純後端專案（無 ui_design）不被誤殺（已驗證）。

## C. 故事：UI 覆蓋從「軟引用」升級為「硬閘門」
- [x] `user_stories.md`：「UI alignment」改寫為「Frontend & delivery-surface coverage」HARD RULE ——
      交付面含 UI → 必須有前端 Epic（含 scaffold + vertical story）；每個 `## Screen` 必須對應 story。
- [x] `stories_stage.py`：`_stories_coverage_validator`（warn-only）抽 ui_design 畫面清單 vs stories 引用，
      列出未覆蓋畫面；畫面清單由 handler 經 `metadata['ui_screens']` 傳入。

## D.（follow-up，不混入本次）DX 收尾覆蓋
- README / docker-compose / .env.example 確定性生成 —— 獨立 task（與上一個 spec_sync task 合流）。

## 驗證（全部通過）
- [x] 後端 pytest：`tests/` 432 全綠（修了 5 處因刻意契約變更的斷言 + 2 處上一個 task 留下的 vertical-launch fixture 債）。
- [x] coverage validator 打 JetBook 舊 artifact：抓到 5 畫面中 3 個（Document Editor/Publish & Revisions/AI Knowledge Assistant）零對應。
      （Dashboard/Search 因後端同名 endpoint 字串被當已覆蓋 —— warn-only 可接受誤放；新流程帶 `Reference: Screen:` 標記會精準命中）。
- [x] 架構 generate prompt 端到端：Step 0 規則 + cwd 禁令 + fullstack 範式 + UI 畫面餵入(HTML strip) 全部到位。
- [x] 軟依賴缺不擋：純後端 PRD（Human Web UI: Out）無 ui_design → 架構照跑、error_code 空。
- [ ] （未做，需真模型）拿 JetBook PRD 真實重跑架構，驗證模型不再砍前端 —— 待使用者要時再跑（會燒 claude-cli 配額）。

## Review
**根因（JetBook 實證）**：前端在契約鏈無錨點。UI 設計（48KB/5畫面）有產出，但架構只收 PRD、被 Android 單一 app 框架逼著「鎖定單一領域」、用 cwd=backend 砍掉前端；故事跟著架構走 → 純後端，5 畫面全孤兒。前一輪「by design 沒前端」判斷有誤。

**修法（治本，跨 PRD/架構/故事 + 軟依賴 + 覆蓋閘門）**：
1. PRD 釘「交付面」第一級契約，往下傳。
2. 架構強制覆蓋每個 In-scope 觸面、去 Android 化、看得到 UI 設計。
3. 故事 UI 覆蓋升為硬閘門 + warn validator 當最後防線。
4. 新 `soft_depends_on`：架構能參考 UI 設計，又不誤殺純後端專案。

**影響面**：8 個檔（sa_system/prd_refine/prd_stage/architect/architecture_stage/register/user_stories/stories_stage）+ engine 2 檔（plugin_api/stage、workflow_engine）+ 7 個測試同步。warn-only、不阻斷既有流程。

**已知限制**：coverage validator 用 substring 比對，舊 stories 會有同名 endpoint 誤放（warn-only，安全方向）。真模型重跑驗證待使用者觸發。

---

# 治本：mermaid 語法防線 + README 確定性生成

起因：JetBook arch diagram 2 因 sequence 訊息標籤含 `;`（mermaid 語句分隔符）語法錯誤；
AI chat「修正」誤診兩次都沒中，且第二次只回片段沒寫回 artifact，壞圖還上了 wiki。

決策（已與使用者確認）：
- 驗證守門：前端真 parser 擋發佈
- 修正端：回寫修復 + 改完先驗證
- README：最後加確定性 README 生成步驟

## A. 生成端（降低發生率）
- [x] `prompts/architect.md` / `arch_chat.md` 加 mermaid 撰寫約束：sequence 訊息標籤禁用 `;`（用「，」或 `then`）、避免裸 `<`/`>`。

## B. 前端真 parser 守門（防壞圖上 wiki）
- [x] `frontend/lib/mermaid.ts`：`validateMermaidMarkdown` / `validateStagesMermaid` 抽 ```mermaid 逐塊 `mermaid.parse()`。
- [x] `DocsPublishModal` / `SpecSyncModal`：confirm 前驗 PRD+architecture，有壞圖就擋下、列出哪張圖+錯誤，附「仍要發佈（忽略警告）」逃生口。

## C. 修正端（chat 改圖治本）
- [x] `arch_chat.md`：任何修改請求（含「只改 X / 只生成 X」）都必須回**完整文件**包 marker，絕不回裸片段。
- [x] `_shared.py`：`lint_mermaid` / `autofix_mermaid`（旗艦規則：sequence 訊息標籤含 `;`，flowchart 不誤報）。
- [x] `_arch_chat`：產出 updated 後跑 autofix；確定性修正並透明告知；無法自動修不謊報、如實警示。

## D. README 確定性生成（治本）
- [x] `spec_sync.build_readme(name, prd, arch)`：抽概述/FR 功能群/tier+技術棧/規格指引/開發，內容豐富不留樣板。
- [x] managed marker：re-sync 升級 Lodestar 受管 README（含舊 stub 簽名），人工/agent 充實過（無 marker）絕不覆寫。

## 驗證
- [x] 後端 pytest：test_arch_stage（含 3 個新 mermaid lint 測）+ test_spec_sync（含 README 升級測）+ docs_publisher 全綠。
- [x] `lint_mermaid` 對 JetBook 壞版本抓到、autofix 後乾淨、flowchart 分號不誤報。
- [x] 前端 `validateMermaidMarkdown` 對 JetBook arch：BAD→diagram 2 flagged、GOOD→diagram 2 pass；抽塊正確（3 塊）。tsc 通過。
- [x] 治標：JetBook arch artifact 的 `;` 已改，重新發佈即更新 wiki。

備註：test_stories_stage 2 筆失敗為**本 session 前既有**（未提交的 stories `vertical_no_launch_cmd` validator，與本次無關）。

## Review
- 三層防線一次補齊：生成端 prompt 約束（降發生率）+ 前端真 parser 發佈/同步守門（權威攔截）+ chat 改完 autofix 驗證（不再謊報已修正）。
- 架構決策：後端 Python 不跑 JS mermaid parser（需 jsdom 跨 runtime 又脆，flowchart/state 在 node 會炸 DOMPurify）；真 parser 守門放前端（真 DOM）；後端只做確定性 focused lint。
- README 從「靠 implement agent、常留一行 stub」改為確定性生成 + managed-marker 可升級；不覆寫人工成果。
- 未做：後端發佈 API 的伺服器端 backstop（依使用者決策以前端守門為主，從略）；可日後加。
