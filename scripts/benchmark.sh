#!/usr/bin/env bash
# ── 配置（按需修改） ─────────────────────────────────────────────────────────
CONTACT="于凯成"
GROUP="CUA-Lark课题-6"
DELAY=10
# ─────────────────────────────────────────────────────────────────────────────

# 用法示例：
#   bash scripts/benchmark.sh                       # 运行所有用例
#   bash scripts/benchmark.sh --product im          # 只跑 IM
#   bash scripts/benchmark.sh --case-id IM_GEN_006  # 跑指定 case
#   bash scripts/benchmark.sh --level L1            # 只跑 L1
#   bash scripts/benchmark.sh --dry-run             # 不操作屏幕

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python tests/run_benchmark.py \
    --contact "$CONTACT" \
    --group   "$GROUP" \
    --delay   "$DELAY" \
    "$@"
