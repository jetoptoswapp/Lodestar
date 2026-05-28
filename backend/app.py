"""FastAPI app —— 通用 HTTP API（無 per-stage 特例）。

啟動時：跑 DB migration → plugin loader，把 Registry 放進 app.state。
M0 endpoint：/api/health、/api/stages（catalog，M0 空）、/api/plugins、/api/integrations。
stage 操作 / workflow / agent / SSE 隨 M1+ 增補。
"""
from __future__ import annotations

import logging
import os
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
import time  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from fastapi import File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

import plugin_loader  # noqa: E402
from api_errors import error_detail  # noqa: E402
from api_models import (  # noqa: E402
    AttachmentListResponse,
    AttachmentResponse,
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
from parsers import parse as parse_attachment  # noqa: E402
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
    # M1.3：把 uploads 根目錄暴露給 claude-cli adapter（--add-dir + --allowedTools Read）。
    # adapter 在 plugin_loader.load_all 之前 import，但實際 invoke 才讀 env，所以這裡先設安全。
    uploads_root = dal.uploads_dir()
    uploads_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LODESTAR_UPLOADS_DIR", str(uploads_root))
    registry = plugin_loader.load_all()
    app.state.registry = registry
    app.state.engine = WorkflowEngine(registry)
    loaded = [p.manifest.id for p in registry.loaded_plugins if p.loaded]
    log.info("startup complete — plugins loaded: %s", loaded or "(none)")
    log.info("LODESTAR_UPLOADS_DIR=%s", os.environ["LODESTAR_UPLOADS_DIR"])
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


# ============================================================
#  Stage attachments（M1.1：上傳檔案 inline 進 SA prompt）
# ============================================================
def _attachment_to_response(row: dict) -> AttachmentResponse:
    return AttachmentResponse(
        file_id=row["file_id"],
        filename=row["filename"],
        mime=row["mime"] or "",
        size_bytes=int(row["size_bytes"] or 0),
        has_parsed_text=bool(row.get("parsed_text")),
        parse_error=row.get("parse_error") or None,
        created_at=row.get("created_at"),
    )


def _ensure_thread(thread_id: str) -> None:
    if dal.get_project(thread_id) is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{thread_id}' 不存在"))


# 注：含 literal `/attachments` 的 route 要在 generic /api/stage/{sid}/{tid} 之前？
# 不會衝突——這些是 depth-4+ 的 path，FastAPI 依 segment count 區分。
@app.post(
    "/api/stage/{stage_id}/{thread_id}/attachments",
    response_model=AttachmentResponse,
)
async def upload_attachment(stage_id: str, thread_id: str, file: UploadFile = File(...)):
    _ensure_thread(thread_id)
    file_id = uuid.uuid4().hex[:12]
    ext = _Path(file.filename or "upload.bin").suffix.lower() or ".bin"

    uploads_root = dal.uploads_dir() / thread_id
    uploads_root.mkdir(parents=True, exist_ok=True)
    rel_path = f"{thread_id}/{file_id}{ext}"
    abs_path = uploads_root / f"{file_id}{ext}"

    content = await file.read()
    abs_path.write_bytes(content)

    text, err = parse_attachment(abs_path, file.content_type or "", file.filename or "")
    dal.add_attachment(
        file_id=file_id, thread_id=thread_id, stage_id=stage_id,
        filename=file.filename or f"upload_{file_id}{ext}",
        mime=file.content_type or "",
        size_bytes=len(content),
        content_path=rel_path,
        parsed_text=text or None,
        parse_error=err,
    )
    dal.append_event(thread_id, stage_id, event_type="attachment_added",
                     detail=f'{{"file_id": "{file_id}", "filename": "{file.filename or ""}"}}')
    return AttachmentResponse(
        file_id=file_id,
        filename=file.filename or f"upload_{file_id}{ext}",
        mime=file.content_type or "",
        size_bytes=len(content),
        has_parsed_text=bool(text),
        parse_error=err or None,
        created_at=time.time(),
    )


@app.get(
    "/api/stage/{stage_id}/{thread_id}/attachments",
    response_model=AttachmentListResponse,
)
async def list_attachments(stage_id: str, thread_id: str):
    rows = dal.list_attachments(thread_id, stage_id)
    return AttachmentListResponse(attachments=[_attachment_to_response(r) for r in rows])


@app.delete("/api/stage/{stage_id}/{thread_id}/attachments/{file_id}")
async def delete_attachment(stage_id: str, thread_id: str, file_id: str):
    row = dal.delete_attachment(file_id)
    if row is None:
        raise HTTPException(
            404,
            detail=error_detail("attachment_not_found", f"attachment '{file_id}' 不存在"),
        )
    # 同步清檔（best-effort）
    try:
        (dal.uploads_dir() / row["content_path"]).unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("delete attachment file failed: %s", exc)
    dal.append_event(thread_id, stage_id, event_type="attachment_removed",
                     detail=f'{{"file_id": "{file_id}"}}')
    return {"deleted": file_id}


@app.get("/api/stage/{stage_id}/{thread_id}/attachments/{file_id}/content")
async def download_attachment(stage_id: str, thread_id: str, file_id: str):
    row = dal.get_attachment(file_id)
    if row is None:
        raise HTTPException(404, detail=error_detail("attachment_not_found", file_id))
    abs_path = dal.uploads_dir() / row["content_path"]
    if not abs_path.exists():
        raise HTTPException(404, detail=error_detail("file_missing", "原始檔已遺失"))
    return FileResponse(
        path=str(abs_path),
        filename=row["filename"],
        media_type=row["mime"] or "application/octet-stream",
    )
