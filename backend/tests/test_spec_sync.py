"""規格同步到 code repo（spec_sync）：把 PRD/架構/UI + CLAUDE.md commit 進 code repo。

git 操作用 fake subprocess（不打網路）。驗證：
- clone code repo → 寫 .lodestar/*.md + CLAUDE.md（managed block）→ commit → push default branch。
- CLAUDE.md managed block：既有檔只換 block、不清掉使用者內容；無 block 則前置。
- 無變更（冪等重同步）→ 不產空 commit、仍回 ok。
- 錯誤：缺 token / clone 失敗 / push 被拒（友善訊息、不外洩 token）。
- endpoint：缺 prd/arch 400、happy path 200 + files 清單。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as appmod
import repo_workspace
import spec_sync
from persistence import dal

_REMOTE = "https://x-access-token:TOKEN@github.com/owner/repo.git"
_FILES = {".lodestar/PRD.md": "# PRD\n\nFR-1", ".lodestar/ARCHITECTURE.md": "# Arch\n\nT0"}
_BLOCK = "# Proj\n\n規矩在這。"


class _FakeProc:
    def __init__(self, rc: int, stdout: str = "", stderr: str = ""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def _fake_git(*, clone_ok: bool = True, push_ok: bool = True, branch: str = "main",
              has_changes: bool = True, seed_claude: str | None = None):
    """回 (fake_run, calls)。clone 成功時建出 dest/.git（可選 seed 既有 CLAUDE.md）。"""
    calls: list[list[str]] = []

    def run(cmd, capture_output=True, text=True, timeout=None):
        calls.append(list(cmd))
        if cmd[:2] == ["git", "clone"]:
            if not clone_ok:
                return _FakeProc(1, stderr="not found")
            dest = Path(cmd[-1])
            (dest / ".git").mkdir(parents=True, exist_ok=True)
            if seed_claude is not None:
                (dest / "CLAUDE.md").write_text(seed_claude, encoding="utf-8")
            return _FakeProc(0)
        sub = cmd[3] if (len(cmd) > 3 and cmd[1] == "-C") else ""
        if sub == "push":
            return _FakeProc(0 if push_ok else 1, stderr="" if push_ok else "protected")
        if sub == "rev-parse":
            return _FakeProc(0, stdout=branch + "\n")
        if sub == "diff":                       # diff --cached --quiet：rc1=有變更 → 會 commit
            return _FakeProc(1 if has_changes else 0)
        return _FakeProc(0)

    return run, calls


# ============================================================
#  merge_managed_block（純函式）
# ============================================================
def test_merge_block_into_empty():
    out = spec_sync.merge_managed_block("", "RULES")
    assert out.startswith("<!-- LODESTAR:BEGIN -->")
    assert "RULES" in out and "<!-- LODESTAR:END -->" in out


def test_merge_block_preserves_existing_user_content():
    existing = "# 使用者自己的 CLAUDE.md\n\n保留我\n"
    out = spec_sync.merge_managed_block(existing, "LODESTAR_RULES")
    assert "保留我" in out                         # 不清掉使用者內容
    assert "LODESTAR_RULES" in out


def test_merge_block_replaces_old_block_idempotent():
    first = spec_sync.merge_managed_block("使用者內容", "V1")
    second = spec_sync.merge_managed_block(first, "V2")
    assert "V2" in second and "V1" not in second   # 只換 block
    assert "使用者內容" in second
    assert second.count("<!-- LODESTAR:BEGIN -->") == 1   # 不重複堆疊


# ============================================================
#  sync_specs（fake git）
# ============================================================
def test_sync_writes_files_and_claude_md(tmp_db, monkeypatch):
    run, calls = _fake_git()
    monkeypatch.setattr(spec_sync.subprocess, "run", run)
    out = spec_sync.sync_specs("t1", _REMOTE, _FILES, web_url="https://github.com/owner/repo",
                               claude_md_block=_BLOCK)
    assert out["ok"] is True
    assert set(out["files"]) == {".lodestar/PRD.md", ".lodestar/ARCHITECTURE.md", "CLAUDE.md"}
    dest = repo_workspace.project_dir("t1") / "spec_sync"
    assert (dest / ".lodestar" / "PRD.md").read_text(encoding="utf-8") == _FILES[".lodestar/PRD.md"]
    claude = (dest / "CLAUDE.md").read_text(encoding="utf-8")
    assert "<!-- LODESTAR:BEGIN -->" in claude and "規矩在這" in claude
    assert any(len(c) > 3 and c[3] == "push" for c in calls)


def test_sync_preserves_existing_claude_md(tmp_db, monkeypatch):
    run, _ = _fake_git(seed_claude="# 既有 CLAUDE\n\n別動我\n")
    monkeypatch.setattr(spec_sync.subprocess, "run", run)
    spec_sync.sync_specs("t2", _REMOTE, _FILES, web_url="x", claude_md_block=_BLOCK)
    claude = (repo_workspace.project_dir("t2") / "spec_sync" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "別動我" in claude                       # 既有內容保留
    assert "規矩在這" in claude                       # Lodestar block 也在


def test_sync_no_changes_skips_commit(tmp_db, monkeypatch):
    run, calls = _fake_git(has_changes=False)
    monkeypatch.setattr(spec_sync.subprocess, "run", run)
    out = spec_sync.sync_specs("t3", _REMOTE, _FILES, web_url="x", claude_md_block=_BLOCK)
    assert out["ok"] is True
    assert not any(len(c) > 3 and c[3] == "commit" for c in calls)   # 無變更不 commit
    assert not any(len(c) > 3 and c[3] == "push" for c in calls)


def test_sync_requires_token(tmp_db):
    with pytest.raises(spec_sync.SpecSyncError):
        spec_sync.sync_specs("t4", "", _FILES, web_url="x")   # remote 空 = 缺 token


def test_sync_clone_fail_friendly(tmp_db, monkeypatch):
    run, _ = _fake_git(clone_ok=False)
    monkeypatch.setattr(spec_sync.subprocess, "run", run)
    with pytest.raises(spec_sync.SpecSyncError) as ei:
        spec_sync.sync_specs("t5", _REMOTE, _FILES, web_url="x")
    assert "TOKEN" not in str(ei.value)


def test_sync_push_fail_friendly_no_token_leak(tmp_db, monkeypatch):
    run, _ = _fake_git(push_ok=False)
    monkeypatch.setattr(spec_sync.subprocess, "run", run)
    with pytest.raises(spec_sync.SpecSyncError) as ei:
        spec_sync.sync_specs("t6", _REMOTE, _FILES, web_url="x", claude_md_block=_BLOCK)
    msg = str(ei.value)
    assert "TOKEN" not in msg and "protected" not in msg
    assert "main" in msg                            # 友善提到 default branch 名


def test_build_claude_md_block_content():
    block = spec_sync.build_claude_md_block("MyProj", has_ui=True)
    assert "MyProj" in block
    assert ".lodestar/PRD.md" in block and ".lodestar/ARCHITECTURE.md" in block
    assert ".lodestar/UI-DESIGN.md" in block        # has_ui=True 才有
    assert "只實作被指派" in block                    # 規矩
    block_no_ui = spec_sync.build_claude_md_block("P", has_ui=False)
    assert "UI-DESIGN.md" not in block_no_ui


# ============================================================
#  endpoint
# ============================================================
def test_specs_sync_endpoint_not_ready_400(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "spec"}).json()["thread_id"]
        dal.upsert_artifact(tid, "prd", "# PRD only")          # 缺 architecture
        r = c.post(f"/api/specs/{tid}/sync")
        assert r.status_code == 400
        assert r.json()["detail"]["category"] == "specs_not_ready"


def test_specs_sync_endpoint_happy(tmp_db, monkeypatch):
    monkeypatch.setattr(spec_sync, "sync_specs",
                        lambda *a, **k: {"ok": True, "url": "https://github.com/owner/repo",
                                         "note": "ok", "files": [".lodestar/PRD.md", "CLAUDE.md"]})
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={
            "name": "spec", "delivery_target": "github",
            "repo_mode": "existing", "repo_full_name": "owner/repo",
        }).json()["thread_id"]
        dal.upsert_artifact(tid, "prd", "# PRD")
        dal.upsert_artifact(tid, "architecture", "# Arch")
        r = c.post(f"/api/specs/{tid}/sync")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["target"] == "github" and body["repo"] == "owner/repo"
        assert "CLAUDE.md" in body["files"]


# ============================================================
#  implement prompt 指引
# ============================================================
def test_build_impl_prompt_points_to_claude_md():
    from async_runtime.orchestrator import build_impl_prompt
    p = build_impl_prompt("實作登入頁", attempt=1)
    assert "CLAUDE.md" in p
    assert ".lodestar/" in p
    assert "UI-DESIGN.md" in p
    assert "實作登入頁" in p
