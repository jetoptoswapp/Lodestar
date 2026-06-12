"""文件發佈（docs_publisher）：PRD/Architecture → GitHub Wiki / GitLab Wiki。

git 操作用 fake subprocess（不打網路、不需真 git remote）。驗證：
- 兩 target 都 push {repo}.wiki.git：寫 PRD/Architecture/Home，push 該分支。
- GitLab 用 self-hosted base_url 組 wiki remote、回 /-/wikis/home 網址。
- 空 wiki（clone 失敗）→ init bootstrap 後仍能 push。
- 錯誤路徑：缺 token / repo 格式錯 / 不支援 target / wiki push 失敗（友善訊息、不外洩 token）。
- endpoint：缺 artifact 400、happy path 200。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as appmod
import docs_publisher
import repo_workspace
from persistence import dal

_DOCS = {"PRD": "# PRD\n\nrequirements", "Architecture": "# Arch\n\n## Tier\nmodules"}


class _FakeProc:
    def __init__(self, rc: int, stdout: str = "", stderr: str = ""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def _fake_git(*, clone_ok: bool = True, push_ok: bool = True, branch: str = "master"):
    """回 (fake_run, calls)。clone 成功時建出 dest/.git；push 依旗標回 rc。"""
    calls: list[list[str]] = []

    def run(cmd, capture_output=True, text=True, timeout=None):
        calls.append(list(cmd))
        if cmd[:2] == ["git", "clone"]:
            if clone_ok:
                (Path(cmd[3]) / ".git").mkdir(parents=True, exist_ok=True)
                return _FakeProc(0)
            return _FakeProc(1, stderr="repo not found")
        sub = cmd[3] if (len(cmd) > 3 and cmd[1] == "-C") else ""
        if sub == "push":
            return _FakeProc(0 if push_ok else 1, stderr="" if push_ok else "denied")
        if sub == "rev-parse":
            return _FakeProc(0, stdout=branch + "\n")
        if sub == "diff":                       # diff --cached --quiet：rc1 = 有變更 → 會 commit
            return _FakeProc(1)
        return _FakeProc(0)

    return run, calls


# ============================================================
#  GitHub Wiki
# ============================================================
def test_github_wiki_happy(tmp_db, monkeypatch):
    run, calls = _fake_git()
    monkeypatch.setattr(docs_publisher.subprocess, "run", run)
    out = docs_publisher.publish_docs("t1", "github", "owner/repo", {"token": "T"}, _DOCS)

    assert out["ok"] is True
    assert out["url"] == "https://github.com/owner/repo/wiki"
    dest = repo_workspace.project_dir("t1") / "wiki"
    assert (dest / "PRD.md").read_text(encoding="utf-8") == _DOCS["PRD"]
    assert (dest / "Architecture.md").read_text(encoding="utf-8") == _DOCS["Architecture"]
    assert "[PRD](PRD)" in (dest / "Home.md").read_text(encoding="utf-8")
    assert any(len(c) > 3 and c[3] == "push" for c in calls)         # 有 push
    # token 不得出現在任何回傳字串
    assert "T" not in out["url"] or "token" not in out["url"]


def test_github_wiki_includes_ui_design_page(tmp_db, monkeypatch):
    """docs 含 UI-Design key → 多寫一頁 UI-Design.md，Home 列出連結（既有兩頁不變）。"""
    run, _ = _fake_git()
    monkeypatch.setattr(docs_publisher.subprocess, "run", run)
    docs = dict(_DOCS, **{"UI-Design": "# UI Design\n\n## Screen: Home"})
    out = docs_publisher.publish_docs("t1b", "github", "owner/repo", {"token": "T"}, docs)
    assert out["ok"] is True
    dest = repo_workspace.project_dir("t1b") / "wiki"
    assert (dest / "UI-Design.md").read_text(encoding="utf-8") == docs["UI-Design"]
    home = (dest / "Home.md").read_text(encoding="utf-8")
    assert "[PRD](PRD)" in home and "[UI-Design](UI-Design)" in home


def test_github_wiki_bootstraps_empty_wiki(tmp_db, monkeypatch):
    """wiki 從未建頁（clone 失敗）→ init 後仍能 push 成功。"""
    run, calls = _fake_git(clone_ok=False)
    monkeypatch.setattr(docs_publisher.subprocess, "run", run)
    out = docs_publisher.publish_docs("t2", "github", "owner/repo", {"token": "T"}, _DOCS)
    assert out["ok"] is True
    assert any(len(c) > 3 and c[3] == "init" for c in calls)         # 走了 init bootstrap


def test_github_wiki_push_fail_friendly(tmp_db, monkeypatch):
    """push 被拒（最常見：repo 未啟用 Wiki）→ 友善訊息、不外洩 token / stderr。"""
    run, _ = _fake_git(push_ok=False)
    monkeypatch.setattr(docs_publisher.subprocess, "run", run)
    with pytest.raises(docs_publisher.DocsPublishError) as ei:
        docs_publisher.publish_docs("t3", "github", "owner/repo", {"token": "secret"}, _DOCS)
    msg = str(ei.value)
    assert "Wiki" in msg
    assert "secret" not in msg and "denied" not in msg


# ============================================================
#  GitLab Wiki
# ============================================================
def test_gitlab_wiki_happy(tmp_db, monkeypatch):
    run, calls = _fake_git()
    monkeypatch.setattr(docs_publisher.subprocess, "run", run)
    out = docs_publisher.publish_docs("g1", "gitlab", "grp/proj", {"token": "T"}, _DOCS)

    assert out["ok"] is True
    assert out["url"] == "https://gitlab.com/grp/proj/-/wikis/home"
    dest = repo_workspace.project_dir("g1") / "wiki"
    assert (dest / "PRD.md").read_text(encoding="utf-8") == _DOCS["PRD"]
    assert (dest / "Architecture.md").read_text(encoding="utf-8") == _DOCS["Architecture"]
    assert (dest / "home.md").exists()                              # GitLab landing 用小寫 home
    assert any(len(c) > 3 and c[3] == "push" for c in calls)
    # clone remote 應指向 wiki repo（.wiki.git），且用 gitlab oauth2 url
    clone_cmd = next(c for c in calls if c[:2] == ["git", "clone"])
    assert clone_cmd[2].endswith("/grp/proj.wiki.git")
    assert clone_cmd[2].startswith("https://oauth2:")


def test_gitlab_wiki_self_hosted_base_url(tmp_db, monkeypatch):
    """self-hosted base_url → wiki remote 與回傳網址都用該 host。"""
    run, calls = _fake_git()
    monkeypatch.setattr(docs_publisher.subprocess, "run", run)
    out = docs_publisher.publish_docs(
        "g2", "gitlab", "grp/proj", {"token": "T", "base_url": "https://gitlab.example.com"}, _DOCS)
    assert out["url"] == "https://gitlab.example.com/grp/proj/-/wikis/home"
    clone_cmd = next(c for c in calls if c[:2] == ["git", "clone"])
    assert "gitlab.example.com/grp/proj.wiki.git" in clone_cmd[2]


def test_gitlab_wiki_push_fail_friendly(tmp_db, monkeypatch):
    run, _ = _fake_git(push_ok=False)
    monkeypatch.setattr(docs_publisher.subprocess, "run", run)
    with pytest.raises(docs_publisher.DocsPublishError) as ei:
        docs_publisher.publish_docs("g3", "gitlab", "grp/proj", {"token": "secret"}, _DOCS)
    msg = str(ei.value)
    assert "Wiki" in msg
    assert "secret" not in msg


# ============================================================
#  分派入口的防呆
# ============================================================
def test_publish_requires_token(tmp_db):
    with pytest.raises(docs_publisher.DocsPublishError):
        docs_publisher.publish_docs("x", "github", "owner/repo", {}, _DOCS)


def test_publish_rejects_bad_repo(tmp_db):
    with pytest.raises(docs_publisher.DocsPublishError):
        docs_publisher.publish_docs("x", "github", "noslash", {"token": "T"}, _DOCS)


def test_publish_rejects_unsupported_target(tmp_db):
    with pytest.raises(docs_publisher.DocsPublishError):
        docs_publisher.publish_docs("x", "jira", "owner/repo", {"token": "T"}, _DOCS)


# ============================================================
#  endpoint
# ============================================================
def test_docs_publish_endpoint_not_ready_400(tmp_db):
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={"name": "docs"}).json()["thread_id"]
        dal.upsert_artifact(tid, "prd", "# PRD only")          # 缺 architecture
        r = c.post(f"/api/docs/{tid}/publish")
        assert r.status_code == 400
        assert r.json()["detail"]["category"] == "docs_not_ready"


def test_docs_publish_endpoint_happy(tmp_db, monkeypatch):
    monkeypatch.setattr(docs_publisher, "publish_docs",
                        lambda *a, **k: {"ok": True, "url": "https://github.com/owner/repo/wiki", "note": "ok"})
    with TestClient(appmod.app) as c:
        tid = c.post("/api/projects", json={
            "name": "docs", "delivery_target": "github",
            "repo_mode": "existing", "repo_full_name": "owner/repo",
        }).json()["thread_id"]
        dal.upsert_artifact(tid, "prd", "# PRD")
        dal.upsert_artifact(tid, "architecture", "# Arch")
        r = c.post(f"/api/docs/{tid}/publish")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["target"] == "github"
        assert body["repo"] == "owner/repo"
        assert body["url"] == "https://github.com/owner/repo/wiki"
