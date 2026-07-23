"""Testet collect.main() in beiden Betriebsarten mit gefaelschtem API-Aufruf,
inklusive der Fehlerpfade. Braucht keinen API-Key.

    py test_collect.py

Der Test arbeitet in einem temporaeren Ordner - deine echte Messreihe wird
nicht angefasst.
"""

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import collect

# --- Isolierung: alle Schreibpfade in einen Wegwerf-Ordner umbiegen ---------
ARBEITSORDNER = Path(tempfile.mkdtemp(prefix="verkehrskarte-test-"))
collect.DB_PFAD = ARBEITSORDNER / "verkehr.sqlite"
collect.MESSUNGEN_ORDNER = ARBEITSORDNER / "messungen"
collect.SEGMENTE_DATEI = ARBEITSORDNER / "segmente.json"
collect.LOG_PFAD = ARBEITSORDNER / "collect.log"

ANTWORT = {
    "sourceUpdated": "2026-07-23T11:45:00Z",
    "results": [
        {
            "location": {
                "description": "Teststrasse", "length": 420.0,
                "shape": {"links": [{"points": [
                    {"lat": 47.3905, "lng": 8.5205},
                    {"lat": 47.3888, "lng": 8.5241}]}]},
            },
            "currentFlow": {"speed": 4.2, "freeFlow": 13.9, "jamFactor": 6.1,
                            "confidence": 0.9, "traversability": "open"},
        },
        {   # Segment ohne brauchbare Geometrie - muss uebersprungen werden
            "location": {"description": "Kaputt", "shape": {"links": [{"points": []}]}},
            "currentFlow": {"jamFactor": 2.0},
        },
    ],
}

collect.config_laden = lambda: {
    "bbox": {"west": 8.5, "sued": 47.37, "ost": 8.54, "nord": 47.39},
    "here_api_key": "test", "strassenklassen": [1, 2, 3, 4, 5],
}

fehler = []


def pruefe(bedingung, text):
    print(("  ok    " if bedingung else "  FEHL  ") + text)
    if not bedingung:
        fehler.append(text)


def main():
    print("1) Lokaler Betrieb (SQLite)")
    collect.abrufen = lambda url: json.dumps(ANTWORT)
    pruefe(collect.main([]) == 0, "main() endet mit 0")
    con = sqlite3.connect(collect.DB_PFAD)
    pruefe(con.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 1,
           "genau 1 Segment (kaputtes verworfen)")
    pruefe(con.execute("SELECT COUNT(*) FROM messungen").fetchone()[0] == 1,
           "1 Messwert")
    pruefe(con.execute("SELECT status FROM runs").fetchone()[0] == "ok",
           "Lauf als ok vermerkt")
    con.close()

    print("2) Kompakt-Betrieb (Dateien)")
    pruefe(collect.main(["--kompakt"]) == 0, "main() endet mit 0")
    pruefe(len(list(collect.MESSUNGEN_ORDNER.glob("*/*.json.gz"))) == 1,
           "genau 1 Messdatei")
    pruefe(collect.SEGMENTE_DATEI.exists(), "segmente.json angelegt")
    with open(collect.SEGMENTE_DATEI, encoding="utf-8") as f:
        pruefe(len(json.load(f)) == 1, "1 Segment in segmente.json")

    print("3) API-Fehler")

    def kaputt(url):
        raise RuntimeError("HTTP 401: Unauthorized")

    collect.abrufen = kaputt
    pruefe(collect.main([]) == 0,
           "lokal: endet trotzdem mit 0 (Taskplaner ueberlebt)")
    con = sqlite3.connect(collect.DB_PFAD)
    zeilen = con.execute(
        "SELECT status, fehler FROM runs WHERE status='fehler'").fetchall()
    pruefe(len(zeilen) == 1 and "401" in zeilen[0][1],
           "Fehler steht in der Tabelle runs")
    con.close()
    pruefe(collect.main(["--kompakt"]) == 1, "CI: endet mit 1 (Lauf wird rot)")

    print("4) Leere Antwort (typischer bbox-Dreher)")
    collect.abrufen = lambda url: json.dumps({"results": []})
    collect.main([])
    con = sqlite3.connect(collect.DB_PFAD)
    letzter = con.execute(
        "SELECT fehler FROM runs WHERE status='fehler' ORDER BY ts_utc DESC").fetchone()
    pruefe(letzter and "Bounding Box" in letzter[0],
           "Hinweis auf Bounding Box im Fehlertext")
    con.close()

    print()
    print("ALLES GRUEN" if not fehler else f"{len(fehler)} FEHLGESCHLAGEN: {fehler}")
    return 1 if fehler else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(ARBEITSORDNER, ignore_errors=True)
    sys.exit(code)
