"""Server-side credential keystore（Fernet 對稱加密）。

取代「token 存瀏覽器 localStorage」：integration 機密（如 GitHub PAT）只存在後端、
加密落地於 SQLite（integration_secrets 表），明文永不回傳前端。

加密金鑰來源（依序）：
  1. 環境變數 LODESTAR_KEYSTORE_KEY（base64 Fernet key）—— 正式部署建議用此，
     由外部秘密管理（k8s secret / vault / env）注入，金鑰不落地於專案目錄。
  2. backend/data/.keystore.key —— 本機開發自動生成（檔案權限 0600）。

設計鐵則（spec §2）：host owns all I/O。本模組屬 host 層，plugin 不得 import。
"""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from persistence import dal

_KEY_ENV = "LODESTAR_KEYSTORE_KEY"

_fernet: Optional[Fernet] = None


def _key_file() -> Path:
    """key 檔跟著 DB 所在資料夾走（與 uploads_dir 一致）——測試用 temp DB 時 key 也隔離。"""
    return Path(dal.db_path()).parent / ".keystore.key"


def _load_or_create_key() -> bytes:
    """取得 Fernet 金鑰：env 優先；否則讀本機 key 檔；都沒有則生成並以 0600 落地。"""
    env = os.environ.get(_KEY_ENV)
    if env:
        return env.encode("ascii") if isinstance(env, str) else env
    kf = _key_file()
    if kf.exists():
        return kf.read_bytes().strip()
    key = Fernet.generate_key()
    kf.parent.mkdir(parents=True, exist_ok=True)
    kf.write_bytes(key)
    try:
        os.chmod(kf, stat.S_IRUSR | stat.S_IWUSR)  # 0600：僅擁有者可讀寫
    except OSError:
        pass  # 某些檔系統不支援 chmod；金鑰仍存在，僅權限未收緊
    return key


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def reset_cache() -> None:
    """清掉快取的 Fernet（測試在切換 key / DB 後呼叫）。"""
    global _fernet
    _fernet = None


def set_credentials(target: str, config: dict) -> None:
    """加密整份 config（dict）並覆寫儲存。"""
    blob = json.dumps(config, ensure_ascii=False).encode("utf-8")
    token = _f().encrypt(blob).decode("ascii")
    dal.set_integration_secret(target, token, time.time())


def get_credentials(target: str) -> dict:
    """解密回 config dict；無資料或解密失敗（金鑰換掉 / 毀損）回 {}（不拋例外）。"""
    token = dal.get_integration_secret(target)
    if not token:
        return {}
    try:
        blob = _f().decrypt(token.encode("ascii"))
        data = json.loads(blob.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (InvalidToken, ValueError):
        return {}


def delete_credentials(target: str) -> bool:
    return dal.delete_integration_secret(target)


def has_credentials(target: str) -> bool:
    return bool(get_credentials(target))
