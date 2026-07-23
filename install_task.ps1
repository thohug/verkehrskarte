# Registriert den Sammler im Windows-Taskplaner.
#
#   powershell -ExecutionPolicy Bypass -File .\install_task.ps1
#
# Standard: alle 15 Minuten, rund um die Uhr, laeuft auch im Akkubetrieb weiter.
# Entfernen mit:  Unregister-ScheduledTask -TaskName "Verkehrskarte Sammler" -Confirm:$false

param(
    [int]$IntervallMinuten = 15,
    [string]$TaskName = "Verkehrskarte Sammler"
)

$ErrorActionPreference = "Stop"

$ordner = Split-Path -Parent $MyInvocation.MyCommand.Path
$skript = Join-Path $ordner "collect.py"

if (-not (Test-Path $skript)) { throw "collect.py nicht gefunden in $ordner" }

$pyLauncher = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $pyLauncher) { throw "Python-Launcher 'py' nicht gefunden. Python installieren." }

$aktion = New-ScheduledTaskAction -Execute $pyLauncher `
    -Argument "`"$skript`"" -WorkingDirectory $ordner

$ausloeser = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervallMinuten) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# Wichtig: der Task darf im Akkubetrieb nicht pausieren, sonst reisst die
# Messreihe genau dann ab, wenn der Laptop nicht am Strom haengt.
$einstellungen = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Bestehenden Task entfernt."
}

Register-ScheduledTask -TaskName $TaskName -Action $aktion -Trigger $ausloeser `
    -Settings $einstellungen -Description "Holt alle $IntervallMinuten Minuten die Verkehrslage von HERE." | Out-Null

Write-Output "Task '$TaskName' registriert: alle $IntervallMinuten Minuten."
Write-Output "Erster Lauf in ca. 1 Minute. Kontrolle: Get-ScheduledTask -TaskName '$TaskName'"
Write-Output "Protokoll: $(Join-Path $ordner 'collect.log')"
