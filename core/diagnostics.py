#!/usr/bin/env python3
"""
core/diagnostics.py — Der Bericht fuer Fehlermeldungen
======================================================
Sammelt die Randbedingungen, nach denen sonst jedes Mal einzeln gefragt
werden muss: Versionen, Distribution, GPU, OpenXR-Runtime, adb-Zustand,
Firewall, Netzwerkpuffer. Als Textblock zum Einfuegen in ein GitHub-Issue
oder in einen Chat.

Wozu, wenn es doch die Logdatei gibt: im Log steht, WAS passiert ist, aber
nicht, auf welchem System. Und genau die Frage kommt bei jeder zweiten
Fehlermeldung zurueck ("welche WiVRn-Version? welche Distro? Wayland oder
X11?"). Der Bericht nimmt drei Nachfragerunden vorweg.

Ohne Qt, damit er ohne laufende Oberflaeche erzeugt und getestet werden
kann.

----------------------------------------------------------------------
Zwei Regeln, die hier nicht verhandelbar sind
----------------------------------------------------------------------
1. JEDE Angabe einzeln absichern. Der Bericht wird gerade dann gebraucht,
   wenn etwas kaputt ist — faellt eine Abfrage aus, muss der Rest trotzdem
   entstehen. Deshalb liefert ``_safe()`` im Fehlerfall einen Platzhalter
   statt eine Ausnahme nach oben zu reichen.

2. NICHTS Persoenliches. Der Bericht landet in oeffentlichen Issues. Der
   Heimatpfad wird zu ``~`` gekuerzt, und der Benutzername faellt auch aus
   Pfaden heraus, die anders gebaut sind. IP-Adressen und Hostnamen kommen
   gar nicht erst vor — was nicht gesammelt wird, kann auch nicht
   verplappert werden.
"""
import datetime
import os
import platform
import re
import sys

import proc
from logging_setup import get_logger

log = get_logger("diagnostics")

UNKNOWN = "?"
FAILED = "(nicht ermittelbar)"


# --------------------------------------------------------------------------- #
#  Werkzeug
# --------------------------------------------------------------------------- #
def _safe(fn, default=FAILED):
    """Eine Abfrage ausfuehren; scheitert sie, kommt der Platzhalter."""
    try:
        value = fn()
    except Exception as exc:
        log.debug("Diagnose: %s", exc)
        return default
    if value is None or value == "":
        return default
    return value


# Namen, die NICHT geschwaerzt werden duerfen, auch wenn sie zufaellig den
# Benutzernamen enthalten. Der Anlass ist kein erfundener Sonderfall: Der
# Autor dieser Anwendung heisst yakuda, das Programm heisst yakuda-connect —
# auf seinem Rechner machte die Schwaerzung aus der Kopfzeile
# "<user>-connect diagnostics" und aus jedem Cache-Pfad
# "~/.cache/<user>-connect/app.log". Der Bericht war damit an genau den
# Stellen unleserlich, an denen er etwas aussagen soll.
SAFE_LITERALS = ("yakuda-connect",)

_SENTINEL = "\x00{}\x00"


def redact(text):
    """
    Heimatpfad und Benutzername aus einem Text entfernen.

    Drei Schritte, und die Reihenfolge ist wichtig:

    1. Geschuetzte Namen (``SAFE_LITERALS``) beiseitelegen. Sonst zerlegt
       Schritt 3 den Programmnamen, wenn er den Benutzernamen enthaelt.
    2. Heimatpfad zu ``~`` kuerzen.
    3. Den Benutzernamen ersetzen — und zwar nicht nur im eigenen
       Heimatpfad: Flatpak-Pfade, Steam-Bibliotheken auf anderen Laufwerken
       und Fehlermeldungen tragen ihn ebenso.

    Namen unter drei Zeichen bleiben stehen: ein Benutzer namens "vr" oder
    "pi" wuerde sonst mitten in anderen Woertern zuschlagen.
    """
    if not text:
        return text

    # 1. Schuetzen — Gross-/Kleinschreibung egal, Originalform wird beim
    #    Zuruecklegen wiederhergestellt.
    parked = []
    for index, literal in enumerate(SAFE_LITERALS):
        def _park(match, index=index):
            parked.append(match.group(0))
            return _SENTINEL.format(f"{index}:{len(parked) - 1}")
        text = re.sub(re.escape(literal), _park, text, flags=re.I)

    # 2. Heimatpfad
    home = os.path.expanduser("~")
    if home and home != "/":
        text = text.replace(home, "~")

    # 3. Benutzername
    user = os.environ.get("USER") or os.path.basename(home or "")
    if user and len(user) > 2:
        text = re.sub(rf"\b{re.escape(user)}\b", "<user>", text, flags=re.I)

    # 1b. Zuruecklegen
    for index, literal in enumerate(SAFE_LITERALS):
        for slot, original in enumerate(parked):
            text = text.replace(_SENTINEL.format(f"{index}:{slot}"), original)
    return text


def _section(title, rows):
    """Ein Abschnitt mit ausgerichteten Beschriftungen."""
    lines = [f"[{title}]"]
    width = max((len(label) for label, _ in rows), default=0)
    for label, value in rows:
        lines.append(f"  {label.ljust(width)} : {value}")
    return lines


# --------------------------------------------------------------------------- #
#  Die einzelnen Abschnitte
# --------------------------------------------------------------------------- #
def system_rows():
    return [
        ("Python", sys.version.split()[0]),
        ("Platform", _safe(platform.platform)),
        ("Distro", _safe(_distro_name)),
        ("Desktop", f"{os.environ.get('XDG_CURRENT_DESKTOP', UNKNOWN)} "
                    f"({os.environ.get('XDG_SESSION_TYPE', UNKNOWN)})"),
        ("Flatpak", "ja" if os.path.exists("/.flatpak-info") else "nein"),
        ("AppImage", "ja" if os.environ.get("APPIMAGE") else "nein"),
    ]


def _distro_name():
    """``PRETTY_NAME`` aus /etc/os-release — die verlaesslichste Quelle."""
    with open("/etc/os-release", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    return UNKNOWN


def gpu_rows():
    """
    Grafikkarte und Treiber.

    ``lspci`` ist nicht ueberall installiert, deshalb faellt es weich auf
    das aus, was der Kernel meldet. Die Zeile ist wichtig genug, um beide
    Wege zu rechtfertigen: bei Bildfehlern und Encoder-Problemen ist sie
    das Erste, wonach gefragt wird.
    """
    rows = [("Renderer", _safe(lambda: os.environ.get("DRI_PRIME") and
                               f"DRI_PRIME={os.environ['DRI_PRIME']}", "-"))]
    gpus = _safe(_gpu_list, "")
    rows.insert(0, ("GPU", gpus or FAILED))
    return rows


def _gpu_list():
    out = proc.output_of(["lspci", "-nn"], timeout=proc.DEFAULT_TIMEOUT)
    names = []
    for line in (out or "").splitlines():
        if re.search(r"(VGA compatible controller|3D controller|Display controller)", line):
            names.append(line.split(":", 2)[-1].strip())
    return " | ".join(names)


def wivrn_rows():
    """
    Der wichtigste Abschnitt: passen Server und Brille zusammen?

    Client und Server muessen dieselbe Version haben — das ist die haeufigste
    Ursache fuer "verbindet nicht" und steht deshalb als eigene Zeile da,
    fertig ausgewertet, statt beide Nummern nebeneinanderzustellen und das
    Vergleichen dem Leser zu ueberlassen.
    """
    import wivrn_apk as wapk
    import vr_environment as venv

    server = _safe(wapk.server_version, "")
    rows = [
        ("Server", server or FAILED),
        ("Server binary", _safe(venv.wivrn_server_binary, "-")),
        ("OpenXR runtime", _safe(venv.primary_active_runtime, "-")),
    ]

    serial = _safe(wapk.ready_serial, "")
    if serial and serial != FAILED:
        package = _safe(lambda: wapk.detect_package(serial), "")
        client = _safe(lambda: wapk.client_version(serial, package), "")
        rows.append(("Headset package", package or FAILED))
        rows.append(("Headset client", client or FAILED))
        match = wapk.versions_match(client, server)
        rows.append(("Versions match",
                     {True: "ja", False: "NEIN — Client und Server passen nicht zusammen",
                      None: "unbekannt"}[match]))
    else:
        rows.append(("Headset", "nicht per adb erreichbar"))
    return rows


def adb_rows():
    import adb_doctor as adbdoc

    rows = [
        ("adb client", _safe(adbdoc.client_version, "-")),
        ("android-tools", _safe(adbdoc.package_version, "-")),
    ]
    state = _safe(lambda: adbdoc.probe().get("state"), "")
    rows.append(("adb state", state or FAILED))
    updated = _safe(adbdoc.updated_since_success, None)
    if updated and updated != FAILED:
        rows.append(("Updated since OK", f"{updated[0]} -> {updated[1]}"))
    return rows


def network_rows():
    import firewall as fw

    rows = [("Firewall", _safe(lambda: fw.detect().get("kind"), "-"))]
    for key in ("net.core.rmem_max", "net.core.wmem_max"):
        rows.append((key, _safe(lambda k=key: _sysctl(k), "-")))
    return rows


def _sysctl(key):
    out = proc.output_of(["sysctl", "-n", key], timeout=proc.DEFAULT_TIMEOUT)
    return (out or "").strip()


# --------------------------------------------------------------------------- #
#  Der Bericht
# --------------------------------------------------------------------------- #
def build_report(app_version=UNKNOWN, log_tail=""):
    """
    Der vollstaendige Bericht als Text.

    ``log_tail`` wird angehaengt, wenn es mitgegeben wird — die Logdatei
    selbst zu lesen ist nicht Aufgabe dieses Moduls, das macht der Aufrufer
    mit ``read_log_tail()``.
    """
    lines = [
        "yakuda-connect diagnostics",
        "=" * 64,
        f"App version : {app_version}",
        f"Date        : {datetime.datetime.now().isoformat(timespec='seconds')}",
        "",
    ]

    for title, rows in (
        ("System", _safe(system_rows, [])),
        ("GPU", _safe(gpu_rows, [])),
        ("WiVRn", _safe(wivrn_rows, [])),
        ("adb", _safe(adb_rows, [])),
        ("Network", _safe(network_rows, [])),
    ):
        lines += _section(title, rows or [("-", FAILED)]) + [""]

    if log_tail:
        lines += ["-" * 64, "log tail:", "-" * 64, log_tail]

    return redact("\n".join(lines))
