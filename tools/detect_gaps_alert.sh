#!/bin/bash
# 🔍 detect_gaps_alert.sh — periodic pipeline gap check
# Cron: every 30 min. Silent when OK, alert when gaps found.

cd /home/yiucheng620/workspace || exit 1

RESULT=$(/home/yiucheng620/.hermes/hermes-agent/venv/bin/python3 tools/detect_gaps.py --quick 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    # All clear — silent
    exit 0
fi

# Gaps found — output details
echo "$RESULT"
exit 1
