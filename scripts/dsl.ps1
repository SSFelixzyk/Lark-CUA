# ── 配置（按需修改） ─────────────────────────────────────────────────────────
$PRODUCT = "im"     # im | docs | calendar | base | vc | mail
# ─────────────────────────────────────────────────────────────────────────────

$ROOT = Split-Path $PSScriptRoot -Parent
Set-Location $ROOT

# 用法：
#   .\scripts\dsl.ps1 "向于凯成发送消息「Hello」"
#   .\scripts\dsl.ps1 "创建明天下午3点的会议" --product calendar
#   .\scripts\dsl.ps1 "新建云文档" --product docs --evaluate
#   .\scripts\dsl.ps1 eval tests/benchmark/generated/im/IM_GEN_006.yaml   # 只评分

if ($args[0] -eq "eval") {
    $yamlPath = $args[1]
    if (-not $yamlPath) { Write-Host "Usage: dsl.ps1 eval <yaml-path>"; exit 1 }
    python -m dsl.evaluator $yamlPath
    exit
}

if (-not $args[0]) {
    Write-Host "Usage:"
    Write-Host "  dsl.ps1 <description> [--product <product>] [--evaluate]"
    Write-Host "  dsl.ps1 eval <yaml-path>"
    exit 0
}

$DESC = $args[0]
$extraArgs = @()
$currentProduct = $PRODUCT
for ($i = 1; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq "--product" -and $i+1 -lt $args.Count) {
        $currentProduct = $args[$i+1]; $i++
    } else {
        $extraArgs += $args[$i]
    }
}

python -m dsl.generator "$DESC" --product $currentProduct @extraArgs
