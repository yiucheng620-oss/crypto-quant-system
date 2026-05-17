#!/bin/bash
# VP_BTC_4h Alert — runs after signal-tracker-4h
export HOME=/home/yiucheng620
cd /home/yiucheng620/workspace
exec /home/yiucheng620/.hermes/hermes-agent/venv/bin/python3 btc_vp_alert.py 2>&1