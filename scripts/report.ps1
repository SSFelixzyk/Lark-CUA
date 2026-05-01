# ── 配置（按需修改） ─────────────────────────────────────────────────────────
$PUBLISH     = $true   # $true = 发布飞书云文档
$NO_INSIGHTS = $false  # $true = 跳过 AI 分析（更快）
# ─────────────────────────────────────────────────────────────────────────────

$ROOT = Split-Path $PSScriptRoot -Parent
Set-Location $ROOT

# 用法：
#   .\scripts\report.ps1                                   # 自动取最新结果
#   .\scripts\report.ps1 tests/results/benchmark_xxx.json # 指定结果文件
#   .\scripts\report.ps1 --no-insights                    # 跳过 AI 分析

if ($args -contains "--no-insights") { $NO_INSIGHTS = $true }
if ($args -contains "--no-publish")  { $PUBLISH = $false }

# Find result JSON: explicit path, or latest in tests/results/
$resultPath = $args | Where-Object { $_ -like "*.json" } | Select-Object -First 1
if (-not $resultPath) {
    $resultPath = Get-ChildItem "tests/results/benchmark_*.json" |
                  Sort-Object LastWriteTime -Descending |
                  Select-Object -First 1 -ExpandProperty FullName
    if (-not $resultPath) { Write-Host "No benchmark results found in tests/results/"; exit 1 }
    Write-Host "[report] Using latest result: $resultPath"
}

$cmd = @("python", "tools/report_publisher.py", "`"$resultPath`"")
if ($PUBLISH)     { $cmd += "--publish" }
if ($NO_INSIGHTS) { $cmd += "--no-insights" }

Invoke-Expression ($cmd -join " ")
