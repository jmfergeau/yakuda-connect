#!/usr/bin/env python3
"""
core/proton_manual_install.py — Download manuell verteilter Proton-Builds
=========================================================================
Nicht jede empfohlene Proton-Version kommt ueber die AUR oder ProtonPlus.
Proton-RTSP-Wayland-GE zum Beispiel gibt es ausschliesslich als Tarball am
GitHub-Release. Dieser Worker holt so einen Build, prueft ihn und entpackt
ihn nach compatibilitytools.d.

Drei Dinge, die hier bewusst so geloest sind:

1. Es wird das BINARY-Archiv geladen, nicht das "-source"-Archiv. Projekte
   unter LGPL legen ihren Releases ein Corresponding-Source-Archiv bei; das
   enthaelt nur vorbereiteten Quellcode und keine compatibilitytool.vdf.
   Entpackt landet es zwar im Ordner, Steam zeigt aber nichts an.
   _reject_source_archive() faengt eine falsch gepflegte URL deshalb ab,
   bevor ueberhaupt geladen wird.

2. Das Zielverzeichnis kommt aus games.compat_tools_install_dir() und ist
   nicht hartkodiert. Ein fest eingetragener Flatpak-Pfad wuerde bei nativem
   Steam — dem Normalfall auf Arch/CachyOS — ins Leere entpacken.

3. Die .sha512sum wird geprueft, wenn das Release eine mitliefert. Der
   Download wird anschliessend als Proton-Runtime ausgefuehrt; ein abgebrochener
   Transfer soll nicht als halbes Compat-Tool liegenbleiben.
"""
import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.request

from PySide6.QtCore import QThread, Signal as QtSignal

import games as games_db
from logging_setup import get_logger

log = get_logger("proton_manual")

_UA = {"User-Agent": "yakuda-connect"}
_CHUNK = 256 * 1024


def _reject_source_archive(url):
    """True, wenn die URL auf ein Corresponding-Source-Archiv zeigt."""
    name = os.path.basename(url).lower()
    return "-source.tar" in name or name.endswith("-source.tar.gz")


def _download(url, dest, progress=None):
    """Laedt url nach dest und meldet Fortschritt in Prozent (oder -1)."""
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(_CHUNK)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(int(done * 100 / total) if total else -1)
    return dest


def _expected_sha512(checksum_url):
    """Holt die erwartete Pruefsumme aus einer .sha512sum-Datei.

    Format ist das von sha512sum: '<hex>  <dateiname>'. Es wird nur das
    erste Feld gebraucht; der Dateiname darin kann vom heruntergeladenen
    Namen abweichen, wenn der Maintainer umbenannt hat.
    """
    req = urllib.request.Request(checksum_url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read(8192).decode("utf-8", "ignore")
    for line in text.splitlines():
        parts = line.split()
        if parts and len(parts[0]) == 128:
            return parts[0].lower()
    return None


def _sha512_of(path):
    h = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _tool_root(tar):
    """Der gemeinsame oberste Ordner im Archiv, oder None.

    Ein Proton-Tarball entpackt sich normalerweise in genau ein Verzeichnis.
    Faellt das weg (Dateien liegen flach im Archiv), wird spaeter ein eigener
    Ordner angelegt, sonst kippt der Inhalt direkt in compatibilitytools.d.
    """
    roots = set()
    for member in tar.getmembers():
        head = member.name.split("/", 1)[0]
        if head in ("", ".", ".."):
            return None
        roots.add(head)
        if len(roots) > 1:
            return None
    return next(iter(roots), None)


def _safe_extract(tar, target):
    """Entpackt tar nach target und weist Pfade ausserhalb davon zurueck.

    Ein praeparierter Tarball kann mit '../' oder absoluten Pfaden Dateien
    ausserhalb des Zielordners schreiben. Python 3.12 bringt dafuer den
    'data'-Filter mit; auf aelteren Versionen wird von Hand geprueft.
    """
    try:
        tar.extractall(target, filter="data")
        return
    except TypeError:
        pass
    base = os.path.realpath(target)
    for member in tar.getmembers():
        dest = os.path.realpath(os.path.join(target, member.name))
        if not (dest == base or dest.startswith(base + os.sep)):
            raise RuntimeError(f"Archiv enthaelt unerlaubten Pfad: {member.name}")
    tar.extractall(target)


def install_manual_proton(proton, progress=None, status=None):
    """Laedt, prueft und entpackt einen manuell verteilten Proton-Build.

    Rueckgabe: (ok: bool, meldung_oder_ordnername: str)
    """
    url = proton.get("download_url")
    if not url:
        return False, "keine download_url hinterlegt"
    if _reject_source_archive(url):
        return False, ("die hinterlegte URL zeigt auf das Source-Archiv "
                       "(-source.tar.gz), nicht auf den Build")

    target_dir = games_db.compat_tools_install_dir()
    if not target_dir:
        return False, "kein Steam-Verzeichnis fuer compatibilitytools.d gefunden"

    tmp = tempfile.mkdtemp(prefix="yakuda-proton-")
    archive = os.path.join(tmp, os.path.basename(url))
    try:
        if status:
            status("download")
        _download(url, archive, progress)

        checksum_url = proton.get("checksum_url")
        if checksum_url:
            if status:
                status("verify")
            try:
                expected = _expected_sha512(checksum_url)
            except Exception as exc:
                log.warning("Pruefsumme nicht abrufbar — %s", exc)
                expected = None
            if expected:
                actual = _sha512_of(archive)
                if actual != expected:
                    return False, "Pruefsumme stimmt nicht — Download verworfen"

        if status:
            status("extract")
        with tarfile.open(archive) as tar:
            root = _tool_root(tar)
            if root:
                dest = os.path.join(target_dir, root)
                # Eine aeltere Installation desselben Builds wird ersetzt.
                # Druebergepackt entstuenden sonst Mischordner aus zwei
                # Versionen, die niemand mehr auseinanderhaelt.
                if os.path.isdir(dest):
                    shutil.rmtree(dest)
                _safe_extract(tar, target_dir)
            else:
                root = os.path.splitext(os.path.splitext(
                    os.path.basename(url))[0])[0]
                dest = os.path.join(target_dir, root)
                if os.path.isdir(dest):
                    shutil.rmtree(dest)
                os.makedirs(dest, exist_ok=True)
                _safe_extract(tar, dest)

        vdf = os.path.join(target_dir, root, "compatibilitytool.vdf")
        if not os.path.isfile(vdf):
            return False, ("entpackt, aber ohne compatibilitytool.vdf — "
                           "Steam wird den Build nicht anzeigen")
        return True, root
    except Exception as exc:
        log.warning("install_manual_proton: %s", exc)
        return False, str(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class ManualProtonInstallWorker(QThread):
    """Haelt den Download aus dem GUI-Thread heraus."""
    progress_signal = QtSignal(int)
    status_signal = QtSignal(str)
    finished_signal = QtSignal(bool, str)

    def __init__(self, proton):
        super().__init__()
        self.proton = proton

    def run(self):
        ok, msg = install_manual_proton(
            self.proton,
            progress=self.progress_signal.emit,
            status=self.status_signal.emit,
        )
        self.finished_signal.emit(ok, msg)
