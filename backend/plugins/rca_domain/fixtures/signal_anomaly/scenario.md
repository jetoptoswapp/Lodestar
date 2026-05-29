# 情境：ETCH-03 製程訊號間歇異常

Line-3（RX-7 製程）ETCH-03 機台的製程訊號在近期出現間歇性異常，
產線工程師需要判斷這是 sensor / 量測端故障，還是真實的製程問題，並釐清是哪一個訊號、何時開始。

## Anomaly Summary
- Symptom — 製程 trace 出現間歇性 endpoint 訊號異常（時好時壞，非持續性偏移）
- When — 約 2026-05-21 之後開始零星出現
- Where — Line-3、製程 RX-7、ETCH-03（以 ETCH-01 作為對照基準）

## Known Facts
- 異常為間歇性（intermittent）：多數樣本正常，僅零星樣本出現掉點或尖峰
- recipe 全程為 RX-7（未換配方）
- 同窗內 ETCH-01 作為對照組，訊號穩定
- 正常 endpoint_intensity 約為 1.00（穩定、波動很小）

## Data Provided
- `process_trace.csv` — 製程訊號 trace 紀錄（2026-05-20 → 05-25），欄位：timestamp, tool_id, sensor, value
  - 涵蓋感測訊號：endpoint_intensity、reflected_power、He_backside_flow
  - 主要為 ETCH-03，並含 ETCH-01 對照基準

## Open Questions
- 間歇異常集中在哪一個 sensor / 哪一台機台？
- 異常是持續性還是間歇性？是否與特定時段相關？
- 屬於量測端（sensor / 光學窗口）問題，還是真實製程偏移？其他訊號是否同步異常？

（本情境為合成資料，用於 RCA PoC 驗證；AI 提供候選根因，最終由工程師現場確認。）
