"""Zeigt, welche Strassenabschnitte im Quartier ueberhaupt Verkehrsdaten haben.

    py abdeckung.py

Sinnvoll direkt nach den ersten Messungen: bevor du wochenlang sammelst,
siehst du hier, ob die Datenquelle dein Gebiet ueberhaupt abdeckt.
"""

import json
import sqlite3
import sys
from pathlib import Path

import build_map as bm

BASIS = Path(__file__).resolve().parent


def main():
    with open(BASIS / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    polygon = cfg.get("polygon")
    # config.json haelt GeoJSON-Reihenfolge [Laenge, Breite], intern rechnen
    # wir mit (Breite, Laenge) wie bei den Segmentpunkten.
    polygon_latlon = [(lat, lon) for lon, lat in polygon] if polygon else None

    db = BASIS / "verkehr.sqlite"
    if not db.exists():
        print("Keine Datenbank. Zuerst collect.py laufen lassen.")
        return 1

    con = sqlite3.connect(db)
    drin, draussen = [], []
    for key, besch, laenge, punkte_json in con.execute(
        "SELECT seg_key, beschreibung, laenge_m, punkte_json FROM segments"
    ):
        punkte = json.loads(punkte_json)
        jam = con.execute(
            "SELECT AVG(jam_factor) FROM messungen WHERE seg_key = ?", (key,)
        ).fetchone()[0]
        eintrag = (besch or "(ohne Namen)", laenge or 0.0, jam)
        if polygon_latlon and not bm.beruehrt_polygon(punkte, polygon_latlon):
            draussen.append(eintrag)
        else:
            drin.append(eintrag)
    con.close()

    drin.sort(key=lambda r: -r[1])
    km = sum(r[1] for r in drin) / 1000

    print(f"IM QUARTIER: {len(drin)} Abschnitte, zusammen {km:.1f} km")
    print()
    print(f"{'Bezeichnung':46} {'Laenge':>9}   Stauwert")
    print("-" * 70)
    for name, laenge, jam in drin:
        jam_text = f"{jam:.1f}" if jam is not None else "  -"
        print(f"{name:46} {laenge/1000:7.2f} km   {jam_text}")

    if draussen:
        km_aussen = sum(r[1] for r in draussen) / 1000
        print()
        print(f"Ausserhalb des Umrisses, wird verworfen: {len(draussen)} Abschnitte, "
              f"{km_aussen:.1f} km")
    return 0


if __name__ == "__main__":
    sys.exit(main())
