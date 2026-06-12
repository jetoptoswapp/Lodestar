"""修改既有專案（modify_existing）：workspace 備料 + change_request stage + 讀 issue。

證明：
- change_request stage / modify_existing workflow 有註冊。
- requires=("workspace",) 的 stage 在 repo 未設時 dispatch → workspace_not_configured（4xx）。
- repo 已設 + token 有 → host clone（這裡 monkeypatch clone 不打真網路），workspace_dir 流到 prompt
  與 adapter（invoke 收到 workspace_dir）。
- get_issue_detail 解析 github / gitlab JSON。
"""
from __future__ import annotations

import json
import urllib.request

import pytest

import plugin_loader as L
from plugin_api import ModelAdapter
from persistence import dal
from workflow_engine import WorkflowEngine, WorkspaceNotConfiguredError


def _capture(reg, choice="claude-cli"):
    """把 model_choice 換成捕捉 fake adapter（接受 workspace_dir）；回記錄 list。"""
    log: list[dict] = []

    def _invoke(p, *, allowed_tools=(), workspace_dir=""):
        log.append({"prompt": p, "allowed_tools": allowed_tools, "workspace_dir": workspace_dir})
        return "# Implementation Brief\n## 1. Summary\nfix it [BRIEF_READY]"

    reg.model_adapters[choice] = ModelAdapter(
        model_choice=choice, invoke=_invoke, is_available=lambda: True,
        description="cap", max_context_tokens=100000,
        prompt_budget_tokens=90000, response_budget_tokens=2000)
    return log


def test_change_request_stage_and_workflow_registered(tmp_db):
    reg = L.load_all()
    assert "change_request" in reg.stages
    assert "modify_existing" in reg.workflows
    s = reg.stages["change_request"]
    assert s.requires == ("workspace",)
    assert reg.workflows["modify_existing"].stages == ("change_request",)


def test_workspace_required_but_repo_unset_raises(tmp_db):
    """modify_existing 專案未設既有 repo → dispatch change_request → WorkspaceNotConfiguredError（4xx）。"""
    reg = L.load_all()
    _capture(reg)
    dal.create_project("m1", "proj", "modify_existing")   # 無 delivery repo
    with pytest.raises(WorkspaceNotConfiguredError) as ei:
        WorkflowEngine(reg).dispatch(thread_id="m1", stage_id="change_request", op="generate")
    assert ei.value.status_code == 400
    assert ei.value.category == "workspace_not_configured"


def test_workspace_clone_flows_to_prompt_and_adapter(tmp_db, monkeypatch):
    """repo 已設 + token 有 → host clone（monkeypatch 不打網路）→ workspace_dir 進 prompt + adapter。"""
    reg = L.load_all()
    log = _capture(reg)
    dal.create_project("m2", "proj", "modify_existing",
                       delivery_target="github", repo_mode="existing",
                       repo_full_name="owner/repo")
    # token：monkeypatch keystore.get_credentials
    import keystore
    monkeypatch.setattr(keystore, "get_credentials", lambda target: {"token": "T"})
    # clone：monkeypatch repo_workspace.prepare_project_clone（回固定路徑，不打網路）
    import repo_workspace
    monkeypatch.setattr(repo_workspace, "prepare_project_clone",
                        lambda thread_id, url: "/tmp/fake-clone/" + thread_id)

    out = WorkflowEngine(reg).dispatch(thread_id="m2", stage_id="change_request", op="generate")
    assert out["error_code"] == ""
    rec = log[-1]
    assert rec["workspace_dir"] == "/tmp/fake-clone/m2"          # 流到 adapter
    assert "/tmp/fake-clone/m2" in rec["prompt"]                 # 流到 prompt（format_workspace）
    assert "Read / Grep / Glob" in rec["prompt"]                # 讀碼指令
    assert "Read" in rec["allowed_tools"]                       # agent 宣告的工具


def test_get_issue_detail_github(monkeypatch):
    from async_runtime import github_pr

    class _Resp:
        def __init__(self, payload): self._p = json.dumps(payload).encode()
        def read(self): return self._p
        def __enter__(self): return self
        def __exit__(self, *a): return False

    payload = {"number": 7, "title": "Bug X", "body": "steps...", "html_url": "http://gh/7"}
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=20: _Resp(payload))
    d = github_pr.get_issue_detail("owner/repo", "T", 7)
    assert d == {"number": 7, "title": "Bug X", "body": "steps...", "url": "http://gh/7"}


def test_get_issue_detail_github_rejects_pr(monkeypatch):
    from async_runtime import github_pr

    class _Resp:
        def __init__(self, payload): self._p = json.dumps(payload).encode()
        def read(self): return self._p
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=20: _Resp({"number": 9, "pull_request": {}}))
    with pytest.raises(RuntimeError):
        github_pr.get_issue_detail("owner/repo", "T", 9)


def test_get_issue_detail_gitlab(monkeypatch):
    from async_runtime import gitlab_mr

    class _Resp:
        def __init__(self, payload): self._p = json.dumps(payload).encode()
        def read(self): return self._p
        def __enter__(self): return self
        def __exit__(self, *a): return False

    payload = {"iid": 3, "title": "Feat Y", "description": "do it", "web_url": "http://gl/3"}
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=20: _Resp(payload))
    d = gitlab_mr.get_issue_detail("https://gitlab.com", "T", "grp/proj", 3)
    assert d == {"number": 3, "title": "Feat Y", "body": "do it", "url": "http://gl/3"}
