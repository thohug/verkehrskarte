"""Baut aus den von GitHub Actions gesammelten Dateien die lokale SQLite-Datei.

    git pull
    py import_data.py
    py build_map.py

Liest segmente.json und alle Dateien unter messungen/. Laeuft beliebig oft -
bereits importierte Zeitschritte werden uebersprungen, es wird also nur das
Neue nachgezogen.
"""

import gzip
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import collect

BASIS = Path(__file__).resolve().parent


def main():
    segmente = collect.bekannte_segmente_lesen()
    if not segmente:
        print("Keine Geometrie gefunden (weder segmente/ noch segmente.json).")
        print("Zuerst 'git pull', oder collect.py --kompakt laufen lassen.")
        return 1

    dateien = sorted(collect.MESSUNGEN_ORDNER.glob("*/*.json.gz"))
    if not dateien:
        print("Keine Messdateien unter messungen/ gefunden.")
        return 1

    con = sqlite3.connect(collect.DB_PFAD)
    collect.schema_anlegen(con)

    schon_da = {r[0] for r in con.execute("SELECT ts_utc FROM runs")}

    # Geometrie einmalig ablegen
    for key, seg in segmente.items():
        con.execute(
            "INSERT OR IGNORE INTO segments "
            "(seg_key, beschreibung, laenge_m, punkte_json, erstmals) VALUES (?,?,?,?,?)",
            (key, seg.get("d"), seg.get("l"),
             json.dumps(seg.get("p"), separators=(",", ":")), None),
        )
    con.commit()

    neu, uebersprungen, kaputt = 0, 0, 0
    for pfad in dateien:
        try:
            with gzip.open(pfad, "rt", encoding="utf-8") as f:
                inhalt = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  uebersprungen (defekt): {pfad.name} - {e}")
            kaputt += 1
            continue

        ts = inhalt.get("ts_utc")
        if not ts:
            # Aeltere Dateien ohne Feld: Zeitstempel aus dem Dateinamen
            roh = pfad.stem.removesuffix(".json")
            ts = datetime.strptime(roh, collect.DATEI_FORMAT).replace(
                tzinfo=timezone.utc).isoformat()

        if ts in schon_da:
            uebersprungen += 1
            continue

        messwerte = inhalt.get("m", [])
        con.executemany(
            "INSERT OR REPLACE INTO messungen "
            "(seg_key, jam_factor, speed_ms, free_flow_ms, confidence, befahrbar, ts_utc) "
            "VALUES (?,?,?,?,?,?,?)",
            [tuple(m) + (ts,) for m in messwerte],
        )
        con.execute(
            "INSERT OR REPLACE INTO runs "
            "(ts_utc, status, n_segmente, quelle_stand, fehler, dauer_ms) VALUES (?,?,?,?,?,?)",
            (ts, "ok", len(messwerte), inhalt.get("quelle_stand"), None, None),
        )
        neu += 1

    con.commit()
    gesamt = con.execute("SELECT COUNT(*) FROM messungen").fetchone()[0]
    con.close()

    print(f"{neu} neue Zeitschritte importiert, {uebersprungen} schon vorhanden"
          + (f", {kaputt} defekt" if kaputt else ""))
    print(f"Datenbank enthaelt jetzt {gesamt} Messwerte, {len(segmente)} Segmente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
