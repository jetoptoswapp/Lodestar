"""builtin_agents：註冊 seed AgentSpec（PRD / Architect / PM lead + PRD 討論 peer）。

`role` 對齊 builtin_core_stages 的 stage id（prd / architecture / stories）——
agent_resolver / HarnessRunner.get_agent_for_stage 以 role==stage_id 解析「該 stage 的 lead」。

lead 的 `system_prompt` **留空（""）**：完整人設（persona）由各 stage handler 以 stage 內建
default 注入（builtin_core_stages 的 _DEFAULT_*_PERSONA），機器契約（questionnaire / PRD
Format / heading shape / [PRD_READY] sentinel 等）留在 prompts/*.md。空 system_prompt 的
語意 = 「用 stage 內建預設人設」；generate 與 chat 各自 fallback 到自己的 default（兩者
原本 persona 不同，留空才能各自精準保留、不互相污染）。使用者在 /agents 填入非空 system_prompt
即覆寫該 stage 單流程的 persona（generate / chat 都會反映），機器契約不受影響。

peer（PRD 討論 panel）保留各自 system_prompt：collab discussion 的 peer 發言會用到。
"""
from __future__ import annotations

from plugin_api import AgentSpec, PluginHost, SkillSpec


# PRD 討論 panel 的 peer 視角（requirements_panel workflow 用）——與 lead SA 互補。
_PRD_PM_PEER_PROMPT = (
    "你是資深產品經理（PM）。從商業與產品視角檢視這份需求：目標客群與痛點是否清楚、"
    "優先級與範圍是否合理、有沒有遺漏的關鍵使用情境或競品考量、成功指標（KPI）為何。"
    "給出具體建議與你認為 PRD 必須補強的點。"
)
_PRD_SEC_PEER_PROMPT = (
    "你是資深資安與合規顧問。從安全、隱私、法規（如 GDPR / PCI）與風險視角檢視這份需求："
    "登入與權限、個資處理、稽核、攻擊面、合規義務有沒有被涵蓋。"
    "點出風險與 PRD 必須補上的非功能性需求（NFR）。"
)


def register(host: PluginHost) -> None:
    # 示範 skill（可組合、可排序的 prompt 片段）：使用者在 /skills 編輯/新增、到 /agents 綁定。
    # seed agent 預設不綁（skills=()），確保「未綁 = prompt 不變」。
    host.register_skill(SkillSpec(
        skill_id="seed_skill_concise", name="Concise Output",
        description="輸出精簡、去除贅詞",
        body="Be concise. Prefer short sentences. Cut filler words and redundant qualifiers.",
    ))
    host.register_skill(SkillSpec(
        skill_id="seed_skill_tw", name="Traditional Chinese",
        description="優先以繁體中文回應",
        body="When the user writes in Chinese, always respond in Traditional Chinese (繁體中文).",
    ))
    # lead agents：system_prompt 留空 → 單流程用 stage 內建 default persona（見 *_stage.py），
    # 使用者在 /agents 填入後覆寫。
    host.register_agent(AgentSpec(
        agent_id="seed_prd",
        name="SA Agent (PRD)",
        role="prd",
        system_prompt="",
        model_choice="claude-cli",
        skills=(),
        tools=(),
        max_iterations=1,
        enabled=True,
    ))
    host.register_agent(AgentSpec(
        agent_id="seed_architect",
        name="Architect Agent",
        role="architecture",
        system_prompt="",
        model_choice="claude-cli",
        skills=(),
        tools=(),
        max_iterations=1,
        enabled=True,
    ))
    host.register_agent(AgentSpec(
        agent_id="seed_pm",
        name="PM Agent (Stories)",
        role="stories",
        system_prompt="",
        model_choice="claude-cli",
        skills=(),
        tools=(),
        max_iterations=1,
        enabled=True,
    ))
    # change_request lead（修改既有專案 workflow）：讀既有 codebase → 產出實作 brief。
    # tools 顯式宣告 Read/Grep/Glob（adapter 在 workspace 存在時亦會自動補，此處讓意圖明確）。
    # system_prompt 留空 → 用 change_request stage 內建 default persona。
    host.register_agent(AgentSpec(
        agent_id="change_planner",
        name="Change Planner",
        role="change_request",
        system_prompt="",
        model_choice="claude-cli",
        skills=(),
        tools=("Read", "Grep", "Glob"),
        max_iterations=1,
        enabled=True,
    ))
    # PRD 討論 panel 的 peer agents（與 seed_prd lead 互補；requirements_panel workflow 綁定）。
    # role 用 "prd_peer" 而非 "prd"：避免污染「role==stage_id 唯一 lead」的解析（見 agent_resolver）。
    host.register_agent(AgentSpec(
        agent_id="seed_prd_pm",
        name="PM Perspective (PRD)",
        role="prd_peer",
        system_prompt=_PRD_PM_PEER_PROMPT,
        model_choice="claude-cli",
        skills=(),
        tools=(),
        max_iterations=1,
        enabled=True,
    ))
    host.register_agent(AgentSpec(
        agent_id="seed_prd_security",
        name="Security & Compliance (PRD)",
        role="prd_peer",
        system_prompt=_PRD_SEC_PEER_PROMPT,
        model_choice="claude-cli",
        skills=(),
        tools=(),
        max_iterations=1,
        enabled=True,
    ))
