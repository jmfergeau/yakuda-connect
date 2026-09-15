#!/usr/bin/env python3
"""
tests/test_usb_autoconnect.py — "Automatisch per USB verbinden"
================================================================
Der Haken schrieb bisher nur eine Option in wivrn-dashboard.conf.
Ausgefuehrt wird die aber vom WiVRn-Dashboard; wer Yakuda Connect statt
dessen benutzt, hatte einen Haken ohne Wirkung und musste trotzdem zum PC
laufen und klicken.

``_maybe_auto_connect_usb`` poll jetzt selbst. Die Logik ist eine KANTE,
kein Zustand — und genau daran haengt, ob sich das Feature richtig anfuehlt
oder unbenutzbar wird:

  * Bei jedem Durchlauf zu verbinden, solange die Brille bereit ist, hiesse
    alle vier Sekunden ein Intent — auch mitten im Spielen.
  * Und ein absichtliches "Trennen" waere vier Sekunden spaeter wieder
    rueckgaengig gemacht. Der Nutzer kaeme gegen sein eigenes Programm
    nicht an.

Getestet wird die Methode direkt am Mixin, mit einem Attrappen-Objekt
statt eines echten Fensters: die Logik haengt an nichts als den abgefragten
Werten.
"""
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))


@pytest.fixture
def mixin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from tabs import dashboard_mixin
    # Nichts von aussen anfassen: Dashboard laeuft nicht, Server laeuft.
    monkeypatch.setattr(dashboard_mixin.wivrn_dash, "dashboard_is_running", lambda: False)
    monkeypatch.setattr(dashboard_mixin.wivrn_server, "is_running", lambda p=None: True)
    return dashboard_mixin


def make_app(checked=True, armed=True, connected=False, busy=False):
    """Ein Attrappen-Fenster mit genau den Feldern, die die Methode liest."""
    calls = {"connect": 0, "notice": []}

    app = types.SimpleNamespace()
    app.ui = types.SimpleNamespace(
        check_usb_autoconnect=types.SimpleNamespace(isChecked=lambda: checked))
    app.server_process = None
    app._usb_auto_armed = armed
    app._connect_worker = (types.SimpleNamespace(isRunning=lambda: True) if busy else None)
    app.is_headset_connected = lambda: connected
    app.connect_usb_headset = lambda: calls.__setitem__("connect", calls["connect"] + 1)
    app._set_usb_notice = lambda text, color, seconds=8: calls["notice"].append(text)
    app.calls = calls
    return app


def run(mixin, app, state="ready"):
    mixin.DashboardMixin._maybe_auto_connect_usb(app, {"state": state})
    return app.calls["connect"]


# --------------------------------------------------------------------------- #
#  Der Normalfall
# --------------------------------------------------------------------------- #
def test_brille_am_kabel_verbindet_von_selbst(mixin):
    app = make_app()
    assert run(mixin, app) == 1


def test_ohne_haken_passiert_nichts(mixin):
    app = make_app(checked=False)
    assert run(mixin, app) == 0


# --------------------------------------------------------------------------- #
#  Die Kante
# --------------------------------------------------------------------------- #
def test_nur_einmal_pro_ansteckvorgang(mixin):
    """
    Ohne diese Bremse feuerte alle vier Sekunden ein Intent — auch mitten
    im Spielen.
    """
    app = make_app()
    run(mixin, app)
    run(mixin, app)
    run(mixin, app)
    assert app.calls["connect"] == 1


def test_kabel_ab_macht_die_kante_wieder_scharf(mixin):
    app = make_app()
    run(mixin, app)                      # erstes Anstecken
    run(mixin, app, state="none")        # Kabel ab
    assert app._usb_auto_armed is True
    run(mixin, app)                      # wieder angesteckt
    assert app.calls["connect"] == 2


def test_manuelles_trennen_wird_nicht_ueberstimmt(mixin):
    # Nach "Trennen" setzt disconnect_current_headset die Kante zurueck.
    # Solange das Kabel steckt, darf nichts nachfeuern.
    app = make_app(armed=False)
    assert run(mixin, app) == 0
    assert run(mixin, app) == 0


def test_schon_verbunden_entschaerft_statt_zu_verbinden(mixin):
    app = make_app(connected=True)
    assert run(mixin, app) == 0
    # Wichtig: entschaerft. Sonst feuerte es sofort nach dem Trennen los,
    # obwohl das Kabel die ganze Zeit steckte.
    assert app._usb_auto_armed is False


# --------------------------------------------------------------------------- #
#  Wann es sich heraushaelt
# --------------------------------------------------------------------------- #
def test_laufendes_wivrn_dashboard_macht_es_selbst(mixin, monkeypatch):
    monkeypatch.setattr(mixin.wivrn_dash, "dashboard_is_running", lambda: True)
    app = make_app()
    assert run(mixin, app) == 0
    # Kante bleibt scharf: wird das Dashboard beendet, uebernehmen wir.
    assert app._usb_auto_armed is True


def test_ohne_server_wird_gewartet_nicht_aufgegeben(mixin, monkeypatch):
    monkeypatch.setattr(mixin.wivrn_server, "is_running", lambda p=None: False)
    app = make_app()
    assert run(mixin, app) == 0
    assert app._usb_auto_armed is True

    # Server startet -> naechster Durchlauf verbindet, ohne dass der Nutzer
    # das Kabel noch einmal anfassen muss.
    monkeypatch.setattr(mixin.wivrn_server, "is_running", lambda p=None: True)
    assert run(mixin, app) == 1


def test_laufender_versuch_wird_nicht_verdoppelt(mixin):
    app = make_app(busy=True)
    assert run(mixin, app) == 0


def test_unauthorized_ist_kein_anlass(mixin):
    # Brille am Kabel, aber adb nicht bestaetigt: verbinden wuerde scheitern.
    app = make_app()
    assert run(mixin, app, state="unauthorized") == 0


def test_hinweis_erscheint_beim_automatischen_verbinden(mixin):
    app = make_app()
    run(mixin, app)
    assert app.calls["notice"], "Ohne Meldung wirkt es, als passiere von selbst etwas Unerklaerliches"


def test_leerer_scan_kippt_nichts(mixin):
    app = make_app()
    mixin.DashboardMixin._maybe_auto_connect_usb(app, None)
    assert app.calls["connect"] == 0
