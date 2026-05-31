#!/bin/bash
# Smart Money Daily Snapshot — pull fresh + save to history
# DESIGNED FOR CRON (no_agent=true): silent on success, error msg on failure
set -e
export HOME=/home/yiucheng620
VENV_PYTHON=/home/yiucheng620/.hermes/hermes-agent/venv/bin/python3
WORKSPACE=/home/yiucheng620/workspace
LOG=/home/yiucheng620/workspace/data/smart_money/history/snapshot.log

# Pull fresh Smart Money data (silent)
$VENV_PYTHON $WORKSPACE/collectors/smart_money_collector.py --json >> $LOG 2>&1

# Save snapshot to history
$VENV_PYTHON $WORKSPACE/tools/save_smart_money_history.py >> $LOG 2>&1

# Count history files — ONLY print if < 1 (error)
COUNT=$(ls $WORKSPACE/data/smart_money/history/sm_*.json 2>/dev/null | wc -l)
if [ "$COUNT" -lt 1 ]; then
    echo "❌ SMART MONEY SNAPSHOT FAILED: 0 history files"
    exit 1
fi

# Success = silent (empty stdout → no delivery)
