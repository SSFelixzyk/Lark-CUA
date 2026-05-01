# ── Config (edit here) ────────────────────────────────────────────────────────
$CONTACT = "于凯成"
$GROUP   = "CUA-Lark课题-6"
$DELAY   = 10
# ─────────────────────────────────────────────────────────────────────────────

# Usage:
#   .\scripts\benchmark.ps1                        - run all cases
#   .\scripts\benchmark.ps1 --product im           - IM only
#   .\scripts\benchmark.ps1 --case-id IM_GEN_006   - specific case
#   .\scripts\benchmark.ps1 --level L1             - L1 only
#   .\scripts\benchmark.ps1 --dry-run              - print tasks, no screen

$ROOT = Split-Path $PSScriptRoot -Parent
Set-Location $ROOT

$extra = $args -join " "
Invoke-Expression "python tests/run_benchmark.py --contact `"$CONTACT`" --group `"$GROUP`" --delay $DELAY $extra"
