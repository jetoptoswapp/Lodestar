"""_reattach_to_work_branch / _pin_head_to_work_branch：真實 git repo 重現 Story 7.2 的
detached-HEAD bug（agent 把 HEAD 切離 work branch → host 開 PR 時誤判「無變更」failed）。

情境：rd 把工作 commit 在 lodestar/impl-N，tester 跑 `git checkout <base>` 把 HEAD detach 走，
之後 host/下游 role 應把 HEAD 釘回 work branch、看得到工作。
"""
import subprocess

import pytest

from async_runtime import github_pr, gitlab_mr, orchestrator


def _git(wt, *args):
    return subprocess.run(["git", "-C", str(wt), *args], capture_output=True, text=True, check=True)


def _head_branch(wt) -> str:
    return subprocess.run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo_detached(tmp_path):
    """建 repo：main 有 base commit；work branch lodestar/impl-7 有工作 commit；
    HEAD 被 detach 到 base（模擬 tester 的 `git checkout <base>`）。回 (路徑, work branch 名)。"""
    wt = tmp_path / "repo"
    wt.mkdir()
    _git(wt, "init", "-q")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    _git(wt, "checkout", "-q", "-b", "main")
    (wt / "base.txt").write_text("base\n")
    _git(wt, "add", "-A"); _git(wt, "commit", "-q", "-m", "base")
    base_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()

    branch = "lodestar/impl-7"
    _git(wt, "checkout", "-q", "-b", branch)
    (wt / "feature.txt").write_text("the story 7 work\n")
    _git(wt, "add", "-A"); _git(wt, "commit", "-q", "-m", "feat: story 7 work")

    _git(wt, "checkout", "-q", base_sha)            # tester 切走 → detached HEAD
    assert _head_branch(wt) == "HEAD"               # 確認真的 detached
    assert not (wt / "feature.txt").exists()        # 工作從當前樹消失
    return wt, branch


@pytest.mark.parametrize("mod", [gitlab_mr, github_pr])
def test_reattach_recovers_detached_work(repo_detached, mod):
    wt, branch = repo_detached
    mod._reattach_to_work_branch(wt, branch)
    assert _head_branch(wt) == branch               # HEAD 釘回 work branch
    assert (wt / "feature.txt").read_text() == "the story 7 work\n"  # 工作回來了


@pytest.mark.parametrize("mod", [gitlab_mr, github_pr])
def test_reattach_noop_when_already_on_branch(tmp_path, mod):
    wt = tmp_path / "r"; wt.mkdir()
    _git(wt, "init", "-q"); _git(wt, "config", "user.email", "t@t"); _git(wt, "config", "user.name", "t")
    _git(wt, "checkout", "-q", "-b", "lodestar/impl-9")
    (wt / "a.txt").write_text("x\n"); _git(wt, "add", "-A"); _git(wt, "commit", "-q", "-m", "c")
    mod._reattach_to_work_branch(wt, "lodestar/impl-9")
    assert _head_branch(wt) == "lodestar/impl-9"


@pytest.mark.parametrize("mod", [gitlab_mr, github_pr])
def test_reattach_safe_when_branch_missing(repo_detached, mod):
    wt, _ = repo_detached
    mod._reattach_to_work_branch(wt, "lodestar/impl-does-not-exist")  # 不存在 → 不報錯、保持 detached
    assert _head_branch(wt) == "HEAD"


def test_pin_head_to_work_branch_recovers(repo_detached):
    """orchestrator 在每個 role 後呼叫的 host 端釘回（session_id=7 → lodestar/impl-7）。"""
    wt, _ = repo_detached
    orchestrator._pin_head_to_work_branch(str(wt), 7)
    assert _head_branch(wt) == "lodestar/impl-7"
    assert (wt / "feature.txt").exists()


def test_pin_head_noop_on_non_git(tmp_path):
    orchestrator._pin_head_to_work_branch(str(tmp_path), 7)  # 非 git 目錄 → best-effort 不報錯
