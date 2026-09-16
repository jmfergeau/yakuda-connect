#!/usr/bin/env python3
"""
scripts/steam_vr_debug.py — nachsehen, was der Games-Tab wirklich liest
=======================================================================
Aufruf:

    python3 scripts/steam_vr_debug.py              # alle installierten Spiele
    python3 scripts/steam_vr_debug.py 438100       # nur diese AppIDs

Warum es das gibt
-----------------
Der Games-Tab richtet sich nach Steams eigener VR-Kennzeichnung aus
``appcache/appinfo.vdf``. Wenn dort etwas nicht stimmt, sieht der Nutzer nur
das Ergebnis — eine kurze Liste oder gar keine — und kann nicht erkennen,
woran es liegt. Dieses Skript zeigt die Zwischenschritte:

  * welche appinfo.vdf gefunden wurde und in welcher Formatversion
  * welche Apps installiert sind
  * welche VR-Felder Steam pro App gesetzt hat
  * was der Scanner daraus macht

Genau das fehlte beim Bau der Erkennung: der ``common``-Block wurde eine
Ebene zu hoch gesucht, war deshalb immer leer, und jedes Spiel galt als
"kein VR" — ohne Fehlermeldung, weil eine leere Angabe ein gueltiges
Ergebnis ist. Mit dieser Ausgabe waere es in Minuten sichtbar gewesen.

Das Skript ist reines Werkzeug: es liest nur und aendert nichts.
"""
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import steam_appinfo as sa      # noqa: E402
import games as games_db        # noqa: E402


VERSIONS = {sa.MAGIC_V39: "v39", sa.MAGIC_V40: "v40", sa.MAGIC_V41: "v41"}


def main(argv):
    wanted = {a for a in argv if a.isdigit()}

    print("=" * 70)
    print("appinfo.vdf")
    print("=" * 70)
    paths = sa.appinfo_paths()
    if not paths:
        print("  KEINE gefunden. Steam mindestens einmal starten, damit der")
        print("  Zwischenspeicher angelegt wird. Der Games-Tab benutzt dann")
        print("  die langsamere Dateierkennung.")
    for path in paths:
        size = os.path.getsize(path) / 1048576
        with open(path, "rb") as fh:
            magic = int.from_bytes(fh.read(4), "little")
        print(f"  {path}")
        print(f"    Groesse : {size:.1f} MB")
        print(f"    Format  : {VERSIONS.get(magic, f'UNBEKANNT (0x{magic:08x})')}")

    print()
    print("=" * 70)
    print("Installierte Steam-Spiele")
    print("=" * 70)
    apps = games_db.installed_steam_apps()
    if wanted:
        apps = [a for a in apps if a["appid"] in wanted]
    print(f"  {len(apps)} gefunden")
    if not apps:
        return 0

    commons, ok = sa.commons([a["appid"] for a in apps])
    print(f"  Steams Daten lesbar: {'ja' if ok else 'NEIN'}")
    print(f"  davon in appinfo.vdf enthalten: {len(commons)}")

    print()
    print("=" * 70)
    print("VR-Kennzeichnung je Spiel")
    print("=" * 70)
    sources = games_db.vr_sources(apps)
    marks = {"steam": "VR ", "files": "DAT", "manual": "HAND", "": "   "}
    vr_count = 0
    for app in sorted(apps, key=lambda a: a["name"].lower()):
        common = commons.get(app["appid"])
        src = sources.get(app["appid"], "")
        vr_count += bool(src)
        mark = marks.get(src, "   ")

        if common is None:
            print(f"  [{mark}] {app['name']}  ({app['appid']}) "
                  f"— steht nicht in appinfo.vdf")
            continue
        if not common:
            print(f"  [{mark}] {app['name']}  ({app['appid']}) "
                  f"— common-Block LEER (Erkennung kann hier nichts sagen)")
            continue

        fields = {k: v for k, v in common.items()
                  if sa._is_vr_field(k) or k == "playareavr"}
        cats = sa.category_ids(common)
        vr_cats = sorted(cats & sa.VR_CATEGORY_IDS)
        typ = sa.app_type(common) or "?"
        print(f"  [{mark}] {app['name']}  ({app['appid']}, type={typ})")
        for key, value in sorted(fields.items()):
            print(f"          {key} = {value!r}")
        if vr_cats:
            print(f"          VR-Kategorien: {vr_cats}")
        if not fields and not vr_cats:
            print("          (kein VR-Feld, keine VR-Kategorie)")
        if src == "files":
            print("          -> ueber die Dateierkennung gefunden "
                  "(Steam kennzeichnet es nicht)")

    print()
    print("  Legende: [VR ] Steams Kennzeichnung  [DAT] Dateierkennung  "
          "[HAND] von dir eingetragen")

    print()
    print("=" * 70)
    print("Ergebnis des Scanners")
    print("=" * 70)
    tested, untested = games_db.scan_installed_games()
    per_source = {}
    for src in sources.values():
        if src:
            per_source[src] = per_source.get(src, 0) + 1
    print(f"  als VR erkannt: {vr_count}  "
          f"(Steam: {per_source.get('steam', 0)}, "
          f"Dateien: {per_source.get('files', 0)}, "
          f"von Hand: {per_source.get('manual', 0)})")
    print(f"  getestet (kuratiertes Profil): {len(tested)}")
    for appid in tested:
        print(f"      {games_db.GAMES[appid]['name']}  ({appid})")
    print(f"  ungetestet: {len(untested)}")
    for g in untested:
        print(f"      {g['name']}  ({g['appid']})")

    manual = games_db.load_manual_steam_appids()
    if manual:
        print(f"  von Hand ergaenzt: {', '.join(manual)}")
    local = games_db.load_local_games()
    if local:
        print(f"  eigene Spiele: {len(local)}")
        for e in local:
            print(f"      {e['name']}  -> {e['exe']}")

    cached = games_db.load_vr_filecheck_cache()
    print(f"  Dateierkennung gecacht fuer {len(cached)} Spiele "
          "(der teure Durchlauf laeuft nur einmal je Spiel)")

    if vr_count == 0 and commons:
        print()
        print("  HINWEIS: Steams Daten wurden gelesen, aber kein einziges")
        print("  Spiel ist als VR gekennzeichnet. Der Scanner faellt in dem")
        print("  Fall automatisch auf die Dateierkennung zurueck. Bitte die")
        print("  Ausgabe oben in ein GitHub-Issue kopieren — dann laesst sich")
        print("  sehen, welche Felder Steam bei dir wirklich setzt.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
