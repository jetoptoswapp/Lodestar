"""github_pr.make_github_pr_opener：happy / 無變更 / 缺 token / 冪等 / rollback（全 mock，不碰網路與真 git）。"""
from __future__ import annotations

import pytest

from async_runtime import github_pr
from async_runtime.github_pr import PrError, make_github_pr_opener


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_git(monkeypatch, *, has_changes=True, push_ok=True, record=None):
    def fake_run(args, **kw):
        if record is not None:
            record.append(args)
        # git diff --cached --quiet：0=無變更、1=有變更
        if "diff" in args and "--quiet" in args:
            return _FakeProc(returncode=1 if has_changes else 0)
        if "push" in args and "--delete" not in args:
            return _FakeProc(returncode=0 if push_ok else 1, stderr="" if push_ok else "denied")
        return _FakeProc(returncode=0)
    monkeypatch.setattr(github_pr.subprocess, "run", fake_run)


def test_happy_path(monkeypatch, tmp_path):
    _patch_git(monkeypatch)
    monkeypatch.setattr(github_pr, "_create_pr", lambda *a, **k: "https://github.com/o/r/pull/7")
    opener = make_github_pr_opener(get_token=lambda: "tok", workdir_for=lambda sid: tmp_path)
    assert opener(7, "o/r", "") == "https://github.com/o/r/pull/7"


def test_no_changes_no_empty_pr(monkeypatch, tmp_path):
    _patch_git(monkeypatch, has_changes=False)
    opener = make_github_pr_opener(get_token=lambda: "tok", workdir_for=lambda sid: tmp_path)
    with pytest.raises(PrError, match="無變更"):
        opener(7, "o/r", "")


def test_missing_token(monkeypatch, tmp_path):
    _patch_git(monkeypatch)
    opener = make_github_pr_opener(get_token=lambda: "", workdir_for=lambda sid: tmp_path)
    with pytest.raises(PrError, match="token"):
        opener(7, "o/r", "")


def test_invalid_repo(monkeypatch, tmp_path):
    _patch_git(monkeypatch)
    opener = make_github_pr_opener(get_token=lambda: "tok", workdir_for=lambda sid: tmp_path)
    with pytest.raises(PrError, match="target_repo"):
        opener(7, "no-slash", "")


def test_idempotent(monkeypatch, tmp_path):
    _patch_git(monkeypatch)
    # already_opened 回既有 url → 不再 push/開 PR
    monkeypatch.setattr(github_pr, "_create_pr",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該開")))
    opener = make_github_pr_opener(get_token=lambda: "tok", workdir_for=lambda sid: tmp_path,
                                   already_opened=lambda sid: "https://github.com/o/r/pull/3")
    assert opener(7, "o/r", "") == "https://github.com/o/r/pull/3"


def test_rollback_on_pr_failure(monkeypatch, tmp_path):
    rec = []
    _patch_git(monkeypatch, record=rec)

    def boom(*a, **k):
        raise RuntimeError("422 validation")
    monkeypatch.setattr(github_pr, "_create_pr", boom)
    opener = make_github_pr_opener(get_token=lambda: "tok", workdir_for=lambda sid: tmp_path)
    with pytest.raises(PrError, match="回滾"):
        opener(7, "o/r", "")
    # rollback：應有一次 push --delete
    assert any("push" in a and "--delete" in a for a in rec)
