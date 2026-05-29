"""__PLUGIN_NAME__ register —— 註冊一個 stage。

複製本範本後：
1. 全域替換 __PLUGIN_ID__ / __PLUGIN_NAME__ / __STAGE_ID__
2. 改 prompts/stage.md 的內容
3. 視需要設 depends_on（上游 stage_id），host 會自動擋缺失 + reset 下游
4. 把整個目錄放進 backend/plugins/__PLUGIN_ID__/ 並重啟 backend
"""
from __future__ import annotations

from plugin_api import PluginHost, StageContext, StageResult, StageSpec


def _generate(ctx: StageContext, run) -> StageResult:
    prompt = run.render_prompt("stage.md", {
        # TODO: 填你的佔位符；ctx.upstream_artifacts 取得上游 stage 的 artifact
        "CONVERSATION": "\n\n".join(f"{r}: {c}" for r, c in ctx.conversation) or "(none)",
    })
    result = run.harnessed_step(
        telemetry_stage="__STAGE_ID__", operation="generate___STAGE_ID__",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    return StageResult(artifact=result.raw_output.strip(),
                       telemetry_metadata={"run_id": result.run_id})


STAGE = StageSpec(
    id="__STAGE_ID__",
    label="TODO: stage 顯示名稱",
    icon="document",
    telemetry_stage="__STAGE_ID__",
    generate_operation="generate___STAGE_ID__",
    depends_on=(),              # TODO: e.g. ("prd",) 表示依賴 PRD
    artifact_key="__STAGE_ID__",
    prompt_keys=("stage.md",),
    generate=_generate,
    supports_chat=False,
)


def register(host: PluginHost) -> None:
    host.register_stage(STAGE)
    # 可選：host.register_validator("__STAGE_ID__", "generate___STAGE_ID__", my_validator_fn)
