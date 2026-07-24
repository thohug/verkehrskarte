# Holt die neuen Messungen von GitHub, baut die Datenbank nach und schreibt
# die Karte neu. Danach oeffnet sich karte.html.
#
#   .\aktualisieren.ps1              nur holen und auswerten
#   .\aktualisieren.ps1 -Sammeln     vorher zusaetzlich selbst eine Messung machen
#   .\aktualisieren.ps1 -Min 1       auch Zeitfenster mit nur einer Messung zeigen
#
# Fuer -Sammeln muss der HERE-Key als Umgebungsvariable gesetzt sein:
#   setx HERE_API_KEY "dein-key"      (danach neues Fenster oeffnen)

param(
    [switch]$Sammeln,
    [int]$Min = 0,
    [switch]$NichtOeffnen
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Schritt($text) {
    Write-Host ""
    Write-Host "== $text" -ForegroundColor Cyan
}

if ($Sammeln) {
    Schritt "Eigene Messung"
    if (-not $env:HERE_API_KEY) {
        Write-Host "HERE_API_KEY ist nicht gesetzt - Schritt uebersprungen." -ForegroundColor Yellow
        Write-Host 'Setzen mit:  setx HERE_API_KEY "dein-key"   (danach neues Fenster)'
    } else {
        py collect.py
    }
}

Schritt "Neue Messungen von GitHub holen"
$vorher = if (Test-Path messungen) { (Get-ChildItem messungen -Recurse -File).Count } else { 0 }

# Ohne Verknuepfung zwischen main und origin/main weiss git nicht, wovon es
# ziehen soll. Einmalig nachtragen statt mit einer Fehlermeldung abzubrechen.
# Bewusst ueber git config statt rev-parse: das gibt bei fehlender Verknuepfung
# nichts auf stderr aus, und eine stderr-Umleitung wuerde in PowerShell 5.1
# als NativeCommandError den ganzen Lauf abbrechen.
$zweig = (git rev-parse --abbrev-ref HEAD)
$verknuepft = (git config --get "branch.$zweig.merge")
if (-not $verknuepft) {
    Write-Host "Verknuepfung zu origin/$zweig fehlte - wird gesetzt." -ForegroundColor Yellow
    git fetch origin $zweig
    git branch --set-upstream-to=origin/$zweig $zweig
}

git pull --rebase
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "git pull fehlgeschlagen - siehe Meldung oben." -ForegroundColor Red
    Write-Host "Haeufigste Ursache: eigene, noch nicht committete Aenderungen." -ForegroundColor Red
    Write-Host "Pruefen mit:  git status" -ForegroundColor Red
    exit 1
}
$nachher = if (Test-Path messungen) { (Get-ChildItem messungen -Recurse -File).Count } else { 0 }
Write-Host "$($nachher - $vorher) neue Messdatei(en), insgesamt $nachher."

Schritt "In die Datenbank uebernehmen"
py import_data.py
if ($LASTEXITCODE -ne 0) { exit 1 }

Schritt "Karte bauen"
if ($Min -gt 0) { py build_map.py --min $Min } else { py build_map.py }
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Tipp: mit  .\aktualisieren.ps1 -Min 1  siehst du auch einen sehr frischen Stand." -ForegroundColor Yellow
    exit 1
}

if (-not $NichtOeffnen -and (Test-Path karte.html)) {
    Start-Process (Resolve-Path karte.html)
}
