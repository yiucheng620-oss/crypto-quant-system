#!/usr/bin/env python3
"""
Positioning & Sentiment Data Collector — Binance Public API (FREE)
===================================================================
Pull funding rate, open interest, long/short ratio, taker buy/sell.
No API key needed. Saves to ~/workspace/market_data/positioning/

Modes:
  (default)   → Full dashboard print + save CSV
  --silent    → Save CSV only, print nothing unless EXTREME alert fires
  --json      → Output signals as JSON (for pipeline)
  --once      → Print dashboard once (explicit)
"""

import sys
import os
import json
import csv
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

HKT = timezone(timedelta(hours=8))
DATA_DIR = Path(os.path.expanduser("~/workspace/market_data/positioning"))
CSV_PATH = DATA_DIR / "positioning_history.csv"

# ═══════════════════════════════════════
# Binance Public Endpoints (no auth)
# ═══════════════════════════════════════
FUNDING_RATE_URL = "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=10"
OPEN_INTEREST_URL = "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT"
LS_RATIO_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=10"
TAKER_LS_URL = "https://fapi.binance.com/futures/data/takerlongshortRatio?symbol=BTCUSDT&period=5m&limit=10"


def fetch_json(url, name):
    """Fetch JSON from Binance public API with retry."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
    return None


def get_previous_oi():
    """Read last OI value from CSV for delta computation."""
    if not CSV_PATH.exists():
        return None
    try:
        with open(CSV_PATH) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if rows:
                return float(rows[-1].get("open_interest", 0))
    except:
        pass
    return None


def compute_signals(funding_rates, oi_data, ls_ratios, taker_ratios):
    """Compute positioning signals from raw data."""
    signals = {}
    
    # ── Funding Rate Signal ──
    if funding_rates and len(funding_rates) > 1:
        latest_fr = float(funding_rates[-1]["fundingRate"])
        prev_fr = float(funding_rates[-2]["fundingRate"])
        avg_fr = sum(float(f["fundingRate"]) for f in funding_rates) / len(funding_rates)
        
        signals["funding_rate"] = round(latest_fr * 100, 4)  # as %
        signals["funding_rate_annual"] = round(latest_fr * 3 * 365 * 100, 2)
        signals["funding_rate_avg_80h"] = round(avg_fr * 100, 4)
        signals["funding_trend"] = "rising" if latest_fr > prev_fr else "falling"
        
        # Extreme positioning alerts
        if latest_fr > 0.001:  # >0.1% per 8h → overheated longs
            signals["positioning_alert"] = "⚠️ OVERHEATED LONGS"
        elif latest_fr < -0.001:  # <-0.1% per 8h → crowded shorts
            signals["positioning_alert"] = "⚠️ CROWDED SHORTS"
        else:
            signals["positioning_alert"] = "neutral"
    
    # ── Open Interest Signal ──
    if oi_data:
        current_oi = float(oi_data["openInterest"])
        signals["open_interest"] = round(current_oi, 0)
        
        # OI Delta from last reading
        prev_oi = get_previous_oi()
        if prev_oi and prev_oi > 0:
            oi_delta_pct = ((current_oi - prev_oi) / prev_oi) * 100
            signals["oi_delta_pct"] = round(oi_delta_pct, 3)
            if abs(oi_delta_pct) > 2:
                direction = "INCREASE" if oi_delta_pct > 0 else "DECREASE"
                signals["oi_alert"] = f"⚠️ OI {direction}: {oi_delta_pct:+.1f}% in 1h"
            else:
                signals["oi_alert"] = "normal"
        else:
            signals["oi_delta_pct"] = 0
            signals["oi_alert"] = "normal"
    
    # ── Long/Short Ratio Signal ──
    if ls_ratios and len(ls_ratios) > 1:
        latest_ls = float(ls_ratios[-1]["longShortRatio"])
        signals["long_short_ratio"] = round(latest_ls, 3)
        
        if latest_ls > 2.0:
            signals["ls_signal"] = "🐻 bearish contrarian"
        elif latest_ls < 0.7:
            signals["ls_signal"] = "🐂 bullish contrarian"
        else:
            signals["ls_signal"] = "neutral"
        
        trend = sum(1 for i in range(1, len(ls_ratios)) 
                    if float(ls_ratios[i]["longShortRatio"]) > float(ls_ratios[i-1]["longShortRatio"]))
        signals["ls_trend"] = "more_long" if trend > len(ls_ratios)//2 else "more_short"
    
    # ── Taker Buy/Sell Signal ──
    if taker_ratios and len(taker_ratios) > 1:
        latest_taker = float(taker_ratios[-1]["buySellRatio"])
        signals["taker_buy_sell"] = round(latest_taker, 3)
        
        if latest_taker > 1.3:
            signals["taker_signal"] = "🟢 aggressive buying"
        elif latest_taker < 0.7:
            signals["taker_signal"] = "🔴 aggressive selling"
        else:
            signals["taker_signal"] = "balanced"
    
    # ── Composite Directional Bias ──
    bias_score = 0
    bias_reasons = []
    
    # Funding rate: negative = shorts pay = potential squeeze
    fr_val = signals.get("funding_rate", 0)
    if fr_val < -0.005:
        bias_score += 2
        bias_reasons.append(f"負費率({fr_val}%)")
    elif fr_val > 0.05:
        bias_score -= 2
        bias_reasons.append(f"高費率({fr_val}%)")
    
    # Long/Short contrarian
    if signals.get("ls_signal", "").startswith("🐂"):
        bias_score += 2
        bias_reasons.append("散戶偏淡")
    elif signals.get("ls_signal", "").startswith("🐻"):
        bias_score -= 2
        bias_reasons.append("散戶偏濃")
    
    # Taker flow
    taker_val = signals.get("taker_buy_sell", 1)
    if taker_val > 1.2:
        bias_score += 1
        bias_reasons.append("主動買盤強")
    elif taker_val < 0.8:
        bias_score -= 1
        bias_reasons.append("主動沽盤強")
    
    # OI change: rising OI = conviction in current direction
    oi_delta = signals.get("oi_delta_pct", 0)
    if oi_delta > 1.5:
        bias_reasons.append(f"OI增({oi_delta:+.1f}%)")
        # OI rising + taker buying = strong bullish conviction
        if taker_val > 1:
            bias_score += 1
        # OI rising + taker selling = strong bearish conviction
        elif taker_val < 1:
            bias_score -= 1
    elif oi_delta < -1.5:
        bias_reasons.append(f"OI減({oi_delta:+.1f}%)")
    
    if bias_score >= 3:
        signals["directional_bias"] = "🟢 BULLISH"
    elif bias_score >= 1:
        signals["directional_bias"] = "🟡 LEAN LONG"
    elif bias_score <= -3:
        signals["directional_bias"] = "🔴 BEARISH"
    elif bias_score <= -1:
        signals["directional_bias"] = "🟠 LEAN SHORT"
    else:
        signals["directional_bias"] = "⚪ NEUTRAL"
    
    signals["bias_score"] = bias_score
    signals["bias_reasons"] = bias_reasons
    
    # Extreme flag for silent mode
    signals["is_extreme"] = (
        abs(bias_score) >= 3 or
        signals.get("positioning_alert") != "neutral" or
        signals.get("oi_alert", "normal") != "normal"
    )
    
    return signals


def save_to_csv(signals):
    """Append signals to CSV for historical tracking."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    now = datetime.now(HKT)
    row = {
        "timestamp": now.isoformat(),
        "funding_rate_%": signals.get("funding_rate", ""),
        "funding_annual_%": signals.get("funding_rate_annual", ""),
        "open_interest": signals.get("open_interest", ""),
        "oi_delta_%": signals.get("oi_delta_pct", ""),
        "long_short_ratio": signals.get("long_short_ratio", ""),
        "taker_buy_sell": signals.get("taker_buy_sell", ""),
        "bias_score": signals.get("bias_score", ""),
        "directional_bias": signals.get("directional_bias", ""),
        "alert": signals.get("positioning_alert", ""),
    }
    
    file_exists = CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def print_dashboard(signals):
    """Print full dashboard to stdout."""
    print(f"""
📊 BTC Positioning — {datetime.now(HKT).strftime('%H:%M HKT')}

  資金費率    : {signals.get('funding_rate', 'N/A'):>8}% (8h) → 年化 {signals.get('funding_rate_annual', 'N/A')}%
  費率趨勢    : {signals.get('funding_trend', 'N/A')}

  持倉量 OI   : {signals.get('open_interest', 'N/A')} BTC  (Δ{signals.get('oi_delta_pct', 0):+.2f}%)
  多空比      : {signals.get('long_short_ratio', 'N/A')}  ({signals.get('ls_signal', 'N/A')})
  Taker 買賣  : {signals.get('taker_buy_sell', 'N/A')}  ({signals.get('taker_signal', 'N/A')})

  🎯 方向偏差 : {signals.get('directional_bias', 'N/A')}  (score: {signals.get('bias_score', 'N/A')})
  原因        : {', '.join(signals.get('bias_reasons', [])) or '無明顯信號'}
""")


def print_alert(signals):
    """Print extreme alert for TG delivery."""
    alerts = []
    
    pa = signals.get("positioning_alert", "neutral")
    if pa != "neutral":
        alerts.append(pa)
    
    oa = signals.get("oi_alert", "normal")
    if oa != "normal":
        alerts.append(oa)
    
    if not alerts:
        return
    
    now = datetime.now(HKT)
    lines = [f"⚠️ <b>BTC Positioning Alert</b> — {now.strftime('%m/%d %H:%M HKT')}", ""]
    for a in alerts:
        lines.append(f"  {a}")
    lines.append("")
    lines.append(f"  方向偏差: {signals.get('directional_bias', 'N/A')}")
    lines.append(f"  費率: {signals.get('funding_rate', 'N/A')}% | OI: {signals.get('open_interest', 'N/A')} BTC")
    lines.append(f"  多空比: {signals.get('long_short_ratio', 'N/A')} | Taker: {signals.get('taker_buy_sell', 'N/A')}")
    
    print("\n".join(lines))


def main():
    silent = "--silent" in sys.argv
    
    # Fetch all data
    funding_rates = fetch_json(FUNDING_RATE_URL, "Funding Rate")
    oi_data = fetch_json(OPEN_INTEREST_URL, "Open Interest")
    ls_ratios = fetch_json(LS_RATIO_URL, "Long/Short Ratio")
    taker_ratios = fetch_json(TAKER_LS_URL, "Taker Buy/Sell")
    
    # Compute signals
    signals = compute_signals(funding_rates, oi_data, ls_ratios, taker_ratios)
    
    # Save to CSV (always)
    save_to_csv(signals)
    
    # Output
    if "--json" in sys.argv:
        print(json.dumps(signals, ensure_ascii=False))
    elif silent:
        # Only print if extreme
        if signals.get("is_extreme"):
            print_alert(signals)
        # else: completely silent
    else:
        print_dashboard(signals)
        if signals.get("is_extreme"):
            print("⚠️ EXTREME POSITIONING DETECTED\n")


if __name__ == "__main__":
    main()
