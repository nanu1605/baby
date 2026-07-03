# Baby setup — run from repo root:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# Idempotent. Installs nothing without telling you what and why.

$ErrorActionPreference = "Stop"

Write-Host "== Baby setup ==" -ForegroundColor Cyan

# --- 1. Ollama ---------------------------------------------------------------
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Ollama not found — installing via winget..." -ForegroundColor Yellow
    winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
    # winget updates only the registry PATH; patch this session so ollama resolves now.
    $env:Path = "$env:LOCALAPPDATA\Programs\Ollama;$env:Path"
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Write-Host "Ollama installed but not resolvable yet — open a new terminal and re-run this script." -ForegroundColor Red
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
    Write-Host "Ollama daemon not reachable — starting it..." -ForegroundColor Yellow
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
    try {
        $tags = (Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 5).models.name
    } catch {
        Write-Host "Could not reach Ollama at 127.0.0.1:11434 — launch the Ollama app and re-run." -ForegroundColor Red
        exit 1
    }
}
if ($tags -contains $daily) {
    Write-Host "Daily model: OK ($daily)"
} else {
    Write-Host "Pulling daily model $daily (~6.6 GB)..." -ForegroundColor Yellow
    ollama pull $daily
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Model pull failed — check network and re-run." -ForegroundColor Red
        exit 1
    }
}

# --- 2b. Everything (instant file search) -------------------------------------
if (-not (Get-Command Everything -ErrorAction SilentlyContinue) -and
    -not (Test-Path "$env:ProgramFiles\Everything\Everything.exe") -and
    -not (Test-Path "${env:ProgramFiles(x86)}\Everything\Everything.exe")) {
    Write-Host "Everything not found — installing via winget..." -ForegroundColor Yellow
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
        Write-Host "SDK download failed — file_search will use the scandir fallback index." -ForegroundColor Yellow
    }
} else {
    Write-Host "Everything SDK DLL: OK"
}

# --- 3. uv + Python env ------------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv not found — installing (user-scope)..." -ForegroundColor Yellow
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
    Write-Host "e5 model download failed — memory will be unavailable until it succeeds." -ForegroundColor Yellow
}
uv run python -c "import sqlite3, sqlite_vec; c = sqlite3.connect(':memory:'); c.enable_load_extension(True); c.load_extension(sqlite_vec.loadable_path()); print('sqlite-vec: OK', c.execute('select vec_version()').fetchone()[0])"
if ($LASTEXITCODE -ne 0) {
    Write-Host "sqlite-vec failed to load — memory will fall back to brute-force search." -ForegroundColor Yellow
}

# --- 4. Secrets template -----------------------------------------------------
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created from template — fill in keys as needed (never commit it)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Setup complete. Start Baby with:  uv run python run.py --cli" -ForegroundColor Green
