"""agent_resolver —— agent / skill 解析的單一事實源（registry seed + DB user）。

收斂兩條歷史路徑：
- collab_coordinator.resolve_agent（registry → DB fallback）
- harness_runner.get_agent_for_stage（只查 registry、遍歷第一個 role 匹配，脆弱）

解析語意：**DB 先、registry seed 後**——對齊 /api/agents、/api/skills「user 同 id 覆寫 builtin」
（list endpoint 的合併規則）。agent 解析時一併填好 skills（DB agent_skills 綁定優先，無則用
plugin 帶的 AgentSpec.skills）。

host 層模組（非 plugin）：可 import dal，與 workflow_engine / collab_coordinator 同層。
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Optional, Sequence

from plugin_api import AgentSpec, SkillSpec
from plugin_api.workflow import AgentBinding
from persistence import dal

log = logging.getLogger("agent_resolver")


# ============================================================
#  Skill 解析（registry seed + DB user，DB 先）
# ============================================================
def _row_to_skill(row: dict) -> SkillSpec:
    return SkillSpec(
        skill_id=row["skill_id"], name=row["name"],
        description=row.get("description", ""), body=row.get("body", ""),
        version=row.get("version", "1.0"),
    )


def resolve_skill(registry, skill_id: str) -> Optional[SkillSpec]:
    """依 skill_id 解析。DB user skill 先（覆寫語意），registry seed 後。找不到回 None。"""
    row = dal.get_skill(skill_id)
    if row is not None:
        return _row_to_skill(row)
    return registry.skills.get(skill_id)


def _resolve_agent_skills(registry, agent_id: str,
                          seed_skills: tuple[SkillSpec, ...]) -> tuple[SkillSpec, ...]:
    """agent 綁定的 skills：DB agent_skills 有綁定 → 依 sort_order resolve（skip 解析不到的，
    如已刪的孤兒）；無綁定 → 回 seed_skills（plugin 帶的 AgentSpec.skills）。"""
    pairs = dal.get_agent_skill_ids(agent_id)   # [(skill_id, sort_order)]，已依 sort_order 排序
    if not pairs:
        return seed_skills
    resolved = [resolve_skill(registry, sid) for sid, _ in pairs]
    return tuple(s for s in resolved if s is not None)


# ============================================================
#  Agent 解析
# ============================================================
def _row_to_spec(registry, row: dict) -> AgentSpec:
    """DB agents row → AgentSpec。skills 從 agent_skills 撈（DB agent 無 in-memory seed，故
    seed_skills fallback 為 ()）。"""
    return AgentSpec(
        agent_id=row["agent_id"], name=row["name"], role=row["role"],
        system_prompt=row.get("system_prompt", ""),
        model_choice=row.get("model_choice", "claude-cli"),
        skills=_resolve_agent_skills(registry, row["agent_id"], ()),
        tools=tuple(row.get("tools") or ()),
        max_iterations=row.get("max_iterations", 1),
        enabled=bool(row.get("enabled", True)),
    )


def resolve_agent(registry, agent_id: str) -> Optional[AgentSpec]:
    """依 agent_id 解析。DB user agent 先（覆寫語意），registry seed 後。找不到回 None。
    兩條路徑都會填好 skills（DB agent_skills 綁定優先，無則用 seed 的 AgentSpec.skills）。"""
    row = dal.get_agent(agent_id)
    if row is not None:
        return _row_to_spec(registry, row)
    seed = registry.agents.get(agent_id)
    if seed is None:
        return None
    # seed agent：user 可能對它（同 id）在 DB agent_skills 綁了 skill，卻沒覆寫 agent 本體。
    merged = _resolve_agent_skills(registry, agent_id, seed.skills)
    return seed if merged == seed.skills else replace(seed, skills=merged)


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

    保證：只回 enabled 的 agent（且已填 skills）；disabled / 找不到 → None。
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
                candidate = _row_to_spec(registry, db_rows[0])

    return candidate if (candidate and candidate.enabled) else None
