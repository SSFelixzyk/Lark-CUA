#!/usr/bin/env bash
# ── 配置（按需修改） ─────────────────────────────────────────────────────────
PUBLISH="--publish"    # 留空 "" 则不发布
NO_INSIGHTS=""         # "--no-insights" 跳过 AI 分析（更快）
# ─────────────────────────────────────────────────────────────────────────────

# 用法：
#   bash scripts/report.sh                                    # 自动取最新结果
#   bash scripts/report.sh tests/results/benchmark_xxx.json  # 指定结果文件
#   bash scripts/report.sh --no-insights                      # 跳过 AI 分析
#   bash scripts/report.sh --no-publish                       # 只生成 MD

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RESULT_PATH=""
for arg in "$@"; do
    case "$arg" in
        --no-publish)  PUBLISH="" ;;
        --no-insights) NO_INSIGHTS="--no-insights" ;;
        *.json)        RESULT_PATH="$arg" ;;
    esac
done

# Auto-pick latest if not specified
if [ -z "$RESULT_PATH" ]; then
    RESULT_PATH=$(ls -t tests/results/benchmark_*.json 2>/dev/null | head -1)
    [ -z "$RESULT_PATH" ] && echo "No benchmark results found in tests/results/" && exit 1
    echo "[report] Using latest result: $RESULT_PATH"
fi

python tools/report_publisher.py "$RESULT_PATH" $PUBLISH $NO_INSIGHTS
