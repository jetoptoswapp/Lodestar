"""WorkflowEngine —— 純 Python，無 graph。

spec §7：core 內零 stage 名稱硬編碼；依賴與下游 reset 完全從 spec/workflow 推導。
host owns all I/O —— plugin handler 回傳 StageResult，由 engine 統一寫 artifact /
reset 下游 / 記 revision / 寫 event。
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Optional

from plugin_api import StageContext, SEVERITY_FAIL
from plugin_api.stage import StageChatResult, StageResult
from plugin_api.workflow import WorkflowSpec, normalize_bindings

from harness_runner import HarnessRunner
from agent_resolver import resolve_lead_agent
from persistence import dal
from plugin_host import Registry

log = logging.getLogger("workflow_engine")


# ============================================================
#  Exceptions（由 endpoint 層 catch 並轉為 HTTPException）
# ============================================================
class WorkflowError(Exception):
    """Base — 帶 category（呼應 api_errors.error_detail）+ status_code。"""
    category = "workflow_error"
    status_code = 400


class StageNotFoundError(WorkflowError):
    category = "stage_not_found"
    status_code = 404

    def __init__(self, stage_id: str) -> None:
        super().__init__(f"stage '{stage_id}' 未註冊")


class StageNotInWorkflowError(WorkflowError):
    category = "stage_not_in_workflow"

    def __init__(self, stage_id: str, workflow_id: str) -> None:
        super().__init__(f"stage '{stage_id}' 不在 workflow '{workflow_id}' 中")


class MissingDependencyError(WorkflowError):
    """上游 artifact 缺失（spec §11：缺則 4xx + 明確訊息）。"""

    def __init__(self, stage_id: str, missing_upstream: str) -> None:
        super().__init__(f"'{missing_upstream}' 必須先完成")
        self.stage_id = stage_id
        self.missing_upstream = missing_upstream
        self.category = f"missing_{missing_upstream}"


class OperationNotSupportedError(WorkflowError):
    category = "operation_not_supported"

    def __init__(self, stage_id: str, op: str) -> None:
        super().__init__(f"stage '{stage_id}' 不支援 op '{op}'")


class WorkspaceNotConfiguredError(WorkflowError):
    """stage 宣告 requires=("workspace",) 但專案未設既有 repo / 缺 token，無法 clone 既有 codebase。"""
    category = "workspace_not_configured"

    def __init__(self, stage_id: str, reason: str) -> None:
        super().__init__(f"stage '{stage_id}' 需要既有 repo 但無法取得：{reason}")
        self.stage_id = stage_id


# ============================================================
#  Pure helpers（測試友善）
# ============================================================
def compute_dependencies(
    workflow: WorkflowSpec, stage_registry: dict[str, Any]
) -> dict[str, list[str]]:
    """每個 stage 的上游清單。edges_override 優先；否則 StageSpec.depends_on。
    只保留在 workflow.stages 內的（防止跨 workflow 殘留依賴）。
    """
    deps: dict[str, list[str]] = {}
    for sid in workflow.stages:
        if sid in workflow.edges_override:
            ups = workflow.edges_override[sid]
        else:
            spec = stage_registry.get(sid)
            ups = spec.depends_on if spec else ()
        deps[sid] = [d for d in ups if d in workflow.stages]
    return deps


def downstream_of(stage_id: str, deps: dict[str, list[str]]) -> list[str]:
    """反推所有 transitive 下游。回拓樸序的 list（distinct）。"""
    reverse: dict[str, list[str]] = defaultdict(list)
    for sid, ups in deps.items():
        for up in ups:
            reverse[up].append(sid)
    out: list[str] = []
    seen: set[str] = set()
    queue: list[str] = list(reverse.get(stage_id, []))
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        for nxt in reverse.get(cur, []):
            if nxt not in seen:
                queue.append(nxt)
    return out


def _spec_to_stage_rows(workflow: WorkflowSpec, stage_registry: dict[str, Any]) -> list[dict]:
    """WorkflowSpec → workflow_definitions.stages_json 的 list（_user_workflow_to_spec 的反向）。
    depends_on 用 compute_dependencies 取（builtin spec 多半不設 edges_override，靠 StageSpec.depends_on）。"""
    deps = compute_dependencies(workflow, stage_registry)
    rows: list[dict] = []
    for sid in workflow.stages:
        bindings = workflow.agent_bindings.get(sid, ())
        rows.append({
            "stage_id": sid,
            "depends_on": list(deps.get(sid, [])),
            "agent_bindings": [{"agent_id": b.agent_id, "role": b.role} for b in bindings],
            "collab_mode": workflow.collab_mode.get(sid, "single"),
        })
    return rows


def seed_builtin_workflows(registry) -> int:
    """把 in-memory builtin workflow 冪等 seed 進 workflow_definitions DB，讓預設 workflow 跟自訂的一樣
    可在 /workflows 編輯。已存在同 id 則跳過（永不蓋使用者編輯）；in-memory 仍留作 fallback。回 seed 筆數。
    在 plugin 載入後呼叫（builtin WorkflowSpec 此時才在 registry 內）。"""
    seeded = 0
    for wf_id, spec in registry.workflows.items():
        if dal.get_workflow_definition(wf_id) is not None:
            continue
        dal.upsert_workflow_definition(
            wf_id=wf_id, label=spec.label, description=spec.description,
            stages=_spec_to_stage_rows(spec, registry.stages),
            source_plugin=spec.source_plugin or "builtin",
        )
        seeded += 1
    return seeded


# ============================================================
#  Engine
# ============================================================
class WorkflowEngine:
    """純 Python，無 graph。app.state 持有單一 instance（與 Registry 綁定）。"""

    def __init__(self, registry: Registry) -> None:
        self._reg = registry

    # ------- workflow resolution -------
    def active_workflow_for(self, thread_id: str) -> WorkflowSpec:
        """thread 綁的 workflow → DB（含 seed 的 builtin，可編輯）→ in-memory builtin fallback → default → lazy。

        順序（builtin 已於啟動 seed 進 DB，故 DB 優先讓編輯生效）：
          1. project.workflow_id 對應 DB workflow_definitions → 即時轉成 WorkflowSpec
          2. project.workflow_id 對應 in-memory builtin（DB row 被刪時的 fallback）
          3. 'default'（DB 優先，再 in-memory）
          4. lazy 全 stage（M0 fallback）
        """
        from plugin_api.workflow import AgentBinding

        project = dal.get_project(thread_id)
        wf_id = project["workflow_id"] if project else None

        if wf_id:
            # DB 優先（builtin 已 seed 進 DB → 編輯生效）；in-memory 僅作 fallback（DB row 被刪時）。
            user_wf = dal.get_workflow_definition(wf_id)
            if user_wf is not None:
                return self._user_workflow_to_spec(user_wf)
            if wf_id in self._reg.workflows:
                return self._reg.workflows[wf_id]

        default_wf = dal.get_workflow_definition("default")
        if default_wf is not None:
            return self._user_workflow_to_spec(default_wf)
        if "default" in self._reg.workflows:
            return self._reg.workflows["default"]

        # 無 workflow（M0 狀態 / 全新環境）：用 registry 所有 stage 組 lazy workflow
        return WorkflowSpec(
            id="__lazy__", label="Lazy",
            stages=tuple(self._reg.stages.keys()),
        )

    @staticmethod
    def _user_workflow_to_spec(d: dict) -> WorkflowSpec:
        """把 DB workflow_definitions row（含 stages_json 解析後的 list）轉 WorkflowSpec。"""
        from plugin_api.workflow import AgentBinding
        stages_payload = d.get("stages") or []
        stage_ids = tuple(s["stage_id"] for s in stages_payload)
        edges: dict[str, tuple[str, ...]] = {}
        bindings: dict[str, tuple[AgentBinding, ...]] = {}
        collab: dict[str, str] = {}
        for s in stages_payload:
            sid = s["stage_id"]
            if s.get("depends_on"):
                edges[sid] = tuple(s["depends_on"])
            ab = s.get("agent_bindings") or []
            if ab:
                bindings[sid] = tuple(
                    AgentBinding(agent_id=b["agent_id"], role=b.get("role", "lead"))
                    for b in ab if b.get("agent_id")
                )
            mode = s.get("collab_mode", "single")
            if mode and mode != "single":
                collab[sid] = mode
        return WorkflowSpec(
            id=d["id"], label=d["label"], description=d.get("description", ""),
            stages=stage_ids, edges_override=edges,
            agent_bindings=bindings, collab_mode=collab,
            source_plugin="user",
        )

    def _judge_model_choice(self) -> str:
        """judge 用的 model_choice。M1 預設回 ""（judge 關閉：預設不跑、零成本、零行為改變）；
        未來從 app_settings 讀 per-stage / 全域 judge model 開關以啟用語義驗證。"""
        return ""

    def _prepare_workspace(self, thread_id: str, stage) -> str:
        """stage 宣告 requires=("workspace",) → clone 既有 repo，回 clone 絕對路徑（host owns I/O）。

        未宣告 → 回 ""（行為不變）。專案未設既有 repo / 缺 token → WorkspaceNotConfiguredError（4xx）。
        clone 為「一專案一份」、冪等（已存在則 fetch 沿用），與 implement 端共用同一目錄。
        core 只認 requires tuple、不認 stage 名（鐵則①）。"""
        requires = getattr(stage, "requires", ())
        # build_verify 等需要「implement 的成果」：直接給 implement 寫碼的快照/clone（local 與 remote
        # 都寫 project_clone_dir）。與唯讀讀碼的 "workspace"（local 模式回原始路徑）區分。
        if "impl_workspace" in requires:
            import repo_workspace
            return str(repo_workspace.project_clone_dir(thread_id))
        if "workspace" not in requires:
            return ""
        import keystore
        import repo_workspace
        from delivery_repo import DeliveryRepoError, clone_url, resolve_project_repo
        try:
            target, repo_full = resolve_project_repo(self._reg, thread_id, create=False)
        except DeliveryRepoError as exc:
            raise WorkspaceNotConfiguredError(stage.id, str(exc)) from exc
        if target == "local":
            # 本機模式：sync 讀碼直接唯讀讀本機原始路徑（不快照 → 與 async 不共用目錄、無競態，永遠看到最新 WIP）。
            # repo_full 此時是 resolve_project_repo 回傳的本機絕對路徑。
            from pathlib import Path as _Path
            if not _Path(repo_full).is_dir():
                raise WorkspaceNotConfiguredError(stage.id, f"local_path 不是資料夾或不存在: {repo_full}")
            return repo_full
        creds = keystore.get_credentials(target)
        url = clone_url(target, creds, repo_full)
        if not url:
            raise WorkspaceNotConfiguredError(
                stage.id, f"integration '{target}' 尚無 token（先到 INTEGRATIONS 設定）")
        try:
            clone_path = repo_workspace.prepare_project_clone(thread_id, url)
        except Exception as exc:  # noqa: BLE001 - clone 失敗（網路 / 權限）轉 4xx，不回顯 token url
            raise WorkspaceNotConfiguredError(stage.id, "git clone 既有 repo 失敗") from exc
        return str(clone_path)

    # ------- dispatch -------
    def dispatch(
        self, *, thread_id: str, stage_id: str, op: str,
        model_choice: str = "claude-cli",
        instruction: str = "",
        user_input: str = "",
        focus_section: Optional[str] = None,
    ) -> dict:
        """執行 generate / refine / chat。回 dict（artifact / reply / state_extra / downstream_reset / error_*）。"""
        if op not in ("generate", "refine", "chat"):
            raise OperationNotSupportedError(stage_id, op)

        # 1. workflow + stage spec
        workflow = self.active_workflow_for(thread_id)
        stage = self._reg.stages.get(stage_id)
        if stage is None:
            raise StageNotFoundError(stage_id)
        if stage_id not in workflow.stages:
            raise StageNotInWorkflowError(stage_id, workflow.id)

        # 2. 上游 artifacts（缺則 4xx，靠 exception 上拋）
        deps = compute_dependencies(workflow, self._reg.stages)
        upstream: dict[str, str] = {}
        for up in deps.get(stage_id, []):
            art = dal.get_artifact(thread_id, up)
            if not art or not art.strip():
                raise MissingDependencyError(stage_id, up)
            upstream[up] = art

        current = dal.get_artifact(thread_id, stage_id) or ""

        # 3. conversation history —— chat / refine / generate 皆載入。
        #    generate 也載入：讓「先在 chat 描述需求 → 生成」能納入對話（修正先前 generate 忽略對話），
        #    且 collab 討論的 peers/lead 看得到既有需求脈絡。無對話時為 ()，行為不變。
        conv: tuple = ()
        if op in ("chat", "refine", "generate"):
            msgs = dal.list_messages(thread_id, stage_id, limit=50)
            conv = tuple((m["role"], m["content"]) for m in msgs)
        # chat 的當前 user_input 必須讓 handler 看到：DB append 在下方 chat 分支（handler 之後），
        # 故先把它併進 in-memory conv —— 否則第一次 chat 時 handler 收到空對話，SA 會誤回「沒收到需求」。
        if op == "chat" and user_input:
            conv = conv + (("user", user_input),)

        # 4. 附件（M1.1 inline / M1.3 path-passing 並存）
        #    - 注入 abs_path：plugin 不必碰 dal / FS 也能告訴 model 去 Read。
        #    - 仍保留 parsed_text：給未來不支援 Read tool 的 adapter inline 退路。
        raw_attachments = dal.list_attachments(thread_id, stage_id)
        uploads_root = dal.uploads_dir()
        attachments: list[dict] = []
        for row in raw_attachments:
            item = dict(row)
            cpath = item.get("content_path")
            if cpath:
                item["abs_path"] = str(uploads_root / cpath)
            attachments.append(item)

        # 5. agent 解析（單一 lead）：供單流程注入 ctx.agent / runner。
        #    collab（discussion/dispatch）另由 coordinator 解析各 agent；這裡解析的是 lead。
        bindings = normalize_bindings(workflow.agent_bindings.get(stage_id, ()))
        lead_agent = resolve_lead_agent(
            self._reg, stage_id,
            default_agent_role=stage.default_agent_role, bindings=bindings,
        )
        # model_choice：lead agent 指定且該 adapter 已註冊 → 用 agent 的；否則沿用傳入值（+warn）。
        eff_model = model_choice
        if lead_agent and lead_agent.model_choice and lead_agent.model_choice != model_choice:
            if lead_agent.model_choice in self._reg.model_adapters:
                eff_model = lead_agent.model_choice
            else:
                log.warning("stage '%s' lead '%s' 指定 model '%s' 未註冊，沿用 '%s'",
                            stage_id, lead_agent.agent_id, lead_agent.model_choice, model_choice)

        # 5b. workspace（既有 repo clone）：stage 宣告 requires=("workspace",) 才備（host owns I/O）。
        workspace_dir = self._prepare_workspace(thread_id, stage)

        # 6. StageContext + HarnessRunner
        md: dict = {"attachments": attachments}
        # impl_workspace 類 stage（build_verify）需要專案 build 設定 → host 注入 ctx.metadata
        # （plugin 不得讀 DB；host owns I/O）。
        if "impl_workspace" in getattr(stage, "requires", ()):
            proj = dal.get_project(thread_id) or {}
            md["build_command"] = proj.get("build_command", "")
            md["build_env_script"] = proj.get("build_env_script", "")
        ctx = StageContext(
            thread_id=thread_id,
            stage_id=stage_id,
            model_choice=eff_model,
            instruction=instruction if op == "refine" else "",
            upstream_artifacts=upstream,
            current_artifact=current,
            conversation=conv,
            focus_section=focus_section,
            metadata=md,
            agent=lead_agent,
            workspace_dir=workspace_dir,
        )
        runner = HarnessRunner(self._reg, thread_id, stage_id, eff_model,
                               judge_model_choice=self._judge_model_choice(),
                               agent=lead_agent, workspace_dir=workspace_dir)

        # 5. 派發 handler
        new_artifact = current
        state_extra: dict = {}
        reply: Optional[str] = None
        error_code = ""
        error_message = ""

        try:
            if op == "generate":
                # §6.4 collab：多 binding + discussion/dispatch → 交 coordinator 合成 artifact
                # （bindings 已在 agent 解析時算過；此處沿用，不重算）
                mode = workflow.collab_mode.get(stage_id, "single")
                if mode in ("discussion", "dispatch") and len(bindings) > 1:
                    from collab_coordinator import run_collab
                    new_artifact = run_collab(
                        self._reg, thread_id=thread_id, stage=stage, ctx=ctx,
                        model_choice=model_choice, bindings=bindings, mode=mode,
                    )
                    state_extra = {}
                elif stage.generate is None:
                    raise OperationNotSupportedError(stage_id, op)
                else:
                    res: StageResult = stage.generate(ctx, runner)
                    new_artifact = res.artifact
                    state_extra = dict(res.state_extra or {})
            elif op == "refine":
                if stage.refine is None:
                    raise OperationNotSupportedError(stage_id, op)
                res = stage.refine(ctx, runner)
                new_artifact = res.artifact
                state_extra = dict(res.state_extra or {})
            else:  # chat
                if stage.chat is None or not stage.supports_chat:
                    raise OperationNotSupportedError(stage_id, op)
                cres: StageChatResult = stage.chat(ctx, runner)
                reply = cres.reply
                # 寫 chat 對話（user 先、assistant 後）
                if user_input:
                    dal.append_message(thread_id, stage_id, "user", user_input)
                dal.append_message(thread_id, stage_id, "assistant", reply)
                if cres.updated_artifact:
                    new_artifact = cres.updated_artifact
        except WorkflowError:
            raise
        except RuntimeError as exc:
            # adapter / harness 錯誤 → 不上拋，包成 error fields
            error_code = "harness.internal"
            error_message = str(exc)
            log.warning("dispatch %s/%s/%s harness error: %s",
                        thread_id, stage_id, op, exc)
        except Exception as exc:  # noqa: BLE001
            error_code = "harness.internal"
            error_message = repr(exc)
            log.exception("dispatch %s/%s/%s unexpected", thread_id, stage_id, op)

        # harness 把 model 錯誤吞進 result（不 raise）、handler 只取 raw_output → 錯誤會被默默吃掉、
        # 前端只收到空回覆（「沒反應」）。這裡補抓 runner 記下的 model/harness 錯誤，讓它如實上報。
        if not error_code:
            rc = getattr(runner, "_last_error_code", "")
            if rc:
                error_code = rc
                error_message = getattr(runner, "_last_error_message", "")

        # 本次 harnessed_step 的 validations（collab 分支不經此 runner → 空）。
        # has_fail：judge 等 fail 級 validator 未通過（含跑滿 fix-loop 仍未解）。
        validations = list(getattr(runner, "_last_validations", []))
        has_fail = any(o.severity == SEVERITY_FAIL for o in validations)

        # 6. host owns I/O：寫 artifact / reset 下游 / 記 revision
        downstream_reset: list[str] = []
        if new_artifact and new_artifact != current and not error_code:
            dal.upsert_artifact(thread_id, stage_id, new_artifact)
            downstream_reset = downstream_of(stage_id, deps)
            for d in downstream_reset:
                if dal.get_stage_status(thread_id, d) == "approved":
                    dal.set_stage_status(thread_id, d, "needs_revision")
            dal.add_revision(
                thread_id, stage_id,
                source=f"{op}_{stage_id}",
                instruction=instruction or user_input,
                downstream_reset=downstream_reset,
                content_length=len(new_artifact),
            )

        # 7. status：generate/refine 成功 → needs_revision（judge fail 未解）或 draft（已 approved 者保留）
        if not error_code and op in ("generate", "refine"):
            if dal.get_stage_status(thread_id, stage_id) != "approved":
                dal.set_stage_status(thread_id, stage_id,
                                     "needs_revision" if has_fail else "draft")

        # 8. on_complete_state_extra（spec §6.1：stage 個性 side-effect 寫進 event）
        if state_extra and not error_code:
            dal.append_event(
                thread_id, stage_id,
                event_type="state_extra",
                detail=json.dumps(state_extra, ensure_ascii=False),
            )

        # 9. progress event
        if error_code:
            dal.append_event(
                thread_id, stage_id, event_type="error",
                detail=json.dumps({"code": error_code, "message": error_message}, ensure_ascii=False),
            )
        else:
            dal.append_event(thread_id, stage_id, event_type=op, detail="")

        return {
            "artifact": new_artifact,
            "state_extra": state_extra,
            "reply": reply,
            "downstream_reset": downstream_reset,
            "error_code": error_code,
            "error_message": error_message,
            "validations": [
                {"validator": o.validator, "severity": o.severity, "message": o.message,
                 "fix_hint": o.fix_hint, "detail": o.detail}
                for o in validations
            ],
        }
