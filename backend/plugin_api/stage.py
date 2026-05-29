from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from plugin_api.runner import HarnessRunner


@dataclass(frozen=True)
class SkillSpec:
    """可組合、可排序的 prompt 片段（沿用 ver2 SkillDef）。"""
    skill_id: str
    name: str
    description: str
    body: str
    version: str = "1.0"


@dataclass(frozen=True)
class AgentSpec:
    """一個可完整客製化的 AI agent。plugin 可帶 seed；user 可在 UI 覆蓋。"""
    agent_id: str
    name: str
    role: str                       # 綁定的 stage role（data-driven，非 frozenset）
    system_prompt: str
    model_choice: str = "claude-cli"
    skills: tuple[SkillSpec, ...] = ()
    tools: tuple[str, ...] = ()     # 允許工具（給 tool-using / 實作 agent）
    max_iterations: int = 1
    enabled: bool = True


@dataclass(frozen=True)
class StageContext:
    """host 餵給 stage handler 的唯讀輸入。handler 不碰 conn / graph / DB。"""
    thread_id: str
    stage_id: str
    model_choice: str
    instruction: str = ""                                   # refine 用
    upstream_artifacts: dict[str, str] = field(default_factory=dict)  # host 依 depends_on 備好
    current_artifact: str = ""                              # refine / chat 時非空
    conversation: tuple[tuple[str, str], ...] = ()          # (role, content)；chat/refine 用
    focus_section: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StageResult:
    """handler 回傳。host 負責寫 artifact / reset 下游 / 記 revision。"""
    artifact: str
    telemetry_metadata: dict = field(default_factory=dict)
    state_extra: dict = field(default_factory=dict)         # 額外 side-effect（如 prd is_ready）


@dataclass(frozen=True)
class StageChatResult:
    reply: str
    updated_artifact: Optional[str] = None  # chat 產生新 artifact（[CONTENT_START]..[CONTENT_END]）時填


StageGenerateFn = Callable[["StageContext", "HarnessRunner"], StageResult]
StageRefineFn = Callable[["StageContext", "HarnessRunner"], StageResult]
StageChatFn = Callable[["StageContext", "HarnessRunner"], StageChatResult]


@dataclass(frozen=True)
class StageSpec:
    """整個系統的心臟。雙詞彙（id / telemetry_stage）務必兩套都帶。"""
    id: str                         # UI/狀態詞彙：prd / architecture / stories
    label: str
    description: str = ""           # 一句話用途說明（catalog / workflow 編輯器顯示）
    icon: str = ""                  # icon 名稱字串（前端 allowlist resolve）
    telemetry_stage: str = ""       # 遙測詞彙：specify / design / deliver
    generate_operation: str = ""    # 預設 f"generate_{id}"
    refine_operation: str = ""      # 預設 f"refine_{id}"
    chat_operation: str = ""        # 預設 f"chat_{id}"
    depends_on: tuple[str, ...] = ()        # 上游 stage_id；downstream 由 host 反推
    artifact_key: str = ""          # 預設等於 id（host 用它存/取 stage_artifacts）
    prompt_keys: tuple[str, ...] = ()       # 用到的 prompt 資產檔名（見附錄 D）
    default_agent_role: str = ""    # 預設綁的 agent role（可被 workflow 覆寫）
    generate: Optional[StageGenerateFn] = None
    refine: Optional[StageRefineFn] = None
    chat: Optional[StageChatFn] = None
    supports_chat: bool = False
    on_complete_state_extra: dict = field(default_factory=dict)
