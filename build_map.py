"""Aggregiert die gesammelten Messungen und schreibt eine einzelne HTML-Datei
mit der Belastungskarte: Strassen eingefaerbt nach mittlerem Stauwert, mit
Schieberegler ueber die Tagesstunden und Umschalter Werktag / Wochenende.

Aufruf:  py build_map.py
Ergebnis: karte.html im selben Ordner, per Doppelklick zu oeffnen.
"""

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASIS = Path(__file__).resolve().parent
DB_PFAD = BASIS / "verkehr.sqlite"
HTML_PFAD = BASIS / "karte.html"

# Ein Segment/Zeitfenster wird erst gezeigt, wenn es so oft gemessen wurde.
# Verhindert, dass ein einzelner Ausreisser eine Strasse rot faerbt.
MIN_MESSUNGEN = 3


def config_laden():
    with open(BASIS / "config.json", encoding="utf-8") as f:
        return json.load(f)


def punkt_in_polygon(lat, lon, polygon_latlon):
    """Strahlenverfahren. `polygon_latlon` ist eine Liste von (Breite, Laenge) -
    also derselben Reihenfolge wie die Segmentpunkte, nicht der GeoJSON-Reihenfolge.
    Umgerechnet wird einmal in auf_polygon_beschneiden()."""
    drin = False
    n = len(polygon_latlon)
    for i in range(n):
        y1, x1 = polygon_latlon[i]
        y2, x2 = polygon_latlon[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            schnitt_x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < schnitt_x:
                drin = not drin
    return drin


def _richtung(a, b, c):
    """Vorzeichen des Kreuzprodukts: liegt c links oder rechts von a->b?"""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def strecken_schneiden(a, b, c, d):
    """Schneiden sich die Strecken a-b und c-d? Punkte als (lat, lon).
    Der Sonderfall exakt kollinearer Strecken wird nicht behandelt - bei
    Geokoordinaten mit sechs Nachkommastellen kommt er praktisch nicht vor."""
    d1, d2 = _richtung(c, d, a), _richtung(c, d, b)
    d3, d4 = _richtung(a, b, c), _richtung(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def beruehrt_polygon(punkte, polygon_latlon):
    """Wahr, sobald der Streckenzug das Polygon beruehrt: entweder liegt ein
    Stuetzpunkt darin, oder eine Teilstrecke kreuzt die Grenze. Der zweite Fall
    faengt lange Abschnitte, die quer durchs Gebiet laufen, ohne darin einen
    Stuetzpunkt zu haben."""
    for lat, lon in punkte:
        if punkt_in_polygon(lat, lon, polygon_latlon):
            return True

    kanten = list(zip(polygon_latlon, polygon_latlon[1:] + polygon_latlon[:1]))
    for a, b in zip(punkte, punkte[1:]):
        for c, d in kanten:
            if strecken_schneiden(a, b, c, d):
                return True
    return False


def auf_polygon_beschneiden(daten, polygon):
    """HERE liefert immer das ganze Rechteck. Hier fallen die Abschnitte weg,
    die den Quartierumriss gar nicht beruehren. Abschnitte, die ueber die
    Grenze hinausragen, bleiben vollstaendig erhalten - sie abzuschneiden
    wuerde die Messwerte verfaelschen, denn der Stauwert gilt fuer den ganzen
    Abschnitt, nicht fuer ein Teilstueck.

    Bewusst erst hier und nicht beim Sammeln: so laesst sich die Grenze
    spaeter aendern, ohne die Messreihe neu aufbauen zu muessen."""
    # config.json haelt das Polygon in GeoJSON-Reihenfolge [Laenge, Breite],
    # die Segmentpunkte dagegen als [Breite, Laenge].
    polygon_latlon = [(lat, lon) for lon, lat in polygon]
    return [seg for seg in daten if beruehrt_polygon(seg["p"], polygon_latlon)]


def zeitzone_laden(name):
    """Windows bringt keine Zeitzonendatenbank mit. Ist das Paket `tzdata`
    nicht installiert, nehmen wir die Systemzeitzone - auf einem Schweizer
    Rechner ist das dasselbe, Sommerzeit inklusive. `None` bedeutet fuer
    datetime.astimezone() genau das."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        print(
            f"Hinweis: Zeitzone '{name}' nicht verfuegbar (kein tzdata-Paket), "
            "verwende die Systemzeitzone."
        )
        return None


def daten_aggregieren(con, tz, min_messungen=None):
    """Liefert pro Segment die Mittelwerte je (Tagtyp, Stunde) und gesamt."""
    schwelle = MIN_MESSUNGEN if min_messungen is None else min_messungen
    segmente = {
        r[0]: {"d": r[1], "p": json.loads(r[2])}
        for r in con.execute("SELECT seg_key, beschreibung, punkte_json FROM segments")
    }

    # summe[seg][bucket] = [summe_jam, summe_ratio, anzahl]
    summe = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0]))

    zeilen = con.execute(
        "SELECT ts_utc, seg_key, jam_factor, speed_ms, free_flow_ms "
        "FROM messungen WHERE jam_factor IS NOT NULL"
    )
    for ts_utc, seg, jam, speed, frei in zeilen:
        lokal = datetime.fromisoformat(ts_utc).astimezone(tz)
        tagtyp = "we" if lokal.weekday() >= 5 else "wt"
        # Verhaeltnis Ist- zu Freifluss-Geschwindigkeit: 1.0 = freie Fahrt
        ratio = (speed / frei) if (speed is not None and frei) else None

        for bucket in (f"{tagtyp}-{lokal.hour}", "alle"):
            eintrag = summe[seg][bucket]
            eintrag[0] += jam
            if ratio is not None:
                eintrag[1] += ratio
            eintrag[2] += 1

    ausgabe = []
    for seg, buckets in summe.items():
        if seg not in segmente:
            continue
        werte = {}
        for bucket, (s_jam, s_ratio, n) in buckets.items():
            if n < schwelle:
                continue
            werte[bucket] = [round(s_jam / n, 2), round(s_ratio / n, 3), n]
        if not werte:
            continue
        ausgabe.append(
            {
                "d": segmente[seg]["d"],
                "p": segmente[seg]["p"],
                "v": werte,
            }
        )
    return ausgabe


def zeitraum(con):
    z = con.execute(
        "SELECT MIN(ts_utc), MAX(ts_utc), COUNT(*) FROM runs WHERE status='ok'"
    ).fetchone()
    return z or (None, None, 0)


HTML_VORLAGE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strassenbelastung __NAME__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  html, body { margin:0; height:100%; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
  #karte { position:absolute; inset:0; }
  .panel {
    position:absolute; z-index:1000; top:12px; left:12px; width:300px; max-width:calc(100vw - 24px);
    background:rgba(255,255,255,.94); border-radius:10px; padding:14px 16px;
    box-shadow:0 2px 16px rgba(0,0,0,.22);
  }
  @media (prefers-color-scheme: dark) {
    .panel { background:rgba(28,28,30,.94); color:#eee; }
  }
  .panel h1 { margin:0 0 2px; font-size:15px; font-weight:600; }
  .panel .meta { font-size:12px; opacity:.65; margin-bottom:12px; }
  .warnung {
    display:block; margin:6px 0 8px; padding:5px 8px; border-radius:6px;
    background:#ffd8d8; color:#8a1010; font-weight:600; opacity:1;
  }
  @media (prefers-color-scheme: dark) { .warnung { background:#5a1a1a; color:#ffd0d0; } }
  .zeile { display:flex; align-items:center; gap:8px; margin-bottom:10px; }
  .tabs { display:flex; gap:4px; margin-bottom:12px; }
  .tabs button {
    flex:1; padding:6px 4px; font-size:12px; cursor:pointer;
    border:1px solid rgba(128,128,128,.4); background:transparent; color:inherit; border-radius:6px;
  }
  .tabs button.aktiv { background:#0b6ef4; border-color:#0b6ef4; color:#fff; }
  input[type=range] { width:100%; }
  .stunde { font-variant-numeric:tabular-nums; font-weight:600; min-width:74px; text-align:right; }
  .skala { display:flex; height:9px; border-radius:5px; overflow:hidden; margin-top:14px; }
  .skala div { flex:1; }
  .skala-text { display:flex; justify-content:space-between; font-size:11px; opacity:.65; margin-top:4px; }
</style>
</head>
<body>
<div id="karte"></div>
<div class="panel">
  <h1>Strassenbelastung __NAME__</h1>
  <div class="meta">__META__</div>

  <div class="tabs">
    <button data-modus="alle" class="aktiv">Gesamt</button>
    <button data-modus="wt">Werktag</button>
    <button data-modus="we">Wochenende</button>
  </div>

  <div class="zeile" id="stundenZeile" style="display:none">
    <input type="range" id="stunde" min="0" max="23" value="8">
    <span class="stunde" id="stundeLabel">08:00</span>
  </div>

  <div class="skala">
    <div style="background:#2ecc40"></div><div style="background:#a8d70b"></div>
    <div style="background:#ffdc00"></div><div style="background:#ff851b"></div>
    <div style="background:#e8112d"></div><div style="background:#85144b"></div>
  </div>
  <div class="skala-text"><span>frei</span><span>zäh</span><span>Stau</span></div>
</div>

<script>
const SEGMENTE = __DATEN__;
const UMRISS = __POLYGON__;

const karte = L.map('karte');
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap, &copy; CARTO &middot; Verkehrsdaten &copy; HERE',
  maxZoom: 19
}).addTo(karte);

if (UMRISS) {
  L.polygon(UMRISS.map(([lon, lat]) => [lat, lon]), {
    color: '#555', weight: 1.5, dashArray: '6 5', fill: false, interactive: false
  }).addTo(karte);
}

// Farbverlauf grün → gelb → orange → dunkelrot, angelehnt an die
// gewohnte Darstellung. Eingang ist der jamFactor 0..10.
function farbe(jam) {
  const stufen = [[0,'#2ecc40'],[2.5,'#a8d70b'],[4,'#ffdc00'],[6,'#ff851b'],[8,'#e8112d'],[10,'#85144b']];
  for (let i = 1; i < stufen.length; i++) {
    if (jam <= stufen[i][0]) {
      const [a, fa] = stufen[i-1], [b, fb] = stufen[i];
      return mische(fa, fb, (jam - a) / (b - a));
    }
  }
  return '#85144b';
}
function mische(a, b, t) {
  const zu = h => [1,3,5].map(i => parseInt(h.substr(i,2),16));
  const [r1,g1,b1] = zu(a), [r2,g2,b2] = zu(b);
  const m = (x,y) => Math.round(x + (y-x) * Math.max(0, Math.min(1,t)));
  return `rgb(${m(r1,r2)},${m(g1,g2)},${m(b1,b2)})`;
}

let modus = 'alle', stunde = 8;
const linien = [];
const grenzen = L.latLngBounds([]);

for (const seg of SEGMENTE) {
  const linie = L.polyline(seg.p, { weight: 4, opacity: .9 }).addTo(karte);
  linie._seg = seg;
  linie.on('mouseover', () => linie.setStyle({ weight: 7 }));
  linie.on('mouseout',  () => linie.setStyle({ weight: 4 }));
  linien.push(linie);
  grenzen.extend(linie.getBounds());
}
karte.fitBounds(grenzen.isValid() ? grenzen : [[47.37,8.50],[47.39,8.54]]);

function schluessel() { return modus === 'alle' ? 'alle' : `${modus}-${stunde}`; }

function zeichnen() {
  const k = schluessel();
  for (const linie of linien) {
    const w = linie._seg.v[k];
    if (!w) { linie.setStyle({ opacity: .12, color: '#999' }); linie.unbindTooltip(); continue; }
    const [jam, ratio, n] = w;
    linie.setStyle({ opacity: .9, color: farbe(jam) });
    linie.bindTooltip(
      `<b>${linie._seg.d || 'ohne Namen'}</b><br>` +
      `Stauwert ${jam.toFixed(1)} / 10<br>` +
      `Tempo ${Math.round(ratio*100)} % vom Freifluss<br>` +
      `<span style="opacity:.6">${n} Messungen</span>`,
      { sticky: true }
    );
  }
}

document.querySelectorAll('.tabs button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('aktiv'));
    b.classList.add('aktiv');
    modus = b.dataset.modus;
    document.getElementById('stundenZeile').style.display = modus === 'alle' ? 'none' : 'flex';
    zeichnen();
  };
});
document.getElementById('stunde').oninput = e => {
  stunde = +e.target.value;
  document.getElementById('stundeLabel').textContent = String(stunde).padStart(2,'0') + ':00';
  zeichnen();
};

zeichnen();
</script>
</body>
</html>
"""


def karte_bauen(db_pfad=None, html_pfad=None, min_messungen=None, warnung=None):
    db_pfad = Path(db_pfad or DB_PFAD)
    html_pfad = Path(html_pfad or HTML_PFAD)
    schwelle = MIN_MESSUNGEN if min_messungen is None else min_messungen

    if not db_pfad.exists():
        print(f"Keine Datenbank unter {db_pfad}. "
              "Zuerst collect.py ein paar Mal laufen lassen.")
        return 1

    cfg = config_laden()
    tz = zeitzone_laden(cfg.get("zeitzone", "Europe/Zurich"))

    con = sqlite3.connect(db_pfad)
    daten = daten_aggregieren(con, tz, schwelle)
    von, bis, n_runs = zeitraum(con)
    con.close()

    if not daten:
        print(
            f"Noch zu wenig Daten (mindestens {schwelle} Messungen pro Zeitfenster "
            "noetig). Sammler laenger laufen lassen, oder --min 1 setzen, um den "
            "aktuellen Stand trotzdem anzusehen."
        )
        return 1

    polygon = cfg.get("polygon")
    if polygon:
        vorher = len(daten)
        daten = auf_polygon_beschneiden(daten, polygon)
        print(f"Auf Quartierumriss beschnitten: {vorher} -> {len(daten)} Abschnitte")
        if not daten:
            print("Nach dem Beschneiden ist nichts uebrig. Liegt das Polygon "
                  "wirklich in der Bounding Box? Reihenfolge ist [Laenge, Breite].")
            return 1

    def hübsch(ts):
        if not ts:
            return "?"
        return datetime.fromisoformat(ts).astimezone(tz).strftime("%d.%m.%Y %H:%M")

    meta = (
        f"{len(daten)} Strassenabschnitte &middot; {n_runs} Messl&auml;ufe<br>"
        f"{hübsch(von)} bis {hübsch(bis)}"
    )
    if warnung:
        meta = f'<span class="warnung">{warnung}</span>' + meta

    html = (
        HTML_VORLAGE.replace("__NAME__", cfg.get("gebiet_name", ""))
        .replace("__META__", meta)
        .replace("__DATEN__", json.dumps(daten, separators=(",", ":"), ensure_ascii=False))
        .replace("__POLYGON__", json.dumps(polygon, separators=(",", ":")) if polygon else "null")
    )
    html_pfad.write_text(html, encoding="utf-8")
    print(f"Geschrieben: {html_pfad}  ({len(daten)} Segmente, {n_runs} Laeufe)")
    return 0


def main(argv):
    """Argumente:
      --db PFAD     andere Datenbank verwenden (Vorgabe verkehr.sqlite)
      --html PFAD   andere Ausgabedatei    (Vorgabe karte.html)
      --min N       Mindestzahl Messungen je Zeitfenster (Vorgabe 3).
                    --min 1 zeigt auch einen ganz frischen Stand.
    """
    werte = {"--db": None, "--html": None, "--min": None, "--warnung": None}
    rest = list(argv)
    while rest:
        schluessel = rest.pop(0)
        if schluessel in ("-h", "--help"):
            print(main.__doc__)
            return 0
        if schluessel not in werte:
            print(f"Unbekanntes Argument: {schluessel}")
            print(main.__doc__)
            return 2
        if not rest:
            print(f"{schluessel} braucht einen Wert.")
            return 2
        werte[schluessel] = rest.pop(0)

    return karte_bauen(
        db_pfad=werte["--db"],
        html_pfad=werte["--html"],
        min_messungen=int(werte["--min"]) if werte["--min"] else None,
        warnung=werte["--warnung"],
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
