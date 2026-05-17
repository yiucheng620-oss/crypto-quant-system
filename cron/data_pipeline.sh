#!/bin/bash
# data_pipeline.sh — 每日數據管線
# 使用 $HOME 而唔係 ~ （cron 環境兼容）

HOME_DIR="$HOME"
# Cron environment fix: use venv python explicitly
PYTHON="$HOME_DIR/.hermes/hermes-agent/venv/bin/python3"
export HOME="$HOME_DIR"

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "🔄 Pipeline Start: $TIMESTAMP"
echo ""

cd "$HOME_DIR" || exit 1

echo "📡 Step 1/2: 市場數據採集..."
$PYTHON "$HOME_DIR/.hermes/scripts/market_data_collector.py"
COLLECTOR_EXIT=$?

echo ""
echo "📊 Step 2/2: 相關性矩陣..."
$PYTHON "$HOME_DIR/.hermes/scripts/correlation_matrix.py"
CORR_EXIT=$?

echo ""
echo "✅ Pipeline Complete: $(date '+%Y-%m-%d %H:%M:%S')"
echo "   Collector exit: $COLLECTOR_EXIT"
echo "   Correlation exit: $CORR_EXIT"

if [ $COLLECTOR_EXIT -ne 0 ] || [ $CORR_EXIT -ne 0 ]; then
    exit 1
fi
exit 0
