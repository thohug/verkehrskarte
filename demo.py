"""Erzeugt eine Demo-Auswertung, damit du das fertige Ergebnis ansehen kannst,
bevor genug echte Daten da sind.

    py demo.py

Verwendet die ECHTE Geometrie deines Quartiers und legt ERFUNDENE Messwerte
darueber: 14 Tage im 15-Minuten-Takt mit Morgen- und Abendspitze, ruhigerem
Wochenende und einer Nachtabsenkung. Ergebnis ist demo.html, deutlich als
Demo gekennzeichnet.

Die echte Messreihe in verkehr.sqlite wird nicht angefasst - die Demo schreibt
ausschliesslich nach demo.sqlite.
"""

import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import build_map
import collect

BASIS = Path(__file__).resolve().parent
DEMO_DB = BASIS / "demo.sqlite"
DEMO_HTML = BASIS / "demo.html"

TAGE = 14
TAKT_MINUTEN = 15


def basiswert(seg_key):
    """Gibt jedem Abschnitt eine eigene Grundbelastung zwischen 0.6 und 3.4.
    Aus dem Schluessel abgeleitet, damit dieselbe Strasse bei jedem Lauf
    denselben Charakter behaelt."""
    return 0.6 + (int(seg_key[:6], 16) % 280) / 100


def stauwert(basis, zeitpunkt):
    """Tagesgang: Spitzen um 08:00 und 17:30, nachts fast frei, Wochenende
    deutlich ruhiger."""
    h = zeitpunkt.hour + zeitpunkt.minute / 60
    morgen = math.exp(-((h - 8.0) ** 2) / 2.5)
    abend = math.exp(-((h - 17.5) ** 2) / 3.5)
    grund = 0.25 if 6 <= h <= 22 else 0.05
    spitze = max(morgen, abend * 1.1)
    faktor = 0.4 if zeitpunkt.weekday() >= 5 else 1.0
    return min(10.0, basis * (grund + 2.8 * spitze) * faktor)


def main():
    segmente = collect.bekannte_segmente_lesen()
    if not segmente:
        print("Keine Geometrie gefunden. Zuerst 'git pull' oder collect.py laufen lassen.")
        return 1

    if DEMO_DB.exists():
        DEMO_DB.unlink()
    con = sqlite3.connect(DEMO_DB)
    collect.schema_anlegen(con)

    for key, seg in segmente.items():
        con.execute(
            "INSERT OR IGNORE INTO segments "
            "(seg_key, beschreibung, laenge_m, punkte_json, erstmals) VALUES (?,?,?,?,?)",
            (key, seg.get("d"), seg.get("l"),
             json.dumps(seg.get("p"), separators=(",", ":")), None),
        )

    # Ein Montag als Startpunkt, damit Werk- und Wochenendtage sauber verteilt sind
    heute = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start = heute - timedelta(days=heute.weekday() + 7 * (TAGE // 7))

    schritte = TAGE * 24 * (60 // TAKT_MINUTEN)
    frei = 13.9
    for i in range(schritte):
        t = start + timedelta(minutes=TAKT_MINUTEN * i)
        ts = t.isoformat(timespec="seconds")
        zeilen = []
        for key in segmente:
            jam = stauwert(basiswert(key), t)
            tempo = frei * max(0.12, 1 - jam / 11)
            zeilen.append((key, round(jam, 2), round(tempo, 2), frei, 0.9, "open", ts))
        con.executemany(
            "INSERT OR REPLACE INTO messungen "
            "(seg_key, jam_factor, speed_ms, free_flow_ms, confidence, befahrbar, ts_utc) "
            "VALUES (?,?,?,?,?,?,?)", zeilen)
        con.execute(
            "INSERT OR REPLACE INTO runs "
            "(ts_utc, status, n_segmente, quelle_stand, fehler, dauer_ms) VALUES (?,?,?,?,?,?)",
            (ts, "ok", len(segmente), ts, None, None))

    con.commit()
    anzahl = con.execute("SELECT COUNT(*) FROM messungen").fetchone()[0]
    con.close()

    print(f"{schritte} erfundene Messlaeufe ueber {TAGE} Tage, "
          f"{len(segmente)} Abschnitte, {anzahl} Messwerte.")

    code = build_map.karte_bauen(
        db_pfad=DEMO_DB, html_pfad=DEMO_HTML,
        warnung="DEMO - erfundene Messwerte, echte Geometrie",
    )
    if code == 0:
        print()
        print(f"Oeffne {DEMO_HTML.name} - so sieht die Auswertung mit genug Daten aus.")
        print("Loeschen kannst du sie jederzeit: demo.sqlite und demo.html.")
    return code


if __name__ == "__main__":
    sys.exit(main())
