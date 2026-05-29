from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Union


# ============================================================
#  Agent binding（spec §6.4 extension：1 stage 可綁多個 agent）
# ============================================================
CollabRole = Literal["lead", "peer", "subagent"]
CollabMode = Literal["single", "discussion", "dispatch"]


@dataclass(frozen=True)
class AgentBinding:
    """一個 agent 對 stage 的綁定關係。

    - lead     ：主導 stage、最後合成 artifact。每 stage 至少要有 1 個 lead。
    - peer     ：平行 agent，跟 lead 一起在 chat 內討論。（PRD 加 Sales/PM 就是這型）
    - subagent ：被 lead 分派任務的下手（M5 前端 / 後端 engineer 屬這型）。
    """
    agent_id: str
    role: CollabRole = "lead"


# Backward-compat 用：caller 可給 raw str 或 AgentBinding；normalize_bindings 處理。
RawBinding = Union[str, AgentBinding, dict]


def normalize_bindings(raw: object) -> tuple[AgentBinding, ...]:
    """把多種 raw 形式統一成 tuple[AgentBinding, ...]。

    接受：
    - str（agent_id）         → AgentBinding(agent_id, "lead")
    - AgentBinding             → 原樣
    - dict {agent_id, role?}   → AgentBinding(...)
    - list / tuple of 任一形式 → 全部 normalize
    - None / 空                → ()
    """
    if raw is None:
        return ()
    # 單一 element
    if isinstance(raw, (str, AgentBinding, dict)):
        raw_list: list = [raw]
    elif isinstance(raw, (list, tuple)):
        raw_list = list(raw)
    else:
        return ()

    out: list[AgentBinding] = []
    for item in raw_list:
        if isinstance(item, AgentBinding):
            out.append(item)
        elif isinstance(item, str):
            out.append(AgentBinding(agent_id=item, role="lead"))
        elif isinstance(item, dict):
            aid = item.get("agent_id")
            if not aid:
                continue
            role = item.get("role", "lead")
            if role not in ("lead", "peer", "subagent"):
                role = "lead"
            out.append(AgentBinding(agent_id=aid, role=role))
    return tuple(out)


# ============================================================
#  WorkflowSpec（spec §6.4 with multi-binding extension）
# ============================================================
@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    label: str
    description: str = ""
    stages: tuple[str, ...] = ()                    # 有序 stage_id 序列
    edges_override: dict[str, tuple[str, ...]] = field(default_factory=dict)

    # M3：agent_bindings 從 1:1 升級成 1:N + role（spec §6.4 extension）。
    # 接 dict[stage_id, tuple[AgentBinding, ...]]。
    # Backward-compat：載入 DB / plugin 時 caller 用 normalize_bindings()
    # 把舊單字串轉成 tuple[AgentBinding]。
    agent_bindings: dict[str, tuple[AgentBinding, ...]] = field(default_factory=dict)

    # 每個 stage 的協作模式（spec §6.4）：
    #   single     — 1 個 lead 自己跑（預設）
    #   discussion — 多 peer + 1 lead 輪流發言、user 在 chat 參與、lead 合成 artifact
    #   dispatch   — 1 lead 拆任務分派多 subagent，subagent 並行 worker、lead 合併
    collab_mode: dict[str, CollabMode] = field(default_factory=dict)

    source_plugin: str = ""
