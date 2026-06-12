"""ui_design stage：StageSpec + generate / refine / chat handlers + warn-only validator。

孿生自 change_request_stage（四檔 prompt + sentinel 模式），差別：
  - depends_on=("prd",)：與 architecture 平行，上游 PRD 經 ctx.upstream_artifacts 注入。
  - 產出「UI 設計稿」：設計理念 + design tokens + 每畫面 `## Screen: <名稱>` 內嵌一個
    自包含 HTML 原型（前端抽 ```html fence 用 sandboxed iframe 預覽）。
  - sentinel `[UI_READY]`；structural validator（Screen / html fence / tokens / 禁用字體）warn-only。

persona/契約分離：_DEFAULT_UI_DESIGNER_PERSONA 可被 /agents 覆寫；文件結構、HTML 自包含
規範、questionnaire、sentinel 全在 prompts/ui_design_*.md，改人設不會改壞前端解析。
"""
from __future__ import annotations

import re
from typing import Tuple

from plugin_api import (
    HarnessValidationOutcome,
    SEVERITY_WARN,
    StageChatResult,
    StageContext,
    StageResult,
    StageSpec,
)
from plugin_api.harness import HarnessContext

from ._shared import (
    effective_persona,
    format_attachments as _format_attachments,
    format_conversation as _shared_format_conversation,
    format_focus_section,
    render_skills_block,
)


# stage 內建 persona（agent.system_prompt 未設時的 default）。
# 設計信條取自 Anthropic frontend-design skill：大膽方向、有個性字體、tokens 先行、
# CSS 動效、反 generic AI slop。
_DEFAULT_UI_DESIGNER_PERSONA = (
    "你是一位資深 UI/UX 設計師兼創意前端工程師。你的工作：依據 PRD 產出一份「瀏覽器可直接"
    "渲染」的 UI 設計稿——整體設計理念、design tokens，以及每個關鍵畫面一份自包含 HTML 原型。\n"
    "\n"
    "你的設計信條（每一稿都必須做到）：\n"
    "1. 先立一個大膽、明確、一句話講得出來的美學方向（例：「編輯部風格的冷靜權威」、"
    "「瑞士網格 × 暖紙質感」、「霓虹工業風」），所有畫面貫徹同一方向；方向要能回溯到 PRD "
    "的產品個性與目標用戶。\n"
    "2. 字體要有態度：標題與內文用對比鮮明的配對（如 display serif × geometric sans），"
    "從 Google Fonts 挑有個性的字體。嚴禁 Inter、Roboto、Arial、Helvetica 與 system-ui "
    "等預設字體堆疊——用了它們就是放棄設計。\n"
    "3. Design tokens 先行：色彩、字級、間距、圓角、陰影一律收斂成 :root 的 CSS variables，"
    "所有畫面共用同一組 tokens；色彩給語意名（--ink、--accent），不要 --blue-500 式裸名。\n"
    "4. 動效純 CSS：進場用 staggered reveal（依序延遲的淡入/位移）、hover 微互動、克制的 "
    "transition；不依賴 JS 動畫庫，JS 只允許少量原生互動（tab、menu 開合）。\n"
    "5. 構圖拒絕安全牌：避免「置中等寬卡片流」。用非對稱版面、元素重疊、出血圖、誇張的"
    "字級對比製造張力；留白是設計的一部分，不是沒做完。\n"
    "6. 背景要有氛圍：gradient mesh、noise/grain 質感、細緻幾何 pattern 至少擇一融入，"
    "拒絕死白或死黑的平面底。\n"
    "7. 嚴禁 generic AI slop：紫色漸層配白底、千篇一律的 rounded 玻璃卡片、無理由的 "
    "glassmorphism、emoji 充當 icon——一律不准出現。\n"
    "8. 為真實內容設計：用 PRD 的領域語言寫有真實感的文案與資料（不用 Lorem Ipsum）；"
    "主要畫面至少示意一種非理想狀態（空態、載入或錯誤）。"
)

_UI_READY_SENTINEL = "[UI_READY]"

# generate 與 chat 共用 ui_design_chat.md，以 TASK_DIRECTIVE 區分（chat 留空）。
_GENERATE_DIRECTIVE = (
    "Now produce the COMPLETE UI design document following the format rules above "
    "(Design Direction, Design Tokens, then every key screen as `## Screen:` with one "
    "self-contained ```html prototype). Do not ask further questions unless the PRD is "
    "fundamentally ambiguous. End with [UI_READY]."
)


def _strip_sentinel(text: str) -> Tuple[str, bool]:
    """從 model 輸出移除 [UI_READY]，回 (清洗後內容, 是否標記完成)。"""
    if _UI_READY_SENTINEL in text:
        return text.replace(_UI_READY_SENTINEL, "").rstrip(), True
    return text, False


def _format_conversation(conv: tuple) -> str:
    return _shared_format_conversation(conv, ai_label="Designer")


def _system_prompt(ctx: StageContext, run) -> str:
    return run.render_prompt("ui_design_system.md", {
        "PERSONA": effective_persona(ctx, _DEFAULT_UI_DESIGNER_PERSONA),
        "SKILLS": render_skills_block(ctx.agent.skills if ctx.agent else ()),
    })


def _upstream_prd(ctx: StageContext) -> str:
    return ctx.upstream_artifacts.get("prd", "")


# ============================================================
#  Handlers
# ============================================================
def _ui_design_generate(ctx: StageContext, run) -> StageResult:
    """generate：PRD + 對話 → 完整 UI 設計稿（tokens + 每畫面 HTML 原型）。"""
    prompt = run.render_prompt("ui_design_chat.md", {
        "SYSTEM_PROMPT": _system_prompt(ctx, run),
        "PRD_DRAFT": _upstream_prd(ctx),
        "CONVERSATION_TEXT": _format_conversation(ctx.conversation),
        "FOCUS_SECTION": format_focus_section(ctx.focus_section),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
        "TASK_DIRECTIVE": _GENERATE_DIRECTIVE,
    })
    result = run.harnessed_step(
        telemetry_stage="design", operation="generate_ui_design",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    artifact, ready = _strip_sentinel(result.raw_output.strip())
    return StageResult(
        artifact=artifact,
        telemetry_metadata={"run_id": result.run_id, "ui_ready": ready},
        state_extra={"ui_ready": True} if ready else {},
    )


def _ui_design_refine(ctx: StageContext, run) -> StageResult:
    """refine：基於現有設計稿 + instruction，輸出完整更新版（含未改畫面）。"""
    prompt = run.render_prompt("ui_design_refine.md", {
        "PRD_DRAFT": _upstream_prd(ctx),
        "UI_DESIGN_DRAFT": ctx.current_artifact or "(empty)",
        "INSTRUCTION": ctx.instruction or "",
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
    })
    result = run.harnessed_step(
        telemetry_stage="design", operation="refine_ui_design",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    artifact, ready = _strip_sentinel(result.raw_output.strip())
    return StageResult(
        artifact=artifact,
        telemetry_metadata={"run_id": result.run_id, "ui_ready": ready},
        state_extra={"ui_ready": True} if ready else {},
    )


def _ui_design_chat(ctx: StageContext, run) -> StageChatResult:
    """chat：討論視覺方向／釐清。設計稿已存在則前綴 amendment_prefix。
    Sentinel `[UI_READY]` 出現 → 視為設計稿完成、回 updated_artifact。"""
    sys_prompt = _system_prompt(ctx, run)
    if ctx.current_artifact and ctx.current_artifact.strip():
        amendment = run.render_prompt("ui_design_amendment_prefix.md", {
            "CURRENT_DESIGN": ctx.current_artifact,
        })
        sys_prompt = sys_prompt + "\n\n" + amendment

    prompt = run.render_prompt("ui_design_chat.md", {
        "SYSTEM_PROMPT": sys_prompt,
        "PRD_DRAFT": _upstream_prd(ctx),
        "CONVERSATION_TEXT": _format_conversation(ctx.conversation),
        "FOCUS_SECTION": format_focus_section(ctx.focus_section),
        "ATTACHMENTS": _format_attachments(ctx.metadata.get("attachments", [])),
        "TASK_DIRECTIVE": "",
    })
    result = run.harnessed_step(
        telemetry_stage="design", operation="chat_ui_design",
        prompt=prompt, metadata={"thread_id": ctx.thread_id},
        max_iterations=1,
    )
    reply, ready = _strip_sentinel(result.raw_output.strip())
    return StageChatResult(
        reply=reply,
        updated_artifact=reply if ready else None,
    )


# ============================================================
#  Validator —— structural sanity（warn-only）
# ============================================================
_SCREEN_HEADING = re.compile(r"(?m)^##\s+Screen\s*[:：]\s*\S")
_HTML_FENCE = re.compile(r"```\s*html\b", re.IGNORECASE)
_TOKENS_HINT = re.compile(r"(?im)(^#+\s*design\s+tokens|^#+\s*設計\s*tokens?|:root\s*\{)")
_GENERIC_FONT = re.compile(
    r"(?i)font-family[^;{}]*\b(Inter|Roboto|Arial|Helvetica|system-ui)\b"
)


def _ui_design_structural_validator(
    artifact: str, _ctx: HarnessContext,
) -> list[HarnessValidationOutcome]:
    """檢查 UI 設計稿結構（前端 parser / iframe 預覽依賴）。warn-only。"""
    outcomes: list[HarnessValidationOutcome] = []

    screens = _SCREEN_HEADING.findall(artifact)
    fences = _HTML_FENCE.findall(artifact)

    if not screens:
        outcomes.append(HarnessValidationOutcome(
            validator="ui_design.has_screen", severity=SEVERITY_WARN,
            message="缺少 `## Screen: <名稱>` 畫面章節（前端分頁籤依賴）",
            fix_hint="每個畫面用「## Screen: <畫面名稱>」H2 標題開一節（字面 Screen + 冒號）",
        ))
    if not fences:
        outcomes.append(HarnessValidationOutcome(
            validator="ui_design.has_html_fence", severity=SEVERITY_WARN,
            message="缺少 ```html 原型 code fence（iframe 預覽依賴）",
            fix_hint="每個 Screen 章節內放恰好一個 ```html fence，內容為自包含 HTML（<!DOCTYPE html> 起手）",
        ))
    if screens and fences and len(fences) < len(screens):
        outcomes.append(HarnessValidationOutcome(
            validator="ui_design.screen_missing_html", severity=SEVERITY_WARN,
            message=f"有 {len(screens)} 個 Screen 但只有 {len(fences)} 個 ```html 原型——部分畫面缺原型",
            fix_hint="補齊每個 Screen 的 ```html fence；注意 HTML 內不可出現三連反引號（會截斷 fence）",
        ))
    if not _TOKENS_HINT.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="ui_design.has_design_tokens", severity=SEVERITY_WARN,
            message="缺少 Design Tokens 章節或 :root CSS variables",
            fix_hint="加「## Design Tokens」章節，含 ```css fence 的 :root { --ink: ...; --accent: ...; } 全套變數",
        ))
    if _GENERIC_FONT.search(artifact):
        outcomes.append(HarnessValidationOutcome(
            validator="ui_design.generic_font", severity=SEVERITY_WARN,
            message="偵測到 Inter/Roboto/Arial/Helvetica/system-ui 等預設字體（違反設計信條）",
            fix_hint="改用 Google Fonts 上有個性的字體配對（display × body 對比），並更新 tokens 與各畫面 font-family",
        ))
    return outcomes


# ============================================================
#  StageSpec + Validators registry
# ============================================================
UI_DESIGN_STAGE = StageSpec(
    id="ui_design",
    label="UI 設計",
    description="依 PRD 產出 UI 設計稿：設計理念、design tokens 與每個畫面可直接渲染的自包含 HTML 原型。",
    icon="palette",
    telemetry_stage="design",
    generate_operation="generate_ui_design",
    refine_operation="refine_ui_design",
    chat_operation="chat_ui_design",
    depends_on=("prd",),
    artifact_key="ui_design",
    prompt_keys=(
        "ui_design_system.md", "ui_design_chat.md",
        "ui_design_refine.md", "ui_design_amendment_prefix.md",
    ),
    default_agent_role="ui_design",
    generate=_ui_design_generate,
    refine=_ui_design_refine,
    chat=_ui_design_chat,
    supports_chat=True,
    on_complete_state_extra={},
)


VALIDATORS = [
    ("design", "generate_ui_design", _ui_design_structural_validator),
    ("design", "refine_ui_design",   _ui_design_structural_validator),
]
