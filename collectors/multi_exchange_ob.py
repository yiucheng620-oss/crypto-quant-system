#!/usr/bin/env python3
"""
Multi-Exchange Order Book Collector — Binance + Bybit + Hyperliquid
Cross-validate order book pressure across CEXs and DEX to detect spoofing.

Usage:
    python multi_exchange_ob.py

Output:
    ~/workspace/orderbook_data/multi_exchange_ob.json
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

import ccxt

# ── Config ──────────────────────────────────────────────────────────────────
OUTPUT_DIR = os.path.expanduser("~/workspace/orderbook_data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "multi_exchange_ob.json")

COINS = ["BTC", "ETH", "SOL"]
SYMBOLS = {coin: f"{coin}/USDT" for coin in COINS}  # ccxt symbol format

# Hyperliquid API (same as hl_orderbook.py)
HL_API_URL = "https://api.hyperliquid.xyz/info"

# For bid/ask ratio, we use top 10 levels within 1% of mid price
DEPTH_PCT = 0.01  # 1%

# Rate limiting: pause between exchange calls (seconds)
RATE_LIMIT_SLEEP = 1.5


# ── Hyperliquid Helpers (adapted from hl_orderbook.py) ──────────────────────
def _hl_post(payload: dict) -> dict | None:
    """POST to Hyperliquid info endpoint."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        HL_API_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
        print(f"  [ERROR] Hyperliquid API: {e}", file=sys.stderr)
        return None


def get_hl_mids() -> dict[str, float]:
    """Get mid prices for all coins from Hyperliquid."""
    result = _hl_post({"type": "allMids"})
    if result is None:
        return {}
    mids = {}
    for coin, price_str in result.items():
        try:
            mids[coin] = float(price_str)
        except (ValueError, TypeError):
            pass
    return mids


def get_hl_l2_book(coin: str) -> dict | None:
    """Get L2 order book for a coin from Hyperliquid."""
    return _hl_post({"type": "l2Book", "coin": coin})


def compute_hl_stats(book: dict, mid: float) -> dict:
    """
    Compute depth stats for Hyperliquid book.
    HL format: {"levels": [[bids...], [asks...]]}
    """
    levels_list = book.get("levels", [[], []])
    bids_raw = levels_list[0] if len(levels_list) > 0 else []
    asks_raw = levels_list[1] if len(levels_list) > 1 else []

    bid_volume_1pct = 0.0
    ask_volume_1pct = 0.0
    top_10_bid_vol = 0.0
    top_10_ask_vol = 0.0
    top_bid = None
    top_ask = None
    bid_levels = []
    ask_levels = []

    bid_price_floor = mid * (1 - DEPTH_PCT)
    ask_price_cap = mid * (1 + DEPTH_PCT)

    for level in bids_raw[:10]:
        px = float(level.get("px", 0))
        sz = float(level.get("sz", 0))
        if top_bid is None or px > top_bid:
            top_bid = px
        if px >= bid_price_floor:
            bid_volume_1pct += sz
        top_10_bid_vol += sz
        bid_levels.append({"px": px, "sz": sz})

    for level in asks_raw[:10]:
        px = float(level.get("px", 0))
        sz = float(level.get("sz", 0))
        if top_ask is None or px < top_ask:
            top_ask = px
        if px <= ask_price_cap:
            ask_volume_1pct += sz
        top_10_ask_vol += sz
        ask_levels.append({"px": px, "sz": sz})

    spread = (top_ask - top_bid) if top_bid and top_ask else None
    spread_pct = (spread / mid * 100) if spread and mid else None

    bid_ratio = bid_volume_1pct / ask_volume_1pct if ask_volume_1pct > 0 else None

    return {
        "mid": round(mid, 2),
        "top_bid": top_bid,
        "top_ask": top_ask,
        "spread": spread,
        "spread_pct": round(spread_pct, 4) if spread_pct else None,
        "bid_volume_1pct": round(bid_volume_1pct, 4),
        "ask_volume_1pct": round(ask_volume_1pct, 4),
        "total_volume_1pct": round(bid_volume_1pct + ask_volume_1pct, 4),
        "bid_ratio": round(bid_ratio, 4) if bid_ratio else None,
        "bid_levels": bid_levels,
        "ask_levels": ask_levels,
        "top10_bid_vol": round(top_10_bid_vol, 4),
        "top10_ask_vol": round(top_10_ask_vol, 4),
    }


def fetch_hyperliquid() -> dict[str, dict]:
    """Fetch order books for all coins from Hyperliquid."""
    print("[Hyperliquid] Fetching mids...")
    all_mids = get_hl_mids()
    print(f"[Hyperliquid] Got {len(all_mids)} mid prices")

    results = {}
    for coin in COINS:
        print(f"[Hyperliquid] Fetching L2 book for {coin}...")
        book = get_hl_l2_book(coin)
        if book is None:
            print(f"  [WARN] No book data for {coin}")
            results[coin] = {"error": "No data"}
            continue

        mid = all_mids.get(coin)
        if mid is None:
            levels_list = book.get("levels", [[], []])
            bids_arr = levels_list[0] if len(levels_list) > 0 else []
            asks_arr = levels_list[1] if len(levels_list) > 1 else []
            if bids_arr and asks_arr:
                mid = (float(bids_arr[0]["px"]) + float(asks_arr[0]["px"])) / 2
            else:
                mid = 0.0

        stats = compute_hl_stats(book, mid)
        print(f"  {coin}: mid={stats['mid']}, bid_ratio={stats['bid_ratio']}, "
              f"spread={stats['spread_pct']}%")
        results[coin] = stats

    return results


# ── CEX Helpers (ccxt: Binance / Bybit) ─────────────────────────────────────
def fmt_cex_stats(coin: str, orderbook: dict, mid: float) -> dict:
    """
    Compute depth stats from a ccxt orderbook object.
    orderbook: {"bids": [[px, sz], ...], "asks": [[px, sz], ...]}
    """
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])

    bid_volume_1pct = 0.0
    ask_volume_1pct = 0.0
    top_10_bid_vol = 0.0
    top_10_ask_vol = 0.0
    top_bid = None
    top_ask = None
    bid_levels = []
    ask_levels = []

    bid_price_floor = mid * (1 - DEPTH_PCT)
    ask_price_cap = mid * (1 + DEPTH_PCT)

    for px, sz in bids[:10]:
        px = float(px)
        sz = float(sz)
        if top_bid is None or px > top_bid:
            top_bid = px
        if px >= bid_price_floor:
            bid_volume_1pct += sz
        top_10_bid_vol += sz
        bid_levels.append({"px": px, "sz": sz})

    for px, sz in asks[:10]:
        px = float(px)
        sz = float(sz)
        if top_ask is None or px < top_ask:
            top_ask = px
        if px <= ask_price_cap:
            ask_volume_1pct += sz
        top_10_ask_vol += sz
        ask_levels.append({"px": px, "sz": sz})

    spread = (top_ask - top_bid) if top_bid and top_ask else None
    spread_pct = (spread / mid * 100) if spread and mid else None

    bid_ratio = bid_volume_1pct / ask_volume_1pct if ask_volume_1pct > 0 else None

    return {
        "mid": round(mid, 2),
        "top_bid": top_bid,
        "top_ask": top_ask,
        "spread": spread,
        "spread_pct": round(spread_pct, 4) if spread_pct else None,
        "bid_volume_1pct": round(bid_volume_1pct, 4),
        "ask_volume_1pct": round(ask_volume_1pct, 4),
        "total_volume_1pct": round(bid_volume_1pct + ask_volume_1pct, 4),
        "bid_ratio": round(bid_ratio, 4) if bid_ratio else None,
        "bid_levels": bid_levels,
        "ask_levels": ask_levels,
        "top10_bid_vol": round(top_10_bid_vol, 4),
        "top10_ask_vol": round(top_10_ask_vol, 4),
    }


def fetch_cex(exchange_name: str, exchange: ccxt.Exchange) -> dict[str, dict]:
    """Fetch order books for all coins from a CEX."""
    print(f"[{exchange_name}] Fetching order books...")
    results = {}
    for coin in COINS:
        symbol = SYMBOLS[coin]
        print(f"[{exchange_name}] Fetching {symbol}...")
        try:
            ob = exchange.fetch_order_book(symbol, limit=20)
            # Compute mid from top bid/ask
            top_bid = ob["bids"][0][0] if ob["bids"] else None
            top_ask = ob["asks"][0][0] if ob["asks"] else None
            if top_bid and top_ask:
                mid = (top_bid + top_ask) / 2
            elif top_bid:
                mid = top_bid
            elif top_ask:
                mid = top_ask
            else:
                print(f"  [WARN] Empty order book for {symbol}")
                results[coin] = {"error": "Empty order book"}
                continue

            stats = fmt_cex_stats(coin, ob, mid)
            print(f"  {coin}: mid={stats['mid']}, bid_ratio={stats['bid_ratio']}, "
                  f"spread={stats['spread_pct']}%")
            results[coin] = stats
        except Exception as e:
            print(f"  [ERROR] {exchange_name} {symbol}: {e}", file=sys.stderr)
            results[coin] = {"error": str(e)}

        # Rate limit: be kind to public APIs
        time.sleep(0.5)

    return results


# ── Cross-Validation ────────────────────────────────────────────────────────
def determine_pressure(data: dict) -> str | None:
    """Determine buy/sell/neutral pressure from an exchange's stats."""
    if "error" in data:
        return None
    ratio = data.get("bid_ratio")
    if ratio is None:
        return None
    if ratio > 1.15:
        return "buy"
    elif ratio < 0.85:
        return "sell"
    else:
        return "neutral"


def cross_validate(all_data: dict) -> dict:
    """
    Cross-validate across all exchanges for each coin.
    Returns dict with consensus and confidence per coin.
    """
    coin_results = {}
    for coin in COINS:
        pressures = {}
        for ex in ["binance", "bybit", "hyperliquid"]:
            ex_data = all_data.get(ex, {}).get(coin, {})
            pressures[ex] = determine_pressure(ex_data)

        # Count buy/sell signals (exclude None/error exchanges)
        buy_count = sum(1 for v in pressures.values() if v == "buy")
        sell_count = sum(1 for v in pressures.values() if v == "sell")
        neutral_count = sum(1 for v in pressures.values() if v == "neutral")
        total_valid = buy_count + sell_count + neutral_count

        # Determine consensus direction
        if buy_count > sell_count and buy_count > 0:
            consensus = "buy"
        elif sell_count > buy_count and sell_count > 0:
            consensus = "sell"
        elif buy_count == sell_count and buy_count > 0:
            consensus = "mixed"
        else:
            consensus = "neutral"

        # Determine confidence
        if total_valid >= 3 and (buy_count == 3 or sell_count == 3):
            confidence = "high"
        elif total_valid >= 2 and (buy_count >= 2 or sell_count >= 2):
            confidence = "medium"
        elif total_valid >= 1:
            confidence = "low"
        else:
            confidence = "none"

        # Spoofing flag: Hyperliquid shows pressure but both CEXs disagree
        spoofing_flags = []
        hl_press = pressures.get("hyperliquid")
        if hl_press in ("buy", "sell"):
            cex_agree = 0
            cex_total = 0
            for ex in ["binance", "bybit"]:
                p = pressures.get(ex)
                if p is not None:
                    cex_total += 1
                    if p == hl_press:
                        cex_agree += 1
            if cex_total >= 2 and cex_agree == 0:
                spoofing_flags.append(
                    "⚠️ Hyperliquid 買壓但 Binance/Bybit 唔確認 — 可能造假"
                    if hl_press == "buy"
                    else "⚠️ Hyperliquid 賣壓但 Binance/Bybit 唔確認 — 可能造假"
                )
            elif cex_total >= 1 and cex_agree == 0:
                spoofing_flags.append(
                    "⚠️ Hyperliquid 壓力未被 CEX 確認 — 謹慎"
                )

        coin_results[coin] = {
            "binance": {
                "bid_ratio": all_data.get("binance", {}).get(coin, {}).get("bid_ratio"),
                "spread_pct": all_data.get("binance", {}).get(coin, {}).get("spread_pct"),
                "mid": all_data.get("binance", {}).get(coin, {}).get("mid"),
                "bid_volume_1pct": all_data.get("binance", {}).get(coin, {}).get("bid_volume_1pct"),
                "ask_volume_1pct": all_data.get("binance", {}).get(coin, {}).get("ask_volume_1pct"),
                "pressure": pressures.get("binance"),
            },
            "bybit": {
                "bid_ratio": all_data.get("bybit", {}).get(coin, {}).get("bid_ratio"),
                "spread_pct": all_data.get("bybit", {}).get(coin, {}).get("spread_pct"),
                "mid": all_data.get("bybit", {}).get(coin, {}).get("mid"),
                "bid_volume_1pct": all_data.get("bybit", {}).get(coin, {}).get("bid_volume_1pct"),
                "ask_volume_1pct": all_data.get("bybit", {}).get(coin, {}).get("ask_volume_1pct"),
                "pressure": pressures.get("bybit"),
            },
            "hyperliquid": {
                "bid_ratio": all_data.get("hyperliquid", {}).get(coin, {}).get("bid_ratio"),
                "spread_pct": all_data.get("hyperliquid", {}).get(coin, {}).get("spread_pct"),
                "mid": all_data.get("hyperliquid", {}).get(coin, {}).get("mid"),
                "bid_volume_1pct": all_data.get("hyperliquid", {}).get(coin, {}).get("bid_volume_1pct"),
                "ask_volume_1pct": all_data.get("hyperliquid", {}).get(coin, {}).get("ask_volume_1pct"),
                "pressure": pressures.get("hyperliquid"),
            },
            "consensus": consensus,
            "confidence": confidence,
            "spoofing_flags": spoofing_flags,
        }

    return coin_results


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 60)
    print("  多交易所訂單簿收集器 Multi-Exchange OB Collector")
    print("=" * 60)
    print()

    all_data = {}

    # ── 1. Binance ──
    print("─" * 40)
    print("📊 Binance")
    print("─" * 40)
    try:
        binance = ccxt.binance({
            "enableRateLimit": True,
            "rateLimit": 1200,
            "options": {"defaultType": "spot"},
        })
        all_data["binance"] = fetch_cex("Binance", binance)
    except Exception as e:
        print(f"  [ERROR] Binance init failed: {e}", file=sys.stderr)
        all_data["binance"] = {c: {"error": str(e)} for c in COINS}

    time.sleep(RATE_LIMIT_SLEEP)

    # ── 2. Bybit ──
    print()
    print("─" * 40)
    print("📊 Bybit")
    print("─" * 40)
    try:
        bybit = ccxt.bybit({
            "enableRateLimit": True,
            "rateLimit": 1200,
        })
        all_data["bybit"] = fetch_cex("Bybit", bybit)
    except Exception as e:
        print(f"  [ERROR] Bybit init failed: {e}", file=sys.stderr)
        all_data["bybit"] = {c: {"error": str(e)} for c in COINS}

    time.sleep(RATE_LIMIT_SLEEP)

    # ── 3. Hyperliquid ──
    print()
    print("─" * 40)
    print("📊 Hyperliquid")
    print("─" * 40)
    all_data["hyperliquid"] = fetch_hyperliquid()

    # ── Cross-Validate ──
    print()
    print("=" * 60)
    print("  🔍 交叉驗證 Cross-Validation Results")
    print("=" * 60)
    coin_results = cross_validate(all_data)

    for coin in COINS:
        cr = coin_results[coin]
        print()
        print(f"── {coin} ──")
        print(f"  共識方向: {cr['consensus']}  |  信心度: {cr['confidence']}")
        print(f"  Binance:     pressure={cr['binance']['pressure']}, "
              f"bid_ratio={cr['binance']['bid_ratio']}, "
              f"spread={cr['binance']['spread_pct']}%")
        print(f"  Bybit:       pressure={cr['bybit']['pressure']}, "
              f"bid_ratio={cr['bybit']['bid_ratio']}, "
              f"spread={cr['bybit']['spread_pct']}%")
        print(f"  Hyperliquid: pressure={cr['hyperliquid']['pressure']}, "
              f"bid_ratio={cr['hyperliquid']['bid_ratio']}, "
              f"spread={cr['hyperliquid']['spread_pct']}%")
        for flag in cr["spoofing_flags"]:
            print(f"  🚩 {flag}")

    # ── Save ──
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    output = {
        "timestamp": timestamp_utc,
        "unix_seconds": int(time.time()),
        "source": "multi_exchange_ccxt",
        "coins": coin_results,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n✅ 已儲存至 {OUTPUT_FILE}")

    # ── Summary ──
    print()
    print("=" * 60)
    print("  📋 摘要 Summary")
    print("=" * 60)
    for coin in COINS:
        cr = coin_results[coin]
        emoji = {"high": "🟢", "medium": "🟡", "low": "🔴", "none": "⚪"}
        print(f"  {emoji.get(cr['confidence'], '❓')} {coin}: "
              f"{cr['consensus']} (confidence={cr['confidence']})")
        for flag in cr["spoofing_flags"]:
            print(f"     {flag}")

    print("\n✅ Done.")


if __name__ == "__main__":
    main()
