# scripts/dev_ui.ps1 - Vite dev server (HMR) for the v3 "Brain" UI.
#
# Proxies API + WebSocket calls to the live backend on :8765, so the hot-reload
# shell talks to the real Baby. Start the backend separately in another terminal:
#     uv run python run.py --ui
# then open http://127.0.0.1:5173 for hot-reload frontend development.
#
# This is a DEV convenience only. Production serving is the built dist/ served by
# FastAPI at / (ui.frontend: v3) - no Node needed at runtime.
$ErrorActionPreference = "Stop"
$app = Join-Path $PSScriptRoot "..\ui\app"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Node not found. Run scripts\setup.ps1 first (it installs Node LTS)." -ForegroundColor Red
    exit 1
}

Push-Location $app
try {
    if (-not (Test-Path "node_modules")) {
        Write-Host "Installing ui/app dependencies (npm ci)..." -ForegroundColor Yellow
        npm ci
    }
    Write-Host "Vite dev server: http://127.0.0.1:5173  (proxying API + /ws to backend :8765)" -ForegroundColor Green
    Write-Host "Backend must be running:  uv run python run.py --ui" -ForegroundColor Cyan
    npm run dev
} finally {
    Pop-Location
}
