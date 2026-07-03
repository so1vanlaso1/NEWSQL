<#
.SYNOPSIS
    Development mode for SQLNEW (Phase 9): API with autoreload + Vite dev server.

.DESCRIPTION
    Starts uvicorn (backend.app:app --reload) in a separate window and runs the Vite
    dev server (npm run dev) in this one. Vite proxies /api to the backend, so open the
    Vite URL (usually http://localhost:5173/) for a hot-reloading UI.

.PARAMETER Port
    Backend port (default 8000).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
#>
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "Python venv not found at $Python. Create it: python -m venv .venv"
    exit 1
}

# Backend (autoreload) in its own window so its logs stay readable.
Write-Host "[dev] starting backend (uvicorn --reload) on port $Port ..." -ForegroundColor Cyan
$env:LOG_FORMAT = "console"
Start-Process -FilePath $Python `
    -ArgumentList "-m", "uvicorn", "backend.app:app", "--reload", "--port", "$Port" `
    -WorkingDirectory $Root

$npm = (Get-Command npm -ErrorAction SilentlyContinue)
if ($null -eq $npm) {
    Write-Error "npm is not on PATH. Install Node.js to run the Vite dev server."
    exit 1
}

Push-Location (Join-Path $Root "frontend")
try {
    if (-not (Test-Path (Join-Path $Root "frontend\node_modules"))) { npm install }
    Write-Host "[dev] starting Vite dev server (proxies /api -> :$Port) ..." -ForegroundColor Green
    npm run dev
} finally {
    Pop-Location
}
