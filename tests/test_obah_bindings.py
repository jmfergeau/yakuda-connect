#!/usr/bin/env python3
"""
tests/test_obah_bindings.py — Auswahl-Schritte von obah (Spiel/Controller/Bindings)
==================================================================================
Nachgebaute Steam-Bibliothek mit einem Unity-typischen Spiel:
  <Spiel>/<Spiel>_Data/StreamingAssets/SteamVR/actions.json
mit Default-Bindings fuer Index und Touch, dazu eine xrizer-Datei und eine
VapoR-Datei — so wie obah sie im Spielordner sucht.
"""
import json
import os
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import obah_bindings as ob  # noqa: E402


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)


@pytest.fixture
def library(tmp_path):
    sa = tmp_path / "steamapps"
    # Spiel 1: installdir weicht vom Namen ab (Steam macht das oft)
    game = sa / "common" / "SpaceGame"
    steamvr = game / "SpaceGame_Data" / "StreamingAssets" / "SteamVR"
    _write(steamvr / "actions.json", {
        "actions": [],
        "default_bindings": [
            {"controller_type": "knuckles", "binding_url": "bindings_knuckles.json"},
            {"controller_type": "oculus_touch", "binding_url": "bindings_touch.json"},
            {"controller_type": "unbekannt", "binding_url": "x.json"},
        ]})
    _write(steamvr / "bindings_knuckles.json", {"controller_type": "knuckles"})
    _write(steamvr / "bindings_touch.json", {"controller_type": "oculus_touch"})
    _write(game / "xrizer" / "knuckles.json", {})
    _write(game / "xrizer" / "vivecontroller.json", {})     # ohne Unterstriche!
    _write(game / "vapor_binding.json", {"controller_type": "oculus_touch"})
    _write(game / "OpenComposite" / "gamepad.json", {})

    # Spiel 2: kein VR -> taucht nicht auf
    (sa / "common" / "FlatGame").mkdir(parents=True)
    # Spiel 3: Manifest mit anderem erlaubten Namen, alphabetisch vorne
    _write(sa / "common" / "aero" / "bin" / "vr_actions.json", {"default_bindings": []})

    apps = [
        {"appid": "1", "name": "Space Game: Deluxe", "installdir": "SpaceGame", "steamapps": str(sa)},
        {"appid": "2", "name": "Flat Game", "installdir": "FlatGame", "steamapps": str(sa)},
        {"appid": "3", "name": "Aero", "installdir": "aero", "steamapps": str(sa)},
        {"appid": "4", "name": "Deinstalliert", "installdir": "weg", "steamapps": str(sa)},
    ]
    return apps


def test_list_games_only_vr_and_sorted(library):
    games = ob.list_games(apps=library)
    assert [g.name for g in games] == ["Aero", "Space Game: Deluxe"]
    space = games[1]
    assert space.actions_json.endswith("SteamVR/actions.json")


def test_find_actions_json_respects_depth(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    _write(deep / "actions.json", {})
    assert ob.find_actions_json(str(tmp_path)) is not None
    assert ob.find_actions_json(str(tmp_path), max_depth=2) is None


def test_scan_bindings_like_obah(library):
    game = ob.list_games(apps=library)[1]
    gb = ob.scan_bindings(game)
    assert set(gb.controllers) == set(ob.CONTROLLER_TYPES)   # immer alle Profile
    k = gb.controllers["knuckles"]
    assert (k.default, k.xrizer, k.vapor, k.opencomposite) == (True, True, False, False)
    t = gb.controllers["oculus_touch"]
    assert (t.default, t.xrizer, t.vapor) == (True, False, True)
    assert gb.controllers["vive_controller"].xrizer is True    # vivecontroller.json
    assert gb.controllers["gamepad"].opencomposite is True
    assert not gb.controllers["rift"].any()


def test_sources_order_and_scratch_always_last(library):
    gb = ob.scan_bindings(ob.list_games(apps=library)[1])
    assert gb.controllers["knuckles"].sources() == ["default", "xrizer", "scratch"]
    assert gb.controllers["oculus_touch"].sources() == ["default", "vapor", "scratch"]
    assert gb.controllers["rift"].sources() == ["scratch"]


def test_binding_file_resolution(library):
    game = ob.list_games(apps=library)[1]
    assert ob.binding_file(game, "knuckles", "default").endswith("bindings_knuckles.json")
    assert ob.binding_file(game, "knuckles", "xrizer").endswith("xrizer/knuckles.json")
    assert ob.binding_file(game, "vive_controller", "xrizer").endswith("xrizer/vivecontroller.json")
    assert ob.binding_file(game, "oculus_touch", "vapor").endswith("vapor_binding.json")
    assert ob.binding_file(game, "rift", "default") is None
    assert ob.binding_file(game, "knuckles", "scratch") is None


def test_missing_default_file(library, tmp_path):
    game = ob.list_games(apps=library)[1]
    os.remove(ob.binding_file(game, "knuckles", "default"))
    assert ob.binding_file(game, "knuckles", "default") is None
    assert ob.binding_file(game, "knuckles", "default", must_exist=False).endswith("bindings_knuckles.json")


def test_labels():
    assert ob.format_name("oculus_touch") == "Oculus Touch"
    assert ob.controller_label("knuckles") == "Valve Index (Knuckles)"


# --------------------------------------------------------------------------- #
#  Oberflaeche
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def app(qapp, tmp_path_factory):
    os.environ["HOME"] = str(tmp_path_factory.mktemp("home"))
    from PySide6.QtWidgets import QMessageBox
    for m in ("warning", "information", "critical"):
        setattr(QMessageBox, m, staticmethod(lambda *a, **k: QMessageBox.Ok))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    from main import VRApp
    window = VRApp()
    yield window
    window.close()


def _wait_scan(app, qapp):
    w = app._obah_scan_worker
    if w is not None:
        w.wait(5000)
    for _ in range(20):
        qapp.processEvents()


def test_panel_collapsed_and_lazy(app):
    assert not app.ui.obah_body.isVisibleTo(app.ui.tab_controls)
    assert app._obah_scanned is False        # vor dem Aufklappen wird nichts gesucht


def test_panel_fills_dropdowns(app, qapp, library, monkeypatch):
    games = ob.list_games(apps=library)
    monkeypatch.setattr(ob, "list_games", lambda cancelled=None: games)
    app.ui.btn_obah_expand.setChecked(True)
    _wait_scan(app, qapp)
    ui = app.ui
    assert ui.obah_body.isVisibleTo(ui.tab_controls)
    assert [ui.combo_obah_game.itemText(i) for i in range(ui.combo_obah_game.count())] \
        == ["Aero", "Space Game: Deluxe"]

    ui.combo_obah_game.setCurrentIndex(1)
    assert ui.combo_obah_controller.count() == len(ob.CONTROLLER_TYPES)
    # Voreinstellung: Oculus/Meta Touch
    assert ui.combo_obah_controller.currentData() == "oculus_touch"

    ui.combo_obah_controller.setCurrentIndex(ui.combo_obah_controller.findData("knuckles"))
    assert [ui.combo_obah_source.itemData(i) for i in range(ui.combo_obah_source.count())] \
        == ["default", "xrizer", "scratch"]
    # Voreinstellung: xrizer
    assert ui.combo_obah_source.currentData() == "xrizer"
    assert "xrizer/knuckles.json" in ui.lbl_obah_hint.text()

    # Automatisch erzwungenes 'scratch' (Spiel ohne Bindings) wird NICHT mitgenommen
    ui.combo_obah_game.setCurrentIndex(0)
    assert ui.combo_obah_source.currentData() == "scratch"
    ui.combo_obah_game.setCurrentIndex(1)
    assert ui.combo_obah_controller.currentData() == "knuckles"
    assert ui.combo_obah_source.currentData() == "xrizer"

    # Eine SELBST getroffene Wahl bleibt beim Spielwechsel
    ui.combo_obah_source.setCurrentIndex(0)          # default
    ui.combo_obah_game.setCurrentIndex(0)
    ui.combo_obah_game.setCurrentIndex(1)
    assert ui.combo_obah_source.currentData() == "default"
    sel = app.obah_selection()
    assert sel[0].name == "Space Game: Deluxe" and sel[1:] == ("knuckles", "default")

    # Sprachwechsel behaelt die komplette Auswahl
    ui.combo_obah_source.setCurrentIndex(1)
    app.obah_retranslate()
    assert app.obah_selection()[1:] == ("knuckles", "xrizer")


def test_no_games(app, qapp, monkeypatch):
    monkeypatch.setattr(ob, "list_games", lambda cancelled=None: [])
    app.start_obah_game_scan()
    _wait_scan(app, qapp)
    assert not app.ui.combo_obah_game.isEnabled()
    assert not app.ui.combo_obah_controller.isEnabled()
    assert not app.ui.combo_obah_source.isEnabled()
    assert app.obah_selection() is None
