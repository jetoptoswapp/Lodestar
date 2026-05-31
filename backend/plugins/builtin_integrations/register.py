"""builtin_integrations：github / jira / gitlab delivery targets（dogfood）。

證明 plugin API 夠用：只 import plugin_api，不碰任何 host 內部模組。
preview-before-publish —— preview 回「將建立什麼」的結構化清單。

M2.5：GitHub real publish 上線（用 PAT 呼 POST /repos/{owner}/{repo}/issues）。
Jira / GitLab 仍為安全佔位，待真實憑證與測試環境再接（M3+）。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from plugin_api import (
    DeliveryItem,
    DeliveryPublishResult,
    IntegrationSpec,
    PluginHost,
)

log = logging.getLogger("builtin_integrations")


def _preview(target: str, ref_key: str):
    """產生一個 preview 函式：把每個 DeliveryItem 攤成「將建立的 issue」預覽列。"""
    def preview(items: list[DeliveryItem], config: dict[str, str]) -> list[dict]:
        dest = config.get(ref_key, f"<{ref_key}>")
        return [
            {
                "target": target,
                "destination": dest,
                "title": it.title,
                "labels": list(it.labels),
                "estimate": it.estimate,
                "group": it.group,
                "body_preview": it.body[:200],
            }
            for it in items
        ]
    return preview


# ============================================================
#  GitHub real publish（M2.5）
# ============================================================
def _publish_github(items: list[DeliveryItem], config: dict[str, str]) -> DeliveryPublishResult:
    """POST /repos/{owner}/{repo}/issues 對每個 DeliveryItem 建一個 issue。

    config:
      - repo (str)        "owner/repo"，必填
      - token (str)       GitHub PAT，需 repo / public_repo scope，必填
      - dry_run (truthy)  不真打 API（test / preview 用），回 placeholder URLs

    回傳 created = 成功建立的 issue URL 列表；任一失敗 → success=False 但仍回部分結果。
    """
    repo = (config.get("repo") or "").strip()
    token = (config.get("token") or "").strip()
    dry_run = bool(config.get("dry_run"))

    if not repo or "/" not in repo:
        return DeliveryPublishResult(
            success=False, target="github", count=len(items), created=[],
        )
    if not token and not dry_run:
        return DeliveryPublishResult(
            success=False, target="github", count=len(items), created=[],
        )

    if dry_run:
        return DeliveryPublishResult(
            success=True, target="github", count=len(items),
            created=[f"https://github.com/{repo}/issues/dry-run-{i+1}" for i in range(len(items))],
        )

    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "lodestar-delivery-publisher",
        "Content-Type": "application/json",
    }

    created: list[str] = []
    any_failed = False
    for it in items:
        payload = {
            "title": it.title,
            "body": it.body,
            "labels": list(it.labels),
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                issue_url = body.get("html_url") or ""
                if issue_url:
                    created.append(issue_url)
                    it.github_issue_number = int(body.get("number") or 0)
                    it.github_repo = repo
                    it.github_url = issue_url
                else:
                    any_failed = True
        except urllib.error.HTTPError as e:
            any_failed = True
            try:
                msg = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                msg = str(e)
            log.warning("github publish HTTPError %s: %s — story=%s", e.code, msg, it.title)
        except Exception as e:  # noqa: BLE001
            any_failed = True
            log.warning("github publish error: %s — story=%s", e, it.title)

    return DeliveryPublishResult(
        success=(not any_failed) and bool(created),
        target="github",
        count=len(items),
        created=created,
    )


def _create_github_repo(config: dict[str, str], name: str,
                        visibility: str = "private", owner: str = "") -> str:
    """建立 GitHub repo → 回 full_name（owner/repo）。

    owner 空 → 個人帳號 POST /user/repos；非空 → 組織 POST /orgs/{owner}/repos。
    auto_init=true 讓 repo 有初始 main commit（implement clone 才有 base 可改）。
    visibility=="public" → 公開；否則 private（internal 等 GitLab 用）。
    """
    token = (config.get("token") or "").strip()
    if not token:
        raise RuntimeError("缺 GitHub token（keystore 無 github 憑證）")
    org = (owner or "").strip()
    url = f"https://api.github.com/orgs/{org}/repos" if org else "https://api.github.com/user/repos"
    payload = {"name": name, "private": visibility != "public", "auto_init": True}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lodestar-delivery-publisher",
            "Content-Type": "application/json",
        }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            msg = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            msg = str(exc)
        raise RuntimeError(f"GitHub create repo failed ({exc.code}): {msg}") from exc
    full_name = body.get("full_name") or ""
    if not full_name:
        raise RuntimeError("GitHub create repo 回應無 full_name")
    return full_name


_GITLAB_DEFAULT = "https://gitlab.com"


def _gitlab_base(config: dict[str, str]) -> str:
    return (config.get("base_url") or _GITLAB_DEFAULT).rstrip("/")


def _gitlab_req(url: str, token: str, *, data=None, method: str = "GET"):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
        "User-Agent": "lodestar-delivery-publisher",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _create_gitlab_repo(config: dict[str, str], name: str,
                        visibility: str = "private", owner: str = "") -> str:
    """建立 GitLab project → 回 path_with_namespace。owner 非空 → 先查 namespace_id 建在 group 下。"""
    token = (config.get("token") or "").strip()
    if not token:
        raise RuntimeError("缺 GitLab token")
    base = _gitlab_base(config)
    vis = visibility if visibility in ("public", "internal", "private") else "private"
    payload: dict = {"name": name, "visibility": vis}
    org = (owner or "").strip()
    if org:
        try:
            ns = _gitlab_req(f"{base}/api/v4/namespaces/{urllib.parse.quote(org, safe='')}", token)
            payload["namespace_id"] = ns.get("id")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitLab namespace '{org}' 查詢失敗 ({exc.code})") from exc
    try:
        body = _gitlab_req(f"{base}/api/v4/projects", token, data=payload, method="POST")
    except urllib.error.HTTPError as exc:
        try:
            msg = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            msg = str(exc)
        raise RuntimeError(f"GitLab create project failed ({exc.code}): {msg}") from exc
    full = body.get("path_with_namespace") or ""
    if not full:
        raise RuntimeError("GitLab create project 回應無 path_with_namespace")
    return full


def _publish_gitlab(items: list[DeliveryItem], config: dict[str, str]) -> DeliveryPublishResult:
    """POST /projects/{url-encoded path}/issues 對每個 DeliveryItem 建 issue。"""
    repo = (config.get("repo") or "").strip()
    token = (config.get("token") or "").strip()
    if not repo or not token:
        return DeliveryPublishResult(success=False, target="gitlab", count=len(items), created=[])
    base = _gitlab_base(config)
    pid = urllib.parse.quote(repo, safe="")
    created: list[str] = []
    any_failed = False
    for it in items:
        try:
            body = _gitlab_req(
                f"{base}/api/v4/projects/{pid}/issues", token,
                data={"title": it.title, "description": it.body, "labels": ",".join(it.labels)},
                method="POST")
            url = body.get("web_url") or ""
            if url:
                created.append(url)
                it.gitlab_issue_url = url
                it.gitlab_issue_iid = int(body.get("iid") or 0)
            else:
                any_failed = True
        except Exception as exc:  # noqa: BLE001
            any_failed = True
            log.warning("gitlab publish error: %s — story=%s", exc, it.title)
    return DeliveryPublishResult(success=(not any_failed) and bool(created),
                                 target="gitlab", count=len(items), created=created)


def _publish_stub(target: str):
    """Jira：保留 stub（success=False）。M3+ 接真實。"""
    def publish(items: list[DeliveryItem], config: dict[str, str]) -> DeliveryPublishResult:
        return DeliveryPublishResult(success=False, target=target, count=len(items), created=[])
    return publish


_GITHUB = IntegrationSpec(
    target="github",
    preview=_preview("github", "repo"),
    publish=_publish_github,
    create_repo=_create_github_repo,
    config_schema={
        "fields": [
            {"key": "token", "label": "Personal Access Token", "type": "password", "required": True},
        ]
    },
    description="GitHub：發 issue / 開 PR。repo 由各專案設定（可開新或指向既有）。",
)

_JIRA = IntegrationSpec(
    target="jira",
    preview=_preview("jira", "project_key"),
    publish=_publish_stub("jira"),
    config_schema={
        "fields": [
            {"key": "base_url", "label": "Jira Base URL", "type": "text", "required": True},
            {"key": "project_key", "label": "Project Key", "type": "text", "required": True},
            {"key": "email", "label": "Account Email", "type": "text", "required": True},
            {"key": "api_token", "label": "API Token", "type": "password", "required": True},
        ]
    },
    description="Publish delivery items as Jira issues.",
)

_GITLAB = IntegrationSpec(
    target="gitlab",
    preview=_preview("gitlab", "repo"),
    publish=_publish_gitlab,
    create_repo=_create_gitlab_repo,
    config_schema={
        "fields": [
            {"key": "token", "label": "Access Token", "type": "password", "required": True},
            {"key": "base_url", "label": "GitLab Base URL（self-hosted 才填）", "type": "text", "required": False},
        ]
    },
    description="GitLab：發 issue / 開 MR。project 由各專案設定（可開新或指向既有）。",
)


def register(host: PluginHost) -> None:
    host.register_integration(_GITHUB)
    host.register_integration(_JIRA)
    host.register_integration(_GITLAB)
