# Plan：implement 撞 5hr 用量上限 → 自動等重置續跑

## 目標
claude-cli 跑 lodestar implement（single / roles / batch）時撞到 **5 小時用量上限**，要：
1. **偵測**到是用量上限（而非一般失敗）
2. **抓出解鎖時間**（reset at）
3. **可取消地等到**解鎖時間（+緩衝）
4. **自動續跑**同一份工作，過程透明、最終結果誠實（曾中斷但最終完成，不留誤導的 failed）

## 關鍵事實（已查證）
- claude CLI 版本 2.1.183；5hr 上限是**訂閱方案層級**，不是 API 429。
- 官方文件化的人讀訊息：`You've hit your session limit · resets 3:45pm`（另有 weekly / Opus 變體）。
- **沒有**文件化的結構化 reset 欄位 → 只能字串解析，且格式可能隨版本變。
- 另一已知格式 `Claude AI usage limit reached|<epoch>`（pipe + epoch 秒）官方文件查不到，但一併支援無害。
- 結論：偵測要**容錯多種字串**，解析不到時間就退回固定預設等待。

## 範圍：implement（async）＋ harness（sync）兩條都接
所有 claude 生成只經兩個咽喉點，兩個都接、共用同一套偵測：
- **async**：`AgentRunner.run()`（`ClaudeCliRunner`）—— single fix-loop / roles pipeline / batch 每個 story。
- **sync**：`claude_cli._invoke()`（`ModelAdapter`）—— harness 生成 stage（prd/stories/arch/ui…）、judge 呼叫、collab。
  經查 `app.py` stage_generate/refine/chat 皆 `await asyncio.to_thread(...)` → 跑在 worker thread，
  故 sync `time.sleep` 只擋住該 thread、不凍住 event loop，可行。
- 範圍外：codex-cli / agy-cli / mock 不接（非 claude，沿用原行為）。

## 設計決策：注入在單一咽喉點 `AgentRunner.run()`（async）
single fix-loop、roles pipeline（lead/rd/tester/reviewer）、batch 每個 story —— **全部**經過
`plugin_api/model.py` 的 `AgentRunner.run()`。在這層處理 = 一份實作覆蓋全路徑，呼叫端（orchestrator
兩個 loop）零改動，等待對它們透明（回傳的就是「等完並續跑後的最終 result」）。

職責分離（避免 plugin_api 汙染 claude 細節）：
- **base `run()`（generic）**：「子類說被限到 T → 記 log/事件 → 可取消睡到 T → 重啟同一子程序」的機制 + 安全上限。
- **子類 `ClaudeCliRunner`（claude 專屬）**：只負責「從我的輸出判斷是否被限、解鎖何時」。

## 變更檔案
1. **新增 `backend/plugin_api/rate_limit.py`** — 純函式、好單測、僅 stdlib（共用於 async + sync 兩路）。
   放 plugin_api 因兩個 plugin（builtin_models / builtin_implement）都已相依它、且它本就 stdlib-only。
   - `detect(output, *, now) -> Optional[RateLimitSignal]`，容錯：
     - `Claude AI usage limit reached|<epoch>` → reset = epoch（秒或毫秒）
     - `... session/weekly/Opus limit · resets 3:45pm` / `resets Mon 12:00am` → 算「下一次該本地時刻」
     - 只有片語、解析不到時間 → `RateLimitSignal(reset_at=None)`（交由 caller 用預設等待）
     - 僅在「明確的上限片語」才命中，降低誤判（agent 程式碼剛好提到 usage limit 不算）。
   - `seconds_until(signal, *, now, cfg) -> float`：算睡眠秒數（reset 後 +buffer、封頂 max_wait、過去→極短）。
   - `load_config()`：讀 env `LODESTAR_RATELIMIT_DEFAULT_WAIT`(3600) / `_MAX_WAIT`(6h) / `_BUFFER`(60) / `_MAX_CYCLES`(6)。
   - `RateLimitSignal` dataclass（`reset_at: Optional[datetime]`, `message: str`）。從 plugin_api 匯出。

2. **`backend/plugin_api/model.py`**（async 路徑）：
   - `AgentRunner._detect_rate_limit(output) -> Optional[RateLimitSignal]`，base 回 `None`（no-op，僅 claude 覆寫）。
   - 重構 `run()`：把單次 spawn＋drive 抽成 `_spawn_once(...)`；外面包迴圈：
     - 跑完若 `ok` / `cancelled` / `timed_out` → 直接回（不變）。
     - 純失敗（exit≠0）→ `_detect_rate_limit(last_output)`：
       - `None` → 照舊回失敗（交給 orchestrator 既有重試策略）。
       - 命中 → `seconds_until` 算等待 → `on_log`/`on_event` 記「等到 HH:MM 自動續跑」→ **可取消睡眠** → 重 spawn 同 argv/prompt。
   - `cancel()` 同時 set 一個 `asyncio.Event`，讓睡眠中被取消能即時中止 → 回 `cancelled=True`。
   - 睡眠抽成 `_sleep(seconds)` 方便測試覆寫（不真的睡）；續跑輪數封頂 cfg.max_cycles。

3. **`backend/plugins/builtin_implement/runner.py`**（async）：
   `ClaudeCliRunner._detect_rate_limit` 委派給 `plugin_api.rate_limit.detect`。Codex/Mock 不覆寫（沿用 base no-op）。

4. **`backend/plugins/builtin_models/claude_cli.py`**（sync harness 路徑）：
   `_invoke` 包一層重試迴圈：跑完先 `rate_limit.detect`（同時看 stdout+stderr，不論 exit code）；
   - 命中 → log「等到 HH:MM 自動續跑」→ `time.sleep`（可測試覆寫 `_sleep`）→ 重跑同 cmd（封頂 max_cycles）。
   - 未命中 → 維持原行為（exit≠0 raise RuntimeError、exit 0 回 `_parse_stream_json`）。
   - sync 路徑不做取消（一次性生成、跑在 worker thread；要中止就重啟，符合既有 stage 生成語意）。

## 行為 / UX
- 等待期間 session 維持 `running`（它確實還在跑，只是睡著）；run log 出現
  `⏸ 已達用量上限，將等到 2026-06-21 15:45（約 2h12m）後自動續跑` 與 `▶ 用量已重置，續跑中`。
- 續跑用同一 cwd → agent 看得到中斷前已寫入的半成品，接續完成（與既有重試同樣靠磁碟狀態）。
- batch / session 的 cancel 在等待中即時生效。
- 最終 run 以真正結果收尾（succeeded / failed），不因「曾被限流」而誤標 failed。

## 測試與驗證
- **新增 `backend/tests/test_rate_limit.py`**：固定 `now`，覆蓋 epoch（秒/毫秒）/ session / weekly / Opus /
  片語無時間 / 非限流失敗（不誤判）/ 過去時間 / 超大 reset 被封頂 / seconds_until 各分支。
- **async**：假 runner（輸出限流訊息 N 次後成功）+ 覆寫 `_sleep`，斷言「等待→重啟→最終 ok」、
  續跑輪數封頂、等待中 cancel → cancelled。
- **sync**：monkeypatch `subprocess.run`（先回限流、再回正常）+ 覆寫 `_sleep`，斷言「重試→最終回正常輸出」、
  非限流非零 exit 仍 raise。
- 跑全測試套件（`backend/tests`）確認無回歸。

## 不做（範圍外）
- codex-cli / agy-cli / mock 不接（非 claude）。
- sync 路徑不做取消（一次性生成、worker thread；要中止就重啟）。
- 不引入 `--resume/--continue` 的 CLI session 續接（無狀態重跑同 prompt 已足夠，且更穩；續跑靠 cwd 磁碟狀態）。

---

## Review（已完成 2026-06-21）
**改動檔案：**
- 新增 `backend/plugin_api/rate_limit.py`（偵測 + 解析 + seconds_until + humanize；JSON-aware 防誤判）
- 新增 `backend/tests/test_rate_limit.py`（28 測試：解析多格式 + 不誤判 + async 等待/續跑/封頂/取消 + sync 重試/raise/正常不受影響）
- `backend/plugin_api/model.py`：`run()` 重構成「spawn→偵測→可取消睡→續跑」迴圈 + `_spawn_once`/`_detect_rate_limit`/`_sleep`；`cancel()` 喚醒睡眠；新增 tail 緩衝（大型輸出也偵測得到最後的上限事件）
- `backend/plugin_api/__init__.py`：匯出 `rate_limit` / `RateLimitSignal`
- `backend/plugins/builtin_implement/runner.py`：`ClaudeCliRunner._detect_rate_limit` 委派偵測
- `backend/plugins/builtin_models/claude_cli.py`：`_invoke` 加重試迴圈 + `_sleep`

**驗證：** `pytest tests/` → **411 passed, 2 failed**。2 個失敗在 `test_stories_stage.py`（`vertical_no_launch_cmd` validator），
經 `git stash` 證實源自 session 前既有的 `stories_stage.py` 未提交改動，**與本次無關**（stash 後 stories 測試全過）。本次改動零回歸。

**設計重點 / 取捨：**
- 注入單一咽喉點（async `run()` + sync `_invoke`），呼叫端零改動、等待透明。
- 偵測 JSON-aware：排除 assistant 內容與成功 result 的 `result` 欄位，避免產出物（PRD/stories）合法提到 "usage limit" 被誤判。
- head-only 擷取會漏看尾段的 result/上限事件 → 加 tail 環狀緩衝，head∪tail 供偵測。
- 解析不到解鎖時間 → 退回 `default_wait` 仍自動續跑（不放棄）。
- 安全：封頂 `max_wait`、續跑輪數封頂 `max_cycles`，防解析錯誤造成超長等待 / 無限迴圈。
