#!/usr/bin/env python3
"""
BTC Liquidation Zone Tracker — OI Drop + Price Move Heuristic
==============================================================
Binance 已 deprecated 所有 liquidation data endpoint。呢個 tracker 用替代方案：

方法：每 5 分鐘記錄 BTC 期貨 OI + 價格
      當 OI 急跌（>X% in 5m）+ 價格大幅波動 → 判定為 liquidation cascade
      記錄嗰個價位 → 累積成為 liquidation heatmap

Liquidation 特徵：
  - LONG liquidation: OI 急跌 + 價格急跌（多頭被強平 → 賣壓）
  - SHORT liquidation: OI 急跌 + 價格急升（空頭被強平 → 買壓）

Storage: CSV with columns [timestamp, price, oi, oi_change_pct, price_change_pct, liq_type, liq_size_est]
"""

import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
DATA_FILE = os.path.expanduser("~/workspace/scalp_lab/engines/btc_liq_zones.json")
STATE_FILE = os.path.expanduser("~/workspace/scalp_lab/engines/btc_liq_state.json")

# Thresholds for liquidation detection
OI_DROP_THRESHOLD_PCT = 1.5   # OI must drop >1.5% in 5 min
PRICE_MOVE_THRESHOLD_PCT = 0.3  # Price must move >0.3% in same period
MIN_OI_DROP_USD = 5_000_000   # Minimum OI drop in USD to be meaningful

# Binance futures API
BINANCE_FUTURES = "https://fapi.binance.com/fapi/v1"


def fetch(url: str, timeout: int = 5) -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"_error": str(e)}


def get_btc_oi() -> dict:
    """Get BTCUSDT open interest from Binance futures."""
    data = fetch(f"{BINANCE_FUTURES}/openInterest?symbol=BTCUSDT")
    if "_error" in data:
        return data
    return {
        "oi": float(data.get("openInterest", 0)),
        "timestamp": data.get("time", 0),
    }


def get_btc_price() -> dict:
    """Get BTCUSDT mark price from Binance futures."""
    data = fetch(f"{BINANCE_FUTURES}/premiumIndex?symbol=BTCUSDT")
    if "_error" in data:
        # Fallback to ticker
        data = fetch(f"{BINANCE_FUTURES}/ticker/price?symbol=BTCUSDT")
        if "_error" in data:
            return data
        return {"price": float(data.get("price", 0)), "timestamp": int(time.time() * 1000)}
    return {
        "price": float(data.get("markPrice", 0)),
        "timestamp": data.get("time", int(time.time() * 1000)),
    }


def load_zones() -> list:
    """Load accumulated liquidation zones."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_zones(zones: list):
    """Save liquidation zones atomically."""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(zones, f, indent=2, default=str)
    os.replace(tmp, DATA_FILE)


def detect_liquidation(prev_state: dict, curr_oi: float, curr_price: float, curr_ts: int) -> dict:
    """Detect if a liquidation cascade just happened."""
    prev_oi = prev_state.get("oi", 0)
    prev_price = prev_state.get("price", 0)
    
    if prev_oi == 0 or prev_price == 0:
        return {"detected": False, "reason": "no_previous_state"}
    
    oi_change_pct = (curr_oi - prev_oi) / prev_oi * 100
    price_change_pct = (curr_price - prev_price) / prev_price * 100
    
    # OI drop + price move = potential liquidation
    oi_dropped = oi_change_pct < -OI_DROP_THRESHOLD_PCT
    price_moved = abs(price_change_pct) > PRICE_MOVE_THRESHOLD_PCT
    
    if not oi_dropped:
        return {"detected": False, "reason": "oi_stable", 
                "oi_change_pct": round(oi_change_pct, 3), 
                "price_change_pct": round(price_change_pct, 3)}
    
    if not price_moved:
        return {"detected": False, "reason": "oi_drop_no_price_move",
                "oi_change_pct": round(oi_change_pct, 3),
                "price_change_pct": round(price_change_pct, 3)}
    
    # Determine liquidation type
    if price_change_pct < 0:
        liq_type = "LONG"  # OI down + price down = longs liquidated
    else:
        liq_type = "SHORT"  # OI down + price up = shorts liquidated
    
    # Estimate size: OI drop in USD
    oi_drop_contracts = abs(prev_oi - curr_oi)
    oi_drop_usd = oi_drop_contracts * curr_price  # rough estimate
    
    if oi_drop_usd < MIN_OI_DROP_USD:
        return {"detected": False, "reason": "oi_drop_too_small",
                "oi_change_pct": round(oi_change_pct, 3),
                "oi_drop_usd": round(oi_drop_usd, 0)}
    
    return {
        "detected": True,
        "type": liq_type,
        "price": round(curr_price, 1),
        "prev_price": round(prev_price, 1),
        "price_change_pct": round(price_change_pct, 3),
        "oi_prev": round(prev_oi, 0),
        "oi_curr": round(curr_oi, 0),
        "oi_change_pct": round(oi_change_pct, 3),
        "oi_drop_usd": round(oi_drop_usd, 0),
        "timestamp": curr_ts,
    }


def format_tg_alert(liq: dict) -> str:
    """Format liquidation alert for Telegram."""
    liq_type = liq["type"]
    emoji = "🔴" if liq_type == "LONG" else "🟢"
    
    return (
        f"{emoji} **BTC Liquidation Cascade!**\n"
        f"Type: {liq_type}S liquidated\n"
        f"Price: ${liq['price']:,.0f} (was ${liq['prev_price']:,.0f}, {liq['price_change_pct']:+.2f}%)\n"
        f"OI Drop: {liq['oi_change_pct']:+.2f}% (≈${liq['oi_drop_usd']/1e6:.1f}M USD)\n"
        f"⏰ {datetime.fromtimestamp(liq['timestamp']/1000, HKT).strftime('%H:%M HKT')}"
    )


def get_heatmap_summary(zones: list, current_price: float) -> str:
    """Generate heatmap summary showing liquidation clusters near current price."""
    if not zones:
        return "No liquidation zones accumulated yet (need 1+ weeks of data)"
    
    now = datetime.now(HKT)
    
    # Group zones by price range (±$500 buckets)
    buckets = {}
    for z in zones:
        price = z.get("price", 0)
        bucket = round(price / 500) * 500
        if bucket not in buckets:
            buckets[bucket] = {"long_count": 0, "short_count": 0, "total_usd": 0, "prices": []}
        b = buckets[bucket]
        if z.get("type") == "LONG":
            b["long_count"] += 1
        else:
            b["short_count"] += 1
        b["total_usd"] += z.get("oi_drop_usd", 0)
        b["prices"].append(price)
    
    # Find zones near current price (±$2000)
    nearby = []
    for bucket_price, b in sorted(buckets.items()):
        if abs(bucket_price - current_price) <= 2000:
            nearby.append((bucket_price, b))
    
    lines = [
        f"📊 **BTC Liquidation Heatmap** — {now.strftime('%m/%d %H:%M HKT')}",
        f"💰 Current: ${current_price:,.0f}",
        f"📦 Total zones: {len(zones)} events",
        f"",
    ]
    
    if nearby:
        lines.append("📍 **Nearby Zones** (±$2,000):")
        for bucket_price, b in sorted(nearby, reverse=True):
            if bucket_price > current_price:
                pos = "ABOVE"
            elif bucket_price < current_price:
                pos = "BELOW"
            else:
                pos = "AT"
            
            long_bar = "🔴" * min(b["long_count"], 5)
            short_bar = "🟢" * min(b["short_count"], 5)
            distance = abs(bucket_price - current_price)
            lines.append(
                f"  ${bucket_price:,} ({pos}, {distance:,} away) | "
                f"LONG liq: {long_bar or '—'} ({b['long_count']}) | "
                f"SHORT liq: {short_bar or '—'} ({b['short_count']}) | "
                f"≈${b['total_usd']/1e6:.1f}M"
            )
    
    if not nearby:
        lines.append("📍 No liquidation zones within ±$2,000 of current price")
    
    # Summary: most liquidated zone
    if buckets:
        most_liq = max(buckets.items(), key=lambda x: x[1]["total_usd"])
        lines.append(f"")
        lines.append(f"🔥 **Most Liquidated Zone:** ${most_liq[0]:,} "
                     f"(≈${most_liq[1]['total_usd']/1e6:.1f}M total)")
    
    return "\n".join(lines)


def main():
    now = datetime.now(HKT)
    print(f"🔍 BTC Liquidation Zone Tracker — {now.strftime('%H:%M HKT')}")
    
    # Fetch current data
    oi_data = get_btc_oi()
    price_data = get_btc_price()
    
    if "_error" in oi_data or "_error" in price_data:
        print(f"❌ API Error: {oi_data.get('_error', price_data.get('_error', 'unknown'))}")
        return
    
    curr_oi = oi_data["oi"]
    curr_price = price_data["price"]
    curr_ts = oi_data.get("timestamp", int(time.time() * 1000))
    
    print(f"  OI: {curr_oi:,.0f} contracts | Price: ${curr_price:,.0f}")
    
    # Load previous state
    prev_state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                prev_state = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    
    # Detect liquidation
    result = detect_liquidation(prev_state, curr_oi, curr_price, curr_ts)
    
    if result["detected"]:
        print(f"\n  ⚡ LIQUIDATION DETECTED: {result['type']}")
        print(f"     Price: ${result['price']:,.0f} ({result['price_change_pct']:+.2f}%)")
        print(f"     OI Drop: {result['oi_change_pct']:+.2f}% (≈${result['oi_drop_usd']/1e6:.1f}M)")
        
        # Save to zones
        zones = load_zones()
        zones.append(result)
        # Keep last 500 events max
        if len(zones) > 500:
            zones = zones[-500:]
        save_zones(zones)
        
        # Print TG alert
        print(f"\n{format_tg_alert(result)}")
    else:
        reason = result.get("reason", "unknown")
        oi_chg = result.get("oi_change_pct", 0)
        px_chg = result.get("price_change_pct", 0)
        print(f"  No liquidation: {reason} (OI: {oi_chg:+.2f}%, Price: {px_chg:+.2f}%)")
    
    # Save current state for next comparison
    new_state = {
        "oi": curr_oi,
        "price": curr_price,
        "timestamp": curr_ts,
        "updated_at": now.isoformat(),
    }
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(new_state, f)
    os.replace(tmp, STATE_FILE)
    
    # Print heatmap summary (every 30 min)
    zones = load_zones()
    if zones and now.minute % 30 < 5:
        print(f"\n{get_heatmap_summary(zones, curr_price)}")
    
    return result


if __name__ == "__main__":
    main()
