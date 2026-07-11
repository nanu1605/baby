# Register (or remove) Baby's hidden Windows logon task(s) - feature #2.
#
# The always-on BACKEND SERVICE ("Baby Assistant") runs pythonw.exe (no window);
# output goes to %LOCALAPPDATA%\baby\logs\baby.log (run.py self-logs when its streams
# are None). No admin needed: a current-user logon trigger with the default limited
# run level.
#
# With -Shell native (v4), a SECOND logon task ("Baby Shell") also opens the native
# desktop window, which ATTACHES to the always-on service (DECISIONS #120). The two
# are independent: closing the app window ("Quit Baby (app)") never stops the service
# - stopping the service is this script's -Remove, a deliberate separate action.
#
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1                    # backend service, browser mode
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Shell native      # + native window at logon
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove            # unregister everything

param(
    [switch]$Remove,
    [ValidateSet("browser", "native")]
    [string]$Shell = "browser"   # set to match ui.shell in config.yaml
)

$TaskName  = "Baby Assistant"    # always-on backend service (pythonw run.py --all)
$ShellTask = "Baby Shell"        # native desktop window (attaches to the service)

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName  -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $ShellTask -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "removed autostart tasks '$TaskName' and '$ShellTask'."
    Write-Host "the currently-running service (if any) keeps running until you reboot or end its process." -ForegroundColor Cyan
    exit 0
}

$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-Host "no .venv\Scripts\pythonw.exe - run scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

# --- always-on backend service (unchanged) -----------------------------------
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "run.py --all" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# RestartCount: if boot still fails (e.g. Ollama very slow to come up), Task
# Scheduler relaunches Baby up to 3 times a minute apart.
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null
Write-Host "registered '$TaskName': the Baby backend service launches hidden at every logon." -ForegroundColor Green
Write-Host "log file: $env:LOCALAPPDATA\baby\logs\baby.log"

# --- native shell window (only when ui.shell: native) ------------------------
if ($Shell -eq "native") {
    $shellExe = Join-Path $repo "ui\shell\src-tauri\target\release\baby-shell.exe"
    if (Test-Path $shellExe) {
        # --attach-only: the window ATTACHES to the always-on service and never spawns
        # its own backend. Both this task and the service fire at logon; the service's
        # port binds only after its model loads, so the shell WAITS for it rather than
        # racing a duplicate backend (DECISIONS #120, #122).
        $sAction = New-ScheduledTaskAction -Execute $shellExe -Argument "--attach-only" -WorkingDirectory $repo
        $sTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        # Not hidden - it opens a visible window.
        $sSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $ShellTask -Action $sAction -Trigger $sTrigger `
            -Settings $sSettings -Force | Out-Null
        Write-Host "registered '$ShellTask': the native window opens at logon (attaches to the service)." -ForegroundColor Green
    } else {
        Write-Host "native shell not built ($shellExe missing) - skipping '$ShellTask'." -ForegroundColor Yellow
        Write-Host "build it first: npm --prefix ui/shell run build" -ForegroundColor Yellow
    }
} else {
    # Switching back to browser: drop any prior shell task so it does not linger.
    Unregister-ScheduledTask -TaskName $ShellTask -Confirm:$false -ErrorAction SilentlyContinue
}

Write-Host "remove any time with: scripts\autostart.ps1 -Remove"
Write-Host ""
Write-Host "Stopping the always-on service is a SEPARATE action from closing the app window:" -ForegroundColor Cyan
Write-Host "  scripts\autostart.ps1 -Remove   (then reboot, or end the running pythonw process)" -ForegroundColor Cyan
