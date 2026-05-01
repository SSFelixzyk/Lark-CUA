# ── 配置（按需修改） ─────────────────────────────────────────────────────────
$CONTACT    = "于凯成"
$GROUP      = "CUA-Lark课题-6"
$DELAY      = 10
# ─────────────────────────────────────────────────────────────────────────────

$ROOT = Split-Path $PSScriptRoot -Parent
Set-Location $ROOT

# 用法示例：
#   .\scripts\benchmark.ps1                          # 运行所有用例
#   .\scripts\benchmark.ps1 --product im             # 只跑 IM
#   .\scripts\benchmark.ps1 --case-id IM_GEN_006     # 跑指定 case
#   .\scripts\benchmark.ps1 --level L1               # 只跑 L1
#   .\scripts\benchmark.ps1 --dry-run                # 不操作屏幕，只打印任务

$extraArgs = $args

python tests/run_benchmark.py `
    --contact "$CONTACT" `
    --group   "$GROUP" `
    --delay   $DELAY `
    @extraArgs
