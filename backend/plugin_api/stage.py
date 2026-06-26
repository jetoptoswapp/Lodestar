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
    """一個可完整客製化的 AI agent。plugin 可帶 seed；user 可在 UI 覆蓋。

    詞彙注意：本類 `role` = 綁定的 **stage id**（prd / architecture / …），與
    workflow.AgentBinding.role（協作角色 lead / peer / subagent）同名但語意不同。
    未來若正名，方向為 AgentSpec.stage_role / AgentBinding.collab_role。
    """
    agent_id: str
    name: str
    role: str                       # 綁定的 stage id（data-driven）；≠ AgentBinding.role（協作角色）
    system_prompt: str              # 單流程 persona（空 → 用 stage 內建 default persona）；機器契約在 .md
    model_choice: str = "claude-cli"
    skills: tuple[SkillSpec, ...] = ()   # 未接線：SkillSpec/DB 表(skills+agent_skills)已在，但缺 register_skill/DAL/API/執行
    tools: tuple[str, ...] = ()     # 允許工具（如 "Read"）；經 HarnessRunner._allowed_tools 流到 adapter
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
    # host dispatch 解析的「該 stage lead agent」（單流程的 persona / model 來源）。
    # collab 路徑會刻意設 None（lead 合成用 stage 的 default persona，見 collab_coordinator）。
    agent: Optional["AgentSpec"] = None
    # host 備好的既有 repo clone 絕對路徑（唯讀輸入；stage 宣告 requires=("workspace",) 時非空）。
    # handler 不碰 FS/git，只把它寫進 prompt 告訴 model 去 Read/Grep/Glob。語意同附件 path-passing。
    workspace_dir: str = ""


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
    depends_on: tuple[str, ...] = ()        # 硬上游 stage_id；缺則 host 直接 4xx（MissingDependency）。downstream 由 host 反推
    soft_depends_on: tuple[str, ...] = ()   # 軟上游：artifact 存在才注入 upstream_artifacts，缺不擋（不參與 gating / 拓樸）。e.g. architecture 想看 ui_design 但純後端專案可無
    requires: tuple[str, ...] = ()  # 宣告需要的 host 資源（如 "workspace"=既有 repo clone）；core 讀此決定備料，不認 stage 名
    artifact_key: str = ""          # 預設等於 id（host 用它存/取 stage_artifacts）
    prompt_keys: tuple[str, ...] = ()       # 用到的 prompt 資產檔名（見附錄 D）
    default_agent_role: str = ""    # 預設綁的 agent role（可被 workflow 覆寫）
    generate: Optional[StageGenerateFn] = None
    refine: Optional[StageRefineFn] = None
    chat: Optional[StageChatFn] = None
    supports_chat: bool = False
    on_complete_state_extra: dict = field(default_factory=dict)
