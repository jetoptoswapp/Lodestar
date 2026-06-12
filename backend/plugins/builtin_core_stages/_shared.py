"""builtin_core_stages 內共用 helper：對話 / 附件 / chat content block 解析。

不放 plugin_api（只 plugin 內部用）；prd / architecture / stories handlers 共用。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple


# ============================================================
#  Conversation formatter（user/assistant → 可讀字串）
# ============================================================
def format_conversation(conv: tuple, *, ai_label: str = "AI") -> str:
    """把 ((role, content), ...) 轉成 prompt 內可讀的對話形式。

    ai_label：non-user role 顯示名（SA / Architect / PM）。
    """
    if not conv:
        return "(no prior conversation)"
    lines: list[str] = []
    for role, content in conv:
        speaker = "User" if role == "user" else ai_label
        lines.append(f"{speaker}:\n{content}")
    return "\n\n".join(lines)


# ============================================================
#  Collab discussion 前綴（§6.4）—— generate 收到注入的多方發言時前綴進 prompt
# ============================================================
def collab_discussion_prefix(conversation: tuple) -> str:
    """collab 模式下 coordinator 會把 peer/subagent 發言以 conversation 注入 generate。

    回傳要前綴進 prompt 的「多方討論」區塊；conversation 為空（單代理模式）→ 回 ""（no-op，
    行為與原本完全一致）。
    """
    if not conversation:
        return ""
    body = format_conversation(conversation, ai_label="Specialist")
    return (
        "## 多方討論（specialists 的觀點）\n"
        f"{body}\n\n"
        "請整合上述各方觀點，再依本階段規範產出最終、完整的成果。\n\n"
        "---\n\n"
    )


# ============================================================
#  Attachments block（M1.3 path-passing；M1.1 inline fallback）
# ============================================================
def format_attachments(attachments: list) -> str:
    """把 ctx.metadata['attachments'] 渲染成 prompt block。

    M1.3：每筆 attachment 有 abs_path → path-list + READ 指令（claude-cli + Read tool）。
    Fallback：缺 abs_path → 退回 inline parsed_text marker（M1.1 行為）。
    """
    if not attachments:
        return "(no attached files)"

    all_have_path = all(a.get("abs_path") for a in attachments)
    if all_have_path:
        lines: list[str] = [
            "READ the following files NOW with the Read tool BEFORE answering.",
            "Images use native vision; PDFs and DOCX read via the same Read tool.",
            "Treat their contents as primary reference material; cite them where relevant.",
            "",
        ]
        for a in attachments:
            fname = a.get("filename", "(unnamed)")
            mime = a.get("mime") or "unknown"
            size = a.get("size_bytes", 0)
            lines.append(
                f"- {a['abs_path']}  ·  original={fname}  ·  mime={mime}  ·  {size} bytes"
            )
        return "\n".join(lines)

    blocks: list[str] = []
    for a in attachments:
        fname = a.get("filename", "(unnamed)")
        mime = a.get("mime") or "unknown"
        size = a.get("size_bytes", 0)
        header = f"<<< attachment: {fname}  ·  mime={mime}  ·  {size} bytes >>>"
        body = a.get("parsed_text") or f"[未解析：{a.get('parse_error') or 'unsupported'}]"
        blocks.append(f"{header}\n{body}\n<<< end of {fname} >>>")
    return "\n\n".join(blocks)


# ============================================================
#  Workspace block（既有 repo path-passing；讀碼 stage 用）
# ============================================================
def format_workspace(workspace_dir: str) -> str:
    """把 ctx.workspace_dir 渲染成 prompt block，指示 model 先讀既有 codebase 再回答。

    workspace_dir 空（非讀碼 stage / 未設 repo）→ 回 ""（no-op）。
    非空 → path + Read/Grep/Glob 探索指令（claude-cli adapter 已 --add-dir 此目錄 + 補唯讀工具）。
    """
    if not workspace_dir:
        return ""
    return (
        "--- Existing codebase ---\n"
        f"The existing repository is checked out at: {workspace_dir}\n"
        "EXPLORE it NOW with the Read / Grep / Glob tools BEFORE answering: read the README, "
        "map the project structure, and locate the files/areas the requested change or bug touches.\n"
        "Ground every statement in the ACTUAL code you read — reference real file paths and symbols, "
        "never invent structure that isn't there.\n"
        "--- End of codebase ---"
    )


# ============================================================
#  Chat content-block 解析 —— [CONTENT_START]...[CONTENT_END]
# ============================================================
_CONTENT_BLOCK_RE = re.compile(
    r"\[CONTENT_START\]\s*\n?(.*?)\n?\s*\[CONTENT_END\]",
    re.DOTALL,
)


def extract_content_block(text: str) -> Tuple[str, Optional[str]]:
    """解 `[CONTENT_START]...[CONTENT_END]`（arch_chat / stories_chat 協定）。

    回 (reply, updated_artifact)：
    - 有標記 → reply 是去掉整個 block 後的剩餘對話；updated_artifact 是 block 內容
    - 無標記 → reply 是整段；updated_artifact = None
    """
    m = _CONTENT_BLOCK_RE.search(text)
    if not m:
        return text.strip(), None
    updated = m.group(1).strip()
    reply = (text[: m.start()] + text[m.end() :]).strip()
    return reply, updated


# ============================================================
#  Focus block 統一格式
# ============================================================
def format_focus_section(focus_section: Optional[str]) -> str:
    """聚焦段落（FOCUS_SECTION）標準寫法。"""
    return f"\n[Focus on section: {focus_section}]\n" if focus_section else ""


# ============================================================
#  Persona 注入（agent.system_prompt 接回單流程；persona/契約分離）
# ============================================================
def effective_persona(ctx, default_persona: str) -> str:
    """單流程 system prompt 的 persona 段：ctx.agent.system_prompt（使用者在 /agents 編的）
    優先，空則用 stage 內建 default_persona。

    機器契約（questionnaire / PRD Format / heading shape / [PRD_READY] sentinel 等）留在
    各 stage 的 .md，不放進 persona —— 使用者改人設不會改壞前端解析與 publish pipeline。
    collab lead 合成時 ctx.agent 為 None（見 collab_coordinator），故走 default。
    """
    agent = getattr(ctx, "agent", None)
    if agent is not None and (agent.system_prompt or "").strip():
        return agent.system_prompt.strip()
    return default_persona


# ============================================================
#  Skills 注入（獨立 SKILLS 區塊；persona 之後、機器契約之前）
# ============================================================
def render_skills_block(skills: tuple) -> str:
    """把 agent 綁定的 skills（依序）組成獨立 SKILLS 區塊字串。

    R1 迴歸守門：空 skills（或全部 body 空）→ 回 ""；呼叫端把 {{SKILLS}} 替成空字串後，
    render 結果與接線前逐字相同（.md 寫成 `{{PERSONA}}\\n\\n{{SKILLS}}<契約>`，空時塌回原樣）。
    非空時自帶尾端 "\\n\\n"，與下方機器契約隔開。body 是機器無關的 prompt 片段（不含契約）。
    """
    if not skills:
        return ""
    parts = ["## Skills (apply the following capabilities)"]
    for s in skills:
        body = (s.body or "").strip()
        if not body:
            continue
        parts.append(f"### {s.name}\n{body}")
    if len(parts) == 1:          # 全部 skill body 為空 → 視同無 skill
        return ""
    return "\n\n".join(parts) + "\n\n"
