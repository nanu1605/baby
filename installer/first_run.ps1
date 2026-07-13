<#
v6 W3d: first-run bootstrap -- stand up the Python backend on first launch.

The shell runs this the first time an installed Baby launches without a venv
(main.rs ensure_venv). Using the BUNDLED uv.exe, it installs a managed CPython and
builds %LOCALAPPDATA%\baby\.venv from the pinned lock, then gates on a FUNCTIONAL
wheels probe (core.health --level wheels): import + a real op per native wheel, so a
"pip succeeded but the .pyd won't load" case (usually a missing Visual C++ runtime)
surfaces here with a legible message, not three screens later.

Runs on first LAUNCH, not during install, on purpose: a silent installer doing a
~1.5 GB uv sync would freeze with no progress and leave a half-install on failure.
On launch it is resumable (uv sync continues from its cache on re-run) and the shell
paints a "setting up" splash; a classified failure message replaces a raw trace.

The heavy MODEL downloads (whisper/kokoro/e5/9B) are NOT here -- the in-app wizard
fetches those with progress after the backend boots.

Usage (shell):
  powershell -NoProfile -ExecutionPolicy Bypass -File first_run.ps1 `
      -UvExe <code>\uv.exe -SourceDir <code> -BabyHome %LOCALAPPDATA%\baby

Dev smoke (verify the wheels probe against an existing venv, no re-sync):
  installer\first_run.ps1 -SourceDir . -BabyHome . -ProbeOnly
#>

[CmdletBinding()]
param(
    # Bundled uv.exe (payload\uv.exe). Falls back to uv on PATH for a dev smoke.
    [string]$UvExe = "uv",
    # Where run.py + the Python source live (the installed code dir / repo root).
    [string]$SourceDir = ".",
    # Per-user writable home; the venv lands at $BabyHome\.venv.
    [string]$BabyHome = (Join-Path $env:LOCALAPPDATA "baby"),
    # Managed CPython to install (matches requires-python >=3.11; lock resolved on 3.13).
    [string]$PythonVersion = "3.13",
    [int]$Retries = 3,
    # Skip python-install + sync; only run the wheels probe against an existing venv.
    [switch]$ProbeOnly
)

$ErrorActionPreference = "Stop"
$VenvDir = Join-Path $BabyHome ".venv"
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
# The shell scrapes the LAST "ERROR:" line for the splash, so keep failures on one.
function Write-Fail($m) { Write-Host "ERROR: $m" }

# Reframe a uv failure as a legible, actionable, retryable message -- never a raw
# trace. Proxy needs an explicit proxy signal (a bare "Connect" is a plain DNS/
# network failure, not a proxy problem -- the W0 spike caught that misclassification).
function Resolve-SyncError([string]$capturedErr) {
    $e = ($capturedErr | Out-String)
    if ($e -match "proxy|\b407\b|proxy tunnel|CONNECT tunnel") {
        return "A proxy/firewall looks to be blocking PyPI. Set HTTPS_PROXY (and HTTP_PROXY) to your corporate proxy, then reopen Baby -- setup resumes from where it stopped."
    }
    if ($e -match "getaddrinfo|Temporary failure|dns error|No such host|os error 11001|resolve|Network is unreachable|Connection reset|timed out|timeout|error sending request") {
        return "No internet connection reached PyPI. Reconnect and reopen Baby -- setup resumes from its cache, so nothing already downloaded is lost."
    }
    if ($e -match "No space left|disk full|ENOSPC|not enough space") {
        return "Ran out of disk space building the environment (it needs ~1-1.5 GB). Free a few GB and reopen Baby."
    }
    if ($e -match "hash mismatch|checksum|corrupt") {
        return "A download was corrupted (partial transfer). Reopen Baby -- only the bad file is re-fetched."
    }
    return "Setup failed. Reopen Baby once connected; if it persists, copy the details into an issue. (Setup is safe to re-run -- it resumes.)"
}

# Run uv capturing its stderr for classification. CRITICAL (PS 5.1): a native
# command whose stderr is 2>-redirected throws a terminating NativeCommandError on
# the FIRST stderr line under ErrorActionPreference=Stop -- and uv writes progress to
# stderr on every SUCCESSFUL run, so that would abort the happy path before we ever
# read the exit code. Drop EAP to Continue around the redirected call; we read
# $LASTEXITCODE explicitly, so Stop buys nothing here and only breaks us. Returns the
# exit code; the captured stderr lands in $errFile for Resolve-SyncError.
function Invoke-Uv([string[]]$uvArgs, [string]$errFile) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $UvExe @uvArgs 2> $errFile
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

# The MSVC 2015-2022 x64 runtime that every native wheel dlopens (vcruntime140.dll /
# vcruntime140_1.dll / msvcp140.dll). A clean image may lack it, and without it the
# wheels probe fails with an opaque "DLL load failed". Detect via the load-relevant
# System32 DLLs (matches core.health check_vcredist).
function Test-VCRedist {
    $sys32 = Join-Path $env:SystemRoot "System32"
    foreach ($d in "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll") {
        if (-not (Test-Path (Join-Path $sys32 $d))) { return $false }
    }
    return $true
}

# Install the redist if missing. This is the ONE step of an otherwise no-admin
# per-user install that needs elevation, so it triggers a UAC prompt; a declined
# prompt is reported legibly, not as a raw trace.
function Install-VCRedist {
    if (Test-VCRedist) { Write-Step "Visual C++ runtime present" ; return $true }
    Write-Step "Installing the Visual C++ runtime (Windows will ask for permission)"
    # Unique name so two first-run invocations can't race the same download/exec.
    $exe = Join-Path $env:TEMP ("baby_vc_redist_{0}.exe" -f [guid]::NewGuid())
    try {
        Invoke-WebRequest "https://aka.ms/vs/17/release/vc_redist.x64.exe" -OutFile $exe -TimeoutSec 300
    } catch {
        Write-Fail (Resolve-SyncError $_.Exception.Message)
        return $false
    }
    try {
        $p = Start-Process $exe -ArgumentList "/install", "/quiet", "/norestart" -Verb RunAs -Wait -PassThru
    } catch {
        Remove-Item $exe -ErrorAction SilentlyContinue
        Write-Fail "The Visual C++ runtime is required but the permission prompt was declined. Reopen Baby to try again, or install aka.ms/vs/17/release/vc_redist.x64.exe yourself."
        return $false
    }
    Remove-Item $exe -ErrorAction SilentlyContinue
    # 0 = installed, 3010 = installed (reboot needed), 1638/1641 = newer present / reboot.
    if (($p.ExitCode -in 0, 3010, 1638, 1641) -and (Test-VCRedist)) {
        Write-Step "Visual C++ runtime installed"
        return $true
    }
    Write-Fail "Couldn't install the Visual C++ runtime (exit $($p.ExitCode)). Install aka.ms/vs/17/release/vc_redist.x64.exe manually and reopen Baby."
    return $false
}

if (-not $ProbeOnly) {
    if (-not (Test-Path $BabyHome)) { New-Item -ItemType Directory -Force -Path $BabyHome | Out-Null }

    # Point uv's project environment at the per-user data dir (NOT the read-only
    # install dir). This is the whole trick that makes an installed app's venv work.
    $env:UV_PROJECT_ENVIRONMENT = $VenvDir

    Write-Step "Installing managed Python $PythonVersion (no system Python needed)"
    # This step hits the network (CPython download), so classify its failure too.
    $errFile = New-TemporaryFile
    $code = Invoke-Uv @("python", "install", $PythonVersion) $errFile.FullName
    $capturedErr = Get-Content $errFile.FullName -Raw -ErrorAction SilentlyContinue
    Remove-Item $errFile.FullName -ErrorAction SilentlyContinue
    if ($code -ne 0) { Write-Fail (Resolve-SyncError $capturedErr) ; exit 1 }

    $attempt = 0
    while ($true) {
        $attempt++
        Write-Step "Building the environment (uv sync) -- attempt $attempt/$Retries"
        # Capture stderr so a failure is classified, not spewed raw.
        $errFile = New-TemporaryFile
        $code = Invoke-Uv @("sync", "--frozen", "--project", $SourceDir, "--python", $PythonVersion) $errFile.FullName
        $capturedErr = Get-Content $errFile.FullName -Raw -ErrorAction SilentlyContinue
        Remove-Item $errFile.FullName -ErrorAction SilentlyContinue
        if ($code -eq 0) { break }
        if ($attempt -ge $Retries) { Write-Fail (Resolve-SyncError $capturedErr) ; exit 1 }
        $backoff = [Math]::Min(30, [Math]::Pow(2, $attempt))
        Write-Host "  transient failure -- retrying in ${backoff}s (uv sync resumes from cache)" -ForegroundColor Yellow
        Start-Sleep -Seconds $backoff
    }
    Write-Step "Environment ready at $VenvDir"

    # The native wheels can't load without the MSVC runtime, so ensure it before the
    # probe (a no-op when already present, e.g. every dev box and most Win11 images).
    if (-not (Install-VCRedist)) { exit 1 }
}

if (-not (Test-Path $VenvPy)) {
    Write-Fail "No interpreter at $VenvPy -- run without -ProbeOnly to build it first."
    exit 1
}

# Functional wheels gate: import + a real op per native wheel. NOT the model-load
# checks -- the models aren't downloaded yet (the in-app wizard does that once the
# backend is up). core.health lives under SourceDir, so run from there.
Write-Step "Verifying the engine (functional wheel probe)"
Push-Location $SourceDir
try {
    & $VenvPy -m core.health --level wheels --mode cloud_only
    $probeCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($probeCode -ne 0) {
    Write-Fail "A required component installed but does not load (often the Visual C++ runtime is missing). See the rows above."
    exit $probeCode
}

# Completion sentinel: written ONLY after the wheels probe passes. `uv sync` creates
# the venv scaffolding (Scripts\pythonw.exe) BEFORE installing the ~1.5 GB of deps, so
# the shell must NOT treat pythonw.exe as "done" -- an interrupted sync would leave a
# half-built venv that never resumes. The shell gates on this marker instead.
Set-Content -Path (Join-Path $VenvDir ".baby-ready") -Value "wheels probe passed" -Encoding ascii

Write-Host ""
Write-Host "OK: first-run environment is ready." -ForegroundColor Green
