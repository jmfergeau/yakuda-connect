#!/usr/bin/env python3
"""
tests/test_compat_mapping.py — „Use" setzt Steams Haken wirklich
===============================================================
Der Haken „Die Verwendung eines bestimmten Kompatibilitaetswerkzeugs
erzwingen" in Steams Eigenschaften ist nichts anderes als ein Eintrag in
config.vdf -> CompatToolMapping. Er ist nur dann gesetzt, wenn

  * dort ein Name steht, den Steam kennt (der INTERNE Name aus
    compatibilitytool.vdf, nicht zwingend der Ordnername),
  * auch fuer Valves Proton ueberhaupt etwas dort steht (frueher wurde der
    Eintrag fuer „Standard" entfernt — Nicht-Steam-Spiele liefen dann ohne
    Proton),
  * und Steam beim Schreiben nicht laeuft (sonst ueberschreibt es die Datei
    beim Beenden). Das prueft der UI-Teil unten.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))

import games as games_db  # noqa: E402

CONFIG_VDF = '''"InstallConfigStore"
{
\t"Software"
\t{
\t\t"Valve"
\t\t{
\t\t\t"Steam"
\t\t\t{
\t\t\t\t"AutoUpdateWindowEnabled"\t\t"0"
\t\t\t}
\t\t}
\t}
}
'''

GE = {"protonplus_runner": "proton-ge", "version": "GE-Proton10-26"}
CACHY = {"protonplus_runner": "proton-cachyos", "version": "proton-cachyos-11.x"}
VALVE = {"protonplus_runner": None, "version": "Proton 11 (Standard)"}


@pytest.fixture
def steam(tmp_path, monkeypatch):
    root = tmp_path / "Steam"
    (root / "config").mkdir(parents=True)
    (root / "config" / "config.vdf").write_text(CONFIG_VDF)
    tools = root / "compatibilitytools.d"
    tools.mkdir()
    system = tmp_path / "usr-share-steam" / "compatibilitytools.d"
    system.mkdir(parents=True)
    common = root / "steamapps" / "common"
    common.mkdir(parents=True)

    monkeypatch.setattr(games_db.venv, "steam_data_roots", lambda: [str(root)])
    monkeypatch.setattr(games_db, "_steamapps_dirs", lambda: [str(root / "steamapps")])
    monkeypatch.setattr(games_db, "SYSTEM_COMPAT_TOOLS_DIRS", [str(system)])
    return {"root": root, "tools": tools, "system": system, "common": common}


def _tool(where, folder, internal=None):
    d = where / folder
    d.mkdir()
    if internal is not None:
        (d / "compatibilitytool.vdf").write_text(
            '"compatibilitytools"\n{\n  "compat_tools"\n  {\n'
            f'    "{internal}" // Interner Name\n    {{\n'
            '      "install_path" "."\n      "display_name" "Irgendwas"\n'
            '      "from_oslist" "windows"\n      "to_oslist" "linux"\n    }\n  }\n}\n')


def _valve(common, folder):
    d = common / folder
    d.mkdir()
    (d / "toolmanifest.vdf").write_text('"manifest" { "commandline" "/proton %verb%" }')


# --------------------------------------------------------------------------- #
#  Interner Name
# --------------------------------------------------------------------------- #
def test_interner_name_aus_compatibilitytool_vdf(steam):
    _tool(steam["tools"], "GE-Proton10-26", internal="GE-Proton10-26")
    assert games_db.compat_mapping_name(GE) == ("GE-Proton10-26", "")


def test_ordnername_und_interner_name_verschieden(steam):
    # Distributionspaket: Ordner "proton-cachyos", Steam kennt es anders.
    _tool(steam["system"], "proton-cachyos", internal="proton-cachyos-slr")
    assert games_db.compat_mapping_name(CACHY) == ("proton-cachyos-slr", "")


def test_ohne_vdf_bleibt_der_ordnername(steam):
    _tool(steam["tools"], "GE-Proton10-26")
    assert games_db.compat_mapping_name(GE) == ("GE-Proton10-26", "")


def test_systemordner_zaehlt_als_installiert(steam):
    _tool(steam["system"], "proton-cachyos-11.0-20260703", internal="proton-cachyos-11.0-20260703")
    assert games_db.installed_builds(CACHY) == ["proton-cachyos-11.0-20260703"]


def test_nicht_installiert(steam):
    assert games_db.compat_mapping_name(GE) == (None, "not_installed")


def test_systemordner_ist_nie_installationsziel(steam):
    import shutil
    shutil.rmtree(steam["tools"])
    _tool(steam["system"], "proton-cachyos")
    target = games_db.compat_tools_install_dir()
    assert target and not target.startswith(str(steam["system"]))


# --------------------------------------------------------------------------- #
#  Valves Proton
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("folder, internal", [
    ("Proton 10.0", "proton_10"),
    ("Proton 5.13", "proton_513"),
    ("Proton 9.0", "proton_9"),
    ("Proton - Experimental", "proton_experimental"),
    ("Proton Hotfix", "proton_hotfix"),
    ("Proton EasyAntiCheat Runtime", None),
    ("SteamLinuxRuntime_sniper", None),
])
def test_valve_namensschema(folder, internal):
    assert games_db._valve_internal_name(folder) == internal


def test_standard_wird_jetzt_ausdruecklich_eingetragen(steam):
    _valve(steam["common"], "Proton 9.0")
    _valve(steam["common"], "Proton 11.0")
    _valve(steam["common"], "Proton - Experimental")
    assert games_db.compat_mapping_name(VALVE) == ("proton_11", "")


def test_standard_nimmt_neueste_wenn_gewuenschte_fehlt(steam):
    _valve(steam["common"], "Proton 9.0")
    _valve(steam["common"], "Proton 10.0")
    assert games_db.compat_mapping_name(VALVE) == ("proton_10", "")


def test_standard_nur_experimental(steam):
    _valve(steam["common"], "Proton - Experimental")
    assert games_db.compat_mapping_name(VALVE) == ("proton_experimental", "")


def test_ordner_ohne_toolmanifest_zaehlt_nicht(steam):
    (steam["common"] / "Proton 10.0").mkdir()      # halb geloescht
    assert games_db.compat_mapping_name(VALVE) == (None, "valve_missing")


# --------------------------------------------------------------------------- #
#  Schreiben und zuruecklesen
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("appid", ["438100", "3000000001"])     # Steam + Nicht-Steam
def test_eintrag_wird_geschrieben_und_gelesen(steam, appid):
    assert games_db.get_steam_compat_tool(appid) is None
    ok, err = games_db.set_steam_compat_tool(appid, "proton-cachyos-11.0-20260703")
    assert ok, err
    assert games_db.get_steam_compat_tool(appid) == "proton-cachyos-11.0-20260703"
    text = (steam["root"] / "config" / "config.vdf").read_text()
    assert '"priority"\t\t"250"' in text
    # Wechsel auf ein anderes Tool ersetzt, statt zu verdoppeln.
    games_db.set_steam_compat_tool(appid, "proton_11")
    assert games_db.get_steam_compat_tool(appid) == "proton_11"
    assert (steam["root"] / "config" / "config.vdf").read_text().count(f'"{appid}"') == 1


def test_anderes_spiel_mit_aehnlicher_id_stoert_nicht(steam):
    games_db.set_steam_compat_tool("4381000", "GE-Proton10-26")
    assert games_db.get_steam_compat_tool("438100") is None


def test_shutdown_befehl(monkeypatch):
    monkeypatch.setattr(games_db.shutil, "which", lambda n: f"/usr/bin/{n}")
    assert games_db.steam_shutdown_cmd() == ["steam", "-shutdown"]
