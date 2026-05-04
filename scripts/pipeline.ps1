# ── Config (edit here) ────────────────────────────────────────────────────────
$CONTACT     = "于凯成"
$GROUP       = "CUA-Lark课题-6"
$PRODUCT     = ""         # leave blank to auto-detect from task; or set: im | docs | calendar | base | vc | mail
$DELAY       = 15         # seconds to wait before GUI actions (switch to Feishu)
$PUBLISH     = $true      # publish Feishu cloud doc report
$SKIP_EVAL   = $false     # skip generate→evaluate loop (single-shot)
$MAX_RETRIES = 3          # max refinement attempts in the loop
# ─────────────────────────────────────────────────────────────────────────────

$ROOT = Split-Path $PSScriptRoot -Parent

if (-not $args[0]) {
    Write-Host "Usage: pipeline.ps1 <task> [--no-publish] [--skip-eval] [--max-retries N]"
    Write-Host ""
    Write-Host "Example:"
    Write-Host "  .\scripts\pipeline.ps1 `"send Hello to contact`""
    exit 0
}

$TASK = $args[0]
for ($i = 1; $i -lt $args.Count; $i++) {
    switch ($args[$i]) {
        "--no-publish"  { $PUBLISH = $false }
        "--skip-eval"   { $SKIP_EVAL = $true }
        "--max-retries" { $MAX_RETRIES = [int]$args[$i + 1]; $i++ }
    }
}

$cmd = "python run_pipeline.py --task `"$TASK`" --contact `"$CONTACT`" --group `"$GROUP`" --delay $DELAY --max-retries $MAX_RETRIES"
if ($PRODUCT) { $cmd += " --product $PRODUCT" }
if ($PUBLISH)   { $cmd += " --publish" }
if ($SKIP_EVAL) { $cmd += " --skip-eval" }

Set-Location $ROOT
Invoke-Expression $cmd
