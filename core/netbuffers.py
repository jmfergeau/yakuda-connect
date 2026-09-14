#!/usr/bin/env python3
"""
netbuffers.py — UDP-Puffergroessen fuer WiVRn anheben
=====================================================
WiVRn schiebt die Videoframes ueber UDP. Reicht der Socket-Puffer des
Kernels fuer einen Frame nicht aus, verwirft der Kernel Pakete, bevor
WiVRn sie ueberhaupt zu Gesicht bekommt — sichtbar als Ruckeln,
Makroblock-Artefakte oder kurze Aussetzer, besonders bei hoher Bitrate
und hoher Aufloesung.

Die Standardwerte vieler Distributionen liegen bei ein paar hundert
Kilobyte. Angehoben wird auf 25 MiB (26214400 Byte) — derselbe Wert, den
die WiVRn-Dokumentation nennt.

Zwei Wege, beide ueber EIN pkexec (eine Passwortabfrage):

  * Sitzung    — sysctl -w. Gilt bis zum naechsten Neustart, aendert
                 keine Datei. Zum Ausprobieren gedacht.
  * Dauerhaft  — schreibt /etc/sysctl.d/99-yakuda-connect-wivrn.conf UND
                 setzt die Werte sofort. Ueberlebt den Neustart.

Bewusste Entscheidungen:
  * Es werden nur die beiden Schluessel gesetzt, die WiVRn braucht.
    rmem_default/wmem_default bleiben unangetastet: die gelten fuer
    JEDEN Socket im System und nicht nur fuer den Videostrom.
  * Die dauerhafte Variante legt eine eigene Datei unter /etc/sysctl.d/
    an, statt /etc/sysctl.conf anzufassen. Eine eigene Datei laesst sich
    rueckstandslos loeschen; eine editierte Systemdatei nicht.
  * status() liest aus /proc und braucht dafuer keine Rechte.
"""
import os

import proc
from logging_setup import get_logger

log = get_logger("netbuffers")

# 25 MiB — der in der WiVRn-Dokumentation genannte Wert.
TARGET = 26214400

KEYS = ("net.core.rmem_max", "net.core.wmem_max")

SYSCTL_FILE = "/etc/sysctl.d/99-yakuda-connect-wivrn.conf"

FILE_CONTENT = (
    "# Angelegt von yakuda-connect.\n"
    "# Groessere UDP-Socket-Puffer fuer das WiVRn-Videostreaming.\n"
    "# Entfernen mit:  sudo rm " + SYSCTL_FILE + "\n"
    f"net.core.rmem_max = {TARGET}\n"
    f"net.core.wmem_max = {TARGET}\n"
)


def _read(key):
    """Aktueller Wert eines sysctl-Schluessels, oder None.

    Gelesen wird direkt aus /proc statt ueber den sysctl-Befehl: das
    braucht keine Rechte, kein Unterprozess und ist auf jedem Kernel da.
    """
    path = "/proc/sys/" + key.replace(".", "/")
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception as exc:
        log.debug("netbuffers._read(%s): %s", key, exc)
        return None


def current():
    """{schluessel: wert|None} der beiden relevanten Puffergroessen."""
    return {k: _read(k) for k in KEYS}


def is_applied():
    """Sind BEIDE Werte mindestens auf dem Zielwert?

    'mindestens', nicht 'genau': wer die Werte selbst hoeher gesetzt hat,
    soll nicht mit einem Knopf beglueckt werden, der sie wieder senkt.
    """
    vals = current()
    return all(v is not None and v >= TARGET for v in vals.values())


def is_persistent():
    """Existiert die sysctl.d-Datei? (Sagt nichts ueber den aktiven Wert.)"""
    return os.path.isfile(SYSCTL_FILE)


def _script(permanent):
    setvals = "sysctl -w " + " ".join(f"{k}={TARGET}" for k in KEYS)
    if not permanent:
        # Nur fuer diese Sitzung — keine Datei wird angefasst.
        return setvals
    # Heredoc mit zitiertem Marker: der Inhalt geht unveraendert in die
    # Datei, ohne dass die Shell darin etwas ersetzt. Der Marker steht
    # bewusst am Zeilenanfang, sonst endet das Heredoc nie.
    # 'cat > datei' statt 'printf "$(cat ...)"': letzteres laeuft durch eine
    # Kommandosubstitution, die den abschliessenden Zeilenumbruch schluckt.
    return (
        "set -e\n"
        f"cat > {SYSCTL_FILE} <<'YKEOF'\n"
        f"{FILE_CONTENT}"
        "YKEOF\n"
        f"chmod 0644 {SYSCTL_FILE}\n"
        f"{setvals}\n"
    )


def apply(permanent):
    """Hebt die Puffergroessen an. Rueckgabe: (ok, fehlertext).

    Laeuft ueber genau EIN pkexec, wie die Firewall-Einrichtung — der
    Nutzer sieht eine Passwortabfrage, nicht zwei.
    """
    res = proc.run(["pkexec", "sh", "-c", _script(permanent)],
                   timeout=proc.LONG_TIMEOUT)
    if res.returncode == 0:
        log.info("UDP-Puffer auf %s gesetzt (dauerhaft=%s)", TARGET, permanent)
        return True, ""

    err = (res.stderr or res.stdout or "").strip()
    # 126 = polkit-Dialog abgebrochen, 127 = pkexec nicht vorhanden
    if res.returncode == 126 and not err:
        err = "pkexec: cancelled"
    if res.returncode == 127 and not err:
        err = "pkexec: not found"
    log.warning("UDP-Puffer fehlgeschlagen (rc=%s): %s", res.returncode, err)
    return False, err or f"exit {res.returncode}"


def manual_commands(permanent):
    """Die Befehle zum Selbstausfuehren, falls pkexec fehlt oder scheitert."""
    if not permanent:
        return [f"sudo sysctl -w {k}={TARGET}" for k in KEYS]
    return [
        f"sudo tee {SYSCTL_FILE} > /dev/null <<'EOF'",
        FILE_CONTENT.rstrip("\n"),
        "EOF",
        f"sudo sysctl --system",
    ]
