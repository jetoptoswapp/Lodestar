"""plugin loader：semver range、載入 builtin、壞 plugin 隔離、host_api 不符 skip。"""
from __future__ import annotations

import plugin_loader as L

_GOOD = (
    "from plugin_api import IntegrationSpec\n"
    "def register(h):\n"
    "    h.register_integration(IntegrationSpec(target='good_t', preview=lambda i, c: [], publish=lambda i, c: None))\n"
)
_BAD = (
    "def register(h):\n"
    "    raise RuntimeError('boom')\n"
)


def _write_plugin(root, pid, reg_body, host_api=">=1.0,<2.0"):
    d = root / pid
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / "plugin.toml").write_text(
        f'[plugin]\n'
        f'id = "{pid}"\n'
        f'name = "{pid}"\n'
        f'version = "1.0.0"\n'
        f'host_api = "{host_api}"\n'
        f'entry_module = "{pid}.reg"\n'
        f'requires_plugins = []\n',
        encoding="utf-8",
    )
    (d / "reg.py").write_text(reg_body, encoding="utf-8")
    return d


def test_semver_range():
    assert L.host_api_satisfied(">=1.0,<2.0") is True
    assert L.host_api_satisfied(">=1.0,<2.0", (1, 9, 0)) is True
    assert L.host_api_satisfied(">=1.0,<2.0", (2, 0, 0)) is False
    assert L.host_api_satisfied(">=2.0") is False
    assert L.host_api_satisfied("==1.0.0") is True


def test_load_builtin_integrations(tmp_db):
    reg = L.load_all()
    assert {"github", "jira", "gitlab"} <= set(reg.integrations)
    loaded = {p.manifest.id for p in reg.loaded_plugins if p.loaded}
    assert "builtin_integrations" in loaded


def test_bad_plugin_isolated(tmp_path, monkeypatch, tmp_db):
    pdir = tmp_path / "plugins"
    _write_plugin(pdir, "good_p", _GOOD)
    _write_plugin(pdir, "bad_p", _BAD)
    monkeypatch.setattr(L, "_PLUGINS_DIR", pdir)
    monkeypatch.syspath_prepend(str(pdir))
    reg = L.load_all()
    by_id = {p.manifest.id: p for p in reg.loaded_plugins}
    # 好 plugin 正常載入；壞 plugin 被隔離但不影響好的、也不打掛流程
    assert "good_t" in reg.integrations
    assert by_id["good_p"].loaded is True
    assert by_id["bad_p"].loaded is False
    assert by_id["bad_p"].error


def test_host_api_incompatible_skipped(tmp_path, monkeypatch, tmp_db):
    pdir = tmp_path / "plugins"
    _write_plugin(pdir, "old_p", _GOOD, host_api=">=2.0")
    monkeypatch.setattr(L, "_PLUGINS_DIR", pdir)
    monkeypatch.syspath_prepend(str(pdir))
    reg = L.load_all()
    by_id = {p.manifest.id: p for p in reg.loaded_plugins}
    assert by_id["old_p"].loaded is False
    assert "host_api" in by_id["old_p"].error
    assert "good_t" not in reg.integrations  # host_api 不符 → 根本不註冊
