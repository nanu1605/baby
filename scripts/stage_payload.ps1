<#
v6 W1e: stage the backend runtime payload for the installer.

Assembles an ALLOWLISTED copy of exactly what an installed Baby needs to run --
the Python source + shipped data -- into a staging dir that tauri.conf's
`bundle.resources` ships next to baby-shell.exe. An allowlist (not a denylist) is
deliberate for a public build: nothing ships unless it is named here, so a stray
secret, the owner's config.yaml/.env/baby.db, tests, or build junk can never leak
into the installer.

NOT staged (built/fetched at first run): the .venv (uv sync), models/ (downloaded),
and uv.exe unless passed via -UvExe.

Usage:
  powershell -ExecutionPolicy Bypass -File scripts/stage_payload.ps1 [-UvExe path\to\uv.exe]
#>

[CmdletBinding()]
param(
    [string]$Dest = "",
    [string]$UvExe = ""
)

$ErrorActionPreference = "Stop"
# $PSScriptRoot can be empty in a param default under nested invocation; resolve in the body.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir "..")).Path
if (-not $Dest) { $Dest = Join-Path $Root "ui\shell\src-tauri\payload" }

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }

# Fresh staging dir every build.
if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# robocopy succeeds with exit codes 0-7; >=8 is a real error.
function Copy-Tree($src, $dst) {
    $full = Join-Path $Root $src
    if (-not (Test-Path $full)) { throw "missing payload source: $src" }
    robocopy $full (Join-Path $Dest $dst) /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ .pytest_cache /XF *.pyc | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $src (exit $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
}

function Copy-File($rel) {
    Copy-Item (Join-Path $Root $rel) (Join-Path $Dest $rel) -Force
}

Write-Step "Staging runtime payload -> $Dest"

# Top-level entry + dependency manifests (installer bundles uv.lock/pyproject for uv sync).
Copy-File "run.py"
Copy-File "pyproject.toml"
Copy-File "uv.lock"

# Python runtime packages (whole trees, minus caches).
foreach ($pkg in "core", "db", "tools", "memory", "voice", "clients", "workers") {
    Copy-Tree $pkg $pkg
}

# Shipped data: sound/wake assets + the conservative config template + EULA
# (paths._TEMPLATE resolves installer/config.default.yaml relative to the source root).
Copy-Tree "assets" "assets"
Copy-Tree "installer" "installer"

# ui/ is selective: the server + its Python siblings + classic web + the BUILT SPA.
# Excludes ui/shell (the shell's own source) and ui/app/{src,node_modules,configs}.
New-Item -ItemType Directory -Force -Path (Join-Path $Dest "ui") | Out-Null
foreach ($f in "__init__.py", "server.py", "tray.py", "gamewatch.py") {
    Copy-Item (Join-Path $Root "ui\$f") (Join-Path $Dest "ui\$f") -Force
}
Copy-Tree "ui\web" "ui\web"
if (-not (Test-Path (Join-Path $Root "ui\app\dist\index.html"))) {
    throw "ui/app/dist is not built -- run 'npm --prefix ui/app run build' first"
}
Copy-Tree "ui\app\dist" "ui\app\dist"

# Optional: bundle uv.exe so first-run needs no pre-existing Python/uv.
if ($UvExe -and (Test-Path $UvExe)) {
    Copy-Item $UvExe (Join-Path $Dest "uv.exe") -Force
    Write-Step "Included uv.exe"
} else {
    Write-Host "  (uv.exe not bundled -- pass -UvExe to include it)" -ForegroundColor Yellow
}

# Safety net: assert none of the never-ship files slipped in.
foreach ($forbidden in "config.yaml", ".env", "baby.db") {
    $hit = Get-ChildItem -Recurse -Force -File $Dest -Filter $forbidden -ErrorAction SilentlyContinue
    if ($hit) { throw "FORBIDDEN file staged: $($hit.FullName)" }
}

$size = (Get-ChildItem -Recurse -File $Dest | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("OK: payload staged ({0:N1} MB, .venv + models fetched first-run)." -f $size) -ForegroundColor Green
