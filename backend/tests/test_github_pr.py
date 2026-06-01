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


class _FakeResp:
    def __init__(self, payload):
        self._b = __import__("json").dumps(payload).encode()
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_list_open_issue_numbers_filters_prs(monkeypatch):
    # 第一頁：1 個 issue + 1 個 PR（含 pull_request 欄）；PR 應被濾掉
    page = [{"number": 12}, {"number": 13, "pull_request": {"url": "x"}}]
    calls = {"n": 0}
    def fake_urlopen(req, timeout=20):
        calls["n"] += 1
        return _FakeResp(page if calls["n"] == 1 else [])
    monkeypatch.setattr(github_pr.urllib.request, "urlopen", fake_urlopen)
    assert github_pr.list_open_issue_numbers("o/r", "tok") == [12]


def test_list_open_issue_numbers_swallows_errors(monkeypatch):
    def boom(req, timeout=20):
        raise RuntimeError("network")
    monkeypatch.setattr(github_pr.urllib.request, "urlopen", boom)
    assert github_pr.list_open_issue_numbers("o/r", "tok") == []


def test_pr_body_includes_closes(monkeypatch):
    captured = {}
    def fake_urlopen(req, timeout=20):
        captured["body"] = __import__("json").loads(req.data.decode())["body"]
        return _FakeResp({"html_url": "https://github.com/o/r/pull/9"})
    monkeypatch.setattr(github_pr.urllib.request, "urlopen", fake_urlopen)
    url = github_pr._create_pr("o/r", "tok", "head", "main", 9, [5, 6])
    assert url.endswith("/pull/9")
    assert "Closes #5" in captured["body"] and "Closes #6" in captured["body"]


def test_open_pr_passes_issue_numbers(monkeypatch, tmp_path):
    _patch_git(monkeypatch)
    monkeypatch.setattr(github_pr, "list_open_issue_numbers", lambda repo, token, **k: [1, 2, 3])
    seen = {}
    def fake_create(repo, token, head, base, sid, issue_numbers=None):
        seen["nums"] = issue_numbers
        return "https://github.com/o/r/pull/1"
    monkeypatch.setattr(github_pr, "_create_pr", fake_create)
    opener = make_github_pr_opener(get_token=lambda: "tok", workdir_for=lambda sid: tmp_path)
    assert opener(7, "o/r", "") == "https://github.com/o/r/pull/1"
    assert seen["nums"] == [1, 2, 3]


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
