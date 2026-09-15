#!/usr/bin/env python3
"""
tests/test_wivrn_apk.py — Client-APK und Verbindung ueber das Kabel
===================================================================
Der teure Fehler in diesem Bereich ist nicht "es stuerzt ab", sondern "es
installiert klaglos die falsche Version": Server 25.12, APK 26.4, und die
Brille verbindet danach nicht mehr. Genau das pruefen die ersten Tests.

Ohne Netz und ohne Headset — GitHub-Antworten und adb-Aufrufe werden
eingesetzt.

Geprueft wird:
  1. Das Release wird nach der SERVER-Version gewaehlt, nicht nach "neu".
  2. Passt keines, ist das Ergebnis ein Rueckfall MIT Kennzeichnung.
  3. Der Dateiname traegt die Version (sonst sind drei Downloads
     ununterscheidbar).
  4. Eine Antwort, die keine APK ist (Fehlerseite), wird abgelehnt — und
     hinterlaesst keine Datei.
  5. Ein Abbruch laesst keine halbe Datei zurueck, die wie eine fertige
     aussieht.
  6. Der Paketname wird auf der Brille ermittelt, nicht geraten.
  7. Verbunden wird mit ``adb reverse`` (nicht ``forward``).
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))


@pytest.fixture
def wapk(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import wivrn_apk
    return wivrn_apk


def _release(tag, prerelease=False, asset="org.meumeu.wivrn-release.apk"):
    assets = [{"name": asset,
               "browser_download_url": f"https://example.invalid/{tag}/{asset}",
               "size": 1234}] if asset else []
    return {"tag_name": tag, "prerelease": prerelease, "draft": False,
            "assets": assets}


# --------------------------------------------------------------------------- #
#  1-2: die richtige Version
# --------------------------------------------------------------------------- #
def test_pick_release_nimmt_die_passende_nicht_die_neueste(wapk, monkeypatch):
    releases = [_release("v26.4"), _release("v26.1"), _release("v25.12")]
    monkeypatch.setattr(wapk, "list_releases", lambda **kw: releases)

    release, matched, server = wapk.pick_release(server="25.12")

    assert release["tag_name"] == "v25.12"
    assert matched is True
    assert server == "25.12"


def test_pick_release_kennzeichnet_den_rueckfall(wapk, monkeypatch):
    # Server 24.1 — dafuer gibt es kein Release mehr.
    releases = [_release("v26.4"), _release("v25.12")]
    monkeypatch.setattr(wapk, "list_releases", lambda **kw: releases)

    release, matched, _server = wapk.pick_release(server="24.1")

    assert release["tag_name"] == "v26.4"
    # False, NICHT None: die Oberflaeche muss hier warnen duerfen.
    assert matched is False


def test_pick_release_ohne_server_kennzeichnet_nichts(wapk, monkeypatch):
    monkeypatch.setattr(wapk, "list_releases", lambda **kw: [_release("v26.4")])

    release, matched, _server = wapk.pick_release(server="")

    assert release["tag_name"] == "v26.4"
    # None = "weiss nicht" -> keine Warnung, aber auch keine Bestaetigung.
    assert matched is None


def test_pick_release_ignoriert_releases_ohne_apk(wapk, monkeypatch):
    monkeypatch.setattr(wapk, "list_releases",
                        lambda **kw: [_release("v26.4", asset=None), _release("v25.12")])

    release, _matched, _server = wapk.pick_release(server="")
    assert release["tag_name"] == "v25.12"


def test_patch_nummer_bricht_die_kompatibilitaet_nicht(wapk):
    # WiVRn nummeriert Jahr.Monat; eine dritte Stelle ist ein Bugfix.
    assert wapk.versions_match("25.12.1", "25.12") is True
    assert wapk.versions_match("26.1", "25.12") is False
    # git-describe-Suffix darf den Vergleich nicht kippen
    assert wapk.version_key("25.12-30-gabc123") == (25, 12)
    # Unbekannt ist nicht "falsch"
    assert wapk.versions_match("", "25.12") is None


# --------------------------------------------------------------------------- #
#  3: Dateiname
# --------------------------------------------------------------------------- #
def test_dateiname_traegt_die_version(wapk):
    name = wapk.suggested_filename(_release("v25.12"), "org.meumeu.wivrn-release.apk")
    assert name == "org.meumeu.wivrn-release-25.12.apk"


# --------------------------------------------------------------------------- #
#  4-5: Download
# --------------------------------------------------------------------------- #
class _FakeResponse:
    """Minimales Gegenstueck zu urlopen(): liefert Bloecke der Reihe nach."""

    def __init__(self, chunks, total=None):
        self._chunks = list(chunks)
        body = b"".join(chunks)
        self.headers = {"Content-Length": str(total if total is not None else len(body))}

    def read(self, _size=-1):
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, wapk, chunks):
    monkeypatch.setattr(wapk.urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResponse(chunks))


def test_fehlerseite_wird_abgelehnt(wapk, tmp_path, monkeypatch):
    _serve(monkeypatch, wapk, [b"<!DOCTYPE html><html>Anmeldung noetig"])
    dest = tmp_path / "wivrn.apk"

    with pytest.raises(wapk.DownloadError) as exc:
        wapk.download("https://example.invalid/x.apk", str(dest))

    assert exc.value.key == "apk_status_bad_file"
    assert not dest.exists()
    assert not (tmp_path / "wivrn.apk.part").exists()


def test_echte_apk_landet_am_ziel(wapk, tmp_path, monkeypatch):
    _serve(monkeypatch, wapk, [wapk.ZIP_MAGIC + b"restlicher", b"inhalt"])
    dest = tmp_path / "wivrn.apk"

    wapk.download("https://example.invalid/x.apk", str(dest))

    assert dest.read_bytes().startswith(wapk.ZIP_MAGIC)
    assert not (tmp_path / "wivrn.apk.part").exists()


def test_abbruch_hinterlaesst_keine_halbe_datei(wapk, tmp_path, monkeypatch):
    _serve(monkeypatch, wapk, [wapk.ZIP_MAGIC + b"a", b"b", b"c"])
    dest = tmp_path / "wivrn.apk"

    with pytest.raises(wapk.Cancelled):
        wapk.download("https://example.invalid/x.apk", str(dest),
                      cancel=lambda: True)

    assert not dest.exists()
    assert not (tmp_path / "wivrn.apk.part").exists()


# --------------------------------------------------------------------------- #
#  6-7: die Brille am Kabel
# --------------------------------------------------------------------------- #
class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_paketname_kommt_von_der_brille(wapk, monkeypatch):
    listing = ("package:com.oculus.shellenv\n"
               "package:org.meumeu.wivrn.github\n")
    monkeypatch.setattr(wapk.proc, "run", lambda cmd, **kw: _Result(0, stdout=listing))

    assert wapk.detect_package("SERIAL1") == "org.meumeu.wivrn.github"


def test_store_version_gewinnt_bei_zwei_installationen(wapk, monkeypatch):
    listing = ("package:org.meumeu.wivrn.local\n"
               "package:org.meumeu.wivrn\n")
    monkeypatch.setattr(wapk.proc, "run", lambda cmd, **kw: _Result(0, stdout=listing))

    assert wapk.detect_package("SERIAL1") == "org.meumeu.wivrn"


def test_ohne_wivrn_app_kein_paket(wapk, monkeypatch):
    monkeypatch.setattr(wapk.proc, "run", lambda cmd, **kw: _Result(
        0, stdout="package:com.oculus.shellenv\n"))
    assert wapk.detect_package("SERIAL1") is None


def _no_sleep(wapk, monkeypatch):
    """Wartezeiten ueberspringen — sonst dauert der Test Minuten."""
    monkeypatch.setattr(wapk, "_sleep", lambda s, cancel=None: None)


def test_verbinden_benutzt_reverse_und_den_richtigen_intent(wapk, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _Result(0, stdout="Starting: Intent {...}")

    monkeypatch.setattr(wapk.proc, "run", fake_run)
    monkeypatch.setattr(wapk, "detect_packages",
                        lambda s, **kw: ["org.meumeu.wivrn.github"])

    res = wapk.connect_usb("SERIAL1")

    assert res["ok"] and not res["uncertain"]
    assert res["package"] == "org.meumeu.wivrn.github"
    assert calls[0] == ["adb", "-s", "SERIAL1", "reverse", "tcp:9757", "tcp:9757"]
    assert "forward" not in " ".join(calls[0])
    assert "wivrn+tcp://localhost" in calls[1]
    assert calls[1][-1] == "org.meumeu.wivrn.github"


def test_ohne_erkanntes_paket_wird_trotzdem_verbunden(wapk, monkeypatch):
    """
    Der Fehler aus der ersten Fassung: ``pm list packages`` liefert nichts,
    also brach die App mit "keine WiVRn-App gefunden" ab — waehrend das
    offizielle Dashboard auf derselben Brille verbindet, weil es gar nicht
    erst fragt. Eine leere Erkennung darf den Versuch nicht verhindern.
    """
    attempts = []

    def fake_run(cmd, **kw):
        if "reverse" in cmd:
            return _Result(0)
        attempts.append(cmd[-1])
        if cmd[-1] == "org.meumeu.wivrn.github.nighly":
            return _Result(0, stdout="Starting: Intent {...}")
        return _Result(0, stdout="Error: Activity not started, unable to resolve Intent")

    monkeypatch.setattr(wapk.proc, "run", fake_run)
    monkeypatch.setattr(wapk, "detect_packages", lambda s, **kw: [])

    res = wapk.connect_usb("SERIAL1")

    assert res["ok"] is True
    assert res["package"] == "org.meumeu.wivrn.github.nighly"
    assert attempts[0] == "org.meumeu.wivrn"


def test_letzter_versuch_laeuft_ohne_paketangabe(wapk, monkeypatch):
    def fake_run(cmd, **kw):
        if "reverse" in cmd:
            return _Result(0)
        if cmd[-1].startswith("org.meumeu"):
            return _Result(0, stdout="Error: Activity not started, unable to resolve Intent")
        return _Result(0, stdout="Starting: Intent {...}")

    monkeypatch.setattr(wapk.proc, "run", fake_run)
    monkeypatch.setattr(wapk, "detect_packages", lambda s, **kw: [])

    res = wapk.connect_usb("SERIAL1")
    assert res["ok"] is True
    assert res["package"] == ""


def test_fehlt_die_app_wirklich_wird_es_gemeldet(wapk, monkeypatch):
    def fake_run(cmd, **kw):
        if "reverse" in cmd:
            return _Result(0)
        return _Result(0, stdout="Error: Activity not started, unable to resolve Intent")

    monkeypatch.setattr(wapk.proc, "run", fake_run)
    monkeypatch.setattr(wapk, "detect_packages", lambda s, **kw: [])

    res = wapk.connect_usb("SERIAL1")
    assert res["ok"] is False and res["package"] == ""
    assert "unable to resolve Intent" in res["detail"]
    # Ein fehlendes Paket aendert sich durch Warten nicht — nicht wiederholen.
    assert wapk.retryable(res["detail"]) is False


def test_bereits_laufende_app_gilt_als_erfolg(wapk, monkeypatch):
    monkeypatch.setattr(wapk.proc, "run", lambda cmd, **kw: _Result(
        0, stdout="Warning: Activity not started, intent has been delivered "
                  "to currently running top activity"))
    monkeypatch.setattr(wapk, "detect_packages", lambda s, **kw: ["org.meumeu.wivrn"])

    res = wapk.connect_usb("SERIAL1")
    assert res["ok"] is True
    assert res["package"] == "org.meumeu.wivrn"


# --------------------------------------------------------------------------- #
#  Der PICO-Fall: adb schweigt, weil MTP die Schnittstelle blockiert
# --------------------------------------------------------------------------- #
def test_stiller_adb_aufruf_wird_wiederholt(wapk, monkeypatch):
    """
    Die PICO antwortet erst, nachdem der Nutzer an der Brille kurz auf
    "Nur Laden" und zurueck gestellt hat. Ein einziger Aufruf trifft genau
    das Zeitfenster davor und meldet "timeout" — obwohl es zwei Sekunden
    spaeter geklappt haette.
    """
    _no_sleep(wapk, monkeypatch)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # Erst der dritte Versuch geht durch.
        if len(calls) < 3:
            return _Result(wapk.proc.RC_TIMEOUT, stderr="timeout")
        return _Result(0, stdout="ok")

    monkeypatch.setattr(wapk.proc, "run", fake_run)

    res = wapk.adb("SERIAL1", ["reverse", "tcp:9757", "tcp:9757"])
    assert res.returncode == 0
    assert len(calls) == 3


def test_echte_fehler_werden_nicht_wiederholt(wapk, monkeypatch):
    # Nur Stille rechtfertigt einen zweiten Anlauf. "device not found"
    # dreimal zu fragen kostet nur Zeit.
    _no_sleep(wapk, monkeypatch)
    calls = []
    monkeypatch.setattr(wapk.proc, "run", lambda cmd, **kw: (
        calls.append(cmd), _Result(1, stderr="device 'SERIAL1' not found"))[1])

    res = wapk.adb("SERIAL1", ["reverse", "tcp:9757", "tcp:9757"])
    assert res.returncode == 1
    assert len(calls) == 1


def test_retry_meldet_sich_bei_der_oberflaeche(wapk, monkeypatch):
    _no_sleep(wapk, monkeypatch)
    seen = []
    monkeypatch.setattr(wapk.proc, "run",
                        lambda cmd, **kw: _Result(wapk.proc.RC_TIMEOUT, stderr="timeout"))

    wapk.adb("SERIAL1", ["reverse"], on_retry=lambda a, t: seen.append((a, t)))

    # Zwei Meldungen bei drei Versuchen: vor dem letzten wird nicht mehr
    # angekuendigt, danach kommt das Ergebnis.
    assert seen == [(1, 3), (2, 3)]


def test_stumme_shell_nach_dem_intent_gilt_als_erfolg(wapk, monkeypatch):
    """
    Kern des PICO-Problems: ``am start`` setzt den Intent ab, die Brille
    startet WiVRn — aber die adb-Shell meldet wegen des MTP-Konflikts kein
    Dateiende. Das als Fehlschlag zu werten hiesse, dem Nutzer eine
    Fehlermeldung zu zeigen, waehrend die Brille bereits verbindet.
    """
    _no_sleep(wapk, monkeypatch)

    def fake_run(cmd, **kw):
        if "reverse" in cmd:
            return _Result(0)
        return _Result(wapk.proc.RC_TIMEOUT, stderr="timeout")

    monkeypatch.setattr(wapk.proc, "run", fake_run)
    monkeypatch.setattr(wapk, "detect_packages", lambda s, **kw: ["org.meumeu.wivrn"])

    res = wapk.connect_usb("SERIAL1")
    assert res["ok"] is True
    assert res["uncertain"] is True
    assert res["package"] == "org.meumeu.wivrn"


def test_stille_shell_probiert_nicht_alle_pakete_durch(wapk, monkeypatch):
    """
    Nach einer stummen Shell ist der Intent raus. Weiterzumachen hiesse,
    fuenfmal ins Zeitlimit zu laufen und die ohnehin klemmende Brille
    zusaetzlich zu beschaeftigen.
    """
    _no_sleep(wapk, monkeypatch)
    intents = []

    def fake_run(cmd, **kw):
        if "reverse" in cmd:
            return _Result(0)
        intents.append(cmd)
        return _Result(wapk.proc.RC_TIMEOUT, stderr="timeout")

    monkeypatch.setattr(wapk.proc, "run", fake_run)
    monkeypatch.setattr(wapk, "detect_packages", lambda s, **kw: [])

    wapk.connect_usb("SERIAL1")
    # Ein Paket, dreimal versucht — und dann Schluss.
    assert len({tuple(c) for c in intents}) == 1


def test_timeout_ist_wiederholenswert(wapk):
    assert wapk.retryable("timeout") is True
    assert wapk.retryable("device 'X' not found") is True
    assert wapk.retryable("Error: Activity not started, unable to resolve Intent") is False


def test_abbruch_wirkt_waehrend_der_wartezeit(wapk, monkeypatch):
    monkeypatch.setattr(wapk.proc, "run",
                        lambda cmd, **kw: _Result(wapk.proc.RC_TIMEOUT, stderr="timeout"))

    with pytest.raises(wapk.Cancelled):
        wapk.adb("SERIAL1", ["reverse"], cancel=lambda: True)


def test_paketerkennung_gibt_bei_stille_sofort_auf(wapk, monkeypatch):
    # Haengt die USB-Seite, haengt sie fuer alle drei Abfragevarianten.
    # Dreimal ins Zeitlimit zu laufen verzoegert nur den eigentlichen
    # Verbindungsversuch.
    calls = []
    monkeypatch.setattr(wapk.proc, "run", lambda cmd, **kw: (
        calls.append(cmd), _Result(wapk.proc.RC_TIMEOUT, stderr="timeout"))[1])

    assert wapk.detect_packages("SERIAL1") == []
    assert len(calls) == 1


def test_paketliste_vertraegt_crlf_und_uid_anhang(wapk, monkeypatch):
    monkeypatch.setattr(wapk.proc, "run", lambda cmd, **kw: _Result(
        0, stdout="package:org.meumeu.wivrn.github uid:10123\r\n"))
    assert wapk.detect_packages("SERIAL1") == ["org.meumeu.wivrn.github"]


def test_nur_bereite_geraete_zaehlen(wapk, monkeypatch):
    monkeypatch.setattr(wapk.usbhs, "adb_devices",
                        lambda: {"AAA": "unauthorized", "BBB": "device"})
    assert wapk.ready_serial() == "BBB"

    monkeypatch.setattr(wapk.usbhs, "adb_devices", lambda: {"AAA": "unauthorized"})
    assert wapk.ready_serial() is None
