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
    max-height:calc(100vh - 24px); overflow-y:auto;
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
  .gang { margin-top:12px; padding-top:10px; border-top:1px solid rgba(128,128,128,.25); }
  .gang-titel { font-size:12px; font-weight:600; margin-bottom:4px; }
  .gang svg { display:block; cursor:pointer; }
  .gang-legende { display:flex; gap:12px; font-size:11px; opacity:.7; margin-top:2px; }
  .gang-legende i { display:inline-block; width:10px; height:2px; margin-right:4px; vertical-align:middle; }
  .gang-cap { font-size:11px; opacity:.65; margin-top:3px; }
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

  <div class="gang">
    <div class="gang-titel">Tagesganglinie Gesamtnetz</div>
    <div id="gangSvg"></div>
    <div class="gang-legende">
      <span><i style="background:currentColor"></i>Auswahl</span>
      <span><i style="background:#0b6ef4"></i>Mo–Fr</span>
      <span><i style="background:#ff851b"></i>Sa–So</span>
    </div>
    <div class="gang-cap" id="gangCap"></div>
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
// Graue Landeskarte von swisstopo: frei nutzbar ohne Schluessel, und das Grau
// laesst die farbigen Belastungslinien gut hervortreten. (CARTO verlangt fuer
// seine Kacheln inzwischen einen API-Key und blendet sonst ein Wasserzeichen ein.)
L.tileLayer('https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-grau/default/current/3857/{z}/{x}/{y}.jpeg', {
  attribution: '&copy; swisstopo &middot; Verkehrsdaten &copy; HERE',
  maxNativeZoom: 18,
  maxZoom: 19,
  opacity: 0.45   // abgeschwaecht, damit die Belastungslinien im Vordergrund stehen
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

// HERE liefert pro Fahrtrichtung ein eigenes Segment, oft auf exakt derselben
// Linie (nur umgekehrte Punktreihenfolge). Uebereinander gezeichnet verdeckt
// eine Richtung die andere. Deshalb jede Linie ein paar Meter nach RECHTS der
// Fahrtrichtung versetzen - die Gegenrichtung weicht automatisch nach links aus,
// beide liegen dann nebeneinander, so wie man es von Google kennt.
const VERSATZ_M = 7;

function versetzt(punkte, meter) {
  const mLat = 111320;
  const mLng = mLat * Math.cos((punkte[0] ? punkte[0][0] : 47.5) * Math.PI / 180);
  // In lokale Meter umrechnen und doppelte Folgepunkte entfernen. HERE haengt
  // mehrere Links aneinander, Endpunkt = Anfangspunkt; ein Teilstueck der
  // Laenge null hat keine Richtung und liess die Linie frueher zickzacken.
  const p = [];
  for (const [lat, lng] of punkte) {
    const x = lng * mLng, y = lat * mLat;
    const l = p[p.length - 1];
    if (!l || Math.hypot(x - l[0], y - l[1]) > 0.5) p.push([x, y]);
  }
  if (p.length < 2) return punkte;

  // Normale rechts der Fahrtrichtung fuer jedes Teilstueck: (dx,dy) -> (dy,-dx)
  const n = [];
  for (let i = 0; i < p.length - 1; i++) {
    const dx = p[i+1][0] - p[i][0], dy = p[i+1][1] - p[i][1];
    const len = Math.hypot(dx, dy);
    n.push([dy / len, -dx / len]);
  }

  const raus = [];
  for (let i = 0; i < p.length; i++) {
    const a = n[Math.max(0, i - 1)], b = n[Math.min(n.length - 1, i)];
    // Winkelhalbierende der beiden angrenzenden Normalen; damit der Abstand
    // zur Mittellinie ueberall gleich bleibt, durch cos(halber Knickwinkel)
    // teilen - bei sehr spitzen Knicken begrenzt, sonst schiessen Spitzen raus.
    let mx = a[0] + b[0], my = a[1] + b[1];
    const ml = Math.hypot(mx, my);
    if (ml < 1e-6) { mx = b[0]; my = b[1]; } else { mx /= ml; my /= ml; }
    const cos = Math.max(mx * b[0] + my * b[1], 0.5);
    const d = meter / cos;
    raus.push([(p[i][1] + my * d) / mLat, (p[i][0] + mx * d) / mLng]);
  }
  return raus;
}

for (const seg of SEGMENTE) {
  const linie = L.polyline(versetzt(seg.p, VERSATZ_M), { weight: 4, opacity: .9 }).addTo(karte);
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

// --- Tagesganglinie Gesamtnetz ------------------------------------------
// Laenge je Abschnitt in Metern: die Netzlinie wird laengengewichtet, sonst
// zaehlte ein 10-m-Stummel gleich viel wie vier Kilometer Autobahn.
const LAENGE = SEGMENTE.map(s => {
  let m = 0;
  for (let i = 1; i < s.p.length; i++) {
    const [a1, o1] = s.p[i-1], [a2, o2] = s.p[i];
    m += Math.hypot((a2 - a1) * 111320, (o2 - o1) * 111320 * Math.cos(a1 * Math.PI / 180));
  }
  return m;
});

// Mittlerer Stauwert des Netzes je Stunde fuer die gegebenen Wochentage.
// Pro Abschnitt zuerst dessen Mittel (mit derselben Mindestzahl wie die Karte),
// dann ueber die Abschnitte nach Laenge gewichtet.
const gangCache = {};
function ganglinie(wtage) {
  const key = wtage.join(',');
  if (gangCache[key]) return gangCache[key];
  const werte = [];
  for (let h = 0; h < 24; h++) {
    let sw = 0, sl = 0;
    SEGMENTE.forEach((seg, i) => {
      let sj = 0, n = 0;
      for (const t of wtage) {
        const b = seg.h[t + '-' + h];
        if (!b) continue;
        sj += b[0];
        for (const c of b[2]) n += c;
      }
      if (n < MIN || !LAENGE[i]) return;
      sw += (sj / n) * LAENGE[i];
      sl += LAENGE[i];
    });
    werte.push(sl ? sw / sl : null);
  }
  return gangCache[key] = werte;
}

function gangZeichnen() {
  const W = 268, H = 92, LI = 22, UN = 14, OB = 4;
  const alle = [0,1,2,3,4,5,6];
  const auswahl = modus === 'gesamt' ? alle : [...tage].sort();
  const wt = ganglinie([0,1,2,3,4]), we = ganglinie([5,6]), sel = ganglinie(auswahl);
  const max = Math.max(0.5, ...[...wt, ...we, ...sel].filter(v => v != null));
  const ymax = Math.ceil(max);
  const x = h => LI + h * (W - LI - 4) / 23;
  const y = v => OB + (H - UN - OB) * (1 - v / ymax);

  const pfad = werte => {
    let d = '', neu = true;
    werte.forEach((v, h) => {
      if (v == null) { neu = true; return; }
      d += (neu ? 'M' : 'L') + x(h).toFixed(1) + ',' + y(v).toFixed(1);
      neu = false;
    });
    return d;
  };

  let s = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`;
  // Hilfslinien und Achsen
  // Hilfslinien bei ganzen Stauwerten
  for (let v = 0; v <= ymax; v++) {
    s += `<line x1="${LI}" x2="${W-4}" y1="${y(v)}" y2="${y(v)}" stroke="currentColor" stroke-opacity=".12"/>`;
    s += `<text x="${LI-4}" y="${y(v)+3}" font-size="9" text-anchor="end" fill="currentColor" opacity=".55">${v}</text>`;
  }
  for (const h of [0, 6, 12, 18, 23]) {
    const anker = h === 0 ? 'start' : h === 23 ? 'end' : 'middle';
    s += `<text x="${x(h)}" y="${H-3}" font-size="9" text-anchor="${anker}" fill="currentColor" opacity=".55">${h}h</text>`;
  }
  // gewaehlte Stunde
  if (modus === 'stunde') {
    s += `<line x1="${x(stunde)}" x2="${x(stunde)}" y1="${OB}" y2="${H-UN}" stroke="currentColor" stroke-opacity=".35" stroke-dasharray="2 2"/>`;
  }
  s += `<path d="${pfad(wt)}" fill="none" stroke="#0b6ef4" stroke-width="1.3" opacity=".75"/>`;
  s += `<path d="${pfad(we)}" fill="none" stroke="#ff851b" stroke-width="1.3" opacity=".75" stroke-dasharray="4 2"/>`;
  s += `<path d="${pfad(sel)}" fill="none" stroke="currentColor" stroke-width="2.2"/>`;
  if (modus === 'stunde' && sel[stunde] != null) {
    s += `<circle cx="${x(stunde)}" cy="${y(sel[stunde])}" r="3.5" fill="${farbe(sel[stunde])}" stroke="currentColor" stroke-width="1"/>`;
  }
  // unsichtbare Klickflaechen je Stunde
  for (let h = 0; h < 24; h++) {
    const b = (W - LI - 4) / 23;
    s += `<rect data-h="${h}" x="${x(h) - b/2}" y="0" width="${b}" height="${H}" fill="transparent"><title>${h}:00</title></rect>`;
  }
  s += '</svg>';
  const el = document.getElementById('gangSvg');
  el.innerHTML = s;
  el.querySelectorAll('rect[data-h]').forEach(r => r.onclick = () => stundeSetzen(+r.dataset.h));

  // Spitzenstunde der Auswahl
  let spitze = -1;
  sel.forEach((v, h) => { if (v != null && (spitze < 0 || v > sel[spitze])) spitze = h; });
  const wahl = modus === 'gesamt' ? 'alle Tage'
    : [...tage].sort().map(t => WTAGE[t]).join(' ');
  document.getElementById('gangCap').textContent = spitze < 0
    ? 'Noch zu wenig Daten fuer eine Ganglinie.'
    : `Ø Stauwert, laengengewichtet · Auswahl: ${wahl} · Spitze ${spitze}:00 (${sel[spitze].toFixed(1)}) · Klick waehlt Stunde`;
}

function modusSetzen(m) {
  modus = m;
  document.querySelectorAll('.tabs button').forEach(x =>
    x.classList.toggle('aktiv', x.dataset.modus === m));
  document.getElementById('filter').style.display = m === 'gesamt' ? 'none' : 'block';
}

function stundeSetzen(h) {
  stunde = h;
  document.getElementById('stunde').value = h;
  document.getElementById('stundeLabel').textContent = String(h).padStart(2,'0') + ':00';
  if (modus !== 'stunde') modusSetzen('stunde');
  zeichnen();
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
  gangZeichnen();
}

function tageAnzeigen() {
  document.querySelectorAll('.wtage button').forEach(b =>
    b.classList.toggle('an', tage.has(+b.dataset.tag)));
}

document.querySelectorAll('.tabs button').forEach(b => {
  b.onclick = () => {
    modusSetzen(b.dataset.modus);
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

document.getElementById('stunde').oninput = e => stundeSetzen(+e.target.value);

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
