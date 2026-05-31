#!/bin/bash
# 纏論 System — 獨立定時任務 (完全隔離)
# ====================================
# Step 0: Update K-lines (Binance 公共 API)
# Step 1: Scan for 6大買賣點型態 & 採集環境特徵
# Step 2: Track signal lifecycle  
# Step 3: Manage paper trades (動態結構風控)
# Step 4: Run Attribution Analysis (特徵歸因分析)
# Step 5: Export data for other systems
#
# 靜默運行：除非有信號更新、新開平倉或超時平倉。
set -e
export HOME=/home/yiucheng620
VENV_PYTHON=/home/yiucheng620/.hermes/hermes-agent/venv/bin/python3
WORKSPACE=/home/yiucheng620/workspace
RUN_DIR=$WORKSPACE/chanlun_system/run

# Step 0: 更新 K 線數據
$VENV_PYTHON $WORKSPACE/tools/update_phase1_data.py --quiet > /dev/null 2>&1

# Step 1: 執行信號掃描與特徵採集
$VENV_PYTHON $WORKSPACE/tools/chanlun_scanner.py --save > /dev/null 2>&1

# Step 2: 追蹤信號狀態
TRACK_OUT=$($VENV_PYTHON $RUN_DIR/tracker.py --quiet 2>&1)

# Step 3: 虛擬交易與動態結構風控管理
PAPER_OUT=$($VENV_PYTHON $RUN_DIR/paper_tracker.py --quiet 2>&1)

# Step 4: 特徵與因子歸因分析
$VENV_PYTHON $RUN_DIR/attribution_analyst.py > /dev/null 2>&1

# Step 5: 導出結構化數據給其他系統
$VENV_PYTHON $RUN_DIR/data_exporter.py --quiet > /dev/null 2>&1

# 只有在信號或持倉發生變更時，才在終端/Telegram 中廣播
OUTPUT=""
[ -n "$TRACK_OUT" ] && OUTPUT="$TRACK_OUT"
[ -n "$PAPER_OUT" ] && OUTPUT="${OUTPUT}${OUTPUT:+ | }${PAPER_OUT}"
[ -n "$OUTPUT" ] && echo "$OUTPUT"
