#!/usr/bin/env python3
"""
Unified Signal Alert Checker
══════════════════════════════════
Consolidates btc-vp4h, cross-vp4h, btc-vp1h-short, rsi-eth-short
into a single script. Silent if no signal, outputs alert if triggered.

Cron: every 4h, no_agent, deliver: origin

Replaces:
  - btc_vp_alert.sh (VP_BTC_4h_L alert)
  - cross_vp_alert.sh (BTC+ETH VP_4h cross-confirmation)
  - btc_short_alert.sh (VP_BTC_1h_S alert)
  - rsi_eth_short_alert.sh (RSI_ETH_1h_S alert)
"""

import os, sys, sqlite3, time
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
ALERT_WINDOW_HOURS = 8  # Check signals within last 8 hours

# ── Alert definitions ──
ALERTS = {
    "VP_BTC_4h": {
        "indicator": "VP_BTC_4h_L",
        "emoji": "🚨",
        "label": "VP BTC 4h Long",
        "min_wr": 50,
        "note": "80% WFA WR — flagship indicator",
    },
    "VP_BTC_1h_Short": {
        "indicator": "VP_BTC_1h_S",
        "emoji": "🔻",
        "label": "VP BTC 1h Short",
        "min_wr": 45,
        "note": "Short alert — check regime match",
    },
    "RSI_ETH_1h_Short": {
        "indicator": "RSI_ETH_1h_S",
        "emoji": "🔻",
        "label": "RSI ETH 1h Short",
        "min_wr": 60,
        "note": "Top performer — 83.6% WR",
    },
}

def check_recent_signals(indicator, hours=ALERT_WINDOW_HOURS):
    """Check if indicator fired within the last N hours."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int(time.time()) - hours * 3600
    c.execute("""
        SELECT coin, price, direction, created_at 
        FROM signals 
        WHERE indicator = ? AND created_at > ?
        ORDER BY created_at DESC LIMIT 1
    """, (indicator, cutoff))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {
            "coin": row[0],
            "price": row[1],
            "direction": row[2],
            "time": datetime.fromtimestamp(row[3], HKT).strftime("%m/%d %H:%M"),
        }
    return None

def check_cross_vp():
    """Cross-market VP confirmation: BTC + ETH VP_4h both fired within 8h."""
    btc = check_recent_signals("VP_BTC_4h_L")
    eth = check_recent_signals("VP_ETH_4h_L")
    
    if btc and eth:
        return {
            "btc": btc,
            "eth": eth,
        }
    return None

def main():
    now = datetime.now(HKT).strftime("%m/%d %H:%M HKT")
    alerts_triggered = []
    
    # Check individual alerts
    for alert_id, cfg in ALERTS.items():
        sig = check_recent_signals(cfg["indicator"])
        if sig:
            alerts_triggered.append({
                **cfg,
                "signal": sig,
            })
    
    # Check cross-market confirmation
    cross = check_cross_vp()
    if cross:
        alerts_triggered.append({
            "indicator": "CROSS_VP_4h",
            "emoji": "⚡",
            "label": "Cross-Market VP 4h CONFIRMED",
            "min_wr": 70,
            "note": f"BTC={cross['btc']['price']:,.0f} + ETH={cross['eth']['price']:,.0f} — strong signal",
            "signal": {"coin": "BTC+ETH", "price": 0, "direction": "LONG", "time": cross['btc']['time']},
        })
    
    # Silent if nothing triggered
    if not alerts_triggered:
        sys.exit(0)  # Zero output (no byte at all) — cron adds no noise
    
    # Build alert message
    lines = [f"📡 **Signal Alert** — {now}"]
    
    for a in alerts_triggered:
        sig = a["signal"]
        direction = "🔺 LONG" if sig["direction"] == "LONG" else "🔻 SHORT"
        lines.append(f"\n{a['emoji']} **{a['label']}**")
        lines.append(f"  {direction} {sig['coin']} @ ${sig['price']:,.0f} ({sig['time']})")
        lines.append(f"  _{a['note']}_")
    
    lines.append(f"\n⏰ _24h exit rule enforced_")
    
    print("\n".join(lines))

if __name__ == "__main__":
    main()
