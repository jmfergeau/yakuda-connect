#!/usr/bin/env python3
"""
tests/test_games_scan.py — Spiele-Erkennung und eigene Einträge
===============================================================
Deckt den Umbau der Spiele-Erkennung ab:

  * Der VR-Scan richtet sich nach **Steams eigener Kennzeichnung**, nicht
    mehr nach Dateien im Spielordner.
  * Fällt Steams Datei aus, greift wieder die alte Dateierkennung — die
    Liste darf nie leer bleiben, nur weil Valve das Format ändert.
  * Von Hand ergänzte Steam-Spiele überleben jeden Neuscan. Das ist der
    eigentliche Zweck des "Spiel hinzufügen"-Dialogs: Ohne diese Zusicherung
    wäre jeder Handeintrag beim nächsten Tab-Besuch wieder weg.
  * Eigene (Nicht-Steam-)Spiele: anlegen, ändern, löschen, starten.

Alles läuft gegen eine erfundene Steam-Bibliothek in tmp_path — kein echtes
Steam nötig, damit der Test auf jedem Build-Server durchläuft.
"""
import os
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import games as games_db          # noqa: E402
import steam_appinfo              # noqa: E402


# --------------------------------------------------------------------------- #
#  Erfundene Steam-Bibliothek
# --------------------------------------------------------------------------- #
def _write_acf(steamapps, appid, name, installdir):
    (steamapps / f"appmanifest_{appid}.acf").write_text(
        '"AppState"\n{\n'
        f'\t"appid"\t\t"{appid}"\n'
        f'\t"name"\t\t"{name}"\n'
        f'\t"installdir"\t\t"{installdir}"\n'
        "}\n", encoding="utf-8")
    (steamapps / "common" / installdir).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Eine Steam-Bibliothek mit fünf Apps + isolierte App-Config."""
    steamapps = tmp_path / "Steam" / "steamapps"
    steamapps.mkdir(parents=True)

    _write_acf(steamapps, "438100", "VRChat", "VRChat")
    _write_acf(steamapps, "620980", "Beat Saber", "Beat Saber")
    _write_acf(steamapps, "570", "Dota 2", "dota 2 beta")
    _write_acf(steamapps, "1234", "Flachspiel", "Flachspiel")
    _write_acf(steamapps, "1493710", "Proton Experimental", "Proton - Experimental")

    monkeypatch.setattr(games_db, "_steamapps_dirs", lambda: [str(steamapps)])

    cfg = tmp_path / "config.json"
    monkeypatch.setattr(games_db, "APP_CONFIG", str(cfg))
    return steamapps


@pytest.fixture
def steam_says(monkeypatch):
    """Erlaubt jedem Test, Steams Kennzeichnung selbst vorzugeben."""
    def _set(mapping, ok=True):
        monkeypatch.setattr(steam_appinfo, "commons", lambda ids: (mapping, ok))
        monkeypatch.setattr(games_db.steam_appinfo, "commons",
                            lambda ids: (mapping, ok))
    return _set


# --------------------------------------------------------------------------- #
#  Scan nach Steams Kennzeichnung
# --------------------------------------------------------------------------- #
def test_scan_nimmt_nur_was_steam_als_vr_fuehrt(library, steam_says):
    steam_says({
        "438100": {"type": "game", "openvrsupport": "1"},
        "620980": {"type": "game", "openxrsupport": "1"},
        "570":    {"type": "game", "openvrsupport": ""},
        "1234":   {"type": "game"},
    })
    tested, untested = games_db.scan_installed_games()
    found = set(tested) | {g["appid"] for g in untested}
    assert found == {"438100", "620980"}


def test_scan_ueberspringt_werkzeuge_und_dlc(library, steam_says):
    """Ein als Werkzeug geführter Eintrag fliegt raus, auch mit VR-Feld."""
    steam_says({
        "438100": {"type": "tool", "openvrsupport": "1"},
        "620980": {"type": "dlc", "openvrsupport": "1"},
        "1234":   {"type": "game", "openvrsupport": "1"},
    })
    tested, untested = games_db.scan_installed_games()
    found = set(tested) | {g["appid"] for g in untested}
    assert found == {"1234"}


def test_proton_taucht_nie_auf(library, steam_says):
    """Proton-Installationen enthalten OpenVR-Dateien — sie dürfen trotzdem
    nie in der Spieleliste landen (Filter über AppID UND Name)."""
    steam_says({"1493710": {"type": "game", "openvrsupport": "1"}})
    tested, untested = games_db.scan_installed_games()
    found = set(tested) | {g["appid"] for g in untested}
    assert "1493710" not in found


def test_getestete_landen_in_der_ersten_sektion(library, steam_says):
    """438100/620980 stehen in config/games.json, 1234 nicht — die Trennung
    zwischen kuratiert ('getestet') und automatisch muss genau daran haengen."""
    steam_says({
        "438100": {"type": "game", "openvrsupport": "1"},
        "1234":   {"type": "game", "openvrsupport": "1"},
    })
    tested, untested = games_db.scan_installed_games()
    assert tested == ["438100"]
    assert [g["appid"] for g in untested] == ["1234"]


def test_fallback_auf_dateierkennung(library, steam_says, monkeypatch):
    """Ohne Steams Daten muss die alte Erkennung wieder greifen."""
    steam_says({}, ok=False)
    seen = {}

    def fake_look(steamapps, installdir, quick=False):
        seen[installdir] = quick
        return installdir == "Beat Saber"

    monkeypatch.setattr(games_db, "_looks_like_vr_game", fake_look)
    tested, _untested = games_db.scan_installed_games()
    assert tested == ["620980"]          # Beat Saber ist kuratiert
    # Ohne Steam-Daten ist der VOLLE Durchlauf erlaubt (quick=False) —
    # sonst fiele die Erkennung genau dann aus, wenn sie gebraucht wird.
    assert seen["Beat Saber"] is False


def test_volle_dateipruefung_auch_wenn_steam_daten_hat(library, steam_says,
                                                       monkeypatch):
    """Fuer Spiele, zu denen Steam schweigt, muss der VOLLE Durchlauf
    laufen. Nur die schnelle Stufe zu nehmen — aus Sorge um die Scan-Dauer —
    laesst genau die Titel durchfallen, wegen derer es den Rueckfall
    ueberhaupt gibt (VR nachtraeglich ergaenzt, Loader tief im Baum).

    Die Dauer faengt stattdessen der Ergebnis-Cache ab.
    """
    steam_says({"438100": {"type": "game", "openvrsupport": "1"}}, ok=True)
    quicks = []

    def fake_look(steamapps, installdir, quick=False):
        quicks.append(quick)
        return False

    monkeypatch.setattr(games_db, "_looks_like_vr_game", fake_look)
    games_db.scan_installed_games()
    assert quicks and all(q is False for q in quicks)


# --------------------------------------------------------------------------- #
#  Von Hand ergänzte Steam-Spiele
# --------------------------------------------------------------------------- #
def test_handeintrag_ueberlebt_den_scan(library, steam_says):
    """Der Kern des Dialogs: ein von Hand ergänztes Spiel muss auch dann in
    der Liste stehen, wenn Steam es ausdrücklich NICHT als VR führt."""
    steam_says({"1234": {"type": "game", "openvrsupport": ""}})
    assert games_db.add_manual_steam_appid("1234") is True
    tested, untested = games_db.scan_installed_games()
    assert "1234" in {g["appid"] for g in untested}


def test_handeintrag_schlaegt_auch_den_typfilter(library, steam_says):
    steam_says({"1234": {"type": "tool"}})
    games_db.add_manual_steam_appid("1234")
    _tested, untested = games_db.scan_installed_games()
    assert "1234" in {g["appid"] for g in untested}


def test_handeintrag_doppelt_und_entfernen(library):
    assert games_db.add_manual_steam_appid("1234") is True
    assert games_db.add_manual_steam_appid("1234") is False    # schon drin
    assert games_db.load_manual_steam_appids() == ["1234"]
    assert games_db.remove_manual_steam_appid("1234") is True
    assert games_db.load_manual_steam_appids() == []
    assert games_db.remove_manual_steam_appid("1234") is False


def test_handeintrag_nur_zahlen(library):
    assert games_db.add_manual_steam_appid("local:1") is False
    assert games_db.add_manual_steam_appid("") is False


def test_alle_steam_spiele_fuer_die_auswahlliste(library, steam_says):
    """Die Auswahlliste im Dialog zeigt ALLES, nicht nur VR."""
    steam_says({
        "438100": {"type": "game", "openvrsupport": "1"},
        "570":    {"type": "game"},
        "1234":   {"type": "game"},
        "620980": {"type": "game"},
    })
    names = [g["name"] for g in games_db.scan_all_steam_games()]
    assert "Dota 2" in names and "VRChat" in names
    assert "Proton Experimental" not in names
    assert names == sorted(names, key=str.lower)


def test_auswahlliste_ohne_dlc(library, steam_says):
    steam_says({"570": {"type": "dlc"}, "1234": {"type": "game"}})
    ids = {g["appid"] for g in games_db.scan_all_steam_games()}
    assert "570" not in ids and "1234" in ids


# --------------------------------------------------------------------------- #
#  Auto-Scan-Schalter
# --------------------------------------------------------------------------- #
def test_auto_scan_standard_ist_an(library):
    assert games_db.auto_scan_enabled() is True


def test_auto_scan_abschaltbar(library):
    games_db.set_auto_scan(False)
    assert games_db.auto_scan_enabled() is False
    games_db.set_auto_scan(True)
    assert games_db.auto_scan_enabled() is True


def test_auto_scan_versteht_alte_textwerte(library, monkeypatch):
    monkeypatch.setattr(games_db, "_load_app_config",
                        lambda: {"games_auto_scan": "false"})
    assert games_db.auto_scan_enabled() is False
    monkeypatch.setattr(games_db, "_load_app_config",
                        lambda: {"games_auto_scan": "1"})
    assert games_db.auto_scan_enabled() is True


# --------------------------------------------------------------------------- #
#  Eigene Spiele
# --------------------------------------------------------------------------- #
@pytest.fixture
def exe(tmp_path):
    path = tmp_path / "Spiel.x86_64"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


def test_eigenes_spiel_anlegen_und_lesen(library, exe):
    ok, gid = games_db.add_local_game("Mein Spiel", exe, "-vr --fullscreen")
    assert ok is True
    assert gid == "local:1"
    entries = games_db.load_local_games()
    assert len(entries) == 1
    assert entries[0]["name"] == "Mein Spiel"
    assert entries[0]["launch_options"] == "-vr --fullscreen"


def test_eigenes_spiel_prueft_die_eingaben(library, exe, tmp_path):
    assert games_db.add_local_game("", exe)[1] == "no_name"
    assert games_db.add_local_game("X", "")[1] == "no_exe"
    assert games_db.add_local_game("X", str(tmp_path / "weg.bin"))[1] == "not_found"


def test_kennungen_werden_wiederverwendet(library, exe):
    games_db.add_local_game("A", exe)
    ok, gid_b = games_db.add_local_game("B", exe)
    assert gid_b == "local:2"
    games_db.remove_local_game("local:1")
    ok, gid_c = games_db.add_local_game("C", exe)
    assert gid_c == "local:1"


def test_eigenes_spiel_aendern_und_loeschen(library, exe):
    _ok, gid = games_db.add_local_game("Alt", exe)
    assert games_db.update_local_game(gid, name="Neu") is True
    assert games_db.local_game(gid)["name"] == "Neu"
    assert games_db.remove_local_game(gid) is True
    assert games_db.local_game(gid) is None
    assert games_db.remove_local_game(gid) is False


def test_kaputte_eintraege_werden_uebergangen(library, monkeypatch):
    monkeypatch.setattr(games_db, "_load_app_config", lambda: {"games_local": [
        {"id": "local:1", "name": "Gut", "exe": "/bin/sh"},
        {"id": "local:2", "name": "", "exe": "/bin/sh"},      # ohne Namen
        {"id": "", "name": "X", "exe": "/bin/sh"},            # ohne Kennung
        "kein dict",
    ]})
    assert [e["id"] for e in games_db.load_local_games()] == ["local:1"]


def test_is_local_id():
    assert games_db.is_local_id("local:1") is True
    assert games_db.is_local_id("438100") is False


def test_startbefehl_native_binary(library, exe):
    cmd, err = games_db.local_launch_cmd(
        {"exe": exe, "launch_options": "-a -b"})
    assert err == ""
    assert cmd == [exe, "-a", "-b"]


def test_startbefehl_fehlende_datei(library, tmp_path):
    cmd, err = games_db.local_launch_cmd({"exe": str(tmp_path / "weg")})
    assert cmd is None and err == "not_found"


def test_startbefehl_exe_ohne_wine(library, tmp_path, monkeypatch):
    win = tmp_path / "Spiel.exe"
    win.write_text("MZ")
    monkeypatch.setattr(games_db.shutil, "which", lambda n: None)
    cmd, err = games_db.local_launch_cmd({"exe": str(win)})
    assert cmd is None and err == "no_wine"


def test_startbefehl_exe_mit_wine(library, tmp_path, monkeypatch):
    win = tmp_path / "Spiel.exe"
    win.write_text("MZ")
    monkeypatch.setattr(games_db.shutil, "which",
                        lambda n: "/usr/bin/wine" if n == "wine" else None)
    cmd, err = games_db.local_launch_cmd({"exe": str(win), "launch_options": "-x"})
    assert err == ""
    assert cmd == ["/usr/bin/wine", str(win), "-x"]


def test_startbefehl_sh_ohne_ausfuehrungsrecht(library, tmp_path):
    sh = tmp_path / "start.sh"
    sh.write_text("#!/bin/sh\n")
    sh.chmod(0o644)
    cmd, err = games_db.local_launch_cmd({"exe": str(sh)})
    assert err == ""
    assert cmd == ["sh", str(sh)]


def test_startbefehl_binary_ohne_ausfuehrungsrecht(library, tmp_path):
    binary = tmp_path / "Spiel.x86_64"
    binary.write_text("x")
    binary.chmod(0o644)
    cmd, err = games_db.local_launch_cmd({"exe": str(binary)})
    assert cmd is None and err == "not_executable"


def test_startbefehl_unpaarige_anfuehrungszeichen(library, exe):
    """Darf nicht mit ValueError aus shlex hochgehen."""
    cmd, err = games_db.local_launch_cmd({"exe": exe, "launch_options": '-name "Ha'})
    assert err == ""
    assert cmd[0] == exe


def test_appimage_wird_unterstuetzt(library, tmp_path):
    app = tmp_path / "Spiel.AppImage"
    app.write_text("x")
    app.chmod(0o755)
    cmd, err = games_db.local_launch_cmd({"exe": str(app)})
    assert err == "" and cmd == [str(app)]


def test_cover_fuer_eigene_spiele_loest_keinen_download_aus(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("darf für eigene Einträge nicht aufgerufen werden")
    monkeypatch.setattr(games_db, "download_cover", boom)
    monkeypatch.setattr(games_db, "find_game_cover", boom)
    assert games_db.get_game_cover("local:1") is None
    assert games_db.get_game_cover("local:1", allow_download=True) is None


def test_pfad_wird_absolut_gespeichert(library, exe, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ok, _gid = games_db.add_local_game("Rel", os.path.basename(exe))
    assert ok is True
    assert os.path.isabs(games_db.load_local_games()[0]["exe"])


# --------------------------------------------------------------------------- #
#  Zusammenfuehrung beider Quellen
# --------------------------------------------------------------------------- #
def test_dateierkennung_ergaenzt_was_steam_nicht_kennzeichnet(library, steam_says,
                                                              monkeypatch):
    """Der Kern der Zusammenfuehrung: Steam schweigt zu einem Spiel, die
    Dateien verraten es trotzdem. Ohne den Rueckfall faellt genau dieser
    Fall durch (VR nachtraeglich ergaenzt)."""
    steam_says({
        "438100": {"type": "game", "openvrsupport": "1"},
        "1234":   {"type": "game"},              # Steam sagt nichts
    })
    monkeypatch.setattr(games_db, "_looks_like_vr_game",
                        lambda sa, d, quick=False: d == "Flachspiel")
    tested, untested = games_db.scan_installed_games()
    found = set(tested) | {g["appid"] for g in untested}
    assert found == {"438100", "1234"}


def test_liste_kann_nie_kuerzer_sein_als_mit_dateierkennung_allein(library,
                                                                   steam_says,
                                                                   monkeypatch):
    steam_says({a: {"type": "game"} for a in ("438100", "620980", "570", "1234")})
    monkeypatch.setattr(games_db, "_looks_like_vr_game",
                        lambda sa, d, quick=False: d in ("VRChat", "Beat Saber"))
    tested, _u = games_db.scan_installed_games()
    assert set(tested) == {"438100", "620980"}


def test_steam_treffer_spart_den_teuren_durchlauf(library, steam_says, monkeypatch):
    """Was Steam schon beantwortet hat, wird nicht nochmal durchsucht."""
    steam_says({
        "438100": {"type": "game", "openvrsupport": "1"},
        "620980": {"type": "game", "category": {"category_54": 1}},
    })
    geprueft = []
    monkeypatch.setattr(games_db, "_looks_like_vr_game",
                        lambda sa, d, quick=False: geprueft.append(d) or False)
    games_db.scan_installed_games()
    assert "VRChat" not in geprueft
    assert "Beat Saber" not in geprueft


def test_kategorie_signal_wirkt_im_scan(library, steam_says, monkeypatch):
    steam_says({"1234": {"type": "game", "category": {"category_53": 1}}})
    monkeypatch.setattr(games_db, "_looks_like_vr_game",
                        lambda sa, d, quick=False: False)
    _t, untested = games_db.scan_installed_games()
    assert "1234" in {g["appid"] for g in untested}


def test_werkzeuge_bleiben_draussen_trotz_dateierkennung(library, steam_says,
                                                         monkeypatch):
    """Ein als Tool gefuehrter Eintrag darf auch dann nicht auftauchen, wenn
    in seinem Ordner OpenVR-Dateien liegen — bei SteamVR-naher Software ist
    das der Normalfall."""
    steam_says({"1234": {"type": "tool"}})
    monkeypatch.setattr(games_db, "_looks_like_vr_game",
                        lambda sa, d, quick=False: True)
    _t, untested = games_db.scan_installed_games()
    assert "1234" not in {g["appid"] for g in untested}


# --------------------------------------------------------------------------- #
#  Cache der Dateierkennung
# --------------------------------------------------------------------------- #
def test_teurer_durchlauf_nur_einmal(library, steam_says, monkeypatch):
    """Ohne den Cache waere der Auto-Scan wieder so langsam wie v1.2.8."""
    steam_says({a: {"type": "game"} for a in ("438100", "620980", "570", "1234")})
    laeufe = []
    monkeypatch.setattr(games_db, "_looks_like_vr_game",
                        lambda sa, d, quick=False: laeufe.append(d) or False)

    games_db.scan_installed_games()
    erster = len(laeufe)
    assert erster > 0

    laeufe.clear()
    games_db.scan_installed_games()
    assert laeufe == [], "zweiter Scan hat erneut durchsucht"


def test_geaenderter_ordner_wird_neu_geprueft(library, steam_says, monkeypatch):
    steam_says({a: {"type": "game"} for a in ("438100",)})
    laeufe = []
    monkeypatch.setattr(games_db, "_looks_like_vr_game",
                        lambda sa, d, quick=False: laeufe.append(d) or False)
    games_db.scan_installed_games()
    laeufe.clear()

    # Ordner anfassen -> Aenderungszeit springt -> neu pruefen
    (library / "common" / "VRChat" / "neu.txt").write_text("x")
    games_db.scan_installed_games()
    assert "VRChat" in laeufe


def test_cache_version_entwertet_alte_ergebnisse(library, monkeypatch):
    games_db.save_vr_filecheck_cache({"1234": {"stamp": "x", "vr": True}})
    assert games_db.load_vr_filecheck_cache()["1234"]["vr"] is True
    monkeypatch.setattr(games_db, "_VR_FILECHECK_VERSION",
                        games_db._VR_FILECHECK_VERSION + 1)
    assert games_db.load_vr_filecheck_cache() == {}


def test_kaputter_cache_wird_ignoriert(library, monkeypatch):
    monkeypatch.setattr(games_db, "_load_app_config",
                        lambda: {"games_vr_filecheck": "kaputt"})
    assert games_db.load_vr_filecheck_cache() == {}


def test_vr_sources_nennt_das_signal(library, steam_says, monkeypatch):
    """Fuer das Diagnose-Skript: welches Signal hat bei welchem Spiel
    angeschlagen?"""
    steam_says({
        "438100": {"type": "game", "openvrsupport": "1"},
        "1234":   {"type": "game"},
        "570":    {"type": "game"},
    })
    monkeypatch.setattr(games_db, "_looks_like_vr_game",
                        lambda sa, d, quick=False: d == "Flachspiel")
    games_db.add_manual_steam_appid("620980")
    sources = games_db.vr_sources()
    assert sources["438100"] == "steam"
    assert sources["1234"] == "files"
    assert sources["620980"] == "manual"
    assert sources["570"] == ""


def test_leere_bibliothek(library, steam_says, monkeypatch):
    monkeypatch.setattr(games_db, "installed_steam_apps", lambda: [])
    steam_says({}, ok=True)
    assert games_db.scan_installed_games() == ([], [])


# --------------------------------------------------------------------------- #
#  Spiele aus der Liste entfernen
# --------------------------------------------------------------------------- #
def test_entferntes_spiel_bleibt_nach_dem_scan_weg(library, steam_says):
    """Der Kern der Funktion: ohne diese Zusicherung waere das Entfernen beim
    naechsten Tab-Besuch wieder rueckgaengig gemacht."""
    steam_says({a: {"type": "game", "openvrsupport": "1"}
                for a in ("438100", "620980")})
    assert games_db.hide_game("620980") is True
    tested, untested = games_db.scan_installed_games()
    found = set(tested) | {g["appid"] for g in untested}
    assert found == {"438100"}


def test_entfernen_ist_idempotent(library):
    assert games_db.hide_game("620980") is True
    assert games_db.hide_game("620980") is False
    assert games_db.load_hidden_games() == ["620980"]


def test_manuell_hinzufuegen_holt_ein_entferntes_spiel_zurueck(library, steam_says):
    """Sonst legt der Nutzer den Eintrag an und die Liste bleibt trotzdem leer."""
    steam_says({"1234": {"type": "game"}})
    games_db.hide_game("1234")
    games_db.add_manual_steam_appid("1234")
    assert games_db.load_hidden_games() == []
    _t, untested = games_db.scan_installed_games()
    assert "1234" in {g["appid"] for g in untested}


def test_entfernen_loescht_den_handeintrag(library):
    """Zwei gegensaetzliche Wuensche in der Config ('immer zeigen' und 'nie
    zeigen') duerfen gar nicht erst entstehen."""
    games_db.add_manual_steam_appid("1234")
    games_db.hide_game("1234")
    assert games_db.load_manual_steam_appids() == []
    assert games_db.load_hidden_games() == ["1234"]


def test_einzelnes_spiel_zurueckholen(library):
    games_db.hide_game("1234")
    assert games_db.unhide_game("1234") is True
    assert games_db.unhide_game("1234") is False
    assert games_db.load_hidden_games() == []


def test_zuruecksetzen_holt_alles_zurueck(library, steam_says):
    steam_says({a: {"type": "game", "openvrsupport": "1"}
                for a in ("438100", "620980")})
    games_db.hide_game("438100")
    games_db.hide_game("620980")
    assert games_db.clear_hidden_games() == 2
    assert games_db.clear_hidden_games() == 0        # nichts mehr zu tun
    tested, untested = games_db.scan_installed_games()
    assert set(tested) | {g["appid"] for g in untested} == {"438100", "620980"}


def test_kaputte_hidden_liste_wird_ignoriert(library, monkeypatch):
    monkeypatch.setattr(games_db, "_load_app_config",
                        lambda: {"games_hidden": "kaputt"})
    assert games_db.load_hidden_games() == []


# --------------------------------------------------------------------------- #
#  Cover aus games.json
# --------------------------------------------------------------------------- #
def test_hinterlegte_bild_url_wird_zuerst_probiert(library, monkeypatch, tmp_path):
    """Steams Bildpfade folgen nicht durchgaengig dem Muster
    .../<appid>/header.jpg — neuere Titel haben einen Hash im Pfad. Ohne den
    Vorrang der hinterlegten URL bleibt die Kachel beim Platzhalter."""
    versucht = []
    jpeg = b"\xff\xd8" + b"x" * 2000

    class FakeResp:
        status = 200
        def read(self): return jpeg
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=8):
        versucht.append(req.full_url)
        return FakeResp()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(games_db, "COVER_CACHE_DIR", str(tmp_path / "covers"))

    path = games_db.download_cover("2800080")
    assert path is not None
    assert versucht[0] == games_db.GAMES["2800080"]["picture"]


def test_ohne_hinterlegte_url_bleibt_das_cdn_muster(library, monkeypatch, tmp_path):
    versucht = []

    def fake_urlopen(req, timeout=8):
        versucht.append(req.full_url)
        raise OSError("offline")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(games_db, "COVER_CACHE_DIR", str(tmp_path / "covers"))

    assert games_db.download_cover("999999") is None
    assert all("999999" in u for u in versucht)
    assert len(versucht) == len(games_db.STEAM_CDN_NAMES)


def test_thief_vr_hat_ein_bild_hinterlegt():
    """Regression zum Bericht 'Thief VR hat kein Bild'."""
    pic = games_db.GAMES["2800080"].get("picture", "")
    assert pic.startswith("https://") and pic.endswith(".jpg")


# --------------------------------------------------------------------------- #
#  Bilder eigener Spiele
# --------------------------------------------------------------------------- #
def _img(path):
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    return str(path)


@pytest.fixture
def local_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(games_db, "APP_CONFIG", str(tmp_path / "cfg" / "config.json"))
    (tmp_path / "cfg").mkdir()
    exe = tmp_path / "spiel.sh"
    exe.write_text("#!/bin/sh\n")
    return tmp_path, str(exe)


def test_bild_wird_kopiert_nicht_verlinkt(local_setup):
    tmp, exe = local_setup
    src = _img(tmp / "download.png")
    ok, gid = games_db.add_local_game("Spiel", exe, "", src)
    assert ok
    stored = games_db.local_game(gid)["image"]
    assert stored != src and os.path.dirname(stored) == games_db.local_images_dir()
    os.remove(src)                                     # Downloads aufgeraeumt
    assert games_db.get_game_cover(gid) == stored


def test_bild_wechseln_und_entfernen_loescht_nur_eigene_kopie(local_setup):
    tmp, exe = local_setup
    ok, gid = games_db.add_local_game("Spiel", exe)
    first_src = _img(tmp / "a.png")
    assert games_db.set_local_game_image(gid, first_src) == (True, "")
    first = games_db.local_game(gid)["image"]
    assert games_db.set_local_game_image(gid, _img(tmp / "b.jpg")) == (True, "")
    assert not os.path.exists(first), "alte Kopie bleibt liegen"
    assert os.path.exists(first_src), "Originalbild des Nutzers geloescht"
    second = games_db.local_game(gid)["image"]
    games_db.clear_local_game_image(gid)
    assert not os.path.exists(second)
    assert games_db.get_game_cover(gid) is None


def test_entfernen_des_spiels_raeumt_bild_auf(local_setup):
    tmp, exe = local_setup
    ok, gid = games_db.add_local_game("Spiel", exe, "", _img(tmp / "a.png"))
    stored = games_db.local_game(gid)["image"]
    games_db.remove_local_game(gid)
    assert not os.path.exists(stored)
    # Kennung wird wiederverwendet — das neue Spiel erbt kein Bild.
    ok, gid2 = games_db.add_local_game("Anderes", exe)
    assert gid2 == gid and games_db.get_game_cover(gid2) is None


def test_falsches_bild_wird_abgelehnt(local_setup):
    tmp, exe = local_setup
    gif = tmp / "x.gif"
    gif.write_bytes(b"GIF89a")
    assert games_db.add_local_game("Spiel", exe, "", str(gif)) == (False, "bad_image")
    ok, gid = games_db.add_local_game("Spiel", exe)
    assert games_db.set_local_game_image(gid, str(gif)) == (False, "bad_type")


def test_exe_erkennung():
    assert games_db.is_windows_exe("/a/Spiel.EXE")
    assert not games_db.is_windows_exe("/a/spiel.x86_64")
