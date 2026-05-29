# 情境：ETCH 機台製程參數疑似漂移

Line-3（RX-7 製程）在近期良率窗內出現異常，懷疑某一台 etch 機台的製程參數逐漸偏離設定值（drift）。
產線工程師需要從機台參數時序資料中釐清是哪一台機台、哪一項參數出現偏移，以及偏移從何時開始。

## Anomaly Summary
- Symptom — 良率窗內懷疑某機台製程參數逐漸偏離 spec（緩慢漂移而非單點跳動）
- When — 約 2026-05-21 之後開始
- Where — Line-3、製程 RX-7、etch 區（ETCH-01 / ETCH-02 / ETCH-03）

## Known Facts
- 異常表現為參數逐步漂移（drift），非單一突跳
- recipe 全程為 RX-7（未換配方）
- 三台機台跑相同 recipe，理論上參數應一致
- 製程參數規格（SPEC）：
  - chamber_pressure：80 ± 5 mtorr（上限 85 / 下限 75）
  - rf_power：1500 ± 30 W
  - temp：65 ± 2 C
  - gas_flow：120 ± 5 sccm

## Data Provided
- `tool_params_timeseries.csv` — 三台機台的製程參數時序紀錄（2026-05-18 → 05-25，約每 6 小時一筆），欄位：timestamp, tool_id, chamber_pressure_mtorr, rf_power_w, temp_c, gas_flow_sccm

## Open Questions
- 偏移集中在哪一台機台？哪一項參數超出 spec？
- 偏移從哪個時間點開始，趨勢是漸進還是步階？
- 是否伴隨任何維修、PM、MFC / 幫浦相關事件？

（本情境為合成資料，用於 RCA PoC 驗證；AI 提供候選根因，最終由工程師現場確認。）
