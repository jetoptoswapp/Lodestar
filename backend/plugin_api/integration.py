from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

from plugin_api.common import DeliveryItem, DeliveryPublishResult


@dataclass(frozen=True)
class IntegrationSpec:
    target: str                     # github / jira / gitlab / 第三方
    preview: Callable[[list[DeliveryItem], dict[str, str]], list[dict]]
    publish: Callable[[list[DeliveryItem], dict[str, str]], DeliveryPublishResult]
    config_schema: dict = field(default_factory=dict)   # 前端自動 render 設定表單
    description: str = ""
