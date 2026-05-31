#!/usr/bin/env python3
"""BTC/ETH ETF Flow Tracker
Uses Yahoo Finance free public data (no API key).
Volume-based flow proxy: compares latest volume vs 4-day average.
Saves to ~/workspace/etf_data/etf_flow_latest.json
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

OUTPUT_DIR = os.path.expanduser("~/workspace/etf_data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "etf_flow_latest.json")

# ── BTC Spot ETF Tickers ──
BTC_ETF_TICKERS = ["IBIT", "FBTC", "GBTC", "ARKB", "BITB", "BTCO", "EZBC", "BRRR", "BTCW", "DEFI", "HODL"]
# ── ETH Spot ETF Tickers ──
ETH_ETF_TICKERS = ["ETHA", "FETH", "ETHW", "CETH", "ETHV", "QETH"]


def fetch_yahoo_chart(ticker: str) -> dict | None:
    """Fetch daily chart data from Yahoo Finance (free, no API key)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [WARN] {ticker}: {e}", file=sys.stderr)
        return None


def extract_volume(data: dict) -> dict:
    """Extract volume info from Yahoo chart data."""
    result = data.get("chart", {}).get("result", [{}])[0]
    indicators = result.get("indicators", {})
    quote = indicators.get("quote", [{}])[0]
    volumes = quote.get("volume", [])
    timestamps = result.get("timestamp", [])

    if not volumes:
        return {"error": "No volume data"}

    # Filter valid (ts, vol) pairs
    valid = [(ts, vol) for ts, vol in zip(timestamps, volumes) if vol is not None]
    if not valid:
        return {"error": "No valid volume data"}

    latest_ts, latest_vol = valid[-1]
    # Average of previous days
    avg_vol = (sum(v for _, v in valid[:-1]) / max(len(valid) - 1, 1)) if len(valid) > 1 else latest_vol

    return {
        "latest_volume": latest_vol,
        "avg_volume_4d": round(avg_vol, 2),
        "volume_ratio": round(latest_vol / avg_vol, 4) if avg_vol > 0 else 0,
        "date": time.strftime("%Y-%m-%d", time.gmtime(latest_ts)),
    }


def fetch_etf_volumes(tickers: list[str], label: str) -> dict:
    """Fetch volume for all tickers in a list with rate limiting."""
    print(f"[etf_flow] Fetching {label} ETF volume data...")
    results = {}
    for ticker in tickers:
        raw = fetch_yahoo_chart(ticker)
        if raw is None:
            results[ticker] = {"error": "No response"}
        else:
            vol = extract_volume(raw)
            results[ticker] = vol
            if "volume_ratio" in vol:
                print(f"  {ticker}: vol={vol['latest_volume']:,.0f}, ratio={vol['volume_ratio']:.2f}x")
        time.sleep(0.3)  # Rate limit
    return results


def estimate_flow(volumes: dict[str, dict]) -> dict:
    """Estimate net flow from volume data (volume proxy)."""
    total_vol = 0.0
    total_avg = 0.0
    count = 0
    for ticker, data in volumes.items():
        if "latest_volume" in data and "avg_volume_4d" in data:
            total_vol += data["latest_volume"]
            total_avg += data["avg_volume_4d"]
            count += 1

    excess = (total_vol - total_avg) if count > 0 and total_avg > 0 else None

    return {
        "total_latest_volume": total_vol,
        "total_avg_volume": total_avg,
        "estimated_excess_volume": excess,
        "funds_tracked": count,
        "note": "Volume-based proxy — not actual net flow. Compares latest day volume vs 4-day average.",
    }


def collect() -> dict:
    """Collect ETF flow data for BTC and ETH ETFs."""
    btc_volumes = fetch_etf_volumes(BTC_ETF_TICKERS, "BTC")
    eth_volumes = fetch_etf_volumes(ETH_ETF_TICKERS, "ETH")

    btc_flow = estimate_flow(btc_volumes)
    eth_flow = estimate_flow(eth_volumes)

    print(f"\n[etf_flow] BTC total excess volume: {btc_flow['estimated_excess_volume']:,.0f}" if btc_flow['estimated_excess_volume'] else "")
    print(f"[etf_flow] ETH total excess volume: {eth_flow['estimated_excess_volume']:,.0f}" if eth_flow['estimated_excess_volume'] else "")

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "unix_seconds": int(time.time()),
        "source": "yahoo_finance_volume_proxy",
        "btc_etf": {
            "estimated_net_flow_proxy": btc_flow,
            "individual_tickers": btc_volumes,
        },
        "eth_etf": {
            "estimated_net_flow_proxy": eth_flow,
            "individual_tickers": eth_volumes,
        },
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    data = collect()
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n[etf_flow] Saved to {OUTPUT_FILE}")
    print("[etf_flow] Done.")


if __name__ == "__main__":
    main()
