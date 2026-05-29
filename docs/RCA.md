# Manufacturing RCA — `rca_domain` plugin

製造現場異常時，AI 協助工程師更快整理「候選根因 / 證據線索 / 下一步該檢查什麼」的 PoC。
以 **plugin** 形式掛在 Lodestar 上，與既有「需求工程」領域**並存**（核心引擎零修改）。

> **AI 是 Copilot，不是 Judge。** 輸出是供工程師確認的**候選根因**，不是最終判決。
> 真正原因仍須依現場設備、製程條件、維修紀錄與實際檢查確認。UI 與 prompt 都貫徹此定位。

## 三種執行方式（+ 協作模式）

| Workflow | 模式 | stages | 說明 |
|---|---|---|---|
| `rca_single` | 單代理 | rca_intake → rca_analysis | 一個 copilot 直接跑完，產候選根因表 |
| `rca_chain` | 多代理鏈 | intake → baseline → causal → knowledge → synthesis | specialist 分工：基線量化 → 因果圖 → 知識/SOP 對照 → 彙整 |
| `rca_planner` | Agentic 動態規劃 | intake → rca_plan | AI 產 workflow plan → 人核准 → `apply-plan` 轉成真 workflow 執行（可規劃/可派工/可追蹤）|
| `rca_panel` | collab · discussion | intake → rca_analysis（lead + 2 peer）| 多 specialist 輪流發言、lead 合成 |
| `rca_dispatch` | collab · dispatch | intake → rca_analysis（lead + 2 subagent）| lead 拆任務 → subagent 平行 → lead 合併 |

候選根因 artifact 格式：`Baseline vs. Anomaly` + 排序候選表（`Rank | 候選 | 信心 | 證據 | 下一步檢查`，≥3）+ 建議檢查順序 + copilot 免責聲明。warn-only validator 會提醒缺漏（候選數 / 證據 / 下一步 / 免責 / 因果圖 / plan 形狀）。

## 合成資料（fixtures）

`backend/plugins/rca_domain/fixtures/`：
- `yield_drop/` — 良率步階下降，集中於 ETCH-03（recipe 不變）→ `rca_single`
- `param_drift/` — ETCH-03 chamber_pressure 自 05-21 漂移破 spec → `rca_chain`
- `signal_anomaly/` — ETCH-03 endpoint 訊號間歇異常 → `rca_chain`

每個 `scenario.md` 只描述症狀、不洩漏根因。資料檔掛在「會讀它的 stage」（單代理=rca_analysis、鏈=rca_baseline）；claude-cli 經 `--add-dir + Read tool` 原生讀 CSV。

## 用法

一鍵 seed 三個示範 thread：
```bash
cd backend && python -m scripts.seed_rca      # 需 LODESTAR_UPLOADS_DIR 由 app 設定
```
- **UI**（`./start.sh` → http://localhost:8724）：選 RCA thread → workspace 自動切成 RcaWorkspace；逐 stage Generate / Refine / Approve；候選表可逐列 Confirm / Reject（諮詢用）。
- **API**：`POST /api/stage/{id}/generate`、`/refine`、`POST /api/stage/{id}/{tid}/approve`；planner：generate `rca_plan` → approve → `POST /api/projects/{tid}/rca/apply-plan`（重用 `_save_workflow` + `set_project_workflow`，未核准回 409、未知 stage 回 400）。

## 檔案地圖

```
backend/plugins/rca_domain/
  plugin.toml  register.py  _shared.py
  intake_stage.py  analysis_stage.py        # 單代理
  chain_stages.py                           # baseline/causal/knowledge/synthesis（工廠函式）
  planner_stage.py                          # rca_plan + parse_plan + plan-shape validator
  prompts/*.md  fixtures/*/
backend/collab_coordinator.py               # host 層：discussion / dispatch 執行（§6.4）
backend/workflow_engine.py                  # dispatch() 加 collab 分支（mode≠single 且 binding>1）
backend/app.py                              # POST /api/projects/{tid}/rca/apply-plan
backend/scripts/seed_rca.py
frontend/components/RcaWorkspace.tsx        # RCA UI（候選表/信心 chip/護欄帶/Mermaid/plan apply）
backend/tests/test_rca_stage.py test_rca_chain.py test_rca_planner.py test_collab.py
```

核心未改：`rca_*` 與既有 stage/workflow 零碰撞，無 DB migration。collab 分支僅在 `collab_mode≠single` 時啟動，不影響單代理/鏈/需求工程流程。

## 測試
```bash
python -m pytest backend/tests/           # 全套（含 RCA 共 23 個新測試）
```
RCA 測試涵蓋：plugin 載入、依賴鏈與下游 reset、缺上游擋關、mock 端到端、validators、planner parse / apply-plan（含 409 / 400）、collab discussion / dispatch / agent 解析。
