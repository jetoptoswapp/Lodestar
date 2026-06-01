# Lessons（從使用者修正中累積，避免重蹈覆轍）

## React：把「吃參數的 callback」直接綁 onClick → event 被當成第一個參數（隱性 bug）
- **症狀**：PRD「Refine…」modal 標題顯示「修訂 undefined」、API 路徑變 `/api/stage/[object Object]/refine`、送出丟 TypeError、靜默失敗。
- **真因**：`onRefine = (stageId="prd") => ...` 這種吃參數的 callback，若被 `<button onClick={onRefine}>` 直接綁，
  React 會把 SyntheticEvent 當 `stageId` 傳入 → `STAGE_LABEL[event]`=undefined、`${event}`="[object Object]"、`setBusyFor[event]` 不是函式而 throw。
  對照組（architecture/stories）有用 wrapper `() => onRefine("architecture")` 包好就沒事 → **同一份 callback 三處用法不一致** 是嗅覺點。
- **修法**：綁定處一律用 arrow wrapper：`onClick={() => onRefine("prd")}`（或傳已包好的 `onRefinePrd`）。**勿** `onClick={fnNeedingArg}`。
- **稽核法**：grep `onClick=\{[a-zA-Z_]\w*\}`（裸 identifier），逐一確認該 fn 不吃「非 event」參數；吃參數者必須在綁定處用 arrow 包。

## React/Next：初始 state 勿在 render 期讀 window（hydration mismatch）
- **症狀**：`useState(() => window.innerWidth >= 1024)` 在 SSR（window undefined）與 client 初值不同 → hydration mismatch（dev overlay「1 Issue」）。
- **修法**：初值用與 SSR 一致的常數（如 `useState(true)`），在 **mount 後的 useEffect** 內讀 window 再 `setState`。window 只在 effect 讀 = SSR-safe。

## 安全：機密（token/PAT）勿存瀏覽器 localStorage → 用 server-side keystore
- localStorage 會被 XSS 讀取且持久留存。改：後端 Fernet 加密存 DB（`keystore.py` + `integration_secrets` 表），
  明文永不回傳前端（GET 只回「是否已設定」+ 非機密欄）；preview/publish 由後端從 keystore 合併機密。
- 金鑰：env `LODESTAR_KEYSTORE_KEY` 優先（正式部署），否則本機 `data/.keystore.key`（0600，gitignore）。升級時清掉 legacy localStorage key。

## GUI 測試：preview 工具在此 Next16/React19 app 的限制（用 eval 取代 click/screenshot）
- **preview_click 觸發不到 React onClick**：回報「Successfully clicked」但 state 不變（全域，連 nav / 側欄鈕都一樣）。
  早期 resize 後甚至會點到「錯的座標」（曾誤觸 stage 按鈕，造成假象 bug）。**改用 `preview_eval` 原生 `.click()`**。
- **抓真實「浮層擋點擊」bug**：原生 click 會繞過 hit-testing，所以要另用 `document.elementFromPoint(cx,cy)`
  確認元素是否真的在最上層（這才等同真實滑鼠）。本輪用此法確認 nav/model popover/modal 皆無遮擋。
- **React controlled input 要用 native setter + dispatch**：`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set` 後 `dispatchEvent(new Event('input',{bubbles:true}))`；select 用 change、Enter 用 KeyboardEvent。
- **同一個 eval tick 內「設值+送出」會 race**：React 狀態還沒 commit，submit 會用到舊值（曾因此建出名稱錯誤的 thread）。**設值與點擊分兩次 eval 呼叫**。
- **preview_screenshot 在 `window.location.reload()` 後會 render 破圖**（內容擠左上角），但 DOM 實際正常；`preview_resize` 可重置。驗證一律以 eval 量測為準，screenshot 僅輔助。
- **等 claude-cli 生成**：用 `run_in_background` 的 bash 迴圈 grep 後端 log 的 `POST .../generate 200`（注意 `grep -c` 多行回傳會讓 `[ -gt ]` 比較式報錯，用單一數字）。

## 前端：popover/dropdown 被同層內容蓋過、看似半透明 → 檢查祖先的 transform/filter（stacking context 陷阱）
- **症狀**：model selector popover 內容後面透出主內容（stepper / 討論面板）的文字，看起來「太透明」。
- **真因不是透明度**：popover 背景其實是 opaque（`--paper` #1f2733、opacity 1）。問題是 **z-index 失效**——
  TopBar `<header>` 帶 `rise-1`（進場動畫，套 `transform`），transform 會**建立新的 stacking context**，
  把 popover 的 `z-40` 困在 header 子樹內；header 在其父層是 `z=auto`，而主內容是它「之後」的兄弟節點，
  於是主內容整片畫在 header（含 popover）之上。跨 stacking context 時，內層 z-index 再高也沒用。
- **修法**：給該 stacking context 的根（這裡是 `<header>`）一個夠高的 `z-50`，讓整個子樹（含 popover）
  排在內容兄弟之上。**不是**去調 popover 自己的 z（那在錯的 context 裡）。
- **診斷法**：`getComputedStyle(popover).backgroundColor/opacity/zIndex`（確認自身 opaque）＋
  `document.elementFromPoint(x,y)` 取 popover 內幾點，看最上層是否 `popover.contains(e)`；
  再爬 popover 祖先鏈找 `transform !== none` 或 `filter` 的節點 = 罪魁 stacking context。
- **通則**：任何「絕對定位浮層被蓋住」先懷疑祖先的 transform/filter/will-change/opacity<1，而非加更大的 z。

## 驗證 UI 不能只靠 typecheck
- className 改動 tsc 永遠綠，但視覺/stacking 問題只能用實際瀏覽器驗（preview_eval + elementFromPoint + screenshot）。

## 前端「假裝成真功能」的 M0 mock 沒接 API → 用新 thread / 空狀態驗
- **症狀**：開新 thread，右側 chat 卻有一整段「PRD Discussion」假對話、輸入框送不出去。
- **真因**：`ChatPanel` 是 M0 靜態 mock（寫死 `CHAT` 陣列、無 props），後端 chat（`/api/stage/{s}/chat` + `/history`）
  其實 M1 就做好了，但前端從沒接。同類前例：workflow 可選 stage 寫死、stepper 用 mock `STAGES` 常數。
- **修法**：元件吃 `thread`+`stageId`，mount/切換時 `fetchStageHistory` 載真實歷史（空 thread → empty state），
  send→`stageChat`→append；`updated_content` 經 callback 回寫 artifact。移除整段 mock（含相關 type / 渲染器）。
- **通則**：驗收任何「看起來已完成」的功能，務必開**新 / 空** thread 看是否還有殘留假資料；
  「後端做了」≠「前端接了」，兩邊都要實際操作確認資料來自 API。

## 判定「dead field／沒實作」前必須徹查整條資料鏈，別信片面報告就外推
- **症狀**：review 時斷言 `AgentSpec.skills`「連 DB 都沒存、DB/API/UI 都沒有」，寫進回覆＋memory＋程式碼註解。
  實際上 DB 有 `skills`＋`agent_skills` 兩表（schema.sql:147-163）、spec 完整設計了 API/UI、前端有 mock 展示——
  只是 host(register_skill)/DAL/API/執行整條中間層沒接。被使用者一句「怎麼沒看到 skill」抓包。
- **真因**：接受了 Explore subagent「agents 表沒有 skills 欄位」的片面結論就外推成「skills 連 DB 都沒有」。
  單一主表沒某欄位 ≠ 整個功能沒資料層——多對多關係常拆成獨立關聯表（agent_skills）。
- **修法**：下「X 是 dead field／沒實作」這種強結論前，逐層查證（契約／**schema.sql 全文**／host 註冊／
  DAL／API／前端／執行消費），DB 要 grep 整個 schema 而非只看主表，spec 文件也要查設計意圖。
  寧可精確說「X 層有、Y 層斷」，不要籠統說「都沒有」。
- **通則**：subagent 回報的片面事實，要自己補全關鍵缺口再外推；強否定結論（「完全沒有」）成本最高、最該先驗。

## 後端 fix「沒生效」先查 running server 是不是舊 code（沒重啟）
- **症狀**：修了後端 bug、測試綠、push，使用者實測卻說「還是壞的 / 又變這樣」。差點繞去重查 code 與前端路徑，其實 code 是對的。
- **真因**：使用者的 uvicorn（`start.sh` 起、**無 `--reload`**）在改 code 前就啟動，running process 跑的是舊 code；改檔 / commit 不會自動載入。
- **診斷法**：`ps -eo pid,lstart,command | grep uvicorn` 取 process 啟動時間，比對 fix 的 commit 時間（`git log -1`）/ 改檔時間（`stat`）。process **早於** fix → 舊 code → 要重啟。一次比對就定位，免得瞎猜或重查。
- **通則**：使用者回報「fix 沒生效」時，**先確認 running server 已重啟**（process 啟動時間晚於改檔）再懷疑 code。dev server 建議開 `--reload` 根治此坑。
