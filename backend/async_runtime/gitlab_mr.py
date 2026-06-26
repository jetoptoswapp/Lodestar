"""真實開 GitLab MR 的 PrOpener（P4）：clone 內 commit → push branch → POST /merge_requests。

對應 github_pr，但走 GitLab API（PRIVATE-TOKEN header、push 用 oauth2:{token}）。
token 不外流（只在 remote url，錯誤訊息不回顯）。冪等（already_opened）+ rollback（push 失敗刪遠端 branch）。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

PrOpener = Callable[[int, str, str], str]

_MR_IID_RE = re.compile(r"/merge_requests/(\d+)")
_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)


class MrError(RuntimeError):
    """開 MR 流程的任何失敗（缺 token / 無變更 / push / API）。訊息保證不含 token。"""


def _git(wt: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(wt), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise MrError(f"git {args[0]} failed (exit {proc.returncode}): {proc.stderr[:200]}")
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


def _gl_headers(token: str) -> dict:
    return {"PRIVATE-TOKEN": token, "User-Agent": "lodestar-impl"}


def list_open_issues(base: str, token: str, repo: str, *, max_pages: int = 5) -> list[tuple[int, str]]:
    """列 GitLab project 的 open issue (iid, title)。失敗回 []（不中斷上層）。"""
    pid = urllib.parse.quote(repo, safe="")
    out: list[tuple[int, str]] = []
    try:
        for page in range(1, max_pages + 1):
            url = (f"{base}/api/v4/projects/{pid}/issues"
                   f"?state=opened&per_page=100&page={page}")
            req = urllib.request.Request(url, headers=_gl_headers(token), method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                items = json.loads(resp.read().decode("utf-8"))
            if not items:
                break
            for it in items:
                iid = it.get("iid")
                if isinstance(iid, int):
                    out.append((iid, it.get("title") or ""))
            if len(items) < 100:
                break
    except Exception:                            # noqa: BLE001
        return []
    return out


def list_all_issues(base: str, token: str, repo: str, *, max_pages: int = 30) -> list[tuple[int, str]]:
    """列 GitLab project 的**所有** issue（opened + closed）(iid, title)。
    與 list_open_issues 不同：**失敗會 raise RuntimeError**（給 publish 冪等用，不可吞，否則會重複發佈）。"""
    pid = urllib.parse.quote(repo, safe="")
    out: list[tuple[int, str]] = []
    try:
        for page in range(1, max_pages + 1):
            url = f"{base}/api/v4/projects/{pid}/issues?per_page=100&page={page}"
            req = urllib.request.Request(url, headers=_gl_headers(token), method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                items = json.loads(resp.read().decode("utf-8"))
            if not items:
                break
            for it in items:
                iid = it.get("iid")
                if isinstance(iid, int):
                    out.append((iid, it.get("title") or ""))
            if len(items) < 100:
                break
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"列既有 issue 失敗（HTTP {exc.code}）") from None
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"列既有 issue 失敗：{exc}") from None
    return out


def get_issue_detail(base: str, token: str, repo: str, iid: int) -> dict:
    """讀單一 GitLab issue 的 {number, title, body, url}（修改既有專案：匯入 issue 當任務來源）。
    失敗 → raise RuntimeError（訊息不含 token）。number 用 iid（與 list_open_issues 一致）。"""
    pid = urllib.parse.quote(repo, safe="")
    url = f"{base}/api/v4/projects/{pid}/issues/{iid}"
    req = urllib.request.Request(url, headers=_gl_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            it = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"讀取 issue #{iid} 失敗（HTTP {exc.code}）") from None
    except Exception:                            # noqa: BLE001
        raise RuntimeError(f"讀取 issue #{iid} 失敗") from None
    return {
        "number": it.get("iid"),
        "title": it.get("title") or "",
        "body": it.get("description") or "",
        "url": it.get("web_url") or "",
    }


def add_issue_note(base: str, token: str, repo: str, issue_iid: int, body: str) -> None:
    """在 GitLab issue 上留 note。失敗只吞掉、不上拋。"""
    pid = urllib.parse.quote(repo, safe="")
    url = f"{base}/api/v4/projects/{pid}/issues/{issue_iid}/notes"
    req = urllib.request.Request(
        url, data=json.dumps({"body": body}).encode("utf-8"), method="POST",
        headers={**_gl_headers(token), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20):
            pass
    except Exception:                            # noqa: BLE001
        pass


def _find_open_mr(base: str, token: str, repo: str, source_branch: str) -> str:
    """撈該 source branch 已開的 MR web_url（重試安全 / 冪等用）。查不到回 ""。"""
    pid = urllib.parse.quote(repo, safe="")
    sb = urllib.parse.quote(source_branch, safe="")
    url = f"{base}/api/v4/projects/{pid}/merge_requests?state=opened&source_branch={sb}"
    try:
        req = urllib.request.Request(url, headers=_gl_headers(token), method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            items = json.loads(resp.read().decode("utf-8"))
        return (items[0].get("web_url") or "") if items else ""
    except Exception:                                # noqa: BLE001
        return ""


def _create_mr(base: str, token: str, repo: str, head: str, target: str, session_id: int,
               issue_iid: Optional[int] = None, title: str = "",
               *, attempts: int = 4, delay: float = 2.0) -> str:
    pid = urllib.parse.quote(repo, safe="")
    url = f"{base}/api/v4/projects/{pid}/merge_requests"
    description = "Automated implementation by Lodestar. Review before merge."
    if issue_iid:
        description += f"\n\nCloses #{issue_iid}"   # GitLab：MR merge 時自動關閉該 issue
    payload = {
        "source_branch": head, "target_branch": target,
        "title": title or f"Lodestar implementation (session {session_id})",
        "description": description,
    }
    last: Optional[Exception] = None
    for i in range(attempts):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={**_gl_headers(token), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            web = body.get("web_url")
            if not web:
                raise MrError("MR API 回應無 web_url")
            return web
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:300]
            except Exception:                        # noqa: BLE001
                pass
            low = detail.lower()
            # 已有同 source branch 的 open MR（前次嘗試其實成功了/重複）→ 撈回它，冪等不失敗
            if exc.code in (409, 400) and "already exists" in low:
                ex = _find_open_mr(base, token, repo, head)
                if ex:
                    return ex
            # 自架 GitLab：push 後 branch 尚未可見 → 400 "source branch ... does not exist"；或 5xx → 重試
            transient = exc.code >= 500 or (
                exc.code == 400 and ("does not exist" in low or "source" in low or not detail))
            last = MrError(f"MR create HTTP {exc.code}: {detail or exc.reason}")
            if transient and i < attempts - 1:
                time.sleep(delay)
                continue
            raise last from exc
    raise last or MrError("MR create failed")


def make_gitlab_mr_opener(*, get_token: Callable[[], str],
                          workdir_for: Callable[[int], Path],
                          base_url: str = "https://gitlab.com",
                          already_opened: Optional[Callable[[int], str]] = None,
                          issue_number_for: Optional[Callable[[int], Optional[int]]] = None,
                          pr_title_for: Optional[Callable[[int], str]] = None) -> PrOpener:
    """issue_number_for/pr_title_for：batch 用，scope MR 到單一 issue + 帶 story 標題 + 開完留 note。"""
    base = base_url.rstrip("/")
    host = base.split("://")[-1]

    def open_mr(session_id: int, target_repo: str, last_output: str) -> str:
        if already_opened:
            ex = (already_opened(session_id) or "").strip()
            if ex:
                return ex
        repo = (target_repo or "").strip()
        if "/" not in repo:
            raise MrError(f"target_repo 無效：'{repo}'")
        token = (get_token() or "").strip()
        if not token:
            raise MrError("缺 GitLab token")
        wt = workdir_for(session_id)
        branch = f"lodestar/impl-{session_id}"

        # agent 可能把 HEAD 切離 work branch（detached / 別條）→ 先釘回，否則下面以「當前 HEAD」
        # 為準的 diff/push 會漏掉真正的工作 commit（症狀：工作有做卻誤判空 diff、failed）。
        _reattach_to_work_branch(wt, branch)
        cur = (_git(wt, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout or "").strip()
        # detached 時 rev-parse 回字面 "HEAD"——不能拿它當 base；連同已在 work branch 的情形 → base 用 main。
        base_branch = cur if (cur and cur not in ("HEAD", branch)) else "main"
        _git(wt, ["add", "-A"])
        # agent（bypassPermissions）可能已自行 git commit → 沒得 stage；有殘留未提交則收進一個 commit
        if _git(wt, ["diff", "--cached", "--quiet"], check=False).returncode != 0:
            _git(wt, ["commit", "-m", f"Lodestar impl session {session_id}"])
        # 真正判斷有無東西可 MR：HEAD 相對 origin/base 有無差異（涵蓋 agent 自 commit + host commit）。
        # 只看 staged 會在 agent 已 commit 時誤判「無變更」（症狀：工作有做卻說空 diff、failed）。
        if _git(wt, ["diff", "--quiet", f"origin/{base_branch}", "HEAD"], check=False).returncode == 0:
            raise MrError("worktree 無變更，不開空 MR")

        remote = f"https://oauth2:{token}@{host}/{repo}.git"
        try:
            # timeout：遠端不通時 push 不該無限卡（即使已 off-loop，也別綁死 worker thread）。
            push = subprocess.run(
                ["git", "-C", str(wt), "push", remote, f"HEAD:{branch}", "--force"],
                capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            raise MrError("git push timed out (120s) — 遠端不可達？")
        if push.returncode != 0:
            raise MrError(f"git push failed (exit {push.returncode})")   # 不回顯 stderr（含 token url）

        issue = issue_number_for(session_id) if issue_number_for else None
        title = (pr_title_for(session_id) if pr_title_for else "") or ""
        try:
            mr_url = _create_mr(base, token, repo, branch, base_branch, session_id,
                                issue_iid=issue, title=title)
        except Exception as exc:                       # rollback：刪遠端 branch
            subprocess.run(["git", "-C", str(wt), "push", remote, "--delete", branch],
                           capture_output=True)
            raise MrError(f"開 MR 失敗，已回滾遠端 branch：{exc}") from exc

        if issue:
            add_issue_note(base, token, repo, issue, f"🤖 Lodestar 已開 MR 實作此 issue：{mr_url}")
        return mr_url

    return open_mr


# ============================================================
#  策略 A（GitLab）：merge MR + 冪等重跑（對齊 github_pr）
# ============================================================
def merge_mr(base: str, token: str, repo: str, mr_iid: int, *,
             ready_timeout: float = 45.0, poll_delay: float = 2.0) -> bool:
    """Merge 一個 MR（PUT /merge_requests/:iid/merge）。回 True=已 merge。

    先 GET 輪詢 mergeability 直到離開 'checking/unchecked/preparing'（GitLab 非同步算 merge_status，
    剛開 MR 多半還在算）→ 就緒才 PUT merge；真不可 merge（衝突 / 需 pipeline / 需 approve）→ 回 False
    （不上拋，由 batch 記 log 續跑）。窗口拉長到 ~45s：避免「還在 checking 就放棄」→ 後續 story 亂序
    merge → 先前 story 反被擠成衝突（策略 A 序列化失效的根因）。"""
    pid = urllib.parse.quote(repo, safe="")
    detail_url = f"{base}/api/v4/projects/{pid}/merge_requests/{mr_iid}"
    merge_url = f"{detail_url}/merge"

    # 1) 輪詢 mergeability：等它算完再決定（就緒 / 真不可 merge / 超時）
    deadline = time.monotonic() + ready_timeout
    while True:
        try:
            req = urllib.request.Request(detail_url, headers=_gl_headers(token), method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                mr = json.loads(resp.read().decode("utf-8"))
        except Exception:                            # noqa: BLE001 - 查不到當不可 merge
            return False
        if mr.get("state") == "merged":              # 已被 merge → 冪等回 True
            return True
        status = (mr.get("detailed_merge_status") or mr.get("merge_status") or "").lower()
        if status in ("mergeable", "can_be_merged"):
            break                                    # 就緒 → 去 merge
        if status not in ("checking", "unchecked", "preparing", ""):
            return False                             # conflict / ci_must_pass / not_approved / draft… → 不可 merge
        if time.monotonic() >= deadline:
            return False                             # 還在算就超時 → 不亂 merge，留人處理
        time.sleep(poll_delay)

    # 2) 就緒 → PUT merge（偶有短暫 race 回 405/409 → 少量重試）
    for i in range(3):
        req = urllib.request.Request(merge_url, data=b"{}", method="PUT",
                                     headers={**_gl_headers(token), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body.get("state") == "merged"
        except urllib.error.HTTPError as exc:
            if exc.code in (405, 406, 409):
                if i < 2:
                    time.sleep(1.5)
                    continue
                return False
            raise MrError(f"merge MR !{mr_iid} failed (HTTP {exc.code})") from exc
    return False


def close_issue(base: str, token: str, repo: str, issue_iid: int) -> None:
    """主動關 issue（merge 後備援）。GitLab 的 `Closes #N` auto-close 不穩——同樣寫法有時關、
    有時沒解析到（實測 MR merge 進 default branch 後 issue 仍 open），故 host 自己補關。
    idempotent（關已關的 issue 是 no-op）、失敗只吞、不影響 merge 結果。"""
    pid = urllib.parse.quote(repo, safe="")
    url = f"{base}/api/v4/projects/{pid}/issues/{issue_iid}?state_event=close"
    req = urllib.request.Request(url, data=b"", method="PUT", headers=_gl_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=20):
            pass
    except Exception:                            # noqa: BLE001
        pass


def make_gitlab_mr_merger(*, get_token: Callable[[], str], base_url: str, repo: str,
                          pr_url_for: Callable[[int], str],
                          issue_iid_for: Optional[Callable[[int], Optional[int]]] = None,
                          ) -> Callable[[int], bool]:
    """組 merge(session_id)->bool：查該 session 的 MR web_url → 解析 iid → API merge。

    issue_iid_for 給定時，merge 成功後主動關該 issue（備援 GitLab 不穩的 auto-close）。"""
    base = (base_url or "https://gitlab.com").rstrip("/")

    def do_merge(session_id: int) -> bool:
        m = _MR_IID_RE.search((pr_url_for(session_id) or "").strip())
        if not m:
            return False
        token = (get_token() or "").strip()
        if not token:
            return False
        merged = merge_mr(base, token, repo, int(m.group(1)))
        if merged and issue_iid_for:
            iid = issue_iid_for(session_id)
            if iid:
                close_issue(base, token, repo, int(iid))   # 備援：確保 issue 真的關掉
        return merged

    return do_merge


def list_closed_issues(base: str, token: str, repo: str, *, max_pages: int = 5) -> list[tuple[int, str]]:
    """列 GitLab project 的 closed issue (iid, title)。冪等重跑：issue 已關 = 已交付 → 跳過。失敗回 []。"""
    pid = urllib.parse.quote(repo, safe="")
    out: list[tuple[int, str]] = []
    try:
        for page in range(1, max_pages + 1):
            url = (f"{base}/api/v4/projects/{pid}/issues"
                   f"?state=closed&per_page=100&page={page}")
            req = urllib.request.Request(url, headers=_gl_headers(token), method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                items = json.loads(resp.read().decode("utf-8"))
            if not items:
                break
            for it in items:
                iid = it.get("iid")
                if isinstance(iid, int):
                    out.append((iid, it.get("title") or ""))
            if len(items) < 100:
                break
    except Exception:                            # noqa: BLE001
        return []
    return out


def list_active_mr_issue_iids(base: str, token: str, repo: str, *, max_pages: int = 5) -> set[int]:
    """列「opened 或 merged MR 的 description 透過 Closes/Fixes #N 連到的 issue iid」。
    冪等重跑：開著的 MR = 進行中；merged 的 MR = 已交付（GitLab auto-close 不穩，issue 可能沒關，
    故不能只靠 issue 狀態）。兩者都跳過、不重做。closed/declined（未 merge 就關）不算。失敗回 set()。"""
    pid = urllib.parse.quote(repo, safe="")
    out: set[int] = set()
    try:
        for state in ("opened", "merged"):
            for page in range(1, max_pages + 1):
                url = (f"{base}/api/v4/projects/{pid}/merge_requests"
                       f"?state={state}&per_page=100&page={page}")
                req = urllib.request.Request(url, headers=_gl_headers(token), method="GET")
                with urllib.request.urlopen(req, timeout=20) as resp:
                    items = json.loads(resp.read().decode("utf-8"))
                if not items:
                    break
                for mr in items:
                    for m in _CLOSES_RE.finditer(mr.get("description") or ""):
                        out.add(int(m.group(1)))
                if len(items) < 100:
                    break
    except Exception:                            # noqa: BLE001
        return set()
    return out
