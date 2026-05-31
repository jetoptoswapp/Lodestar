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
    from harness_runner import _invoke_accepts_allowed_tools as accepts, _invoke_adapter

    assert accepts(lambda p, *, allowed_tools=(): p) is True
    assert accepts(lambda p, **kw: p) is True
    assert accepts(lambda p: p) is False

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
