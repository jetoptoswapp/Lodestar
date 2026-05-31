"""per-project delivery repo 解析（host 層）。

resolve_project_repo：依專案的 delivery 設定回 (target, repo_full_name)。
- existing：直接回設定的 owner/repo。
- new 且未建：用 integration.create_repo + keystore token lazy 建 repo，回填 DB（repo_created=1）。
- new 且已建：回回填的 full_name（冪等，不重複建）。
- 未設定 / 缺 token / 不支援建 repo：raise DeliveryRepoError（endpoint 轉 400）。
"""
from __future__ import annotations

import re

import keystore
from persistence import dal


class DeliveryRepoError(RuntimeError):
    """delivery repo 無法解析（未設定 / 缺 token / integration 不支援建 repo）。"""


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (text or "").strip()).strip("-").lower()
    return (s or "lodestar-project")[:90]


def resolve_project_repo(registry, thread_id: str) -> tuple[str, str]:
    proj = dal.get_project(thread_id)
    if proj is None:
        raise DeliveryRepoError(f"thread '{thread_id}' 不存在")
    target = (proj.get("delivery_target") or "").strip()
    if not target:
        raise DeliveryRepoError("此專案未設定 delivery repo（先設 target + repo 模式）")
    full = (proj.get("repo_full_name") or "").strip()
    mode = (proj.get("repo_mode") or "").strip()

    if mode == "existing":
        if not full:
            raise DeliveryRepoError("existing 模式但未填 repo（owner/repo）")
        return target, full
    if mode != "new":
        raise DeliveryRepoError(f"未知 repo_mode '{mode}'（需 new / existing）")
    if proj.get("repo_created"):
        return target, full                          # 已 lazy 建過，冪等回回填值

    integ = registry.integrations.get(target)
    if integ is None or getattr(integ, "create_repo", None) is None:
        raise DeliveryRepoError(f"integration '{target}' 不支援建立 repo")
    creds = keystore.get_credentials(target)
    if not creds.get("token"):
        raise DeliveryRepoError(f"integration '{target}' 尚無 token（先到 INTEGRATIONS 設定）")

    name = (full.split("/")[-1] if full else "") or _slug(proj.get("name"))
    owner = (proj.get("repo_owner") or "").strip()
    visibility = (proj.get("repo_visibility") or "private").strip()
    created_full = integ.create_repo(creds, name, visibility, owner)
    dal.set_project_repo_created(thread_id, created_full)
    return target, created_full
