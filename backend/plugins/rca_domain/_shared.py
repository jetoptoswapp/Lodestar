"""rca_domain 內共用 helper：對話 / 附件 / chat content block 解析。

與 builtin_core_stages/_shared.py 同源（plugin 內部用、不放 plugin_api）。
RCA 各 stage handler 共用。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple


# ============================================================
#  Conversation formatter（user/assistant → 可讀字串）
# ============================================================
def format_conversation(conv: tuple, *, ai_label: str = "RCA") -> str:
    """把 ((role, content), ...) 轉成 prompt 內可讀的對話形式。"""
    if not conv:
        return "(no prior conversation)"
    lines: list[str] = []
    for role, content in conv:
        speaker = "Engineer" if role == "user" else ai_label
        lines.append(f"{speaker}:\n{content}")
    return "\n\n".join(lines)


def collab_discussion_prefix(conversation: tuple) -> str:
    """collab（§6.4）：coordinator 把 peer/subagent 發言以 conversation 注入 generate 時，
    前綴成「多方討論」區塊；單代理模式（空）→ 回 ""（no-op，行為不變）。"""
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

    每筆有 abs_path → path-list + READ 指令（claude-cli + Read tool 原生讀 CSV/PDF/圖）；
    缺 abs_path → 退回 inline parsed_text marker。
    """
    if not attachments:
        return "(no attached files)"

    all_have_path = all(a.get("abs_path") for a in attachments)
    if all_have_path:
        lines: list[str] = [
            "READ the following files NOW with the Read tool BEFORE answering.",
            "CSV / data files, PDFs and DOCX read via the Read tool; images use native vision.",
            "Treat their contents as primary evidence; cite specific rows / columns / trends.",
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
#  Chat content-block 解析 —— [CONTENT_START]...[CONTENT_END]
# ============================================================
_CONTENT_BLOCK_RE = re.compile(
    r"\[CONTENT_START\]\s*\n?(.*?)\n?\s*\[CONTENT_END\]",
    re.DOTALL,
)


def extract_content_block(text: str) -> Tuple[str, Optional[str]]:
    """解 `[CONTENT_START]...[CONTENT_END]`。

    回 (reply, updated_artifact)：
    - 有標記 → reply 是去掉整個 block 後的剩餘對話；updated_artifact 是 block 內容
    - 無標記 → reply 是整段；updated_artifact = None
    """
    m = _CONTENT_BLOCK_RE.search(text)
    if not m:
        return text.strip(), None
    updated = m.group(1).strip()
    reply = (text[: m.start()] + text[m.end():]).strip()
    return reply, updated
