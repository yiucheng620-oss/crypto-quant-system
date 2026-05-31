#!/usr/bin/env python3
"""
🌊 Macro Pulse — 每 4 小時快掃全球 macro 狀況
==============================================
用 Gemini 3.5 Flash (free) 快速判斷：
  - Risk-on / Risk-off / Neutral
  - 關鍵 catalyst
  - 需要 alert 嘅異常狀況

Cron: 0 */4 * * * (同其他 4h cron job 一齊)
輸出: scalp_lab/engines/macro_pulse.json + TG (if anomaly)
"""

import json
import os
import sys
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENGINES_DIR = os.path.join(WORKSPACE, "scalp_engines")
PULSE_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
PULSE_FILE = os.path.join(PULSE_DIR, "macro_pulse.json")
os.makedirs(PULSE_DIR, exist_ok=True)

sys.path.insert(0, ENGINES_DIR)

try:
    from gemini_free import call_flash_search
    from tg_notify import tg_send
except ImportError as e:
    print(f"[macro_pulse] Import error: {e}")
    sys.exit(1)


# ═══════════════════════════════════════
# Gemini 3.5 Flash + Google Search — One-shot macro pulse
# ═══════════════════════════════════════

def get_macro_pulse() -> dict:
    """Use Gemini 3.5 Flash with Google Search to get real-time macro data + analysis."""
    now_hkt = datetime.now(HKT)
    
    prompt = f"""Current time: {now_hkt.strftime('%H:%M HKT, %Y-%m-%d')}.

Use Google Search to find the LATEST values for these indicators, then analyze:

1. VIX (CBOE Volatility Index)
2. DXY (US Dollar Index)
3. 10-Year US Treasury Yield
4. Gold price (XAUUSD)
5. S&P 500 (SPX or SPY)
6. Bitcoin price (BTC)
7. Ethereum price (ETH)

Quick macro pulse check. Answer in this exact format:

REGIME: [risk-on / risk-off / neutral]
VIX: [value] | DXY: [value] | 10Y: [value]%
SPX: [value] | Gold: $[value] | BTC: $[value] | ETH: $[value]

ANOMALIES: [any unusual divergence, spike, or decoupling — one line]

ALERT: [none / watch / warning]

Be extremely concise. Use Traditional Chinese for ANOMALIES and ALERT only."""

    try:
        result = call_flash_search(prompt, max_tokens=8192, temperature=0.7)
        text = result.get("text", "") if isinstance(result, dict) else result
        sources = result.get("sources", []) if isinstance(result, dict) else []
        
        return {
            "timestamp": now_hkt.isoformat(),
            "analysis": text.strip(),
            "sources": sources[:5],
        }
    except Exception as e:
        return {
            "timestamp": now_hkt.isoformat(),
            "analysis": f"[Gemini error: {e}]",
            "sources": [],
            "error": str(e),
        }


# ═══════════════════════════════════════
# Main
# ═══════════════════════════════════════

def main():
    now = datetime.now(HKT)
    print(f"[{now.strftime('%H:%M HKT')}] 🌊 Macro Pulse — Gemini 3.5 Flash + Google Search...")

    result = get_macro_pulse()
    analysis = result.get("analysis", "")
    sources = result.get("sources", [])

    # Save
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PULSE_FILE), suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(result, f, indent=2, default=str)
    os.replace(tmp, PULSE_FILE)

    # Check for alerts
    alert_triggers = ["warning", "risk-off", "spike", "decoupling", "divergence"]
    is_alert = any(t in analysis.lower() for t in alert_triggers)

    if is_alert:
        msg = f"⚠️ *Macro Alert* [{now.strftime('%H:%M HKT')}]\n{analysis}"
        try:
            tg_send(msg)
            print(f"  🚨 Alert sent to TG")
        except Exception as e:
            print(f"  ⚠️ TG send failed: {e}")
    else:
        print(f"  ✅ Regime stable, no alert")

    print(f"  Analysis: {analysis[:200]}...")
    if sources:
        print(f"  📎 Sources: {len(sources)}")
        for s in sources[:3]:
            print(f"     - {s.get('title','?')[:60]}")
    print(f"[{now.strftime('%H:%M HKT')}] Done.")


if __name__ == "__main__":
    main()
