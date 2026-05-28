"""pytest 共用設定 / fixtures。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 保險：確保 backend/ 在 sys.path（pyproject pythonpath 已設，這裡雙保險）
_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """每個測試用獨立的臨時 SQLite DB（migrate 後 yield 路徑）。"""
    db = tmp_path / "test.db"
    monkeypatch.setenv("AITOOL_DB", str(db))
    from persistence import migrations
    migrations.migrate()
    yield str(db)
