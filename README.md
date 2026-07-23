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
git init && git add . && git commit -m "Verkehrskarte" && git branch -M main
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

**Der Takt wird nicht eingehalten.** GitHub verschiebt geplante Läufe
routinemässig um 5 bis 30 Minuten, in Lastspitzen um mehr als eine Stunde. Für
diese Auswertung ist das unerheblich: der Zeitstempel wird beim tatsächlichen
Lauf gesetzt und nach Stunde gruppiert. Die Messreihe wird ungleichmässig,
nicht falsch. Rechne aber damit, dass du statt 96 Messungen pro Tag eher 80–90
bekommst.

**Die 60-Tage-Abschaltung trifft dich nicht.** GitHub deaktiviert geplante
Workflows in Repos ohne Commit-Aktivität nach 60 Tagen. Da dieser Workflow bei
jedem Lauf selbst committet, läuft die Frist nie ab.

**Platzbedarf** bei rund 400 Segmenten: ca. 6,6 KB pro Lauf, also gut 0,6 MB
pro Tag, 19 MB pro Monat, gut 110 MB im halben Jahr. Unkritisch. Die Geometrie
liegt nur einmal in `segmente.json`, deshalb ist es so wenig.

## Wie lange sammeln?

Für eine belastbare Aussage über eine Tagesganglinie brauchst du pro
Zeitfenster mehrere Messungen. Bei 15-Minuten-Takt heisst das:

- **Ab 3 Tagen** ist die Gesamtkarte aussagekräftig.
- **Ab 2 Wochen** werden die Stundenkurven für Werktage stabil.
- **Wochenende braucht länger**, weil pro Woche nur zwei Tage anfallen — rechne
  mit 3–4 Wochen, bis die Wochenend-Ansicht trägt.

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
| `collect.py` | Ein Aufruf = ein Zeitschritt. `--kompakt` schreibt Dateien statt Datenbank |
| `import_data.py` | Baut aus `messungen/` die lokale Datenbank |
| `build_map.py` | Aggregiert und schreibt `karte.html` |
| `install_task.ps1` | Registriert den Sammler im Windows-Taskplaner |
| `.github/workflows/sammeln.yml` | Derselbe Sammler, alle 15 Minuten bei GitHub |
| `messungen/` | Ein gzip pro Lauf, nur Messwerte — das ist die Messreihe |
| `segmente.json` | Geometrie und Namen, einmalig |
| `verkehr.sqlite` | Lokal aus dem Obigen erzeugt, nicht im Repo |
| `collect.log` | Protokoll jedes Laufs |

Lokaler Betrieb und GitHub Actions lassen sich mischen: `import_data.py`
überspringt Zeitschritte, die schon in der Datenbank stehen.

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
