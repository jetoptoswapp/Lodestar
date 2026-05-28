from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class DeliveryItem:
    """一筆可發佈到 tracker 的交付項目（逐字沿用 ver2 artifacts.py）。"""
    title: str
    body: str
    estimate: int
    group: str
    labels: list[str]
    target_project: str = ""
    senior_rd_days: float = 1.5
    requirement_refs: list[str] = field(default_factory=list)
    requirement_source: str = "unmapped"
    jira_key: str = ""
    github_issue_number: int = 0
    github_repo: str = ""
    github_url: str = ""
    gitlab_issue_url: str = ""
    gitlab_issue_iid: int = 0
    gitlab_project_id: str = ""


@dataclass(frozen=True)
class DeliveryPublishResult:
    success: bool
    target: str
    count: int
    created: list[str]
