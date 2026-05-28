from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    label: str
    description: str = ""
    stages: tuple[str, ...] = ()                    # 有序 stage_id 序列
    edges_override: dict[str, tuple[str, ...]] = field(default_factory=dict)
    agent_bindings: dict[str, str] = field(default_factory=dict)  # stage_id -> agent_id
    source_plugin: str = ""
