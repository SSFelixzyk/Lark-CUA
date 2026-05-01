# ── Config (edit here) ────────────────────────────────────────────────────────
$PRODUCT = "im"    # im | docs | calendar | base | vc | mail
# ─────────────────────────────────────────────────────────────────────────────

# Usage:
#   .\scripts\dsl.ps1 "send Hello to contact"
#   .\scripts\dsl.ps1 "create meeting tomorrow 3pm" --product calendar
#   .\scripts\dsl.ps1 "create meeting tomorrow 3pm" --product calendar --evaluate
#   .\scripts\dsl.ps1 eval tests/benchmark/generated/im/IM_GEN_006.yaml

$ROOT = Split-Path $PSScriptRoot -Parent
Set-Location $ROOT

if ($args[0] -eq "eval") {
    $yaml = $args[1]
    if (-not $yaml) { Write-Host "Usage: dsl.ps1 eval <yaml-path>"; exit 1 }
    python -m dsl.evaluator $yaml
    exit
}

if (-not $args[0]) {
    Write-Host "Usage:"
    Write-Host "  dsl.ps1 <description> [--product <p>] [--evaluate]"
    Write-Host "  dsl.ps1 eval <yaml-path>"
    exit 0
}

$DESC = $args[0]
$currentProduct = $PRODUCT
$extra = @()

for ($i = 1; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq "--product" -and ($i + 1) -lt $args.Count) {
        $currentProduct = $args[$i + 1]; $i++
    } else {
        $extra += $args[$i]
    }
}

$extraStr = $extra -join " "
Invoke-Expression "python -m dsl.generator `"$DESC`" --product $currentProduct $extraStr"
