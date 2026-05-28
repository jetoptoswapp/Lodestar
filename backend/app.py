"""FastAPI app —— 通用 HTTP API（無 per-stage 特例）。

啟動時：跑 DB migration → plugin loader，把 Registry 放進 app.state。
M0 endpoint：/api/health、/api/stages（catalog，M0 空）、/api/plugins、/api/integrations。
stage 操作 / workflow / agent / SSE 隨 M1+ 增補。
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 確保 backend/ 在 sys.path（plugin_api.* / persistence.* / plugin_* 一律絕對 import）
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

import asyncio  # noqa: E402
import uuid  # noqa: E402

from fastapi import HTTPException  # noqa: E402

import plugin_loader  # noqa: E402
from api_errors import error_detail  # noqa: E402
from api_models import (  # noqa: E402
    CreateProjectRequest,
    PluginListResponse,
    PluginProvides,
    PluginResponse,
    ProjectListResponse,
    ProjectResponse,
    StageActionResponse,
    StageCatalogItem,
    StageCatalogResponse,
    StageChatRequest,
    StageChatResponse,
    StageGenerateRequest,
    StageHistoryMessage,
    StageHistoryResponse,
    StageManualEditRequest,
    StageRefineRequest,
    StageStateResponse,
    StageStatusItem,
    StageStatusesResponse,
)
from persistence import dal, migrations  # noqa: E402
from plugin_host import (  # noqa: E402
    CAP_AGENT,
    CAP_INTEGRATION,
    CAP_STAGE,
    CAP_WORKFLOW,
    Registry,
)
from workflow_engine import (  # noqa: E402
    WorkflowEngine,
    WorkflowError,
    compute_dependencies,
    downstream_of,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
log = logging.getLogger("app")

FRONTEND_PORT = 8724


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrations.migrate()
    registry = plugin_loader.load_all()
    app.state.registry = registry
    app.state.engine = WorkflowEngine(registry)
    loaded = [p.manifest.id for p in registry.loaded_plugins if p.loaded]
    log.info("startup complete — plugins loaded: %s", loaded or "(none)")
    yield


app = FastAPI(title="ai-tool-v3", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{FRONTEND_PORT}", f"http://127.0.0.1:{FRONTEND_PORT}"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _registry() -> Registry:
    return app.state.registry


def _stage_owner_map(reg: Registry) -> dict[str, str]:
    return {cid: pid for (pid, ctype, cid) in reg.contributions if ctype == CAP_STAGE}


def _build_catalog(reg: Registry) -> list[StageCatalogItem]:
    """從 registry.stages 組 catalog；downstream 由 host 反推（其他 stage 的 depends_on 含此 id）。"""
    owner = _stage_owner_map(reg)
    items: list[StageCatalogItem] = []
    for sid, spec in reg.stages.items():
        downstream = sorted(s for s, sp in reg.stages.items() if sid in sp.depends_on)
        ops: list[str] = []
        if spec.generate:
            ops.append("generate")
        if spec.refine:
            ops.append("refine")
        if spec.supports_chat:
            ops.append("chat")
        pid = owner.get(sid)
        source = "builtin" if pid in plugin_loader.BUILTIN_PLUGIN_IDS else "plugin"
        items.append(
            StageCatalogItem(
                id=spec.id,
                label=spec.label,
                icon=spec.icon,
                depends_on=list(spec.depends_on),
                downstream=downstream,
                supports_chat=spec.supports_chat,
                source=source,
                plugin_id=pid,
                operations=ops,
                telemetry_stage=spec.telemetry_stage,
            )
        )
    return items


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/stages", response_model=StageCatalogResponse)
async def get_stages():
    return StageCatalogResponse(stages=_build_catalog(_registry()))


@app.get("/api/plugins", response_model=PluginListResponse)
async def get_plugins():
    reg = _registry()
    provides_by_plugin: dict[str, PluginProvides] = {}
    for (pid, ctype, cid) in reg.contributions:
        pv = provides_by_plugin.setdefault(pid, PluginProvides())
        if ctype == CAP_STAGE:
            pv.stages.append(cid)
        elif ctype == CAP_WORKFLOW:
            pv.workflows.append(cid)
        elif ctype == CAP_AGENT:
            pv.agents.append(cid)
        elif ctype == CAP_INTEGRATION:
            pv.integrations.append(cid)

    plugins: list[PluginResponse] = []
    for plugin_info in reg.loaded_plugins:
        m = plugin_info.manifest
        plugins.append(
            PluginResponse(
                id=m.id,
                name=m.name,
                version=m.version,
                description=m.description,
                enabled=dal.plugin_enabled(m.id),
                provides=provides_by_plugin.get(m.id, PluginProvides()),
                load_error=plugin_info.error or None,
            )
        )
    return PluginListResponse(plugins=plugins)


@app.get("/api/integrations")
async def get_integrations():
    reg = _registry()
    return {
        "integrations": [
            {"target": t, "description": spec.description, "config_schema": spec.config_schema}
            for t, spec in reg.integrations.items()
        ]
    }


# ============================================================
#  Helpers（dispatch / error mapping）
# ============================================================
def _engine() -> WorkflowEngine:
    return app.state.engine


def _raise_workflow_error(exc: WorkflowError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=error_detail(exc.category, str(exc)),
    )


# ============================================================
#  Projects（thread）
# ============================================================
@app.post("/api/projects", response_model=ProjectResponse)
async def create_project(req: CreateProjectRequest):
    thread_id = uuid.uuid4().hex[:12]
    dal.create_project(thread_id, req.name, req.workflow_id)
    project = dal.get_project(thread_id)
    if project is None:  # defensive
        raise HTTPException(500, detail=error_detail("project_create_failed", "建立 thread 失敗"))
    return ProjectResponse(**project)


@app.get("/api/projects", response_model=ProjectListResponse)
async def list_projects():
    return ProjectListResponse(projects=[ProjectResponse(**p) for p in dal.list_projects()])


@app.get("/api/projects/{thread_id}", response_model=ProjectResponse)
async def get_project(thread_id: str):
    project = dal.get_project(thread_id)
    if project is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{thread_id}' 不存在"))
    return ProjectResponse(**project)


# ============================================================
#  Stage operations（generate / refine / chat / manual edit）
# ============================================================
def _dispatch_to_response(stage_id: str, out: dict) -> StageActionResponse:
    if out.get("error_code"):
        raise HTTPException(
            502 if out["error_code"].startswith("model.") else 500,
            detail=error_detail(out["error_code"], out["error_message"]),
        )
    return StageActionResponse(
        stage_id=stage_id,
        artifact=out["artifact"],
        state_extra=out["state_extra"],
        downstream_reset=out["downstream_reset"],
    )


@app.post("/api/stage/{stage_id}/generate", response_model=StageActionResponse)
async def stage_generate(stage_id: str, req: StageGenerateRequest):
    try:
        out = await asyncio.to_thread(
            _engine().dispatch,
            thread_id=req.thread_id, stage_id=stage_id, op="generate",
            model_choice=req.model_choice,
        )
    except WorkflowError as exc:
        _raise_workflow_error(exc)
    return _dispatch_to_response(stage_id, out)


@app.post("/api/stage/{stage_id}/refine", response_model=StageActionResponse)
async def stage_refine(stage_id: str, req: StageRefineRequest):
    try:
        out = await asyncio.to_thread(
            _engine().dispatch,
            thread_id=req.thread_id, stage_id=stage_id, op="refine",
            model_choice=req.model_choice, instruction=req.instruction,
        )
    except WorkflowError as exc:
        _raise_workflow_error(exc)
    return _dispatch_to_response(stage_id, out)


@app.post("/api/stage/{stage_id}/chat", response_model=StageChatResponse)
async def stage_chat(stage_id: str, req: StageChatRequest):
    try:
        out = await asyncio.to_thread(
            _engine().dispatch,
            thread_id=req.thread_id, stage_id=stage_id, op="chat",
            model_choice=req.model_choice, user_input=req.user_input,
            focus_section=req.focus_section,
        )
    except WorkflowError as exc:
        _raise_workflow_error(exc)
    if out.get("error_code"):
        raise HTTPException(
            502 if out["error_code"].startswith("model.") else 500,
            detail=error_detail(out["error_code"], out["error_message"]),
        )
    return StageChatResponse(
        ai_response=out["reply"] or "",
        updated_content=out["artifact"] or None,
    )


@app.put("/api/stage/{stage_id}/{thread_id}", response_model=StageActionResponse)
async def stage_manual_edit(stage_id: str, thread_id: str, req: StageManualEditRequest):
    reg: Registry = _registry()
    if stage_id not in reg.stages:
        raise HTTPException(404, detail=error_detail("stage_not_found", f"stage '{stage_id}' 未註冊"))
    engine = _engine()
    workflow = engine.active_workflow_for(thread_id)
    if stage_id not in workflow.stages:
        raise HTTPException(
            400,
            detail=error_detail("stage_not_in_workflow",
                                f"stage '{stage_id}' 不在 workflow '{workflow.id}'"),
        )
    current = dal.get_artifact(thread_id, stage_id) or ""
    if req.content == current:
        return StageActionResponse(stage_id=stage_id, artifact=current, state_extra={}, downstream_reset=[])
    dal.upsert_artifact(thread_id, stage_id, req.content)
    deps = compute_dependencies(workflow, reg.stages)
    downs = downstream_of(stage_id, deps)
    for d in downs:
        if dal.get_stage_status(thread_id, d) == "approved":
            dal.set_stage_status(thread_id, d, "needs_revision")
    dal.add_revision(
        thread_id, stage_id, source=req.change_source,
        instruction=req.instruction, summary=req.change_context,
        downstream_reset=downs, content_length=len(req.content),
        reviewed=req.reviewed,
    )
    if dal.get_stage_status(thread_id, stage_id) != "approved":
        dal.set_stage_status(thread_id, stage_id, "draft")
    dal.append_event(thread_id, stage_id, event_type="manual_edit", detail="")
    return StageActionResponse(
        stage_id=stage_id, artifact=req.content,
        state_extra={}, downstream_reset=downs,
    )


# ============================================================
#  Stage state / status / history
#  註：含 literal segment 的 endpoint（statuses、history）必須註冊在 generic
#  /api/stage/{stage_id}/{thread_id} 之前，否則被 generic match 吃掉。
# ============================================================
@app.get("/api/stage/statuses/{thread_id}", response_model=StageStatusesResponse)
async def stage_statuses(thread_id: str):
    engine = _engine()
    workflow = engine.active_workflow_for(thread_id)
    persisted = dal.list_stage_statuses(thread_id)
    return StageStatusesResponse(statuses=[
        StageStatusItem(stage_id=sid, status=persisted.get(sid, "draft"))
        for sid in workflow.stages
    ])


@app.get("/api/stage/{stage_id}/history/{thread_id}", response_model=StageHistoryResponse)
async def stage_history(stage_id: str, thread_id: str):
    msgs = dal.list_messages(thread_id, stage_id, limit=500)
    return StageHistoryResponse(messages=[
        StageHistoryMessage(role=m["role"], content=m["content"], created_at=m["created_at"])
        for m in msgs
    ])


@app.get("/api/stage/{stage_id}/{thread_id}", response_model=StageStateResponse)
async def stage_state(stage_id: str, thread_id: str):
    artifact = dal.get_artifact(thread_id, stage_id) or ""
    status = dal.get_stage_status(thread_id, stage_id)
    meta = dal.get_artifact_meta(thread_id, stage_id) or {}
    return StageStateResponse(
        stage_id=stage_id, status=status, artifact=artifact,
        has_content=bool(artifact.strip()),
        last_updated_at=meta.get("updated_at"),
    )
