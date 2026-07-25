"""Prueft die Aggregation je (Wochentag, Stunde) in build_map.

    py test_auswertung.py

Arbeitet mit einer temporaeren Datenbank, deine echte Messreihe bleibt unberuehrt.
"""

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import build_map
import collect

fehler = []


def pruefe(b, t):
    print(("  ok    " if b else "  FEHL  ") + t)
    if not b:
        fehler.append(t)


def main():
    db = Path(tempfile.mkdtemp(prefix="ausw-")) / "t.sqlite"
    con = sqlite3.connect(db)
    collect.schema_anlegen(con)
    con.execute("INSERT INTO segments (seg_key, beschreibung, laenge_m, punkte_json, erstmals) "
                "VALUES ('s1','Teststrasse',100,'[[47.0,8.0],[47.1,8.1]]',NULL)")

    # Montag 08:00 UTC als Ausgangspunkt
    montag8 = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    # Vier Montage um 8 Uhr mit steigenden Stauwerten 2,4,6,8 -> Mittel 5
    werte_mo = [2.0, 4.0, 6.0, 8.0]
    for i, jam in enumerate(werte_mo):
        ts = (montag8 + timedelta(days=7 * i)).isoformat()
        con.execute("INSERT INTO messungen (seg_key,jam_factor,speed_ms,free_flow_ms,confidence,befahrbar,ts_utc) "
                    "VALUES ('s1',?,?,?,0.9,'open',?)", (jam, 10.0, 10.0, ts))
    # Ein Samstag 08:00, Stauwert 1 -> eigener Wochentag
    samstag8 = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
    con.execute("INSERT INTO messungen (seg_key,jam_factor,speed_ms,free_flow_ms,confidence,befahrbar,ts_utc) "
                "VALUES ('s1',1.0,10.0,10.0,0.9,'open',?)", (samstag8.isoformat(),))
    con.commit()

    daten = build_map.daten_aggregieren(con, timezone.utc)
    con.close()

    pruefe(len(daten) == 1, "genau ein Segment")
    seg = daten[0]
    h = seg["h"]

    pruefe("0-8" in h, "Fach Montag 08 vorhanden (Wochentag 0)")
    pruefe("5-8" in h, "Fach Samstag 08 vorhanden (Wochentag 5)")

    # Montag 08: Summe jam = 20, Histogramm-Summe = 4
    sj, sr, hist = h["0-8"]
    pruefe(abs(sj - 20.0) < 1e-6, f"Summe jam Montag 08 = 20 (ist {sj})")
    pruefe(sum(hist) == 4, f"vier Messungen im Histogramm (ist {sum(hist)})")
    # Mittel = 20/4 = 5.0
    pruefe(abs(sj / sum(hist) - 5.0) < 1e-6, "Mittelwert Montag 08 = 5.0")

    # Streuung: Werte 2,4,6,8 fallen in vier verschiedene Faecher (Breite 0.5)
    belegte = sum(1 for c in hist if c > 0)
    pruefe(belegte == 4, f"vier verschiedene Histogramm-Faecher belegt (ist {belegte})")

    # Das Fach fuer jam=2.0: Index int(2.0/0.5)=4
    pruefe(hist[build_map._fach(2.0)] == 1, "jam 2.0 im richtigen Fach")
    pruefe(hist[build_map._fach(8.0)] == 1, "jam 8.0 im richtigen Fach")

    print()
    print("ALLES GRUEN" if not fehler else f"{len(fehler)} FEHLGESCHLAGEN: {fehler}")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
