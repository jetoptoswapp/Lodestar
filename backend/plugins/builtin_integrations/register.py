"""builtin_integrations：github / jira / gitlab delivery targets（dogfood）。

證明 plugin API 夠用：只 import plugin_api，不碰任何 host 內部模組。
preview-before-publish —— preview 回「將建立什麼」的結構化清單；M0 的 publish 為
安全佔位（不真打外部 API），M5 接真實發佈。
"""
from __future__ import annotations

from plugin_api import (
    DeliveryItem,
    DeliveryPublishResult,
    IntegrationSpec,
    PluginHost,
)


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


def _publish_stub(target: str):
    def publish(items: list[DeliveryItem], config: dict[str, str]) -> DeliveryPublishResult:
        # M0 佔位：不真打外部 API。M5 接真實 publish（建立 issue 並回傳 URL）。
        return DeliveryPublishResult(success=False, target=target, count=len(items), created=[])
    return publish


_GITHUB = IntegrationSpec(
    target="github",
    preview=_preview("github", "repo"),
    publish=_publish_stub("github"),
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
