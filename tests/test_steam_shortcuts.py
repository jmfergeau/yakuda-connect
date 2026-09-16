#!/usr/bin/env python3
"""
tests/test_steam_shortcuts.py — Nicht-Steam-Spiele im Games-Tab
==============================================================
Was hier festgenagelt wird, und warum:

  * **shortcuts.vdf wird byte-genau zurueckgeschrieben.** Die Datei gehoert
    Steam und enthaelt Felder, die wir nicht kennen (Tags, Icons, Overlay-
    Schalter). Ein Fehler beim Schreiben loescht im schlimmsten Fall ALLE
    Nicht-Steam-Spiele des Nutzers. Deshalb: lesen → schreiben = dieselbe
    Datei, und nach dem Aendern der Startparameter unterscheidet sich nur
    genau dieses Feld.
  * **Die AppID ist vorzeichenlos.** Steam speichert sie als int32; in
    CompatToolMapping, compatdata und grid steht dieselbe Zahl ohne
    Vorzeichen. Mit Vorzeichen landete die Proton-Auswahl im Nirgendwo.
  * **Gestartet wird ueber die 64-Bit-Spiel-ID**, nicht mit -applaunch.
  * **Die Original-Startparameter bleiben erhalten.** Ein Heroic- oder
    Lutris-Eintrag startet NUR ueber sie. Der erste Play-Klick darf sie
    nicht ueberschreiben, und ein spaeter abgeschalteter Schalter darf nicht
    in ihnen haengen bleiben.
  * **Nicht-Steam-Spiele kommen nur ueber einen Handeintrag in die Liste.**
"""
import os
import pathlib
import struct
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import games as games_db       # noqa: E402
import steam_shortcuts as sc   # noqa: E402

HEROIC_ID = 3000000001              # > 2^31 → Nicht-Steam
HEROIC_SIGNED = HEROIC_ID - 2 ** 32  # so steht es in der Datei


# --------------------------------------------------------------------------- #
#  Binaer-VDF bauen (unabhaengig vom Code unter Test)
# --------------------------------------------------------------------------- #
def _s(key, value):
    return b"\x01" + key.encode() + b"\x00" + value.encode() + b"\x00"


def _i(key, value):
    return b"\x02" + key.encode() + b"\x00" + struct.pack("<i", value)


def _m(key, body):
    return b"\x00" + key.encode() + b"\x00" + body + b"\x08"


def _entry(idx, appid_signed, name, exe, opts, extra=b""):
    body = (_i("appid", appid_signed) + _s("AppName", name) + _s("Exe", exe)
            + _s("StartDir", '"/tmp/"') + _s("icon", "") + _s("LaunchOptions", opts)
            + _i("IsHidden", 0) + _i("AllowOverlay", 1) + extra
            + _m("tags", _s("0", "VR") + _s("1", "Fav")))
    return _m(str(idx), body)


def _file(*entries):
    return _m("shortcuts", b"".join(entries)) + b"\x08"


@pytest.fixture
def steam(tmp_path, monkeypatch):
    """Steam-Wurzel mit einer shortcuts.vdf, isolierte App-Config."""
    root = tmp_path / "Steam"
    cfg_dir = root / "userdata" / "12345" / "config"
    cfg_dir.mkdir(parents=True)
    vdf = cfg_dir / "shortcuts.vdf"
    vdf.write_bytes(_file(
        _entry(0, HEROIC_SIGNED, "Heroic: Cyberpunk", "/usr/bin/heroic",
               "--no-gui heroic://launch/legendary/abc"),
        _entry(1, -1000, "Emulator", "/usr/bin/emu", "",
               extra=struct.pack("<B", 0x07) + b"LastPlayTime64\x00" + struct.pack("<Q", 2 ** 40)),
    ))
    monkeypatch.setattr(sc.venv, "steam_data_roots", lambda: [str(root)])
    monkeypatch.setattr(games_db.venv, "steam_data_roots", lambda: [str(root)])
    monkeypatch.setattr(games_db, "APP_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(games_db, "_steamapps_dirs", lambda: [])
    return root, vdf


# --------------------------------------------------------------------------- #
#  Format
# --------------------------------------------------------------------------- #
def test_lesen_und_schreiben_ergibt_dieselbe_datei(steam):
    _root, vdf = steam
    data = vdf.read_bytes()
    trailer, items, _block = sc._load_file(str(vdf))
    assert sc.dump(items, trailer) == data


def test_unbekannter_feldtyp_wird_nicht_angefasst(tmp_path):
    bad = _m("shortcuts", _m("0", b"\x09weird\x00" + b"xxxx")) + b"\x08"
    with pytest.raises(sc.VdfError):
        sc.parse(bad)


def test_abgeschnittene_datei(tmp_path):
    with pytest.raises(sc.VdfError):
        sc.parse(_file(_entry(0, HEROIC_SIGNED, "A", "/a", ""))[:-10])


# --------------------------------------------------------------------------- #
#  IDs
# --------------------------------------------------------------------------- #
def test_ids_vorzeichenlos_und_unterscheidbar(steam):
    found = {s["name"]: s for s in sc.list_shortcuts()}
    assert found["Heroic: Cyberpunk"]["appid"] == str(HEROIC_ID)
    assert found["Emulator"]["appid"] == str(2 ** 32 - 1000)
    assert found["Heroic: Cyberpunk"]["launch_options"].startswith("--no-gui")
    for s in found.values():
        assert sc.is_shortcut_id(s["appid"])
    assert not sc.is_shortcut_id("438100")          # echtes Steam-Spiel
    assert not sc.is_shortcut_id("local:3")
    assert not sc.is_shortcut_id(str(2 ** 32))       # zu gross


def test_spiel_id_zum_starten():
    assert sc.game_id(HEROIC_ID) == (HEROIC_ID << 32) | 0x02000000


def test_alte_eintraege_ohne_appid_bekommen_steams_crc_id():
    import zlib
    entry = [(sc.T_STRING, "AppName", "Spiel"), (sc.T_STRING, "Exe", '"/a/b"')]
    expected = (zlib.crc32(b'"/a/b"Spiel') & 0xFFFFFFFF) | 0x80000000
    assert sc._entry_appid(entry) == expected


def test_doppelter_link_auf_dieselbe_datei_zaehlt_einmal(steam, tmp_path, monkeypatch):
    root, _vdf = steam
    link = tmp_path / "steam-link"
    os.symlink(root, link)
    monkeypatch.setattr(sc.venv, "steam_data_roots", lambda: [str(root), str(link)])
    assert len(sc.shortcuts_files()) == 1


# --------------------------------------------------------------------------- #
#  Startparameter schreiben
# --------------------------------------------------------------------------- #
def test_nur_das_eine_feld_aendert_sich(steam):
    _root, vdf = steam
    before = vdf.read_bytes()
    ok, err = sc.set_launch_options(str(HEROIC_ID), "gamemoderun %command% --x")
    assert ok, err
    after = vdf.read_bytes()
    old = _s("LaunchOptions", "--no-gui heroic://launch/legendary/abc")
    new = _s("LaunchOptions", "gamemoderun %command% --x")
    assert after == before.replace(old, new, 1)
    assert list(vdf.parent.glob("shortcuts.vdf.bak.*")), "keine Sicherung angelegt"
    # Der andere Eintrag samt uint64-Feld ist unberuehrt.
    names = {s["name"] for s in sc.list_shortcuts()}
    assert names == {"Heroic: Cyberpunk", "Emulator"}


def test_unbekannte_id_schreibt_nichts(steam):
    _root, vdf = steam
    before = vdf.read_bytes()
    ok, _err = sc.set_launch_options("3999999999", "x")
    assert not ok and vdf.read_bytes() == before


def test_fehlendes_feld_wird_angelegt(tmp_path, monkeypatch):
    root = tmp_path / "S"
    cfg = root / "userdata" / "1" / "config"
    cfg.mkdir(parents=True)
    body = _i("appid", HEROIC_SIGNED) + _s("AppName", "Ohne") + _s("Exe", "/x")
    (cfg / "shortcuts.vdf").write_bytes(_m("shortcuts", _m("0", body)) + b"\x08")
    monkeypatch.setattr(sc.venv, "steam_data_roots", lambda: [str(root)])
    assert sc.set_launch_options(str(HEROIC_ID), "-vr")[0]
    assert sc.get(str(HEROIC_ID))["launch_options"] == "-vr"


# --------------------------------------------------------------------------- #
#  Games-Tab
# --------------------------------------------------------------------------- #
def test_dialog_liste_enthaelt_nicht_steam_spiele(steam, monkeypatch):
    monkeypatch.setattr(games_db.steam_appinfo, "commons", lambda ids: ({}, True))
    listed = {g["name"]: g for g in games_db.scan_all_steam_games()}
    assert listed["Heroic: Cyberpunk"]["shortcut"] is True
    assert listed["Heroic: Cyberpunk"]["appid"] == str(HEROIC_ID)


def test_nur_von_hand_eingetragene_erscheinen_im_scan(steam, monkeypatch):
    monkeypatch.setattr(games_db.steam_appinfo, "commons", lambda ids: ({}, True))
    _t, untested = games_db.scan_installed_games()
    assert untested == []
    assert games_db.add_manual_steam_appid(str(HEROIC_ID))
    _t, untested = games_db.scan_installed_games()
    assert untested == [{"appid": str(HEROIC_ID), "name": "Heroic: Cyberpunk"}]


def test_entfernt_oder_in_steam_geloescht_bleibt_weg(steam, monkeypatch):
    _root, vdf = steam
    monkeypatch.setattr(games_db.steam_appinfo, "commons", lambda ids: ({}, True))
    games_db.add_manual_steam_appid(str(HEROIC_ID))
    games_db.hide_game(str(HEROIC_ID))
    assert games_db.scan_installed_games()[1] == []
    games_db.unhide_game(str(HEROIC_ID))
    vdf.write_bytes(_file(_entry(0, -1000, "Emulator", "/usr/bin/emu", "")))
    assert games_db.scan_installed_games()[1] == []


def test_original_parameter_werden_beim_eintragen_gemerkt(steam):
    games_db.add_manual_steam_appid(str(HEROIC_ID))
    # Danach schreibt Play etwas anderes in die Datei ...
    sc.set_launch_options(str(HEROIC_ID), "gamemoderun %command% --no-gui heroic://launch/legendary/abc")
    # ... die Basis bleibt trotzdem der Stand beim Eintragen.
    assert games_db.shortcut_base_options(str(HEROIC_ID)) == "--no-gui heroic://launch/legendary/abc"


def test_heroic_aufruf_ueberlebt_schalter(steam):
    games_db.add_manual_steam_appid(str(HEROIC_ID))
    base = games_db.shortcut_base_options(str(HEROIC_ID))
    on = games_db.compose_launch_options(base, ["gamemode"], "")
    off = games_db.compose_launch_options(base, [], "")
    assert "heroic://launch/legendary/abc" in on and "heroic://launch/legendary/abc" in off
    assert "gamemoderun" not in off


def test_startparameter_landen_in_shortcuts_vdf(steam):
    _root, _vdf = steam
    ok, err = games_db.set_steam_launch_options(str(HEROIC_ID), "%command% -vr")
    assert ok, err
    assert sc.get(str(HEROIC_ID))["launch_options"] == "%command% -vr"


def test_start_ueber_rungameid(monkeypatch):
    monkeypatch.setattr(games_db.shutil, "which", lambda name: "/usr/bin/" + name)
    cmd = games_db.steam_launch_cmd(str(HEROIC_ID))
    assert cmd == ["steam", f"steam://rungameid/{sc.game_id(HEROIC_ID)}"]
    assert games_db.steam_launch_cmd("438100") == ["steam", "-applaunch", "438100"]


def test_cover_nur_aus_grid_ohne_download(steam, monkeypatch):
    root, _vdf = steam
    monkeypatch.setattr(games_db, "download_cover",
                        lambda appid: pytest.fail("Download fuer Nicht-Steam-Spiel"))
    monkeypatch.setattr(games_db, "COVER_CACHE_DIR", str(root / "nocache"))
    assert games_db.get_game_cover(str(HEROIC_ID)) is None
    grid = root / "userdata" / "12345" / "config" / "grid"
    grid.mkdir()
    (grid / f"{HEROIC_ID}.png").write_bytes(b"x")          # nur Querformat
    assert games_db.get_game_cover(str(HEROIC_ID)).endswith(f"{HEROIC_ID}.png")


# --------------------------------------------------------------------------- #
#  „In Steam eintragen"
# --------------------------------------------------------------------------- #
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
       b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture
def game_exe(tmp_path):
    d = tmp_path / "Meine Spiele" / "Spiel"
    d.mkdir(parents=True)
    exe = d / "Spiel.exe"
    exe.write_bytes(b"MZ")
    return str(exe)


def test_neuer_eintrag_alte_bleiben_unberuehrt(steam, game_exe):
    _root, vdf = steam
    before = {(s["appid"], s["name"], s["launch_options"]) for s in sc.list_shortcuts()}
    old_bytes = vdf.read_bytes()
    appid, err = sc.add_shortcut("Mein Spiel", game_exe, "-vr")
    assert err == "" and sc.is_shortcut_id(appid)
    after = {(s["appid"], s["name"], s["launch_options"]) for s in sc.list_shortcuts()}
    assert after == before | {(appid, "Mein Spiel", "-vr")}
    # Die alten Eintraege stehen byte-gleich vorne in der Datei.
    assert vdf.read_bytes().startswith(old_bytes[:-2])
    new = sc.get(appid)
    assert new["exe"] == f'"{game_exe}"', "Pfad mit Leerzeichen muss in Anfuehrungszeichen"
    assert new["start_dir"] == f'"{os.path.dirname(game_exe)}/"'


def test_appid_wie_steam_rom_manager(steam, game_exe):
    import zlib
    appid, _err = sc.add_shortcut("Mein Spiel", game_exe)
    expected = (zlib.crc32(f'"{game_exe}"Mein Spiel'.encode()) & 0xFFFFFFFF) | 0x80000000
    assert appid == str(expected)


def test_zweimal_eintragen_legt_nichts_doppelt_an(steam, game_exe):
    first, _ = sc.add_shortcut("Mein Spiel", game_exe)
    count = len(sc.list_shortcuts())
    again, err = sc.add_shortcut("Mein Spiel", game_exe)
    assert (again, err) == (first, "exists")
    assert len(sc.list_shortcuts()) == count


def test_legt_shortcuts_vdf_an_wenn_keine_da(tmp_path, monkeypatch, game_exe):
    root = tmp_path / "Leer"
    (root / "userdata" / "777" / "config").mkdir(parents=True)
    monkeypatch.setattr(sc.venv, "steam_data_roots", lambda: [str(root)])
    appid, err = sc.add_shortcut("Neu", game_exe)
    assert err == ""
    assert [s["appid"] for s in sc.list_shortcuts()] == [appid]


def test_kein_konto(tmp_path, monkeypatch, game_exe):
    monkeypatch.setattr(sc.venv, "steam_data_roots", lambda: [str(tmp_path / "nix")])
    assert sc.add_shortcut("Neu", game_exe) == (None, "no_account")


def test_zuletzt_angemeldetes_konto_gewinnt(tmp_path, monkeypatch):
    root = tmp_path / "Steam"
    for uid in ("111", "222"):
        (root / "userdata" / uid / "config").mkdir(parents=True)
    (root / "config").mkdir()
    steamid = 76561197960265728 + 222
    (root / "config" / "loginusers.vdf").write_text(
        '"users"\n{\n'
        f'\t"{76561197960265728 + 111}"\n\t{{\n\t\t"MostRecent"\t\t"0"\n\t}}\n'
        f'\t"{steamid}"\n\t{{\n\t\t"AccountName"\t\t"x"\n\t\t"MostRecent"\t\t"1"\n\t}}\n}}\n')
    monkeypatch.setattr(sc.venv, "steam_data_roots", lambda: [str(root)])
    assert "/userdata/222/" in sc.target_shortcuts_file()


def test_kaputte_datei_wird_nicht_ueberschrieben(steam, game_exe):
    _root, vdf = steam
    vdf.write_bytes(b"\x00shortcuts\x00\x09kaputt")
    assert sc.add_shortcut("Neu", game_exe) == (None, "unreadable")
    assert vdf.read_bytes() == b"\x00shortcuts\x00\x09kaputt"


def test_grid_bild_setzen_und_entfernen(steam, tmp_path):
    root, _vdf = steam
    img = tmp_path / "cover.jpeg"
    img.write_bytes(PNG)
    grid = root / "userdata" / "12345" / "config" / "grid"
    grid.mkdir()
    (grid / f"{HEROIC_ID}p.png").write_bytes(b"alt")        # alte Endung
    (grid / f"{HEROIC_ID}_hero.png").write_bytes(b"hero")   # bleibt
    assert sc.set_grid_image(str(HEROIC_ID), str(img)) == (True, "")
    assert (grid / f"{HEROIC_ID}p.jpg").exists()
    assert not (grid / f"{HEROIC_ID}p.png").exists()
    sc.clear_grid_image(str(HEROIC_ID))
    assert not (grid / f"{HEROIC_ID}p.jpg").exists()
    assert (grid / f"{HEROIC_ID}_hero.png").exists()


def test_grid_bild_fehler(steam, tmp_path):
    bad = tmp_path / "x.gif"
    bad.write_bytes(b"GIF")
    assert sc.set_grid_image(str(HEROIC_ID), str(bad)) == (False, "bad_type")
    assert sc.set_grid_image(str(HEROIC_ID), str(tmp_path / "fehlt.png")) == (False, "not_found")
    img = tmp_path / "ok.png"
    img.write_bytes(PNG)
    assert sc.set_grid_image("3999999999", str(img)) == (False, "no_account")


def test_register_in_steam_komplett(steam, game_exe, tmp_path):
    root, _vdf = steam
    img = tmp_path / "cover.png"
    img.write_bytes(PNG)
    appid, err = games_db.register_in_steam("Mein Spiel", game_exe, "-vr", str(img))
    assert err == ""
    assert appid in games_db.load_manual_steam_appids()
    assert games_db.shortcut_base_options(appid) == "-vr"
    assert (root / "userdata" / "12345" / "config" / "grid" / f"{appid}p.png").exists()
    assert games_db.get_game_cover(appid).endswith(f"{appid}p.png")


def test_register_in_steam_prueft_eingaben(steam, tmp_path):
    assert games_db.register_in_steam("", "/x.exe") == (None, "no_name")
    assert games_db.register_in_steam("A", str(tmp_path / "fehlt.exe")) == (None, "not_found")
