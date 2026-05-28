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
from plugin_api.workflow import WorkflowSpec

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
        """thread 綁的 workflow → 或 'default' → 或 lazy 全 stage（無 workflow 時）。"""
        project = dal.get_project(thread_id)
        wf_id = project["workflow_id"] if project else None
        if wf_id and wf_id in self._reg.workflows:
            return self._reg.workflows[wf_id]
        if "default" in self._reg.workflows:
            return self._reg.workflows["default"]
        # 無 workflow（M0 狀態 / 全新環境）：用 registry 所有 stage 組 lazy workflow
        return WorkflowSpec(
            id="__lazy__", label="Lazy",
            stages=tuple(self._reg.stages.keys()),
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

        # 3. conversation history（chat / refine 用）
        conv: tuple = ()
        if op in ("chat", "refine"):
            msgs = dal.list_messages(thread_id, stage_id, limit=50)
            conv = tuple((m["role"], m["content"]) for m in msgs)

        # 4. StageContext + HarnessRunner
        ctx = StageContext(
            thread_id=thread_id,
            stage_id=stage_id,
            model_choice=model_choice,
            instruction=instruction if op == "refine" else "",
            upstream_artifacts=upstream,
            current_artifact=current,
            conversation=conv,
            focus_section=focus_section,
            metadata={},
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
                if stage.generate is None:
                    raise OperationNotSupportedError(stage_id, op)
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
