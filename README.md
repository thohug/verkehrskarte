# Verkehrskarte

Sammelt über mehrere Tage die Strassenbelastung in einem Gebiet und rendert
daraus eine Karte — Strassen eingefärbt nach mittlerem Stauwert, mit
Schieberegler über die Tagesstunden.

Datenquelle ist die **HERE Traffic API v7** (`/flow`). Ein Request pro
Zeitschritt liefert alle Strassensegmente in der Bounding Box, jeweils mit
Geometrie und `jamFactor` (0 = freie Fahrt, 10 = gesperrt) sowie Ist- und
Freifluss-Geschwindigkeit.

## Einrichten

**1. HERE-Zugang holen.** Auf [platform.here.com](https://platform.here.com)
ein Gratiskonto anlegen, ein Projekt erstellen, dort einen REST-API-Key
generieren.

**2. Key hinterlegen.** Entweder in `config.json` eintragen oder — sauberer,
weil er dann nicht in der Datei steht:

```bash
setx HERE_API_KEY "dein-key"
```

**3. Gebiet festlegen.** In `config.json` die `bbox` anpassen. Die Ecken
bekommst du am schnellsten über [bboxfinder.com](http://bboxfinder.com) —
Reihenfolge dort ist bereits west, süd, ost, nord.

**4. Einmal testen.**

```bash
py collect.py
```

Sollte etwas melden wie `ok: 412 Segmente`. Falls nicht, steht der Grund in
`collect.log`.

**5. Dauerlauf starten.** Zwei Möglichkeiten — wenn dein Rechner nicht
durchläuft, nimm die zweite:

*Auf diesem Rechner:*

```bash
powershell -ExecutionPolicy Bypass -File .\install_task.ps1
```

*Oder bei GitHub, rund um die Uhr und gratis:* siehe Abschnitt
[Im Netz laufen lassen](#im-netz-laufen-lassen-github-actions).

**6. Nach ein paar Tagen die Karte bauen.**

```bash
py build_map.py
```

Erzeugt `karte.html`, per Doppelklick zu öffnen.

## Im Netz laufen lassen (GitHub Actions)

Damit der Sammler nicht davon abhängt, ob dein Rechner an ist. Kostet nichts,
braucht aber ein **öffentliches** Repo — siehe Begründung unten.

**1. Repo anlegen und hochladen.**

```bash
git init; git add .; git commit -m "Verkehrskarte"; git branch -M main
```

Dann auf GitHub ein **öffentliches** Repo erstellen und pushen.

**2. Key als Secret hinterlegen.** Im Repo unter Settings → Secrets and
variables → Actions → New repository secret, Name `HERE_API_KEY`.

Achte darauf, dass in `config.json` der Platzhalter stehen bleibt und der Key
nur im Secret liegt — die Datei ist im öffentlichen Repo für alle lesbar.

**3. Schreibrechte für den Workflow.** Settings → Actions → General → Workflow
permissions → *Read and write permissions*. Ohne das kann der Job die
Messwerte nicht zurückschreiben.

**4. Testen.** Actions → *Verkehrsdaten sammeln* → *Run workflow*. Nach einer
Minute sollte ein Commit `Messung …` erscheinen.

**5. Auswerten**, wann immer du willst:

```bash
git pull
py import_data.py
py build_map.py
```

### Warum öffentlich

GitHub rechnet pro Job auf ganze Minuten auf. 96 Läufe am Tag sind rund 2'880
Minuten im Monat — private Repos haben 2'000 Freiminuten, öffentliche
unbegrenzt viele. Alternativ den Takt auf 30 Minuten stellen, dann passt es
auch privat.

### Was du wissen solltest

**Der Takt wird nicht eingehalten — deutlich nicht.** Über 16 Stunden gemessen
lagen zwischen zwei Läufen im Mittel **90 Minuten**, mit Abständen von 60 bis
196 Minuten. Aus dem 15-Minuten-Takt werden faktisch rund **17 Läufe pro Tag
statt 96**. Verlass dich nicht auf den Cron-Ausdruck.

Deshalb misst der Workflow **mehrfach pro Lauf**: sechs Messungen im Abstand von
fünf Minuten, gesteuert über `--wiederholen 6 --abstand 300`. Damit deckt ein
Lauf ein Zeitfenster von 25 Minuten ab statt eines einzelnen Augenblicks, und du
landest bei rund 100 Messungen am Tag. Misslingt eine einzelne, laufen die
übrigen weiter; erst wenn alle scheitern, wird der Lauf rot.

Für die Auswertung ist die Ungleichmässigkeit unerheblich: der Zeitstempel
entsteht bei der tatsächlichen Messung, gruppiert wird nach Stunde. Die
Messreihe wird ungleichmässig, nicht falsch.

**Die 60-Tage-Abschaltung trifft dich nicht.** GitHub deaktiviert geplante
Workflows in Repos ohne Commit-Aktivität nach 60 Tagen. Da dieser Workflow bei
jedem Lauf selbst committet, läuft die Frist nie ab.

**Platzbedarf** bei rund 400 Segmenten: ca. 6,6 KB pro Lauf, also gut 0,6 MB
pro Tag, 19 MB pro Monat, gut 110 MB im halben Jahr. Unkritisch. Die Geometrie
liegt nur einmal in `segmente.json`, deshalb ist es so wenig.

## Auswertung testen, bevor genug Daten da sind

**Fertiges Ergebnis ansehen** — echte Geometrie deines Quartiers, erfundene
Messwerte über 14 Tage:

```bash
py demo.py
```

Schreibt `demo.html`, deutlich als Demo gekennzeichnet. So sieht die Karte aus,
wenn die Messreihe voll ist: Stundenregler mit sichtbarer Morgen- und
Abendspitze, ruhigeres Wochenende. Deine echte `verkehr.sqlite` wird dabei
nicht angefasst — die Demo schreibt nur nach `demo.sqlite`.

**Aktuellen echten Stand ansehen**, auch mit erst zwei, drei Messungen:

```bash
py build_map.py --min 1
```

Normalerweise blendet der Kartenbau Zeitfenster mit weniger als 3 Messungen
aus, damit ein einzelner Ausreisser keine Strasse rot färbt. `--min 1` hebt das
auf. Zum Hinschauen gut, für Schlussfolgerungen nicht.

Weitere Optionen: `--db PFAD`, `--html PFAD`, `--help`.

## Wie lange sammeln?

Entscheidend ist nicht die Gesamtzahl der Messungen, sondern wie viele davon in
jedes einzelne Stundenfach fallen. Es gibt 48 Fächer: 24 Stunden mal Werktag
und Wochenende. Bei rund 100 Messungen am Tag:

- **Ab 3 Tagen** ist die Gesamtkarte aussagekräftig.
- **Ab 1–2 Wochen** werden die Stundenkurven für Werktage stabil.
- **Wochenende braucht länger**, weil pro Woche nur zwei Tage anfallen — rechne
  mit 3–4 Wochen, bis die Wochenend-Ansicht trägt.

Wie es tatsächlich steht:

```bash
py status.py
```

Zeigt eine Tabelle mit allen 48 Fächern und wie viele Messungen in jedem
liegen. Ein Fach mit weniger als drei Messungen bleibt in der Stundenansicht
leer — die Karte ist dann nicht kaputt, sie hat für diese Stunde schlicht noch
keine Grundlage. Die Gesamtansicht funktioniert von Anfang an.

Die Mehrfachmessung hilft hier besonders: sechs Messungen eines Laufs fallen
alle in dieselbe Stunde und füllen das Fach auf einen Schlag. Vorher brauchte
es dafür drei verschiedene Tage, an denen zufällig dieselbe Stunde getroffen
wurde.

`build_map.py` blendet Zeitfenster mit weniger als 3 Messungen aus, statt sie
auf dünner Basis einzufärben. Der Schwellwert steht oben in der Datei.

## Kosten

Ein Request alle 15 Minuten sind rund 2'900 im Monat. Das liegt deutlich
innerhalb des HERE-Gratiskontingents. Falls du das Gebiet stark vergrösserst,
prüfe im HERE-Portal, wie die Traffic-Transaktionen bei dir gezählt werden —
bei sehr grossen Bounding Boxen kann pro zurückgegebenem Segment abgerechnet
werden statt pro Request.

Sparhebel, falls nötig: in `config.json` die `strassenklassen` auf `[1,2,3,4]`
setzen (lässt reine Quartierstrassen weg), oder den Takt im Taskplaner auf 30
Minuten stellen.

## Dateien

| Datei | Zweck |
|---|---|
| `config.json` | Gebiet, API-Key, Zeitzone, Filter |
| `collect.py` | Sammelt. `--kompakt` schreibt Dateien statt Datenbank, `--wiederholen N --abstand S` misst mehrfach |
| `import_data.py` | Baut aus `messungen/` die lokale Datenbank |
| `build_map.py` | Aggregiert und schreibt `karte.html`. Optionen `--min`, `--db`, `--html` |
| `aktualisieren.ps1` | Holen, auswerten, Karte öffnen — in einem Aufruf |
| `status.py` | Wie weit ist die Messreihe? Füllstand der 48 Stundenfächer |
| `demo.py` | Auswertung mit erfundenen Werten ansehen, bevor genug Daten da sind |
| `abdeckung.py` | Zeigt, welche Strassen überhaupt Daten liefern |
| `install_task.ps1` | Registriert den Sammler im Windows-Taskplaner |
| `.github/workflows/sammeln.yml` | Derselbe Sammler, alle 15 Minuten bei GitHub |
| `messungen/` | Ein gzip pro Lauf, nur Messwerte — das ist die Messreihe |
| `segmente/` | Geometrie und Namen, eine Datei je Lauf mit neu entdeckten Abschnitten |
| `segmente.json` | Altbestand aus den ersten Läufen, wird noch gelesen |
| `verkehr.sqlite` | Lokal aus dem Obigen erzeugt, nicht im Repo |
| `collect.log` | Protokoll jedes Laufs |

Lokaler Betrieb und GitHub Actions lassen sich mischen: `import_data.py`
überspringt Zeitschritte, die schon in der Datenbank stehen.

Jeder Lauf legt ausschliesslich neue, nach Zeitstempel benannte Dateien an und
ändert keine bestehende. Deshalb können sich zwei überlappende Läufe beim
Zurückschreiben ins Repo nicht in die Quere kommen. Eine gemeinsam
beschriebene Datei — anfangs war das `segmente.json` — führt dagegen
zuverlässig zu Merge-Konflikten, sobald sich Läufe überschneiden.

## Datenbank

```sql
-- Tagesgang einer Strasse
SELECT strftime('%H', ts_utc) AS stunde, ROUND(AVG(jam_factor),2)
FROM messungen m JOIN segments s USING (seg_key)
WHERE s.beschreibung LIKE '%Rosengarten%'
GROUP BY stunde ORDER BY stunde;

-- Die zehn am stärksten belasteten Abschnitte
SELECT s.beschreibung, ROUND(AVG(m.jam_factor),2) AS jam, COUNT(*) AS n
FROM messungen m JOIN segments s USING (seg_key)
GROUP BY seg_key HAVING n > 20 ORDER BY jam DESC LIMIT 10;

-- Fuellstand der Stundenfaecher: wie weit ist die Messreihe?
SELECT CASE WHEN CAST(strftime('%w', ts_utc) AS INT) IN (0,6)
            THEN 'Wochenende' ELSE 'Werktag' END AS tagtyp,
       strftime('%H', ts_utc) AS stunde, COUNT(DISTINCT ts_utc) AS messungen
FROM runs WHERE status='ok'
GROUP BY tagtyp, stunde ORDER BY tagtyp, stunde;

-- Ausfaelle kontrollieren
SELECT status, COUNT(*) FROM runs GROUP BY status;
```

## Grenzen

- Die Segmentierung stammt von HERE, nicht von OpenStreetMap. Segmentnamen
  sind meist Strassennamen, aber lange Strassen sind in mehrere Abschnitte
  zerlegt.
- Der `seg_key` wird aus Geometrie und Name gehasht. Ändert HERE die
  Zerschneidung einer Strasse, entsteht ein neues Segment und die Historie des
  alten bricht ab. Bei mehrwöchigen Läufen gelegentlich `SELECT COUNT(*) FROM
  segments` prüfen — springt die Zahl, ist das der Grund.
- `jamFactor` ist ein Modellwert aus Flottendaten, keine Zählstelle. Für
  Verkehrsmengen (Fahrzeuge pro Stunde) brauchst du die amtlichen
  Zählstellendaten, nicht das hier.
