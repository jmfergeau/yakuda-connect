#!/usr/bin/env python3
"""
tests/test_exit_guard.py — WiVRn-Server mit der App beenden
===========================================================
Abgedeckt ist, was sich OHNE echten Server pruefen laesst:

  * der Waechter (``watch``): EOF → aufraeumen, „bye" → nichts tun,
    nicht scharf → nichts tun, App tot obwohl die Pipe offen ist → aufraeumen
  * die Prozessgruppen-Pruefung: eine neu vergebene PID wird NICHT getroffen
  * ``ExitGuard`` startet erst dann einen Prozess, wenn es etwas zu bewachen
    gibt, und schickt keinen Stand doppelt
  * ``wivrn_server.stop_blocking`` eskaliert wie der Timer in main.py
  * closeEvent stoppt den Server nur mit Einstellung an
  * die Einstellungen stehen auf der Seite „Erweitert / System"

Bewusst NICHT hier: den Waechter wirklich starten und die App per SIGKILL
beenden. Er wuerde ueber /proc jeden Prozess namens ``wivrn-server`` treffen
— auf einem Entwicklerrechner also den echten Server. Dieser Weg wurde von
Hand mit einem Attrappen-Server geprueft (X, SIGTERM, pkill -f, SIGKILL,
Server der SIGTERM ignoriert, Einstellung aus).
"""
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))

import exit_guard  # noqa: E402
import wivrn_server  # noqa: E402


# --------------------------------------------------------------------------- #
#  Einstellung
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("settings, expected", [
    ({}, True),                                   # Standard AN
    ({"stop_server_with_app": False}, False),
    ({"stop_server_with_app": True}, True),
    ({"stop_server_with_app": "false"}, False),   # alte Text-Werte
    ({"stop_server_with_app": "1"}, True),
    (None, True),
])
def test_einstellung_standard_an(settings, expected):
    assert exit_guard.enabled_in(settings) is expected


def test_standard_steht_auch_in_config_manager():
    import config_manager
    assert config_manager.DEFAULT_SETTINGS[exit_guard.SETTING_KEY] is exit_guard.SETTING_DEFAULT


# --------------------------------------------------------------------------- #
#  Kill-Befehle
# --------------------------------------------------------------------------- #
def test_kill_befehle_laufen_und_leere_werden_uebersprungen(tmp_path):
    marker = tmp_path / "lief"
    exit_guard.run_kill_commands([
        {"label": "leer", "cmd": "   "},
        None,
        f"touch {marker}.str",
        {"label": "echt", "cmd": f"touch {marker}"},
    ])
    assert marker.exists()
    assert pathlib.Path(f"{marker}.str").exists()


# --------------------------------------------------------------------------- #
#  Prozessgruppen
# --------------------------------------------------------------------------- #
def test_startzeit_des_eigenen_prozesses():
    assert isinstance(exit_guard.process_starttime(os.getpid()), int)
    assert exit_guard.process_starttime(2 ** 30) is None


def test_neu_vergebene_pid_wird_nicht_getroffen(monkeypatch):
    monkeypatch.setattr(exit_guard, "process_starttime", lambda pid: 999)
    assert exit_guard.group_is_ours(4242, 999) is True      # derselbe Prozess
    assert exit_guard.group_is_ours(4242, 111) is False     # PID neu vergeben
    assert exit_guard.group_is_ours(4242, None) is False    # unbekannt → Finger weg


def test_gruppe_ohne_startprozess_gilt_als_unsere(monkeypatch):
    # Kein Prozess mit dieser PID: eine noch lebende Gruppe kann nicht neu
    # vergeben sein (Kernel haelt die PID als Gruppen-ID fest).
    monkeypatch.setattr(exit_guard, "process_starttime", lambda pid: None)
    assert exit_guard.group_is_ours(4242, 999) is True


def _living_members(pgid):
    """
    Nicht-Zombie-Prozesse einer Gruppe.

    Zombies zaehlen nicht: im Container raeumt PID 1 sie oft nicht ab, und
    killpg(pgid, 0) meldet sie trotzdem als lebendig.
    """
    members = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as fh:
                data = fh.read()
        except OSError:
            continue
        rest = data[data.rfind(")") + 1:].split()
        # rest[0] = Zustand (Feld 3), rest[2] = Prozessgruppe (Feld 5)
        if len(rest) > 2 and rest[2] == str(pgid) and rest[0] != "Z":
            members.append(int(entry))
    return members


def test_stop_groups_beendet_gruppe_und_eskaliert():
    # Eine Gruppe, die SIGTERM ignoriert — wie eine sture Autostart-App.
    p = subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 30 & wait"],
                         start_new_session=True)
    try:
        time.sleep(0.2)
        start = exit_guard.process_starttime(p.pid)
        exit_guard.stop_groups([[p.pid, start]], term_timeout=0.3)
        p.wait(timeout=3)
        time.sleep(0.2)
        assert _living_members(p.pid) == []
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
#  Waechter-Schleife
# --------------------------------------------------------------------------- #
def _feed(lines, close=True):
    r, w = os.pipe()
    os.write(w, "".join(line + "\n" for line in lines).encode())
    if close:
        os.close(w)
        return r, None
    return r, w


def test_waechter_raeumt_bei_eof_auf():
    calls = []
    state = {"armed": True, "groups": [[123, 456]]}
    r, _ = _feed([json.dumps({"armed": False, "groups": []}), json.dumps(state)])
    try:
        result = exit_guard.watch(os.getppid(), fd=r, cleanup=calls.append)
    finally:
        os.close(r)
    assert result == "cleaned"
    assert calls == [state]


def test_waechter_tut_nach_bye_nichts():
    calls = []
    r, _ = _feed([json.dumps({"armed": True, "groups": []}), "bye"])
    try:
        result = exit_guard.watch(os.getppid(), fd=r, cleanup=calls.append)
    finally:
        os.close(r)
    assert result == "bye" and calls == []


def test_waechter_nicht_scharf_tut_nichts():
    calls = []
    r, _ = _feed([json.dumps({"armed": False, "groups": []})])
    try:
        assert exit_guard.watch(os.getppid(), fd=r, cleanup=calls.append) == "idle"
    finally:
        os.close(r)
    assert calls == []


def test_waechter_ignoriert_kaputte_zeilen():
    calls = []
    r, _ = _feed(["{kaputt", json.dumps({"armed": True, "groups": []}), "[1,2]"])
    try:
        exit_guard.watch(os.getppid(), fd=r, cleanup=calls.append)
    finally:
        os.close(r)
    assert calls == [{"armed": True, "groups": []}]


def test_waechter_merkt_tote_app_auch_bei_offener_pipe(monkeypatch):
    # Hat ein Kind das Schreibende geerbt, kommt nie ein EOF. Dann muss der
    # Wechsel des Elternprozesses reichen.
    monkeypatch.setattr(exit_guard, "_PARENT_CHECK_S", 0.05)
    calls = []
    r, w = _feed([json.dumps({"armed": True, "groups": []})], close=False)
    try:
        result = exit_guard.watch(-1, fd=r, cleanup=calls.append)   # -1 = nie der Elternprozess
    finally:
        os.close(r)
        os.close(w)
    assert result == "cleaned" and len(calls) == 1


# --------------------------------------------------------------------------- #
#  ExitGuard (App-Seite)
# --------------------------------------------------------------------------- #
class _FakeStdin:
    def __init__(self, broken=False):
        self.lines, self.broken, self.closed = [], broken, False

    def write(self, data):
        if self.broken:
            raise BrokenPipeError(32, "Broken pipe")
        self.lines.append(data.decode().strip())

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeProc:
    pid = 4711

    def __init__(self, broken=False):
        self.stdin = _FakeStdin(broken)
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        self.alive = False
        return 0

    def kill(self):
        self.alive = False


@pytest.fixture
def guard_env(monkeypatch):
    monkeypatch.delenv(exit_guard.DISABLE_ENV, raising=False)
    spawned = []

    def popen(*args, **kwargs):
        spawned.append((args, kwargs))
        return _FakeProc()
    return exit_guard.ExitGuard(popen=popen), spawned


def test_guard_startet_erst_wenn_scharf(guard_env):
    guard, spawned = guard_env
    guard.update(False, [])
    assert spawned == []
    guard.update(True, [])
    assert len(spawned) == 1
    args, kwargs = spawned[0]
    assert "--watch" in args[0] and kwargs["start_new_session"] is True
    assert json.loads(guard._proc.stdin.lines[-1])["armed"] is True


def test_guard_schickt_gleichen_stand_nicht_doppelt(guard_env):
    guard, _ = guard_env
    guard.update(True, [])
    guard.update(True, [])
    assert len(guard._proc.stdin.lines) == 1
    guard.update(False, [])
    assert len(guard._proc.stdin.lines) == 2


def test_guard_release_schickt_bye(guard_env):
    guard, _ = guard_env
    guard.update(True, [])
    proc = guard._proc
    guard.release()
    assert proc.stdin.lines[-1] == "bye" and proc.stdin.closed
    assert guard._proc is None


def test_guard_ueberlebt_toten_waechter(monkeypatch):
    monkeypatch.delenv(exit_guard.DISABLE_ENV, raising=False)
    procs = [_FakeProc(broken=True), _FakeProc()]
    guard = exit_guard.ExitGuard(popen=lambda *a, **k: procs.pop(0))
    guard.update(True, [])                  # Schreiben scheitert → vergessen
    assert guard._proc is None
    guard.update(True, [])                  # naechster Versuch → neuer Waechter
    assert guard._proc is not None and guard._proc.stdin.lines


def test_guard_per_umgebungsvariable_aus(monkeypatch):
    monkeypatch.setenv(exit_guard.DISABLE_ENV, "1")
    spawned = []
    guard = exit_guard.ExitGuard(popen=lambda *a, **k: spawned.append(1))
    guard.update(True, [])
    assert spawned == []


def test_waechter_modul_laedt_kein_qt():
    # Der Waechter importiert exit_guard. Ein Qt-Import auf Modulebene
    # wuerde jeden Waechter um PySide6 aufblaehen.
    code = ("import sys; sys.path.insert(0, %r); import exit_guard; "
            "print('PySide6' in sys.modules)" % str(ROOT / "core"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=30)
    assert out.stdout.strip() == "False", out.stderr


# --------------------------------------------------------------------------- #
#  stop_blocking
# --------------------------------------------------------------------------- #
class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


def _patch_server(monkeypatch, running_until_kill, gone_after_term_s=None):
    log = []
    clock = _Clock()
    state = {"term_at": None, "killed": False}

    def is_running(_p=None):
        if state["killed"]:
            return running_until_kill == "forever"
        if state["term_at"] is not None and gone_after_term_s is not None:
            return clock.now - state["term_at"] < gone_after_term_s
        return True

    monkeypatch.setattr(wivrn_server, "is_running", is_running)
    monkeypatch.setattr(wivrn_server, "request_stop",
                        lambda p=None: (log.append("TERM"), state.update(term_at=clock.now)))
    monkeypatch.setattr(wivrn_server, "force_stop",
                        lambda p=None: (log.append("KILL"), state.update(killed=True)))
    monkeypatch.setattr(wivrn_server, "stop_user_service",
                        lambda: log.append("SERVICE"))
    return log, clock


def test_stop_blocking_normalfall(monkeypatch):
    log, clock = _patch_server(monkeypatch, "kill", gone_after_term_s=1.0)
    assert wivrn_server.stop_blocking(None, _sleep=clock.sleep, _clock=clock) is True
    assert log == ["TERM"]


def test_stop_blocking_eskaliert(monkeypatch):
    log, clock = _patch_server(monkeypatch, "kill", gone_after_term_s=None)
    assert wivrn_server.stop_blocking(None, _sleep=clock.sleep, _clock=clock) is True
    assert log == ["TERM", "SERVICE", "KILL"]
    assert clock.now >= 4.0


def test_stop_blocking_gibt_auf(monkeypatch):
    log, clock = _patch_server(monkeypatch, "forever", gone_after_term_s=None)
    assert wivrn_server.stop_blocking(None, _sleep=clock.sleep, _clock=clock) is False
    assert clock.now < 10


def test_stop_blocking_nichts_zu_tun(monkeypatch):
    monkeypatch.setattr(wivrn_server, "is_running", lambda p=None: False)
    monkeypatch.setattr(wivrn_server, "request_stop", lambda p=None: pytest.fail("TERM ohne Server"))
    assert wivrn_server.stop_blocking(None) is True


# --------------------------------------------------------------------------- #
#  Fenster: Anordnung und closeEvent
# --------------------------------------------------------------------------- #
@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    # paths/config_manager rechnen ihre Pfade beim Import aus — HOME
    # umzusetzen reicht deshalb nicht. Ohne diese beiden Zeilen schriebe der
    # Schalter-Test in die ECHTE config.json des Entwicklers.
    import paths
    import config_manager
    monkeypatch.setattr(paths, "config_file", lambda name: str(tmp_path / name))
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.delenv(exit_guard.DISABLE_ENV, raising=False)
    from PySide6.QtWidgets import QMessageBox, QDialog
    for m in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, m, staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: 0)
    # Nie einen echten Server anfassen oder einen Waechter starten.
    monkeypatch.setattr(wivrn_server, "is_running", lambda p=None: False)
    stops = []
    monkeypatch.setattr(wivrn_server, "stop_blocking",
                        lambda *a, **k: stops.append((a, k)) or True)
    from main import VRApp
    w = VRApp()
    w._exit_guard = exit_guard.ExitGuard(popen=lambda *a, **k: _FakeProc())
    w._stops = stops
    yield w
    # Schliessen wartet auf alle Hintergrund-Threads. Ein Test, der selbst
    # geschlossen hat, laeuft hier in den Doppel-Schutz von closeEvent.
    w.close()


def _page_of(w, widget):
    tabs = w.ui.settings_subtabs
    for i in range(tabs.count()):
        if tabs.widget(i).isAncestorOf(widget):
            return tabs.tabText(i)
    return None


def test_spiele_und_neue_option_unter_erweitert(window):
    import translations
    advanced = translations.tr("settings_sub_advanced")
    assert _page_of(window, window.ui.chk_games_autoscan) == advanced
    assert _page_of(window, window.ui.btn_games_reset) == advanced
    assert _page_of(window, window.ui.chk_stop_server_with_app) == advanced


def test_option_standardmaessig_an_und_wird_gespeichert(window):
    assert window.ui.chk_stop_server_with_app.isChecked()
    window.ui.chk_stop_server_with_app.setChecked(False)
    import paths
    with open(paths.config_file("config.json")) as fh:
        assert json.load(fh)[exit_guard.SETTING_KEY] is False
    assert window._stop_server_with_app is False


def test_close_stoppt_server_wenn_an(window):
    window._server_running = True
    window.close()
    assert len(window._stops) == 1
    assert window._server_running is False


def test_close_laesst_server_laufen_wenn_aus(window):
    window.ui.chk_stop_server_with_app.setChecked(False)
    window._server_running = True
    window.close()
    assert window._stops == []


def test_close_ohne_server_wartet_nicht(window):
    window._server_running = False
    window.close()
    assert window._stops == []


def test_close_zweimal_stoppt_nur_einmal(window):
    window._server_running = True
    window.close()
    window._server_running = True
    window.close()
    assert len(window._stops) == 1


def _card_index(w, widget):
    """Position der Karte, in der ``widget`` steckt, auf ihrer Einstellungsseite."""
    tabs = w.ui.settings_subtabs
    for i in range(tabs.count()):
        page = tabs.widget(i)
        inner = page.widget() if hasattr(page, "widget") else page
        layout = inner.layout()
        for j in range(layout.count()):
            card = layout.itemAt(j).widget()
            if card is not None and (card is widget or card.isAncestorOf(widget)):
                return j
    return None


def test_reihenfolge_app_beenden_spiele_killbefehle(window):
    ui = window.ui
    exit_card = _card_index(window, ui.chk_stop_server_with_app)
    games_card = _card_index(window, ui.chk_games_autoscan)
    kill_card = _card_index(window, ui.btn_killcmd_add)
    assert exit_card < games_card < kill_card


def test_changelog_knoepfe_unter_update_knopf(window):
    ui = window.ui
    from PySide6.QtWidgets import QGridLayout
    grid = next(lay for lay in ui.btn_community_check.parentWidget().findChildren(QGridLayout)
                if lay.indexOf(ui.btn_community_check) >= 0)
    pos = lambda btn: grid.getItemPosition(grid.indexOf(btn))[:2]   # noqa: E731
    assert pos(ui.btn_community_check) == (0, 0)
    assert pos(ui.btn_changelog) == (1, 0)
    assert pos(ui.btn_highlights) == (1, 1)


def test_changelog_knopf_oeffnet_dialog(window):
    dlg = window.show_changelog()
    try:
        assert dlg.isVisible() and "v1." in dlg.browser.toPlainText()
    finally:
        dlg.close()
