# Schritt für Schritt: Sammler auf GitHub

Ziel: der Sammler läuft rund um die Uhr bei GitHub, unabhängig davon, ob dein
Rechner an ist. Kostet nichts. Dauert einmalig etwa 15 Minuten.

---

## Vorab: der API-Key

**Der Key darf nicht ins Repo.** Er kommt an zwei Stellen, beide ausserhalb der
Dateien:

| Wo | Wofür |
|---|---|
| Umgebungsvariable `HERE_API_KEY` auf deinem PC | lokales Testen |
| GitHub-Secret `HERE_API_KEY` | der Sammler in der Cloud |

In `config.json` bleibt `"here_api_key": ""` stehen. Die Datei liegt im
öffentlichen Repo und ist für alle lesbar.

Lokal setzen (einmalig, danach neues Terminal öffnen):

```bash
setx HERE_API_KEY "dein-key-hier"
```

---

## Schritt 1 — Lokalen Stand prüfen

Das Git-Repo ist bereits angelegt und alles ist committet. Kontrolliere nur,
was hochgeladen wird:

```bash
git ls-files
```

Es müssen **genau diese 13 Dateien** erscheinen:

```
.github/workflows/sammeln.yml
.gitignore
ANLEITUNG.md
README.md
abdeckung.py
build_map.py
collect.py
config.json
import_data.py
install_task.ps1
test_collect.py
test_geometrie.py
tomtom_test.py
```

Nicht dabei sein dürfen `verkehr.sqlite`, `karte.html` und `collect.log` — die
stehen in `.gitignore`, weil sie sich lokal jederzeit neu erzeugen lassen.

Und zur Sicherheit, bevor etwas öffentlich wird:

```bash
git grep -i "here_api_key.*[A-Za-z0-9_-]\{20\}"
```

Diese Suche darf **nichts** finden. Schlägt sie an, steht dein Key in einer
Datei und gehört dort entfernt.

---

## Schritt 2 — Repo auf GitHub anlegen

Auf [github.com/new](https://github.com/new):

- **Repository name:** `verkehrskarte`
- **Public** auswählen — wichtig, siehe Begründung unten
- **Kein** README, **kein** .gitignore, **keine** Lizenz hinzufügen (hast du
  schon, sonst gibt es beim ersten Push einen Konflikt)
- *Create repository*

### Warum öffentlich

GitHub rundet jeden Job auf ganze Minuten auf. 96 Läufe am Tag ergeben rund
2'880 Abrechnungsminuten im Monat. Private Repos haben 2'000 Freiminuten,
öffentliche unbegrenzt viele. Willst du es privat, stelle in
`.github/workflows/sammeln.yml` den Takt auf `*/30 * * * *` — dann sind es
1'440 Minuten und es passt.

---

## Schritt 3 — Hochladen

GitHub zeigt dir die passenden Zeilen an. Mit deinem Benutzernamen:

```bash
git remote add origin https://github.com/DEIN-NAME/verkehrskarte.git; git push -u origin main
```

Beim ersten Mal fragt Git nach Zugangsdaten. Als Passwort brauchst du ein
Personal Access Token, nicht dein GitHub-Passwort:
Profilbild → Settings → Developer settings → Personal access tokens → Tokens
(classic) → Generate new token, Haken bei `repo`.

---

## Schritt 4 — Key als Secret hinterlegen

Im Repo auf GitHub:

**Settings → Secrets and variables → Actions → New repository secret**

- **Name:** `HERE_API_KEY` — exakt so geschrieben
- **Secret:** dein Key
- *Add secret*

Der Wert ist danach nicht mehr einsehbar, auch für dich nicht. Das ist so
gewollt.

---

## Schritt 5 — Schreibrechte freigeben

Der Sammler muss die Messwerte zurück ins Repo schreiben können.

**Settings → Actions → General**, ganz unten bei *Workflow permissions*:

- **Read and write permissions** auswählen
- *Save*

Ohne diesen Schritt läuft der Job durch, scheitert aber beim letzten Befehl mit
`403`.

---

## Schritt 6 — Testlauf

**Actions → Verkehrsdaten sammeln → Run workflow → Run workflow**

Nach etwa einer Minute sollte der Lauf grün sein und ein neuer Commit
`Messung 2026-…` im Repo stehen, dazu ein Ordner `messungen/` und die Datei
`segmente.json`.

Ab jetzt läuft es von selbst. Der erste geplante Lauf kommt innerhalb der
nächsten halben Stunde.

### Wenn es nicht klappt

| Meldung | Ursache |
|---|---|
| `Kein HERE API Key` | Secret fehlt oder heisst anders als `HERE_API_KEY` |
| `HTTP 401` | Key falsch oder im HERE-Portal nicht aktiv |
| `403` beim Push | Schritt 5 vergessen |
| `Antwort enthielt keine Segmente` | Bounding Box falsch, Reihenfolge ist west, süd, ost, nord |

---

## Schritt 7 — Auswerten, wann immer du willst

```bash
git pull
```

```bash
py import_data.py; py build_map.py
```

Dann `karte.html` per Doppelklick öffnen.

Der Import überspringt Zeitschritte, die schon in deiner Datenbank stehen — du
kannst das also beliebig oft laufen lassen, es wird nur das Neue nachgezogen.

---

## Was danach passiert

Rechne mit:

- **Rund 17 Läufe pro Tag**, nicht 96. GitHub hält den 15-Minuten-Takt nicht
  ein — gemessen lagen im Mittel 90 Minuten dazwischen, in Spitzen über drei
  Stunden. Deshalb misst jeder Lauf sechsmal im Abstand von fünf Minuten, was
  auf etwa 100 Messungen täglich führt.
- **Jeder Lauf dauert rund 25 Minuten**, weil er zwischen den Messungen wartet.
  Das ist normal und kein hängender Job.
- **Rund 19 MB Zuwachs im Monat** im Repo bei 400 Segmenten. In deinem Gebiet
  mit 40 Segmenten ist es entsprechend weniger, etwa 2 MB.
- **Ab 3 Tagen** trägt die Gesamtkarte, **ab 1–2 Wochen** die
  Werktags-Stundenkurven, **ab 3–4 Wochen** die Wochenendansicht.

Auswerten geht am schnellsten mit:

```powershell
.\aktualisieren.ps1
```

## Anhalten

**Actions → Verkehrsdaten sammeln → ⋯ → Disable workflow.** Die gesammelten
Daten bleiben erhalten.
