#!/usr/bin/env python3
"""
core/adb_doctor.py — Wenn der adb-Handshake klemmt
==================================================
Anlass: nach einem Systemupdate (``android-tools`` aus dem AUR, dnf, apt)
findet adb die Brille nicht mehr — obwohl vorher alles lief und sich am
Kabel nichts geaendert hat. Der Nutzer sieht nur "kein Headset gefunden"
und sucht den Fehler bei sich.

Die vier Ursachen, die dahinterstecken, sehen fuer den Nutzer gleich aus,
brauchen aber verschiedene Handgriffe:

1. VERSIONSKONFLIKT. Das Update hat ``/usr/bin/adb`` ersetzt, der bereits
   LAUFENDE adb-Server ist aber noch der alte. Der neue Client meldet
   "adb server version (X) doesn't match this client (Y); killing..." und
   startet ihn neu. Meist heilt das von selbst — nicht aber, wenn der alte
   Server einem anderen Nutzer gehoert (z. B. per sudo gestartet) oder
   wenn ihn ein anderes Programm (SideQuest, ein Flatpak, Android Studio)
   festhaelt. Dann scheitert jeder Aufruf erneut.

2. BESTAETIGUNG WEG. Beim Neustart des Servers fragt die Brille erneut
   "USB-Debugging zulassen?". Liegt sie auf dem Tisch statt auf dem Kopf,
   tippt das niemand weg, und adb meldet dauerhaft ``unauthorized``.

3. UDEV-REGELN. Das Update hat die Regeln aus ``android-udev`` neu
   geschrieben, geladen sind aber noch die alten. adb meldet dann
   ``no permissions``. Ein Reload plus einmal Kabel ziehen behebt es.

4. MTP HAELT DAS GERAET. Dolphin/gvfs haben eine MTP-Sitzung offen. Auf
   manchen Brillen schliessen MTP und adb einander aus — die Fehlermeldung
   im Dateimanager ("Zugriff nicht moeglich, Geraet entsperren") und das
   schweigende adb sind dann dasselbe Problem.

Dieses Modul erkennt den Fall (``probe()``) und macht die Handgriffe, die
sich automatisieren lassen (``repair()``). Ohne Qt, damit es testbar bleibt.

Was es bewusst NICHT tut: ``~/.android/adbkey`` loeschen. Das ist der
haeufigste Ratschlag in Foren und der schaedlichste — danach muss JEDES
Android-Geraet des Nutzers neu bestaetigt werden, nicht nur die Brille.
"""
import json
import os
import re
import time

import proc
from logging_setup import get_logger
from paths import cache_file

log = get_logger("adb_doctor")

# Zustaende, die probe() liefern kann.
OK = "ok"                       # mindestens ein Geraet ist bereit
NO_ADB = "no_adb"               # adb ist nicht installiert
MISMATCH = "mismatch"           # Client/Server-Version passen nicht
UNAUTHORIZED = "unauthorized"   # Bestaetigung in der Brille fehlt
NO_PERMISSION = "no_permission"  # udev-Regeln greifen nicht
OFFLINE = "offline"             # Geraet da, antwortet aber nicht
NO_DEVICE = "no_device"         # adb laeuft, sieht aber nichts

# Nach einem Paketupdate merkt sich die App die Version, mit der der letzte
# erfolgreiche Handshake lief. Weicht sie ab, ist das die wahrscheinlichste
# Erklaerung — und die Meldung kann das sagen, statt zu raten.
_STATE_FILE = "adb_state.json"


# --------------------------------------------------------------------------- #
#  Versionen
# --------------------------------------------------------------------------- #
def client_version():
    """
    Version des adb-CLIENTS, z. B. "35.0.2" — oder "".

    ``adb version`` nennt zwei Nummern: das Protokoll ("1.0.41", seit Jahren
    unveraendert) und die Paketversion darunter. Interessant ist die zweite.
    """
    out = proc.output_of(["adb", "version"], timeout=proc.DEFAULT_TIMEOUT)
    m = re.search(r"^Version\s+([0-9][^\s]*)", out or "", re.M)
    if m:
        return m.group(1).strip()
    m = re.search(r"version\s+([0-9][0-9.]*)", out or "", re.I)
    return m.group(1).strip() if m else ""


def package_version():
    """
    Version des installierten Pakets ``android-tools`` — oder "".

    Drei Paketmanager, weil die App auf Arch, Fedora und Debian laeuft. Der
    erste, der antwortet, gewinnt; fehlt das Paket ueberall (AppImage-Nutzer
    mit handinstalliertem adb), bleibt es leer — dann entfaellt die
    Update-Erkennung, der Rest funktioniert weiter.
    """
    probes = (
        (["pacman", "-Q", "android-tools"], r"android-tools\s+(\S+)"),
        (["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", "android-tools"], r"^(\S+)"),
        (["dpkg-query", "-W", "-f=${Version}", "adb"], r"^(\S+)"),
    )
    for cmd, pattern in probes:
        if not proc.which(cmd[0]):
            continue
        out = proc.output_of(cmd, timeout=proc.DEFAULT_TIMEOUT)
        m = re.search(pattern, out or "")
        if m:
            return m.group(1).strip()
    return ""


# --------------------------------------------------------------------------- #
#  Gemerkter Zustand
# --------------------------------------------------------------------------- #
def _load_state():
    try:
        with open(cache_file(_STATE_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def remember_success():
    """
    Merkt sich Paket- und Client-Version des LETZTEN funktionierenden
    Handshakes. Aufrufer: die App, sobald ein Geraet ``device`` meldet.
    """
    state = {"package": package_version(),
             "client": client_version(),
             "time": int(time.time())}
    try:
        os.makedirs(os.path.dirname(cache_file(_STATE_FILE)), exist_ok=True)
        with open(cache_file(_STATE_FILE), "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError as exc:
        log.debug("adb-Zustand nicht speicherbar: %s", exc)
    return state


def updated_since_success():
    """
    ``(alt, neu)``, wenn das Paket seit dem letzten erfolgreichen Handshake
    aktualisiert wurde — sonst ``None``.

    Genau dieser Fall ist der Anlass fuer das ganze Modul: "es lief gestern
    noch" ist keine Einbildung, sondern hat ein Datum.
    """
    old = _load_state().get("package", "")
    new = package_version()
    if old and new and old != new:
        return old, new
    return None


# --------------------------------------------------------------------------- #
#  Diagnose
# --------------------------------------------------------------------------- #
def probe():
    """
    Einmal nachsehen, was los ist.

    Rueckgabe-Dict:
      ``state``    einer der Konstanten oben
      ``detail``   Rohausgabe von adb, fuers Log und den Aufklappbereich
      ``serial``   erstes Geraet, falls eines erkannt wurde
      ``updated``  ``(alt, neu)`` oder None — Paketupdate seit dem letzten Erfolg

    Ausgewertet wird stdout UND stderr: die interessanten Saetze
    ("doesn't match", "no permissions") schreibt adb nach stderr, waehrend
    die Geraeteliste auf stdout steht. Wer nur stdout liest, sieht eine
    leere Liste und haelt das faelschlich fuer "kein Geraet".
    """
    info = {"state": NO_DEVICE, "detail": "", "serial": "",
            "updated": updated_since_success()}

    if not proc.which("adb"):
        info["state"] = NO_ADB
        return info

    res = proc.run(["adb", "devices"], timeout=proc.DEFAULT_TIMEOUT)
    text = f"{res.stdout or ''}\n{res.stderr or ''}"
    info["detail"] = text.strip()
    low = text.lower()

    # Reihenfolge = Dringlichkeit. Ein Versionskonflikt macht alles andere
    # unzuverlaessig, also wird er zuerst gemeldet.
    if "doesn't match this client" in low or "killing..." in low:
        info["state"] = MISMATCH
        return info
    if "no permissions" in low or "insufficient permissions" in low:
        info["state"] = NO_PERMISSION
        return info

    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("list of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if state == "device":
            return {"state": OK, "detail": info["detail"], "serial": serial,
                    "updated": info["updated"]}
        if state == "unauthorized":
            info["state"], info["serial"] = UNAUTHORIZED, serial
        elif state in ("offline", "recovery", "bootloader") and info["state"] == NO_DEVICE:
            info["state"], info["serial"] = OFFLINE, serial

    return info


def mtp_busy():
    """
    Haelt gerade ein Dateimanager eine MTP-Sitzung auf die Brille offen?

    Kein Beweis, sondern ein Indiz: laeuft ein MTP-Backend, ist das bei
    einem klemmenden Handshake einen Hinweis wert ("erst das Fenster im
    Dateimanager schliessen"). Deshalb blockiert es auch nichts — es steht
    nur als Zusatz in der Meldung.
    """
    for name in ("gvfsd-mtp", "kiod5", "mtpfs", "jmtpfs", "simple-mtpfs"):
        if proc.run_ok(["pgrep", "-x", name], timeout=proc.DEFAULT_TIMEOUT):
            return True
    return False


# --------------------------------------------------------------------------- #
#  Reparatur
# --------------------------------------------------------------------------- #
def restart_server():
    """
    ``adb kill-server`` + ``adb start-server``.

    Das ist die Behandlung fuer den Versionskonflikt und hilft auch bei
    einem Server, der sich verhakt hat. ``start-server`` bekommt mehr Zeit:
    der frische Daemon scannt beim Hochfahren den USB-Bus.
    """
    proc.run(["adb", "kill-server"], timeout=proc.DEFAULT_TIMEOUT, check_log=False)
    res = proc.run(["adb", "start-server"], timeout=proc.LONG_TIMEOUT)
    return res.returncode == 0


def reload_udev():
    """
    udev-Regeln neu laden und anwenden — braucht Rechte, also ``pkexec``.

    Ein Aufruf statt zweier (``control --reload-rules`` und ``trigger``):
    sonst kaeme die Passwortabfrage zweimal hintereinander, was wie ein
    Fehler aussieht. Deshalb hier ausnahmsweise ``sh -c``; die Zeichenkette
    ist eine feste Konstante, es wird nichts eingesetzt.
    """
    res = proc.run(["pkexec", "sh", "-c",
                    "udevadm control --reload-rules && udevadm trigger"],
                   timeout=proc.LONG_TIMEOUT)
    return res.returncode == 0


def repair(state, with_udev=False):
    """
    Die automatisierbaren Handgriffe fuer ``state`` ausfuehren.

    Rueckgabe ``(erfolg, schritte)``; ``schritte`` ist eine Liste von
    Uebersetzungs-Schluesseln, damit die Oberflaeche zeigen kann, was
    passiert ist, ohne dass dieses Modul Texte kennt.

    ``with_udev`` ist getrennt, weil dieser Schritt eine Passwortabfrage
    ausloest. Sie soll nur kommen, wenn sie wirklich ansteht — und nicht bei
    jedem Klick auf "Reparieren".
    """
    steps = []

    if state == NO_ADB:
        return False, ["adb_fix_step_install"]

    if with_udev or state == NO_PERMISSION:
        steps.append("adb_fix_step_udev")
        if not reload_udev():
            return False, steps + ["adb_fix_step_udev_failed"]

    steps.append("adb_fix_step_restart")
    if not restart_server():
        return False, steps + ["adb_fix_step_restart_failed"]

    # Dem frischen Server einen Moment geben, sonst meldet die Nachschau
    # "kein Geraet", obwohl er noch am Aufzaehlen ist.
    time.sleep(1.5)

    after = probe()
    if after["state"] == OK:
        remember_success()
        return True, steps + ["adb_fix_step_ok"]

    # Unauthorized nach dem Neustart ist KEIN Fehlschlag der Reparatur,
    # sondern der erwartete Zwischenschritt: die Brille fragt jetzt nach.
    if after["state"] == UNAUTHORIZED:
        return False, steps + ["adb_fix_step_confirm"]

    return False, steps + ["adb_fix_step_still_broken"]
