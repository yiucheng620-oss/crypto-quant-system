#!/usr/bin/env python3
"""Hyperliquid L2 Order Book Collector — BTC, ETH, SOL
Free public API, no API key needed.
Saves to ~/workspace/orderbook_data/orderbook_latest.json
"""

import json
import os
import time
import urllib.request
import urllib.error
import sys

OUTPUT_DIR = os.path.expanduser("~/workspace/orderbook_data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "orderbook_latest.json")
API_URL = "https://api.hyperliquid.xyz/info"

COINS = ["BTC", "ETH", "SOL"]


def post_hyperliquid(payload: dict) -> dict | None:
    """POST JSON to Hyperliquid info endpoint, return parsed response or None."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
        print(f"  [ERROR] Hyperliquid API call {payload.get('type')}: {e}", file=sys.stderr)
        return None


def get_all_mids() -> dict[str, float]:
    """Get mid prices for all coins from Hyperliquid."""
    result = post_hyperliquid({"type": "allMids"})
    if result is None:
        return {}
    mids = {}
    for coin, price_str in result.items():
        try:
            mids[coin] = float(price_str)
        except (ValueError, TypeError):
            pass
    return mids


def get_l2_book(coin: str) -> dict | None:
    """Get L2 order book for a given coin."""
    return post_hyperliquid({"type": "l2Book", "coin": coin})


def compute_depth_stats(book: dict, mid: float, depth_pct: float = 0.02) -> dict:
    """Compute bid/ask depth stats within depth_pct of mid price.
    Hyperliquid levels format: [bids_array, asks_array]
    """
    levels_list = book.get("levels", [[], []])
    bids = levels_list[0] if len(levels_list) > 0 else []
    asks = levels_list[1] if len(levels_list) > 1 else []
    
    bid_volume, ask_volume = 0.0, 0.0
    top_bid, top_ask = None, None
    bid_levels, ask_levels = [], []

    bid_price_floor = mid * (1 - depth_pct)
    ask_price_cap = mid * (1 + depth_pct)

    for level in bids[:10]:
        px = float(level.get("px", 0))
        sz = float(level.get("sz", 0))
        if top_bid is None or px > top_bid:
            top_bid = px
        if px >= bid_price_floor:
            bid_volume += sz
        bid_levels.append({"px": px, "sz": sz})

    for level in asks[:10]:
        px = float(level.get("px", 0))
        sz = float(level.get("sz", 0))
        if top_ask is None or px < top_ask:
            top_ask = px
        if px <= ask_price_cap:
            ask_volume += sz
        ask_levels.append({"px": px, "sz": sz})

    spread = (top_ask - top_bid) if top_bid and top_ask else None
    spread_pct = (spread / mid * 100) if spread and mid else None

    return {
        "mid": mid,
        "top_bid": top_bid,
        "top_ask": top_ask,
        "spread": spread,
        "spread_pct": spread_pct,
        "bid_volume_2pct": bid_volume,
        "ask_volume_2pct": ask_volume,
        "total_volume_2pct": bid_volume + ask_volume,
        "bid_levels": bid_levels,
        "ask_levels": ask_levels,
    }


def collect() -> dict:
    """Collect order book data for all configured coins."""
    print("[hl_orderbook] Fetching all mids...")
    all_mids = get_all_mids()
    print(f"[hl_orderbook] Got {len(all_mids)} mid prices")

    books = {}
    for coin in COINS:
        print(f"[hl_orderbook] Fetching L2 book for {coin}...")
        book = get_l2_book(coin)
        if book is None:
            print(f"  [WARN] No book data for {coin}")
            books[coin] = {"error": "No data"}
            continue
        
        mid = all_mids.get(coin)
        if mid is None:
            print(f"  [WARN] No mid price for {coin}, using book mid")
            levels_list = book.get("levels", [[], []])
            bids_arr = levels_list[0] if len(levels_list) > 0 else []
            asks_arr = levels_list[1] if len(levels_list) > 1 else []
            if bids_arr and asks_arr:
                mid = (float(bids_arr[0]["px"]) + float(asks_arr[0]["px"])) / 2
            else:
                mid = 0.0

        stats = compute_depth_stats(book, mid)
        stats["coin"] = coin
        books[coin] = stats
        
        spread_str = f"{stats['spread']:.6f} ({stats['spread_pct']:.4f}%)" if stats['spread'] else "N/A"
        print(f"  {coin}: mid={mid:.2f}, spread={spread_str}, "
              f"bid_vol={stats['bid_volume_2pct']:.4f}, ask_vol={stats['ask_volume_2pct']:.4f}")

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "unix_seconds": int(time.time()),
        "source": "hyperliquid_public_api",
        "coins": books,
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    data = collect()
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n[hl_orderbook] Saved to {OUTPUT_FILE}")
    print(f"[hl_orderbook] Done.")


if __name__ == "__main__":
    main()
