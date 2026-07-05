<#
.SYNOPSIS
    End-to-end smoke gate for SQLNEW (plan §25.5, Phase 18).

.DESCRIPTION
    Starts a throwaway uvicorn instance, waits for GET /api/health, then exercises the live
    stack: 3 chat turns (2 normal SQL + 1 analytic review, chained in one conversation) and a
    SearxNG research probe. Prints a short report and exits non-zero on any hard failure, so
    it can gate a deploy.

    This is a LIVE test: it uses the configured LLM_BASE_URL / SEARXNG_URL (see .env) and the
    configured embedder. Run it on the deployment machine after `scripts\start.ps1` works.

.PARAMETER Port
    Port for the throwaway server (default 8123, so it won't clash with a running app on 8000).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\smoke.ps1
#>
param(
    [int]$Port = 8123,
    [int]$StartTimeoutSec = 240,
    [int]$ChatTimeoutSec = 180
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "Python venv not found at $Python (create it: python -m venv .venv)"
    exit 1
}

$base = "http://127.0.0.1:$Port"
$fail = 0
$conv = $null

Write-Host "[smoke] starting uvicorn on $base ..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", "$Port") `
    -PassThru -WindowStyle Hidden

function Invoke-Chat([string]$msg) {
    $payload = @{ message = $msg }
    if ($conv) { $payload["conversation_id"] = $conv }
    $body = $payload | ConvertTo-Json
    return Invoke-RestMethod "$base/api/chat" -Method Post -Body $body `
        -ContentType "application/json" -TimeoutSec $ChatTimeoutSec
}

try {
    # 1) wait for health ----------------------------------------------------
    $h = $null
    for ($i = 0; $i -lt $StartTimeoutSec; $i++) {
        try { $h = Invoke-RestMethod "$base/api/health" -TimeoutSec 5; if ($h) { break } }
        catch { Start-Sleep -Seconds 1 }
    }
    if ($null -eq $h) { throw "health endpoint never became ready in ${StartTimeoutSec}s" }
    Write-Host ("[smoke] health OK  llm.reachable={0}  embedder.ok={1}  search.enabled={2} search.reachable={3}" `
        -f $h.llm.reachable, $h.embedder.ok, $h.search.enabled, $h.search.reachable) -ForegroundColor Green
    if (-not $h.llm.reachable) { Write-Warning "LLM not reachable — chat turns will use fallbacks." }

    # 2) three chat turns (chained) ----------------------------------------
    $t1 = Invoke-Chat "Tổng doanh thu năm 2025 là bao nhiêu?"
    $conv = $t1.conversation_id
    Write-Host ("[smoke] turn1  intent={0} needs_sql={1} error={2}" -f $t1.intent, $t1.needs_sql, $t1.error)

    $t2 = Invoke-Chat "Top 5 khách hàng theo doanh thu năm 2025"
    Write-Host ("[smoke] turn2  intent={0} rows={1}" -f $t2.intent, $t2.row_count)

    $t3 = Invoke-Chat "Phân tích vì sao doanh thu tháng 3/2025 giảm?"
    $evCount = 0; if ($t3.evidence) { $evCount = $t3.evidence.Count }
    $srcCount = 0; if ($t3.sources) { $srcCount = $t3.sources.Count }
    Write-Host ("[smoke] turn3  mode={0} review_id={1} evidence={2} sources={3} status={4}" `
        -f $t3.mode, $t3.review_id, $evCount, $srcCount, $t3.analytic_status)
    if (-not $t3.report_markdown) { throw "analytic turn returned no report_markdown" }

    # 3) SearxNG research probe --------------------------------------------
    $rt = Invoke-RestMethod "$base/api/research/test" -Method Post `
        -Body (@{ query = "giá vàng SJC hôm nay" } | ConvertTo-Json) `
        -ContentType "application/json" -TimeoutSec 30
    Write-Host ("[smoke] research  enabled={0} results={1}" -f $rt.enabled, $rt.result_count)
    if ($h.search.enabled -and $h.search.reachable -and $rt.result_count -lt 1) {
        Write-Warning "SearxNG reachable but returned 0 results for the probe query."
    }

    Write-Host "[smoke] PASS" -ForegroundColor Green
}
catch {
    Write-Host "[smoke] FAIL: $_" -ForegroundColor Red
    $fail = 1
}
finally {
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}

exit $fail
