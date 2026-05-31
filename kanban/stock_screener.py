#!/usr/bin/env python3
"""
US Stock Universe Screener — Stage 1 & 2 (Liquidity + Technical)
==================================================================
Stage 1: Filters 6000+ US stocks → tradable universe (~500-800)
Stage 2: Technical pattern scan → shortlist (~20-30)
Stage 3: Kanban picks up the shortlist → 4-analyst deep dive

Patterns detected (matching user's trading style):
  - Pullback to 4H support (HVN from volume profile)
  - Breakout from 1H/4H consolidation
  - Momentum continuation (higher high, higher low on 4H)
  - Volume spike + price compression (coiled spring)

Usage: python3 stock_screener.py [--top 10] [--sector all]
Output: ~/workspace/scalp_lab/stock_shortlist.json → picked up by Kanban
"""

import sys
import os
import json
import csv
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

HKT = timezone(timedelta(hours=8))
OUTPUT_FILE = Path(os.path.expanduser("~/workspace/scalp_lab/engines/stock_shortlist.json"))
UNIVERSE_CACHE = Path(os.path.expanduser("~/workspace/scalp_lab/engines/us_universe.json"))

# ═══════════════════════════════════════
# Stage 1: Liquidity Filter
# ═══════════════════════════════════════

def fetch_sp500_symbols():
    """Get S&P 500 + Nasdaq 100 symbols from Wikipedia (free, no API key)."""
    symbols = set()
    
    # S&P 500
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode()
            import re
            # Extract tickers from table
            tickers = re.findall(r'<a[^>]*rel="nofollow"[^>]*>([A-Z]{1,5})</a>', html)
            symbols.update(tickers)
            print(f"  S&P 500: {len(tickers)} symbols extracted")
    except Exception as e:
        print(f"  S&P 500 fetch failed: {e}")
    
    # Nasdaq 100
    try:
        url = "https://en.wikipedia.org/wiki/Nasdaq-100"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode()
            tickers = re.findall(r'<a[^>]*title="[^"]*"[^>]*>([A-Z]{1,5})</a>', html)
            symbols.update(tickers)
            print(f"  +Nasdaq 100: {len(symbols)} total unique")
    except Exception as e:
        print(f"  Nasdaq 100 fetch failed: {e}")
    
    return list(symbols)


def check_liquidity(symbol):
    """Quick liquidity check via Yahoo Finance (free, rate-limited)."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice", 0)
        volume = result["indicators"]["quote"][0]["volume"]
        avg_vol = sum(v for v in volume if v) / max(1, len([v for v in volume if v]))
        
        # Filter: price > $5, avg volume > 500K
        if price > 5 and avg_vol > 500000:
            return {
                "symbol": symbol,
                "price": price,
                "avg_volume": int(avg_vol),
                "currency": meta.get("currency", "USD"),
            }
    except:
        pass
    return None


# ═══════════════════════════════════════
# Stage 2: Technical Pattern Scan
# ═══════════════════════════════════════

def fetch_klines(symbol, interval="1h", limit=100):
    """Fetch OHLCV for a symbol from Yahoo."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=60d&interval={interval}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        
        result = data["chart"]["result"][0]
        ts = result["timestamp"]
        o = result["indicators"]["quote"][0]["open"]
        h = result["indicators"]["quote"][0]["high"]
        l = result["indicators"]["quote"][0]["low"]
        c = result["indicators"]["quote"][0]["close"]
        v = result["indicators"]["quote"][0]["volume"]
        
        return [{"t": ts[i], "o": o[i], "h": h[i], "l": l[i], "c": c[i], "v": v[i]}
                for i in range(len(ts)) if all(x is not None for x in [o[i], h[i], l[i], c[i]])]
    except:
        return []


def detect_pullback_to_support(klines_4h):
    """Detect pullback to recent support level."""
    if len(klines_4h) < 20:
        return False, 0
    
    closes = [k["c"] for k in klines_4h]
    lows = [k["l"] for k in klines_4h]
    
    # Find recent swing low (support) in last 15-20 bars
    support_window = closes[-20:-3]
    if not support_window:
        return False, 0
    
    support = min(lows[-20:-3])
    current = closes[-1]
    
    # Price within 3% of support
    dist_pct = (current - support) / support * 100
    if 0 < dist_pct < 3:
        # Check if price was higher recently (confirming pullback)
        recent_high = max(closes[-15:-5])
        if recent_high > support * 1.03:
            return True, round(dist_pct, 1)
    
    return False, 0


def detect_breakout(klines_1h):
    """Detect breakout from 1H consolidation."""
    if len(klines_1h) < 30:
        return False, 0
    
    closes = [k["c"] for k in klines_1h]
    highs = [k["h"] for k in klines_1h]
    
    # Consolidation: tight range in last 20-10 bars
    consolidation_high = max(highs[-20:-5])
    consolidation_low = min(k["l"] for k in klines_1h[-20:-5])
    range_pct = (consolidation_high - consolidation_low) / consolidation_low * 100
    
    if range_pct < 3:  # Tight consolidation
        current = closes[-1]
        if current > consolidation_high * 1.005:  # Breaking above
            # Volume confirmation on breakout
            avg_vol = sum(k["v"] for k in klines_1h[-20:-3]) / 17
            breakout_vol = sum(k["v"] for k in klines_1h[-3:]) / 3
            if breakout_vol > avg_vol * 1.3:
                return True, round((current - consolidation_high) / consolidation_high * 100, 1)
    
    return False, 0


def detect_momentum(klines_4h):
    """Detect higher high + higher low momentum structure."""
    if len(klines_4h) < 30:
        return False, 0
    
    closes = [k["c"] for k in klines_4h]
    
    # Find two swing lows and two swing highs
    # Simplified: check last 3 swing points
    window_size = 5
    swings_high = []
    swings_low = []
    
    for i in range(window_size, len(closes) - window_size):
        if closes[i] == max(closes[i-window_size:i+window_size+1]):
            swings_high.append((i, closes[i]))
        if closes[i] == min(closes[i-window_size:i+window_size+1]):
            swings_low.append((i, closes[i]))
    
    if len(swings_high) >= 2 and len(swings_low) >= 2:
        hh = swings_high[-1][1] > swings_high[-2][1]
        hl = swings_low[-1][1] > swings_low[-2][1]
        if hh and hl:
            # Also check above 20-period SMA
            sma20 = sum(closes[-20:]) / 20
            if closes[-1] > sma20:
                return True, round((closes[-1] - sma20) / sma20 * 100, 1)
    
    return False, 0


def detect_coiled_spring(klines_1h):
    """Volume spike + price compression → potential breakout."""
    if len(klines_1h) < 20:
        return False, 0
    
    closes = [k["c"] for k in klines_1h]
    volumes = [k["v"] for k in klines_1h]
    
    # Volume spike: last 3 bars volume > 2x average
    avg_vol = sum(volumes[-20:-3]) / 17
    recent_vol = sum(volumes[-3:]) / 3
    
    if recent_vol > avg_vol * 2:
        # Price compression: narrow range
        recent_range = (max(closes[-5:]) - min(closes[-5:])) / closes[-1] * 100
        if recent_range < 2:
            return True, 0
    
    return False, 0


def scan_stock(symbol):
    """Full technical scan for one stock."""
    klines_4h = fetch_klines(symbol, "1h", 100)  # Yahoo doesn't have 4h, use 1h with 100 bars
    klines_1h = fetch_klines(symbol, "30m", 50)  # Use 30m instead of 1h for tighter view
    
    if len(klines_4h) < 20:
        return None
    
    patterns = []
    
    pb, pb_dist = detect_pullback_to_support(klines_4h)
    if pb:
        patterns.append({"type": "pullback", "detail": f"{pb_dist}% from support"})
    
    bo, bo_dist = detect_breakout(klines_1h)
    if bo:
        patterns.append({"type": "breakout", "detail": f"+{bo_dist}% above resistance"})
    
    mom, mom_dist = detect_momentum(klines_4h)
    if mom:
        patterns.append({"type": "momentum", "detail": f"{mom_dist}% above SMA"})
    
    cs, _ = detect_coiled_spring(klines_1h)
    if cs:
        patterns.append({"type": "coiled_spring", "detail": "volume spike + tight range"})
    
    if patterns:
        current = klines_4h[-1]["c"]
        change_5d = ((current - klines_4h[-min(20, len(klines_4h))]["c"]) / klines_4h[-min(20, len(klines_4h))]["c"] * 100) if len(klines_4h) >= 20 else 0
        
        return {
            "symbol": symbol,
            "price": round(current, 2),
            "change_5d": round(change_5d, 1),
            "patterns": patterns,
            "pattern_count": len(patterns),
        }
    
    return None


CONFIG_FILE = Path(os.path.expanduser("~/workspace/scalp_lab/engines/kanban_config.json"))

def load_pattern_weights():
    """Load pattern weights from Kanban config (auto-optimized)."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        patterns = config.get("screener", {}).get("patterns", {})
        weights = {}
        for name, cfg in patterns.items():
            if cfg.get("enabled", True):
                weights[name] = cfg.get("weight", 1.0)
        return weights
    # Default: all equal
    return {"pullback": 1.0, "breakout": 1.0, "momentum": 1.0, "coiled_spring": 1.0}

def main():
    top_n = 10
    sector = "all"
    for arg in sys.argv:
        if arg.startswith("--top="):
            top_n = int(arg.split("=")[1])
        if arg.startswith("--sector="):
            sector = arg.split("=")[1]
    
    now = datetime.now(HKT)
    print(f"🔍 US Stock Screener — {now.strftime('%Y-%m-%d %H:%M HKT')}")
    print(f"   Stage 1: Liquidity filter → Stage 2: Technical patterns → Top {top_n}\n")
    
    # ── Stage 1: Get universe ──
    print("Stage 1: Building stock universe...")
    
    universe = None
    
    if UNIVERSE_CACHE.exists():
        # Use cached universe if < 1 hour old
        cache_age = time.time() - UNIVERSE_CACHE.stat().st_mtime
        if cache_age < 3600:
            with open(UNIVERSE_CACHE) as f:
                universe = json.load(f)
            print(f"  Using cached universe: {len(universe)} stocks (age: {cache_age:.0f}s)")
    
    if universe is None:
        symbols = fetch_sp500_symbols()
        print(f"  {len(symbols)} candidates from S&P 500 + Nasdaq 100")
        
        # Check liquidity in parallel (limited to 10 threads to avoid rate limits)
        universe = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(check_liquidity, s): s for s in symbols}
            for i, future in enumerate(as_completed(futures)):
                result = future.result()
                if result:
                    universe.append(result)
                if (i + 1) % 50 == 0:
                    print(f"  ...checked {i+1}/{len(symbols)}, found {len(universe)} liquid")
        
        print(f"  Stage 1 complete: {len(universe)} liquid stocks (price > $5, vol > 500K)")
        
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(UNIVERSE_CACHE), suffix='.tmp')
        with os.fdopen(fd, 'w') as f:
            json.dump(universe, f)
        os.replace(tmp, UNIVERSE_CACHE)
    
    # ── Stage 2: Technical scan ──
    print(f"\nStage 2: Technical pattern scan on {len(universe)} stocks...")
    
    shortlist = []
    # Sample to keep runtime reasonable (~200 stocks for a quick scan)
    sample = universe  # Full scan for S&P 500 universe (~500 stocks)
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(scan_stock, s["symbol"]): s["symbol"] for s in sample}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            if result:
                shortlist.append(result)
            if (i + 1) % 50 == 0:
                print(f"  ...scanned {i+1}/{len(sample)}, found {len(shortlist)} with patterns")
    
    # Sort by weighted pattern score (reads auto-optimized weights from config)
    pattern_weights = load_pattern_weights()
    for s in shortlist:
        s["weighted_score"] = sum(pattern_weights.get(p["type"], 1.0) for p in s["patterns"])
    shortlist.sort(key=lambda x: x["weighted_score"], reverse=True)
    
    # Log disabled patterns
    config = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    for p_name, cfg in config.get("screener", {}).get("patterns", {}).items():
        if not cfg.get("enabled", True):
            print(f"  ⚠️ {p_name}: DISABLED (auto-optimized)")
    
    print(f"\n  Stage 2 complete: {len(shortlist)} stocks with valid patterns")
    
    # ── Output ──
    top_picks = shortlist[:top_n]
    
    if top_picks:
        print(f"\n🎯 Top {len(top_picks)} Picks:\n")
        for i, pick in enumerate(top_picks):
            print(f"  {i+1}. ${pick['symbol']} — ${pick['price']} ({pick['change_5d']:+.1f}% 5d)")
            for p in pick["patterns"]:
                print(f"     {p['type']}: {p['detail']}")
            print()
    
    # Save shortlist for Kanban (atomic write — 防 corrupt)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "timestamp": now.isoformat(),
        "universe_size": len(universe),
        "shortlist_size": len(shortlist),
        "top_picks": top_picks,
        "full_shortlist": shortlist[:50],
    }
    tmp = str(OUTPUT_FILE) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(output, f, indent=2)
    os.replace(tmp, OUTPUT_FILE)  # Atomic!
    
    print(f"📁 Shortlist saved: {OUTPUT_FILE}")
    print(f"   {len(shortlist[:50])} stocks ready for Kanban deep dive")


if __name__ == "__main__":
    main()
