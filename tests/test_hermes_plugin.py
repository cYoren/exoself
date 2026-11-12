"""The Hermes plugin: one draw per profile, one managed block, one event a turn.

The plugin is not imported the way Hermes imports it (``hermes_plugins.exoself``
under the profile's scope), but it is imported the same way in every respect
that matters here: the package, its sibling module, and the one thing it takes
from the host, ``hermes_constants.get_hermes_home``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "hermes"
BEGIN = "<!-- exoself:begin"


def _load_plugin():
    name = "hermes_plugins.exoself"
    if name in sys.modules:
        return sys.modules[name]
    namespace = types.ModuleType("hermes_plugins")
    namespace.__path__ = []
    sys.modules.setdefault("hermes_plugins", namespace)
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    """The plugin, with this profile's home pointed at a temporary directory."""
    host = types.ModuleType("hermes_constants")
    host.get_hermes_home = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "hermes_constants", host)
    # The plugin calls the library in-process; point the import at this checkout.
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    module = _load_plugin()
    return module


def _cert(home: Path) -> dict:
    return json.loads((home / ".exoself" / "birth.json").read_text(encoding="utf-8"))


def test_two_profiles_get_two_temperaments(plugin, tmp_path):
    homes = [tmp_path / "alpha", tmp_path / "beta"]
    for home in homes:
        home.mkdir()
        plugin.profile.ensure(home)

    ids = [_cert(home)["id"] for home in homes]
    assert ids[0] != ids[1], "two profiles drew the same agent"
    for home in homes:
        ok, detail = plugin.profile.verify(home)
        assert ok, detail


def test_a_second_load_changes_nothing(plugin, tmp_path):
    plugin.profile.ensure(tmp_path)
    cert, soul = tmp_path / ".exoself" / "birth.json", tmp_path / "SOUL.md"
    before = (cert.read_bytes(), cert.stat().st_mtime_ns, soul.read_bytes())

    plugin.profile.ensure(tmp_path)
    after = (cert.read_bytes(), cert.stat().st_mtime_ns, soul.read_bytes())

    assert before == after, "a second load rewrote the certificate or the block"
    assert soul.read_text(encoding="utf-8").count(BEGIN) == 1


def test_the_users_own_text_is_kept(plugin, tmp_path):
    soul = tmp_path / "SOUL.md"
    soul.write_text("# My assistant\n\nBe brief.\n", encoding="utf-8")

    plugin.profile.ensure(tmp_path)

    text = soul.read_text(encoding="utf-8")
    assert text.startswith("# My assistant\n\nBe brief.\n")
    assert text.count(BEGIN) == 1
    assert "## Temperament" in text


def test_a_session_end_records_one_event_and_refreshes_the_block(plugin, tmp_path):
    plugin.profile.ensure(tmp_path)
    plugin._remember_ask(session_id="s1", user_message="Fix the failing\n  auth test")
    plugin._on_session_end(desire=False, session_id="s1", platform="cli",
                           completed=True, failed=False, interrupted=False)

    state = json.loads((tmp_path / ".exoself" / "state.json").read_text(encoding="utf-8"))
    assert state["events"] == 1

    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1
    assert "## Temperament" in text
    assert "## Wants" not in text, "desire is off unless the operator turns it on"


def test_desire_is_off_until_the_operator_turns_it_on(plugin, tmp_path):
    plugin.profile.ensure(tmp_path)
    assert "## Wants" not in (tmp_path / "SOUL.md").read_text(encoding="utf-8")

    plugin.profile.ensure(tmp_path, desire=True)
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert "## Wants" in text
    assert text.count(BEGIN) == 1


def test_no_tool_draws_a_seed(plugin, tmp_path):
    """Status and verify are read-only, even before anything was drawn."""
    plugin._status_tool({})
    plugin._verify_tool({})
    assert not (tmp_path / ".exoself" / "birth.json").exists()


def test_the_plugin_never_spawns_a_process(plugin, tmp_path, monkeypatch):
    """A child would inherit the host's environment and expose the ask on argv."""
    import subprocess

    def explode(*args, **kwargs):
        raise AssertionError("the plugin must not spawn a process")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)
    monkeypatch.setattr(subprocess, "call", explode)

    plugin.profile.ensure(tmp_path)
    plugin.profile.develop(tmp_path, "a turn that must stay in this process")
    assert plugin.profile.status(tmp_path)
    ok, detail = plugin.profile.verify(tmp_path)
    assert ok, detail


def test_the_event_is_one_line(plugin):
    plugin._remember_ask(session_id="s", user_message="  a\n\nb\tc  ")
    assert plugin.event_line(session_id="s") == "a b c"
    assert plugin.event_line(session_id="gone", platform="telegram", interrupted=True) == (
        "a turn on telegram (interrupted)")
