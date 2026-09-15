#!/usr/bin/env python3
"""
core/wivrn_apk.py — Der WiVRn-Client auf dem Headset
====================================================
Alles, was mit der Android-Seite von WiVRn zu tun hat, an einer Stelle:
welches Release zum installierten Server passt, wie die APK heruntergeladen
wird, welches Paket auf der Brille liegt und wie der PC die Verbindung ueber
das Kabel ausloest.

Das Modul ist bewusst OHNE Qt geschrieben — es laesst sich damit ohne
laufende Anwendung testen, und die Worker-Threads in ``core/main.py``
benutzen es nur.

----------------------------------------------------------------------
Warum nicht einfach das neueste Release?
----------------------------------------------------------------------
Client und Server muessen ZUSAMMENPASSEN, nicht beide "neu" sein. Wer
``wivrn-server`` 25.12 aus dem AUR installiert hat und die APK eines
neueren Releases aufspielt, bekommt eine Brille, die sich nicht mehr
verbindet — und der Knopf in dieser App waere schuld gewesen.

Deshalb fragt ``pick_release()`` zuerst nach der Server-Version
(``wivrn-server --version``) und sucht das Release mit passendem Tag. Erst
wenn es keines gibt (oder der Server gar nicht installiert ist), faellt es
auf das neueste zurueck — und sagt das dem Aufrufer ueber ``matched``,
damit die Oberflaeche warnen kann, statt still etwas Falsches zu tun.

----------------------------------------------------------------------
Warum der Paketname nicht fest verdrahtet ist
----------------------------------------------------------------------
WiVRn heisst auf dem Headset je nach Herkunft anders:

    org.meumeu.wivrn                  aus dem Meta Horizon Store
    org.meumeu.wivrn.github           Release von GitHub
    org.meumeu.wivrn.github.nighly    Nightly (Schreibfehler ist upstream so)
    org.meumeu.wivrn.github.testing   CI-Build
    org.meumeu.wivrn.local            selbst gebaut

Ein fest eingetragener Name geht damit bei der Haelfte der Nutzer ins
Leere. ``detect_package()`` fragt stattdessen die Brille selbst
(``pm list packages``).

----------------------------------------------------------------------
Wie "Verbinden" funktioniert
----------------------------------------------------------------------
Ueber WLAN kann der PC gar nicht verbinden — da klickt immer die Brille.
Der einzige Weg, bei dem der PC die Verbindung ausloest, ist USB, und der
laeuft genau so, wie es WiVRns Dokumentation beschreibt:

    adb reverse tcp:9757 tcp:9757
    adb shell am start -a android.intent.action.VIEW \\
        -d "wivrn+tcp://localhost" <paket>

``reverse``, nicht ``forward``: die Brille oeffnet ``localhost:9757``, und
der Tunnel muss zum PC zeigen.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

import proc
import usb_headsets as usbhs
from logging_setup import get_logger
from paths import cache_root

log = get_logger("wivrn_apk")

# --------------------------------------------------------------------------- #
#  Konstanten
# --------------------------------------------------------------------------- #
RELEASES_API = "https://api.github.com/repos/WiVRn/WiVRn/releases?per_page=30"
APK_REPO_URL = "https://github.com/WiVRn/WiVRn-APK/releases"
USER_AGENT = "yakuda-connect"

# Die APK-Assets der Releases heissen "org.meumeu.wivrn-release.apk".
ASSET_SUFFIX = "-release.apk"

# WiVRns Port. Steht auch in core/firewall.py — hier noch einmal, weil der
# adb-Tunnel ihn braucht und das Modul sonst nichts von der Firewall wissen
# muss.
WIVRN_PORT = 9757

# Reihenfolge = Vorzug, falls mehrere Varianten auf der Brille liegen.
# Der Store-Build zuerst: er ist der wahrscheinlichste "richtige".
PACKAGE_CANDIDATES = [
    "org.meumeu.wivrn",
    "org.meumeu.wivrn.github",
    "org.meumeu.wivrn.github.nighly",
    "org.meumeu.wivrn.github.testing",
    "org.meumeu.wivrn.local",
]

# GitHub erlaubt ohne Token 60 Anfragen pro Stunde. Die Release-Liste
# aendert sich hoechstens woechentlich — ein Cache von 15 Minuten reicht
# voellig und verhindert, dass ein paar Klicks das Kontingent aufbrauchen.
_CACHE_TTL = 900
_cache = {"time": 0.0, "data": None}


# --------------------------------------------------------------------------- #
#  Versionen vergleichen
# --------------------------------------------------------------------------- #
def version_key(text):
    """
    "v25.12", "WiVRn version 26.1.2", "25.12-30-gabc123" -> (25, 12) bzw.
    (26, 1, 2). ``None``, wenn keine Zahlen drinstehen.

    Bewusst nur die FUEHRENDEN Zahlen: ein git-describe-Suffix haengt hinten
    dran und darf den Vergleich nicht kippen.
    """
    if not text:
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text))
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def versions_match(a, b):
    """
    Passen zwei Versionen zusammen? Verglichen werden die ersten beiden
    Stellen — WiVRn nummeriert nach Jahr.Monat, und eine Patch-Nummer
    dahinter bricht die Kompatibilitaet nicht.

    Ist eine der beiden unbekannt, gibt es ``None`` ("weiss nicht") statt
    ``False``. Der Aufrufer soll dann nicht warnen, sondern schweigen.
    """
    ka, kb = version_key(a), version_key(b)
    if not ka or not kb:
        return None
    return ka[:2] == kb[:2]


def server_version():
    """
    Version des installierten ``wivrn-server`` als Text ("25.12") oder "".

    Liegt schon als Tupel in ``vr_environment.wivrn_version()`` vor; der
    Import passiert hier drinnen, damit dieses Modul ohne die grosse
    Umgebungslogik importierbar bleibt.
    """
    try:
        from vr_environment import wivrn_version
        version = wivrn_version()
    except Exception as exc:            # Modul fehlt, Binary fehlt, egal
        log.debug("Server-Version nicht ermittelbar: %s", exc)
        return ""
    if not version:
        return ""
    return ".".join(str(part) for part in version)


# --------------------------------------------------------------------------- #
#  Releases von GitHub
# --------------------------------------------------------------------------- #
def _api_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def list_releases(timeout=10, force=False):
    """
    Die Releases von WiVRn/WiVRn, neuestes zuerst. Leere Liste bei
    Netzfehler — der Aufrufer meldet das als "kein Release gefunden".

    Ergebnis wird 15 Minuten zwischengespeichert (siehe ``_CACHE_TTL``).
    """
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["time"] < _CACHE_TTL:
        return _cache["data"]

    try:
        data = _api_get(RELEASES_API, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # 403 ist hier fast immer das Anfragelimit, nicht "verboten".
        log.warning("GitHub-Release-Liste nicht abrufbar (HTTP %s)", exc.code)
        return _cache["data"] or []
    except Exception as exc:
        log.warning("GitHub-Release-Liste nicht abrufbar: %s", exc)
        return _cache["data"] or []

    if not isinstance(data, list):
        return _cache["data"] or []

    releases = [r for r in data if not r.get("draft")]
    _cache["data"] = releases
    _cache["time"] = now
    return releases


def asset_for(release):
    """
    (Name, URL, Groesse) des APK-Assets eines Releases — oder ``None``.

    Der ``source``-Filter ist Absicht: manche Releases legen zusaetzlich ein
    Quellarchiv bei, dessen Name ebenfalls auf die Endung passen kann.
    """
    for asset in (release or {}).get("assets", []):
        name = asset.get("name", "")
        if name.endswith(ASSET_SUFFIX) and "source" not in name.lower():
            return name, asset.get("browser_download_url", ""), asset.get("size", 0)
    return None


def pick_release(server=None, timeout=10):
    """
    Das Release, dessen APK zum installierten Server passt.

    Rueckgabe: ``(release, matched, server_text)``

      release      das GitHub-Release-Dict oder ``None``
      matched      True  = Tag passt zur Server-Version
                   False = nichts Passendes gefunden, es ist ein Rueckfall
                   None  = Server-Version unbekannt, also nichts zu pruefen
      server_text  die ermittelte Server-Version ("" wenn unbekannt)

    ``matched is False`` ist der Fall, vor dem die Oberflaeche warnen muss:
    Es wird etwas installiert, das hoechstwahrscheinlich nicht verbindet.
    """
    server_text = server if server is not None else server_version()
    releases = [r for r in list_releases(timeout=timeout) if asset_for(r)]
    if not releases:
        return None, None, server_text

    stable = [r for r in releases if not r.get("prerelease")] or releases
    newest = stable[0]

    key = version_key(server_text)
    if not key:
        return newest, None, server_text

    for release in releases:
        if versions_match(release.get("tag_name"), server_text):
            return release, True, server_text

    return newest, False, server_text


def suggested_filename(release, asset_name):
    """
    Dateiname mit Versionsnummer: ``org.meumeu.wivrn-25.12-release.apk``.

    Upstream heissen alle Assets gleich. Drei Downloads im selben Ordner
    waeren sonst "…(1).apk" und "…(2).apk" — und niemand wuesste hinterher,
    welche zu welchem Server gehoert.
    """
    tag = str((release or {}).get("tag_name", "")).lstrip("v").strip()
    if not tag:
        return asset_name
    base, ext = os.path.splitext(asset_name)
    if tag in base:
        return asset_name
    return f"{base}-{tag}{ext}"


# --------------------------------------------------------------------------- #
#  Herunterladen
# --------------------------------------------------------------------------- #
# Eine APK ist ein ZIP-Archiv und beginnt entsprechend. Kommt stattdessen
# die HTML-Seite eines Captive Portals oder eine Fehlerseite zurueck, faellt
# das hier auf — und nicht erst beim "adb install", das dann eine
# nichtssagende Meldung liefert.
ZIP_MAGIC = b"PK\x03\x04"


class DownloadError(Exception):
    """Download fehlgeschlagen — Text ist ein fertiger Uebersetzungs-Key."""

    def __init__(self, key, **params):
        super().__init__(key)
        self.key = key
        self.params = params


class Cancelled(Exception):
    """Der Nutzer hat abgebrochen."""


def download(url, dest, progress=None, cancel=None, timeout=60):
    """
    Laedt ``url`` nach ``dest``.

    progress: ``callable(geladen_bytes, gesamt_bytes)`` — darf ``None`` sein
    cancel:   ``callable() -> bool``; gibt es True zurueck, wird abgebrochen

    Geschrieben wird nach ``dest + ".part"`` und erst am Ende umbenannt. Ein
    Abbruch (oder ein Absturz) hinterlaesst damit keine halbe Datei, die
    aussieht wie eine fertige — genau darauf faellt man beim zweiten Versuch
    sonst herein.
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    part = dest + ".part"
    first = True
    downloaded = 0

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length", 0) or 0)
            with open(part, "wb") as fh:
                while True:
                    if cancel and cancel():
                        raise Cancelled()
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    if first:
                        first = False
                        if not chunk.startswith(ZIP_MAGIC):
                            raise DownloadError("apk_status_bad_file")
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(downloaded, total)
    except (Cancelled, DownloadError):
        _remove_quietly(part)
        raise
    except Exception as exc:
        _remove_quietly(part)
        raise DownloadError("apk_status_error", err=str(exc))

    if downloaded == 0:
        _remove_quietly(part)
        raise DownloadError("apk_status_bad_file")

    os.replace(part, dest)
    return dest


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def cache_apk_path(name="wivrn-latest.apk"):
    """
    Ablageort im Zwischenspeicher.

    Ueber ``paths.cache_root()`` statt ``~/.cache`` von Hand: sonst wird
    ``XDG_CACHE_HOME`` ignoriert, und genau dafuer gibt es das Modul.
    """
    return os.path.join(cache_root(), name)


# --------------------------------------------------------------------------- #
#  Die Brille am Kabel
# --------------------------------------------------------------------------- #
def ready_serial():
    """
    Seriennummer der ersten per adb erreichbaren Brille — oder ``None``.

    Benutzt ``usb_headsets.adb_devices()`` statt einer zweiten eigenen
    Parserei: das dortige Verfahren kennt auch ``unauthorized`` und
    ``offline`` und haelt sie korrekt heraus.
    """
    for serial, state in sorted(usbhs.adb_devices().items()):
        if state == "device":
            return serial
    return None


def detect_packages(serial, timeout=proc.DEFAULT_TIMEOUT, attempts=1, cancel=None):
    """
    Alle WiVRn-Pakete, die die Brille meldet — best effort.

    Das Ergebnis ist ein HINWEIS, keine Wahrheit: eine leere Liste bedeutet
    "konnte nichts finden", nicht "nichts installiert" (siehe connect_usb).
    Deshalb werden mehrere Wege probiert, und ``\r`` aus der adb-Shell
    fliegt raus — Android schickt CRLF, und ein Paketname mit angehaengtem
    Wagenruecklauf passt auf keinen Vergleich.

    Standardmaessig ohne Wiederholungen: wer nur die Reihenfolge der
    Startversuche vorsortieren will, darf dafuer keine Minute warten.
    """
    if not serial:
        return []

    variants = (
        ["shell", "pm", "list", "packages"],
        ["shell", "pm", "list", "packages", "--user", "0"],
        ["shell", "cmd", "package", "list", "packages"],
    )
    for args in variants:
        res = adb(serial, args, timeout=timeout, attempts=attempts, cancel=cancel)
        if timed_out(res):
            # Haengt die USB-Seite, haengt sie fuer alle Varianten.
            return []
        found = []
        for line in (res.stdout or "").replace("\r", "").splitlines():
            name = line.strip()
            if name.startswith("package:"):
                name = name[len("package:"):].strip()
            # Bei "--user 0" haengt manchmal noch " uid:10123" dran.
            name = name.split()[0] if name else ""
            if name.startswith("org.meumeu.wivrn"):
                found.append(name)
        if found:
            return found
    return []


def detect_package(serial):
    """
    Der Paketname, mit dem gearbeitet wird — oder ``None``.

    Liegen mehrere Varianten auf der Brille (kommt vor: Store-Version plus
    selbst installierte APK), gewinnt die Reihenfolge aus
    ``PACKAGE_CANDIDATES``.
    """
    found = detect_packages(serial)
    if not found:
        return None
    for candidate in PACKAGE_CANDIDATES:
        if candidate in found:
            return candidate
    return found[0]


def client_version(serial, package):
    """
    Version der WiVRn-App auf der Brille ("25.12") oder "".

    ``dumpsys package`` liefert eine lange Auflistung; gesucht ist nur die
    Zeile ``versionName=…``.
    """
    if not serial or not package:
        return ""
    out = proc.output_of(["adb", "-s", serial, "shell", "dumpsys", "package", package],
                         timeout=proc.DEFAULT_TIMEOUT)
    m = re.search(r"versionName=(\S+)", out or "")
    return m.group(1).strip() if m else ""


# --------------------------------------------------------------------------- #
#  adb mit Geduld
# --------------------------------------------------------------------------- #
# Warum das hier noetig ist: die PICO 4 blockiert adb-Aufrufe, solange die
# USB-Schnittstelle im Dateiuebertragungs-Modus (MTP) verhakt ist. adb
# antwortet dann gar nicht — kein Fehler, keine Ausgabe, nur Stille bis zum
# Zeitlimit. Genau in diesem Moment loest der Nutzer es an der Brille selbst:
# in den USB-Optionen kurz auf "Nur Laden" und zurueck auf
# "Dateiuebertragung". Das setzt die Schnittstelle zurueck, und der naechste
# adb-Aufruf geht durch.
#
# Ein einzelner Aufruf mit hartem Zeitlimit trifft genau das Zeitfenster
# davor und meldet "timeout" — obwohl zwei Sekunden spaeter alles
# funktioniert haette. Deshalb wird jeder adb-Aufruf hier mehrfach versucht,
# und der Verbindungsablauf laeuft insgesamt als Polling-Schleife, die
# wartet, bis die Brille sich meldet.
ADB_TIMEOUT = 20          # ein einzelner Aufruf
ADB_ATTEMPTS = 3          # ... und so oft
ADB_RETRY_DELAY = 2.5     # Pause dazwischen, in Sekunden


def _sleep(seconds, cancel=None):
    """Unterbrechbare Pause — sonst haengt ein Abbruch bis zum Ende fest."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel and cancel():
            raise Cancelled()
        time.sleep(0.2)


def adb(serial, args, timeout=ADB_TIMEOUT, attempts=ADB_ATTEMPTS,
        delay=ADB_RETRY_DELAY, cancel=None, on_retry=None):
    """
    Ein adb-Aufruf, der Stille aushaelt.

    Wiederholt NUR bei Zeitueberschreitung. Ein echter Fehler ("device not
    found", "unable to resolve Intent") wird sofort zurueckgegeben — den
    dreimal zu wiederholen kostet nur Zeit und aendert nichts.

    ``on_retry(versuch, gesamt)`` wird vor jeder Wiederholung gerufen, damit
    die Oberflaeche "warte auf die Brille" anzeigen kann, statt stumm
    dazustehen.
    """
    result = None
    for attempt in range(1, attempts + 1):
        if cancel and cancel():
            raise Cancelled()
        result = proc.run(["adb", "-s", serial] + list(args),
                          timeout=timeout, check_log=False)
        if result.returncode != proc.RC_TIMEOUT:
            return result
        log.info("adb %s: Zeitlimit (Versuch %s/%s)", " ".join(args), attempt, attempts)
        if attempt < attempts:
            if on_retry:
                on_retry(attempt, attempts)
            _sleep(delay, cancel)
    return result


def timed_out(result):
    return result is not None and result.returncode == proc.RC_TIMEOUT


def retryable(detail):
    """
    Lohnt ein weiterer Anlauf?

    Ja bei Stille und bei einem Geraet, das gerade verschwindet oder
    wiederkommt — das ist genau der Umschaltvorgang an der Brille. Nein bei
    einer Aussage, die sich durch Warten nicht aendert: fehlt die App,
    fehlt sie auch in zehn Sekunden noch.
    """
    low = (detail or "").lower()
    if "unable to resolve intent" in low:
        return False
    if "more than one device" in low:
        return False
    return True


# --------------------------------------------------------------------------- #
#  Verbinden
# --------------------------------------------------------------------------- #
def open_tunnel(serial, port=WIVRN_PORT, cancel=None, on_retry=None):
    """``adb reverse`` mit Wiederholungen. ``(ok, detail)``."""
    res = adb(serial, ["reverse", f"tcp:{port}", f"tcp:{port}"],
              cancel=cancel, on_retry=on_retry)
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "adb reverse failed").strip()


def _start_client(serial, package, port, cancel=None, on_retry=None):
    """
    Ein Versuch, die App per Intent zu starten.

    Rueckgabe ``(zustand, ausgabe)`` mit zustand aus:
      ``"ok"``         gestartet (oder lief schon)
      ``"uncertain"``  Befehl abgesetzt, aber die adb-Shell kam nicht zurueck
      ``"failed"``     abgelehnt

    ``package=None`` laesst Android selbst entscheiden, wer ``wivrn+tcp://``
    beansprucht — das Schema ist eindeutig, also landet es bei WiVRn, egal
    unter welchem Paketnamen es installiert wurde.

    Zur Auswertung: ``am start`` meldet Fehler mit Exitcode 0 auf stdout.
    "Error: Activity not started, unable to resolve Intent" ist ein
    Fehlschlag — "Warning: Activity not started, intent has been delivered to
    currently running top activity" dagegen NICHT: die App lief schon und hat
    den Intent bekommen.

    Und der PICO-Fall: bleibt die Shell nach dem Absetzen stumm, ist der
    Intent trotzdem in aller Regel angekommen — ``am`` uebergibt ihn an den
    ActivityManager und das Geraet startet WiVRn, waehrend die USB-Seite noch
    klemmt und kein Dateiende schickt. Das als Fehlschlag zu werten hiesse,
    den Nutzer vor eine Fehlermeldung zu setzen, waehrend die Brille bereits
    verbindet. Deshalb ``uncertain`` statt ``failed``.
    """
    args = ["shell", "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", "wivrn+tcp://localhost"]
    if package:
        args.append(package)

    res = adb(serial, args, cancel=cancel, on_retry=on_retry)
    text = f"{res.stdout or ''}\n{res.stderr or ''}".strip()

    if timed_out(res):
        return "uncertain", text or "timeout"
    if res.returncode != 0 or "Error:" in text:
        return "failed", text or "am start failed"
    return "ok", text


def connect_usb(serial, package=None, port=WIVRN_PORT, cancel=None, on_retry=None):
    """
    Startet die Verbindung ueber das Kabel — wie WiVRns eigenes Dashboard.

    Rueckgabe: Dict mit ``ok``, ``package``, ``detail``, ``uncertain``.

    ----------------------------------------------------------------------
    Warum hier NICHT vorher geprueft wird, ob die App installiert ist
    ----------------------------------------------------------------------
    Genau daran ist die erste Fassung gescheitert: sie hat ``pm list
    packages`` befragt, nichts Passendes gefunden und mit "keine WiVRn-App
    auf der Brille" abgebrochen — waehrend das offizielle Dashboard auf
    derselben Brille einwandfrei verbunden hat. Es fragt schlicht nicht: es
    legt den Tunnel und schickt den Intent los.

    Deshalb ist die Erkennung nur noch ein VORSCHLAG fuer die Reihenfolge:
    probiert wird, was die Brille gemeldet hat, danach die bekannten
    Paketnamen, und zuletzt ohne Paketangabe. Erst wenn all das scheitert,
    ist die App wirklich nicht da.

    Die Erkennung bekommt dabei ein kurzes Zeitlimit und KEINE
    Wiederholungen: sie ist nur eine Abkuerzung. Haengt die USB-Seite, soll
    sie den eigentlichen Verbindungsversuch nicht um eine Minute verzoegern.
    """
    result = {"ok": False, "package": "", "detail": "", "uncertain": False}
    if not serial:
        result["detail"] = "no device"
        return result

    ok, detail = open_tunnel(serial, port, cancel=cancel, on_retry=on_retry)
    if not ok:
        result["detail"] = detail
        return result

    order = []
    if package:
        order.append(package)
    detected = detect_packages(serial, timeout=8, attempts=1, cancel=cancel)
    order += [p for p in PACKAGE_CANDIDATES if p in detected]
    order += [p for p in detected if p not in order]
    order += [p for p in PACKAGE_CANDIDATES if p not in order]
    order.append(None)          # letzter Versuch ohne Paketangabe

    detail = ""
    for candidate in order:
        state, detail = _start_client(serial, candidate, port,
                                      cancel=cancel, on_retry=on_retry)
        if state == "ok":
            return {"ok": True, "package": candidate or "",
                    "detail": "", "uncertain": False}
        if state == "uncertain":
            # Stumme Shell: der Intent ist raus. Nicht weiter durch die
            # Kandidatenliste rennen — jeder weitere Versuch wuerde erneut
            # ins Zeitlimit laufen und die Brille zusaetzlich beschaeftigen.
            return {"ok": True, "package": candidate or "",
                    "detail": detail, "uncertain": True}

    result["detail"] = detail
    return result


def disconnect_usb(serial, port=WIVRN_PORT):
    """
    Raeumt den adb-Tunnel wieder ab. Fehler sind hier egal: ist er schon
    weg, ist das Ziel erreicht.
    """
    if not serial:
        return
    proc.run(["adb", "-s", serial, "reverse", "--remove", f"tcp:{port}"],
             timeout=proc.DEFAULT_TIMEOUT, check_log=False)
