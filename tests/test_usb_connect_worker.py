#!/usr/bin/env python3
"""
tests/test_usb_connect_worker.py — Warten, bis die PICO antwortet
==================================================================
Der Worker aus ``core/main.py`` gibt nicht beim ersten Zeitlimit auf: die
PICO 4 blockiert adb, solange die USB-Schnittstelle im
Dateiuebertragungs-Modus verhakt ist, und der Nutzer loest das GENAU
WAEHREND des Versuchs, indem er an der Brille kurz auf "Nur Laden" und
zurueck stellt. Wer da nach fuenf Sekunden aufgibt, verpasst den Moment.

Geprueft wird mit echten Threads, aber ohne adb:
  1. Ein Fehlschlag fuehrt zu einem zweiten Anlauf — bis es klappt.
  2. Ein verschwundenes Geraet (genau das passiert beim Umschalten) gilt
     als "warte weiter", nicht als Fehler.
  3. Etwas, das sich durch Warten nicht aendert, wird sofort gemeldet.
  4. Die Frist wird eingehalten.
  5. Abbrechen wirkt.
  6. Waehrend des Wartens kommt der Hinweis auf die USB-Optionen.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))


@pytest.fixture
def worker_mod(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import main
    import wivrn_apk
    # Wartezeiten raus, sonst dauert jeder Test Sekunden.
    monkeypatch.setattr(wivrn_apk, "_sleep", lambda s, cancel=None: None)
    monkeypatch.setattr(main.UsbConnectWorker, "ROUND_PAUSE", 0.0)
    return main, wivrn_apk


def _run(worker, timeout_ms=5000):
    """Worker starten und auf das Ergebnis warten."""
    from PySide6.QtCore import QEventLoop, QTimer

    results = []
    status = []
    loop = QEventLoop()
    worker.result_signal.connect(lambda *a: (results.append(a), loop.quit()))
    worker.status_signal.connect(status.append)
    worker.finished.connect(loop.quit)

    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(timeout_ms)

    worker.start()
    loop.exec()
    worker.wait(3000)
    assert results, "Worker hat kein Ergebnis geliefert"
    return results[0], status


def test_zweiter_anlauf_nach_timeout(worker_mod, monkeypatch):
    main, wapk = worker_mod
    rounds = []

    def fake_connect(serial, **kw):
        rounds.append(serial)
        if len(rounds) < 3:
            return {"ok": False, "package": "", "detail": "timeout", "uncertain": False}
        return {"ok": True, "package": "org.meumeu.wivrn", "detail": "", "uncertain": False}

    monkeypatch.setattr(wapk, "ready_serial", lambda: "PA7B10")
    monkeypatch.setattr(wapk, "connect_usb", fake_connect)

    (ok, detail, package), _status = _run(main.UsbConnectWorker(deadline=30))

    assert ok is True
    assert package == "org.meumeu.wivrn"
    assert len(rounds) == 3


def test_verschwundenes_geraet_ist_kein_fehler(worker_mod, monkeypatch):
    """
    Beim Umschalten an der Brille faellt das Geraet kurz aus ``adb devices``.
    Das ist nicht das Scheitern, das ist der Vorgang, auf den wir warten.
    """
    main, wapk = worker_mod
    seen = []

    def fake_serial():
        seen.append(1)
        return None if len(seen) < 3 else "PA7B10"

    monkeypatch.setattr(wapk, "ready_serial", fake_serial)
    monkeypatch.setattr(wapk, "connect_usb", lambda s, **kw: {
        "ok": True, "package": "org.meumeu.wivrn", "detail": "", "uncertain": False})

    (ok, _detail, _package), status = _run(main.UsbConnectWorker(deadline=30))

    assert ok is True
    assert len(seen) >= 3
    assert "usb_connect_waiting" in status


def test_fehlende_app_wird_sofort_gemeldet(worker_mod, monkeypatch):
    main, wapk = worker_mod
    rounds = []

    monkeypatch.setattr(wapk, "ready_serial", lambda: "PA7B10")
    monkeypatch.setattr(wapk, "connect_usb", lambda s, **kw: (
        rounds.append(1),
        {"ok": False, "package": "",
         "detail": "Error: Activity not started, unable to resolve Intent",
         "uncertain": False})[1])

    (ok, detail, _package), _status = _run(main.UsbConnectWorker(deadline=30))

    assert ok is False
    assert "unable to resolve Intent" in detail
    # Genau ein Anlauf: Warten aendert an einer fehlenden App nichts.
    assert len(rounds) == 1


def test_frist_wird_eingehalten(worker_mod, monkeypatch):
    main, wapk = worker_mod
    monkeypatch.setattr(wapk, "ready_serial", lambda: "PA7B10")
    monkeypatch.setattr(wapk, "connect_usb", lambda s, **kw: {
        "ok": False, "package": "", "detail": "timeout", "uncertain": False})

    # deadline=0: der erste Durchlauf passiert noch, danach ist Schluss.
    (ok, detail, _package), _status = _run(main.UsbConnectWorker(deadline=0))

    assert ok is False
    assert detail == "timeout"


def test_abbrechen_wirkt(worker_mod, monkeypatch):
    main, wapk = worker_mod
    worker = main.UsbConnectWorker(deadline=30)

    monkeypatch.setattr(wapk, "ready_serial", lambda: "PA7B10")

    def fake_connect(serial, **kw):
        worker.cancel()          # mitten im Versuch abbrechen
        return {"ok": False, "package": "", "detail": "timeout", "uncertain": False}

    monkeypatch.setattr(wapk, "connect_usb", fake_connect)

    (ok, detail, _package), _status = _run(worker)

    assert ok is False
    assert detail == "cancelled"


def test_stumme_shell_wird_als_erfolg_gemeldet(worker_mod, monkeypatch):
    main, wapk = worker_mod
    monkeypatch.setattr(wapk, "ready_serial", lambda: "PA7B10")
    monkeypatch.setattr(wapk, "connect_usb", lambda s, **kw: {
        "ok": True, "package": "org.meumeu.wivrn",
        "detail": "timeout", "uncertain": True})

    (ok, detail, package), _status = _run(main.UsbConnectWorker(deadline=30))

    assert ok is True
    # Die Oberflaeche formuliert daraufhin vorsichtiger ("sollte jetzt
    # verbinden") statt Erfolg zu behaupten.
    assert detail == "uncertain"
    assert package == "org.meumeu.wivrn"


def test_hinweis_auf_die_usb_optionen_kommt_waehrend_des_wartens(worker_mod, monkeypatch):
    """
    Der Hinweis muss WAEHREND des Versuchs erscheinen, nicht erst in der
    Fehlermeldung danach: das Umschalten an der Brille ist die Loesung,
    solange der Versuch noch laeuft.
    """
    main, wapk = worker_mod
    rounds = []

    def fake_connect(serial, **kw):
        rounds.append(1)
        if len(rounds) < 2:
            return {"ok": False, "package": "", "detail": "timeout", "uncertain": False}
        return {"ok": True, "package": "", "detail": "", "uncertain": False}

    monkeypatch.setattr(wapk, "ready_serial", lambda: "PA7B10")
    monkeypatch.setattr(wapk, "connect_usb", fake_connect)

    (ok, _detail, _package), status = _run(main.UsbConnectWorker(deadline=30))

    assert ok is True
    assert "usb_connect_waiting" in status

    # Und der Text dahinter nennt den konkreten Handgriff.
    import json
    for code in ("de", "en"):
        texts = json.loads((ROOT / "locales" / f"{code}.json").read_text(encoding="utf-8"))
        assert "usb_connect_waiting" in texts
