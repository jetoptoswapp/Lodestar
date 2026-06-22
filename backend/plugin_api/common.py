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
    count: int                              # 本次嘗試建立的項目數（host 冪等過濾後 = 待建數）
    created: list[str]
    # 冪等：host 端依既有 issue 比對後跳過的數量（已存在、未重建）。
    skipped: int = 0
    # 逐項失敗 [(title, reason)]，讓使用者看得到「哪幾個、為什麼」失敗（而非只有數字）。
    failed: list = field(default_factory=list)
