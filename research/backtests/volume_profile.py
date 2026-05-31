#!/usr/bin/env python3
"""
BTC Volume Profile Calculator
==============================
Computes Volume Profile from Binance K-line data.
Identifies High Volume Nodes (HVN) = support/resistance
Identifies Low Volume Nodes (LVN) = vacuum zones (price moves fast through)

Usage: python3 volume_profile.py [--days 30] [--levels 100]
"""
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))


def fetch_klines(interval="1h", limit=720):
    """Fetch BTCUSDT klines from Binance."""
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except:
        return []


def compute_volume_profile(klines, num_buckets=150):
    """Compute volume profile from kline data.
    Each kline: [open_time, open, high, low, close, volume, ...]
    Distributes volume proportionally across the high-low range.
    """
    if not klines:
        return {}, 0, 0
    
    prices = []
    for k in klines:
        prices.extend([float(k[2]), float(k[3])])  # high, low
    
    price_min = min(prices)
    price_max = max(prices)
    price_range = price_max - price_min
    bucket_size = price_range / num_buckets
    
    profile = defaultdict(float)
    
    for k in klines:
        high = float(k[2])
        low = float(k[3])
        close = float(k[4])
        volume = float(k[5])
        quote_vol = float(k[7])  # quote asset volume
        
        # Distribute volume across the candle's price range
        candle_range = high - low
        if candle_range == 0:
            bucket = round(close / bucket_size) * bucket_size
            profile[bucket] += quote_vol
            continue
        
        # Use quote volume and distribute by price levels
        for bucket_start in range(int(low/bucket_size), int(high/bucket_size) + 1):
            bucket_price = bucket_start * bucket_size
            # How much of the candle overlaps with this bucket?
            bucket_low = bucket_price
            bucket_high = bucket_price + bucket_size
            
            overlap_low = max(low, bucket_low)
            overlap_high = min(high, bucket_high)
            overlap = max(0, overlap_high - overlap_low)
            
            if overlap > 0:
                weight = overlap / candle_range
                profile[bucket_price] += quote_vol * weight
    
    return profile, price_min, price_max


def find_nodes(profile, current_price):
    """Find High Volume Nodes (HVN) and Low Volume Nodes (LVN)."""
    if not profile:
        return [], [], []
    
    prices = sorted(profile.keys())
    volumes = [profile[p] for p in prices]
    
    if not volumes:
        return [], [], []
    
    avg_vol = sum(volumes) / len(volumes)
    std_vol = (sum((v - avg_vol)**2 for v in volumes) / len(volumes)) ** 0.5
    
    hvn_threshold = avg_vol + std_vol * 0.5
    lvn_threshold = avg_vol * 0.3
    
    hvns = []
    lvns = []
    
    # Find contiguous HVN/LVN zones
    i = 0
    while i < len(prices):
        if volumes[i] >= hvn_threshold:
            zone_start = prices[i]
            zone_vol = volumes[i]
            j = i + 1
            while j < len(prices) and volumes[j] >= hvn_threshold:
                zone_vol += volumes[j]
                j += 1
            zone_end = prices[j-1]
            zone_center = (zone_start + zone_end) / 2
            dist_pct = ((zone_center - current_price) / current_price) * 100
            hvns.append({
                "price": round(zone_center),
                "range": f"${zone_start:,.0f} - ${zone_end:,.0f}",
                "volume": round(zone_vol, 1),
                "dist_pct": round(dist_pct, 1),
                "type": "resistance" if zone_center > current_price else "support",
            })
            i = j
        elif volumes[i] <= lvn_threshold:
            zone_start = prices[i]
            j = i + 1
            while j < len(prices) and volumes[j] <= lvn_threshold:
                j += 1
            zone_end = prices[j-1]
            zone_center = (zone_start + zone_end) / 2
            dist_pct = ((zone_center - current_price) / current_price) * 100
            lvns.append({
                "price": round(zone_center),
                "range": f"${zone_start:,.0f} - ${zone_end:,.0f}",
                "dist_pct": round(dist_pct, 1),
            })
            i = j
        else:
            i += 1
    
    # Sort by volume descending for HVNs
    hvns.sort(key=lambda x: x["volume"], reverse=True)
    
    # Separate closest support and resistance
    supports = [h for h in hvns if h["type"] == "support"]
    resistances = [h for h in hvns if h["type"] == "resistance"]
    
    return supports[:5], resistances[:5], lvns[:5]


def print_profile(current_price, profile, supports, resistances, lvns, price_min, price_max):
    """Print volume profile with key levels."""
    if not profile:
        print("  No data available")
        return
    
    max_vol = max(profile.values())
    width = 40
    
    print(f"\n  📊 Volume Profile — BTC ${current_price:,.0f}")
    print(f"  {'Price':>10}  {'Volume':>12} {'Profile'}")
    print(f"  {'─'*10}  {'─'*12} {'─'*width}")
    
    # Show profile around current price (±10%)
    view_min = current_price * 0.90
    view_max = current_price * 1.10
    
    prices = sorted([p for p in profile.keys() if view_min <= p <= view_max])
    
    for p in prices:
        vol = profile[p]
        bar_len = int((vol / max_vol) * width)
        bar = "█" * bar_len
        
        # Mark HVN/LVN
        marker = "  "
        for s in supports:
            if abs(p - s["price"]) < 50:
                marker = "🟢"
                break
        for r in resistances:
            if abs(p - r["price"]) < 50:
                marker = "🔴"
                break
        for l in lvns:
            if abs(p - l["price"]) < 50:
                marker = "⚪"
                break
        
        if p == round(current_price / 100) * 100:
            marker = "📍"
        
        print(f"  ${p:>9,.0f}  {vol:>10,.0f} USDT {marker} {bar}")
    
    # Key levels summary
    print(f"\n  🎯 <b>Key Levels from Volume Profile:</b>")
    
    if supports:
        print(f"  🟢 <b>Support (HVN below price):</b>")
        for s in supports[:3]:
            print(f"    ${s['price']:,.0f} — {s['volume']:,.0f} USDT ({s['dist_pct']:+.1f}%)")
    
    if resistances:
        print(f"  🔴 <b>Resistance (HVN above price):</b>")
        for r in resistances[:3]:
            print(f"    ${r['price']:,.0f} — {r['volume']:,.0f} USDT ({r['dist_pct']:+.1f}%)")
    
    if lvns:
        print(f"  ⚪ <b>Vacuum Zones (LVN — price moves fast):</b>")
        for l in lvns[:3]:
            print(f"    ${l['price']:,.0f} ({l['dist_pct']:+.1f}%)")


def main():
    days = 30
    levels = 150
    for arg in sys.argv:
        if arg.startswith("--days="):
            days = int(arg.split("=")[1])
        if arg.startswith("--levels="):
            levels = int(arg.split("=")[1])
    
    # Adjust limit based on days and interval
    if days <= 7:
        interval, limit = "15m", min(days * 96, 672)
    elif days <= 30:
        interval, limit = "1h", min(days * 24, 720)
    else:
        interval, limit = "4h", min(days * 6, 720)
    
    now = datetime.now(HKT)
    print(f"[{now.strftime('%H:%M HKT')}] {days}d Volume Profile (interval: {interval})")
    
    klines = fetch_klines(interval, limit)
    if not klines:
        print("  ❌ Failed to fetch kline data")
        return
    
    print(f"  Loaded {len(klines)} candles")
    
    # Compute volume profile
    profile, price_min, price_max = compute_volume_profile(klines, levels)
    
    # Get current price from last candle
    current_price = float(klines[-1][4])
    
    # Find key nodes
    supports, resistances, lvns = find_nodes(profile, current_price)
    
    # Print
    print_profile(current_price, profile, supports, resistances, lvns, price_min, price_max)


if __name__ == "__main__":
    main()
