# Plan：stories→issues 冪等重推（補失敗、不重複、不動已建）

## 目標（使用者情境）
「target github · 預計 87 · 已建立 80 · 失敗 7」→ 要能**重推只補那 7 個**：
- 不重複建立已建好的 80 個
- 不影響已建好的
- 重跑可收斂（最終 87 個都在）

## 現況（為何現在不行）
- `builtin_integrations/_publish_github` 對**每個** DeliveryItem 都 POST 建 issue，**零去重** → 重跑會把 80 個再建一次（重複）。
- 失敗只 `log.warning`，API 只回 `created` URL 與 `count`；前端用 `count - created` 算「失敗 7」，**看不到哪幾個、為什麼失敗**。
- 失敗主因推測：GitHub **secondary rate limit**（短時間連建大量 issue 觸發濫用偵測 → 403），屬暫時性。

## 既有可重用零件
- DeliveryItem.title = `Story N.M — <title>`；`batch._story_key` 用 `Story\s+(\d+\.\d+)` 抽編號。
- `batch.match_issues` 已用 story 編號把既有 issue 對到 story。
- `github_pr.list_open_issues` / `list_closed_issues`、`gitlab_mr.list_open_issues`（list (number,title)，失敗回 []）。
- plugin 契約：integration **不可** import host 模組 → 去重邏輯放 **host 端**（同 batch 的 match 模式）。

## 設計：host 端冪等過濾 + 逐項失敗回報 + 限流退避
1. **host 端冪等過濾（核心）**——publish 端點 `POST /api/stage/stories/{tid}/publish`：
   - 對支援列舉的 target（github/gitlab）先列**既有 issue（open+closed）**→ 抽出已存在的 story 編號集 `existing_keys`。
   - items 拆成 `to_create`（編號不在 existing_keys）與 `skipped`（已存在）。
   - 只把 `to_create` 丟給 `integ.publish`。
   - 列舉失敗（回 []）→ 視為無既有 → 全建（寧可重做也不要漏；與既有 helper 的 fail-open 一致，但此時會有重複風險 → log 警告提示）。
   - **永遠開啟**（不需旗標）：首次 publish existing 為空 → 全建；重跑 → 只補缺。
2. **逐項失敗回報**——`DeliveryPublishResult` 加 `failed: list[(title, reason)]`；`_publish_github`/`_publish_gitlab` 收集每個失敗的 title + 原因（HTTP code + 訊息片段）。
3. **限流退避（降低失敗率，治本）**——`_publish_github` 對 **403/429 secondary rate limit** 讀 `Retry-After`（無則指數退避）睡一下重試（最多 3 次）；非限流錯誤直接記 failed。避免「連建 87 個觸發濫用偵測」。

## 變更檔案
- `backend/plugin_api/common.py`：`DeliveryPublishResult` 加 `skipped: int = 0`、`failed: list[tuple[str,str]] = []`。
- `backend/plugins/builtin_integrations/register.py`：`_publish_github`（+ `_publish_gitlab`）收集 `failed`、加 403/429 退避重試。
- `backend/app.py`：publish 端點加冪等過濾（列既有 issue → 抽 keys → 過濾 items），回應帶 `skipped`/`failed`；小 helper `_existing_story_keys(target, creds, repo)`。
- `backend/api_models.py`：`DeliveryPublishResponse` 加 `skipped: int`、`failed: list[{title, reason}]`。
- `frontend/components/PublishModal.tsx`：結果頁顯示「新建 X · 跳過 Y · 失敗 Z」+ 失敗清單（title + reason）；可直接「再次發佈」補缺。
- 測試：`backend/tests/test_delivery_publish.py`（或既有）加：冪等過濾（既有 80 → 只建 7）、列舉失敗 fall-open、failed 回報、退避重試（mock urlopen 先 403 再成功）。

## 行為（修好後）
- 第一次：existing=0 → 建 87（其中 7 個若限流，退避重試多半救回；真失敗者列在 failed）。
- 再按一次發佈：列到 80（或 80+幾個救回的）既有 → 跳過，只重試仍缺的 → 收斂到 87，**不產生重複**。
- 前端顯示「跳過 N」讓使用者看懂「沒有重複建」。

## 不做（範圍外）
- jira 仍為 stub。
- 不持久化逐項 delivery 狀態（以 GitHub 既有 issue 為事實來源，重啟也對）。

---

## Review（已完成 2026-06-22）
**先處理既有災情**：S-Term 已被重複發佈成 160 issue（80 story × 2）。經使用者選擇，關閉 #81–160（第二次發佈的重複，可 reopen），保留 #1–80 → repo 收斂成 80 個、無重複。缺漏的 7 個（23.1–25.3）待用修好的發佈補。

**改動檔案**
- `plugin_api/common.py`：`DeliveryPublishResult` 加 `skipped`、`failed`。
- `plugins/builtin_integrations/register.py`：`_publish_github`/`_publish_gitlab` 收集 `failed:[(title,reason)]`；github 對 403/429 讀 `Retry-After` 退避重試（`_MAX_RETRY=3`）。
- `async_runtime/github_pr.py` + `gitlab_mr.py`：新增 `list_all_issues`（state=all，**失敗 raise**，不吞）。
- `app.py`：`_existing_issue_keys`（列既有→抽 story key，失敗 raise）；publish 端點冪等過濾（dry_run 跳過）、回應帶 `skipped`/`failed`、列舉失敗→502。
- `api_models.py`：`DeliveryPublishResponse` 加 `skipped`/`failed`。
- `frontend/components/PublishModal.tsx`：結果頁顯示「新建/跳過/失敗」+ 失敗清單；全跳過顯示「已是最新」。

**驗證**
- 單元/整合：`test_delivery_publish.py` +4（冪等跳過、逐項失敗、限流退避、列舉失敗中止）；全套 **424 passed**，2 failed 為既有 `test_stories_stage` 無關。
- 真實 repo（唯讀）：`list_all_issues` 取 80 keys → 過濾 87 items → **待建 7、跳過 80**，正是缺的 23.1–25.3。

**關鍵後續**：使用者運行中的後端是舊 code（無 --reload）→ **必須重啟後端**，否則再按發佈仍走舊路徑會重複。重啟後按一次發佈即補 7、跳 80、不重複。

**設計重點**：列既有 issue 用 raise-on-failure（不可吞）—— 若把「查不到」誤當「沒有」就會重複發佈（正是本 bug）；故寧可 502 中止也不靜默全建。冪等以 GitHub 既有 issue 為事實來源（story 編號比對，沿用 batch.match_issues）。
