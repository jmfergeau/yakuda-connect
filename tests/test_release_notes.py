#!/usr/bin/env python3
"""
tests/test_release_notes.py — Changelog & Highlights in der App
===============================================================
Zwei Arten von Pruefungen:

1. Der Sprachfilter (``core/release_notes.py``) — er entscheidet, was im
   Popup steht. Ein Fehler dort zeigt still die falsche Sprache oder schneidet
   Versionen ab, und niemand merkt es, weil der Dialog trotzdem Text hat.

2. Die ECHTEN Dateien. ``HIGHLIGHTS.md`` ist nur nuetzlich, solange sie mit
   dem Changelog Schritt haelt. Die Tests brechen deshalb ab, wenn ein
   Release einen Changelog-Block bekommt, aber keinen Highlights-Block, oder
   wenn ein Block eine Sprache vergisst. Ausserdem muessen beide Dateien in
   AUR-Paket und AppImage landen — sonst zeigt die App nur einen GitHub-Link.
"""
import os
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import release_notes  # noqa: E402
import version  # noqa: E402

SAMPLE = """# Titel

### 🚀 v2.0.0

#### 🇩🇪 Deutsch

* Neu auf Deutsch

---

#### 🇬🇧 English

* New in English

### 🚀 v1.9.0

#### 🇬🇧 English

* Only English here

### 🚀 v1.8.0

* No language headings at all
"""


# --------------------------------------------------------------------------- #
#  Sprachfilter
# --------------------------------------------------------------------------- #
def test_nur_deutsch():
    out = release_notes.filter_language(SAMPLE, "de")
    assert "Neu auf Deutsch" in out
    assert "New in English" not in out
    assert "Deutsch" not in out.replace("Neu auf Deutsch", "")   # Ueberschrift weg


def test_nur_englisch():
    out = release_notes.filter_language(SAMPLE, "en")
    assert "New in English" in out and "Neu auf Deutsch" not in out


def test_fehlende_sprache_faellt_auf_englisch_zurueck():
    out = release_notes.filter_language(SAMPLE, "de")
    assert "Only English here" in out


def test_block_ohne_sprachen_bleibt_ganz():
    for lang in ("de", "en", "fr"):
        assert "No language headings at all" in release_notes.filter_language(SAMPLE, lang)


def test_alle_versionen_und_titel_bleiben():
    out = release_notes.filter_language(SAMPLE, "de")
    assert out.startswith("# Titel")
    assert release_notes.versions(out) == ["2.0.0", "1.9.0", "1.8.0"]


def test_trennlinien_verschwinden():
    assert "---" not in release_notes.filter_language(SAMPLE, "de")


def test_fehlende_datei(tmp_path):
    assert release_notes.for_display("GIBTSNICHT.md", "de", root=str(tmp_path)) is None


# --------------------------------------------------------------------------- #
#  Die echten Dateien
# --------------------------------------------------------------------------- #
def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _blocks(text):
    """Version -> Text des Blocks."""
    parts = re.split(r"^(###\s.*)$", text, flags=re.M)
    blocks = {}
    for i in range(1, len(parts), 2):
        found = release_notes.versions(parts[i])
        if found:
            blocks[found[0]] = parts[i + 1]
    return blocks


@pytest.mark.parametrize("name", [release_notes.CHANGELOG, release_notes.HIGHLIGHTS])
def test_oberste_version_ist_die_aktuelle(name):
    assert release_notes.versions(_read(name))[0] == version.VERSION, (
        f"{name}: der oberste Block muss v{version.VERSION} sein")


def test_highlights_decken_jede_changelog_version_ab():
    changelog = release_notes.versions(_read(release_notes.CHANGELOG))
    highlights = release_notes.versions(_read(release_notes.HIGHLIGHTS))
    missing = [v for v in changelog if v not in highlights]
    assert not missing, f"HIGHLIGHTS.md fehlt fuer: {missing}"
    assert highlights == [v for v in changelog if v in highlights], "Reihenfolge weicht ab"


def test_jeder_highlights_block_hat_beide_sprachen():
    for ver, body in _blocks(_read(release_notes.HIGHLIGHTS)).items():
        assert "#### 🇩🇪 Deutsch" in body, f"v{ver}: Deutsch fehlt"
        assert "#### 🇬🇧 English" in body, f"v{ver}: English fehlt"


@pytest.mark.parametrize("name", [release_notes.CHANGELOG, release_notes.HIGHLIGHTS])
@pytest.mark.parametrize("lang, other", [("de", "#### 🇬🇧"), ("en", "#### 🇩🇪")])
def test_anzeige_der_echten_dateien(name, lang, other):
    out = release_notes.for_display(name, lang)
    assert out and f"v{version.VERSION}" in out
    assert other not in out


def test_jede_version_hat_ein_datum():
    """
    Jede Ueberschrift traegt ihr Veroeffentlichungsdatum (ISO, hinter der
    Version). Die Daten stammen aus den Git-Tags bzw. den GitHub-Releases;
    beim Anlegen einer neuen Version wird das gern vergessen.
    """
    for name in (release_notes.CHANGELOG, release_notes.HIGHLIGHTS):
        for line in _read(name).splitlines():
            if line.startswith("### "):
                assert re.search(r" — \d{4}-\d{2}-\d{2}$", line), f"{name}: {line}"


def test_changelog_und_highlights_nennen_dasselbe_datum():
    def dates(name):
        return {m.group(1): m.group(2) for m in
                re.finditer(r"^###\s.*?\bv?(\d+\.\d+\.\d+).*? — (\d{4}-\d{2}-\d{2})$",
                            _read(name), re.M)}
    changelog, highlights = dates(release_notes.CHANGELOG), dates(release_notes.HIGHLIGHTS)
    shared = set(changelog) & set(highlights)
    assert shared
    abweichend = {v: (changelog[v], highlights[v]) for v in shared
                  if changelog[v] != highlights[v]}
    assert not abweichend, abweichend


def test_highlights_ohne_relative_links():
    # QTextBrowser wuerde einen relativen Link intern "oeffnen" und eine leere
    # Seite zeigen.
    assert not re.search(r"\]\((?!https?://)", _read(release_notes.HIGHLIGHTS))


def test_dateien_werden_mitgeliefert():
    pkgbuild = _read("packaging/aur/PKGBUILD")
    appimage = _read("build_appimage.sh")
    for name in (release_notes.CHANGELOG, release_notes.HIGHLIGHTS):
        assert re.search(rf"for item in .*\b{re.escape(name)}\b", pkgbuild), f"PKGBUILD ohne {name}"
        assert re.search(rf"^cp .*\b{re.escape(name)}\b", appimage, re.M), f"AppImage ohne {name}"


# --------------------------------------------------------------------------- #
#  Dialog
# --------------------------------------------------------------------------- #
def test_dialog_zeigt_datei_und_oeffnet_nur_einmal(qapp):
    from ui.release_notes_dialog import show_release_notes, _open
    dlg = show_release_notes(None, release_notes.HIGHLIGHTS, "Highlights")
    try:
        assert f"v{version.VERSION}" in dlg.browser.toPlainText()
        assert show_release_notes(None, release_notes.HIGHLIGHTS, "Highlights") is dlg
    finally:
        dlg.close()
    assert release_notes.HIGHLIGHTS not in _open


def test_dialog_ohne_datei_zeigt_github_link(qapp, monkeypatch):
    from ui import release_notes_dialog
    monkeypatch.setattr(release_notes, "for_display", lambda *a, **k: None)
    dlg = release_notes_dialog.show_release_notes(None, "FEHLT.md", "x")
    try:
        assert "FEHLT.md" in dlg.browser.toPlainText()
        assert "github.com" in dlg.lbl_hint.text()
    finally:
        dlg.close()
