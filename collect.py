"""Holt einmal die aktuelle Verkehrslage fuer das konfigurierte Gebiet.
Ein Aufruf = ein Zeitschritt.

Zwei Betriebsarten:

  py collect.py              Schreibt direkt in verkehr.sqlite (lokaler Betrieb)
  py collect.py --kompakt    Schreibt nur Dateien nach messungen/ und
                             segmente.json (fuer GitHub Actions, wird dort
                             anschliessend ins Repo committet)

Der Unterschied ist reine Ablage: --kompakt braucht keine Datenbank im Repo,
und import_data.py baut daraus lokal jederzeit die SQLite-Datei.

Das Skript beendet sich bei API-Fehlern trotzdem sauber - im lokalen Betrieb
wird der Fehler als Zeile in der Tabelle `runs` vermerkt. Eine Luecke in der
Messreihe ist verkraftbar, ein abgestuerzter Taskplaner-Eintrag nicht.

Nur Standardbibliothek, keine Installation noetig.
"""

import gzip
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent
DB_PFAD = BASIS / "verkehr.sqlite"
MESSUNGEN_ORDNER = BASIS / "messungen"
SEGMENTE_ORDNER = BASIS / "segmente"
# Altbestand aus frueheren Laeufen. Wird noch gelesen, aber nicht mehr
# geschrieben - eine gemeinsam beschriebene Datei fuehrt zu Merge-Konflikten,
# sobald sich zwei Laeufe ueberschneiden.
SEGMENTE_DATEI = BASIS / "segmente.json"
LOG_PFAD = BASIS / "collect.log"

API_URL = "https://data.traffic.hereapi.com/v7/flow"
VERSUCHE = 3
TIMEOUT_S = 30

DATEI_FORMAT = "%Y%m%dT%H%M%SZ"


def log(text):
    zeile = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {text}"
    print(zeile)
    try:
        with open(LOG_PFAD, "a", encoding="utf-8") as f:
            f.write(zeile + "\n")
    except OSError:
        pass  # z.B. schreibgeschuetztes Dateisystem im CI


def config_laden():
    with open(BASIS / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    key = os.environ.get("HERE_API_KEY") or cfg.get("here_api_key", "")
    if not key or key.startswith("HIER_"):
        raise SystemExit(
            "Kein HERE API Key. Entweder in config.json eintragen oder die "
            "Umgebungsvariable HERE_API_KEY setzen."
        )
    cfg["here_api_key"] = key
    return cfg


# ---------------------------------------------------------------- API

def url_bauen(cfg):
    b = cfg["bbox"]
    params = {
        "in": f"bbox:{b['west']},{b['sued']},{b['ost']},{b['nord']}",
        "locationReferencing": "shape",
        "apiKey": cfg["here_api_key"],
    }
    klassen = cfg.get("strassenklassen") or []
    if klassen and len(klassen) < 5:
        params["functionalClasses"] = ",".join(str(k) for k in klassen)
    # Erweiterte Abdeckung. Gemessen im Raum Winterthur: 23 -> 42 Segmente,
    # ohne Aufpreis. Die zusaetzlichen Abschnitte tragen meist keinen Namen.
    if cfg.get("deep_coverage", True):
        params["advancedFeatures"] = "deepCoverage"
    return API_URL + "?" + urllib.parse.urlencode(params)


def abrufen(url):
    """Ruft die API ab, mit Backoff. Gibt den rohen Antworttext zurueck."""
    letzter_fehler = None
    for versuch in range(1, VERSUCHE + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "verkehrskarte/1.0"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as antwort:
                return antwort.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            koerper = e.read().decode("utf-8", "replace")[:400]
            letzter_fehler = f"HTTP {e.code}: {koerper}"
            # 4xx ausser 429 wird beim Wiederholen nicht besser
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:  # Timeout, DNS, Verbindungsabbruch
            letzter_fehler = f"{type(e).__name__}: {e}"
        if versuch < VERSUCHE:
            time.sleep(2**versuch)
    raise RuntimeError(letzter_fehler or "unbekannter Fehler")


# ---------------------------------------------------------------- Zerlegen

def punkte_extrahieren(location):
    """HERE liefert die Geometrie als Liste von Links mit je einer Punktliste.
    Wir haengen sie zu einem Polylinienzug zusammen."""
    punkte = []
    for link in location.get("shape", {}).get("links", []):
        for p in link.get("points", []):
            punkte.append([round(p["lat"], 6), round(p["lng"], 6)])
    return punkte


def segment_schluessel(beschreibung, punkte):
    """Stabile ID fuer ein Segment. HERE vergibt bei locationReferencing=shape
    keine ID, deshalb hashen wir Geometrie plus Name. Solange HERE die Strasse
    gleich zerschneidet, bleibt der Schluessel ueber Tage hinweg derselbe."""
    roh = (beschreibung or "") + "|" + json.dumps(punkte, separators=(",", ":"))
    return hashlib.sha1(roh.encode("utf-8")).hexdigest()[:16]


def zerlegen(daten):
    """Trennt die Antwort in Geometrie (aendert sich fast nie) und Messwerte
    (aendern sich bei jedem Lauf). Genau diese Trennung haelt das Repo klein:
    die Geometrie wird einmal abgelegt, nicht 96-mal pro Tag."""
    segmente, messwerte = {}, []
    for eintrag in daten.get("results", []):
        loc = eintrag.get("location", {})
        fluss = eintrag.get("currentFlow", {})
        punkte = punkte_extrahieren(loc)
        if len(punkte) < 2:
            continue
        beschreibung = loc.get("description") or ""
        key = segment_schluessel(beschreibung, punkte)
        segmente[key] = {"d": beschreibung, "l": loc.get("length"), "p": punkte}
        messwerte.append(
            [
                key,
                fluss.get("jamFactor"),
                fluss.get("speed"),
                fluss.get("freeFlow"),
                fluss.get("confidence"),
                fluss.get("traversability"),
            ]
        )
    return segmente, messwerte


# ---------------------------------------------------------------- Ablage: SQLite

def schema_anlegen(con):
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS segments (
            seg_key      TEXT PRIMARY KEY,
            beschreibung TEXT,
            laenge_m     REAL,
            punkte_json  TEXT,
            erstmals     TEXT
        );

        CREATE TABLE IF NOT EXISTS messungen (
            ts_utc       TEXT NOT NULL,
            seg_key      TEXT NOT NULL,
            jam_factor   REAL,
            speed_ms     REAL,
            free_flow_ms REAL,
            confidence   REAL,
            befahrbar    TEXT,
            PRIMARY KEY (ts_utc, seg_key)
        );

        CREATE TABLE IF NOT EXISTS runs (
            ts_utc       TEXT PRIMARY KEY,
            status       TEXT,
            n_segmente   INTEGER,
            quelle_stand TEXT,
            fehler       TEXT,
            dauer_ms     INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_messungen_seg ON messungen(seg_key);
        CREATE INDEX IF NOT EXISTS idx_messungen_ts  ON messungen(ts_utc);
        """
    )
    con.commit()


def in_db_schreiben(con, ts, segmente, messwerte):
    neu = 0
    for key, seg in segmente.items():
        cur = con.execute(
            "INSERT OR IGNORE INTO segments "
            "(seg_key, beschreibung, laenge_m, punkte_json, erstmals) VALUES (?,?,?,?,?)",
            (key, seg["d"], seg["l"],
             json.dumps(seg["p"], separators=(",", ":")), ts),
        )
        neu += cur.rowcount
    con.executemany(
        "INSERT OR REPLACE INTO messungen "
        "(seg_key, jam_factor, speed_ms, free_flow_ms, confidence, befahrbar, ts_utc) "
        "VALUES (?,?,?,?,?,?,?)",
        [tuple(m) + (ts,) for m in messwerte],
    )
    con.commit()
    return neu


# ---------------------------------------------------------------- Ablage: Dateien

def bekannte_segmente_lesen():
    """Vereinigt die Geometrie aus allen Teildateien. Spaetere Dateien
    ueberschreiben fruehere bei gleichem Schluessel."""
    bekannt = {}
    if SEGMENTE_DATEI.exists():
        with open(SEGMENTE_DATEI, encoding="utf-8") as f:
            bekannt.update(json.load(f))
    if SEGMENTE_ORDNER.exists():
        for pfad in sorted(SEGMENTE_ORDNER.glob("*.json")):
            with open(pfad, encoding="utf-8") as f:
                bekannt.update(json.load(f))
    return bekannt


def in_dateien_schreiben(jetzt, ts, quelle_stand, segmente, messwerte):
    """Legt die Messwerte als eine kleine gzip-Datei pro Lauf ab, und neu
    entdeckte Abschnitte als eigene Datei unter segmente/.

    Beide Dateinamen enthalten den Zeitstempel und sind damit eindeutig. Kein
    Lauf beschreibt eine Datei, die ein anderer anfasst - deshalb kann es beim
    Zurueckschreiben ins Repo keine Merge-Konflikte geben, auch wenn sich zwei
    Laeufe ueberschneiden. Genau daran ist die frueher gemeinsam beschriebene
    segmente.json gescheitert."""
    tag_ordner = MESSUNGEN_ORDNER / jetzt.strftime("%Y-%m-%d")
    tag_ordner.mkdir(parents=True, exist_ok=True)
    ziel = tag_ordner / (jetzt.strftime(DATEI_FORMAT) + ".json.gz")

    with gzip.open(ziel, "wt", encoding="utf-8") as f:
        json.dump(
            {"ts_utc": ts, "quelle_stand": quelle_stand, "m": messwerte},
            f, separators=(",", ":"), ensure_ascii=False,
        )

    bekannt = bekannte_segmente_lesen()
    neu = {k: v for k, v in segmente.items() if k not in bekannt}
    if neu:
        SEGMENTE_ORDNER.mkdir(exist_ok=True)
        pfad = SEGMENTE_ORDNER / (jetzt.strftime(DATEI_FORMAT) + ".json")
        with open(pfad, "w", encoding="utf-8") as f:
            json.dump(neu, f, separators=(",", ":"), ensure_ascii=False)
    return len(neu), ziel


# ---------------------------------------------------------------- Ablauf

def main(argv):
    kompakt = "--kompakt" in argv
    jetzt = datetime.now(timezone.utc).replace(microsecond=0)
    ts = jetzt.isoformat()
    start = time.monotonic()

    try:
        cfg = config_laden()
    except SystemExit as e:
        log(str(e))
        return 1

    con = None
    if not kompakt:
        con = sqlite3.connect(DB_PFAD)
        schema_anlegen(con)

    status, n_segmente, quelle_stand, fehler = "ok", 0, None, None
    try:
        daten = json.loads(abrufen(url_bauen(cfg)))
        quelle_stand = daten.get("sourceUpdated")
        segmente, messwerte = zerlegen(daten)
        n_segmente = len(messwerte)
        if not messwerte:
            raise RuntimeError(
                "Antwort enthielt keine Segmente - Bounding Box pruefen "
                "(Reihenfolge west, sued, ost, nord)."
            )
        if kompakt:
            neu, ziel = in_dateien_schreiben(
                jetzt, ts, quelle_stand, segmente, messwerte)
            log(f"ok: {n_segmente} Segmente ({neu} neu) -> {ziel.name}")
        else:
            neu = in_db_schreiben(con, ts, segmente, messwerte)
            log(f"ok: {n_segmente} Segmente ({neu} neu), Stand {quelle_stand}")
    except Exception as e:
        status, fehler = "fehler", str(e)[:500]
        log(f"FEHLER: {fehler}")
        if kompakt:
            return 1  # im CI soll der Lauf sichtbar rot werden

    if con is not None:
        con.execute(
            "INSERT OR REPLACE INTO runs "
            "(ts_utc, status, n_segmente, quelle_stand, fehler, dauer_ms) VALUES (?,?,?,?,?,?)",
            (ts, status, n_segmente, quelle_stand, fehler,
             int((time.monotonic() - start) * 1000)),
        )
        con.commit()
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
