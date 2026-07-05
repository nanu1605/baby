# Register (or remove) Baby's hidden Windows logon task - feature #2.
# Runs pythonw.exe (no window); output goes to %LOCALAPPDATA%\baby\logs\baby.log
# (run.py self-logs when its streams are None). No admin needed: current-user
# logon trigger with the default limited run level.
#
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1          # register
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove  # unregister

param([switch]$Remove)

$TaskName = "Baby Assistant"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "removed autostart task '$TaskName'."
    exit 0
}

$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-Host "no .venv\Scripts\pythonw.exe - run scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "run.py --all" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# RestartCount: if boot still fails (e.g. Ollama very slow to come up), Task
# Scheduler relaunches Baby up to 3 times a minute apart.
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

# -Force = idempotent re-register; no elevation required for a current-user task.
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null

Write-Host "registered '$TaskName': Baby launches hidden at every logon." -ForegroundColor Green
Write-Host "log file: $env:LOCALAPPDATA\baby\logs\baby.log"
Write-Host "remove any time with: scripts\autostart.ps1 -Remove"
