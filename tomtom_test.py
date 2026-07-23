"""Vergleicht die Abdeckung von TomTom mit der von HERE im konfigurierten Gebiet.

Hintergrund: HERE deckt im Raum Winterthur fast nur das uebergeordnete Netz ab
(gemessen 42 Segmente gegenueber 414 in Zuerich bei gleich grossem Ausschnitt).
Bevor du wochenlang sammelst, lohnt der Test, ob TomTom die Quartierstrassen
besser erfasst.

    TomTom-Key gratis holen: https://developer.tomtom.com  (keine Kreditkarte)

    setx TOMTOM_API_KEY "dein-key"
    py tomtom_test.py

Verbraucht rund 60 Abfragen. Das Gratiskontingent liegt bei 2500 pro Tag.

TomTom kennt keine Rechteckabfrage, deshalb wird ein Punktraster ueber das
Gebiet gelegt und pro Punkt der naechstgelegene Strassenabschnitt geholt.
Doppelte Treffer werden zusammengefasst - die Zahl am Ende sagt, wie viele
verschiedene Abschnitte TomTom hier ueberhaupt kennt.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASIS = Path(__file__).resolve().parent
API_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
RASTER = 8  # 8 x 8 = 64 Abfragepunkte


def main():
    key = os.environ.get("TOMTOM_API_KEY", "")
    if not key:
        print("Kein TOMTOM_API_KEY gesetzt.")
        print("Gratis-Key: https://developer.tomtom.com -> Register -> My Dashboard")
        print('Dann:  setx TOMTOM_API_KEY "dein-key"   (neue Shell oeffnen)')
        return 1

    with open(BASIS / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    b = cfg["bbox"]

    gesehen = {}
    fehler = 0
    gesamt = RASTER * RASTER

    for i in range(RASTER):
        for j in range(RASTER):
            lat = b["sued"] + (b["nord"] - b["sued"]) * (i + 0.5) / RASTER
            lon = b["west"] + (b["ost"] - b["west"]) * (j + 0.5) / RASTER
            url = API_URL + "?" + urllib.parse.urlencode(
                {"point": f"{lat},{lon}", "unit": "KMPH", "key": key}
            )
            try:
                with urllib.request.urlopen(url, timeout=20) as antwort:
                    daten = json.loads(antwort.read().decode())
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    print(f"HTTP {e.code}: Key abgelehnt. Ist er im Dashboard aktiv "
                          "und fuer 'Traffic Flow' freigeschaltet?")
                    return 1
                fehler += 1
                continue
            except Exception:
                fehler += 1
                continue

            fs = daten.get("flowSegmentData")
            if not fs:
                continue
            koord = fs.get("coordinates", {}).get("coordinate", [])
            if not koord:
                continue
            # Erster und letzter Punkt als Kennung des Abschnitts
            kennung = (
                round(koord[0]["latitude"], 5), round(koord[0]["longitude"], 5),
                round(koord[-1]["latitude"], 5), round(koord[-1]["longitude"], 5),
            )
            gesehen[kennung] = {
                "klasse": fs.get("frc"),
                "tempo": fs.get("currentSpeed"),
                "frei": fs.get("freeFlowSpeed"),
                "laenge": fs.get("coordinates", {}).get("coordinate") and len(koord),
            }
            print(f"\r{len(gesehen)} verschiedene Abschnitte aus "
                  f"{i*RASTER+j+1}/{gesamt} Punkten", end="", flush=True)

    print()
    print()
    print(f"TomTom kennt hier mindestens {len(gesehen)} verschiedene Strassenabschnitte.")
    if fehler:
        print(f"({fehler} Abfragen fehlgeschlagen)")

    nach_klasse = {}
    for eintrag in gesehen.values():
        nach_klasse[eintrag["klasse"]] = nach_klasse.get(eintrag["klasse"], 0) + 1
    print()
    print("Nach Strassenklasse (FRC0 = Autobahn ... FRC6 = Quartierstrasse):")
    for klasse in sorted(nach_klasse, key=lambda k: str(k)):
        print(f"  {klasse or '?':6} {nach_klasse[klasse]:3} Abschnitte")

    print()
    print("Einordnung: HERE liefert im selben Gebiet 42 Segmente, davon 23 benannt,")
    print("und fast nur Autobahn und Hauptachsen. Findet TomTom deutlich mehr in")
    print("den Klassen FRC4 bis FRC6, lohnt der Wechsel der Datenquelle.")
    print()
    print("Achtung: das Punktraster untererfasst systematisch - benachbarte Punkte")
    print("landen oft auf demselben Abschnitt. Die echte Zahl liegt hoeher. Fuer den")
    print("Vergleich zaehlt die Groessenordnung, nicht der exakte Wert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
