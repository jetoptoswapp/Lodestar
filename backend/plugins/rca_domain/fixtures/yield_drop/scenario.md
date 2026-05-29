# 情境：Line-3 RX-7 良率步階下降

過去一週 Line-3（RX-7 製程）final-test 良率明顯下滑，從約 95–96% 掉到 80% 以下。
產線工程師需要快速釐清可能的根因、佐證線索，以及下一步該檢查什麼。

## Anomaly Summary
- Symptom — final-test 良率步階式下降（約 95% → 76–80%）
- When — 約 2026-05-21 之後出現
- Where — Line-3、製程 RX-7

## Known Facts
- 良率下降呈步階（step-change），非緩慢漂移
- recipe 全程為 RX-7（未換配方）
- 報廢增加、產出下降

## Data Provided
- `yield_by_lot.csv` — 依 lot 的良率紀錄，欄位：lot_id, date, tool_id, recipe, wafers, pass, yield_pct

## Open Questions
- 良率下降是否集中在特定機台 / 班別 / 物料批？
- 是否伴隨任何維修、PM、參數調整事件？

（本情境為合成資料，用於 RCA PoC 驗證；AI 提供候選根因，最終由工程師現場確認。）
