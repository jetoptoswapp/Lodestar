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
    AgentListResponse,
    AgentResponse,
    AgentUpsertRequest,
    AttachmentListResponse,
    AttachmentResponse,
    CreateProjectRequest,
    DeliveryItemPreview,
    DeliveryPreviewResponse,
    DeliveryPublishRequest,
    DeliveryPublishResponse,
    ModelAdapterListResponse,
    ModelAdapterResponse,
    PluginListResponse,
    PluginProvides,
    PluginResponse,
    PluginToggleRequest,
    ProjectListResponse,
    ProjectResponse,
    SetProjectWorkflowRequest,
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
    UpdateProjectRequest,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowUpsertRequest,
    ImplementStartRequest,
    ImplementStartResponse,
    ImplementRunInfo,
    ImplementSessionResponse,
    ImplementSessionListResponse,
    ImplementCancelResponse,
    ImplementLogLine,
    ImplementLogResponse,
    RunnerInfo,
    RunnerListResponse,
)
from async_runtime import impl_dal, orchestrator, task_registry  # noqa: E402
from plugin_api import ToolHook  # noqa: E402
from delivery_parser import parse_stories_to_delivery_items  # noqa: E402
from parsers import parse as parse_attachment  # noqa: E402
from persistence import dal, migrations  # noqa: E402
from plugin_host import (  # noqa: E402
    CAP_AGENT,
    CAP_INTEGRATION,
    CAP_RUNNER,
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
    # shutdown：取消所有在跑的背景實作 task（避免殘留子程序）
    await task_registry.cancel_all()


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
                builtin=m.id in plugin_loader.BUILTIN_PLUGIN_IDS,
                discovery=plugin_info.discovery,
            )
        )
    return PluginListResponse(plugins=plugins)


def _reload_registry() -> Registry:
    """M4：重跑 plugin loader → 換 app.state.registry / engine（enable/disable 後用）。

    load_all 會讀 plugin_state、跳過 disabled、清乾淨舊 contributions，
    所以重跑一次即可讓 registry 反映最新啟用狀態。WorkflowEngine 無內部快取，
    換新 Registry 即生效。
    """
    registry = plugin_loader.load_all()
    app.state.registry = registry
    app.state.engine = WorkflowEngine(registry)
    return registry


@app.patch("/api/plugins/{plugin_id}", response_model=PluginResponse)
async def toggle_plugin(plugin_id: str, req: PluginToggleRequest):
    """啟用 / 停用 plugin。內建 plugin 不可停用（會打掛核心流程）。

    寫 plugin_state 後 hot-reload registry：
    - disable → 該 plugin 不再 register，其 stage / workflow / agent 從 catalog 消失
    - enable  → 重新 register，capability 回歸
    """
    reg = _registry()
    known_ids = {p.manifest.id for p in reg.loaded_plugins}
    if plugin_id not in known_ids:
        raise HTTPException(404, detail=error_detail("plugin_not_found", f"plugin '{plugin_id}' 不存在"))
    if plugin_id in plugin_loader.BUILTIN_PLUGIN_IDS and not req.enabled:
        raise HTTPException(
            409,
            detail=error_detail("plugin_is_builtin", f"plugin '{plugin_id}' 是內建，不可停用"),
        )

    dal.set_plugin_enabled(plugin_id, req.enabled)
    reg = _reload_registry()

    # 回傳更新後的 plugin 狀態
    info = next((p for p in reg.loaded_plugins if p.manifest.id == plugin_id), None)
    if info is None:  # defensive
        raise HTTPException(500, detail=error_detail("plugin_reload_failed", "reload 後找不到 plugin"))
    m = info.manifest
    provides = PluginProvides()
    for (pid, ctype, cid) in reg.contributions:
        if pid != plugin_id:
            continue
        if ctype == CAP_STAGE: provides.stages.append(cid)
        elif ctype == CAP_WORKFLOW: provides.workflows.append(cid)
        elif ctype == CAP_AGENT: provides.agents.append(cid)
        elif ctype == CAP_INTEGRATION: provides.integrations.append(cid)
    return PluginResponse(
        id=m.id, name=m.name, version=m.version, description=m.description,
        enabled=dal.plugin_enabled(m.id), provides=provides,
        load_error=info.error or None,
        builtin=m.id in plugin_loader.BUILTIN_PLUGIN_IDS,
        discovery=info.discovery,
    )


@app.get("/api/models", response_model=ModelAdapterListResponse)
async def get_models():
    """列出已註冊的 ModelAdapter（TopBar selector 用）。

    is_available=False 的 adapter 仍會出現（讓 UI 可標示「環境缺 cli」），
    使用者選 unavailable model 後仍可送 request，HarnessRunner 會回 model.unavailable error。
    """
    reg = _registry()
    owner = {cid: pid for (pid, ctype, cid) in reg.contributions if ctype == "model_adapter"}
    items: list[ModelAdapterResponse] = []
    for choice, adapter in reg.model_adapters.items():
        try:
            available = bool(adapter.is_available())
        except Exception:  # noqa: BLE001
            available = False
        items.append(ModelAdapterResponse(
            model_choice=choice,
            description=adapter.description,
            is_available=available,
            supports_multimodal=adapter.supports_multimodal,
            max_context_tokens=adapter.max_context_tokens,
            prompt_budget_tokens=adapter.prompt_budget_tokens,
            response_budget_tokens=adapter.response_budget_tokens,
            source_plugin=owner.get(choice),
        ))
    # 排序：可用優先、再按 model_choice 字母序，呈現穩定
    items.sort(key=lambda m: (not m.is_available, m.model_choice))
    return ModelAdapterListResponse(models=items)


@app.get("/api/runners", response_model=RunnerListResponse)
async def get_runners():
    """列出已註冊的 async AgentRunner（implement 面板的 runner picker 用）。

    registry 存的是 class（非 instance），instantiate 後檢查 is_available；
    unavailable（如環境缺 claude CLI）仍列出但標示，讓 UI 提示。
    """
    reg = _registry()
    owner = {cid: pid for (pid, ctype, cid) in reg.contributions if ctype == CAP_RUNNER}
    items: list[RunnerInfo] = []
    for choice, runner_cls in reg.runners.items():
        try:
            available = bool(runner_cls().is_available())
        except Exception:  # noqa: BLE001
            available = False
        items.append(RunnerInfo(choice=choice, available=available, source_plugin=owner.get(choice)))
    items.sort(key=lambda r: (not r.available, r.choice))
    return RunnerListResponse(runners=items)


# ============================================================
#  M3：Workflows CRUD（POST/PUT/DELETE/GET）
# ============================================================
def _builtin_workflow_to_response(wf, source_plugin: str) -> WorkflowResponse:
    """把 in-memory builtin WorkflowSpec 投映成 API response。"""
    # 舊式 builtin（agent_bindings: dict[stage_id, str]）→ 投映成 stage entries
    stages = []
    for sid in wf.stages:
        bindings = wf.agent_bindings.get(sid, ())
        # 兼容兩種形狀：tuple[AgentBinding] / str
        binding_list: list[dict] = []
        if isinstance(bindings, (list, tuple)):
            for b in bindings:
                if hasattr(b, "agent_id"):
                    binding_list.append({"agent_id": b.agent_id, "role": b.role})
                elif isinstance(b, str):
                    binding_list.append({"agent_id": b, "role": "lead"})
        elif isinstance(bindings, str):
            binding_list.append({"agent_id": bindings, "role": "lead"})

        stages.append({
            "stage_id": sid,
            "depends_on": list(wf.edges_override.get(sid, ())),
            "agent_bindings": binding_list,
            "collab_mode": wf.collab_mode.get(sid, "single"),
        })
    return WorkflowResponse(
        id=wf.id, label=wf.label, description=wf.description,
        stages=stages, source="builtin", source_plugin=source_plugin or None,
        created_at=None,
    )


@app.get("/api/workflows", response_model=WorkflowListResponse)
async def list_workflows():
    """合併 builtin（in-memory plugin 註冊）+ user-defined（DB）workflows。"""
    reg = _registry()
    owner = {cid: pid for (pid, ctype, cid) in reg.contributions if ctype == CAP_WORKFLOW}
    out: list[WorkflowResponse] = []
    for wf_id, wf in reg.workflows.items():
        out.append(_builtin_workflow_to_response(wf, owner.get(wf_id, "")))
    for d in dal.list_workflow_definitions():
        if d["id"] in reg.workflows:
            continue   # builtin 同 id 優先（user 不應覆寫 plugin workflow）
        out.append(WorkflowResponse(
            id=d["id"], label=d["label"], description=d["description"],
            stages=d["stages"], source="user", source_plugin=None,
            created_at=d["created_at"],
        ))
    return WorkflowListResponse(workflows=out)


@app.post("/api/workflows", response_model=WorkflowResponse, status_code=201)
async def create_workflow(req: WorkflowUpsertRequest):
    return _save_workflow(req, allow_existing=False)


@app.put("/api/workflows/{wf_id}", response_model=WorkflowResponse)
async def update_workflow(wf_id: str, req: WorkflowUpsertRequest):
    if req.id != wf_id:
        raise HTTPException(400, detail=error_detail("workflow_id_mismatch", "URL id 與 body id 不一致"))
    if dal.get_workflow_definition(wf_id) is None:
        raise HTTPException(404, detail=error_detail("workflow_not_found", f"workflow '{wf_id}' 不存在"))
    return _save_workflow(req, allow_existing=True)


def _save_workflow(req: WorkflowUpsertRequest, *, allow_existing: bool) -> WorkflowResponse:
    """upsert workflow + validate stages."""
    reg = _registry()
    # 不允許覆寫 builtin
    if req.id in reg.workflows:
        raise HTTPException(409, detail=error_detail("workflow_is_builtin", f"workflow '{req.id}' 是 builtin，不能 user 覆寫"))
    if not allow_existing and dal.get_workflow_definition(req.id) is not None:
        raise HTTPException(409, detail=error_detail("workflow_exists", f"workflow '{req.id}' 已存在；改用 PUT 更新"))
    # 驗證每個 stage 都已註冊
    stage_ids = [s.stage_id for s in req.stages]
    if len(stage_ids) != len(set(stage_ids)):
        raise HTTPException(400, detail=error_detail("workflow_duplicate_stage", "workflow 內 stage_id 重複"))
    unregistered = [sid for sid in stage_ids if sid not in reg.stages]
    if unregistered:
        raise HTTPException(400, detail=error_detail(
            "workflow_unknown_stage",
            f"未註冊的 stage：{unregistered}（目前 catalog：{sorted(reg.stages.keys())}）",
        ))
    # 驗 depends_on：每個 dep 都要在 workflow.stages 內、且必須在當前 stage 之前
    in_order_ids = []
    for s in req.stages:
        for dep in s.depends_on:
            if dep not in in_order_ids:
                raise HTTPException(400, detail=error_detail(
                    "workflow_invalid_dependency",
                    f"stage '{s.stage_id}' depends_on '{dep}'，但 '{dep}' 不在前面（防環）",
                ))
        in_order_ids.append(s.stage_id)
    # 驗 collab_mode
    for s in req.stages:
        if s.collab_mode not in ("single", "discussion", "dispatch"):
            raise HTTPException(400, detail=error_detail(
                "workflow_invalid_collab_mode",
                f"collab_mode '{s.collab_mode}' 不在 single / discussion / dispatch 內",
            ))

    stages_payload = [s.model_dump() for s in req.stages]
    dal.upsert_workflow_definition(
        wf_id=req.id, label=req.label, description=req.description,
        stages=stages_payload, source_plugin="user",
    )
    saved = dal.get_workflow_definition(req.id)
    assert saved is not None
    return WorkflowResponse(
        id=saved["id"], label=saved["label"], description=saved["description"],
        stages=saved["stages"], source="user", source_plugin=None,
        created_at=saved["created_at"],
    )


@app.delete("/api/workflows/{wf_id}")
async def delete_workflow(wf_id: str):
    reg = _registry()
    if wf_id in reg.workflows:
        raise HTTPException(409, detail=error_detail("workflow_is_builtin", f"workflow '{wf_id}' 是 builtin，不能刪除"))
    ok = dal.delete_workflow_definition(wf_id)
    if not ok:
        raise HTTPException(404, detail=error_detail("workflow_not_found", f"workflow '{wf_id}' 不存在"))
    return {"deleted": wf_id}


# ============================================================
#  M3：Agents CRUD（POST/PUT/DELETE/GET）
# ============================================================
def _builtin_agent_to_response(agent_id: str, spec, source_plugin: str) -> AgentResponse:
    return AgentResponse(
        agent_id=agent_id, name=spec.name, role=spec.role,
        system_prompt=spec.system_prompt, model_choice=spec.model_choice,
        max_iterations=spec.max_iterations, enabled=spec.enabled,
        tools=list(spec.tools), source="builtin",
        created_at=None, updated_at=None,
    )


@app.get("/api/agents", response_model=AgentListResponse)
async def list_agents_endpoint():
    """合併 builtin seed agents + user-defined（DB）；user 同 id 覆蓋 builtin。"""
    reg = _registry()
    out: dict[str, AgentResponse] = {}
    for agent_id, spec in reg.agents.items():
        out[agent_id] = _builtin_agent_to_response(agent_id, spec, "")
    for a in dal.list_agents():
        out[a["agent_id"]] = AgentResponse(
            agent_id=a["agent_id"], name=a["name"], role=a["role"],
            system_prompt=a["system_prompt"], model_choice=a["model_choice"],
            max_iterations=a["max_iterations"], enabled=a["enabled"],
            tools=a["tools"], source="user",
            created_at=a["created_at"], updated_at=a["updated_at"],
        )
    return AgentListResponse(agents=list(out.values()))


@app.post("/api/agents", response_model=AgentResponse, status_code=201)
async def create_agent(req: AgentUpsertRequest):
    return _save_agent(req, allow_existing=False)


@app.put("/api/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: str, req: AgentUpsertRequest):
    if req.agent_id != agent_id:
        raise HTTPException(400, detail=error_detail("agent_id_mismatch", "URL id 與 body id 不一致"))
    return _save_agent(req, allow_existing=True)


def _save_agent(req: AgentUpsertRequest, *, allow_existing: bool) -> AgentResponse:
    if req.max_iterations < 1:
        raise HTTPException(400, detail=error_detail("invalid_iterations", "max_iterations 必須 ≥ 1"))
    if not allow_existing and dal.get_agent(req.agent_id) is not None:
        raise HTTPException(409, detail=error_detail("agent_exists", f"agent '{req.agent_id}' 已存在；改用 PUT"))
    dal.upsert_agent(
        agent_id=req.agent_id, name=req.name, role=req.role,
        system_prompt=req.system_prompt, model_choice=req.model_choice,
        max_iterations=req.max_iterations, enabled=req.enabled,
        tools=list(req.tools),
    )
    saved = dal.get_agent(req.agent_id)
    assert saved is not None
    return AgentResponse(
        agent_id=saved["agent_id"], name=saved["name"], role=saved["role"],
        system_prompt=saved["system_prompt"], model_choice=saved["model_choice"],
        max_iterations=saved["max_iterations"], enabled=saved["enabled"],
        tools=saved["tools"], source="user",
        created_at=saved["created_at"], updated_at=saved["updated_at"],
    )


@app.delete("/api/agents/{agent_id}")
async def delete_agent_endpoint(agent_id: str):
    ok = dal.delete_agent(agent_id)
    if not ok:
        raise HTTPException(404, detail=error_detail("agent_not_found", f"agent '{agent_id}' 不存在"))
    return {"deleted": agent_id}


# ============================================================
#  M3：per-thread workflow 綁定
# ============================================================
@app.post("/api/projects/{thread_id}/workflow", response_model=ProjectResponse)
async def set_project_workflow_endpoint(thread_id: str, req: SetProjectWorkflowRequest):
    if dal.get_project(thread_id) is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{thread_id}' 不存在"))
    # 驗 workflow_id 存在（builtin OR user）；None 表示解除綁定
    if req.workflow_id is not None:
        reg = _registry()
        if req.workflow_id not in reg.workflows and dal.get_workflow_definition(req.workflow_id) is None:
            raise HTTPException(404, detail=error_detail("workflow_not_found", f"workflow '{req.workflow_id}' 不存在"))
    dal.set_project_workflow(thread_id, req.workflow_id)
    project = dal.get_project(thread_id)
    assert project is not None
    return ProjectResponse(**project)


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


@app.patch("/api/projects/{thread_id}", response_model=ProjectResponse)
async def update_project(thread_id: str, req: UpdateProjectRequest):
    """目前只支援改 name；workflow_id 等留待 M3 編輯器一起做。"""
    if req.name is None or not req.name.strip():
        raise HTTPException(400, detail=error_detail("invalid_name", "name 不可為空"))
    ok = dal.update_project_name(thread_id, req.name.strip())
    if not ok:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{thread_id}' 不存在"))
    project = dal.get_project(thread_id)
    if project is None:  # defensive
        raise HTTPException(500, detail=error_detail("project_read_failed", "thread 改名後讀取失敗"))
    return ProjectResponse(**project)


@app.delete("/api/projects/{thread_id}")
async def delete_project(thread_id: str):
    """刪 thread + cascade（artifacts / status / messages / events / revisions /
    attachments / harness_runs / harness_validation_results）+ 對應 uploads/ 檔案。"""
    paths = dal.delete_project_cascade(thread_id)
    if paths is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{thread_id}' 不存在"))
    # best-effort 清檔案；DB 已刪、檔案殘留不致命
    uploads_root = dal.uploads_dir()
    removed = 0
    for rel in paths:
        try:
            (uploads_root / rel).unlink(missing_ok=True)
            removed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("delete attachment file failed for %s: %s", rel, exc)
    # 順手把 uploads/<thread_id>/ 空目錄清掉
    thread_dir = uploads_root / thread_id
    try:
        if thread_dir.exists() and not any(thread_dir.iterdir()):
            thread_dir.rmdir()
    except Exception as exc:  # noqa: BLE001
        log.warning("rmdir %s failed: %s", thread_dir, exc)
    return {"deleted": thread_id, "files_removed": removed}


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


@app.post("/api/stage/stories/{thread_id}/preview-delivery", response_model=DeliveryPreviewResponse)
async def stories_preview_delivery(thread_id: str, req: DeliveryPublishRequest):
    """解析 stories artifact → DeliveryItem[] → 呼 IntegrationSpec.preview()。

    不真打外部 API；給前端顯示「即將建立什麼」清單供 user 確認後再 publish。
    """
    if dal.get_project(thread_id) is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{thread_id}' 不存在"))
    reg = _registry()
    integ = reg.integrations.get(req.target)
    if integ is None:
        raise HTTPException(
            404,
            detail=error_detail("integration_not_found", f"integration '{req.target}' 未註冊"),
        )
    artifact = dal.get_artifact(thread_id, "stories") or ""
    if not artifact.strip():
        raise HTTPException(
            400,
            detail=error_detail("stories_empty", "stories 還沒生成，不能 publish"),
        )
    items = parse_stories_to_delivery_items(artifact, target_project=req.config.get("repo", ""))
    if not items:
        raise HTTPException(
            400,
            detail=error_detail(
                "stories_unparseable",
                "stories 解析不出任何 DeliveryItem；檢查 heading shape：## Epic N: / ### Story N.M —",
            ),
        )
    previews = integ.preview(items, req.config)
    return DeliveryPreviewResponse(
        target=req.target, config=req.config, item_count=len(items),
        items=[DeliveryItemPreview(**p) for p in previews],
    )


@app.post("/api/stage/stories/{thread_id}/publish", response_model=DeliveryPublishResponse)
async def stories_publish(thread_id: str, req: DeliveryPublishRequest):
    """解析 stories → 呼 IntegrationSpec.publish()。回傳已建立的 issue URL。

    要 user 在 publish modal 上 confirm 過再呼叫；本 endpoint 不二次確認。
    """
    if dal.get_project(thread_id) is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{thread_id}' 不存在"))
    reg = _registry()
    integ = reg.integrations.get(req.target)
    if integ is None:
        raise HTTPException(
            404,
            detail=error_detail("integration_not_found", f"integration '{req.target}' 未註冊"),
        )
    artifact = dal.get_artifact(thread_id, "stories") or ""
    if not artifact.strip():
        raise HTTPException(
            400,
            detail=error_detail("stories_empty", "stories 還沒生成，不能 publish"),
        )
    items = parse_stories_to_delivery_items(artifact, target_project=req.config.get("repo", ""))
    if not items:
        raise HTTPException(400, detail=error_detail("stories_unparseable", "stories 解析失敗"))

    # 真實 publish（GitHub 已實作；jira / gitlab stub 會回 success=False）
    result = await asyncio.to_thread(integ.publish, items, req.config)
    dal.append_event(
        thread_id, "stories",
        event_type="delivery_published" if result.success else "delivery_publish_failed",
        detail=f'{{"target":"{result.target}","count":{result.count},"created":{result.count if result.success else 0}}}',
    )
    return DeliveryPublishResponse(
        success=result.success,
        target=result.target,
        count=result.count,
        created=list(result.created),
    )


# ============================================================
#  Implement（async 實作 agent，M5）
# ============================================================
def _impl_run_info(r: dict) -> ImplementRunInfo:
    return ImplementRunInfo(
        run_id=r["run_id"], attempt=r["attempt"], runner=r["runner"], status=r["status"],
        exit_code=r["exit_code"], cancelled=bool(r["cancelled"]), timed_out=bool(r["timed_out"]),
        parent_run_id=r["parent_run_id"], started_at=r["started_at"], ended_at=r["ended_at"],
    )


def _impl_session_response(s: dict) -> ImplementSessionResponse:
    runs = impl_dal.list_runs(s["session_id"])
    return ImplementSessionResponse(
        session_id=s["session_id"], thread_id=s["thread_id"], stage=s["stage"],
        title=s["title"], target_repo=s["target_repo"], runner=s["runner"],
        status=s["status"], pr_url=s["pr_url"], error_message=s["error_message"],
        created_at=s["created_at"], updated_at=s["updated_at"],
        runs=[_impl_run_info(r) for r in runs],
    )


@app.post("/api/implement/start", response_model=ImplementStartResponse)
async def implement_start(req: ImplementStartRequest):
    """啟動一次 async 實作 session（非阻塞，立刻回 session_id）。

    runner 由 registry 解析（mock = 安全 dry-run）；hooks 取所有 registered tool hooks；
    story 留空則讀該 thread 的 stories artifact。實際 fix-loop 在背景 task 跑。
    """
    if dal.get_project(req.thread_id) is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{req.thread_id}' 不存在"))
    reg = _registry()
    runner_cls = reg.runners.get(req.runner)
    if runner_cls is None:
        raise HTTPException(400, detail=error_detail("runner_not_found", f"runner '{req.runner}' 未註冊"))
    runner = runner_cls()
    if not runner.is_available():
        raise HTTPException(400, detail=error_detail("runner_unavailable", f"runner '{req.runner}' 在此環境不可用"))
    story = req.story.strip() or (dal.get_artifact(req.thread_id, "stories") or "")
    if not story.strip():
        raise HTTPException(400, detail=error_detail("story_empty", "沒有 story 可實作：先生成 stories 或於請求帶 story"))
    hooks = [h for h in reg.hooks.get("tool", []) if isinstance(h, ToolHook)]
    title = req.title.strip() or f"Implement {req.thread_id[:8]}"
    session_id = orchestrator.start_session(
        thread_id=req.thread_id, story=story, runner=runner, runner_choice=req.runner,
        target_repo=req.target_repo, title=title, hooks=hooks,
    )
    return ImplementStartResponse(session_id=session_id)


@app.get("/api/implement/threads/{thread_id}/sessions", response_model=ImplementSessionListResponse)
async def implement_sessions(thread_id: str):
    sessions = impl_dal.list_sessions(thread_id)
    return ImplementSessionListResponse(sessions=[_impl_session_response(s) for s in sessions])


@app.post("/api/implement/{session_id}/cancel", response_model=ImplementCancelResponse)
async def implement_cancel(session_id: int):
    if impl_dal.get_session(session_id) is None:
        raise HTTPException(404, detail=error_detail("session_not_found", f"session {session_id} 不存在"))
    requested = await orchestrator.request_cancel(session_id)
    return ImplementCancelResponse(session_id=session_id, cancel_requested=requested)


@app.get("/api/implement/{session_id}/log", response_model=ImplementLogResponse)
async def implement_log(session_id: int, after_id: int = 0):
    """poll log channel：回 after_id 之後的所有 run 串流行 + 各 run 狀態 + 下次游標。"""
    s = impl_dal.get_session(session_id)
    if s is None:
        raise HTTPException(404, detail=error_detail("session_not_found", f"session {session_id} 不存在"))
    rows = impl_dal.list_session_messages(session_id, after_id=after_id)
    next_cursor = rows[-1]["id"] if rows else after_id
    lines = [
        ImplementLogLine(id=r["id"], run_id=r["run_id"], attempt=r["attempt"],
                         kind=r["kind"], content=r["content"])
        for r in rows
    ]
    runs = [_impl_run_info(r) for r in impl_dal.list_runs(session_id)]
    return ImplementLogResponse(session_id=session_id, status=s["status"],
                                next_cursor=next_cursor, lines=lines, runs=runs)


@app.get("/api/implement/{session_id}", response_model=ImplementSessionResponse)
async def implement_status(session_id: int):
    s = impl_dal.get_session(session_id)
    if s is None:
        raise HTTPException(404, detail=error_detail("session_not_found", f"session {session_id} 不存在"))
    return _impl_session_response(s)


@app.post("/api/stage/{stage_id}/{thread_id}/approve", response_model=StageStateResponse)
async def stage_approve(stage_id: str, thread_id: str):
    """把 stage 標為 approved（要求 artifact 非空）。回傳更新後完整 state。"""
    reg: Registry = _registry()
    if stage_id not in reg.stages:
        raise HTTPException(404, detail=error_detail("stage_not_found", f"stage '{stage_id}' 未註冊"))
    if dal.get_project(thread_id) is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{thread_id}' 不存在"))
    artifact = dal.get_artifact(thread_id, stage_id) or ""
    if not artifact.strip():
        raise HTTPException(
            400,
            detail=error_detail("artifact_empty", f"stage '{stage_id}' 還沒生成內容，不能核准"),
        )
    dal.set_stage_status(thread_id, stage_id, "approved")
    dal.append_event(thread_id, stage_id, event_type="approved", detail="")
    meta = dal.get_artifact_meta(thread_id, stage_id) or {}
    return StageStateResponse(
        stage_id=stage_id, status="approved", artifact=artifact,
        has_content=True, last_updated_at=meta.get("updated_at"),
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
