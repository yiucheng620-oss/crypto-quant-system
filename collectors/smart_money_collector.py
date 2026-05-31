#!/usr/bin/env python3
"""
🤑 SMART MONEY COLLECTOR — Binance Whale Data
==============================================
Pull Binance Smart Signal data (whale entry prices, profit ratios, L/S ratio).
No API key needed. Integrated into engine scan pipeline as context layer.

Endpoint: binance.com/bapi/futures/v1/public/future/smart-money/signal/overview
Rate limit: undocumented per-IP budget. Respect with 1s delay + jitter.

Signal types for engines:
  - SQUEEZE: short whale entry > long whale entry by 5%+ → potential squeeze
  - EXTREME: L/S ratio < 0.33 → one-sided positioning, reversal risk
  - WHALE_TRAP: whales on losing side > 70% unrealized loss → trapped money
  - PROFIT_CLUSTER: >80% of one side profitable → mean reversion likely
"""

import json
import os
import time
import random
from datetime import datetime, timezone, timedelta

import requests

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
CACHE_DIR = os.path.join(WORKSPACE, "data", "smart_money")
os.makedirs(CACHE_DIR, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
BASE_URL = "https://www.binance.com/bapi/futures/v1/public/future/smart-money/signal/overview"
CACHE_TTL = 300  # 5 min — matches engine scan interval
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _pull_single(symbol: str) -> dict | None:
    """Pull smart money data for one symbol. Returns None on rate limit or error."""
    try:
        r = requests.get(
            BASE_URL, params={"symbol": symbol},
            headers=HEADERS, timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("success"):
                return data["data"]
        elif r.status_code in (418, 429):
            retry = r.headers.get("Retry-After", "300")
            print(f"  ⚠️ Rate limited on {symbol}, retry in {retry}s")
            return None
    except Exception as e:
        print(f"  ⚠️ Smart money fetch error ({symbol}): {e}")
    return None


def pull_all(symbols: list = None) -> dict:
    """Pull smart money data for configured symbols. Cached 5 min."""
    if symbols is None:
        symbols = SYMBOLS

    cache_file = os.path.join(CACHE_DIR, "smart_money_latest.json")
    
    # Check cache
    if os.path.exists(cache_file):
        age = time.time() - os.path.getmtime(cache_file)
        if age < CACHE_TTL:
            with open(cache_file) as f:
                return json.load(f)

    # Fresh pull
    results = {}
    for sym in symbols:
        data = _pull_single(sym)
        if data:
            results[sym] = data
        # Rate limit respect: 0.8-1.5s between calls
        time.sleep(0.8 + random.random() * 0.7)

    # Add derived signals
    snapshot = {
        "timestamp": datetime.now(HKT).isoformat(),
        "timestamp_ts": time.time(),
        "data": results,
        "signals": _derive_signals(results),
    }

    # Atomic write
    tmp = cache_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)
    os.replace(tmp, cache_file)

    return snapshot


def _derive_signals(data: dict) -> list:
    """Derive actionable signals from smart money data."""
    signals = []
    for sym, d in data.items():
        ticker = sym.replace("USDT", "")
        
        long_whale_entry = float(d.get("longWhalesAvgEntryPrice", 0))
        short_whale_entry = float(d.get("shortWhalesAvgEntryPrice", 0))
        long_profit = d.get("longProfitTraders", 0)
        short_profit = d.get("shortProfitTraders", 0)
        long_total = d.get("longTraders", 1)
        short_total = d.get("shortTraders", 1)
        ls_ratio = d.get("longShortRatio", 0.5)
        
        # Ignore zero data
        if long_whale_entry <= 0 or short_whale_entry <= 0:
            continue

        # === SQUEEZE: short whale entry > long by 5% ===
        if short_whale_entry > long_whale_entry * 1.05:
            squeeze_pct = (short_whale_entry / long_whale_entry - 1) * 100
            signals.append({
                "symbol": ticker,
                "type": "SQUEEZE",
                "severity": "high" if squeeze_pct > 15 else "medium",
                "detail": f"Short whale entry {short_whale_entry:.1f} > Long {long_whale_entry:.1f} (+{squeeze_pct:.1f}%)",
            })

        # === WHALE_TRAP: one side >70% underwater ===
        long_pct = long_profit / max(long_total, 1)
        short_pct = short_profit / max(short_total, 1)
        if long_pct < 0.30 and long_total > 50:
            signals.append({
                "symbol": ticker,
                "type": "LONG_TRAPPED",
                "severity": "high" if long_pct < 0.20 else "medium",
                "detail": f"Only {long_pct*100:.0f}% longs profitable, whales entry ${long_whale_entry:.1f}",
            })
        if short_pct < 0.30 and short_total > 50:
            signals.append({
                "symbol": ticker,
                "type": "SHORT_TRAPPED",
                "severity": "high" if short_pct < 0.20 else "medium",
                "detail": f"Only {short_pct*100:.0f}% shorts profitable, whales entry ${short_whale_entry:.1f}",
            })

        # === EXTREME POSITIONING ===
        if ls_ratio < 0.33:
            signals.append({
                "symbol": ticker,
                "type": "EXTREME_SHORT",
                "severity": "medium",
                "detail": f"L/S ratio {ls_ratio:.2f} — extreme short bias, reversal risk",
            })
        elif ls_ratio > 3.0:
            signals.append({
                "symbol": ticker,
                "type": "EXTREME_LONG",
                "severity": "medium",
                "detail": f"L/S ratio {ls_ratio:.2f} — extreme long bias, reversal risk",
            })

        # === PROFIT CLUSTER: >85% profitable on one side ===
        if long_pct > 0.85 and long_total > 50:
            signals.append({
                "symbol": ticker,
                "type": "LONG_PROFIT_CLUSTER",
                "severity": "low",
                "detail": f"{long_pct*100:.0f}% longs in profit — mean reversion risk",
            })
        if short_pct > 0.85 and short_total > 50:
            signals.append({
                "symbol": ticker,
                "type": "SHORT_PROFIT_CLUSTER",
                "severity": "low",
                "detail": f"{short_pct*100:.0f}% shorts in profit — mean reversion risk",
            })

    return signals


def get_context() -> dict:
    """Get smart money context for engine flow analysis. Cache-friendly."""
    snapshot = pull_all()
    data = snapshot.get("data", {})
    signals = snapshot.get("signals", [])
    
    context = {
        "whales": {},
        "signals": signals,
        "summary": [],
    }
    
    for sym, d in data.items():
        ticker = sym.replace("USDT", "")
        long_whale_entry = float(d.get("longWhalesAvgEntryPrice", 0))
        short_whale_entry = float(d.get("shortWhalesAvgEntryPrice", 0))
        long_profit = d.get("longProfitTraders", 0)
        short_profit = d.get("shortProfitTraders", 0)
        long_total = d.get("longTraders", 1)
        short_total = d.get("shortTraders", 1)
        ls_ratio = d.get("longShortRatio", 0)
        
        context["whales"][ticker] = {
            "long_entry": round(long_whale_entry, 2),
            "short_entry": round(short_whale_entry, 2),
            "long_profit_pct": round(long_profit / max(long_total, 1) * 100, 1),
            "short_profit_pct": round(short_profit / max(short_total, 1) * 100, 1),
            "ls_ratio": round(ls_ratio, 2),
            "long_traders": long_total,
            "short_traders": short_total,
        }
        
        # Summary line
        bias = "🟢 LONG" if ls_ratio > 1.5 else ("🔴 SHORT" if ls_ratio < 0.5 else "⚪ NEUTRAL")
        context["summary"].append(
            f"{ticker}: {bias} L/S={ls_ratio:.2f} "
            f"L_entry=${long_whale_entry:.1f} S_entry=${short_whale_entry:.1f} "
            f"L_win={long_profit}/{long_total} S_win={short_profit}/{short_total}"
        )
    
    return context


# ── CLI ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if "--json" in sys.argv:
        snapshot = pull_all()
        print(json.dumps(snapshot, indent=2, ensure_ascii=False, default=str))
    else:
        ctx = get_context()
        print("🤑 Smart Money Context")
        print("=" * 50)
        for line in ctx["summary"]:
            print(f"  {line}")
        if ctx["signals"]:
            print(f"\n  🚨 Signals ({len(ctx['signals'])}):")
            for s in ctx["signals"]:
                print(f"    [{s['severity'].upper():7s}] {s['symbol']:4s} {s['type']:20s} — {s['detail']}")
        else:
            print("\n  ✅ No actionable signals")
