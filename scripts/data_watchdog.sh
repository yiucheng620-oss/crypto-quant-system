#!/bin/bash
# data_watchdog.sh — 檢查數據管線健康狀態
# 如果超過 25 小時冇新數據 → 緊急警報
# 
# ⚠️ 使用 ps aux (NOT pgrep -f) 做可靠檢測

DATA_DIR="$HOME/market_data/daily"
THRESHOLD_HOURS=25
NOW=$(date +%s)

# Find latest daily directory
LATEST_DIR=$(ls -dt "$DATA_DIR"/*/ 2>/dev/null | head -1)

if [ -z "$LATEST_DIR" ]; then
    echo "❌❌❌ 緊急：冇任何數據目錄！數據管線可能從未運行！"
    exit 1
fi

# Get directory modification time
DIR_TIME=$(stat -c %Y "$LATEST_DIR" 2>/dev/null)
if [ -z "$DIR_TIME" ]; then
    echo "❌ 無法讀取目錄時間"
    exit 1
fi

AGE_SECONDS=$((NOW - DIR_TIME))
AGE_HOURS=$((AGE_SECONDS / 3600))

LATEST_NAME=$(basename "$LATEST_DIR")

echo "📡 Data Watchdog — $(date '+%Y-%m-%d %H:%M:%S') HKT"
echo "   最新數據: $LATEST_NAME (${AGE_HOURS}小時前)"

if [ "$AGE_HOURS" -gt "$THRESHOLD_HOURS" ]; then
    echo "❌❌❌ 數據管線停工！最新數據 ${AGE_HOURS} 小時前（閾值: ${THRESHOLD_HOURS}h）"
    
    # Check if pipeline process exists
    PIPELINE_CHECK=$(ps aux | grep -E "data_pipeline|market_data_collector|correlation_matrix" | grep -v grep)
    if [ -z "$PIPELINE_CHECK" ]; then
        echo "   ⚠️ 管線進程不存在 → 需要人手檢查 cron job"
    else
        echo "   ℹ️ 管線進程存在但冇產出新數據"
    fi
    exit 1
else
    echo "✅ 數據管線正常"
    
    # Quick check: does summary file exist?
    SUMMARY="$LATEST_DIR/_summary.txt"
    if [ -f "$SUMMARY" ]; then
        echo "   📋 摘要存在: $(head -1 "$SUMMARY")"
    else
        echo "   ⚠️ 摘要檔案缺失"
    fi
    exit 0
fi
