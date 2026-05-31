#!/usr/bin/env python3
"""
BTC Liquidation Heatmap — Binance Public API (FREE)
=====================================================
Collects liquidation orders and builds price-level heatmap.
Identifies liquidation clusters = price magnets.

Usage: python3 liquidation_heatmap.py [--levels 20]
"""
import sys
import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

HKT = timezone(timedelta(hours=8))
DATA_DIR = Path(os.path.expanduser("~/workspace/market_data/liquidations"))

# Binance liquidation orders (public, no auth)
LIQ_URL = "https://fapi.binance.com/fapi/v1/allForceOrders?symbol=BTCUSDT&limit=1000"


def fetch_liquidations():
    """Fetch recent liquidation orders from Binance."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(LIQ_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
    return []


def build_heatmap(liquidations, bucket_size=100):
    """Group liquidations into price buckets, separate long/short."""
    long_buckets = defaultdict(float)
    short_buckets = defaultdict(float)
    
    for liq in liquidations:
        price = float(liq.get("price", liq.get("avgPrice", 0)))
        qty = float(liq.get("origQty", liq.get("executedQty", 0)))
        side = liq.get("side", "").upper()
        
        if price <= 0:
            continue
        
        # Round to nearest bucket
        bucket = round(price / bucket_size) * bucket_size
        
        if side == "BUY":
            short_buckets[bucket] += qty  # Shorts get liquidated on BUY
        elif side == "SELL":
            long_buckets[bucket] += qty   # Longs get liquidated on SELL
    
    return long_buckets, short_buckets


def find_clusters(buckets, top_n=10):
    """Find top liquidation clusters."""
    sorted_buckets = sorted(buckets.items(), key=lambda x: x[1], reverse=True)
    return sorted_buckets[:top_n]


def get_current_price():
    """Get current BTC price from Binance."""
    url = "https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return float(data["price"])
    except:
        return None


def print_heatmap(current_price, long_clusters, short_clusters, bucket_size):
    """Print ASCII heatmap showing liquidation clusters around current price."""
    if not current_price:
        return
    
    # Determine range: ±$3000 around current price
    price_min = current_price - 3000
    price_max = current_price + 3000
    
    # Build bars
    levels = []
    for bucket in range(int(price_min), int(price_max) + bucket_size, bucket_size):
        long_qty = long_clusters.get(bucket, 0)
        short_qty = short_clusters.get(bucket, 0)
        total = long_qty + short_qty
        
        if total > 0:
            levels.append((bucket, long_qty, short_qty, total))
    
    if not levels:
        print("  (no recent liquidation data in range)")
        return
    
    max_total = max(l[3] for l in levels)
    
    print(f"\n  💥 Liquidation Heatmap (bucket: ${bucket_size})")
    print(f"  {'Price':>8}  {'Long Liq':>10} {'Short Liq':>10} {'Heatmap'}")
    print(f"  {'─'*8}  {'─'*10} {'─'*10} {'─'*30}")
    
    for bucket, long_qty, short_qty, total in sorted(levels, key=lambda x: x[0]):
        bar_len = int((total / max_total) * 30) if max_total > 0 else 0
        
        # Color indicator
        if bucket > current_price:
            marker = "🔴" if long_qty > short_qty * 2 else "🟡" if long_qty > 0 else "  "
        elif bucket < current_price:
            marker = "🟢" if short_qty > long_qty * 2 else "🟡" if short_qty > 0 else "  "
        else:
            marker = "📍"
        
        bar = "█" * bar_len
        print(f"  ${bucket:>7,.0f}  {long_qty:>8.1f} BTC {short_qty:>8.1f} BTC {marker} {bar}")
    
    # Key levels
    print(f"\n  🎯 <b>Key Liquidation Levels:</b>")
    
    total_long_liq = sum(long_clusters.values())
    total_short_liq = sum(short_clusters.values())
    
    # Top long liquidation clusters (above price = resistance from forced selling)
    above_long = [(p, q) for p, q in long_clusters.items() if p > current_price]
    above_long.sort(key=lambda x: x[1], reverse=True)
    
    # Top short liquidation clusters (below price = support from forced buying)
    below_short = [(p, q) for p, q in short_clusters.items() if p < current_price]
    below_short.sort(key=lambda x: x[1], reverse=True)
    
    if above_long:
        top = above_long[:3]
        print(f"  🔴 <b>阻力 (上方爆多倉):</b>")
        for p, q in top:
            dist = p - current_price
            pct = (dist / current_price) * 100
            print(f"    ${p:,.0f} — {q:.1f} BTC 爆倉 (+{pct:.1f}% 距離)")
    
    if below_short:
        top = below_short[:3]
        print(f"  🟢 <b>支持 (下方爆空倉):</b>")
        for p, q in top:
            dist = current_price - p
            pct = (dist / current_price) * 100
            print(f"    ${p:,.0f} — {q:.1f} BTC 爆倉 (-{pct:.1f}% 距離)")
    
    print(f"\n  📊 總爆倉: 多 {total_long_liq:.1f} BTC | 空 {total_short_liq:.1f} BTC")


def main():
    bucket_size = 100
    for arg in sys.argv:
        if arg.startswith("--levels="):
            bucket_size = int(arg.split("=")[1])
    
    now = datetime.now(HKT)
    
    # Fetch data
    liquidations = fetch_liquidations()
    
    if not liquidations:
        print(f"[{now.strftime('%H:%M HKT')}] No liquidation data available")
        return
    
    print(f"[{now.strftime('%H:%M HKT')}] {len(liquidations)} 筆爆倉記錄")
    
    # Build heatmap
    long_buckets, short_buckets = build_heatmap(liquidations, bucket_size)
    
    # Find clusters
    long_clusters = find_clusters(long_buckets)
    short_clusters = find_clusters(short_buckets)
    
    # Get current price
    current_price = get_current_price()
    if current_price:
        print(f"  BTC: ${current_price:,.0f}")
    
    # Print heatmap
    print_heatmap(current_price, long_buckets, short_buckets, bucket_size)
    
    # Save raw data
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = now.strftime("%Y%m%d_%H%M")
    filepath = DATA_DIR / f"liquidations_{ts}.json"
    with open(filepath, "w") as f:
        json.dump({"timestamp": now.isoformat(), "count": len(liquidations),
                   "price": current_price, "data": liquidations[:500]}, f)


if __name__ == "__main__":
    main()
