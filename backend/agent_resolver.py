"""agent_resolver —— agent 解析的單一事實源（registry seed + DB user agent）。

收斂兩條歷史路徑：
- collab_coordinator.resolve_agent（registry → DB fallback）
- harness_runner.get_agent_for_stage（只查 registry、遍歷第一個 role 匹配，脆弱）

解析語意：**DB 先、registry seed 後**——對齊 /api/agents「user 同 id 覆寫 builtin」
（list_agents_endpoint 的合併規則），讓使用者在 /agents 編輯 seed 後，單流程也吃到 DB 版。

host 層模組（非 plugin）：可 import dal，與 workflow_engine / collab_coordinator 同層。
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from plugin_api import AgentSpec
from plugin_api.workflow import AgentBinding
from persistence import dal

log = logging.getLogger("agent_resolver")


def _row_to_spec(row: dict) -> AgentSpec:
    """DB agents row → AgentSpec（skills DB 未存，預設 ()）。"""
    return AgentSpec(
        agent_id=row["agent_id"],
        name=row["name"],
        role=row["role"],
        system_prompt=row.get("system_prompt", ""),
        model_choice=row.get("model_choice", "claude-cli"),
        tools=tuple(row.get("tools") or ()),
        max_iterations=row.get("max_iterations", 1),
        enabled=bool(row.get("enabled", True)),
    )


def resolve_agent(registry, agent_id: str) -> Optional[AgentSpec]:
    """依 agent_id 解析。DB user agent 先（覆寫語意），registry seed 後。找不到回 None。"""
    row = dal.get_agent(agent_id)
    if row is not None:
        return _row_to_spec(row)
    return registry.agents.get(agent_id)


def resolve_lead_agent(
    registry, stage_id: str, *,
    default_agent_role: str = "",
    bindings: Sequence[AgentBinding] = (),
) -> Optional[AgentSpec]:
    """解析「某 stage 該由哪個 lead agent 驅動」（單流程用；collab 自有解析）。

    (a) 有 binding：取 role=="lead" 的 binding → resolve_agent。
        有 binding 但無 lead → None（此 stage 由 workflow 明確協作指定，無單一 lead）。
    (b) 無 binding：role_key = default_agent_role or stage_id：
        - registry 中 role==role_key 且 enabled 者唯一 → 回（經 resolve_agent 取 DB 覆寫版）；
          多個 → log.warning + None（不靠遍歷順序選第一個，這是舊 get_agent_for_stage 的脆弱來源）。
        - registry 無命中 → 查 DB get_agents_by_role（取第一個 enabled）。

    保證：只回 enabled 的 agent；disabled / 找不到 → None（呼叫端 fallback 到 default persona）。
    """
    bindings = tuple(bindings)
    candidate: Optional[AgentSpec] = None

    if bindings:
        leads = [b for b in bindings if b.role == "lead"]
        candidate = resolve_agent(registry, leads[0].agent_id) if leads else None
    else:
        role_key = default_agent_role or stage_id
        seed_matches = [a for a in registry.agents.values()
                        if a.role == role_key and a.enabled]
        if len(seed_matches) == 1:
            candidate = resolve_agent(registry, seed_matches[0].agent_id)
        elif len(seed_matches) > 1:
            log.warning(
                "stage '%s' 有多個 role=='%s' 的 enabled seed agent（%s）；"
                "單流程無法決定唯一 lead，請改用 workflow binding 指定。",
                stage_id, role_key, [a.agent_id for a in seed_matches],
            )
        else:
            db_rows = [r for r in dal.get_agents_by_role(role_key) if r.get("enabled", True)]
            if db_rows:
                candidate = _row_to_spec(db_rows[0])

    return candidate if (candidate and candidate.enabled) else None
