# Lessons（從使用者修正中累積，避免重蹈覆轍）

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
