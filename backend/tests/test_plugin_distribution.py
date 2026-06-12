"""M4：第三方 plugin 打包 / 分發 —— disabled skip / entry-point / PATCH reload。

用 rca_domain（目錄型、非 builtin、提供 stages + workflows）當「丟目錄即被發現 + 可開關」的代表。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app as appmod
import plugin_loader as L
from persistence import dal


# ============================================================
#  目錄型 plugin（rca_domain）—— 證明丟目錄即被發現
# ============================================================
def test_directory_plugin_discovered(tmp_db):
    reg = L.load_all()
    by_id = {p.manifest.id: p for p in reg.loaded_plugins}
    assert "rca_domain" in by_id
    assert by_id["rca_domain"].loaded is True
    assert by_id["rca_domain"].discovery == "directory"
    assert "rca_intake" in reg.stages


def test_domain_stages_not_in_default_workflow(tmp_db):
    """RCA stage 存在 catalog，但不污染 default workflow。"""
    reg = L.load_all()
    assert reg.workflows["default"].stages == ("prd", "architecture", "ui_design", "stories")


# ============================================================
#  disabled plugin skip
# ============================================================
def test_disabled_plugin_skips_register(tmp_db):
    dal.set_plugin_enabled("rca_domain", False)
    reg = L.load_all()
    by_id = {p.manifest.id: p for p in reg.loaded_plugins}
    # 仍列在 loaded_plugins，但 disabled / 未 register
    assert "rca_domain" in by_id
    assert by_id["rca_domain"].disabled is True
    assert by_id["rca_domain"].loaded is False
    # rca stage 不在 catalog
    assert "rca_intake" not in reg.stages


def test_disabled_plugin_clears_contributions(tmp_db):
    """停用後 plugin_contributions 不應殘留（否則 UI 誤顯示）。"""
    L.load_all()
    assert any(c["plugin_id"] == "rca_domain" for c in dal.contributions())
    dal.set_plugin_enabled("rca_domain", False)
    L.load_all()
    assert not any(c["plugin_id"] == "rca_domain" for c in dal.contributions())


def test_reenable_plugin_restores_stage(tmp_db):
    dal.set_plugin_enabled("rca_domain", False)
    reg = L.load_all()
    assert "rca_intake" not in reg.stages
    dal.set_plugin_enabled("rca_domain", True)
    reg = L.load_all()
    assert "rca_intake" in reg.stages


# ============================================================
#  entry-point discovery
# ============================================================
def test_entry_point_discovery_empty_by_default(tmp_db):
    """無 pip-installed plugin 時 entry-point 掃描回空（不影響開發環境）。"""
    eps = L._discover_entry_points()
    assert isinstance(eps, list)
    assert all(p.discovery == "entry_point" for p in eps)


def test_entry_point_discovery_picks_up_installed(tmp_db, tmp_path, monkeypatch):
    """模擬一個 pip-installed plugin（entry-point 指向含 plugin.toml 的 package）。"""
    pkg = tmp_path / "fake_ext_plugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "plugin.toml").write_text(
        '[plugin]\n'
        'id = "fake_ext"\n'
        'name = "Fake External"\n'
        'version = "0.1.0"\n'
        'host_api = ">=1.0,<2.0"\n'
        'entry_module = "fake_ext_plugin.reg"\n'
        'requires_plugins = []\n',
        encoding="utf-8",
    )
    (pkg / "reg.py").write_text(
        "from plugin_api import IntegrationSpec\n"
        "def register(h):\n"
        "    h.register_integration(IntegrationSpec(target='fake_ext_target', "
        "preview=lambda i, c: [], publish=lambda i, c: None))\n",
        encoding="utf-8",
    )

    fake_module = MagicMock()
    fake_module.__file__ = str(pkg / "__init__.py")
    fake_ep = MagicMock()
    fake_ep.name = "fake_ext"
    fake_ep.value = "fake_ext_plugin"

    eps_obj = MagicMock()
    eps_obj.select.return_value = [fake_ep]

    monkeypatch.syspath_prepend(str(tmp_path))
    with patch.object(L.importlib.metadata, "entry_points", return_value=eps_obj), \
         patch.object(L.importlib, "import_module", return_value=fake_module):
        discovered = L._discover_entry_points()

    assert len(discovered) == 1
    assert discovered[0].manifest.id == "fake_ext"
    assert discovered[0].discovery == "entry_point"


# ============================================================
#  PATCH /api/plugins/{id} —— enable / disable + hot-reload
# ============================================================
def test_patch_disable_removes_stage_from_catalog(tmp_db):
    with TestClient(appmod.app) as c:
        stages = [s["id"] for s in c.get("/api/stages").json()["stages"]]
        assert "rca_intake" in stages

        r = c.patch("/api/plugins/rca_domain", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

        stages2 = [s["id"] for s in c.get("/api/stages").json()["stages"]]
        assert "rca_intake" not in stages2


def test_patch_enable_restores_stage(tmp_db):
    with TestClient(appmod.app) as c:
        c.patch("/api/plugins/rca_domain", json={"enabled": False})
        assert "rca_intake" not in [s["id"] for s in c.get("/api/stages").json()["stages"]]
        r = c.patch("/api/plugins/rca_domain", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        assert "rca_intake" in [s["id"] for s in c.get("/api/stages").json()["stages"]]


def test_patch_builtin_disable_rejected(tmp_db):
    with TestClient(appmod.app) as c:
        r = c.patch("/api/plugins/builtin_core_stages", json={"enabled": False})
        assert r.status_code == 409
        assert r.json()["detail"]["category"] == "plugin_is_builtin"
        assert "prd" in [s["id"] for s in c.get("/api/stages").json()["stages"]]


def test_patch_unknown_plugin_404(tmp_db):
    with TestClient(appmod.app) as c:
        r = c.patch("/api/plugins/nonexistent", json={"enabled": False})
        assert r.status_code == 404
        assert r.json()["detail"]["category"] == "plugin_not_found"


def test_plugins_list_marks_builtin_and_discovery(tmp_db):
    with TestClient(appmod.app) as c:
        plugins = {p["id"]: p for p in c.get("/api/plugins").json()["plugins"]}
        assert plugins["builtin_core_stages"]["builtin"] is True
        assert plugins["rca_domain"]["builtin"] is False
        assert plugins["rca_domain"]["discovery"] == "directory"
        assert "rca_intake" in plugins["rca_domain"]["provides"]["stages"]
