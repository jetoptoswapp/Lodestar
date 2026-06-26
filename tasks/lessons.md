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

## 「從 issues 實作」的單位必須是「逐 issue、過 QA 才換下一個」，不能整坨丟一次
- **症狀**：對 SMKB-v2（44 story issues）跑 implement → issue 跳著做、完全沒開 PR、issue 沒更新。使用者一句「行為不對」抓包。
- **真因**：`implement_start` 把整份 stories artifact 當**一個** story 字串塞進**一個** session，`run_implementation_roles` 對這整坨只跑一輪 → RD 想一次做完 44 story → 撞 1800s timeout（exit 143）→ 走不到開 PR；關 issue 只靠 PR body `Closes` 且一次塞所有 open issue（顆粒度錯）。
- **修法**：加一層 batch 編排（`async_runtime/batch.py`）逐 story 依序、一次一個 issue、做完才換下一個（roles 的 tester+reviewer 即 QA gate）；一 issue 一 branch/PR、PR 只 Closes 該 issue、開完在該 issue 留言。複用現成 parser（排序）+ roles pipeline + PrOpener，不動單 session 核心。
- **通則**：實作「從 GitHub/GitLab issues 出貨」類功能，工作單位是 **one issue at a time → QA gate → 一 issue 一 PR → 更新該 issue**（即內建 *-delivery skill 的語義）。別把整個 backlog 丟給一個 agent run；那必然跳著做、超時、開不了 PR。設計前先問清「PR/issue 顆粒度、處理順序、失敗策略」。

## 互動 affordance 要明顯：別用「點了才知道」的隱藏操作
- **症狀**：implement binding 的 role 用「點 chip 文字循環切換」(lead→rd→tester→reviewer)，只有 `title` tooltip。使用者回報「UI 不明顯，不小心點到才知道能改」。
- **真因**：cycle-on-click 是隱藏 affordance——外觀像純標籤、不像控制項；且循環式要點多次才到目標 role，N 個選項時更糟。
- **修法**：改成明確下拉選單（`BindingChip` 加 dotted underline + `▾` caret 暗示可點，展開列出該 stage 所有合法 role、當前打勾、點一下直接設定）。
- **通則**：可改的值要長得像控制項（caret / underline / select 外觀），並讓選項一次可見；循環式切換只適合 2 值且有明顯提示。新增可互動元素時先問：「不靠 tooltip，使用者看得出這裡能點、能改成什麼嗎？」

## 別在使用者 batch 進行中重啟後端 / 起第二個後端（會被 orphan-sweep 全掃成 failed）
- **症狀**：使用者跑 44-story batch，跑到一半「突然 44 全失敗」，error 全是 `interrupted by server restart`。
- **真因**：`interrupted by server restart` 只由 `impl_dal.fail_orphaned_running()` 設，而它只在**後端啟動時**（app.py lifespan）跑——把上次卡在 running/pending 的 implement session 全標 failed。我為了驗證在 port 8723 留了**第二個後端**，跟使用者 `start.sh`（開頭 `free_port 8723` 會殺佔 port 行程 + 啟動跑 orphan-sweep）相撞；任一後端(重)啟，進行中的整批就被掃光。
- **修法/通則**：(1) **絕不**為了驗證在使用者已有後端的 port 上再起一個；要驗證就用使用者那台、或起在別的 port/DB。(2) implement batch 進行中**不要**重啟後端、不要重跑 start.sh、不要跑會 `pkill uvicorn` 的指令。(3) 起背景服務前先 `pgrep -f "uvicorn app:app"` 確認沒有既有實例。(4) batch 失敗先看 error_message：若是 `interrupted by server restart` → 不是 code/agent 問題，是後端被重啟，重跑即可。

## mermaid（或任何可程式驗證的產物）「是否已修好」要用真 parser 驗，別信前一手的自報修正
- **症狀**：使用者說 arch diagram 2 還是壞的。我先讀 DB，看到前一手 AI chat 把 `->`→`to`、`<>`→`!=`，artifact updated_at 也更新了，就判定「artifact 已修好、是 wiki 沒重發」。使用者貼出炸彈截圖打臉——修正版仍 Syntax error。
- **真因**：前一手 AI 的診斷（`->`/`<>`）根本是錯的（那些在冒號後是純文字、不影響解析）；真兇是 sequence 訊息標籤裡的 `;`（mermaid 語句分隔符）。我沿用了前一手「已修正」的說法、沒實際 parse。
- **修法/通則**：(1) 凡能用工具客觀驗證的產物（mermaid / JSON / 程式碼語法），**先跑真 parser 再下結論**，別靠肉眼或前一手的自報。本案用 `frontend/node_modules/mermaid` 的 `mermaid.parse()` 一跑就指到 line 32。(2) 「updated_at 變了 / 對方說已修正」≠「真的修好」。(3) 注意 node 跑 mermaid.parse 對 flowchart/state 會炸 `DOMPurify.addHook`（缺 DOM）——那是環境假陽性，sequence 的 parse 錯誤才是真的。

## shell 腳本裡裸 `$VAR` 後緊接全形標點（中文括號/逗號）會被 `set -u` 判 unbound
- **症狀**：`start.sh` 在 `log "...主體 $DEV_USER）→..."` 那行炸 `DEV_USER� unbound variable`（變數名後跟一個亂碼字元）；空庫/非空庫兩條路都會死在這行。我上個 session 在 UTF-8 + newer bash 下沒踩到，使用者環境踩到。
- **真因**：bash 讀 `$NAME` 時用 `isalnum()`（locale 相依）判斷哪些位元組屬變數名。macOS 系統 bash 3.2，甚至 `en_US.UTF-8`、與 ISO8859-1 等 locale 下，會把全形 `）`（UTF-8 `EF BC 88`）的位元組當成字母吃進變數名 → 變成不存在的變數 → `set -u`（nounset）報 unbound。報錯字串裡 `$NAME` 後那個 `�` 就是被吃進去的標點首位元組。
- **修法**：用 `${VAR}` 大括號明確界定變數名邊界（`${DEV_USER}）`）——任何 locale/bash 版本都安全。
- **驗證法**：`for L in C en_US.ISO8859-1 en_US.UTF-8; do LC_ALL=$L bash -c 'set -u; V=x; echo "前 $V）後"'; done` 可重現；換 `${V}）` 三種 locale 全過。
- **通則**：(1) shell 腳本（尤其含中文輸出）凡 `$VAR` 後**沒有空白/引號/ASCII 標點分隔**，一律寫 `${VAR}`。(2) 自己寫的「一鍵啟動」類腳本要在乾淨/非 UTF-8 locale + 系統預設 bash 下實跑驗證，別只在自己順手的環境跑過就當完成。

## 「整個 app 看起來像假的/沒功能」要先查 auth/token，別怪前端
- **症狀**：使用者開 JetBook，每頁只剩錯誤框（`malformed access-token claims`），罵「前端根本做假的、功能都沒有」。實際上前端是真的、後端+DB 都正常。
- **真因**：我寫的 `start.sh` 鑄 dev JWT 時用 `${DEV_USER:-UUID}`，但使用者互動 shell 把 `DEV_USER` 解析成系統帳號名 `sheldon.chang`（非 UUID）。後端 `decode_access_token` 對 `sub` 做 `uuid.UUID(...)` → ValueError → 回 401 malformed → **每一個 API 都被拒** → 前端每頁退到錯誤/空狀態 → 看起來像「沒功能的假殼」。
- **修法**：start.sh 在 source .env 後、seed 前驗 `DEV_USER` 是不是 UUID（regex），非 UUID 一律回退到 seed 用的示範 admin UUID（同時保護 seed 的 `permission_grant.principal_id` 寫入）。token 的 `sub` 必須＝seed 授權的 UUID，否則就算是合法 UUID 也沒權限。
- **通則**：(1) 使用者說「做出來很爛/像假的/沒功能」時，**先驗 auth**——壞 token 會讓一個完全正常的 app 整片變空殼，跟「前端沒做」長得一模一樣。一顆正確 token 直接 curl 後端（`/dashboard` 等）回 200+真資料，就能立刻把「前端問題」和「token 問題」分開。(2) 自己寫的啟動腳本鑄的 dev token，`sub` 必須是後端能 parse 的型別（UUID）且對應 seed 的授權主體；別讓它吃環境裡同名變數（`USER`/`DEV_USER`）。(3) dev 腳本注入前端的值（`VITE_*`）改了要靠 Vite 偵測 .env 自動重啟才生效——驗證時記得確認重啟了。
