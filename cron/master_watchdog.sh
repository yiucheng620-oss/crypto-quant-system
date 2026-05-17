#!/bin/bash
# master_watchdog.sh — 全系統守護：check 關鍵 process + data freshness
# Silent unless something is broken
export HOME=/home/yiucheng620

LOG="$HOME/market_data/master_watchdog.log"
ALERT=0

# 1. Vertex Proxy (systemd managed, just health check)
if ! curl -s http://localhost:8899/v1/models > /dev/null 2>&1; then
    echo "$(date) ❌ Vertex Proxy 冇回應 — 嘗試 restart" >> "$LOG"
    systemctl --user restart vertex-proxy 2>/dev/null || true
    ALERT=1
fi

# 2. Data pipeline health — check phase1/ NPZ freshness
# Alert if ALL major NPZ files are older than 25h
DATA_DIR="$HOME/market_data/phase1"
STALE=0
for COIN in BTC ETH SOL; do
    for TF in 1h 4h; do
        AGE=$(( $(date +%s) - $(stat -c %Y "$DATA_DIR/${COIN}_${TF}.npz" 2>/dev/null || echo 0) ))
        if [ "$AGE" -gt 90000 ]; then  # 25h = 90000s
            STALE=$((STALE + 1))
        fi
    done
done
if [ "$STALE" -ge 3 ]; then
    echo "$(date) ⚠️ Phase1 data stale (${STALE}/6 files >25h)" >> "$LOG"
    ALERT=1
fi

# Only print if something is broken (cron delivers stdout)
if [ "$ALERT" -eq 1 ]; then
    echo "🩺 Master Watchdog Alert — $(date)"
    tail -5 "$LOG"
fi