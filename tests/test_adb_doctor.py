#!/usr/bin/env python3
"""
tests/test_adb_doctor.py — Handshake-Diagnose nach einem Systemupdate
=====================================================================
Anlass: ``android-tools`` wird per AUR aktualisiert, danach findet adb die
Brille nicht mehr. Fuer den Nutzer sehen vier verschiedene Ursachen gleich
aus ("kein Headset gefunden"), brauchen aber verschiedene Handgriffe.

Geprueft wird ohne adb, ohne Geraet und ohne Paketmanager — alle externen
Aufrufe werden eingesetzt:

  1. Der Versionskonflikt wird erkannt, auch wenn er auf stderr steht.
  2. ``no permissions`` wird NICHT mit ``unauthorized`` verwechselt — der
     eine Fall braucht udev, der andere einen Fingertipp in der Brille.
  3. Ein bereites Geraet gewinnt gegen ein zweites, das klemmt.
  4. Das Paketupdate seit dem letzten Erfolg wird gemeldet.
  5. Die Reparatur wertet ``unauthorized`` danach als "Brille fragt jetzt",
     nicht als Fehlschlag.
  6. udev wird nur angefasst, wenn es wirklich ansteht (Passwortabfrage!).
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))


@pytest.fixture
def doc(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    import paths
    for name in ("cache_root", "config_root"):
        fn = getattr(paths, name, None)
        if fn is not None and hasattr(fn, "cache_clear"):
            fn.cache_clear()
    import adb_doctor
    monkeypatch.setattr(adb_doctor.proc, "which", lambda p: True)
    return adb_doctor


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _adb_says(monkeypatch, doc, stdout="", stderr="", rc=0):
    monkeypatch.setattr(doc.proc, "run",
                        lambda cmd, **kw: _Result(rc, stdout, stderr))


# --------------------------------------------------------------------------- #
#  1-3: die vier Ursachen auseinanderhalten
# --------------------------------------------------------------------------- #
def test_versionskonflikt_wird_auf_stderr_erkannt(doc, monkeypatch):
    # Genau das passiert nach dem Paketupdate: der Client ist neu, der
    # laufende Server noch alt. Der Satz steht auf stderr, die (leere)
    # Geraeteliste auf stdout — wer nur stdout liest, meldet faelschlich
    # "kein Geraet".
    _adb_says(monkeypatch, doc,
              stdout="List of devices attached\n\n",
              stderr="adb server version (41) doesn't match this client (42); killing...\n")

    assert doc.probe()["state"] == doc.MISMATCH


def test_fehlende_rechte_sind_nicht_unauthorized(doc, monkeypatch):
    _adb_says(monkeypatch, doc,
              stdout="List of devices attached\nPA7B10 no permissions\n")

    # Verwechslung waere teuer: 'unauthorized' schickt den Nutzer in die
    # Brille, obwohl auf dem PC die udev-Regeln fehlen.
    assert doc.probe()["state"] == doc.NO_PERMISSION


def test_unauthorized_traegt_die_seriennummer(doc, monkeypatch):
    _adb_says(monkeypatch, doc,
              stdout="List of devices attached\nPA7B10 unauthorized\n")

    info = doc.probe()
    assert info["state"] == doc.UNAUTHORIZED
    assert info["serial"] == "PA7B10"


def test_bereites_geraet_gewinnt(doc, monkeypatch):
    # Ein altes, haengendes Geraet in der Liste darf das bereite nicht
    # verdecken — sonst meldet die App ein Problem, obwohl alles geht.
    _adb_says(monkeypatch, doc,
              stdout="List of devices attached\nOLD123 offline\nPA7B10 device\n")

    info = doc.probe()
    assert info["state"] == doc.OK
    assert info["serial"] == "PA7B10"


def test_ohne_adb_kein_ratespiel(doc, monkeypatch):
    monkeypatch.setattr(doc.proc, "which", lambda p: False)
    assert doc.probe()["state"] == doc.NO_ADB


# --------------------------------------------------------------------------- #
#  4: "gestern ging es noch"
# --------------------------------------------------------------------------- #
def test_paketupdate_seit_dem_letzten_erfolg_wird_gemeldet(doc, monkeypatch):
    monkeypatch.setattr(doc, "package_version", lambda: "36.0.0-1")
    monkeypatch.setattr(doc, "client_version", lambda: "36.0.0")
    doc.remember_success()

    # ... spaeter, nach einem AUR-Update:
    monkeypatch.setattr(doc, "package_version", lambda: "36.1.0-1")
    assert doc.updated_since_success() == ("36.0.0-1", "36.1.0-1")


def test_ohne_update_keine_meldung(doc, monkeypatch):
    monkeypatch.setattr(doc, "package_version", lambda: "36.0.0-1")
    monkeypatch.setattr(doc, "client_version", lambda: "36.0.0")
    doc.remember_success()
    assert doc.updated_since_success() is None


def test_unbekannte_paketversion_meldet_nichts(doc, monkeypatch):
    # AppImage-Nutzer mit handinstalliertem adb: kein Paket, keine Aussage.
    # Auf keinen Fall eine falsche.
    monkeypatch.setattr(doc, "package_version", lambda: "")
    assert doc.updated_since_success() is None


def test_kaputte_zustandsdatei_wirft_nicht(doc, monkeypatch):
    from paths import cache_file
    path = pathlib.Path(cache_file("adb_state.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{kaputt", encoding="utf-8")

    monkeypatch.setattr(doc, "package_version", lambda: "36.1.0-1")
    assert doc.updated_since_success() is None


def test_erfolg_wird_gespeichert(doc, monkeypatch):
    monkeypatch.setattr(doc, "package_version", lambda: "36.0.0-1")
    monkeypatch.setattr(doc, "client_version", lambda: "36.0.0")
    doc.remember_success()

    from paths import cache_file
    data = json.loads(pathlib.Path(cache_file("adb_state.json")).read_text(encoding="utf-8"))
    assert data["package"] == "36.0.0-1"


# --------------------------------------------------------------------------- #
#  5-6: Reparatur
# --------------------------------------------------------------------------- #
def test_reparatur_startet_den_server_neu(doc, monkeypatch):
    calls = []
    monkeypatch.setattr(doc.proc, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result(0))[1])
    monkeypatch.setattr(doc, "probe", lambda: {"state": doc.OK})
    monkeypatch.setattr(doc, "remember_success", lambda: None)
    monkeypatch.setattr(doc.time, "sleep", lambda s: None)

    ok, steps = doc.repair(doc.MISMATCH)

    assert ok is True
    assert ["adb", "kill-server"] in calls
    assert ["adb", "start-server"] in calls
    assert "adb_fix_step_ok" in steps


def test_udev_nur_bei_rechteproblem(doc, monkeypatch):
    udev_called = []
    monkeypatch.setattr(doc, "reload_udev",
                        lambda: (udev_called.append(True), True)[1])
    monkeypatch.setattr(doc, "restart_server", lambda: True)
    monkeypatch.setattr(doc, "probe", lambda: {"state": doc.OK})
    monkeypatch.setattr(doc, "remember_success", lambda: None)
    monkeypatch.setattr(doc.time, "sleep", lambda s: None)

    # Versionskonflikt: keine Passwortabfrage ausloesen.
    doc.repair(doc.MISMATCH)
    assert udev_called == []

    # Fehlende Rechte: jetzt ist sie berechtigt.
    doc.repair(doc.NO_PERMISSION)
    assert udev_called == [True]


def test_unauthorized_nach_der_reparatur_ist_ein_zwischenschritt(doc, monkeypatch):
    monkeypatch.setattr(doc, "restart_server", lambda: True)
    monkeypatch.setattr(doc, "probe", lambda: {"state": doc.UNAUTHORIZED})
    monkeypatch.setattr(doc.time, "sleep", lambda s: None)

    ok, steps = doc.repair(doc.MISMATCH)

    # Nicht "erledigt" — aber die Meldung schickt den Nutzer in die Brille
    # statt ihn ratlos zu lassen.
    assert ok is False
    assert steps[-1] == "adb_fix_step_confirm"


def test_fehlendes_adb_wird_nicht_wegrepariert(doc, monkeypatch):
    restarted = []
    monkeypatch.setattr(doc, "restart_server",
                        lambda: (restarted.append(True), True)[1])

    ok, steps = doc.repair(doc.NO_ADB)

    assert ok is False
    assert restarted == []          # ein Serverneustart holt kein Paket her
    assert steps == ["adb_fix_step_install"]


def test_adbkey_wird_niemals_geloescht(doc):
    """
    Der haeufigste Forenrat ist der schaedlichste: ``~/.android/adbkey``
    loeschen. Danach muss JEDES Android-Geraet des Nutzers neu bestaetigt
    werden, nicht nur die Brille. Dieser Test nagelt fest, dass das Modul
    das nicht tut — auch nicht in einer spaeteren Fassung.
    """
    source = (ROOT / "core" / "adb_doctor.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    code = code.split('"""')[0] + '"""'.join(code.split('"""')[2:])
    assert "adbkey" not in code
    assert "os.remove" not in code
    assert "unlink" not in code
