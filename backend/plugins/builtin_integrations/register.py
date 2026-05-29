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


def _publish_stub(target: str):
    """Jira / GitLab：保留 stub（success=False）。M3+ 接真實。"""
    def publish(items: list[DeliveryItem], config: dict[str, str]) -> DeliveryPublishResult:
        return DeliveryPublishResult(success=False, target=target, count=len(items), created=[])
    return publish


_GITHUB = IntegrationSpec(
    target="github",
    preview=_preview("github", "repo"),
    publish=_publish_github,
    config_schema={
        "fields": [
            {"key": "repo", "label": "Repository (owner/repo)", "type": "text", "required": True},
            {"key": "token", "label": "Personal Access Token", "type": "password", "required": True},
        ]
    },
    description="Publish delivery items as GitHub issues.",
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
    preview=_preview("gitlab", "project_id"),
    publish=_publish_stub("gitlab"),
    config_schema={
        "fields": [
            {"key": "base_url", "label": "GitLab Base URL", "type": "text", "required": False},
            {"key": "project_id", "label": "Project ID", "type": "text", "required": True},
            {"key": "token", "label": "Access Token", "type": "password", "required": True},
        ]
    },
    description="Publish delivery items as GitLab issues.",
)


def register(host: PluginHost) -> None:
    host.register_integration(_GITHUB)
    host.register_integration(_JIRA)
    host.register_integration(_GITLAB)
