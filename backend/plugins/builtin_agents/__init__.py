"""builtin_agents plugin：PRD / Architect / PM 三個 seed AgentSpec。

M2：先 seed 給 stage 用（HarnessRunner.get_agent_for_stage 依 role 對應）。
M3 起，使用者可在 /agents UI 編輯／新建 agent 覆蓋 seed（user-defined 優先）。
"""
