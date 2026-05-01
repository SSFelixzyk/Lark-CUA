#!/usr/bin/env bash
# ── 配置（按需修改） ─────────────────────────────────────────────────────────
CONTACT="于凯成"          # 替换 <<TEST_CONTACT>>
GROUP="CUA-Lark课题-6"   # 替换 <<TEST_GROUP>>
PRODUCT="im"              # im | docs | calendar | base | vc | mail
DELAY=15                  # GUI 操作前等待秒数
PUBLISH="--publish"       # 留空 "" 则不发布
SKIP_EVAL=""              # "--skip-eval" 跳过评分
# ─────────────────────────────────────────────────────────────────────────────

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -z "$1" ]; then
    echo "Usage: pipeline.sh <task-description> [--no-publish] [--skip-eval]"
    echo ""
    echo "Example:"
    echo "  bash scripts/pipeline.sh '发送「测试消息」给于凯成'"
    exit 0
fi

TASK="$1"
shift
for arg in "$@"; do
    case "$arg" in
        --no-publish) PUBLISH="" ;;
        --skip-eval)  SKIP_EVAL="--skip-eval" ;;
    esac
done

cd "$ROOT"
python run_pipeline.py \
    --task    "$TASK" \
    --product "$PRODUCT" \
    --contact "$CONTACT" \
    --group   "$GROUP" \
    --delay   "$DELAY" \
    $PUBLISH $SKIP_EVAL
