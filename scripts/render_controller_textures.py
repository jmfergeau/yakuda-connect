#!/usr/bin/env python3
"""
scripts/render_controller_textures.py — Standardbilder fuer assets/controls
===========================================================================
Rendert die eingebauten Zeichnungen aus ui/controller_view.py als PNG nach
``assets/controls``. Genau diese Dateien laedt der Controls-Tab; sie koennen
also durch eigene Bilder ersetzt werden, ohne Code anzufassen.

Benennung (so sucht ui/controller_view.py):

    <controller_type>_left.png    linke Hand
    <controller_type>_right.png   rechte Hand
    <controller_type>.png         Controller ohne Haende (Gamepad, Rift)

Die rechte Datei wird bereits gespiegelt gerendert — seitenweise Bilder
werden beim Zeichnen NICHT noch einmal gespiegelt. Nur ein Bild ohne Seite
spiegelt die App selbst.

Aufruf (aus dem Projektordner):

    python3 scripts/render_controller_textures.py            # alle
    python3 scripts/render_controller_textures.py knuckles   # nur einen

Seitenverhaeltnis und Groesse ergeben sich aus der Zeichenflaeche des
Profils (DRAWINGS in ui/controller_view.py), mal SCALE. Wer ein eigenes Bild
malt, sollte dasselbe Seitenverhaeltnis benutzen — sonst sitzen die Punkte
der Eingaben neben den Tasten.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "core"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt                           # noqa: E402
from PySide6.QtGui import QImage, QPainter, QPen                # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

from ui.controller_view import (ASSET_TEXTURE_DIR, C_BODY, C_BODY_EDGE,     # noqa: E402
                                C_PART, C_PART_EDGE, DRAWINGS)

SCALE = 8.0          # Pixel je Profil-Einheit — grosszuegig, damit es auf
                     # HiDPI-Bildschirmen scharf bleibt

# Controller ohne linke/rechte Hand: dafuer gibt es nur eine Datei.
SINGLE = ("gamepad", "rift")


def render(controller_type, side):
    """Ein Bild rendern und als QImage zurueckgeben."""
    draw, box, view_scale = DRAWINGS[controller_type]
    box = QRectF(box)
    w = max(int(round(box.width() * SCALE)), 1)
    h = max(int(round(box.height() * SCALE)), 1)

    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(Qt.transparent)          # durchsichtig: der Kasten scheint durch

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    if side == "right":
        # Wie die Ansicht: die rechte Hand ist die gespiegelte linke.
        p.translate(box.width() * SCALE, 0)
        p.scale(-SCALE, SCALE)
    else:
        p.scale(SCALE, SCALE)
    p.translate(-box.left(), -box.top())

    # Strichstaerken wie in der Ansicht: dort sind es 2,4 bzw. 2,0 Bildpunkte
    # bei dem Massstab, mit dem der Controller gezeigt wird.
    body, parts = draw(side if side in ("left", "right") else "left")
    p.setPen(QPen(C_BODY_EDGE, 2.4 / view_scale))
    p.setBrush(C_BODY)
    for path in body:
        p.drawPath(path)
    p.setPen(QPen(C_PART_EDGE, 2.0 / view_scale))
    p.setBrush(C_PART)
    for path in parts:
        p.drawPath(path)
    p.end()
    return img


def targets(controller_type):
    """Dateinamen (ohne Ordner) fuer diesen Controller."""
    if controller_type in SINGLE:
        return [(f"{controller_type}.png", "left")]
    return [(f"{controller_type}_left.png", "left"),
            (f"{controller_type}_right.png", "right")]


def main(argv):
    wanted = argv[1:] or sorted(DRAWINGS)
    unknown = [c for c in wanted if c not in DRAWINGS]
    if unknown:
        print("Unbekannter Controller:", ", ".join(unknown))
        print("Bekannt:", ", ".join(sorted(DRAWINGS)))
        return 2

    QApplication(["render-textures"])      # QPainter braucht eine App
    os.makedirs(ASSET_TEXTURE_DIR, exist_ok=True)
    for controller_type in wanted:
        for name, side in targets(controller_type):
            img = render(controller_type, side)
            path = os.path.join(ASSET_TEXTURE_DIR, name)
            if not img.save(path, "PNG"):
                print("Konnte nicht speichern:", path)
                return 1
            print(f"{name:42s} {img.width():4d}x{img.height():<4d}")
    print("\nFertig ->", ASSET_TEXTURE_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
