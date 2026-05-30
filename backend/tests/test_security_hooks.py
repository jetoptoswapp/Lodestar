"""坎3 安全：--disallowedTools 下沉 tool-call 層、RedactSecretsHook 塗黑、
worktree 隔離、孤兒 session resume。"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from plugin_api import HookAbort
from plugins.builtin_implement.hooks import DenyProtectedBranchHook, RedactSecretsHook
from plugins.builtin_implement.runner import ClaudeCliRunner


def test_claude_runner_disallows_push_and_pr():
    argv = ClaudeCliRunner().build_argv(cwd="/tmp/x", prompt="p")
    assert "--disallowedTools" in argv
    joined = " ".join(argv)
    assert "git push" in joined and "gh pr" in joined and "git remote" in joined


def test_redact_secrets_hook():
    h = RedactSecretsHook()
    assert "ghp_" not in h.on_log_chunk("r", "token ghp_" + "a" * 36)
    assert "[REDACTED]" in h.on_log_chunk("r", "Authorization: Bearer abc.def-123")
    assert "[REDACTED]" in h.on_log_chunk("r", "api_key=supersecretvalue")


def test_deny_protected_branch_second_line():
    h = DenyProtectedBranchHook()
    with pytest.raises(HookAbort):
        h.pre_run("r", ["git", "push", "origin", "main"], {})
    assert h.pre_run("r", ["ls", "-la"], {}) is None      # 無關命令放行


def test_orphaned_session_recovery(tmp_db):
    from async_runtime import impl_dal
    sid = impl_dal.create_session(thread_id="t", title="x", target_repo="", runner="mock")
    impl_dal.update_session(sid, status="running")
    n = impl_dal.fail_orphaned_running()
    assert n >= 1
    assert impl_dal.get_session(sid)["status"] == "failed"


def test_prepare_worktree_no_base_returns_plain_dir(tmp_db):
    from async_runtime import orchestrator
    d = orchestrator.prepare_worktree(901, base_repo="")   # 無 base → 預設空目錄行為
    assert d.exists()


@pytest.mark.skipif(not shutil.which("git"), reason="git 不可用")
def test_prepare_worktree_isolation(tmp_db, tmp_path):
    from async_runtime import orchestrator
    base = tmp_path / "base"
    base.mkdir()
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    for cmd in (["git", "init"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=base, check=True, capture_output=True)
    (base / "f.txt").write_text("hi")
    subprocess.run(["git", "add", "."], cwd=base, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=base, check=True, capture_output=True)

    wt = orchestrator.prepare_worktree(902, base_repo=str(base))
    assert wt.exists() and (wt / "f.txt").exists()        # worktree 含 base 內容
    branch = subprocess.run(["git", "-C", str(wt), "branch", "--show-current"],
                            capture_output=True, text=True).stdout.strip()
    assert branch == "lodestar/impl-902"                  # 獨立 branch
