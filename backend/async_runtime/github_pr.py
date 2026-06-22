"""真實開 PR 的 PrOpener（坎2）：隔離 worktree commit → push branch → POST /pulls。

複用 builtin_integrations._publish_github 的 urllib + Bearer PAT 模式（改打 /pulls）。
token 由 keystore 提供（不外流：push 用 token 注入 url，錯誤訊息不回顯 url）。

安全與韌性：
- 無變更 → 不開空 PR（PrError）。
- 冪等：already_opened 回非空 → 直接回該 url（重按 approve 不重複開）。
- rollback：push 成功但 PR API 失敗 → 刪遠端 branch、PrError。
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

PrOpener = Callable[[int, str, str], str]

_PR_NUM_RE = re.compile(r"/pull/(\d+)")


class PrError(RuntimeError):
    """開 PR 流程的任何失敗（缺 token / 無變更 / push / API）。訊息保證不含 token。"""


def _git(wt: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(wt), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise PrError(f"git {args[0]} failed (exit {proc.returncode}): {proc.stderr[:200]}")
    return proc


def _reattach_to_work_branch(wt: Path, branch: str) -> None:
    """把 HEAD 釘回 work branch（agent bypassPermissions 可能 git checkout 把 HEAD 切離，
    例如 tester 切到 base 跑測試後沒切回 → detached）。branch 由 prepare 建立、agent 的 commit
    都在其上；未提交變更若無衝突會被 checkout 帶過去。已在該 branch → no-op；branch 不存在或
    切換失敗（衝突）→ 保持原狀、不丟資料（後續 add/commit/push 仍以當前 HEAD 收尾）。"""
    cur = (_git(wt, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout or "").strip()
    if cur == branch:
        return
    if _git(wt, ["rev-parse", "--verify", branch], check=False).returncode != 0:
        return
    _git(wt, ["checkout", branch], check=False)


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "lodestar-impl",
    }


def list_open_issues(repo: str, token: str, *, max_pages: int = 5) -> list[tuple[int, str]]:
    """列 repo 的 open issue (number, title)（排除 PR——GitHub issues API 會混入 PR）。

    給 batch 比對 story↔issue 用。任何失敗回 []（不該因列 issue 失敗而中斷上層流程）。"""
    out: list[tuple[int, str]] = []
    try:
        for page in range(1, max_pages + 1):
            url = f"https://api.github.com/repos/{repo}/issues?state=open&per_page=100&page={page}"
            req = urllib.request.Request(url, headers=_gh_headers(token), method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                items = json.loads(resp.read().decode("utf-8"))
            if not items:
                break
            for it in items:
                if "pull_request" in it:        # PR 也出現在 issues API → 跳過
                    continue
                n = it.get("number")
                if isinstance(n, int):
                    out.append((n, it.get("title") or ""))
            if len(items) < 100:
                break
    except Exception:                            # noqa: BLE001 —— 列舉失敗不影響上層
        return []
    return out


def list_open_issue_numbers(repo: str, token: str, *, max_pages: int = 5) -> list[int]:
    """列 repo 的 open issue 編號（排除 PR）。給單 session 路徑的 PR body `Closes #N` 用。"""
    return [n for n, _ in list_open_issues(repo, token, max_pages=max_pages)]


def list_all_issues(repo: str, token: str, *, max_pages: int = 30) -> list[tuple[int, str]]:
    """列 repo 的**所有** issue（open + closed，排除 PR）(number, title)。

    與 list_open/closed_issues 不同：**列舉失敗會 raise RuntimeError**（不吞）。給 publish 冪等用——
    若無法確認既有 issue 卻當成「沒有」，會重複發佈（正是要避免的 bug），故寧可中止也不靜默放行。"""
    out: list[tuple[int, str]] = []
    try:
        for page in range(1, max_pages + 1):
            url = f"https://api.github.com/repos/{repo}/issues?state=all&per_page=100&page={page}"
            req = urllib.request.Request(url, headers=_gh_headers(token), method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                items = json.loads(resp.read().decode("utf-8"))
            if not items:
                break
            for it in items:
                if "pull_request" in it:
                    continue
                n = it.get("number")
                if isinstance(n, int):
                    out.append((n, it.get("title") or ""))
            if len(items) < 100:
                break
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"列既有 issue 失敗（HTTP {exc.code}）") from None
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"列既有 issue 失敗：{exc}") from None
    return out


def get_issue_detail(repo: str, token: str, number: int) -> dict:
    """讀單一 issue 的 {number, title, body, url}（修改既有專案：匯入 issue 當任務來源）。
    失敗 / 該編號是 PR → raise RuntimeError（訊息不含 token）。"""
    url = f"https://api.github.com/repos/{repo}/issues/{number}"
    req = urllib.request.Request(url, headers=_gh_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            it = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"讀取 issue #{number} 失敗（HTTP {exc.code}）") from None
    except Exception:                            # noqa: BLE001
        raise RuntimeError(f"讀取 issue #{number} 失敗") from None
    if "pull_request" in it:
        raise RuntimeError(f"#{number} 是 pull request，不是 issue")
    return {
        "number": it.get("number"),
        "title": it.get("title") or "",
        "body": it.get("body") or "",
        "url": it.get("html_url") or "",
    }


def list_closed_issues(repo: str, token: str, *, max_pages: int = 5) -> list[tuple[int, str]]:
    """列 repo 的 closed issue (number, title)（排除 PR）。給 batch 冪等重跑：issue 已關 = 已交付 → 跳過。
    任何失敗回 []（不因列舉失敗而誤判：寧可重做也不要漏做）。"""
    out: list[tuple[int, str]] = []
    try:
        for page in range(1, max_pages + 1):
            url = f"https://api.github.com/repos/{repo}/issues?state=closed&per_page=100&page={page}"
            req = urllib.request.Request(url, headers=_gh_headers(token), method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                items = json.loads(resp.read().decode("utf-8"))
            if not items:
                break
            for it in items:
                if "pull_request" in it:
                    continue
                n = it.get("number")
                if isinstance(n, int):
                    out.append((n, it.get("title") or ""))
            if len(items) < 100:
                break
    except Exception:                            # noqa: BLE001
        return []
    return out


_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)


def list_active_pr_issue_numbers(repo: str, token: str, *, max_pages: int = 5) -> set[int]:
    """列「open 或 merged PR 的 body 透過關鍵字（Closes/Fixes/Resolves #N）連到的 issue 編號」。
    冪等重跑：open PR = 進行中；merged PR = 已交付（不能只靠 issue 是否關閉，auto-close 可能漏）。
    兩者都跳過、不重做。closed-未-merge 不算。失敗回 set()。"""
    out: set[int] = set()
    try:
        for page in range(1, max_pages + 1):
            # state=all：涵蓋 open + closed；merged PR 在 GitHub 是 closed + merged_at 非空
            url = f"https://api.github.com/repos/{repo}/pulls?state=all&per_page=100&page={page}"
            req = urllib.request.Request(url, headers=_gh_headers(token), method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                items = json.loads(resp.read().decode("utf-8"))
            if not items:
                break
            for pr in items:
                if pr.get("state") != "open" and not pr.get("merged_at"):
                    continue                     # closed-未-merge → 跳過不算
                for m in _CLOSES_RE.finditer(pr.get("body") or ""):
                    out.add(int(m.group(1)))
            if len(items) < 100:
                break
    except Exception:                            # noqa: BLE001
        return set()
    return out


def add_issue_comment(repo: str, token: str, issue_number: int, body: str) -> None:
    """在 issue 上留 comment（POST /issues/{n}/comments）。失敗只記 log、不上拋。"""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    req = urllib.request.Request(
        url, data=json.dumps({"body": body}).encode("utf-8"),
        headers={**_gh_headers(token), "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20):
            pass
    except Exception:                            # noqa: BLE001 —— comment 失敗不影響 PR 結果
        pass


def _create_pr(repo: str, token: str, head: str, base: str, session_id: int,
               issue_numbers: Optional[list[int]] = None, title: str = "") -> str:
    url = f"https://api.github.com/repos/{repo}/pulls"
    body = "Automated implementation by Lodestar. Review before merge."
    if issue_numbers:
        # GitHub：PR merge 進 default branch 時，body 內 `Closes #N` 會自動關閉對應 issue
        body += "\n\n" + "\n".join(f"Closes #{n}" for n in issue_numbers)
    payload = {
        "title": title or f"Lodestar implementation (session {session_id})",
        "head": head, "base": base,
        "body": body,
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={**_gh_headers(token), "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    pr_url = body.get("html_url")
    if not pr_url:
        raise PrError("PR API 回應無 html_url")
    return pr_url


def merge_pr(repo: str, token: str, pr_number: int, *, method: str = "squash") -> bool:
    """Merge 一個 PR（PUT /pulls/{n}/merge）。回 True=已 merge；
    405（not mergeable）/ 409（衝突/sha 過期）→ False（不可 merge，不上拋）；其餘 HTTP 錯 → PrError。"""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/merge"
    req = urllib.request.Request(
        url, data=json.dumps({"merge_method": method}).encode("utf-8"),
        headers={**_gh_headers(token), "Content-Type": "application/json"}, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return bool(body.get("merged"))
    except urllib.error.HTTPError as exc:
        if exc.code in (405, 409):
            return False
        raise PrError(f"merge PR #{pr_number} failed (HTTP {exc.code})") from exc


def make_github_pr_merger(*, get_token: Callable[[], str], repo: str,
                          pr_url_for: Callable[[int], str],
                          method: str = "squash") -> Callable[[int], bool]:
    """組 merge_pr(session_id)->bool：查該 session 的 pr_url → 解析 PR 號 → API merge。
    pr_url 缺 / 解析不到 / 無 token → False（無 PR 可 merge）。供 batch 在 story 過 QA gate 後依序整合。"""
    def do_merge(session_id: int) -> bool:
        m = _PR_NUM_RE.search((pr_url_for(session_id) or "").strip())
        if not m:
            return False
        token = (get_token() or "").strip()
        if not token:
            return False
        return merge_pr(repo, token, int(m.group(1)), method=method)
    return do_merge


def make_github_pr_opener(*, get_token: Callable[[], str],
                          workdir_for: Callable[[int], Path],
                          base_branch: str = "main",
                          already_opened: Optional[Callable[[int], str]] = None,
                          issue_number_for: Optional[Callable[[int], Optional[int]]] = None,
                          pr_title_for: Optional[Callable[[int], str]] = None) -> PrOpener:
    """組一個真實開 PR 的 PrOpener。

    get_token：回 GitHub PAT（通常 keystore.get_credentials('github')['token']）。
    workdir_for：session_id → worktree 路徑（agent 改檔處，已在 branch lodestar/impl-{id}）。
    issue_number_for：（batch）session_id → 該 session 對應的單一 issue 編號。給 → PR 只 `Closes` 該 issue
        並在開完 PR 後於該 issue 留 comment；不給 → 退回「抓所有 open issue」的舊行為（單 session 路徑）。
    pr_title_for：（batch）session_id → PR 標題（如 story 標題）；不給 → 預設標題。
    """
    def open_pr(session_id: int, target_repo: str, last_output: str) -> str:
        if already_opened:
            existing = (already_opened(session_id) or "").strip()
            if existing:
                return existing                       # 冪等
        repo = (target_repo or "").strip()
        if "/" not in repo:
            raise PrError(f"target_repo 無效：'{repo}'")
        token = (get_token() or "").strip()
        if not token:
            raise PrError("缺 GitHub token（keystore 無 github 憑證）")
        wt = workdir_for(session_id)
        branch = f"lodestar/impl-{session_id}"

        # agent 可能把 HEAD 切離 work branch（detached / 別條）→ 先釘回，否則下面以「當前 HEAD」
        # 為準的 diff/push 會漏掉真正的工作 commit（症狀：工作有做卻誤判空 diff、failed）。
        _reattach_to_work_branch(wt, branch)
        # base：clone 模式本地在 default branch（main/master）→ 當 base；worktree 模式本地已在
        # work branch（== branch）→ 用參數 base_branch。head 一律推當前 commit 上去（HEAD:）。
        # detached 時 rev-parse 回字面 "HEAD"——不能拿它當 base，退回參數 base_branch。
        cur = (_git(wt, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout or "").strip()
        base = base_branch if (not cur or cur in ("HEAD", branch)) else cur
        _git(wt, ["add", "-A"])
        # agent（bypassPermissions）可能已自行 git commit → 沒得 stage；有殘留未提交則收進一個 commit
        if _git(wt, ["diff", "--cached", "--quiet"], check=False).returncode != 0:
            _git(wt, ["commit", "-m", f"Lodestar impl session {session_id}"])
        # 真正判斷有無東西可 PR：HEAD 相對 origin/base 有無差異（涵蓋 agent 自 commit + host commit）。
        # 只看 staged 會在 agent 已 commit 時誤判「無變更」（工作有做卻說空 diff、failed）。
        if _git(wt, ["diff", "--quiet", f"origin/{base}", "HEAD"], check=False).returncode == 0:
            raise PrError("worktree 無變更，不開空 PR")
        remote = f"https://x-access-token:{token}@github.com/{repo}.git"
        push = subprocess.run(
            ["git", "-C", str(wt), "push", remote, f"HEAD:{branch}", "--force"],
            capture_output=True, text=True)
        if push.returncode != 0:
            raise PrError(f"git push failed (exit {push.returncode})")   # 不回顯 stderr（含 token url）

        # issue 關聯：batch → 只 Closes 該 session 對應的單一 issue；單 session → 抓所有 open issue（舊行為）
        issue = issue_number_for(session_id) if issue_number_for else None
        issue_numbers = [issue] if issue else (
            [] if issue_number_for else list_open_issue_numbers(repo, token))
        title = (pr_title_for(session_id) if pr_title_for else "") or ""

        try:
            pr_url = _create_pr(repo, token, branch, base, session_id, issue_numbers, title=title)
        except Exception as exc:                       # rollback：刪遠端 branch
            subprocess.run(["git", "-C", str(wt), "push", remote, "--delete", branch],
                           capture_output=True)
            raise PrError(f"開 PR 失敗，已回滾遠端 branch：{exc}") from exc

        if issue:                                       # batch：在該 issue 留 PR 連結（失敗不影響 PR）
            add_issue_comment(repo, token, issue, f"🤖 Lodestar 已開 PR 實作此 issue：{pr_url}")
        return pr_url

    return open_pr
