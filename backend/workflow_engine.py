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

from plugin_api import StageContext
from plugin_api.stage import StageChatResult, StageResult
from plugin_api.workflow import WorkflowSpec, normalize_bindings

from harness_runner import HarnessRunner
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


# ============================================================
#  Engine
# ============================================================
class WorkflowEngine:
    """純 Python，無 graph。app.state 持有單一 instance（與 Registry 綁定）。"""

    def __init__(self, registry: Registry) -> None:
        self._reg = registry

    # ------- workflow resolution -------
    def active_workflow_for(self, thread_id: str) -> WorkflowSpec:
        """thread 綁的 workflow → builtin in-memory → user-defined（DB）→ default → lazy。

        M3：user-defined workflow（DB workflow_definitions 表）也納入解析鏈。
        順序：
          1. project.workflow_id 對應 builtin registry
          2. project.workflow_id 對應 DB user workflow → 即時轉成 WorkflowSpec
          3. 'default' builtin
          4. lazy 全 stage（M0 fallback）
        """
        from plugin_api.workflow import AgentBinding

        project = dal.get_project(thread_id)
        wf_id = project["workflow_id"] if project else None

        if wf_id:
            if wf_id in self._reg.workflows:
                return self._reg.workflows[wf_id]
            user_wf = dal.get_workflow_definition(wf_id)
            if user_wf is not None:
                return self._user_workflow_to_spec(user_wf)

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

        # 5. StageContext + HarnessRunner
        ctx = StageContext(
            thread_id=thread_id,
            stage_id=stage_id,
            model_choice=model_choice,
            instruction=instruction if op == "refine" else "",
            upstream_artifacts=upstream,
            current_artifact=current,
            conversation=conv,
            focus_section=focus_section,
            metadata={"attachments": attachments},
        )
        runner = HarnessRunner(self._reg, thread_id, stage_id, model_choice)

        # 5. 派發 handler
        new_artifact = current
        state_extra: dict = {}
        reply: Optional[str] = None
        error_code = ""
        error_message = ""

        try:
            if op == "generate":
                # §6.4 collab：多 binding + discussion/dispatch → 交 coordinator 合成 artifact
                bindings = normalize_bindings(workflow.agent_bindings.get(stage_id, ()))
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

        # 7. status：generate/refine 成功 → 至少 draft（已 approved 者保留）
        if not error_code and op in ("generate", "refine"):
            if dal.get_stage_status(thread_id, stage_id) != "approved":
                dal.set_stage_status(thread_id, stage_id, "draft")

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
        }
