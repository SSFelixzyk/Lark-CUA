# ── Config (edit here) ────────────────────────────────────────────────────────
$PUBLISH     = $true   # publish to Feishu cloud doc
$NO_INSIGHTS = $false  # skip AI analysis (faster)
# ─────────────────────────────────────────────────────────────────────────────

# Usage:
#   .\scripts\report.ps1                                    - use latest result
#   .\scripts\report.ps1 tests/results/benchmark_xxx.json  - specific result
#   .\scripts\report.ps1 --no-insights                      - skip AI analysis
#   .\scripts\report.ps1 --no-publish                       - MD only, no Feishu

$ROOT = Split-Path $PSScriptRoot -Parent
Set-Location $ROOT

if ($args -contains "--no-insights") { $NO_INSIGHTS = $true }
if ($args -contains "--no-publish")  { $PUBLISH = $false }

$resultPath = $args | Where-Object { $_ -like "*.json" } | Select-Object -First 1
if (-not $resultPath) {
    $resultPath = Get-ChildItem "tests/results/benchmark_*.json" |
                  Sort-Object LastWriteTime -Descending |
                  Select-Object -First 1 -ExpandProperty FullName
    if (-not $resultPath) { Write-Host "No results found in tests/results/"; exit 1 }
    Write-Host "[report] Latest: $resultPath"
}

$cmd = "python tools/report_publisher.py `"$resultPath`""
if ($PUBLISH)     { $cmd += " --publish" }
if ($NO_INSIGHTS) { $cmd += " --no-insights" }
Invoke-Expression $cmd
