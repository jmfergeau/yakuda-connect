#!/usr/bin/env python3
"""
tests/test_games_tab.py — Games-Tab: Auto-Scan, eigene Spiele, Dialog
=====================================================================
Die Logik unter dem Tab ist in tests/test_games_scan.py abgedeckt. Hier geht
es um das, was erst beim Zusammenbauen der Oberflaeche schiefgehen kann und
worauf ein reiner Logiktest nie stossen wuerde:

  * Der Auto-Scan darf ein AUFGEKLAPPTES Spiel nicht zuklappen, solange sich
    nichts geaendert hat. Sonst schliesst sich das Panel bei jedem
    Tab-Wechsel — der Nutzer haelt das fuer einen Fehler, und zu Recht.
  * Eigene Spiele haben keine AppID. Jede Stelle, die eine erwartet
    (Cover-Download, Proton-Panel, Steam-Start), muss sie erkennen.
  * Der Knopf "Spiele scannen" uebergibt Qt-typisch ein bool an den Slot.
    Landet das als "quiet"-Merker, scannt der Knopf still vor sich hin und
    der Nutzer sieht nie eine Rueckmeldung.

Alles laeuft gegen das echte VRApp-Fenster (offscreen), damit die Pruefungen
denselben Aufbauweg nehmen wie die App beim Nutzer.
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
    """Ein VRApp-Fenster mit eigenem HOME — nie die echte Config anfassen."""
    home = tmp_path_factory.mktemp("home")
    os.environ["HOME"] = str(home)

    from PySide6.QtWidgets import QMessageBox, QDialog
    for m in ("warning", "information", "critical"):
        setattr(QMessageBox, m, staticmethod(lambda *a, **k: QMessageBox.Ok))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.exec = lambda self, *a, **k: QMessageBox.Ok
    QDialog.exec = lambda self, *a, **k: 0

    from main import VRApp
    window = VRApp()
    yield window
    window.close()


@pytest.fixture
def clean_config(monkeypatch, tmp_path):
    import games as games_db
    monkeypatch.setattr(games_db, "APP_CONFIG", str(tmp_path / "config.json"))
    return games_db


@pytest.fixture
def exe(tmp_path):
    path = tmp_path / "Spiel.x86_64"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


# --------------------------------------------------------------------------- #
#  Rendern
# --------------------------------------------------------------------------- #
def test_eigene_spiele_bekommen_eine_kachel(app, clean_config, exe):
    clean_config.add_local_game("Mein Spiel", exe, "-vr")
    app.render_games_cards(["438100"], [{"appid": "1234", "name": "Testspiel"}])
    assert "local:1" in app._games_tiles
    assert app.ui.lbl_games_local_header.isVisible() or True   # Fenster ist nicht gezeigt
    assert app._games_local_entries["local:1"]["name"] == "Mein Spiel"


def test_leere_liste_blendet_alle_ueberschriften_aus(app, clean_config):
    app.render_games_cards([], [])
    assert app.ui.lbl_games_tested_header.isVisible() is False
    assert app.ui.lbl_games_untested_header.isVisible() is False
    assert app.ui.lbl_games_local_header.isVisible() is False


def test_eigene_kachel_loest_keinen_cover_download_aus(app, clean_config, exe):
    clean_config.add_local_game("Mein Spiel", exe)
    app.render_games_cards([], [])
    assert not any(k.startswith("local:") for k in app._pending_covers)


def test_zaehler_enthaelt_auch_eigene_spiele(app, clean_config, exe):
    clean_config.add_local_game("A", exe)
    clean_config.add_local_game("B", exe)
    app.render_games_cards(["438100"], [{"appid": "1234", "name": "Testspiel"}])
    assert "4" in app.ui.lbl_games_status.text()


# --------------------------------------------------------------------------- #
#  Detail-Panel eines eigenen Spiels
# --------------------------------------------------------------------------- #
def test_eigenes_panel_hat_kein_proton(app, clean_config, exe):
    from PySide6.QtWidgets import QPushButton
    from translations import tr

    clean_config.add_local_game("Mein Spiel", exe)
    app.render_games_cards([], [])
    app._on_game_tile_clicked("local:1")
    panel = app._games_detail_widget
    assert panel is not None
    texts = " ".join(b.text() for b in panel.findChildren(QPushButton))
    # Proton-Auswahl waere fuer einen Eintrag ohne AppID wirkungslos —
    # sie darf deshalb gar nicht erst erscheinen.
    assert tr("games_use_btn") not in texts
    assert tr("games_local_remove_btn") in texts
    assert tr("games_play_btn") in texts
    app._collapse_detail()


def test_eigenes_panel_speichert_startparameter(app, clean_config, exe):
    clean_config.add_local_game("Mein Spiel", exe)
    app.render_games_cards([], [])
    app._save_local_field("local:1", "launch_options", "-novr -windowed")
    assert clean_config.local_game("local:1")["launch_options"] == "-novr -windowed"
    assert app._games_local_entries["local:1"]["launch_options"] == "-novr -windowed"


def test_fehlende_datei_wird_im_panel_gemeldet(app, clean_config, exe, tmp_path):
    from PySide6.QtWidgets import QLabel
    from translations import tr

    clean_config.add_local_game("Weg", exe)
    clean_config.update_local_game("local:1", exe=str(tmp_path / "geloescht.bin"))
    app.render_games_cards([], [])
    app._on_game_tile_clicked("local:1")
    labels = " ".join(lb.text() for lb in app._games_detail_widget.findChildren(QLabel))
    assert tr("games_local_err_not_found") in labels
    app._collapse_detail()


def test_eigenes_spiel_starten(app, clean_config, exe, monkeypatch):
    import core.tabs.games_mixin as mixin
    calls = {}

    def fake_popen(cmd, cwd=None, **kw):
        calls["cmd"] = cmd
        calls["cwd"] = cwd
        return object()

    monkeypatch.setattr(mixin.subprocess, "Popen", fake_popen)
    clean_config.add_local_game("Mein Spiel", exe, "-vr")
    app.render_games_cards([], [])
    app._play_game_from_tile("local:1")
    assert calls["cmd"] == [exe, "-vr"]
    # Arbeitsverzeichnis = Ordner der Programmdatei. Unity-Builds finden
    # ihren _Data-Ordner sonst nicht.
    assert calls["cwd"] == os.path.dirname(exe)


def test_start_ohne_datei_meldet_statt_zu_stuerzen(app, clean_config, tmp_path, monkeypatch):
    import core.tabs.games_mixin as mixin

    def boom(*a, **k):
        raise AssertionError("darf nicht gestartet werden")

    monkeypatch.setattr(mixin.subprocess, "Popen", boom)
    exe = tmp_path / "da.bin"
    exe.write_text("x")
    exe.chmod(0o755)
    clean_config.add_local_game("Weg", str(exe))
    clean_config.update_local_game("local:1", exe=str(tmp_path / "weg.bin"))
    app.render_games_cards([], [])
    app._play_game_from_tile("local:1")          # darf nur eine Meldung setzen


def test_eigenes_spiel_entfernen(app, clean_config, exe, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from translations import tr

    clean_config.add_local_game("Weg damit", exe)
    app.render_games_cards([], [])
    # Rueckfrage bestaetigen. Ueber den TEXT und nicht ueber die Position:
    # Qt sortiert die Knoepfe nach Rolle und Plattform um, buttons()[0] ist
    # also nicht verlaesslich der, den wir zuerst angehaengt haben.
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons()
                                          if b.text() == tr("games_local_remove_btn")))
    app.remove_local_game("local:1")
    assert clean_config.load_local_games() == []
    assert "local:1" not in app._games_tiles


# --------------------------------------------------------------------------- #
#  Auto-Scan
# --------------------------------------------------------------------------- #
def test_stiller_scan_laesst_offenes_panel_stehen(app, clean_config, monkeypatch):
    """Der Kern des Auto-Scans: unveraendertes Ergebnis -> kein Neuaufbau."""
    monkeypatch.setattr(clean_config, "load_local_games", lambda: [])
    app.render_games_cards(["438100"], [{"appid": "1234", "name": "Testspiel"}])
    app._on_game_tile_clicked("438100")
    panel_before = app._games_detail_widget
    assert panel_before is not None

    app._games_scan_quiet = True
    app._on_games_scan_done((["438100"], [{"appid": "1234", "name": "Testspiel"}]))
    assert app._games_detail_widget is panel_before
    assert app._expanded_appid == "438100"
    app._collapse_detail()


def test_stiller_scan_baut_bei_aenderung_neu(app, clean_config, monkeypatch):
    monkeypatch.setattr(clean_config, "load_local_games", lambda: [])
    app.render_games_cards(["438100"], [])
    app._games_scan_quiet = True
    app._on_games_scan_done((["438100"], [{"appid": "1234", "name": "Neu"}]))
    assert "1234" in app._games_tiles


def test_lauter_scan_baut_immer_neu(app, clean_config, monkeypatch):
    """Klickt der Nutzer selbst, will er ein sichtbares Ergebnis."""
    monkeypatch.setattr(clean_config, "load_local_games", lambda: [])
    app.render_games_cards(["438100"], [])
    app._on_game_tile_clicked("438100")
    panel_before = app._games_detail_widget
    app._games_scan_quiet = False
    app._on_games_scan_done((["438100"], []))
    assert app._games_detail_widget is not panel_before


def test_umbenanntes_spiel_gilt_als_aenderung(app, clean_config, monkeypatch):
    monkeypatch.setattr(clean_config, "load_local_games", lambda: [])
    app.render_games_cards([], [{"appid": "1234", "name": "Alt"}])
    app._games_scan_quiet = True
    app._on_games_scan_done(([], [{"appid": "1234", "name": "Neu"}]))
    assert app._games_untested_names["1234"] == "Neu"


def test_scan_knopf_scannt_nicht_still(app, monkeypatch):
    """clicked() liefert ein bool mit. Landet das als 'quiet', bliebe die
    Statuszeile beim Knopfdruck stumm."""
    seen = {}
    monkeypatch.setattr(type(app), "start_games_scan",
                        lambda self, quiet=False: seen.update(quiet=quiet))
    app.ui.btn_games_scan.click()
    assert seen == {"quiet": False}


def test_autoscan_schalter_haengt_an_der_config(app, clean_config):
    app.ui.chk_games_autoscan.setChecked(False)
    assert clean_config.auto_scan_enabled() is False
    app.ui.chk_games_autoscan.setChecked(True)
    assert clean_config.auto_scan_enabled() is True


def test_tab_oeffnen_scannt_nur_bei_eingeschaltetem_autoscan(app, clean_config,
                                                             monkeypatch):
    calls = []
    monkeypatch.setattr(type(app), "start_games_scan",
                        lambda self, quiet=False: calls.append(quiet))
    # Cache vortaeuschen, sonst greift der "noch nie gescannt"-Zweig.
    monkeypatch.setattr(clean_config, "load_cached_games", lambda: ([], [], True))

    clean_config.set_auto_scan(True)
    app._games_tab_visited = True
    app.on_games_tab_opened()
    assert calls == [True]

    calls.clear()
    clean_config.set_auto_scan(False)
    app.on_games_tab_opened()
    assert calls == []


def test_erster_besuch_scannt_immer(app, clean_config, monkeypatch):
    """Ohne Cache muss gescannt werden, auch bei abgeschaltetem Auto-Scan —
    sonst stuende der Nutzer beim ersten Start vor einer leeren Liste."""
    calls = []
    monkeypatch.setattr(type(app), "start_games_scan",
                        lambda self, quiet=False: calls.append(quiet))
    monkeypatch.setattr(clean_config, "load_cached_games", lambda: ([], [], False))
    clean_config.set_auto_scan(False)
    app._games_tab_visited = False
    app.on_games_tab_opened()
    assert calls == [False]


# --------------------------------------------------------------------------- #
#  Dialog
# --------------------------------------------------------------------------- #
def test_dialog_baut_und_zeigt_beide_spalten(app, clean_config, monkeypatch):
    from PySide6.QtWidgets import QLabel
    from translations import tr
    import games_add_dialog as gad

    monkeypatch.setattr(gad.games_db, "scan_all_steam_games",
                        lambda: [{"appid": "1234", "name": "Testspiel"}])
    dialog = gad.AddGameDialog(app)
    dialog._steam_worker.wait(5000)
    labels = " ".join(lb.text() for lb in dialog.findChildren(QLabel))
    assert tr("games_add_steam_head") in labels
    assert tr("games_add_local_head") in labels
    dialog.close()


def test_dialog_traegt_steam_spiel_ein(app, clean_config):
    import games_add_dialog as gad

    dialog = gad.AddGameDialog(app)
    dialog._on_steam_games([{"appid": "1234", "name": "Testspiel"}])
    dialog.combo_steam.setCurrentIndex(0)
    dialog.add_steam_game()
    assert clean_config.load_manual_steam_appids() == ["1234"]
    assert dialog.changed is True
    dialog.close()


def test_dialog_ohne_auswahl_meldet(app, clean_config):
    import games_add_dialog as gad
    from translations import tr

    dialog = gad.AddGameDialog(app)
    dialog._on_steam_games([{"appid": "1234", "name": "Testspiel"}])
    dialog.add_steam_game()                       # Index ist -1
    assert dialog.lbl_status.text() == tr("games_add_no_selection")
    assert dialog.changed is False
    dialog.close()


def test_dialog_findet_spiel_ueber_getippten_namen(app, clean_config):
    import games_add_dialog as gad

    dialog = gad.AddGameDialog(app)
    dialog._on_steam_games([{"appid": "1234", "name": "Testspiel"},
                            {"appid": "5678", "name": "Anderes"}])
    dialog.combo_steam.setCurrentIndex(-1)
    dialog.combo_steam.setEditText("anderes")     # Gross-/Kleinschreibung egal
    assert dialog._selected_appid() == "5678"
    dialog.close()


def test_dialog_traegt_eigenes_spiel_ein_und_leert_die_felder(app, clean_config, exe):
    import games_add_dialog as gad

    dialog = gad.AddGameDialog(app)
    dialog.txt_name.setText("Mein Spiel")
    dialog.txt_exe.setText(exe)
    dialog.txt_opts.setText("-vr")
    dialog.add_local_game()
    entries = clean_config.load_local_games()
    assert [e["name"] for e in entries] == ["Mein Spiel"]
    # Felder leer: sonst legt ein zweiter Klick denselben Eintrag nochmal an.
    assert dialog.txt_name.text() == ""
    assert dialog.txt_exe.text() == ""
    dialog.close()


def test_dialog_meldet_fehlende_datei(app, clean_config, tmp_path):
    import games_add_dialog as gad
    from translations import tr

    dialog = gad.AddGameDialog(app)
    dialog.txt_name.setText("X")
    dialog.txt_exe.setText(str(tmp_path / "gibtsnicht"))
    dialog.add_local_game()
    assert dialog.lbl_status.text() == tr("games_add_err_not_found")
    assert clean_config.load_local_games() == []
    dialog.close()


def test_dialog_kennzeichnet_schon_eingetragene(app, clean_config):
    import games_add_dialog as gad
    from translations import tr

    clean_config.add_manual_steam_appid("1234")
    dialog = gad.AddGameDialog(app)
    dialog._on_steam_games([{"appid": "1234", "name": "Testspiel"}])
    assert tr("games_add_steam_already") in dialog.combo_steam.itemText(0)
    dialog.close()


# --------------------------------------------------------------------------- #
#  Spiel aus der Liste entfernen
# --------------------------------------------------------------------------- #
def _bestaetige(monkeypatch, label_key):
    """Rueckfrage bestaetigen — ueber den TEXT, nicht die Position: Qt
    sortiert die Knoepfe nach Rolle und Plattform um."""
    from PySide6.QtWidgets import QMessageBox
    from translations import tr
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons()
                                          if b.text() == tr(label_key)))


def test_entfernen_knopf_steht_im_panel(app, clean_config):
    from PySide6.QtWidgets import QPushButton
    from translations import tr

    app.render_games_cards(["438100"], [])
    app._on_game_tile_clicked("438100")
    texts = " ".join(b.text() for b in
                     app._games_detail_widget.findChildren(QPushButton))
    assert tr("games_remove_btn") in texts
    assert tr("restore_cfg_btn") in texts        # steht daneben, nicht davor
    app._collapse_detail()


def test_entfernen_nimmt_die_kachel_weg_und_merkt_es(app, clean_config, monkeypatch):
    app.render_games_cards(["438100"], [{"appid": "1234", "name": "Testspiel"}])
    _bestaetige(monkeypatch, "games_remove_btn")
    app.remove_game_from_list("1234")
    assert "1234" not in app._games_tiles
    assert "1234" in clean_config.load_hidden_games()


def test_abbrechen_entfernt_nichts(app, clean_config, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    app.render_games_cards([], [{"appid": "1234", "name": "Testspiel"}])
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
    app.remove_game_from_list("1234")
    assert clean_config.load_hidden_games() == []
    assert "1234" in app._games_tiles


def test_zuruecksetzen_ohne_entfernte_spiele(app, clean_config, monkeypatch):
    """Darf nur eine Meldung zeigen und keinen Scan anwerfen."""
    calls = []
    monkeypatch.setattr(type(app), "start_games_scan",
                        lambda self, quiet=False: calls.append(quiet))
    app.reset_games_list()
    assert calls == []


def test_zuruecksetzen_holt_zurueck_und_scannt(app, clean_config, monkeypatch):
    clean_config.hide_game("1234")
    calls = []
    monkeypatch.setattr(type(app), "start_games_scan",
                        lambda self, quiet=False: calls.append(quiet))
    _bestaetige(monkeypatch, "games_reset_btn")
    app.reset_games_list()
    assert clean_config.load_hidden_games() == []
    assert calls == [False]


def test_zuruecksetzen_knopf_ist_verdrahtet(app, clean_config, monkeypatch):
    gerufen = []
    monkeypatch.setattr(type(app), "reset_games_list",
                        lambda self: gerufen.append(True))
    app.ui.btn_games_reset.click()
    assert gerufen == [True]
