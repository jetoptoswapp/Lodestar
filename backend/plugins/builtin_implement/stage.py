"""implement stage spec —— 純 catalog / stepper marker（無 sync AI handler）。

實作是 async 長時工作，由 /api/implement/* 觸發、前端用 custom renderer 呈現，
不走 generic stage 的 generate/refine/chat（三者皆 None → dispatch 會回 OperationNotSupported）。
depends_on=("stories",) 讓它天然排在故事之後、上游未完成時 stepper 顯示 locked。
"""
from __future__ import annotations

from plugin_api import StageSpec

IMPLEMENT_STAGE = StageSpec(
    id="implement",
    label="自動實作",
    description="（async）依使用者故事派實作 agent 自動寫 code 並開 PR。",
    icon="rocket",
    telemetry_stage="implement",
    depends_on=("stories",),
    artifact_key="implement",
    generate=None,
    refine=None,
    chat=None,
    supports_chat=False,
)
