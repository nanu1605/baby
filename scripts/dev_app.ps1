# scripts/dev_app.ps1 - the v4 native Tauri shell in dev.
#
# The shell window loads the SAME ui/app Vite dev server the browser uses
# (http://localhost:5173, DECISIONS #119); Vite proxies API + /ws to the live
# backend on :8765. So start two things first, each in its own terminal:
#     1) backend:   uv run python run.py --ui
#     2) frontend:  scripts\dev_ui.ps1        (Vite on :5173, HMR)
# then run this script to launch the native window against them.
#
# Prod needs no Node/Vite at runtime: FastAPI serves ui/app/dist and the shell
# attaches to :8765 (wired in V1). This is a DEV convenience only.
$ErrorActionPreference = "Stop"
$shell = Join-Path $PSScriptRoot "..\ui\shell"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Node not found. Run scripts\setup.ps1 first (it installs Node LTS)." -ForegroundColor Red
    exit 1
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Host "Rust/cargo not found. Run scripts\setup.ps1 (it installs the Rust toolchain)," -ForegroundColor Red
    Write-Host "or install manually: winget install Rustlang.Rustup  (+ VS Build Tools, Desktop C++)." -ForegroundColor Red
    exit 1
}

Push-Location $shell
try {
    if (-not (Test-Path "node_modules")) {
        Write-Host "Installing ui/shell dependencies (npm install)..." -ForegroundColor Yellow
        npm install
    }
    Write-Host "Backend must be running:   uv run python run.py --ui" -ForegroundColor Cyan
    Write-Host "Vite must be running:      scripts\dev_ui.ps1   (serves the UI on :5173)" -ForegroundColor Cyan
    Write-Host "Launching the native shell (tauri dev -> :5173)..." -ForegroundColor Green
    npm run dev
} finally {
    Pop-Location
}
