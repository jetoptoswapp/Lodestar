"""FastAPI app —— 通用 HTTP API（無 per-stage 特例）。

啟動時：跑 DB migration → plugin loader，把 Registry 放進 app.state。
M0 endpoint：/api/health、/api/stages（catalog，M0 空）、/api/plugins、/api/integrations。
stage 操作 / workflow / agent / SSE 隨 M1+ 增補。
"""
from __future__ import annotations

import json
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
from agent_resolver import resolve_agent  # noqa: E402
from api_models import (  # noqa: E402
    AgentListResponse,
    AgentResponse,
    AgentSkillsUpdateRequest,
    AgentUpsertRequest,
    AttachmentListResponse,
    AttachmentResponse,
    CreateProjectRequest,
    DeliveryItemPreview,
    DeliveryStatus,
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
    SkillListResponse,
    SkillResponse,
    SkillUpsertRequest,
    StageActionResponse,
    ValidationOutcomeResponse,
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
    AgentBindingPayload,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowStagePayload,
    WorkflowUpsertRequest,
    ImplementStartRequest,
    ImplementStartResponse,
    ImplementRunInfo,
    ImplementSessionResponse,
    ImplementSessionListResponse,
    ImplementCancelResponse,
    ImplementLogLine,
    ImplementLogResponse,
    ImplementBatchStartRequest,
    ImplementBatchStartResponse,
    ImplementBatchItem,
    ImplementBatchResponse,
    ImplementBatchListResponse,
    ImplementBatchCancelResponse,
    RunnerInfo,
    RunnerListResponse,
)
from async_runtime import batch as impl_batch, impl_dal, orchestrator, task_registry  # noqa: E402
from plugin_api import ToolHook  # noqa: E402
from delivery_parser import parse_stories_to_delivery_items  # noqa: E402
from parsers import parse as parse_attachment  # noqa: E402
import keystore  # noqa: E402
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
    # 啟動恢復：上次進程留下的孤兒 running/pending impl session（task 已隨進程消失）標 failed
    from async_runtime import impl_dal
    orphaned = impl_dal.fail_orphaned_running()
    if orphaned:
        log.warning("startup recovery: marked %d orphaned impl session(s) as failed", orphaned)
    loaded = [p.manifest.id for p in registry.loaded_plugins if p.loaded]
    log.info("startup complete — plugins loaded: %s", loaded or "(none)")
    log.info("LODESTAR_UPLOADS_DIR=%s", os.environ["LODESTAR_UPLOADS_DIR"])
    yield
    # shutdown：取消所有在跑的背景實作 task（避免殘留子程序）
    await task_registry.cancel_all()


app = FastAPI(title="ai-tool-v3", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{FRONTEND_PORT}", f"http://0.0.0.0:{FRONTEND_PORT}"],
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
                description=spec.description,
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


def _declared_provides(manifest) -> PluginProvides:
    """從 manifest 宣告（plugin.toml `[[contributes.*]]`）反推 provides。
    用於 disabled / 未註冊 plugin —— 沒有 live contribution 時仍能讓 UI 正確分類
    並預覽「啟用後會提供什麼」（否則停用的 feature plugin 會被誤丟進「系統零件」區）。
    """
    c = getattr(manifest, "contributes", None) or {}

    def ids(key: str) -> list[str]:
        return [
            item["id"]
            for item in c.get(key, [])
            if isinstance(item, dict) and item.get("id")
        ]

    return PluginProvides(
        stages=ids("stage"),
        workflows=ids("workflow"),
        agents=ids("agent"),
        integrations=ids("integration"),
    )


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
        # 已註冊（enabled）→ 用 live contribution；未註冊（disabled / 載入失敗）→
        # fallback 用 manifest 宣告值，讓前端 isFeature 分類正確、停用後仍留在「你的功能」區。
        live = provides_by_plugin.get(m.id)
        provides = live if live is not None else _declared_provides(m)
        plugins.append(
            PluginResponse(
                id=m.id,
                name=m.name,
                version=m.version,
                description=m.description,
                enabled=dal.plugin_enabled(m.id),
                provides=provides,
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


@app.get("/api/telemetry/harness")
async def get_harness_metrics(since: float = 0.0, stage: str = ""):
    """harness 遙測指標（fix-loop 迭代鏈、needs_revision／fail 率、validator 命中、judge run、時延）。

    把只寫不讀的 harness_runs / harness_validation_results 變可度量——eval 閉環的線上讀側。
    """
    from telemetry_read import harness_metrics
    return harness_metrics(since=since, stage=stage)


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
def _skill_to_response(s) -> SkillResponse:
    """SkillSpec（registry seed）或 DB dict → SkillResponse。"""
    if isinstance(s, dict):
        return SkillResponse(skill_id=s["skill_id"], name=s["name"],
                             description=s.get("description", ""), body=s.get("body", ""),
                             version=s.get("version", "1.0"))
    return SkillResponse(skill_id=s.skill_id, name=s.name, description=s.description,
                         body=s.body, version=s.version)


def _agent_response_with_skills(reg, agent_id: str) -> AgentResponse:
    """用 resolve_agent（已 embed skills，依 sort_order）組 AgentResponse；source/時間戳看 DB。"""
    spec = resolve_agent(reg, agent_id)
    assert spec is not None
    db = dal.get_agent(agent_id)
    return AgentResponse(
        agent_id=spec.agent_id, name=spec.name, role=spec.role,
        system_prompt=spec.system_prompt, model_choice=spec.model_choice,
        max_iterations=spec.max_iterations, enabled=spec.enabled,
        tools=list(spec.tools), skills=[_skill_to_response(s) for s in spec.skills],
        source="user" if db else "builtin",
        created_at=db["created_at"] if db else None,
        updated_at=db["updated_at"] if db else None,
    )


@app.get("/api/agents", response_model=AgentListResponse)
async def list_agents_endpoint():
    """合併 builtin seed agents + user-defined（DB）；user 同 id 覆蓋 builtin。各帶 resolved skills。"""
    reg = _registry()
    ids = list(reg.agents.keys()) + [a["agent_id"] for a in dal.list_agents()]
    out = {aid: _agent_response_with_skills(reg, aid) for aid in dict.fromkeys(ids)}
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
    return _agent_response_with_skills(_registry(), req.agent_id)


@app.delete("/api/agents/{agent_id}")
async def delete_agent_endpoint(agent_id: str):
    ok = dal.delete_agent(agent_id)
    if not ok:
        raise HTTPException(404, detail=error_detail("agent_not_found", f"agent '{agent_id}' 不存在"))
    return {"deleted": agent_id}


# ============================================================
#  Skills CRUD（POST/PUT/DELETE/GET）+ agent 綁定
# ============================================================
@app.get("/api/skills", response_model=SkillListResponse)
async def list_skills_endpoint():
    """合併 builtin seed skills（registry）+ user-defined（DB）；user 同 id 覆蓋 builtin。"""
    reg = _registry()
    out: dict[str, SkillResponse] = {}
    for sid, spec in reg.skills.items():
        out[sid] = _skill_to_response(spec)
    for s in dal.list_skills():
        out[s["skill_id"]] = _skill_to_response(s)
    return SkillListResponse(skills=list(out.values()))


@app.post("/api/skills", response_model=SkillResponse, status_code=201)
async def create_skill(req: SkillUpsertRequest):
    if dal.get_skill(req.skill_id) is not None:
        raise HTTPException(409, detail=error_detail("skill_exists", f"skill '{req.skill_id}' 已存在；改用 PUT"))
    return _save_skill(req)


@app.put("/api/skills/{skill_id}", response_model=SkillResponse)
async def update_skill(skill_id: str, req: SkillUpsertRequest):
    if req.skill_id != skill_id:
        raise HTTPException(400, detail=error_detail("skill_id_mismatch", "URL id 與 body id 不一致"))
    return _save_skill(req)


def _save_skill(req: SkillUpsertRequest) -> SkillResponse:
    dal.upsert_skill(skill_id=req.skill_id, name=req.name, description=req.description,
                     body=req.body, version=req.version)
    saved = dal.get_skill(req.skill_id)
    assert saved is not None
    return _skill_to_response(saved)


@app.delete("/api/skills/{skill_id}")
async def delete_skill_endpoint(skill_id: str):
    if not dal.delete_skill(skill_id):
        raise HTTPException(404, detail=error_detail("skill_not_found", f"skill '{skill_id}' 不存在"))
    return {"deleted": skill_id}


@app.put("/api/agents/{agent_id}/skills", response_model=AgentResponse)
async def set_agent_skills_endpoint(agent_id: str, req: AgentSkillsUpdateRequest):
    """整批覆寫 agent 的 skill 綁定（陣列順序=sort_order）。對 seed agent 也允許（寫 agent_skills）。"""
    reg = _registry()
    if dal.get_agent(agent_id) is None and agent_id not in reg.agents:
        raise HTTPException(404, detail=error_detail("agent_not_found", f"agent '{agent_id}' 不存在"))
    seen: set[str] = set()
    clean: list[str] = []
    for sid in req.skill_ids:
        if sid in seen:
            continue
        if dal.get_skill(sid) is None and sid not in reg.skills:
            raise HTTPException(404, detail=error_detail("skill_not_found", f"skill '{sid}' 不存在"))
        seen.add(sid)
        clean.append(sid)
    dal.set_agent_skills(agent_id, clean)
    return _agent_response_with_skills(reg, agent_id)


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


# ============================================================
#  RCA：agentic plan → workflow（RCA-3）
#  讀 approved 的 rca_plan artifact → 建 user workflow（重用 _save_workflow 驗證）→ 綁 thread。
#  inline 解析 plan（host 不 import domain plugin，維持邊界）。
# ============================================================
def _parse_rca_plan(text: str) -> "dict | None":
    if not text:
        return None
    start, end = "[PLAN_START]", "[PLAN_END]"
    i, j = text.find(start), text.find(end)
    if i != -1 and j > i:
        blob = text[i + len(start):j]
    else:
        a, b = text.find("{"), text.rfind("}")
        if a == -1 or b <= a:
            return None
        blob = text[a:b + 1]
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@app.post("/api/projects/{thread_id}/rca/apply-plan", response_model=WorkflowResponse)
async def rca_apply_plan(thread_id: str):
    if dal.get_project(thread_id) is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{thread_id}' 不存在"))
    plan_art = dal.get_artifact(thread_id, "rca_plan")
    if not plan_art or not plan_art.strip():
        raise HTTPException(404, detail=error_detail("plan_not_found", "rca_plan artifact 不存在；請先 generate rca_plan"))
    if dal.get_stage_status(thread_id, "rca_plan") != "approved":
        raise HTTPException(409, detail=error_detail("plan_not_approved", "rca_plan 尚未 approved；核准後才能套用"))
    plan = _parse_rca_plan(plan_art)
    if plan is None or not isinstance(plan.get("stages"), list) or not plan["stages"]:
        raise HTTPException(400, detail=error_detail("plan_unparseable", "無法解析 plan JSON（需含 stages）"))

    stages_payload: list[WorkflowStagePayload] = []
    has_intake = any(isinstance(s, dict) and s.get("stage_id") == "rca_intake" for s in plan["stages"])
    if not has_intake:
        # 確保依賴鏈完整：plan 未含 rca_intake 時自動前置（異常來源）
        stages_payload.append(WorkflowStagePayload(
            stage_id="rca_intake", depends_on=[],
            agent_bindings=[AgentBindingPayload(agent_id="rca_intake_helper", role="lead")],
            collab_mode="single"))
    for s in plan["stages"]:
        if not isinstance(s, dict) or not s.get("stage_id"):
            continue
        stages_payload.append(WorkflowStagePayload(
            stage_id=s["stage_id"],
            depends_on=list(s.get("depends_on") or []),
            agent_bindings=[
                AgentBindingPayload(agent_id=b.get("agent_id", ""), role=b.get("role", "lead"))
                for b in (s.get("agent_bindings") or [])
                if isinstance(b, dict) and b.get("agent_id")
            ],
            collab_mode=s.get("collab_mode", "single") or "single",
        ))

    wf_id = f"rca_plan_{thread_id}"
    req = WorkflowUpsertRequest(
        id=wf_id,
        label=plan.get("label") or f"RCA plan · {thread_id}",
        description=plan.get("description") or plan.get("rationale", ""),
        stages=stages_payload,
    )
    resp = _save_workflow(req, allow_existing=True)   # 驗 stage/dep/collab → 精確 4xx
    dal.set_project_workflow(thread_id, wf_id)
    return resp


@app.get("/api/integrations")
async def get_integrations():
    reg = _registry()
    return {
        "integrations": [
            {"target": t, "description": spec.description, "config_schema": spec.config_schema}
            for t, spec in reg.integrations.items()
        ]
    }


# ---- Integration credential keystore（server-side；機密明文不回傳前端）----
def _integration_or_404(target: str):
    integ = _registry().integrations.get(target)
    if integ is None:
        raise HTTPException(
            404, detail=error_detail("integration_not_found", f"integration '{target}' 未註冊")
        )
    return integ


def _secret_field_keys(integ) -> set[str]:
    """config_schema 中 type == 'password' 的欄位 = 機密欄。"""
    fields = (getattr(integ, "config_schema", None) or {}).get("fields", [])
    return {f["key"] for f in fields if isinstance(f, dict) and f.get("type") == "password" and f.get("key")}


def _credentials_status(target: str, integ) -> dict:
    """回傳已存憑證的狀態：機密欄只回「是否已設定」，非機密欄回實際值。明文機密不外洩。"""
    stored = keystore.get_credentials(target)
    secret_keys = _secret_field_keys(integ)
    return {
        "target": target,
        "has_credentials": bool(stored),
        "secret_fields_set": sorted(k for k in stored if k in secret_keys),
        "values": {k: v for k, v in stored.items() if k not in secret_keys},
    }


def _effective_config(target: str, req_config: dict) -> dict:
    """合併 keystore 已存 config 與 request config：request 非空值覆蓋，其餘（如機密）沿用 keystore。"""
    merged = dict(keystore.get_credentials(target))
    for k, v in (req_config or {}).items():
        if v not in (None, ""):
            merged[k] = v
    return merged


@app.get("/api/integrations/{target}/credentials")
async def get_integration_credentials(target: str):
    integ = _integration_or_404(target)
    return _credentials_status(target, integ)


@app.put("/api/integrations/{target}/credentials")
async def put_integration_credentials(target: str, config: dict):
    """儲存（加密）integration 憑證。空字串欄位不覆寫既有值（方便只更新 token 或只更新 repo）。"""
    integ = _integration_or_404(target)
    merged = dict(keystore.get_credentials(target))
    for k, v in (config or {}).items():
        if v not in (None, ""):
            merged[k] = str(v)
    keystore.set_credentials(target, merged)
    return _credentials_status(target, integ)


@app.delete("/api/integrations/{target}/credentials", status_code=204)
async def delete_integration_credentials(target: str):
    _integration_or_404(target)
    keystore.delete_credentials(target)
    return None


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
    dal.create_project(thread_id, req.name, req.workflow_id,
                       delivery_target=req.delivery_target, repo_mode=req.repo_mode,
                       repo_full_name=req.repo_full_name, repo_owner=req.repo_owner,
                       repo_visibility=req.repo_visibility)
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
    """改 name 與/或 delivery repo 設定（皆 optional；只更新有帶的部分）。"""
    cur = dal.get_project(thread_id)
    if cur is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{thread_id}' 不存在"))
    delivery_fields = (req.delivery_target, req.repo_mode, req.repo_full_name,
                       req.repo_owner, req.repo_visibility)
    if req.name is None and all(f is None for f in delivery_fields):
        raise HTTPException(400, detail=error_detail("invalid_name", "name 不可為空"))
    if req.name is not None:
        if not req.name.strip():
            raise HTTPException(400, detail=error_detail("invalid_name", "name 不可為空"))
        dal.update_project_name(thread_id, req.name.strip())
    if any(f is not None for f in delivery_fields):
        dal.update_project_delivery(
            thread_id,
            delivery_target=req.delivery_target if req.delivery_target is not None else cur["delivery_target"],
            repo_mode=req.repo_mode if req.repo_mode is not None else cur["repo_mode"],
            repo_full_name=req.repo_full_name if req.repo_full_name is not None else cur["repo_full_name"],
            repo_owner=req.repo_owner if req.repo_owner is not None else cur["repo_owner"],
            repo_visibility=req.repo_visibility if req.repo_visibility is not None else cur["repo_visibility"],
        )
    project = dal.get_project(thread_id)
    if project is None:  # defensive
        raise HTTPException(500, detail=error_detail("project_read_failed", "thread 更新後讀取失敗"))
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
    validations = out.get("validations", [])
    return StageActionResponse(
        stage_id=stage_id,
        artifact=out["artifact"],
        state_extra=out["state_extra"],
        downstream_reset=out["downstream_reset"],
        validations=[ValidationOutcomeResponse(**v) for v in validations],
        needs_revision=any(v["severity"] == "fail" for v in validations),
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
    cfg = _effective_config(req.target, req.config)  # 合併 keystore 機密（如 token）
    # github/gitlab 有設專案 delivery → repo 以專案設定為準（蓋掉殘留在 keystore 的舊全域 repo）。
    # preview 唯讀（create=False）：new 模式未建時回「預定要建的名稱」，不真的建 repo。
    # 未設專案 delivery → 沿用 config/keystore 的 repo（向後相容）。
    proj = dal.get_project(thread_id) or {}
    if getattr(integ, "create_repo", None) is not None and (proj.get("delivery_target") or "").strip():
        from delivery_repo import DeliveryRepoError, resolve_project_repo
        try:
            _, cfg["repo"] = resolve_project_repo(reg, thread_id, create=False)
        except DeliveryRepoError:
            pass                                     # 設定不完整 → preview 不阻擋，沿用既有值
    items = parse_stories_to_delivery_items(artifact, target_project=cfg.get("repo", ""))
    if not items:
        raise HTTPException(
            400,
            detail=error_detail(
                "stories_unparseable",
                "stories 解析不出任何 DeliveryItem；檢查 heading shape：## Epic N: / ### Story N.M —",
            ),
        )
    previews = integ.preview(items, cfg)
    # 回傳不含合併後機密（response.config 僅回 client 原送值，避免 keystore token 外洩）
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
    cfg = _effective_config(req.target, req.config)  # 合併 keystore 機密（如 token）
    # github/gitlab 有設專案 delivery → repo 以專案設定為準；new 模式未建時在此 lazy 建（create=True）。
    # 設定不完整（缺 token / existing 未填 repo）→ 明確 400，不默默退回殘留全域值。
    # 未設專案 delivery → 沿用 config/keystore 的 repo（向後相容；publish_github 會自行驗 owner/repo）。
    proj = dal.get_project(thread_id) or {}
    if getattr(integ, "create_repo", None) is not None and (proj.get("delivery_target") or "").strip():
        from delivery_repo import DeliveryRepoError, resolve_project_repo
        try:
            _, cfg["repo"] = await asyncio.to_thread(resolve_project_repo, reg, thread_id, create=True)
        except DeliveryRepoError as exc:
            raise HTTPException(400, detail=error_detail("delivery_repo_unresolved", str(exc)))
    items = parse_stories_to_delivery_items(artifact, target_project=cfg.get("repo", ""))
    if not items:
        raise HTTPException(400, detail=error_detail("stories_unparseable", "stories 解析失敗"))

    # 真實 publish（GitHub 已實作；jira / gitlab stub 會回 success=False）
    result = await asyncio.to_thread(integ.publish, items, cfg)
    dal.append_event(
        thread_id, "stories",
        event_type="delivery_published" if result.success else "delivery_publish_failed",
        detail=json.dumps({
            "target": result.target,
            "count": result.count,
            "created": len(result.created),
            "repo": cfg.get("repo", ""),
        }),
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
        dispatch_role=r["dispatch_role"] if "dispatch_role" in r.keys() else "",
        exit_code=r["exit_code"], cancelled=bool(r["cancelled"]), timed_out=bool(r["timed_out"]),
        parent_run_id=r["parent_run_id"], started_at=r["started_at"], ended_at=r["ended_at"],
    )


def _row_get(row: dict, key: str, default=None):
    """sqlite Row → 容錯取欄（舊資料 / 部分 SELECT 可能缺欄）。"""
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _impl_session_response(s: dict) -> ImplementSessionResponse:
    runs = impl_dal.list_runs(s["session_id"])
    return ImplementSessionResponse(
        session_id=s["session_id"], thread_id=s["thread_id"], stage=s["stage"],
        title=s["title"], target_repo=s["target_repo"], runner=s["runner"],
        status=s["status"], pr_url=s["pr_url"], error_message=s["error_message"],
        batch_id=_row_get(s, "batch_id"), issue_number=_row_get(s, "issue_number"),
        story_key=_row_get(s, "story_key", "") or "",
        created_at=s["created_at"], updated_at=s["updated_at"],
        runs=[_impl_run_info(r) for r in runs],
    )


def _real_pr_opener(thread_id: str):
    """組真實開 PR 的 PrOpener（claude-cli 單 session 用）。token 來自 keystore；
    工作目錄為專案共用 clone（一個專案一個目錄，所有 session 沿用）。"""
    from async_runtime.github_pr import make_github_pr_opener
    return make_github_pr_opener(
        get_token=lambda: keystore.get_credentials("github").get("token", ""),
        workdir_for=lambda _sid: orchestrator.project_clone_dir(thread_id),
        already_opened=lambda sid: (impl_dal.get_session(sid) or {}).get("pr_url", ""))


def _gitlab_mr_opener(base_url: str, thread_id: str):
    """組真實開 GitLab MR 的 PrOpener（claude-cli 單 session 用）。"""
    from async_runtime.gitlab_mr import make_gitlab_mr_opener
    return make_gitlab_mr_opener(
        get_token=lambda: keystore.get_credentials("gitlab").get("token", ""),
        workdir_for=lambda _sid: orchestrator.project_clone_dir(thread_id),
        base_url=base_url,
        already_opened=lambda sid: (impl_dal.get_session(sid) or {}).get("pr_url", ""))


def _delivery_opener(target: str, creds: dict, thread_id: str):
    """依 delivery target 回對應的 open_pr/open_mr opener（工作目錄以專案為 key）。"""
    if target == "gitlab":
        return _gitlab_mr_opener((creds.get("base_url") or "https://gitlab.com").rstrip("/"), thread_id)
    return _real_pr_opener(thread_id)


def _delivery_clone_url(target: str, creds: dict, repo: str) -> str:
    """依 delivery target 組含 token 的 clone url（token 缺 → ""）。"""
    token = creds.get("token", "")
    if not token:
        return ""
    if target == "gitlab":
        host = (creds.get("base_url") or "https://gitlab.com").rstrip("/").split("://")[-1]
        return f"https://oauth2:{token}@{host}/{repo}.git"
    return f"https://x-access-token:{token}@github.com/{repo}.git"


def _batch_opener_builder(target: str, creds: dict, thread_id: str):
    """回 batch 用的 build_opener(issue_number_for, pr_title_for) → PrOpener。
    workdir 指向專案共用 clone dir；每 session 只 Closes 自己對應的 issue + 在該 issue 留言。"""
    workdir_for = lambda _sid: orchestrator.project_clone_dir(thread_id)
    already = lambda sid: (impl_dal.get_session(sid) or {}).get("pr_url", "")
    if target == "gitlab":
        from async_runtime.gitlab_mr import make_gitlab_mr_opener
        base_url = (creds.get("base_url") or "https://gitlab.com").rstrip("/")
        return lambda *, issue_number_for, pr_title_for: make_gitlab_mr_opener(
            get_token=lambda: keystore.get_credentials("gitlab").get("token", ""),
            workdir_for=workdir_for, base_url=base_url, already_opened=already,
            issue_number_for=issue_number_for, pr_title_for=pr_title_for)
    from async_runtime.github_pr import make_github_pr_opener
    return lambda *, issue_number_for, pr_title_for: make_github_pr_opener(
        get_token=lambda: keystore.get_credentials("github").get("token", ""),
        workdir_for=workdir_for, already_opened=already,
        issue_number_for=issue_number_for, pr_title_for=pr_title_for)


def _batch_issue_lister(target: str, creds: dict, repo: str):
    """回 list_issues() → repo 的 open issue (number, title)，供 story↔issue 比對。"""
    token = creds.get("token", "")
    if target == "gitlab":
        from async_runtime.gitlab_mr import list_open_issues as gl_list
        base = (creds.get("base_url") or "https://gitlab.com").rstrip("/")
        return lambda: gl_list(base, token, repo)
    from async_runtime.github_pr import list_open_issues as gh_list
    return lambda: gh_list(repo, token)


def _batch_skip_keys(target: str, creds: dict, repo: str) -> set[str]:
    """冪等重跑：回「已完成（issue 已關）或進行中（已有 open PR/MR）」的 story 編號集（如 {'1.1','1.3'}）。
    github / gitlab 皆支援；token 缺 / 其他 target → set()（不跳過，寧可重做也不漏做）。失敗回 set()。"""
    token = creds.get("token", "")
    if not token:
        return set()
    keys: set[str] = set()
    if target == "github":
        from async_runtime.github_pr import (
            list_closed_issues, list_open_issues, list_open_pr_issue_numbers,
        )
        for _n, title in list_closed_issues(repo, token):          # issue 已關 = 已交付
            k = impl_batch._story_key(title)
            if k:
                keys.add(k)
        in_prog = list_open_pr_issue_numbers(repo, token)          # 開著但已有 open PR = 進行中
        if in_prog:
            num2key = {n: impl_batch._story_key(t) for n, t in list_open_issues(repo, token)}
            for n in in_prog:
                k = num2key.get(n)
                if k:
                    keys.add(k)
    elif target == "gitlab":
        from async_runtime.gitlab_mr import (
            list_closed_issues as gl_closed, list_open_issues as gl_open, list_open_mr_issue_iids,
        )
        base = (creds.get("base_url") or "https://gitlab.com").rstrip("/")
        for _iid, title in gl_closed(base, token, repo):
            k = impl_batch._story_key(title)
            if k:
                keys.add(k)
        in_prog = list_open_mr_issue_iids(base, token, repo)
        if in_prog:
            iid2key = {iid: impl_batch._story_key(t) for iid, t in gl_open(base, token, repo)}
            for iid in in_prog:
                k = iid2key.get(iid)
                if k:
                    keys.add(k)
    return keys


def _implement_persona_provider(reg: Registry, thread_id: str):
    """組 roles pipeline 的 persona 注入器：step(lead/rd/tester/reviewer) → 綁定 agent 的 system_prompt。

    來源是該 thread 生效 workflow 的 agent_bindings["implement"]（binding.role 即步驟名；
    rd 可多綁，取第一個）。無綁定的步驟回 ""（orchestrator 退回內建預設 persona）。
    完全無綁定 → 回 None（零行為改變）。"""
    wf = app.state.engine.active_workflow_for(thread_id)
    raw = wf.agent_bindings.get("implement", ()) if wf else ()
    by_step: dict[str, str] = {}
    for b in raw:
        role = getattr(b, "role", "") or ""
        aid = getattr(b, "agent_id", "") or ""
        if role and aid and role not in by_step:   # 同 step 取第一個綁定
            by_step[role] = aid
    if not by_step:
        return None

    def persona_for(step: str) -> str:
        aid = by_step.get(step)
        if not aid:
            return ""
        spec = resolve_agent(reg, aid)
        return (spec.system_prompt or "").strip() if spec else ""

    return persona_for


def _implement_runner_provider(reg: Registry, thread_id: str):
    """組 roles pipeline 的 per-step runner 注入器：step → 依綁定 agent 的 model_choice 解析 runner。

    model_choice 對應已註冊且可用的 async runner（claude-cli / codex-cli…）→ 回該 runner 實例；
    無綁定 / 無 model_choice / runner 未註冊 / 不可用 → 回 None（該步退回傳入的預設 runner，並 log）。
    純文字 model（如 agy-cli）沒有對應的 implement runner，故自然退回預設 runner。
    完全無綁定 → 回 None（零行為改變）。"""
    wf = app.state.engine.active_workflow_for(thread_id)
    raw = wf.agent_bindings.get("implement", ()) if wf else ()
    model_by_step: dict[str, str] = {}
    for b in raw:
        role = getattr(b, "role", "") or ""
        aid = getattr(b, "agent_id", "") or ""
        if role and aid and role not in model_by_step:
            spec = resolve_agent(reg, aid)
            if spec and spec.model_choice:
                model_by_step[role] = spec.model_choice
    if not model_by_step:
        return None

    def runner_for(step: str):
        mc = model_by_step.get(step)
        if not mc:
            return None
        cls = reg.runners.get(mc)
        if cls is None:
            log.warning("implement step '%s' model '%s' 無對應 async runner，退回預設 runner", step, mc)
            return None
        inst = cls()
        if not inst.is_available():
            log.warning("implement step '%s' runner '%s' 在此環境不可用，退回預設 runner", step, mc)
            return None
        return inst

    return runner_for


@app.post("/api/implement/start", response_model=ImplementStartResponse)
async def implement_start(req: ImplementStartRequest):
    """啟動一次 async 實作 session（非阻塞，立刻回 session_id）。

    runner 由 registry 解析（mock = 安全 dry-run）；hooks 取所有 registered tool hooks；
    story 留空則讀該 thread 的 stories artifact。實際 fix-loop 在背景 task 跑。
    """
    if dal.get_project(req.thread_id) is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{req.thread_id}' 不存在"))
    if impl_dal.has_active_for_thread(req.thread_id):
        raise HTTPException(409, detail=error_detail("impl_in_progress", "此專案已有實作在進行中（共用一份工作目錄），請等它結束或先取消"))
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
    mode = req.mode if req.mode in ("single", "roles") else "single"
    # claude-cli（真實執行）→ 解析專案 delivery repo（new 模式 lazy 建 repo）+ clone + 開 PR。
    is_real = req.runner == "claude-cli"
    # 不做人工審批：預設一律 auto-approve（成功即開 PR）；仍可由請求顯式覆寫
    auto_approve = req.auto_approve if req.auto_approve is not None else True
    target_repo = req.target_repo
    clone_url = ""
    open_pr = None
    if is_real:
        from delivery_repo import DeliveryRepoError, resolve_project_repo
        try:
            target, target_repo = await asyncio.to_thread(resolve_project_repo, reg, req.thread_id)
        except DeliveryRepoError as exc:
            raise HTTPException(400, detail=error_detail("delivery_not_configured", str(exc)))
        creds = keystore.get_credentials(target)
        clone_url = _delivery_clone_url(target, creds, target_repo)
        if not clone_url:
            raise HTTPException(400, detail=error_detail("token_missing", f"{target} token 未設定（到 INTEGRATIONS 設）"))
        open_pr = _delivery_opener(target, creds, req.thread_id)
    persona_for = _implement_persona_provider(reg, req.thread_id) if mode == "roles" else None
    # per-step runner 只在「真實 runner」時套用；mock = 整條 dry-run，不被 per-step 覆蓋
    runner_for = (_implement_runner_provider(reg, req.thread_id)
                  if (mode == "roles" and req.runner != "mock") else None)
    session_id = orchestrator.start_session(
        thread_id=req.thread_id, story=story, runner=runner, runner_choice=req.runner,
        target_repo=target_repo, title=title, hooks=hooks, mode=mode,
        open_pr=open_pr, auto_approve=auto_approve, clone_url=clone_url,
        persona_for=persona_for, runner_for=runner_for,
    )
    return ImplementStartResponse(session_id=session_id)


@app.post("/api/implement/start-batch", response_model=ImplementBatchStartResponse)
async def implement_start_batch(req: ImplementBatchStartRequest):
    """逐 issue 依序實作：把該 thread 的 stories 拆成逐 story，依編號依序、一次一個 issue 實作。

    每個 story 各開 branch/PR、PR 只 `Closes` 對應 issue 並在該 issue 留言；做完才換下一個（QA gate）。
    非阻塞，立刻回 batch_id + 各 story 對應的 session/issue。"""
    if dal.get_project(req.thread_id) is None:
        raise HTTPException(404, detail=error_detail("thread_not_found", f"thread '{req.thread_id}' 不存在"))
    if impl_dal.has_active_for_thread(req.thread_id):
        raise HTTPException(409, detail=error_detail("impl_in_progress", "此專案已有實作在進行中（共用一份工作目錄），請等它結束或先取消"))
    reg = _registry()
    runner_cls = reg.runners.get(req.runner)
    if runner_cls is None:
        raise HTTPException(400, detail=error_detail("runner_not_found", f"runner '{req.runner}' 未註冊"))
    if not runner_cls().is_available():
        raise HTTPException(400, detail=error_detail("runner_unavailable", f"runner '{req.runner}' 在此環境不可用"))
    story_artifact = dal.get_artifact(req.thread_id, "stories") or ""
    if not story_artifact.strip():
        raise HTTPException(400, detail=error_detail("story_empty", "沒有 stories 可實作：先生成 stories"))
    hooks = [h for h in reg.hooks.get("tool", []) if isinstance(h, ToolHook)]
    mode = req.mode if req.mode in ("single", "roles") else "roles"

    is_real = req.runner == "claude-cli"
    target_repo = req.target_repo
    clone_url = ""
    list_issues = None
    build_opener = None
    merge_pr = None
    skip_keys = None
    if is_real:
        from delivery_repo import DeliveryRepoError, resolve_project_repo
        try:
            target, target_repo = await asyncio.to_thread(resolve_project_repo, reg, req.thread_id)
        except DeliveryRepoError as exc:
            raise HTTPException(400, detail=error_detail("delivery_not_configured", str(exc)))
        creds = keystore.get_credentials(target)
        clone_url = _delivery_clone_url(target, creds, target_repo)
        if not clone_url:
            raise HTTPException(400, detail=error_detail("token_missing", f"{target} token 未設定（到 INTEGRATIONS 設）"))
        list_issues = _batch_issue_lister(target, creds, target_repo)

        def build_opener(*, batch_id, issue_number_for, pr_title_for):
            # workdir 指向專案共用 clone dir（batch_id 此處不需要）
            return _batch_opener_builder(target, creds, req.thread_id)(
                issue_number_for=issue_number_for, pr_title_for=pr_title_for)

        # 策略 A：過 gate 即依序 merge（github + gitlab）
        if req.auto_merge and target == "github":
            from async_runtime.github_pr import make_github_pr_merger
            merge_pr = make_github_pr_merger(
                get_token=lambda: keystore.get_credentials("github").get("token", ""),
                repo=target_repo,
                pr_url_for=lambda sid: (impl_dal.get_session(sid) or {}).get("pr_url") or "",
            )
        elif req.auto_merge and target == "gitlab":
            from async_runtime.gitlab_mr import make_gitlab_mr_merger
            merge_pr = make_gitlab_mr_merger(
                get_token=lambda: keystore.get_credentials("gitlab").get("token", ""),
                base_url=(creds.get("base_url") or "https://gitlab.com"),
                repo=target_repo,
                pr_url_for=lambda sid: (impl_dal.get_session(sid) or {}).get("pr_url") or "",
            )
        elif req.auto_merge:
            log.warning("auto_merge 僅支援 github / gitlab（target=%s）→ 不自動 merge", target)

        # 冪等重跑：跳過已完成（issue 已關）/ 進行中（已有 open PR）的 story
        skip_keys = await asyncio.to_thread(_batch_skip_keys, target, creds, target_repo)

    persona_for = _implement_persona_provider(reg, req.thread_id) if mode == "roles" else None
    runner_for = (_implement_runner_provider(reg, req.thread_id)
                  if (mode == "roles" and req.runner != "mock") else None)
    try:
        result = impl_batch.start_batch(
            thread_id=req.thread_id, story_artifact=story_artifact,
            runner_factory=runner_cls, runner_choice=req.runner, mode=mode,
            target_repo=target_repo, clone_url=clone_url, list_issues=list_issues,
            build_opener=build_opener, hooks=hooks, stop_on_failure=req.stop_on_failure,
            persona_for=persona_for, runner_for=runner_for, merge_pr=merge_pr,
            skip_keys=skip_keys,
        )
    except impl_batch.BatchError as exc:
        raise HTTPException(400, detail=error_detail("batch_empty", str(exc)))
    return ImplementBatchStartResponse(
        batch_id=result["batch_id"], total=result["total"],
        skipped=result.get("skipped", 0),
        items=[ImplementBatchItem(**it) for it in result["items"]],
    )


def _batch_response(b: dict) -> ImplementBatchResponse:
    sessions = impl_dal.list_sessions_by_batch(b["batch_id"])
    items = [
        ImplementBatchItem(
            session_id=s["session_id"], story_key=_row_get(s, "story_key", "") or "",
            title=s["title"], issue_number=_row_get(s, "issue_number"),
            status=s["status"], pr_url=s["pr_url"],
        )
        for s in sessions
    ]
    return ImplementBatchResponse(
        batch_id=b["batch_id"], thread_id=b["thread_id"], target_repo=b["target_repo"],
        runner=b["runner"], mode=b["mode"], total=b["total"], status=b["status"],
        stop_on_failure=bool(b["stop_on_failure"]), error_message=b["error_message"],
        created_at=b["created_at"], updated_at=b["updated_at"], items=items,
    )


@app.get("/api/implement/batches/{batch_id}", response_model=ImplementBatchResponse)
async def implement_batch(batch_id: int):
    b = impl_dal.get_batch(batch_id)
    if b is None:
        raise HTTPException(404, detail=error_detail("batch_not_found", f"batch {batch_id} 不存在"))
    return _batch_response(b)


@app.get("/api/implement/threads/{thread_id}/batches", response_model=ImplementBatchListResponse)
async def implement_batches(thread_id: str):
    batches = impl_dal.list_batches(thread_id)
    return ImplementBatchListResponse(batches=[_batch_response(b) for b in batches])


@app.post("/api/implement/batches/{batch_id}/cancel", response_model=ImplementBatchCancelResponse)
async def implement_batch_cancel(batch_id: int):
    if impl_dal.get_batch(batch_id) is None:
        raise HTTPException(404, detail=error_detail("batch_not_found", f"batch {batch_id} 不存在"))
    requested = await impl_batch.request_cancel(batch_id)
    return ImplementBatchCancelResponse(batch_id=batch_id, cancel_requested=requested)


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


@app.post("/api/implement/{session_id}/approve", response_model=ImplementSessionResponse)
async def implement_approve(session_id: int):
    """審批通過 awaiting_approval 的 session → 真實開 PR（冪等：已開過直接回該 session）。"""
    s = impl_dal.get_session(session_id)
    if s is None:
        raise HTTPException(404, detail=error_detail("session_not_found", f"session {session_id} 不存在"))
    if s["pr_url"]:
        return _impl_session_response(s)                      # 冪等：已開過
    if s["status"] != "awaiting_approval":
        raise HTTPException(409, detail=error_detail(
            "not_awaiting_approval", f"session {session_id} 非 awaiting_approval（目前 {s['status']}）"))
    proj = dal.get_project(s["thread_id"]) or {}
    target = (proj.get("delivery_target") or "github").strip()
    creds = keystore.get_credentials(target)
    if not creds.get("token"):
        raise HTTPException(400, detail=error_detail("token_missing", f"{target} token 未設定（到 INTEGRATIONS 設）"))
    opener = _delivery_opener(target, creds, s["thread_id"])
    try:
        pr_url = await asyncio.to_thread(opener, session_id, s["target_repo"], "")
    except RuntimeError as exc:   # PrError / MrError 皆 RuntimeError 子類
        impl_dal.update_session(session_id, status="failed", error_message=str(exc))
        raise HTTPException(502, detail=error_detail("pr_failed", str(exc)))
    impl_dal.update_session(session_id, status="succeeded", pr_url=pr_url)
    return _impl_session_response(impl_dal.get_session(session_id))


@app.post("/api/implement/{session_id}/reject", response_model=ImplementSessionResponse)
async def implement_reject(session_id: int):
    """否決 awaiting_approval 的 session → cancelled（不開 PR；worktree 留待清理）。"""
    s = impl_dal.get_session(session_id)
    if s is None:
        raise HTTPException(404, detail=error_detail("session_not_found", f"session {session_id} 不存在"))
    if s["status"] != "awaiting_approval":
        raise HTTPException(409, detail=error_detail(
            "not_awaiting_approval", f"session {session_id} 非 awaiting_approval（目前 {s['status']}）"))
    impl_dal.update_session(session_id, status="cancelled", error_message="rejected by user")
    return _impl_session_response(impl_dal.get_session(session_id))


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
                         kind=r["kind"], content=r["content"], created_at=r.get("created_at"))
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
        delivery=_last_delivery_status(thread_id, stage_id),
    )


def _last_delivery_status(thread_id: str, stage_id: str) -> Optional[DeliveryStatus]:
    """從 stage_events 取最後一次成功發佈結果（給 UI 的「已發佈」狀態）。"""
    ev = dal.latest_event(thread_id, stage_id, "delivery_published")
    if not ev:
        return None
    try:
        d = json.loads(ev.get("detail") or "{}")
    except (ValueError, TypeError):
        d = {}
    return DeliveryStatus(
        target=d.get("target", ""), repo=d.get("repo", ""),
        count=int(d.get("count", 0)), created=int(d.get("created", 0)),
        published_at=ev.get("created_at"),
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
