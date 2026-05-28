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


class PluginListResponse(BaseModel):
    plugins: list[PluginResponse]


class PluginToggleRequest(BaseModel):
    enabled: bool


# ============================================================
#  Projects / threads（M1）
# ============================================================
class CreateProjectRequest(BaseModel):
    name: str
    workflow_id: Optional[str] = None   # None → lazy default


class UpdateProjectRequest(BaseModel):
    """PATCH /api/projects/{tid} —— 改 name（之後可加 workflow_id 等）。"""
    name: Optional[str] = None


class ProjectResponse(BaseModel):
    thread_id: str
    name: str
    workflow_id: Optional[str] = None
    created_at: float


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


class StageActionResponse(BaseModel):
    stage_id: str
    artifact: str
    state_extra: dict = {}              # 如 prd 的 {"is_ready": true}
    downstream_reset: list[str] = []    # 被連帶 reset 的下游 stage_id


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
class StageStateResponse(BaseModel):
    """thread × stage 的當前完整狀態（artifact + status + meta）。"""
    stage_id: str
    status: str
    artifact: str
    has_content: bool
    last_updated_at: Optional[float] = None


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
