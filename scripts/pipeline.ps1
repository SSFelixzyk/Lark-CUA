# ── 配置（按需修改） ─────────────────────────────────────────────────────────
$CONTACT  = "于凯成"          # 替换 <<TEST_CONTACT>>
$GROUP    = "CUA-Lark课题-6"  # 替换 <<TEST_GROUP>>
$PRODUCT  = "im"              # im | docs | calendar | base | vc | mail
$DELAY    = 15                # GUI 操作前等待秒数（切换到飞书窗口）
$PUBLISH  = $true             # $true = 发布飞书云文档报告
$SKIP_EVAL = $false           # $true = 跳过 DSL 质量评分
# ─────────────────────────────────────────────────────────────────────────────

$ROOT = Split-Path $PSScriptRoot -Parent

if (-not $args[0]) {
    Write-Host "Usage: pipeline.ps1 <task-description> [--no-publish] [--skip-eval]"
    Write-Host ""
    Write-Host "Example:"
    Write-Host "  .\scripts\pipeline.ps1 `"发送『测试消息』给于凯成`""
    exit 0
}

$TASK = $args[0]
if ($args -contains "--no-publish")  { $PUBLISH   = $false }
if ($args -contains "--skip-eval")   { $SKIP_EVAL = $true  }

$cmd = @(
    "python", "run_pipeline.py",
    "--task",    "`"$TASK`"",
    "--product", $PRODUCT,
    "--contact", "`"$CONTACT`"",
    "--group",   "`"$GROUP`"",
    "--delay",   $DELAY
)
if ($PUBLISH)   { $cmd += "--publish" }
if ($SKIP_EVAL) { $cmd += "--skip-eval" }

Set-Location $ROOT
Invoke-Expression ($cmd -join " ")
