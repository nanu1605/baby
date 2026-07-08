# Baby setup - run from repo root:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# Idempotent. Installs nothing without telling you what and why.

$ErrorActionPreference = "Stop"

Write-Host "== Baby setup ==" -ForegroundColor Cyan

# Load .env into this session so HF_TOKEN etc. reach the python download
# steps below (run.py loads .env itself, but these one-off calls do not).
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^([A-Za-z_][A-Za-z0-9_]*)=(.+)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), "Process")
        }
    }
}

# --- 1. Ollama ---------------------------------------------------------------
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Ollama not found - installing via winget..." -ForegroundColor Yellow
    winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
    # winget updates only the registry PATH; patch this session so ollama resolves now.
    $env:Path = "$env:LOCALAPPDATA\Programs\Ollama;$env:Path"
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Write-Host "Ollama installed but not resolvable yet - open a new terminal and re-run this script." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Ollama: OK ($((Get-Command ollama).Source))"
}

# Ollama runtime tuning (user env vars; take effect on next Ollama restart)
[Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KV_CACHE_TYPE", "q8_0", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "8192", "User")
Write-Host "Ollama env: FLASH_ATTENTION=1, KV_CACHE_TYPE=q8_0, CONTEXT_LENGTH=8192 (restart Ollama to apply)"

# --- 2. Daily model ----------------------------------------------------------
# Model presence is checked over HTTP: parsing `ollama list` output is fragile in
# PowerShell 5.1, where redirected native stderr becomes a terminating error under
# $ErrorActionPreference = "Stop".
$daily = "qwen3.5:9b-q4_K_M"
$tags = $null
try {
    $tags = (Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 5).models.name
} catch {
    Write-Host "Ollama daemon not reachable - starting it..." -ForegroundColor Yellow
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
    try {
        $tags = (Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 5).models.name
    } catch {
        Write-Host "Could not reach Ollama at 127.0.0.1:11434 - launch the Ollama app and re-run." -ForegroundColor Red
        exit 1
    }
}
if ($tags -contains $daily) {
    Write-Host "Daily model: OK ($daily)"
} else {
    Write-Host "Pulling daily model $daily (~6.6 GB)..." -ForegroundColor Yellow
    ollama pull $daily
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Model pull failed - check network and re-run." -ForegroundColor Red
        exit 1
    }
}

# --- 2b. Everything (instant file search) -------------------------------------
if (-not (Get-Command Everything -ErrorAction SilentlyContinue) -and
    -not (Test-Path "$env:ProgramFiles\Everything\Everything.exe") -and
    -not (Test-Path "${env:ProgramFiles(x86)}\Everything\Everything.exe")) {
    Write-Host "Everything not found - installing via winget..." -ForegroundColor Yellow
    winget install --id voidtools.Everything -e --accept-package-agreements --accept-source-agreements
} else {
    Write-Host "Everything: OK"
}

# Everything must run non-elevated for IPC from Baby to work, and should start
# at login so file_search stays instant. HKCU Run key handles both.
Set-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" -Name "Everything" `
    -Value '"C:\Program Files\Everything\Everything.exe" -startup'

# Everything SDK DLL (ships separately from the app; needed for the ctypes IPC).
$babyDir = "$env:LOCALAPPDATA\baby"
$dllPath = "$babyDir\Everything64.dll"
if (-not (Test-Path $dllPath)) {
    Write-Host "Downloading Everything SDK (for Everything64.dll)..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force $babyDir | Out-Null
    $sdkZip = "$env:TEMP\Everything-SDK.zip"
    try {
        Invoke-WebRequest "https://www.voidtools.com/Everything-SDK.zip" -OutFile $sdkZip -TimeoutSec 120
        Expand-Archive $sdkZip -DestinationPath "$env:TEMP\Everything-SDK" -Force
        Copy-Item "$env:TEMP\Everything-SDK\dll\Everything64.dll" $dllPath -Force
        Write-Host "Everything64.dll installed to $dllPath"
    } catch {
        Write-Host "SDK download failed - file_search will use the scandir fallback index." -ForegroundColor Yellow
    }
} else {
    Write-Host "Everything SDK DLL: OK"
}

# --- 3. uv + Python env ------------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv not found - installing (user-scope)..." -ForegroundColor Yellow
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
} else {
    Write-Host "uv: OK ($((Get-Command uv).Source))"
}

Write-Host "Syncing Python environment (.venv)..."
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Host "uv sync failed." -ForegroundColor Red
    exit 1
}

# --- 3b. Memory stack (Phase 2) -----------------------------------------------
# Pre-download the e5 embedding model (~470 MB, one-time) so first boot is fast,
# and smoke-test the sqlite-vec extension load.
Write-Host "Checking embedding model + sqlite-vec..."
uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small', device='cpu'); print('e5 model: OK')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "e5 model download failed - memory will be unavailable until it succeeds." -ForegroundColor Yellow
}
uv run python -c "import sqlite3, sqlite_vec; c = sqlite3.connect(':memory:'); c.enable_load_extension(True); c.load_extension(sqlite_vec.loadable_path()); print('sqlite-vec: OK', c.execute('select vec_version()').fetchone()[0])"
if ($LASTEXITCODE -ne 0) {
    Write-Host "sqlite-vec failed to load - memory will fall back to brute-force search." -ForegroundColor Yellow
}

# --- 3c. Voice stack (Phase 3) --------------------------------------------------
# Kokoro TTS model (onnx, ~310 MB) + voices, openWakeWord feature models,
# Whisper cache warm-up, espeak-ng verification, and the pre-rendered ready cue.
New-Item -ItemType Directory -Force models, assets | Out-Null
$ProgressPreference = 'SilentlyContinue'
$kokoroBase = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
if (-not (Test-Path "models\kokoro-v1.0.onnx")) {
    Write-Host "Downloading Kokoro TTS model (~310 MB)..." -ForegroundColor Yellow
    Invoke-WebRequest "$kokoroBase/kokoro-v1.0.onnx" -OutFile "models\kokoro-v1.0.onnx" -TimeoutSec 600
}
if (-not (Test-Path "models\voices-v1.0.bin")) {
    Invoke-WebRequest "$kokoroBase/voices-v1.0.bin" -OutFile "models\voices-v1.0.bin" -TimeoutSec 300
}
Write-Host "Downloading openWakeWord feature models..."
uv run python -c "import openwakeword.utils; openwakeword.utils.download_models()"
Write-Host "Verifying bundled espeak-ng (espeakng-loader)..."
uv run python -c "import espeakng_loader; print('espeak-ng:', espeakng_loader.get_library_path())"
if ($LASTEXITCODE -ne 0) {
    Write-Host "espeakng-loader failed - install espeak-ng system-wide: winget install eSpeak-NG.eSpeak-NG" -ForegroundColor Yellow
}
Write-Host "Warming faster-whisper model cache (large-v3-turbo, one-time ~1.6 GB)..."
uv run python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')"
if (-not (Test-Path "assets\baby_ready.wav")) {
    Write-Host "Pre-rendering ready cue..."
    uv run python -m voice.tts --prerender "Baby ready" assets\baby_ready.wav
}

# --- 3d. Autonomy stack (Phase 4) ------------------------------------------------
# Playwright Chromium for browser_act and the working directories under
# %LOCALAPPDATA%\baby. (The local heavy model was removed at N5 - the heavy
# tier is z-ai/glm-5.2 on NVIDIA NIM, no local download needed.)
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\baby\browser",
    "$env:LOCALAPPDATA\baby\logs", "$env:LOCALAPPDATA\baby\shots" | Out-Null
Write-Host "Installing Playwright Chromium (~170 MB, one-time)..." -ForegroundColor Yellow
uv run playwright install chromium

# --- 3e. Speaker verification model (Phase 5) ---------------------------------
# CAM++ speaker-embedding onnx (27 MB) from the sherpa-onnx release; the '+'
# characters must be percent-encoded in the URL but not in the filename.
$spkModel = "models\wespeaker_en_voxceleb_CAM++.onnx"
if (-not (Test-Path $spkModel)) {
    Write-Host "Downloading speaker verification model (27 MB)..." -ForegroundColor Yellow
    $spkUrl = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_CAM%2B%2B.onnx"
    try {
        Invoke-WebRequest $spkUrl -OutFile $spkModel -TimeoutSec 300
    } catch {
        Write-Host "speaker model download failed - voice verification stays off until it lands." -ForegroundColor Yellow
    }
} else {
    Write-Host "Speaker verification model: OK"
}
Write-Host "Enroll your voice (one-time, ~2 min):  uv run python scripts\enroll_voice.py"

# --- 3f. Hardware sensors: LibreHardwareMonitor (P1) ---------------------------
# Windows exposes no CPU temperature to psutil (that API is Linux-only), so Baby
# reads LibreHardwareMonitor's Remote Web Server (JSON at http://127.0.0.1:8085/
# data.json; LHM dropped its WMI provider in 0.9.x). Install it and autostart it
# at login; enabling the web server + the kernel driver (run LHM as admin) is left
# to the user in LHM's Options menu. get_sensors degrades to a structured error
# until this is done, so setup never blocks on it.
$lhmPaths = @(
    "$env:ProgramFiles\LibreHardwareMonitor\LibreHardwareMonitor.exe",
    "${env:ProgramFiles(x86)}\LibreHardwareMonitor\LibreHardwareMonitor.exe"
)
$lhmExe = $lhmPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $lhmExe) {
    Write-Host "Installing LibreHardwareMonitor via winget..." -ForegroundColor Yellow
    try {
        winget install --id LibreHardwareMonitor.LibreHardwareMonitor -e `
            --accept-package-agreements --accept-source-agreements
    } catch {
        Write-Host "LibreHardwareMonitor install skipped - CPU temps stay unavailable until it is installed." -ForegroundColor Yellow
    }
    $lhmExe = $lhmPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
} else {
    Write-Host "LibreHardwareMonitor: OK ($lhmExe)"
}
if ($lhmExe) {
    Set-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" -Name "LibreHardwareMonitor" `
        -Value "`"$lhmExe`""
    Write-Host "One-time in LibreHardwareMonitor -> Options: enable 'Run on Windows startup'," -ForegroundColor Cyan
    Write-Host "'Minimize to tray', and 'Remote Web Server' (Run, port 8085); run LHM as admin so temps populate." -ForegroundColor Cyan
}

# --- 3g. v3 "Brain" UI build: Node LTS + Vite build (v3.0.0) ------------------
# ui/app (React + Vite) builds to static files that FastAPI serves at / when
# ui.frontend: v3. Node is a BUILD-time dependency only - production serving is
# static files (no Node at runtime). The classic UI stays at /classic regardless.
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Node LTS via winget (build-time only)..." -ForegroundColor Yellow
    try {
        winget install --id OpenJS.NodeJS.LTS -e `
            --accept-package-agreements --accept-source-agreements
        # winget's PATH edit needs a fresh shell; refresh this session's PATH.
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
    } catch {
        Write-Host "Node install skipped - the v3 UI won't build; classic UI stays at /." -ForegroundColor Yellow
    }
}
if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "Building the v3 Brain UI (npm ci && npm run build)..." -ForegroundColor Yellow
    Push-Location ui\app
    try {
        npm ci
        if ($LASTEXITCODE -eq 0) {
            npm run build
            Write-Host "v3 UI built. Enable with  ui.frontend: v3  in config.yaml" -ForegroundColor Cyan
            Write-Host "(rollback: ui.frontend: classic; /classic is always served)." -ForegroundColor Cyan
        } else {
            Write-Host "npm ci failed - v3 UI not built; classic UI stays available." -ForegroundColor Yellow
        }
    } finally { Pop-Location }
} else {
    Write-Host "Node unavailable - skipping v3 UI build. The classic UI works as before." -ForegroundColor Yellow
}

# --- 4. Secrets template -----------------------------------------------------
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created from template - fill in keys as needed (never commit it)." -ForegroundColor Yellow
}
Write-Host "Telegram (optional): create a bot with @BotFather, put TELEGRAM_BOT_TOKEN and"
Write-Host "TELEGRAM_CHAT_ID (from @userinfobot) in .env, then set telegram.enabled: true."

Write-Host ""
Write-Host "Setup complete. Start Baby with:  uv run python run.py --all" -ForegroundColor Green
Write-Host "Autostart at login (once you're happy): scripts\autostart.ps1" -ForegroundColor Green
