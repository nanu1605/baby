<#
W0 backend-delivery spike - bundled-uv first-run harness.

Proves the v6 backend-delivery path on a machine with NO pre-existing Python and
NO uv: the installer bundles a tiny uv.exe + pinned pyproject.toml + uv.lock, and
first-run stands up a working venv in the per-user data dir, then runs the
functional health probe.

Flow:
  1. bundled uv installs a managed CPython           (no system Python needed)
  2. uv sync builds %LOCALAPPDATA%\baby\.venv         (from the pinned lock)
  3. functional health probe                          (import + real op per wheel)
  4. (optional) launch run.py --all with BABY_HOME     (-Launch)

Failure UX (the risky bit the spike must prove, not the happy path): a dropped
network / partial download / corporate proxy must surface a LEGIBLE, RETRYABLE
message - never a raw stack trace. `uv sync` is itself resumable (it continues
from its cache on re-run), so the harness retries with backoff and, on final
failure, classifies the cause and tells the user exactly how to recover. This
logic ports directly into W3's progress + retry orchestration.

Out of scope (recorded in DECISIONS): offline-first-install. The web-installer
requires first-run network by design; `uv sync` pulls wheels from PyPI.

Usage (clean VM):
  powershell -ExecutionPolicy Bypass -File first_run.ps1 `
      -UvExe .\uv.exe -SourceDir . -PythonVersion 3.13 -Launch

Dev-box smoke (proves the invocation without a multi-GB re-download):
  first_run.ps1 -SourceDir S:\Projects\Baby-assistant `
      -BabyHome S:\Projects\Baby-assistant -ProbeOnly
#>

[CmdletBinding()]
param(
    # Path to the bundled uv.exe. Falls back to uv on PATH (dev box).
    [string]$UvExe = "uv",
    # Where pyproject.toml + uv.lock live (the bundled app payload).
    [string]$SourceDir = ".",
    # Per-user writable home; the venv lands at $BabyHome\.venv.
    [string]$BabyHome = (Join-Path $env:LOCALAPPDATA "baby"),
    # Managed CPython to install (matches requires-python >=3.11; lock resolved on 3.13).
    [string]$PythonVersion = "3.13",
    [int]$Retries = 3,
    # Skip python-install + sync; just run the probe against an existing venv.
    [switch]$ProbeOnly,
    # After a green probe, launch the backend (run.py --all) under BABY_HOME.
    [switch]$Launch
)

$ErrorActionPreference = "Stop"
$VenvDir = Join-Path $BabyHome ".venv"
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
$Probe = Join-Path $PSScriptRoot "health_probe.py"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red }

# Reframe a uv failure as a legible, actionable, retryable message. Never dump a
# raw trace at an end user; classify by the shape of the captured stderr.
function Resolve-SyncError([string]$capturedErr) {
    $e = ($capturedErr | Out-String)
    # Proxy must require an explicit proxy signal. Do NOT match a bare "Connect" /
    # "CONNECT" token: uv's reqwest error chain says "client error (Connect)" for an
    # ordinary DNS/network failure, which would misclassify no-internet as a proxy
    # problem and send the user down a wrong dead-end (caught in the W0 spike).
    if ($e -match "proxy|\b407\b|proxy tunnel|CONNECT tunnel") {
        return "Looks like a proxy/firewall is blocking PyPI. Set HTTPS_PROXY (and HTTP_PROXY) to your corporate proxy, then retry - the download resumes from where it stopped."
    }
    if ($e -match "getaddrinfo|Temporary failure|dns error|No such host|os error 11001|resolve|Network is unreachable|Connection reset|timed out|timeout|error sending request") {
        return "No internet connection reached PyPI. Reconnect and retry - 'uv sync' resumes from its cache, so nothing already downloaded is lost."
    }
    if ($e -match "No space left|disk full|ENOSPC|not enough space") {
        return "Ran out of disk space building the environment. Free a few GB and retry (the Python env is ~1-1.5 GB before models)."
    }
    if ($e -match "hash mismatch|checksum|corrupt") {
        return "A download was corrupted (partial/interrupted transfer). Retry - uv re-fetches only the bad artifact."
    }
    return "Environment setup failed. Retry once connected; if it persists, copy the last few lines above into an issue. (uv sync is safe to re-run - it resumes.)"
}

if (-not $ProbeOnly) {
    if (-not (Test-Path $BabyHome)) { New-Item -ItemType Directory -Force -Path $BabyHome | Out-Null }

    # Point uv's project environment at the per-user data dir (not the read-only
    # install dir). This is the whole trick that makes an installed app work.
    $env:UV_PROJECT_ENVIRONMENT = $VenvDir

    Write-Step "Installing managed CPython $PythonVersion (no system Python required)"
    & $UvExe python install $PythonVersion
    if ($LASTEXITCODE -ne 0) { Write-Fail (Resolve-SyncError "python install failed"); exit 1 }

    $attempt = 0
    while ($true) {
        $attempt++
        Write-Step "Building environment (uv sync) - attempt $attempt/$Retries"
        # Capture stderr so a failure is classified, not spewed raw.
        $errFile = New-TemporaryFile
        & $UvExe sync --frozen --project $SourceDir --python $PythonVersion 2> $errFile.FullName
        $code = $LASTEXITCODE
        $capturedErr = Get-Content $errFile.FullName -Raw -ErrorAction SilentlyContinue
        Remove-Item $errFile.FullName -ErrorAction SilentlyContinue
        if ($code -eq 0) { break }
        if ($attempt -ge $Retries) {
            Write-Fail (Resolve-SyncError $capturedErr)
            exit 1
        }
        $backoff = [Math]::Min(30, [Math]::Pow(2, $attempt))
        Write-Host "  transient failure - retrying in ${backoff}s (uv sync resumes from cache)" -ForegroundColor Yellow
        Start-Sleep -Seconds $backoff
    }
    Write-Step "Environment ready at $VenvDir"
}

if (-not (Test-Path $VenvPy)) {
    Write-Fail "No interpreter at $VenvPy - run without -ProbeOnly to build it first."
    exit 1
}

Write-Step "Functional health probe (import + real op per native wheel)"
& $VenvPy $Probe --browser
$probeCode = $LASTEXITCODE
if ($probeCode -ne 0) {
    Write-Fail "A required dependency installed but does not function. See the probe rows above - this is exactly the 'pip succeeded but the wheel is broken' case the probe guards against."
    exit $probeCode
}

if ($Launch) {
    Write-Step "Launching backend (run.py --all) with BABY_HOME=$BabyHome"
    $env:BABY_HOME = $BabyHome
    & (Join-Path $VenvDir "Scripts\pythonw.exe") (Join-Path $SourceDir "run.py") --all
}

Write-Host ""
Write-Host "OK: bundled-uv first-run stood up a functional backend environment." -ForegroundColor Green
