# ── Config (edit here) ────────────────────────────────────────────────────────
$CONTACT  = "于凯成"
$GROUP    = "CUA-Lark课题-6"
$PRODUCT  = "im"       # im | docs | calendar | base | vc | mail
$DELAY    = 15         # seconds to wait before GUI actions (switch to Feishu)
$PUBLISH  = $true      # publish Feishu cloud doc report
$SKIP_EVAL = $false    # skip DSL quality scoring
# ─────────────────────────────────────────────────────────────────────────────

$ROOT = Split-Path $PSScriptRoot -Parent

if (-not $args[0]) {
    Write-Host "Usage: pipeline.ps1 <task> [--no-publish] [--skip-eval]"
    Write-Host ""
    Write-Host "Example:"
    Write-Host "  .\scripts\pipeline.ps1 `"send Hello to contact`""
    exit 0
}

$TASK = $args[0]
if ($args -contains "--no-publish") { $PUBLISH   = $false }
if ($args -contains "--skip-eval")  { $SKIP_EVAL = $true  }

$cmd = "python run_pipeline.py --task `"$TASK`" --product $PRODUCT --contact `"$CONTACT`" --group `"$GROUP`" --delay $DELAY"
if ($PUBLISH)   { $cmd += " --publish" }
if ($SKIP_EVAL) { $cmd += " --skip-eval" }

Set-Location $ROOT
Invoke-Expression $cmd
