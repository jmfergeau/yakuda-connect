#!/usr/bin/env python3
"""
tests/test_controls_tab.py — Controls-Tab (XR HOTAS / obah)
===========================================================
Geprueft wird der Weg eines Schalters:
  an + installiert        -> bleibt an, wird gemerkt
  an + nicht installiert  -> Rueckfrage -> Installation ueber den Tools-Tab
  Abbruch / Fehler        -> Schalter geht wieder aus
  im Tools-Tab geloescht  -> Schalter geht aus
"""
import os
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app(qapp, tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    os.environ["HOME"] = str(home)
    from PySide6.QtWidgets import QMessageBox
    for m in ("warning", "information", "critical"):
        setattr(QMessageBox, m, staticmethod(lambda *a, **k: QMessageBox.Ok))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    from main import VRApp
    window = VRApp()
    yield window
    window.close()


class FakeWorker:
    def __init__(self, running=True):
        self._running = running

    def isRunning(self):
        return self._running


@pytest.fixture
def env(app, monkeypatch):
    """Installationszustand + Dialogantwort steuerbar machen."""
    import appimage_installer as appimg
    from PySide6.QtWidgets import QMessageBox
    state = {"installed": set(), "answer": "cargo", "asked": [], "installs": []}

    monkeypatch.setattr(appimg, "installed_locally",
                        lambda tool: tool.get("key") in state["installed"])

    def fake_exec(box, *a, **k):
        state["asked"].append(box.windowTitle())
        for btn in box.buttons():
            if state["answer"] and btn.text() == {"cargo": "Cargo", "yay": "yay (AUR)"}.get(state["answer"]):
                btn.click()
                return 0
        for btn in box.buttons():
            if box.buttonRole(btn) == QMessageBox.RejectRole:
                btn.click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    def fake_install_tool(key):
        state["installs"].append((key, app._selected_method(app.ui.tool_cards[key])))
        app.tool_worker = FakeWorker(running=True)
    monkeypatch.setattr(app, "install_tool", fake_install_tool)
    app.tool_worker = None

    for key in app.ui.controls_rows:
        app._set_control_toggle(key, False)
        app._controls_pending.discard(key)
    return state


def _config(app):
    import json
    import paths
    with open(paths.config_file("config.json")) as fh:
        return json.load(fh)


def test_tab_exists_with_both_toggles(app):
    assert app.ui.pages.indexOf(app.ui.tab_controls) == 5
    assert app.ui.pages.indexOf(app.ui.tab_settings) == 6
    assert list(app.ui.controls_rows) == ["xr-hotas", "obah"]   # XR HOTAS oben
    assert app.ui.sidebar.count() == app.ui.pages.count()


def test_toggle_on_when_installed(app, env):
    env["installed"].add("obah")
    app.ui.controls_rows["obah"]["toggle"].setChecked(True)
    assert env["asked"] == []                 # keine Rueckfrage
    assert app.ui.controls_rows["obah"]["toggle"].isChecked()
    assert _config(app)["controls_obah"] is True
    assert app.ui.controls_rows["obah"]["btn_start"].isVisibleTo(app.ui.tab_controls)


def test_toggle_not_installed_asks_and_installs(app, env):
    toggle = app.ui.controls_rows["xr-hotas"]["toggle"]
    toggle.setChecked(True)
    assert len(env["asked"]) == 1
    assert env["installs"] == [("xr-hotas", "cargo")]
    assert "xr-hotas" in app._controls_pending
    assert not toggle.isEnabled()             # waehrend der Installation gesperrt

    # Installation fertig
    env["installed"].add("xr-hotas")
    app.on_control_install_finished("xr-hotas", True)
    assert toggle.isChecked() and toggle.isEnabled()
    assert _config(app)["controls_xr_hotas"] is True


def test_cancel_turns_toggle_off(app, env):
    env["answer"] = None
    toggle = app.ui.controls_rows["xr-hotas"]["toggle"]
    toggle.setChecked(True)
    assert env["installs"] == []
    assert not toggle.isChecked()


def test_failed_install_turns_toggle_off(app, env):
    toggle = app.ui.controls_rows["xr-hotas"]["toggle"]
    toggle.setChecked(True)
    app.on_control_install_finished("xr-hotas", False)
    assert not toggle.isChecked()
    assert _config(app)["controls_xr_hotas"] is False


def test_busy_worker_blocks_second_install(app, env):
    app.tool_worker = FakeWorker(running=True)
    toggle = app.ui.controls_rows["obah"]["toggle"]
    toggle.setChecked(True)
    assert env["installs"] == []
    assert not toggle.isChecked()


def test_removed_in_tools_tab_turns_toggle_off(app, env):
    env["installed"].add("obah")
    toggle = app.ui.controls_rows["obah"]["toggle"]
    toggle.setChecked(True)
    env["installed"].discard("obah")
    app.on_control_tool_status("obah", {"appimage_installed": False, "pm_installed": False,
                                        "config_present": False})
    assert not toggle.isChecked()
    assert _config(app)["controls_obah"] is False


def test_install_callback_from_tools_tab_reaches_controls(app, env):
    """on_tool_installed (Tools-Tab) muss den Controls-Tab benachrichtigen."""
    toggle = app.ui.controls_rows["xr-hotas"]["toggle"]
    toggle.setChecked(True)
    env["installed"].add("xr-hotas")
    app.on_tool_installed("xr-hotas", True)
    assert toggle.isChecked()
    assert "xr-hotas" not in app._controls_pending
