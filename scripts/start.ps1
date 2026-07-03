<#
.SYNOPSIS
    One-command production start for SQLNEW (Phase 9).

.DESCRIPTION
    Serves the API AND the built React UI from a single uvicorn process:
      1. verifies the Python venv exists,
      2. builds frontend/dist if it is missing (needs npm),
      3. launches uvicorn (backend.app:app), which mounts frontend/dist.

    Open http://localhost:<port>/ for the UI; the API lives under /api.

.PARAMETER Port
    Port to serve on (default 8000).

.PARAMETER SkipBuild
    Skip the frontend build step even if frontend/dist is missing.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#>
param(
    [int]$Port = 8000,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot   # scripts\ -> repo root
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "Python venv not found at $Python. Create it first:`n  python -m venv .venv`n  .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt"
    exit 1
}

$Dist = Join-Path $Root "frontend\dist"
if (-not (Test-Path $Dist) -and -not $SkipBuild) {
    $npm = (Get-Command npm -ErrorAction SilentlyContinue)
    if ($null -eq $npm) {
        Write-Warning "frontend\dist is missing and npm is not on PATH; starting API only. Install Node.js and re-run, or use scripts\dev.ps1 for the Vite dev server."
    } else {
        Write-Host "[start] building frontend (frontend\dist missing) ..." -ForegroundColor Cyan
        Push-Location (Join-Path $Root "frontend")
        try {
            if (-not (Test-Path (Join-Path $Root "frontend\node_modules"))) { npm install }
            npm run build
        } finally {
            Pop-Location
        }
    }
}

Write-Host "[start] uvicorn on http://localhost:$Port  (UI + API)" -ForegroundColor Green
& $Python -m uvicorn backend.app:app --host 0.0.0.0 --port $Port
