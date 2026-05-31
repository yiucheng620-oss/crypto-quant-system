#!/bin/bash
# data_pipeline.sh — 每日數據管線
# 1. 採集市場數據
# 2. 計算相關性矩陣
# 3. 產出摘要

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "🔄 Pipeline Start: $TIMESTAMP"
echo ""

# Activate the Hermes venv (where yfinance is installed)
# Use system python3 which has yfinance
cd ~/

echo "📡 Step 1/2: 市場數據採集..."
python3 ~/market_data_collector.py
COLLECTOR_EXIT=$?

echo ""
echo "📊 Step 2/2: 相關性矩陣..."
python3 ~/correlation_matrix.py
CORR_EXIT=$?

echo ""
echo "✅ Pipeline Complete: $(date '+%Y-%m-%d %H:%M:%S')"
echo "   Collector exit: $COLLECTOR_EXIT"
echo "   Correlation exit: $CORR_EXIT"

# Return non-zero if either failed
if [ $COLLECTOR_EXIT -ne 0 ] || [ $CORR_EXIT -ne 0 ]; then
    exit 1
fi
exit 0
