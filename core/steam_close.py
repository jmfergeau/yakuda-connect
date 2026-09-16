#!/usr/bin/env python3
"""
core/steam_close.py — „Steam beenden, dann schreiben"
====================================================
Steam haelt config.vdf, localconfig.vdf und shortcuts.vdf im Speicher und
schreibt sie beim Beenden zurueck. Alles, was die App WAEHREND Steam laeuft
in diese Dateien schreibt, ist danach wieder weg — auch nach einem
„Neustart", denn der Neustart ist genau der Moment des Ueberschreibens.

Zwei Stellen brauchen deshalb denselben Ablauf:

  * „Use" im Games-Tab (Proton-Auswahl → config.vdf)
  * „In Steam eintragen" im Hinzufuegen-Dialog und im Panel eigener
    Spiele (neuer Eintrag → shortcuts.vdf)

Ablauf: nachfragen → ``steam -shutdown`` → per QTimer nachsehen, bis der
Prozess weg ist (Fenster bleibt bedienbar) → erst dann schreiben. Beendet
sich Steam nicht rechtzeitig, wird NICHT trotzdem geschrieben: das Ergebnis
waere wieder eine Aenderung, die Steam gleich ueberschreibt.
"""
import subprocess

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

import games as games_db
from logging_setup import get_logger
from translations import tr

log = get_logger("steam_close")

TIMEOUT_MS = 30000
POLL_MS = 500


def ask(parent, text_key, widen=None):
    """Rueckfrage „Steam beenden?". ``text_key`` erklaert, was sonst verloren geht."""
    box = QMessageBox(parent)
    box.setWindowTitle(tr("games_close_steam_title"))
    box.setIcon(QMessageBox.Question)
    box.setText(tr(text_key))
    btn_close = box.addButton(tr("games_close_steam_btn"), QMessageBox.AcceptRole)
    box.addButton(tr("cancel"), QMessageBox.RejectRole)
    box.setDefaultButton(btn_close)
    if widen is not None:
        widen(box)
    box.exec()
    return box.clickedButton() is btn_close


def close_then(owner, callback, status, timeout_ms=TIMEOUT_MS, poll_ms=POLL_MS,
               on_timer=None, keys=None):
    """
    Beendet Steam und ruft ``callback()`` auf, sobald der Prozess weg ist.

    owner    : QObject, an dem der Timer haengt (stirbt mit ihm)
    status   : Callback(text, farbe) fuer die Statuszeile
    on_timer : bekommt den Timer (bzw. None am Ende) — damit der Aufrufer
               ihn beim Schliessen anhalten kann
    keys     : Texte {"waiting", "timeout", "failed"} — sie nennen den
               Knopf, den man danach erneut druecken soll
    Rueckgabe: der laufende QTimer oder None, wenn Steam nicht beendet
    werden konnte.
    """
    keys = {"waiting": "games_close_steam_waiting",
            "timeout": "games_close_steam_timeout",
            "failed": "games_close_steam_failed", **(keys or {})}
    cmd = games_db.steam_shutdown_cmd()
    if not cmd:
        status(tr(keys["failed"]), "#bf616a")
        return None
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        log.warning("Steam konnte nicht beendet werden: %s", exc)
        status(tr(keys["failed"]), "#bf616a")
        return None

    status(tr(keys["waiting"]), "#88c0d0")
    waited = {"ms": 0}
    timer = QTimer(owner)

    def finish():
        timer.stop()
        if on_timer is not None:
            on_timer(None)

    def poll():
        waited["ms"] += poll_ms
        if not games_db.steam_is_running():
            finish()
            callback()
        elif waited["ms"] >= timeout_ms:
            finish()
            status(tr(keys["timeout"]), "#bf616a")

    timer.timeout.connect(poll)
    timer.start(poll_ms)
    if on_timer is not None:
        on_timer(timer)
    return timer
