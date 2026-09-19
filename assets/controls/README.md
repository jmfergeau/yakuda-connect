# assets/controls — Controller-Texturen

Die Bilder in diesem Ordner zeigt der Controls-Tab (Bereich „Controls per obah“)
anstelle der eingebauten Zeichnung. Sie lassen sich frei austauschen — es muss
nur der Dateiname stimmen.

## Dateinamen

| Datei | Wofuer |
|---|---|
| `<controller_type>_left.png`  | linke Hand |
| `<controller_type>_right.png` | rechte Hand |
| `<controller_type>.png`       | Fallback fuer beide Seiten; rechts spiegelt die App das Bild selbst |

Vorhandene `controller_type`-Werte (aus obahs Profilen):

`oculus_touch`, `knuckles`, `vive_controller`, `vive_focus3_controller`,
`svl_hand_interaction_augmented`, `gamepad`, `rift`

Erlaubte Endungen: `.png`, `.svg`, `.webp`, `.jpg`, `.jpeg` — in dieser
Reihenfolge wird gesucht. Fehlt eine Datei, zeichnet die App wie bisher selbst.

## Eigene Bilder ohne Aenderung am Programm

Wer die mitgelieferten Bilder behalten, aber eigene benutzen will, legt sie
unter demselben Namen in

    ~/.config/yakuda-connect/controls/

ab. Dieser Ordner hat Vorrang vor `assets/controls` und bleibt bei Updates
unangetastet.

## Seitenverhaeltnis

Die Punkte der Eingaben (Trigger, Stick, Tasten) sitzen an festen Stellen der
Zeichenflaeche des jeweiligen Profils. Damit sie auf dem Bild an der richtigen
Stelle landen, sollte das Bild dasselbe Seitenverhaeltnis haben wie die
mitgelieferte Datei. Das Bild wird ohne Verzerrung mittig eingepasst.

| Controller | Groesse der mitgelieferten Datei |
|---|---|
| `oculus_touch` | 1000 × 1280 |
| `knuckles` | 528 × 864 |
| `vive_controller` | 544 × 1120 |
| `vive_focus3_controller` | 480 × 624 |
| `svl_hand_interaction_augmented` | 560 × 656 |
| `gamepad` | 1680 × 1136 |
| `rift` | 1520 × 1008 |

Transparenter Hintergrund sieht am besten aus — der Kasten scheint dann durch.

## Standardbilder neu erzeugen

```bash
python3 scripts/render_controller_textures.py            # alle
python3 scripts/render_controller_textures.py knuckles   # nur einen
```

Das Skript rendert die eingebauten Vektor-Zeichnungen aus
`ui/controller_view.py` in diesen Ordner — praktisch als Vorlage zum
Uebermalen.
