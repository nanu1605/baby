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

# --- 4. Secrets template -----------------------------------------------------
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created from template — fill in keys as needed (never commit it)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Setup complete. Start Baby with:  uv run python run.py --cli" -ForegroundColor Green
