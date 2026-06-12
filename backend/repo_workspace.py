"""共用 repo workspace（host 層）。

clone 既有 repo 到「一專案一份」的工作目錄，供兩端共用：
- sync 生成端（workflow_engine 的讀碼 stage）唯讀讀 codebase；
- async 實作端（async_runtime）切 branch 改 code 開 PR。

抽成獨立 host 模組的理由（設計鐵則④）：sync runtime 禁止 import async_runtime，
故 clone 邏輯不能留在 async_runtime/orchestrator.py。兩端都 import 此模組，彼此零 cross-import。

純 stdlib + git subprocess，無任一 runtime 依賴。remote_url 已含 token（呼叫端組好），
token 在 url，錯誤訊息不回顯。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from persistence import dal


def project_dir(thread_id: str) -> Path:
    """一個專案（thread）共用一個工作根目錄：impl_work/{thread_id}。"""
    return dal.uploads_dir().parent / "impl_work" / thread_id


def project_clone_dir(thread_id: str) -> Path:
    """專案共用 clone 目錄（整個專案 clone 一次，所有 batch / story / 讀碼 stage 沿用）。"""
    return project_dir(thread_id) / "repo"


def prepare_project_clone(thread_id: str, remote_url: str) -> Path:
    """專案 clone：不存在 → git clone；已存在 → git fetch 沿用（一個專案一個目錄，重跑不重 clone）。

    remote_url 已含 token（呼叫端依 target 組 github/gitlab url）；token 在 url，錯誤訊息不回顯。
    既有 clone 損毀（非 git repo）→ 移除重 clone（自癒）。"""
    dest = project_clone_dir(thread_id)
    is_repo = dest.exists() and (dest / ".git").exists()
    if is_repo:
        fetch = subprocess.run(["git", "-C", str(dest), "fetch", "origin"],
                               capture_output=True, text=True, timeout=180)
        if fetch.returncode == 0:
            return dest
        shutil.rmtree(dest, ignore_errors=True)        # fetch 失敗（remote 變動等）→ 重 clone
    elif dest.exists():
        shutil.rmtree(dest, ignore_errors=True)        # 殘留非 git 目錄 → 清掉重 clone
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "clone", remote_url, str(dest)],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed (exit {proc.returncode})")   # 不回顯 stderr（含 token url）
    return dest
