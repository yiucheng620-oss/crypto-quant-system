#!/usr/bin/env python3
"""Hyperliquid Funding Rate Collector — BTC, ETH, SOL
Free public API, no API key needed.
Saves to ~/workspace/funding_data/funding_latest.json
"""

import json
import os
import time
import urllib.request
import urllib.error
import sys

OUTPUT_DIR = os.path.expanduser("~/workspace/funding_data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "funding_latest.json")
API_URL = "https://api.hyperliquid.xyz/info"

TARGET_COINS = {"BTC", "ETH", "SOL"}


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
        print(f"  [ERROR] API call {payload.get('type')}: {e}", file=sys.stderr)
        return None


def compute_annualized(funding_rate: float, interval_hours: float = 1.0) -> float:
    """Convert per-interval funding rate to annualized %.
    Default: 1h funding → annualized = rate * 24 * 365 * 100
    """
    return funding_rate * (24 / interval_hours) * 365 * 100


def collect() -> dict:
    """Collect funding rate data for BTC, ETH, SOL."""
    print("[hl_funding] Fetching metaAndAssetCtxs...")
    result = post_hyperliquid({"type": "metaAndAssetCtxs"})

    if result is None:
        print("  [ERROR] metaAndAssetCtxs returned no data")
        return {"error": "No data from API", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # Result is a list: [universe_meta, asset_contexts]
    if not isinstance(result, list) or len(result) < 2:
        print(f"  [ERROR] Unexpected response format")
        return {"error": "Unexpected response format", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    meta = result[0]  # {"universe": [{"name": "BTC", ...}, ...], ...}
    ctxs = result[1]  # list of asset contexts

    universe = meta.get("universe", []) if isinstance(meta, dict) else []
    if not universe:
        print("  [ERROR] No universe metadata found")
        return {"error": "No universe", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # Build coin → index mapping
    coin_to_idx = {}
    for i, entry in enumerate(universe):
        name = entry.get("name", "")
        if isinstance(name, str):
            coin_to_idx[name] = i

    funding_data = {}

    for coin in TARGET_COINS:
        idx = coin_to_idx.get(coin)
        if idx is None or idx >= len(ctxs):
            print(f"  [WARN] {coin} not found in asset contexts")
            funding_data[coin] = {"error": "Not found in asset contexts"}
            continue

        ctx = ctxs[idx]
        try:
            # funding is typically in the asset context
            funding_str = ctx.get("funding", "0")
            funding_rate = float(funding_str)
            
            # Also try to get open interest, etc.
            oi_str = ctx.get("openInterest", "0")
            open_interest = float(oi_str)
            
            annualized = compute_annualized(funding_rate)
            
            funding_data[coin] = {
                "funding_rate": funding_rate,
                "funding_rate_pct": funding_rate * 100,
                "annualized_pct": round(annualized, 4),
                "open_interest": open_interest,
                "premium": ctx.get("premium"),
                "oracle_price": ctx.get("oraclePx"),
                "mark_price": ctx.get("markPx"),
                "day_volume_ntl": ctx.get("dayNtlVlm"),
                "day_base_volume": ctx.get("dayBaseVlm"),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            print(f"  {coin}: rate={funding_rate:.7f} ({funding_rate*100:.5f}%), "
                  f"annualized={annualized:.2f}%, OI={open_interest:.2f}, "
                  f"premium={ctx.get('premium', '?')}")
        except (ValueError, TypeError, IndexError) as e:
            print(f"  [ERROR] Parsing {coin} context: {e}")
            funding_data[coin] = {"error": str(e)}

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "unix_seconds": int(time.time()),
        "source": "hyperliquid_public_api",
        "funding": funding_data,
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    data = collect()
    
    # Save latest
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n[hl_funding] Saved to {OUTPUT_FILE}")
    
    # Append to funding history (for squeeze backtest)
    history_file = os.path.join(OUTPUT_DIR, "funding_history.json")
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            history = []
    
    # Keep last 500 records (~21 days for 1h collection)
    history.append(data)
    if len(history) > 500:
        history = history[-500:]
    
    with open(history_file, "w") as f:
        json.dump(history, f, indent=1, default=str)
    print(f"[hl_funding] History: {len(history)} records saved")
    print(f"[hl_funding] Done.")


if __name__ == "__main__":
    main()
