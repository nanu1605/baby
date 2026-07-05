# Baby setup - run from repo root:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# Idempotent. Installs nothing without telling you what and why.

$ErrorActionPreference = "Stop"

Write-Host "== Baby setup ==" -ForegroundColor Cyan

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
# Playwright Chromium for browser_act, the heavy escalation model, and the
# working directories under %LOCALAPPDATA%\baby.
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\baby\browser",
    "$env:LOCALAPPDATA\baby\logs", "$env:LOCALAPPDATA\baby\shots" | Out-Null
Write-Host "Installing Playwright Chromium (~170 MB, one-time)..." -ForegroundColor Yellow
uv run playwright install chromium
$heavyTag = "qwen3.6:35b-a3b"
$tags = (Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 5).models.name
if ($tags -notcontains $heavyTag) {
    Write-Host "Pulling heavy model $heavyTag (~24 GB download - needs >22 GB FREE RAM to run;" -ForegroundColor Yellow
    Write-Host "escalation falls back to the Gemini cloud tier whenever RAM is short)." -ForegroundColor Yellow
    ollama pull $heavyTag
}

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
