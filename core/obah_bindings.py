#!/usr/bin/env python3
"""
obah_bindings.py — die Auswahl-Schritte von obah, ohne obah zu starten
======================================================================
obah (https://github.com/galister/obah) fuehrt durch drei Bildschirme:

  1. Spiel waehlen        — alle Steam-Spiele MIT OpenVR-Action-Manifest
  2. Controller waehlen   — feste Liste von Eingabeprofilen, je Profil mit
                            Haekchen, welche Bindings es schon gibt
  3. Bindings laden       — "Game default bindings", "xrizer bindings",
                            "VapoR bindings", "OpenComposite bindings" (nur
                            die, die es gibt) oder "Start from scratch"

Dieses Modul liest dieselben Daten nach denselben Regeln (Stand obah 0.1.1,
src/steam.rs, src/sources.rs, src/input_profiles.rs,
src/screens/controller_type.rs), damit der Controls-Tab sie als Dropdowns
anbieten kann. Es wird nur GELESEN — geschrieben wird hier nichts.

Eine Abweichung, bewusst: obah sucht den Spielordner unter
'steamapps/common/<Name aus der .acf>'. Steam legt ihn aber unter
'installdir' ab, und der weicht beim Namen oft ab (Doppelpunkte,
Markenzeichen, ...). Wir nehmen 'installdir' und fallen auf den Namen
zurueck — so tauchen hier auch Spiele auf, die obah uebersieht.
"""
import json
import os
from collections import deque
from dataclasses import dataclass, field

from logging_setup import get_logger

log = get_logger("obah_bindings")

# src/steam.rs: MANIFEST_NAMES
MANIFEST_NAMES = ("actions.json", "action_manifest.json",
                  "vr_actions.json", "steamvr_actions.json")
# src/steam.rs: find_actions_json -> 'depth > 8' bricht ab
MAX_DEPTH = 8

# src/input_profiles.rs: INPUT_PROFILES_BYTES — Reihenfolge wie in obah.
# (pico_controller liegt in obahs profiles/, ist aber nicht eingetragen und
# damit in obah nicht waehlbar — hier deshalb auch nicht.)
CONTROLLER_TYPES = (
    "gamepad",
    "knuckles",
    "oculus_touch",
    "rift",
    "svl_hand_interaction_augmented",
    "vive_controller",
    "vive_focus3_controller",
)

# Lesbare Namen. obah zeigt nur den umformatierten Schluessel
# ("Oculus Touch"); fuer ein Dropdown darf es etwas sprechender sein.
CONTROLLER_LABELS = {
    "gamepad": "Gamepad",
    "knuckles": "Valve Index (Knuckles)",
    "oculus_touch": "Oculus / Meta Touch",
    "rift": "Oculus Rift",
    "svl_hand_interaction_augmented": "Hand-Tracking (SVL Hand Interaction)",
    "vive_controller": "HTC Vive Controller",
    "vive_focus3_controller": "HTC Vive Focus 3 Controller",
}

# Bindings-Quellen (src/screens/mod.rs: BindingSourceSelection)
SOURCE_DEFAULT = "default"
SOURCE_XRIZER = "xrizer"
SOURCE_VAPOR = "vapor"
SOURCE_OPENCOMPOSITE = "opencomposite"
SOURCE_SCRATCH = "scratch"
SOURCE_ORDER = (SOURCE_DEFAULT, SOURCE_XRIZER, SOURCE_VAPOR,
                SOURCE_OPENCOMPOSITE, SOURCE_SCRATCH)


def format_name(controller_type):
    """obahs InputProfile::format_name_str: 'oculus_touch' -> 'Oculus Touch'."""
    return " ".join(w[:1].upper() + w[1:] for w in controller_type.split("_"))


def controller_label(controller_type):
    return CONTROLLER_LABELS.get(controller_type, format_name(controller_type))


@dataclass
class ObahGame:
    name: str
    appid: str
    game_folder: str
    actions_json: str


@dataclass
class Availability:
    default: bool = False
    xrizer: bool = False
    vapor: bool = False
    opencomposite: bool = False

    def any(self):
        return self.default or self.xrizer or self.vapor or self.opencomposite

    def sources(self):
        """Ladbare Quellen in obahs Reihenfolge; 'Start from scratch' immer zuletzt."""
        out = [s for s in (SOURCE_DEFAULT, SOURCE_XRIZER, SOURCE_VAPOR, SOURCE_OPENCOMPOSITE)
               if getattr(self, s)]
        out.append(SOURCE_SCRATCH)
        return out


@dataclass
class GameBindings:
    """Ergebnis fuer ein Spiel: je Controller, welche Bindings es gibt."""
    game: ObahGame
    controllers: dict = field(default_factory=dict)   # {controller_type: Availability}


# --------------------------------------------------------------------------- #
#  1. Spiele
# --------------------------------------------------------------------------- #
def find_actions_json(game_folder, max_depth=MAX_DEPTH):
    """
    Pfad des OpenVR-Action-Manifests im Spielordner, sonst None.

    Breitensuche statt obahs Tiefensuche: gleiche Namen, gleiche Tiefe, aber
    ein Manifest weiter oben gewinnt. Bei Unity liegt es typischerweise
    unter <Spiel>_Data/StreamingAssets/SteamVR/actions.json.
    """
    if not os.path.isdir(game_folder):
        return None
    queue = deque([(game_folder, 0)])
    while queue:
        current, depth = queue.popleft()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for e in entries:
            if e.name in MANIFEST_NAMES and e.is_file():
                return e.path
        if depth >= max_depth:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    queue.append((e.path, depth + 1))
            except OSError:
                pass
    return None


def _game_folder(app):
    common = os.path.join(app["steamapps"], "common")
    for sub in (app.get("installdir"), app.get("name")):
        if sub:
            path = os.path.join(common, sub)
            if os.path.isdir(path):
                return path
    return ""


def list_games(apps=None, cancelled=None):
    """
    Alle installierten Steam-Spiele mit Action-Manifest, alphabetisch
    (wie obah: ohne Gross-/Kleinschreibung).

    apps: Liste wie games.installed_steam_apps() (fuer Tests austauschbar).
    """
    if apps is None:
        import games
        apps = games.installed_steam_apps()
    found = []
    for app in apps:
        if cancelled and cancelled():
            break
        folder = _game_folder(app)
        if not folder:
            continue
        manifest = find_actions_json(folder)
        if manifest:
            found.append(ObahGame(name=app.get("name") or folder,
                                  appid=str(app.get("appid", "")),
                                  game_folder=folder, actions_json=manifest))
    found.sort(key=lambda g: g.name.lower())
    return found


# --------------------------------------------------------------------------- #
#  2./3. Controller und Bindings-Quellen
# --------------------------------------------------------------------------- #
def xrizer_path(game_folder, controller_type):
    """src/sources.rs: <Spiel>/xrizer/<typ ohne Unterstriche>.json"""
    return os.path.join(game_folder, "xrizer", controller_type.replace("_", "") + ".json")


def opencomposite_path(game_folder, controller_type):
    return os.path.join(game_folder, "OpenComposite", controller_type + ".json")


def vapor_path(game_folder):
    return os.path.join(game_folder, "vapor_binding.json")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def default_bindings(actions_json):
    """{controller_type: Pfad} aus 'default_bindings' des Manifests."""
    data = _read_json(actions_json)
    out = {}
    if not isinstance(data, dict):
        return out
    base = os.path.dirname(actions_json)
    for b in data.get("default_bindings") or []:
        if isinstance(b, dict) and b.get("controller_type") and b.get("binding_url"):
            out[b["controller_type"]] = os.path.normpath(os.path.join(base, b["binding_url"]))
    return out


def scan_bindings(game):
    """Je Controller aus CONTROLLER_TYPES: welche Bindings gibt es fuer dieses Spiel?"""
    avail = {ct: Availability() for ct in CONTROLLER_TYPES}

    for ct in default_bindings(game.actions_json):
        if ct in avail:
            avail[ct].default = True

    # xrizer: Dateiname ohne Unterstriche, Vergleich ebenfalls ohne
    xdir = os.path.join(game.game_folder, "xrizer")
    if os.path.isdir(xdir):
        stripped = {ct.replace("_", ""): ct for ct in CONTROLLER_TYPES}
        for fname in os.listdir(xdir):
            stem, ext = os.path.splitext(fname)
            if ext == ".json" and stem.replace("_", "") in stripped:
                avail[stripped[stem.replace("_", "")]].xrizer = True

    odir = os.path.join(game.game_folder, "OpenComposite")
    if os.path.isdir(odir):
        for fname in os.listdir(odir):
            stem, ext = os.path.splitext(fname)
            if ext == ".json" and stem in avail:
                avail[stem].opencomposite = True

    vapor = _read_json(vapor_path(game.game_folder))
    if isinstance(vapor, dict) and vapor.get("controller_type") in avail:
        avail[vapor["controller_type"]].vapor = True

    return GameBindings(game=game, controllers=avail)


def binding_file(game, controller_type, source, must_exist=True):
    """
    Datei, die obah fuer diese Quelle laden wuerde (src/main.rs:
    resolve_binding_path). None bei 'Start from scratch' oder — mit
    must_exist — wenn es sie nicht gibt. Das kommt vor: manche Manifeste
    verweisen auf Dateien, die das Spiel gar nicht mitliefert. obah startet
    dann ohne Meldung mit einer leeren Belegung.
    """
    if source == SOURCE_DEFAULT:
        path = default_bindings(game.actions_json).get(controller_type)
    elif source == SOURCE_XRIZER:
        path = xrizer_path(game.game_folder, controller_type)
    elif source == SOURCE_VAPOR:
        path = vapor_path(game.game_folder)
    elif source == SOURCE_OPENCOMPOSITE:
        path = opencomposite_path(game.game_folder, controller_type)
    else:
        return None
    if not path:
        return None
    return path if (not must_exist or os.path.isfile(path)) else None
