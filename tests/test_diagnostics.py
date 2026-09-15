#!/usr/bin/env python3
"""
tests/test_diagnostics.py — Der Bericht fuer Fehlermeldungen
=============================================================
Zwei Eigenschaften sind hier wichtiger als jeder Inhalt:

  1. Der Bericht entsteht AUCH, wenn alles kaputt ist. Er wird ja genau
     dann gebraucht — eine Ausnahme mitten in der Sammlung waere der
     denkbar schlechteste Moment.
  2. Er enthaelt nichts Persoenliches. Er landet in oeffentlichen Issues.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))


@pytest.fixture
def diag(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import diagnostics
    return diagnostics


# --------------------------------------------------------------------------- #
#  1: haelt aus, wenn nichts funktioniert
# --------------------------------------------------------------------------- #
def test_bericht_entsteht_auch_wenn_alles_faellt(diag, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("kaputt")

    for name in ("system_rows", "gpu_rows", "wivrn_rows", "adb_rows", "network_rows"):
        monkeypatch.setattr(diag, name, boom)

    report = diag.build_report("v1.2.8")

    # Kopf steht, Abschnitte sind da, nichts fliegt hoch.
    assert "yakuda-connect diagnostics" in report
    assert "v1.2.8" in report
    for section in ("[System]", "[GPU]", "[WiVRn]", "[adb]", "[Network]"):
        assert section in report


def test_einzelne_ausfaelle_kippen_den_rest_nicht(diag, monkeypatch):
    monkeypatch.setattr(diag, "_gpu_list", lambda: (_ for _ in ()).throw(OSError("kein lspci")))
    report = diag.build_report("v1.2.8")
    assert "[GPU]" in report
    assert "[Network]" in report          # der Abschnitt danach existiert noch


def test_safe_liefert_platzhalter_statt_ausnahme(diag):
    assert diag._safe(lambda: 1 / 0) == diag.FAILED
    assert diag._safe(lambda: "", default="-") == "-"
    assert diag._safe(lambda: "wert") == "wert"


# --------------------------------------------------------------------------- #
#  2: nichts Persoenliches
# --------------------------------------------------------------------------- #
def test_heimatpfad_wird_gekuerzt(diag, monkeypatch, tmp_path):
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    text = f"runtime: {home}/.config/openxr/1/active_runtime.json"
    assert diag.redact(text) == "runtime: ~/.config/openxr/1/active_runtime.json"


def test_benutzername_faellt_auch_ausserhalb_des_heimatpfads(diag, monkeypatch):
    """
    Der Name steht nicht nur im eigenen Heimatpfad: Steam-Bibliotheken auf
    anderen Laufwerken, Flatpak-Pfade und Fehlermeldungen tragen ihn ebenso.
    """
    monkeypatch.setenv("USER", "yakuda")
    text = "/mnt/games/SteamLibrary/yakuda/prefix — chown yakuda failed"
    out = diag.redact(text)
    assert "yakuda" not in out
    assert out.count("<user>") == 2


def test_kurze_benutzernamen_werden_nicht_ersetzt(diag, monkeypatch):
    # Ein Name wie "vr" oder "pi" wuerde sonst mitten in anderen Woertern
    # zuschlagen und den Bericht unlesbar machen.
    monkeypatch.setenv("USER", "vr")
    assert diag.redact("vr-mode aktiv") == "vr-mode aktiv"


def test_bericht_ist_geschwaerzt(diag, monkeypatch, tmp_path):
    monkeypatch.setenv("USER", "geheimname")
    monkeypatch.setattr(diag, "system_rows",
                        lambda: [("Pfad", f"{tmp_path}/geheimname/config")])
    report = diag.build_report("v1.2.8")
    assert "geheimname" not in report


def test_log_tail_wird_mit_geschwaerzt(diag, monkeypatch):
    monkeypatch.setenv("USER", "geheimname")
    report = diag.build_report("v1.2.8", log_tail="Fehler in /home/geheimname/x")
    assert "geheimname" not in report
    assert "log tail:" in report


def test_ohne_log_tail_kein_log_abschnitt(diag):
    # Der Kopier-Knopf schickt den Bericht ohne Log — in eine Chatnachricht
    # passt es ohnehin nicht.
    assert "log tail:" not in diag.build_report("v1.2.8")


# --------------------------------------------------------------------------- #
#  Inhalt
# --------------------------------------------------------------------------- #
def test_versionsabgleich_steht_ausgewertet_im_bericht(diag, monkeypatch):
    """
    Nicht beide Nummern nebeneinanderstellen und das Vergleichen dem Leser
    ueberlassen: "passen nicht zusammen" ist die haeufigste Ursache fuer
    "verbindet nicht" und gehoert als Klartext in den Bericht.
    """
    import wivrn_apk as wapk
    monkeypatch.setattr(wapk, "server_version", lambda: "25.12")
    monkeypatch.setattr(wapk, "ready_serial", lambda: "PA7B10")
    monkeypatch.setattr(wapk, "detect_package", lambda s: "org.meumeu.wivrn")
    monkeypatch.setattr(wapk, "client_version", lambda s, p: "26.4")

    rows = dict(diag.wivrn_rows())
    assert rows["Versions match"].startswith("NEIN")


def test_ohne_headset_wird_das_gesagt(diag, monkeypatch):
    import wivrn_apk as wapk
    monkeypatch.setattr(wapk, "server_version", lambda: "25.12")
    monkeypatch.setattr(wapk, "ready_serial", lambda: "")

    rows = dict(diag.wivrn_rows())
    assert "nicht per adb erreichbar" in rows["Headset"]
    assert "Versions match" not in rows       # nichts zu vergleichen


def test_abschnitte_sind_ausgerichtet(diag):
    text = "\n".join(diag._section("Test", [("kurz", "a"), ("laengerer Name", "b")]))
    lines = [l for l in text.splitlines() if ":" in l and l.startswith("  ")]
    assert len({l.index(":") for l in lines}) == 1


# --------------------------------------------------------------------------- #
#  Wenn der Benutzername im Programmnamen steckt
# --------------------------------------------------------------------------- #
# Kein erfundener Sonderfall: Der Autor heisst yakuda, das Programm heisst
# yakuda-connect. Auf seinem Rechner machte die Schwaerzung aus der
# Kopfzeile "<user>-connect diagnostics" und aus jedem Cache-Pfad
# "~/.cache/<user>-connect/app.log" — der Bericht war an genau den Stellen
# unleserlich, an denen er etwas aussagen soll.
def test_programmname_ueberlebt_den_gleichnamigen_benutzer(diag, monkeypatch):
    monkeypatch.setenv("USER", "yakuda")
    assert diag.redact("yakuda-connect diagnostics") == "yakuda-connect diagnostics"


def test_pfade_mit_dem_programmnamen_bleiben_lesbar(diag, monkeypatch, tmp_path):
    monkeypatch.setenv("USER", "yakuda")
    monkeypatch.setenv("HOME", "/home/yakuda")
    out = diag.redact("log: /home/yakuda/.cache/yakuda-connect/app.log")
    assert out == "log: ~/.cache/yakuda-connect/app.log"


def test_grossschreibung_schuetzt_nicht_vor_der_schwaerzung(diag, monkeypatch):
    # Umgekehrt zum Fall darueber: ausserhalb des Programmnamens muss der
    # Name auch dann fallen, wenn er gross geschrieben ist.
    monkeypatch.setenv("USER", "yakuda")
    out = diag.redact("chown Yakuda /mnt/games/Yakuda/prefix")
    assert "Yakuda" not in out
    assert out.count("<user>") == 2


def test_projektordner_mit_grossem_anfangsbuchstaben_bleibt(diag, monkeypatch):
    monkeypatch.setenv("USER", "yakuda")
    out = diag.redact("~/projects/Yakuda-connect/core/main.py")
    assert out == "~/projects/Yakuda-connect/core/main.py"


def test_kopfzeile_des_berichts_bleibt_unversehrt(diag, monkeypatch):
    monkeypatch.setenv("USER", "yakuda")
    report = diag.build_report("v1.2.8")
    assert report.startswith("yakuda-connect diagnostics")
