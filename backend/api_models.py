"""API request/response schema（pydantic）。通用 endpoint，無 per-stage 特例
（移除硬編碼的 prd/architecture/stories 欄位，改 stage_id 參數化 + list 回應）。

M0 落地 catalog + plugin schema；stage 操作 / status / workflow / agent 的 schema
隨 M1 / M3 增補（見 spec 附錄 B）。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ---- Stage catalog（GET /api/stages）----
class StageCatalogItem(BaseModel):
    id: str
    label: str
    icon: str
    description: str = ""
    depends_on: list[str]
    downstream: list[str]
    supports_chat: bool
    source: str                     # "builtin" / "plugin"
    plugin_id: Optional[str] = None
    operations: list[str]           # ["generate","refine","chat"]
    telemetry_stage: str = ""


class StageCatalogResponse(BaseModel):
    stages: list[StageCatalogItem]


# ---- Plugin 管理（GET /api/plugins、PATCH /api/plugins/{id}）----
class PluginProvides(BaseModel):
    stages: list[str] = []
    workflows: list[str] = []
    agents: list[str] = []
    integrations: list[str] = []


class PluginResponse(BaseModel):
    id: str
    name: str
    version: str
    description: str
    enabled: bool
    provides: PluginProvides
    requires_rebuild: bool = False      # plugin 帶前端 renderer 時 true
    load_error: Optional[str] = None    # 載入失敗原因（host_api 不符 / import 例外）
    builtin: bool = False               # M4：內建 plugin（不可 disable）
    discovery: str = "directory"        # M4：directory / entry_point


class PluginListResponse(BaseModel):
    plugins: list[PluginResponse]


class PluginToggleRequest(BaseModel):
    enabled: bool


# ============================================================
#  ModelAdapter（GET /api/models）—— TopBar selector / 各 stage 操作的 model_choice
# ============================================================
class ModelAdapterResponse(BaseModel):
    model_choice: str
    description: str
    is_available: bool
    supports_multimodal: bool = False
    max_context_tokens: int = 0
    prompt_budget_tokens: int = 0
    response_budget_tokens: int = 0
    source_plugin: Optional[str] = None


class ModelAdapterListResponse(BaseModel):
    models: list[ModelAdapterResponse]


# ============================================================
#  Projects / threads（M1）
# ============================================================
class CreateProjectRequest(BaseModel):
    name: str
    workflow_id: Optional[str] = None   # None → lazy default
    # delivery repo（可選；建專案時設，亦可事後 PATCH）
    delivery_target: str = ""           # github / gitlab / ''
    repo_mode: str = ""                 # new / existing / ''
    repo_full_name: str = ""            # 既有：owner/repo
    repo_owner: str = ""                # 開新的 org/group（空=個人）
    repo_visibility: str = "private"    # public / private / internal


class UpdateProjectRequest(BaseModel):
    """PATCH /api/projects/{tid} —— 改 name 與/或 delivery repo 設定（皆 optional）。"""
    name: Optional[str] = None
    delivery_target: Optional[str] = None
    repo_mode: Optional[str] = None
    repo_full_name: Optional[str] = None
    repo_owner: Optional[str] = None
    repo_visibility: Optional[str] = None


class ProjectResponse(BaseModel):
    thread_id: str
    name: str
    workflow_id: Optional[str] = None
    created_at: float
    delivery_target: str = ""
    repo_mode: str = ""
    repo_full_name: str = ""
    repo_owner: str = ""
    repo_visibility: str = "private"
    repo_created: bool = False


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]


# ============================================================
#  Stage operations（spec 附錄 B；對應 POST/PUT /api/stage/*）
# ============================================================
class StageGenerateRequest(BaseModel):
    thread_id: str
    model_choice: str = "claude-cli"


class StageRefineRequest(BaseModel):
    thread_id: str
    model_choice: str = "claude-cli"
    instruction: str
    preview_only: bool = False


class StageChatRequest(BaseModel):
    thread_id: str
    user_input: str
    model_choice: str = "claude-cli"
    preview_only: bool = False
    focus_section: Optional[str] = None


class StageManualEditRequest(BaseModel):
    content: str
    change_source: str = "manual_edit"
    reviewed: bool = False
    instruction: str = ""
    change_context: str = ""


class ValidationOutcomeResponse(BaseModel):
    validator: str
    severity: str                       # warn / fail
    message: str = ""
    fix_hint: Optional[str] = None
    detail: dict = {}


class StageActionResponse(BaseModel):
    stage_id: str
    artifact: str
    state_extra: dict = {}              # 如 prd 的 {"is_ready": true}
    downstream_reset: list[str] = []    # 被連帶 reset 的下游 stage_id
    validations: list[ValidationOutcomeResponse] = []   # 結構/語義驗證結果（含 judge）
    needs_revision: bool = False        # 有 fail 級未解 → 前端提示「需修正」


class StageChatResponse(BaseModel):
    ai_response: str
    updated_content: Optional[str] = None


class StageHistoryMessage(BaseModel):
    role: str
    content: str
    created_at: Optional[float] = None


class StageHistoryResponse(BaseModel):
    messages: list[StageHistoryMessage]


# ============================================================
#  Stage state / status / summary（讀取）
# ============================================================
class DeliveryStatus(BaseModel):
    """最後一次發佈到 tracker 的結果（給 UI 顯示「已發佈」狀態）。"""
    target: str
    repo: str = ""
    count: int = 0
    created: int = 0
    published_at: Optional[float] = None


class StageStateResponse(BaseModel):
    """thread × stage 的當前完整狀態（artifact + status + meta）。"""
    stage_id: str
    status: str
    artifact: str
    has_content: bool
    last_updated_at: Optional[float] = None
    delivery: Optional[DeliveryStatus] = None    # 僅 stories：最後一次成功發佈結果


class StageStatusItem(BaseModel):
    stage_id: str
    status: str                         # draft / approved / needs_revision


class StageStatusesResponse(BaseModel):
    statuses: list[StageStatusItem]     # 依 active workflow 順序


class SetStageStatusRequest(BaseModel):
    status: str


# ============================================================
#  Stage attachments（M1.1：上傳檔案 inline 進 SA prompt）
# ============================================================
class AttachmentResponse(BaseModel):
    file_id: str
    filename: str
    mime: str = ""
    size_bytes: int = 0
    has_parsed_text: bool = False
    parse_error: Optional[str] = None
    created_at: Optional[float] = None


class AttachmentListResponse(BaseModel):
    attachments: list[AttachmentResponse]


# ============================================================
#  Delivery publish（M2.5）—— stories → DeliveryItem → IntegrationSpec.publish
# ============================================================
# ============================================================
#  Workflow / Agent CRUD（M3）
# ============================================================
class AgentBindingPayload(BaseModel):
    agent_id: str
    role: str = "lead"          # lead / peer / subagent


class WorkflowStagePayload(BaseModel):
    """workflow_definitions.stages_json 內每個 stage entry。

    spec §6.4 extension：agent_bindings 1:N + collab_mode。
    """
    stage_id: str
    depends_on: list[str] = []
    agent_bindings: list[AgentBindingPayload] = []
    collab_mode: str = "single"      # single / discussion / dispatch


class WorkflowUpsertRequest(BaseModel):
    id: str                          # workflow id（user 自訂，e.g. "checkout-flow"）
    label: str
    description: str = ""
    stages: list[WorkflowStagePayload]


class WorkflowResponse(BaseModel):
    id: str
    label: str
    description: str = ""
    stages: list[dict]               # 直接吐 stages JSON（含 agent_bindings + collab_mode）
    source: str                      # "builtin" / "user"
    source_plugin: Optional[str] = None
    created_at: Optional[float] = None


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowResponse]


class SkillResponse(BaseModel):
    skill_id: str
    name: str
    description: str = ""
    body: str = ""
    version: str = "1.0"


class SkillListResponse(BaseModel):
    skills: list[SkillResponse]


class SkillUpsertRequest(BaseModel):
    skill_id: str
    name: str
    description: str = ""
    body: str = ""
    version: str = "1.0"


class AgentSkillsUpdateRequest(BaseModel):
    skill_ids: list[str]             # 陣列順序即 sort_order


class AgentUpsertRequest(BaseModel):
    agent_id: str
    name: str
    role: str                        # stage_id（prd / architecture / stories / 自訂）
    system_prompt: str = ""
    model_choice: str = "claude-cli"
    max_iterations: int = 1
    enabled: bool = True
    tools: list[str] = []


class AgentResponse(BaseModel):
    agent_id: str
    name: str
    role: str
    system_prompt: str = ""
    model_choice: str = "claude-cli"
    max_iterations: int = 1
    enabled: bool = True
    tools: list[str] = []
    skills: list[SkillResponse] = []
    source: str                      # "builtin" / "user"
    created_at: Optional[float] = None
    updated_at: Optional[float] = None


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]


class SetProjectWorkflowRequest(BaseModel):
    workflow_id: Optional[str] = None    # None → 解除綁定 → lazy default


# ============================================================
class DeliveryItemPreview(BaseModel):
    """來自 IntegrationSpec.preview() 的一筆預覽。"""
    target: str
    destination: str
    title: str
    labels: list[str]
    estimate: int
    group: str
    body_preview: str


class DeliveryPreviewResponse(BaseModel):
    target: str
    config: dict
    item_count: int
    items: list[DeliveryItemPreview]


class DeliveryPublishRequest(BaseModel):
    target: str             # github / jira / gitlab / 其他 registered integration
    config: dict            # 對應 IntegrationSpec.config_schema 的 fields


class DeliveryPublishResponse(BaseModel):
    success: bool
    target: str
    count: int
    created: list[str]      # 已建立的 issue / ticket URL


# ---- Runners（async runner 清單，M5）----
class RunnerInfo(BaseModel):
    choice: str
    available: bool
    source_plugin: Optional[str] = None


class RunnerListResponse(BaseModel):
    runners: list[RunnerInfo]


# ---- Implement（async 實作 agent，M5）----
class ImplementStartRequest(BaseModel):
    thread_id: str
    runner: str = "mock"            # registry 內的 runner choice（mock / claude-cli）
    target_repo: str = ""           # owner/repo（mock 階段為示意值）
    story: str = ""                 # 留空則讀該 thread 的 stories artifact
    title: str = ""
    mode: str = "single"            # single = fix-loop；roles = lead→RD→tester→reviewer pipeline
    auto_approve: Optional[bool] = None  # None=依 runner（claude-cli→需審批、mock→直接）；可顯式覆寫


class ImplementStartResponse(BaseModel):
    session_id: int


class ImplementRunInfo(BaseModel):
    run_id: int
    attempt: int
    runner: str
    status: str                     # running/succeeded/failed/cancelled/timed_out/rejected
    dispatch_role: str = ""         # 多角色模式：lead / rd / tester / reviewer（單一模式為空）
    exit_code: Optional[int] = None
    cancelled: bool = False
    timed_out: bool = False
    parent_run_id: Optional[int] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None


class ImplementSessionResponse(BaseModel):
    session_id: int
    thread_id: str
    stage: str
    title: str
    target_repo: str
    runner: str
    status: str                     # pending/running/succeeded/failed/cancelled
    pr_url: str = ""
    error_message: str = ""
    batch_id: Optional[int] = None
    issue_number: Optional[int] = None
    story_key: str = ""
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    runs: list[ImplementRunInfo] = []


class ImplementSessionListResponse(BaseModel):
    sessions: list[ImplementSessionResponse]


# ---- Implement batch（逐 issue 依序實作）----
class ImplementBatchStartRequest(BaseModel):
    thread_id: str
    runner: str = "mock"
    target_repo: str = ""           # 留空 → 由專案 delivery 設定 resolve
    mode: str = "roles"             # batch 預設 roles（tester+reviewer 即 QA gate）
    stop_on_failure: bool = False   # False=continue-on-failure（預設）
    auto_merge: bool = False        # True=過 gate 即依序 merge PR（策略 A，後者吃得到前者）；僅 github 真跑生效


class ImplementBatchItem(BaseModel):
    session_id: int
    story_key: str = ""
    title: str = ""
    issue_number: Optional[int] = None
    status: str = "pending"
    pr_url: str = ""


class ImplementBatchStartResponse(BaseModel):
    batch_id: int
    total: int
    skipped: int = 0                 # 冪等：跳過幾個已完成/進行中的 story
    items: list[ImplementBatchItem]


class ImplementBatchResponse(BaseModel):
    batch_id: int
    thread_id: str
    target_repo: str = ""
    runner: str = ""
    mode: str = ""
    total: int
    status: str                     # running/succeeded/failed/cancelled/partial
    stop_on_failure: bool = False
    error_message: str = ""
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    items: list[ImplementBatchItem] = []


class ImplementBatchListResponse(BaseModel):
    batches: list[ImplementBatchResponse]


class ImplementBatchCancelResponse(BaseModel):
    batch_id: int
    cancel_requested: bool


class ImplementCancelResponse(BaseModel):
    session_id: int
    cancel_requested: bool          # 是否有 active runner 被要求取消（已結束則 False）


class ImplementLogLine(BaseModel):
    id: int                         # 全域單調游標
    run_id: int
    attempt: int
    kind: str                       # log / event / system
    content: str


class ImplementLogResponse(BaseModel):
    session_id: int
    status: str
    next_cursor: int                # 下次 poll 帶回的 after_id
    lines: list[ImplementLogLine]
    runs: list[ImplementRunInfo] = []
