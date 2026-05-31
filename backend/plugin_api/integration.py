from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional

from plugin_api.common import DeliveryItem, DeliveryPublishResult


@dataclass(frozen=True)
class IntegrationSpec:
    target: str                     # github / jira / gitlab / 第三方
    preview: Callable[[list[DeliveryItem], dict[str, str]], list[dict]]
    publish: Callable[[list[DeliveryItem], dict[str, str]], DeliveryPublishResult]
    config_schema: dict = field(default_factory=dict)   # 前端自動 render 設定表單
    description: str = ""
    # 可選：建立新 repo（config, name, visibility, owner） -> repo_full_name（owner/repo）。
    # owner 空 = 建在 token 對應的個人帳號；非空 = 建在該 org/group。jira 等無 repo 概念者留 None。
    create_repo: Optional[Callable[[dict, str, str, str], str]] = None
