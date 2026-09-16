#!/usr/bin/env python3
"""
ui/release_notes_dialog.py — Changelog / Highlights als Popup
============================================================
Nicht modal (``show`` statt ``exec``): wer beim Lesen auf eine neue Funktion
stoesst, soll sie im Hauptfenster suchen koennen, ohne den Text erst zu
schliessen. Pro Datei gibt es hoechstens EIN offenes Fenster — ein zweiter
Klick holt das vorhandene nach vorn, statt ein weiteres zu stapeln.

Farben kommen aus dem App-Stylesheet (ui_main.py: ``QDialog QTextEdit``
gilt auch fuer QTextBrowser), damit der Dialog jedes Thema mitmacht.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel,
                               QTextBrowser, QVBoxLayout)

import release_notes
from translations import get_language, tr

_open = {}      # Dateiname -> offener Dialog
_LINK = "#88c0d0"   # dieselbe Akzentfarbe wie die (ⓘ)-Symbole beim Hovern


class ReleaseNotesDialog(QDialog):
    def __init__(self, name, title, parent=None):
        super().__init__(parent)
        self.name = name
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(780, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(8)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        # Qts Standard-Linkblau ist auf den dunklen Themen kaum lesbar.
        self.browser.document().setDefaultStyleSheet(f"a {{ color:{_LINK}; }}")
        layout.addWidget(self.browser, 1)

        self.lbl_hint = QLabel()
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setTextFormat(Qt.RichText)
        self.lbl_hint.setOpenExternalLinks(True)
        self.lbl_hint.setStyleSheet("font-size:11px;")
        layout.addWidget(self.lbl_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText(tr("release_notes_close"))
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self.reload()

    def reload(self):
        """Inhalt in der aktuellen Sprache (neu) setzen."""
        text = release_notes.for_display(self.name, get_language())
        url = release_notes.GITHUB_BASE + self.name
        if text is None:
            self.browser.setPlainText(tr("release_notes_missing").format(name=self.name))
            self.lbl_hint.setText(f'<a style="color:{_LINK}" href="{url}">{url}</a>')
            return
        self.browser.setMarkdown(text)
        self.lbl_hint.setText(tr("release_notes_github").format(
            link=f'<a style="color:{_LINK}" href="{url}">GitHub</a>'))

    def closeEvent(self, event):
        _open.pop(self.name, None)
        super().closeEvent(event)


def show_release_notes(parent, name, title):
    """Oeffnet den Dialog oder holt den bereits offenen nach vorn."""
    dlg = _open.get(name)
    if dlg is None:
        dlg = ReleaseNotesDialog(name, title, parent)
        _open[name] = dlg
        dlg.show()
    else:
        dlg.reload()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
