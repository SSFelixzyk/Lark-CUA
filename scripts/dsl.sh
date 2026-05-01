#!/usr/bin/env bash
# ── 配置（按需修改） ─────────────────────────────────────────────────────────
PRODUCT="im"    # im | docs | calendar | base | vc | mail
# ─────────────────────────────────────────────────────────────────────────────

# 用法：
#   bash scripts/dsl.sh "向于凯成发送消息「Hello」"
#   bash scripts/dsl.sh "创建明天下午3点的会议" --product calendar
#   bash scripts/dsl.sh "新建云文档" --product docs --evaluate
#   bash scripts/dsl.sh eval tests/benchmark/generated/im/IM_GEN_006.yaml

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ "$1" = "eval" ]; then
    [ -z "$2" ] && echo "Usage: dsl.sh eval <yaml-path>" && exit 1
    python -m dsl.evaluator "$2"
    exit
fi

if [ -z "$1" ]; then
    echo "Usage:"
    echo "  dsl.sh <description> [--product <product>] [--evaluate]"
    echo "  dsl.sh eval <yaml-path>"
    exit 0
fi

DESC="$1"; shift

# Override PRODUCT if --product is in args
ARGS=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --product) PRODUCT="$2"; shift 2 ;;
        *)         ARGS+=("$1"); shift ;;
    esac
done

python -m dsl.generator "$DESC" --product "$PRODUCT" "${ARGS[@]}"
