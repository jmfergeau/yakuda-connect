#!/usr/bin/env python3
"""
core/release_notes.py — CHANGELOG.md und HIGHLIGHTS.md in der App anzeigen
=========================================================================
Einstellungen → Allgemein & Updates → „Changelog" / „Highlights".

Zwei Dateien, zwei Leserschaften:

  * ``CHANGELOG.md``  — alles, mit Modulnamen und Begruendungen. Fuer Leute,
    die wissen wollen, WARUM sich etwas geaendert hat.
  * ``HIGHLIGHTS.md`` — pro Version eine Handvoll Saetze ohne Fachbegriffe.
    Fuer alle, die nur wissen wollen, was neu ist.

Beide sind gleich aufgebaut::

    ### 🚀 v1.3.0
    #### 🇩🇪 Deutsch
    ...
    #### 🇬🇧 English
    ...

Angezeigt wird nur die Sprache der Oberflaeche. Beide Sprachen untereinander
verdoppeln die Laenge, und die Haelfte davon ist fuer den Leser nutzlos. Fehlt
eine Sprache in einem Block, wird Englisch gezeigt, und gibt es gar keine
Sprach-Ueberschriften, der ganze Block — lieber zu viel als nichts.

Die Dateien liegen im Programmordner neben ``core/``. PKGBUILD und
build_appimage.sh muessen sie deshalb mitkopieren (Test in
tests/test_release_notes.py).

Bewusst ohne Qt: dadurch in Tests ohne Fenster pruefbar.
"""
import os
import re

from logging_setup import get_logger

log = get_logger("release_notes")

CHANGELOG = "CHANGELOG.md"
HIGHLIGHTS = "HIGHLIGHTS.md"

# Fallback, wenn die Datei im Paket fehlt (z. B. selbst gebaut ohne sie).
GITHUB_BASE = "https://github.com/yakuda-stack/yakuda-connect/blob/main/"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_VERSION_RE = re.compile(r"^###\s")
_LANG_RE = re.compile(r"^####\s")

# Welche Sprach-Ueberschrift zu welchem Sprachcode gehoert. Erkannt wird am
# Wort UND an der Flagge, damit ein Tippfehler im einen nicht alles kippt.
_LANG_MARKERS = {
    "de": ("deutsch", "🇩🇪"),
    "en": ("english", "🇬🇧"),
}


def path_of(name, root=ROOT):
    return os.path.join(root, name)


def load(name, root=ROOT):
    """Dateiinhalt oder None, wenn sie fehlt oder nicht lesbar ist."""
    try:
        with open(path_of(name, root), encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        log.warning("%s nicht lesbar: %s", name, exc)
        return None


def _lang_of(heading):
    low = heading.lower()
    for code, markers in _LANG_MARKERS.items():
        if any(m in low for m in markers):
            return code
    return None


def filter_language(text, lang):
    """
    Nur die Abschnitte einer Sprache behalten.

    Alles vor der ersten Versions-Ueberschrift (Dateititel) bleibt stehen.
    Die Sprach-Ueberschriften selbst fallen weg — sie sagen nur noch, was man
    ohnehin gerade liest.
    """
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines) and not _VERSION_RE.match(lines[i]):
        out.append(lines[i])
        i += 1

    while i < len(lines):
        heading = lines[i]
        i += 1
        preamble, sections, current = [], {}, None
        while i < len(lines) and not _VERSION_RE.match(lines[i]):
            line = lines[i]
            i += 1
            if _LANG_RE.match(line):
                current = _lang_of(line) or "?"
                sections.setdefault(current, [])
                continue
            if current is None:
                preamble.append(line)
            else:
                sections[current].append(line)

        out.append(heading)
        out.extend(preamble)
        if not sections:
            continue
        chosen = sections.get(lang) or sections.get("en")
        if chosen is None:                     # unbekannte Sprachen → alles
            chosen = [ln for block in sections.values() for ln in block]
        out.extend(chosen)
    return "\n".join(_strip_rules(out)).strip() + "\n"


def _strip_rules(lines):
    """
    Trennlinien (---) am Ende eines Sprachabschnitts entfernen.

    Im Changelog steht zwischen Deutsch und Englisch ein ``---``. Ohne den
    englischen Teil landet es direkt vor der naechsten Version und ergibt
    eine doppelte Linie. Mehrere Leerzeilen hintereinander werden
    zusammengefasst.
    """
    result = []
    for line in lines:
        if line.strip() == "---":
            continue
        if not line.strip() and result and not result[-1].strip():
            continue
        result.append(line)
    return result


def for_display(name, lang, root=ROOT):
    """Markdown fuer den Dialog — oder None, wenn die Datei fehlt."""
    text = load(name, root)
    if text is None:
        return None
    return filter_language(text, lang)


def versions(text):
    """Versionsnummern in Dateireihenfolge (fuer Tests und Plausibilitaet)."""
    return re.findall(r"^###\s.*?\bv?(\d+\.\d+\.\d+(?:[-_][A-Za-z0-9]+)?)", text, re.M)
