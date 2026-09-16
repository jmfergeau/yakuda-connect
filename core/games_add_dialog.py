#!/usr/bin/env python3
"""
games_add_dialog.py — "Spiel hinzufügen" (Games-Tab)
====================================================
Der Scanner richtet sich nach Steams eigener VR-Kennzeichnung (siehe
steam_appinfo.py). Das ist die mit Abstand verlässlichste Quelle, aber sie
ist nicht lückenlos: Beta-Zweige, Spiele mit nachgerüstetem VR-Modus,
Mod-Loader und alles außerhalb von Steam stehen dort nicht.

Diesen Rest über die Erkennung abfangen zu wollen, hieße raten — und jedes
Raten holt Flachbildschirm-Spiele mit in die Liste. Deshalb bleibt die
Erkennung streng, und dieser Dialog ist das Ventil daneben:

  * **Steam-Spiel hinzufügen** — Auswahl über ALLE installierten
    Steam-Spiele, unabhängig von der VR-Kennzeichnung. Der Eintrag wird
    dauerhaft gemerkt und überlebt jeden Neuscan.
  * **Eigenes Spiel hinzufügen** — Name, Programmdatei und optionale
    Startparameter für alles, was nicht über Steam läuft (itch.io, GOG,
    AppImage, selbst gebaute Builds).

Der Dialog bleibt nach dem Hinzufügen OFFEN. Wer aus einem Ordner mehrere
Titel einträgt, müsste ihn sonst jedes Mal neu öffnen und die Steam-Liste
neu laden lassen.
"""

import os

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QComboBox, QFrame,
                               QFileDialog, QCompleter)
from PySide6.QtCore import Qt, QThread, Signal as QtSignal

import games as games_db
from translations import tr

from logging_setup import get_logger

log = get_logger("games_add")


# Nord-Palette wie im übrigen Games-Tab. Bewusst hier wiederholt und nicht
# aus dem Tab importiert: der Dialog soll auch dann bauen, wenn jemand den
# Tab umbaut.
_CSS_PRIMARY = """
    QPushButton { background-color:#5e81ac; color:white; border:none;
                  font-weight:bold; padding:6px 14px; border-radius:4px; font-size:11px; }
    QPushButton:hover { background-color:#81a1c1; }
    QPushButton:disabled { background-color:#3b4252; color:#7b88a1; }
"""
_CSS_SECONDARY = """
    QPushButton { background-color:#2e3440; color:#d8dee9; border:1px solid #4c566a;
                  padding:6px 12px; border-radius:4px; font-size:11px; }
    QPushButton:hover { background-color:#3b4252; border-color:#5e81ac; }
"""
_CSS_INPUT = "font-size:11px; padding:4px;"
_CSS_LABEL = "color:#7b88a1; font-size:11px; font-weight:bold; border:none;"
_CSS_DESC = "color:#7b88a1; font-size:10px; border:none;"


class SteamGamesWorker(QThread):
    """Liest die Liste aller installierten Steam-Spiele im Hintergrund.

    Beim allerersten Aufruf muss dafür Steams appinfo.vdf gelesen werden —
    je nach Größe der Bibliothek über hundert Megabyte. Im GUI-Faden würde
    der Dialog währenddessen als graues Rechteck dastehen.
    """
    result_signal = QtSignal(object)      # [{"appid", "name"}, ...]

    def run(self):
        try:
            games = games_db.scan_all_steam_games()
        except Exception as exc:
            log.warning("[Games] Steam-Liste konnte nicht gelesen werden: %s", exc)
            games = []
        self.result_signal.emit(games)


class AddGameDialog(QDialog):
    """Zweispaltiges Fenster: links Steam-Spiel, rechts eigenes Spiel."""

    #: Wird nach JEDEM erfolgreichen Eintrag ausgelöst — der Games-Tab baut
    #: seine Kacheln dann sofort neu, ohne dass man den Dialog schließen muss.
    game_added = QtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("games_add_title"))
        self.setMinimumWidth(720)
        self.setStyleSheet("QDialog { background-color:#282c34; } QLabel { color:#d8dee9; }")

        self._steam_worker = None
        self._steam_games = []
        #: True, sobald mindestens ein Eintrag entstanden ist. Der Aufrufer
        #: kann danach entscheiden, ob ein Neuaufbau der Kacheln nötig ist.
        self.changed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(self._build_steam_column(), 1)
        columns.addWidget(self._build_local_column(), 1)
        root.addLayout(columns)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color:#88c0d0; font-size:11px;")
        root.addWidget(self.lbl_status)

        foot = QHBoxLayout()
        foot.addStretch()
        self.btn_close = QPushButton(tr("games_add_close"))
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setStyleSheet(_CSS_SECONDARY)
        self.btn_close.clicked.connect(self.accept)
        foot.addWidget(self.btn_close)
        root.addLayout(foot)

        self._load_steam_games()

    # ------------------------------------------------------------------ #
    #  Aufbau
    # ------------------------------------------------------------------ #
    @staticmethod
    def _card(title_key, desc_key):
        card = QFrame()
        card.setObjectName("addcard")
        card.setStyleSheet("""
            QFrame#addcard { background-color:#21252b; border:1px solid #2e3440;
                             border-radius:6px; }
        """)
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)

        head = QLabel(tr(title_key))
        head.setStyleSheet("color:#eceff4; font-size:13px; font-weight:bold; border:none;")
        box.addWidget(head)

        desc = QLabel(tr(desc_key))
        desc.setWordWrap(True)
        desc.setStyleSheet(_CSS_DESC)
        box.addWidget(desc)
        return card, box

    def _build_steam_column(self):
        card, box = self._card("games_add_steam_head", "games_add_steam_desc")

        lbl = QLabel(tr("games_add_steam_label"))
        lbl.setStyleSheet(_CSS_LABEL)
        box.addWidget(lbl)

        # Editierbar + Vervollständigung: eine Bibliothek mit dreistelliger
        # Spielzahl ist über eine reine Klappliste nicht mehr bedienbar.
        # MatchContains statt MatchStartsWith, weil kaum jemand den Titel von
        # vorne tippt ("saber" soll "Beat Saber" finden).
        self.combo_steam = QComboBox()
        self.combo_steam.setEditable(True)
        self.combo_steam.setInsertPolicy(QComboBox.NoInsert)
        self.combo_steam.lineEdit().setPlaceholderText(tr("games_add_steam_placeholder"))
        self.combo_steam.setStyleSheet(_CSS_INPUT)
        self.combo_steam.setEnabled(False)
        completer = self.combo_steam.completer()
        if completer is not None:
            completer.setCompletionMode(QCompleter.PopupCompletion)
            completer.setFilterMode(Qt.MatchContains)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
        box.addWidget(self.combo_steam)

        self.lbl_steam_hint = QLabel(tr("games_add_steam_loading"))
        self.lbl_steam_hint.setWordWrap(True)
        self.lbl_steam_hint.setStyleSheet(_CSS_DESC)
        box.addWidget(self.lbl_steam_hint)

        row = QHBoxLayout()
        row.addStretch()
        self.btn_add_steam = QPushButton(tr("games_add_btn_add"))
        self.btn_add_steam.setCursor(Qt.PointingHandCursor)
        self.btn_add_steam.setStyleSheet(_CSS_PRIMARY)
        self.btn_add_steam.setEnabled(False)
        self.btn_add_steam.clicked.connect(self.add_steam_game)
        row.addWidget(self.btn_add_steam)
        box.addLayout(row)

        box.addStretch()
        return card

    def _build_local_column(self):
        card, box = self._card("games_add_local_head", "games_add_local_desc")

        lbl_name = QLabel(tr("games_add_name_label"))
        lbl_name.setStyleSheet(_CSS_LABEL)
        box.addWidget(lbl_name)

        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText(tr("games_add_name_placeholder"))
        self.txt_name.setStyleSheet(_CSS_INPUT)
        box.addWidget(self.txt_name)

        lbl_exe = QLabel(tr("games_add_exe_label"))
        lbl_exe.setStyleSheet(_CSS_LABEL)
        box.addWidget(lbl_exe)

        exe_row = QHBoxLayout()
        exe_row.setSpacing(6)
        self.txt_exe = QLineEdit()
        self.txt_exe.setPlaceholderText(tr("games_add_exe_placeholder"))
        self.txt_exe.setStyleSheet("font-family:monospace; font-size:11px; padding:4px;")
        exe_row.addWidget(self.txt_exe)

        self.btn_browse = QPushButton(tr("games_add_browse_btn"))
        self.btn_browse.setCursor(Qt.PointingHandCursor)
        self.btn_browse.setStyleSheet(_CSS_SECONDARY)
        self.btn_browse.clicked.connect(self.browse_executable)
        exe_row.addWidget(self.btn_browse)
        box.addLayout(exe_row)

        lbl_opts = QLabel(tr("games_add_opts_label"))
        lbl_opts.setStyleSheet(_CSS_LABEL)
        box.addWidget(lbl_opts)

        self.txt_opts = QLineEdit()
        self.txt_opts.setPlaceholderText(tr("games_add_opts_placeholder"))
        self.txt_opts.setStyleSheet("font-family:monospace; font-size:11px; padding:4px;")
        box.addWidget(self.txt_opts)

        row = QHBoxLayout()
        row.addStretch()
        self.btn_add_local = QPushButton(tr("games_add_btn_add"))
        self.btn_add_local.setCursor(Qt.PointingHandCursor)
        self.btn_add_local.setStyleSheet(_CSS_PRIMARY)
        self.btn_add_local.clicked.connect(self.add_local_game)
        row.addWidget(self.btn_add_local)
        box.addLayout(row)

        box.addStretch()
        return card

    # ------------------------------------------------------------------ #
    #  Steam-Liste
    # ------------------------------------------------------------------ #
    def _load_steam_games(self):
        self._steam_worker = SteamGamesWorker()
        self._steam_worker.result_signal.connect(self._on_steam_games)
        self._steam_worker.start()

    def _on_steam_games(self, games):
        self._steam_games = games or []
        self.combo_steam.clear()
        if not self._steam_games:
            self.lbl_steam_hint.setText(tr("games_add_steam_empty"))
            self.combo_steam.setEnabled(False)
            self.btn_add_steam.setEnabled(False)
            return

        already = set(games_db.load_manual_steam_appids())
        for game in self._steam_games:
            label = game["name"]
            if game["appid"] in already:
                # Schon von Hand eingetragen: sichtbar lassen, aber
                # kennzeichnen. Ausblenden wäre schlechter — der Nutzer
                # suchte das Spiel dann vergeblich und wüsste nicht, warum.
                label = f"{label}  {tr('games_add_steam_already')}"
            self.combo_steam.addItem(label, game["appid"])
        # Kein Vorschlag beim Öffnen: sonst trägt ein schneller Klick auf
        # "Hinzufügen" irgendein alphabetisch erstes Spiel ein.
        self.combo_steam.setCurrentIndex(-1)
        self.combo_steam.setEnabled(True)
        self.btn_add_steam.setEnabled(True)
        self.lbl_steam_hint.setText(
            tr("games_add_steam_count").format(n=len(self._steam_games)))

    def _selected_appid(self):
        """Die AppID der aktuellen Auswahl — auch bei getipptem Text."""
        idx = self.combo_steam.currentIndex()
        if idx >= 0:
            return self.combo_steam.itemData(idx)
        # Der Nutzer hat getippt, ohne aus der Liste zu wählen: den Namen
        # eindeutig zuordnen, sonst gilt die Eingabe als leer.
        typed = self.combo_steam.currentText().strip().lower()
        if not typed:
            return None
        hits = [g for g in self._steam_games if g["name"].strip().lower() == typed]
        return hits[0]["appid"] if len(hits) == 1 else None

    # ------------------------------------------------------------------ #
    #  Aktionen
    # ------------------------------------------------------------------ #
    def _status(self, text, color="#88c0d0"):
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f"color:{color}; font-size:11px;")

    def add_steam_game(self):
        appid = self._selected_appid()
        if not appid:
            self._status(tr("games_add_no_selection"), "#ebcb8b")
            return

        name = next((g["name"] for g in self._steam_games
                     if g["appid"] == appid), appid)
        if not games_db.add_manual_steam_appid(appid):
            self._status(tr("games_add_dup_steam").format(name=name), "#ebcb8b")
            return

        self.changed = True
        self._status(tr("games_add_ok_steam").format(name=name), "#a3be8c")
        self.game_added.emit()

    def browse_executable(self):
        """Dateiauswahl für die Programmdatei.

        Der Filter deckt ab, was auf einem Linux-Desktop tatsächlich
        vorkommt: Windows-Programme, Unity-Builds (*.x86_64/*.x86),
        AppImages und Start-Skripte. "Alle Dateien" steht daneben, weil
        native Binaries oft gar keine Endung haben.
        """
        start = os.path.dirname(self.txt_exe.text().strip()) or os.path.expanduser("~")
        path, _filter = QFileDialog.getOpenFileName(
            self, tr("games_add_file_dialog_title"), start,
            f"{tr('games_add_filter_games')} "
            "(*.exe *.sh *.AppImage *.appimage *.x86_64 *.x86 *.bin *.run);;"
            f"{tr('games_add_filter_all')} (*)")
        if path:
            self.txt_exe.setText(path)
            if not self.txt_name.text().strip():
                # Namensvorschlag aus dem Dateinamen — bei "BeatSaber.exe"
                # ist das schon die halbe Eingabe.
                self.txt_name.setText(os.path.splitext(os.path.basename(path))[0])

    def add_local_game(self):
        ok, result = games_db.add_local_game(
            self.txt_name.text(), self.txt_exe.text(), self.txt_opts.text())
        if not ok:
            self._status(tr(f"games_add_err_{result}"), "#bf616a")
            return

        name = self.txt_name.text().strip()
        self.changed = True
        self._status(tr("games_add_ok_local").format(name=name), "#a3be8c")
        # Felder leeren: der nächste Eintrag soll nicht die Reste des
        # vorigen erben (und stillschweigend dasselbe Spiel doppelt anlegen).
        self.txt_name.clear()
        self.txt_exe.clear()
        self.txt_opts.clear()
        self.game_added.emit()

    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        """Auf den Hintergrund-Faden warten, bevor das Fenster verschwindet.

        Ohne das kann der Worker sein Ergebnis an bereits abgeräumte Widgets
        liefern — Qt beendet den Prozess dann mit einem Absturz statt einer
        Fehlermeldung.
        """
        worker = self._steam_worker
        if worker is not None and worker.isRunning():
            worker.wait(3000)
        super().closeEvent(event)
