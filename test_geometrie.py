"""Prueft die Polygonlogik mit einem Quadrat, bei dem jeder Fall von Hand
nachrechenbar ist, und anschliessend gegen das echte Polygon aus config.json.

    py test_geometrie.py
"""

import json
import sys
from pathlib import Path

import build_map as bm

BASIS = Path(__file__).resolve().parent

# Quadrat 0..10 in beiden Achsen, als (Breite, Laenge)
QUADRAT = [(0, 0), (0, 10), (10, 10), (10, 0)]

FAELLE = [
    ("Punkt klar innen",            [(5, 5), (6, 6)],     True),
    ("Punkt klar aussen",           [(20, 20), (21, 21)], False),
    ("ein Ende innen, eins aussen", [(5, 5), (5, 30)],    True),
    ("quer durch, kein Punkt drin", [(5, -5), (5, 30)],   True),
    ("laeuft knapp daneben",        [(-1, -5), (-1, 30)], False),
    ("diagonal durch die Ecke",     [(-2, 5), (5, -2)],   True),
    ("weit weg, parallel",          [(50, 0), (50, 10)],  False),
]


def main():
    fehler = []

    def pruefe(bedingung, text):
        print(("  ok    " if bedingung else "  FEHL  ") + text)
        if not bedingung:
            fehler.append(text)

    print("Streckenzug gegen Quadrat:")
    for name, punkte, erwartet in FAELLE:
        pruefe(bm.beruehrt_polygon(punkte, QUADRAT) == erwartet, name)

    print("Echtes Polygon aus config.json:")
    with open(BASIS / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    polygon = cfg.get("polygon")
    if not polygon:
        print("  (uebersprungen, kein Polygon konfiguriert)")
    else:
        latlon = [(lat, lon) for lon, lat in polygon]
        breiten = [p[0] for p in latlon]
        laengen = [p[1] for p in latlon]
        mitte_breite = sum(breiten) / len(breiten)
        mitte_laenge = sum(laengen) / len(laengen)
        pruefe(bm.punkt_in_polygon(mitte_breite, mitte_laenge, latlon),
               "Schwerpunkt liegt im eigenen Polygon")
        pruefe(not bm.punkt_in_polygon(mitte_breite + 1.0, mitte_laenge + 1.0, latlon),
               "Punkt ein Grad daneben liegt draussen")
        b = cfg["bbox"]
        pruefe(min(laengen) >= b["west"] and max(laengen) <= b["ost"]
               and min(breiten) >= b["sued"] and max(breiten) <= b["nord"],
               "Polygon liegt vollstaendig in der Bounding Box")

    print()
    print("ALLES GRUEN" if not fehler else f"{len(fehler)} FEHLGESCHLAGEN: {fehler}")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
