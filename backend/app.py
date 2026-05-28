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

import plugin_loader  # noqa: E402
from api_models import (  # noqa: E402
    PluginListResponse,
    PluginProvides,
    PluginResponse,
    StageCatalogItem,
    StageCatalogResponse,
)
from persistence import dal, migrations  # noqa: E402
from plugin_host import (  # noqa: E402
    CAP_AGENT,
    CAP_INTEGRATION,
    CAP_STAGE,
    CAP_WORKFLOW,
    Registry,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
log = logging.getLogger("app")

FRONTEND_PORT = 8724


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrations.migrate()
    registry = plugin_loader.load_all()
    app.state.registry = registry
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
