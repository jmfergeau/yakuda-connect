#!/usr/bin/env python3
"""
ui/opaque_combo.py — Aufklapplisten mit echtem Hintergrund
==========================================================
Unter KDE/Breeze (und einigen anderen Stilen) ist die Aufklappliste einer
QComboBox ein eigenes, halbdurchsichtiges Fenster. Mit einem dunklen
Stylesheet auf der Combo selbst bleibt dann nur der Text stehen — der Inhalt
dahinter scheint durch und die Liste ist kaum lesbar.

make_opaque(combo) schaltet die Durchsicht fuer diese eine Liste ab und gibt
ihr einen festen Hintergrund. Der Stil kann das Attribut beim Anzeigen erneut
setzen (Polish), deshalb wacht ein Ereignisfilter darueber.
"""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QListView

BG = "#1c1f26"
FG = "#d8dee9"
BORDER = "#3b4252"
SEL_BG = "#3b4252"
SEL_FG = "#88c0d0"

LIST_CSS = f"""
QListView {{ background:{BG}; color:{FG}; border:none; outline:none; padding:2px; }}
QListView::item {{ padding:5px 10px; min-height:18px; }}
QListView::item:hover {{ background:#2e3440; }}
QListView::item:selected {{ background:{SEL_BG}; color:{SEL_FG}; }}
"""


class _KeepOpaque(QObject):
    """Setzt die Deckkraft bei jedem Anzeigen/Polish erneut."""

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Show, QEvent.Polish, QEvent.StyleChange):
            _apply(obj)
        return False


def _apply(container):
    container.setAttribute(Qt.WA_TranslucentBackground, False)
    container.setAttribute(Qt.WA_NoSystemBackground, False)
    container.setAutoFillBackground(True)
    pal = container.palette()
    for role in (QPalette.Window, QPalette.Base):
        pal.setColor(role, QColor(BG))
    container.setPalette(pal)


def make_opaque(combo):
    """Aufklappliste dieser Combo deckend machen. Gibt die Combo zurueck."""
    view = QListView()
    view.setStyleSheet(LIST_CSS)
    view.setAutoFillBackground(True)
    combo.setView(view)
    container = view.parentWidget()          # QComboBoxPrivateContainer
    if container is not None:
        container.setStyleSheet(
            f"background:{BG}; border:1px solid {BORDER}; border-radius:4px;")
        _apply(container)
        guard = _KeepOpaque(container)
        container.installEventFilter(guard)
        container._opaque_guard = guard      # am Leben halten
    return combo


def is_opaque(combo):
    """Fuer Tests: ist die Liste deckend eingerichtet?"""
    container = combo.view().parentWidget()
    return (container is not None
            and not container.testAttribute(Qt.WA_TranslucentBackground)
            and container.autoFillBackground())
