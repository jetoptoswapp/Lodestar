"""Agent tools 接線：agent.tools → adapter.invoke(allowed_tools)，含附件回歸守門與向後相容。"""
from __future__ import annotations

import plugin_loader as L
from plugin_api import ModelAdapter
from persistence import dal
from workflow_engine import WorkflowEngine

_PRD_OUT = ("# PRD\n## 3. Functional Requirements\nFR-1 x\n"
            "## 4. Non-Functional Requirements\nNFR-1 y")


def _capturing_adapter(choice, cap, response="ok"):
    def fn(prompt, *, allowed_tools=()):
        cap["allowed_tools"] = allowed_tools
        cap["prompt"] = prompt
        return response
    return ModelAdapter(model_choice=choice, invoke=fn, is_available=lambda: True,
                        description="cap", max_context_tokens=100000,
                        prompt_budget_tokens=90000, response_budget_tokens=2000)


def test_agent_tools_flow_to_invoke(tmp_db):
    """編輯 seed_prd 的 tools → 單流程 generate 時 adapter.invoke 收到該 allowed_tools。"""
    reg = L.load_all()
    cap = {}
    reg.model_adapters["claude-cli"] = _capturing_adapter("claude-cli", cap, _PRD_OUT)
    dal.upsert_agent(agent_id="seed_prd", name="SA", role="prd", system_prompt="x",
                     model_choice="claude-cli", max_iterations=1, enabled=True,
                     tools=["Read", "Bash"])
    dal.create_project("t1", "proj")
    out = WorkflowEngine(reg).dispatch(thread_id="t1", stage_id="prd", op="generate")
    assert out["error_code"] == ""
    assert cap["allowed_tools"] == ("Read", "Bash")


def test_no_tools_passes_empty(tmp_db):
    """seed 預設 tools=() → invoke 收到空 allowed_tools（行為與接線前一致）。"""
    reg = L.load_all()
    cap = {}
    reg.model_adapters["claude-cli"] = _capturing_adapter("claude-cli", cap, _PRD_OUT)
    dal.create_project("t2", "proj")
    out = WorkflowEngine(reg).dispatch(thread_id="t2", stage_id="prd", op="generate")
    assert out["error_code"] == ""
    assert cap["allowed_tools"] == ()


def test_claude_tool_flags_attachment_read(tmp_path, monkeypatch):
    """附件回歸守門：uploads dir 存在時，即使 agent.tools=() 也補 Read（並合併、去重）。"""
    from plugins.builtin_models import claude_cli as cc
    monkeypatch.setenv(cc._UPLOADS_ENV, str(tmp_path))

    # agent.tools=() 但有附件 → 補 Read + --add-dir
    flags = cc._tool_flags(())
    assert "--allowedTools" in flags
    assert "Read" in flags[flags.index("--allowedTools") + 1]
    assert "--add-dir" in flags

    # agent.tools=("Bash",) + 附件 → 合併 Read + Bash
    joined = (lambda f: f[f.index("--allowedTools") + 1])(cc._tool_flags(("Bash",)))
    assert "Read" in joined and "Bash" in joined

    # agent 已含 Read → 去重，不出現 Read,Read
    joined = (lambda f: f[f.index("--allowedTools") + 1])(cc._tool_flags(("Read",)))
    assert joined.split(",").count("Read") == 1


def test_parse_stream_json_joins_all_turns():
    """大型輸出跨多個 assistant 輪次 → 串接全部 text（修前段截斷的核心）。

    `--output-format text` 只回最後一輪會丟前段（標題 + 前面 Epic）；stream-json 逐輪接回。"""
    import json as _json
    from plugins.builtin_models.claude_cli import _parse_stream_json

    def asst(text):
        return _json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})

    lines = [
        _json.dumps({"type": "system", "subtype": "init"}),
        asst("# Proj — User Stories\n\n## Epic 1: 基礎\n### Story 1.1 — scaffold\n"),
        # tool_use block 不取文字；user/tool_result 略過
        _json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {}}]}}),
        _json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}}),
        asst("## Epic 2: 進階\n### Story 2.1 — feature\n"),
        _json.dumps({"type": "result", "subtype": "success", "result": "## Epic 2: 進階\n### Story 2.1 — feature\n"}),
    ]
    out = _parse_stream_json("\n".join(lines))
    # 兩輪 assistant text 都在（不是只剩最後一輪 / result 的尾段）
    assert out.startswith("# Proj — User Stories")        # 前段標題保留
    assert "## Epic 1: 基礎" in out and "### Story 1.1" in out
    assert "## Epic 2: 進階" in out and "### Story 2.1" in out
    assert "tool_use" not in out and "Read" not in out    # tool block 不混入


def test_parse_stream_json_robust_to_noise():
    """非 JSON 行 / 無 assistant → 不爆、回空字串。"""
    from plugins.builtin_models.claude_cli import _parse_stream_json
    assert _parse_stream_json("") == ""
    assert _parse_stream_json("not json\n{bad\n") == ""


def test_claude_tool_flags_no_uploads(tmp_path, monkeypatch):
    """無 uploads dir：無 tools → 純文字（空 flags）；有 agent tools → 帶工具但不補 Read、不加 --add-dir。"""
    from plugins.builtin_models import claude_cli as cc
    monkeypatch.delenv(cc._UPLOADS_ENV, raising=False)

    assert cc._tool_flags(()) == []

    flags = cc._tool_flags(("Bash",))
    joined = flags[flags.index("--allowedTools") + 1]
    assert "Bash" in joined and "Read" not in joined
    assert "--add-dir" not in flags


def test_invoke_adapter_backward_compat():
    """相容偵測：舊式只收 prompt 的 adapter + 非空 allowed_tools → 不報錯、走只傳 prompt。"""
    from harness_runner import _invoke_accepts as accepts, _invoke_adapter

    assert accepts(lambda p, *, allowed_tools=(): p, "allowed_tools") is True
    assert accepts(lambda p, **kw: p, "allowed_tools") is True
    assert accepts(lambda p: p, "allowed_tools") is False

    old_calls = []
    old = ModelAdapter(model_choice="old", invoke=lambda p: (old_calls.append(p) or "ok"),
                       is_available=lambda: True, description="", max_context_tokens=1,
                       prompt_budget_tokens=1, response_budget_tokens=1)
    assert _invoke_adapter(old, "hi", ("Read",)) == "ok"   # 舊式 + 工具 → 不爆
    assert old_calls == ["hi"]

    seen = {}
    def _new_invoke(p, *, allowed_tools=()):
        seen["t"] = allowed_tools
        return "ok"
    new = ModelAdapter(model_choice="new", invoke=_new_invoke, is_available=lambda: True,
                       description="", max_context_tokens=1, prompt_budget_tokens=1,
                       response_budget_tokens=1)
    _invoke_adapter(new, "hi", ("Read", "Bash"))
    assert seen["t"] == ("Read", "Bash")
    seen.clear()
    _invoke_adapter(new, "hi", ())                          # 空 → 走只傳 prompt（用預設）
    assert seen["t"] == ()


def test_invoke_adapter_workspace_dir():
    """workspace_dir 透傳：adapter 接受才帶；不接受則靜默降級（不爆）。"""
    from harness_runner import _invoke_accepts as accepts, _invoke_adapter

    assert accepts(lambda p, *, workspace_dir="": p, "workspace_dir") is True
    assert accepts(lambda p, *, allowed_tools=(): p, "workspace_dir") is False

    seen = {}
    def _ws_invoke(p, *, allowed_tools=(), workspace_dir=""):
        seen["t"] = allowed_tools
        seen["w"] = workspace_dir
        return "ok"
    ws = ModelAdapter(model_choice="ws", invoke=_ws_invoke, is_available=lambda: True,
                      description="", max_context_tokens=1, prompt_budget_tokens=1,
                      response_budget_tokens=1)
    _invoke_adapter(ws, "hi", ("Read",), workspace_dir="/tmp/repo")
    assert seen["t"] == ("Read",) and seen["w"] == "/tmp/repo"

    # 舊式 adapter（只收 allowed_tools）+ 非空 workspace_dir → 不帶 workspace_dir、不爆
    seen.clear()
    def _tools_only(p, *, allowed_tools=()):
        seen["t"] = allowed_tools
        return "ok"
    legacy = ModelAdapter(model_choice="legacy", invoke=_tools_only, is_available=lambda: True,
                          description="", max_context_tokens=1, prompt_budget_tokens=1,
                          response_budget_tokens=1)
    assert _invoke_adapter(legacy, "hi", ("Read",), workspace_dir="/tmp/repo") == "ok"
    assert seen["t"] == ("Read",)


def test_collab_agent_tools_flow(tmp_db):
    """collab（discussion）各 agent 的 tools 經 _runner 流到 invoke（rca agents 都帶 Read）。"""
    reg = L.load_all()
    captured = []

    def _invoke(p, *, allowed_tools=()):
        captured.append(allowed_tools)
        return "## Candidate Root Causes\n| 1 | x | high | e | c |"
    reg.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli", invoke=_invoke, is_available=lambda: True,
        description="cap", max_context_tokens=100000, prompt_budget_tokens=90000,
        response_budget_tokens=2000)
    dal.create_project("tc", "panel", workflow_id="rca_panel")
    dal.upsert_artifact("tc", "rca_intake", "anomaly")
    WorkflowEngine(reg).dispatch(thread_id="tc", stage_id="rca_analysis", op="generate")
    assert any(t == ("Read",) for t in captured)            # rca agents tools=("Read",) 流到 invoke
