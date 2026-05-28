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
