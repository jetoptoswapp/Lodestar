# Plan — implement 階段接上自訂 agent（並補 lint gate）

## 問題

使用者可在 `/agents` 自訂 agent（Orchestrator / [Domain] Implementation / Tester，皆 role="implement"），
但 **implement 執行路徑完全不讀它們**。最初的提問「implement 是否該放 android/ios/web/backend 的 lint」，
前提是 lint 要有地方掛 —— 而現在連 agent 的 system_prompt 都沒被消費，lint 寫進去也讀不到。

### 盤點：各路徑實際吃了哪些 agent 欄位
| 路徑 | system_prompt | model_choice | tools | max_iter | skills |
|---|:--:|:--:|:--:|:--:|:--:|
| prd / architecture / stories | ✅ {{PERSONA}} | ✅ | ✅ | ✅ | ✅ |
| collab（discussion/dispatch） | ✅ | ✅ | ✅ | ✅ | — |
| RCA domain stages | ❌ | ✅ | ✅ | ✅ | ❌ |
| **implement（本案）** | ❌ | ❌ | ❌ | ❌ | ❌ |

### 三個落差
1. **Gap 1（本案主目標）**：implement 不走 workflow dispatch。`stage.py` 是純 marker（generate=None），
   實作由 `/api/implement/*` → `orchestrator.run_implementation_roles` / `batch.py` 跑，用寫死的
   lead/rd/tester/reviewer prompt（`_role_prompt`）＋全程共用一個 runner，從不 `resolve_agent`。
2. **Gap 2**：RCA prompts 無 `{{PERSONA}}`（0 筆），register 自承「system_prompt 只放短角色描述」→ 改 RCA agent persona 不生效。
3. **Gap 3（母題）**：`HarnessRunner.harnessed_step` 不在中央灌 system_prompt，靠各 stage .md 自己放 placeholder；
   忘了放就靜默丟棄，無任何 warning。

### 既有設計意圖（重要）
前端 mock 已把 implement 設計成 **dispatch 協作**：`collab_mode.implement = "dispatch"`、
bindings = `implementation_lead`(lead) + `frontend_engineer`/`backend_engineer`(subagent)
（frontend/app/page.tsx:209）。memory [[multi-agent-stage-binding]] 的 dispatch pattern 也指向同一方向。
亦即「implement 用多 agent binding 跑」本來就規劃好了，只是 async 執行路徑沒接上。

---

## 設計決策（動工前需拍板）

implement 內部步驟（lead→rd→tester→reviewer）要怎麼對應到自訂 agent？

- **方案 A — workflow agent_bindings（推薦，對齊既有意圖）**
  async pipeline 改從 workflow 的 `agent_bindings["implement"]` 解析每步的 agent，role 對應步驟
  （lead→規劃/orchestrator、rd/subagent→domain 實作者、tester→QA、reviewer→審）；無綁定的步驟
  退回現有寫死 persona。對齊前端 mock + collab dispatch 詞彙，天生支援多 domain subagent。
  代價：要把 binding 從 workflow_engine 路徑接進 async 路徑（目前只有 collab 用）。

- **方案 B — 新增 implement 子角色**
  定義 `implement_lead / implement_rd / implement_tester / implement_reviewer`，用既有
  `resolve_lead_agent(role==key 唯一 enabled)` 解析；使用者把三個 agent 的 role 改成對應子角色。
  改動較小、1:1 乾淨；但與前端 mock 的 binding 模型分歧，多 domain subagent 不易表達。

> 待使用者確認 A / B。下列任務先以 **A** 描述，選 B 則 Phase 1 的解析來源換成子角色、其餘相同。

> 注意：使用者目前三個 agent 都 role="implement"，`resolve_lead_agent` 對「同 role 多 enabled」會回 None+warn，
> 故無論 A/B 都需要一次 role/binding 的重新指派（含 migration 或 UI 操作）。

---

## Phase 1 — 接線：async implement 讀 agent + 注入 system_prompt（讓 Gap 1 通電）✅
- [x] async_runtime 維持與 plugin_host 解耦：注入 `persona_for: Callable[[step], str]`（給步驟名回 persona 文字），
      比照 list_issues / build_opener / PrOpener 注入風格。`orchestrator.PersonaProvider` 型別別名。
- [x] app.py `_implement_persona_provider(reg, thread_id)`：讀 `engine.active_workflow_for(tid).agent_bindings["implement"]`
      （binding.role 即步驟名，rd 可多綁取第一個）→ `resolve_agent` → system_prompt；無綁定回 None
- [x] `_role_prompt` 拆成身分句（persona，可覆寫）+ 機器契約（恆來自 code）；`_DEFAULT_PERSONA[role]` 作 fallback，
      persona="" 時 render 逐字相同（零回歸）
- [x] `persona_for` 沿鏈穿入 start_session / run_session_to_terminal / batch.start_batch → run_implementation_roles，
      四步各注入；單一 fix-loop 模式不受影響
- [x] 測試：`_role_prompt` 預設逐字相同、persona 逐步注入、無 provider 不變、provider 端到端從 bindings 解析；
      全套 303 passed 零回歸

## Phase 2 — per-step model_choice（讓 model_choice 通電）✅
- [x] 注入 `runner_for: Callable[[step], AgentRunner|None]`（`orchestrator.RunnerProvider`）；pipeline 每步
      `_runner(step)` 取該步 runner（memoize，fix-loop 沿用同實例）、未指定退回傳入預設 runner
- [x] cancel 正確性：`_runner(step)` 每次把該步 runner 登記為 `_ACTIVE_RUNNERS[session_id]`，cancel 命中正在跑的這步
- [x] 新增並註冊 **CodexCliRunner**（`codex exec --sandbox workspace-write`，agentic 可寫；prompt 走 stdin）
      —— 決策：補 codex runner、agy 步驟退回 claude（agy 是純文字生成器、不能改檔/跑測試）
- [x] app.py `_implement_runner_provider`：binding agent.model_choice → `reg.runners` 取實例；
      未註冊（agy-cli）/ 不可用 → None（退回預設 runner，log 警告）
- [x] 測試：CodexCliRunner argv/註冊、per-step runner 選擇 + fallback、provider 解析（mock→實例 / agy→None）；
      全套 306 passed。live 驗證 SMKB-v2：lead→codex-cli、rd→claude-cli、tester/reviewer(agy)→退回 claude-cli
- [ ] （待首次真跑）codex agentic 旗標的實際行為 live 驗證

## Phase 3 — lint gate（最初的問題）✅
- [x] tester 的機器契約擴為「repo 品質門」：fail-fast 依序 (1) lint/format + type-check 用 repo 自己的設定
      （偵測 package.json scripts / Makefile / pre-commit / ruff·eslint·prettier·tsc / ktlint·detekt /
      swiftlint / golangci-lint），(2) 寫+跑測試；lint/type-check/測試任一失敗 → exit≠0
- [x] 折進 tester（非獨立 step）：複用既有 tester 失敗→帶回饋重做 rd 的回圈，最小改動
- [x] 副作用修正：base runner=mock 時不套用 per-step runner（否則 mock dry-run 會因 codex 可用而真跑 codex）
- [x] 測試：tester 契約含 lint+type-check+fail-fast、點名 ruff/eslint/ktlint/swiftlint；persona 不蓋契約；
      全套 307 passed。live：mock dry-run 四步全 mock（codex 不被觸發）、pipeline 綠
- [~] domain 實作者 persona 寫「lint 以 repo 為準」屬**使用者內容**（domain_impl.system_prompt）；
      建議補但不由 code 強制（tester gate 已是硬把關）

## Phase 4 — binding UI（implement 步驟詞彙）+ 驗證 ✅（核心完成）
- [x] 後端確認：`_save_workflow` 存 `model_dump()` 不 clamp、`AgentBindingPayload.role` 自由字串
      → rd/tester/reviewer 本來就存得進，無需後端改動
- [x] 前端 binding role 改 **stage-aware**：implement → lead/rd/tester/reviewer；其餘 stage → lead/peer/subagent
      （`api.ts` 放寬 `CollabRole`；`bindingRoleOrder(stageId)`；`cycleBindingRole` 依 stage 取詞彙；
      `ROLE_COLOR` 補 rd/tester/reviewer chip 顏色）。collab 詞彙不受影響
- [x] 驗證：`tsc --noEmit` 乾淨；dev server HMR `✓ Compiled` 無 error；
      live API round-trip 建 workflow 綁 orches→lead / domain_impl→rd / ac_tester→tester，
      取回三 role **未被 clamp**（PASS）；單元測試證 provider 從 bindings 解析出 system_prompt
- [ ] （延後）前端 implement 面板顯示本次各步用哪個 agent / model（透明化）
- [ ] （延後，需 Phase 2/3）dry-run：對 dev thread 跑一個 story，確認各步 agent/model/lint 生效

## Phase 5 — 連帶修補（可獨立，徵詢後再做）
- [ ] Gap 2：RCA prompts 補 `{{PERSONA}}` + skills block，讓 RCA agent persona 生效
- [ ] Gap 3：防呆 —— agent 設了 system_prompt 但該 stage 未 render persona 時，log.warning（避免再有「定義卻沒用」靜默發生）

## 範圍外（本案不做，另記）
- implement 的 `tools`/allowed_tools 接線：async `AgentRunner.run` 目前不收 allowed_tools，需先改 runner 介面，獨立處理。

## Review（完工後補）
- 待填：實際改動摘要、main vs 變更行為差異、測試結果。
