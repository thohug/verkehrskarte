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


# Aufloesung des Streuungs-Histogramms: 20 Faecher von 0 bis 10, Breite 0.5.
HIST_FAECHER = 20
HIST_BREITE = 10.0 / HIST_FAECHER


def _fach(jam):
    return min(HIST_FAECHER - 1, int(jam / HIST_BREITE))


def daten_aggregieren(con, tz):
    """Pro Segment die Verteilung des Stauwerts je (Wochentag, Stunde).

    Bewusst NICHT nach Werktag/Wochenende vorverdichtet und ohne Schwelle:
    beides passiert erst im Browser. So kann man dort frei nach Wochentagen
    filtern und die Streuung je Auswahl zeichnen, ohne die Karte neu zu bauen.
    Als Nebeneffekt fuellt sich die Werktagsansicht schneller, weil sie fuenf
    Tage zusammenfasst.

    Je (Wochentag 0..6, Stunde 0..23) wird gespeichert:
      [summe_jam, summe_ratio, histogramm]   mit histogramm = 20 Faecher.
    Die Zahl der Messungen ergibt sich als Summe des Histogramms."""
    segmente = {
        r[0]: {"d": r[1], "p": json.loads(r[2])}
        for r in con.execute("SELECT seg_key, beschreibung, punkte_json FROM segments")
    }

    summe = defaultdict(
        lambda: defaultdict(lambda: [0.0, 0.0, [0] * HIST_FAECHER]))

    zeilen = con.execute(
        "SELECT ts_utc, seg_key, jam_factor, speed_ms, free_flow_ms "
        "FROM messungen WHERE jam_factor IS NOT NULL"
    )
    for ts_utc, seg, jam, speed, frei in zeilen:
        lokal = datetime.fromisoformat(ts_utc).astimezone(tz)
        key = f"{lokal.weekday()}-{lokal.hour}"
        eintrag = summe[seg][key]
        eintrag[0] += jam
        if speed is not None and frei:
            eintrag[1] += speed / frei  # 1.0 = freie Fahrt
        eintrag[2][_fach(jam)] += 1

    ausgabe = []
    for seg, buckets in summe.items():
        if seg not in segmente:
            continue
        h = {key: [round(sj, 2), round(sr, 3), hist]
             for key, (sj, sr, hist) in buckets.items()}
        ausgabe.append(
            {"d": segmente[seg]["d"], "p": segmente[seg]["p"], "h": h}
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
  .wtage { display:flex; gap:3px; margin-bottom:8px; }
  .wtage button {
    flex:1; padding:5px 0; font-size:12px; cursor:pointer;
    border:1px solid rgba(128,128,128,.4); background:transparent; color:inherit; border-radius:5px;
  }
  .wtage button.an { background:#0b6ef4; border-color:#0b6ef4; color:#fff; }
  .presets { display:flex; gap:10px; font-size:11px; margin-bottom:10px; }
  .presets a { color:#0b6ef4; cursor:pointer; text-decoration:none; }
  .presets a:hover { text-decoration:underline; }
  input[type=range] { width:100%; }
  .stunde { font-variant-numeric:tabular-nums; font-weight:600; min-width:74px; text-align:right; }
  .tt-titel { font-weight:600; margin-bottom:2px; }
  .tt-cap { font-size:11px; opacity:.6; margin-top:2px; }
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
    <button data-modus="gesamt" class="aktiv">Gesamt</button>
    <button data-modus="stunde">Nach Uhrzeit</button>
  </div>

  <div id="filter" style="display:none">
    <div class="wtage">
      <button data-tag="0">Mo</button><button data-tag="1">Di</button>
      <button data-tag="2">Mi</button><button data-tag="3">Do</button>
      <button data-tag="4">Fr</button><button data-tag="5">Sa</button>
      <button data-tag="6">So</button>
    </div>
    <div class="presets">
      <a data-preset="wt">Werktag</a><a data-preset="we">Wochenende</a><a data-preset="alle">alle Tage</a>
    </div>
    <div class="zeile">
      <input type="range" id="stunde" min="0" max="23" value="8">
      <span class="stunde" id="stundeLabel">08:00</span>
    </div>
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
const MIN = __MIN__;              // Mindestzahl Messungen, sonst ausgegraut
const HB = __HISTBREITE__;        // Breite eines Histogramm-Fachs
const FAECHER = 20;
const WTAGE = ['Mo','Di','Mi','Do','Fr','Sa','So'];

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

let modus = 'gesamt', stunde = 8;
let tage = new Set([0,1,2,3,4,5,6]);   // gewaehlte Wochentage im Stundenmodus
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

// Fasst die Verteilungen ueber die aktuell gewaehlten Wochentage und Stunden
// zusammen. Im Gesamtmodus alle Tage und alle Stunden, sonst die Auswahl.
function verdichten(seg) {
  const wtage = modus === 'gesamt' ? [0,1,2,3,4,5,6] : [...tage];
  const stunden = modus === 'gesamt' ? Array.from({length:24}, (_,i)=>i) : [stunde];
  let sj = 0, sr = 0;
  const hist = new Array(FAECHER).fill(0);
  for (const t of wtage) for (const h of stunden) {
    const b = seg.h[t + '-' + h];
    if (!b) continue;
    sj += b[0]; sr += b[1];
    for (let i = 0; i < FAECHER; i++) hist[i] += b[2][i];
  }
  const n = hist.reduce((a, c) => a + c, 0);
  if (n === 0) return null;
  return { jam: sj / n, ratio: sr / n, n, hist };
}

// Kleines Balkendiagramm der Streuung, eingefaerbt wie die Karte, mit
// gestrichelter Linie beim Mittelwert.
function histSvg(hist, mean) {
  const W = 188, H = 52, bw = W / FAECHER, max = Math.max(...hist, 1);
  let s = `<svg width="${W}" height="${H+14}" style="display:block;margin-top:4px">`;
  for (let i = 0; i < FAECHER; i++) {
    const h = hist[i] / max * (H - 4);
    if (h > 0) s += `<rect x="${(i*bw).toFixed(1)}" y="${(H-h).toFixed(1)}" `
      + `width="${(bw-0.6).toFixed(1)}" height="${h.toFixed(1)}" fill="${farbe(i*HB+HB/2)}"/>`;
  }
  const mx = (mean / 10 * W).toFixed(1);
  s += `<line x1="${mx}" y1="0" x2="${mx}" y2="${H}" stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 2"/>`;
  s += `<text x="0" y="${H+11}" font-size="9" fill="currentColor" opacity=".6">0</text>`;
  s += `<text x="${W/2-4}" y="${H+11}" font-size="9" fill="currentColor" opacity=".6">5</text>`;
  s += `<text x="${W-14}" y="${H+11}" font-size="9" fill="currentColor" opacity=".6">10</text>`;
  return s + '</svg>';
}

function auswahlText() {
  if (modus === 'gesamt') return 'alle Tage, ganzer Tag';
  const tg = [...tage].sort().map(t => WTAGE[t]).join(' ');
  return `${tg}, ${String(stunde).padStart(2,'0')}:00`;
}

function zeichnen() {
  const cap = auswahlText();
  for (const linie of linien) {
    const a = verdichten(linie._seg);
    if (!a || a.n < MIN) {
      linie.setStyle({ opacity: .12, color: '#999' });
      linie.unbindTooltip();
      continue;
    }
    linie.setStyle({ opacity: .9, color: farbe(a.jam) });
    linie.bindTooltip(
      `<div class="tt-titel">${linie._seg.d || 'ohne Namen'}</div>` +
      `Stauwert &oslash; ${a.jam.toFixed(1)} / 10<br>` +
      `Tempo ${Math.round(a.ratio*100)} % vom Freifluss<br>` +
      `<span style="opacity:.6">${a.n} Messungen</span>` +
      histSvg(a.hist, a.jam) +
      `<div class="tt-cap">Streuung Stauwert &middot; ${cap}</div>`,
      { sticky: true }
    );
  }
}

function tageAnzeigen() {
  document.querySelectorAll('.wtage button').forEach(b =>
    b.classList.toggle('an', tage.has(+b.dataset.tag)));
}

document.querySelectorAll('.tabs button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('aktiv'));
    b.classList.add('aktiv');
    modus = b.dataset.modus;
    document.getElementById('filter').style.display = modus === 'gesamt' ? 'none' : 'block';
    zeichnen();
  };
});

document.querySelectorAll('.wtage button').forEach(b => {
  b.onclick = () => {
    const t = +b.dataset.tag;
    if (tage.has(t)) { if (tage.size > 1) tage.delete(t); }  // mind. einer bleibt
    else tage.add(t);
    tageAnzeigen();
    zeichnen();
  };
});

document.querySelectorAll('.presets a').forEach(a => {
  a.onclick = () => {
    const p = a.dataset.preset;
    tage = new Set(p === 'wt' ? [0,1,2,3,4] : p === 'we' ? [5,6] : [0,1,2,3,4,5,6]);
    tageAnzeigen();
    zeichnen();
  };
});

document.getElementById('stunde').oninput = e => {
  stunde = +e.target.value;
  document.getElementById('stundeLabel').textContent = String(stunde).padStart(2,'0') + ':00';
  zeichnen();
};

tageAnzeigen();
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
    daten = daten_aggregieren(con, tz)
    von, bis, n_runs = zeitraum(con)
    con.close()

    if not daten:
        print("Noch keine Messungen in der Datenbank. Zuerst collect.py laufen "
              "lassen (bzw. import_data.py nach dem git pull).")
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
        .replace("__MIN__", str(schwelle))
        .replace("__HISTBREITE__", repr(HIST_BREITE))
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
