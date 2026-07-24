"""Zeigt, wie weit die Messreihe ist und woran es hakt.

    py status.py

Beantwortet die drei Fragen, die beim Sammeln immer wieder aufkommen:
wieviel ist da, wie gleichmaessig verteilt, und reicht es schon fuer die Karte.
"""

import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import build_map

BASIS = Path(__file__).resolve().parent


def main():
    db = BASIS / "verkehr.sqlite"
    if not db.exists():
        print("Keine Datenbank. Zuerst import_data.py oder collect.py laufen lassen.")
        return 1

    cfg = build_map.config_laden()
    tz = build_map.zeitzone_laden(cfg.get("zeitzone", "Europe/Zurich"))
    con = sqlite3.connect(db)

    # --- Dateien gegen Datenbank ---------------------------------------
    dateien = list((BASIS / "messungen").glob("*/*.json.gz"))
    n_runs = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    n_ok = con.execute("SELECT COUNT(*) FROM runs WHERE status='ok'").fetchone()[0]
    n_ts = con.execute("SELECT COUNT(DISTINCT ts_utc) FROM messungen").fetchone()[0]
    n_mess = con.execute("SELECT COUNT(*) FROM messungen").fetchone()[0]
    n_seg = con.execute("SELECT COUNT(*) FROM segments").fetchone()[0]

    print("BESTAND")
    print(f"  Messdateien im Ordner messungen/ : {len(dateien)}")
    print(f"  Zeitschritte in der Datenbank    : {n_ts}")
    if len(dateien) > n_ts:
        print("  ^ Es liegen Dateien herum, die nicht importiert sind:")
        print("      py import_data.py")
    elif n_ts > len(dateien):
        print(f"  ({n_ts - len(dateien)} davon lokal gesammelt, ohne Datei - das ist normal,")
        print("   wenn du collect.py direkt aufgerufen hast)")
    print(f"  Laeufe vermerkt (davon ok)       : {n_runs} ({n_ok})")
    print(f"  Messwerte gesamt                 : {n_mess}")
    print(f"  bekannte Strassenabschnitte      : {n_seg}")

    n_fehler = con.execute(
        "SELECT COUNT(*) FROM runs WHERE status = 'fehler'").fetchone()[0]
    if n_fehler:
        print()
        print(f"  Fehlgeschlagene Laeufe: {n_fehler}. Die letzten davon:")
        for ts, txt in con.execute(
            "SELECT ts_utc, fehler FROM runs WHERE status = 'fehler' "
            "ORDER BY ts_utc DESC LIMIT 3"
        ):
            print(f"    {ts}  {(txt or '')[:70]}")

    # --- Zeitraum -------------------------------------------------------
    von, bis = con.execute(
        "SELECT MIN(ts_utc), MAX(ts_utc) FROM messungen").fetchone()
    if von:
        a = datetime.fromisoformat(von).astimezone(tz)
        b = datetime.fromisoformat(bis).astimezone(tz)
        stunden = (b - a).total_seconds() / 3600
        print()
        print("ZEITRAUM")
        print(f"  von {a:%d.%m.%Y %H:%M} bis {b:%d.%m.%Y %H:%M}  "
              f"({stunden/24:.1f} Tage)")
        if stunden > 0:
            print(f"  Schnitt: {n_ts / stunden * 24:.0f} Messungen pro Tag")

    # --- Fuellstand der Stundenfaecher ----------------------------------
    faecher = defaultdict(int)
    for (ts,) in con.execute("SELECT DISTINCT ts_utc FROM messungen"):
        lokal = datetime.fromisoformat(ts).astimezone(tz)
        tagtyp = "we" if lokal.weekday() >= 5 else "wt"
        faecher[(tagtyp, lokal.hour)] += 1
    con.close()

    schwelle = build_map.MIN_MESSUNGEN
    print()
    print(f"FUELLSTAND DER STUNDENFAECHER (Schwelle {schwelle})")
    print("  Stunde    " + "".join(f"{h:>3}" for h in range(24)))
    for tagtyp, name in (("wt", "Werktag"), ("we", "Wochenende")):
        zeile = "".join(f"{faecher[(tagtyp, h)]:>3}" if faecher[(tagtyp, h)] else "  ."
                        for h in range(24))
        voll = sum(1 for h in range(24) if faecher[(tagtyp, h)] >= schwelle)
        print(f"  {name:<10}{zeile}   {voll}/24 voll")

    gesamt_voll = sum(1 for k in faecher if faecher[k] >= schwelle)
    print()
    if gesamt_voll == 0:
        print("Noch kein Fach voll. Die Karte zeigt nur die Gesamtansicht, und auch")
        print("die erst ab 3 Messungen. Zum Hinschauen:  py build_map.py --min 1")
    else:
        print(f"{gesamt_voll} von 48 Faechern sind gefuellt. Fehlende Stunden bleiben")
        print("in der jeweiligen Ansicht leer, bis genug Messungen da sind.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
