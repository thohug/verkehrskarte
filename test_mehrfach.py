"""Prueft die Mehrfachmessung: erzeugt --wiederholen N wirklich N Dateien,
und ueberlebt der Lauf eine einzelne gestoerte Messung?"""
import json
import shutil
import sys
import tempfile
from pathlib import Path


import collect

ORDNER = Path(tempfile.mkdtemp(prefix="batch-"))
collect.DB_PFAD = ORDNER / "verkehr.sqlite"
collect.MESSUNGEN_ORDNER = ORDNER / "messungen"
collect.SEGMENTE_ORDNER = ORDNER / "segmente"
collect.SEGMENTE_DATEI = ORDNER / "segmente.json"
collect.LOG_PFAD = ORDNER / "collect.log"

ANTWORT = {"sourceUpdated": "x", "results": [{
    "location": {"description": "Teststrasse", "length": 100.0,
                 "shape": {"links": [{"points": [{"lat": 47.0, "lng": 8.0},
                                                 {"lat": 47.1, "lng": 8.1}]}]}},
    "currentFlow": {"speed": 5.0, "freeFlow": 13.9, "jamFactor": 3.0,
                    "confidence": 0.9, "traversability": "open"}}]}

collect.config_laden = lambda: {"bbox": {"west": 8, "sued": 47, "ost": 8.2, "nord": 47.2},
                               "here_api_key": "x", "strassenklassen": [1,2,3,4,5]}

fehler = []
def pruefe(b, t):
    print(("  ok    " if b else "  FEHL  ") + t)
    if not b: fehler.append(t)

print("1) Vier Messungen in einem Aufruf")
collect.abrufen = lambda url: json.dumps(ANTWORT)
code = collect.main(["--kompakt", "--wiederholen", "4", "--abstand", "1"])
dateien = sorted(collect.MESSUNGEN_ORDNER.glob("*/*.json.gz"))
pruefe(code == 0, "endet mit 0")
pruefe(len(dateien) == 4, f"4 Messdateien angelegt (sind {len(dateien)})")
pruefe(len({d.name for d in dateien}) == 4, "alle Dateinamen verschieden")
pruefe(len(list(collect.SEGMENTE_ORDNER.glob('*.json'))) == 1,
       "nur 1 Segmentdatei (Geometrie nicht wiederholt)")

print("2) Eine gestoerte Messung darf die anderen nicht entwerten")
# Eigener Ordner je Abschnitt: sonst faellt die erste Messung dieses
# Abschnitts in dieselbe Sekunde wie die letzte des vorigen, und die
# Dateinamen (Sekundenaufloesung) kollidieren.
collect.MESSUNGEN_ORDNER = ORDNER / "messungen2"
zaehler = {"n": 0}
def mal_kaputt(url):
    zaehler["n"] += 1
    if zaehler["n"] == 2:
        raise RuntimeError("HTTP 503: Service Unavailable")
    return json.dumps(ANTWORT)
collect.abrufen = mal_kaputt
vorher = len(list(collect.MESSUNGEN_ORDNER.glob("*/*.json.gz")))
code = collect.main(["--kompakt", "--wiederholen", "3", "--abstand", "1"])
nachher = len(list(collect.MESSUNGEN_ORDNER.glob("*/*.json.gz")))
pruefe(code == 0, "Lauf bleibt gruen, weil 2 von 3 gelangen")
pruefe(nachher - vorher == 2, f"2 neue Dateien (sind {nachher - vorher})")

print("3) Alle Messungen misslungen -> Lauf wird rot")
collect.abrufen = lambda url: (_ for _ in ()).throw(RuntimeError("HTTP 401"))
code = collect.main(["--kompakt", "--wiederholen", "2", "--abstand", "1"])
pruefe(code == 1, "endet mit 1")

print("4) Lokaler Betrieb bleibt gruen, Fehler stehen in der Tabelle")
code = collect.main(["--wiederholen", "2", "--abstand", "1"])
import sqlite3
con = sqlite3.connect(collect.DB_PFAD)
n = con.execute("SELECT COUNT(*) FROM runs WHERE status='fehler'").fetchone()[0]
con.close()
pruefe(code == 0, "endet mit 0 (Taskplaner ueberlebt)")
pruefe(n == 2, f"2 Fehlerzeilen vermerkt (sind {n})")

shutil.rmtree(ORDNER, ignore_errors=True)
print()
print("ALLES GRUEN" if not fehler else f"FEHLGESCHLAGEN: {fehler}")
sys.exit(1 if fehler else 0)
