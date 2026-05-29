"""example_notes —— 示範「第三方 stage plugin」最小完整範例。

證明：只 import plugin_api、帶自己的 prompts/、丟進 plugins/ 目錄即被 loader 發現。
這個 plugin 預設啟用，但不在 default workflow 內，所以不影響主流程；
使用者可在 /plugins 停用它，或在 /workflows 把 notes 加進自訂 workflow。
"""
