#!/usr/bin/env python3
"""
Deribit Options Data Collector
===============================
Uses Deribit public REST API (no API key needed).
Calculates max pain, put/call ratio, and key strike levels
for BTC and ETH options. Saves results to JSON.

API docs: https://docs.deribit.com/#public-get_book_summary_by_currency
"""

import json
import os
import urllib.request
import urllib.error
import ssl
from datetime import datetime
from collections import defaultdict

# ── Configuration ──────────────────────────────────────────────────────────
BASE_URL = "https://www.deribit.com/api/v2/public"
OUTPUT_DIR = os.path.expanduser("~/workspace/options_data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "options_latest.json")
TIMEOUT = 30  # seconds


def fetch_json(url: str) -> dict | None:
    """Fetch JSON from a URL with SSL context and timeout."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "deribit-collector/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
        print(f"  [ERROR] Failed to fetch {url}: {e}")
        return None


def fetch_book_summaries(currency: str) -> list[dict]:
    """Fetch /get_book_summary_by_currency for a given currency."""
    url = f"{BASE_URL}/get_book_summary_by_currency?currency={currency}&kind=option"
    print(f"  Fetching {currency} options summary...")
    data = fetch_json(url)
    if data is None or data.get("error"):
        err_msg = data.get("error", {}).get("message", "unknown error") if data else "no response"
        print(f"  [ERROR] API error for {currency}: {err_msg}")
        return []
    result = data.get("result", [])
    print(f"    Got {len(result)} options")
    return result


def compute_metrics(options: list[dict]) -> dict:
    """Compute max pain, put/call ratio, and top strikes from option summaries."""
    if not options:
        return {
            "max_pain": None,
            "put_call_ratio": None,
            "total_oi": 0,
            "top_strikes": [],
            "gamma_profile": [],
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    # Group by strike — parse from instrument_name like "BTC-31JUL26-115000-C"
    by_strike = defaultdict(lambda: {"call_oi": 0.0, "put_oi": 0.0, "call_vol": 0.0, "put_vol": 0.0})

    for opt in options:
        instr = opt.get("instrument_name", "")
        oi = float(opt.get("open_interest", 0) or 0)
        vol = float(opt.get("volume", 0) or 0)

        # Parse instrument name: CURRENCY-DATE-STRIKE-TYPE
        parts = instr.rsplit("-", 2)  # split on last two hyphens
        if len(parts) != 3:
            continue
        strike_str, kind = parts[1], parts[2].lower()
        try:
            strike = float(strike_str)
        except (ValueError, TypeError):
            continue

        if kind == "c":
            by_strike[strike]["call_oi"] += oi
            by_strike[strike]["call_vol"] += vol
        elif kind == "p":
            by_strike[strike]["put_oi"] += oi
            by_strike[strike]["put_vol"] += vol

    if not by_strike:
        return {
            "max_pain": None,
            "put_call_ratio": None,
            "total_oi": 0,
            "top_strikes": [],
            "gamma_profile": [],
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    # ── Total Open Interest ────────────────────────────────────────────────
    total_call_oi = sum(s["call_oi"] for s in by_strike.values())
    total_put_oi = sum(s["put_oi"] for s in by_strike.values())
    total_oi = total_call_oi + total_put_oi

    # ── Put/Call Ratio ─────────────────────────────────────────────────────
    put_call_ratio = round(total_put_oi / total_call_oi, 4) if total_call_oi > 0 else None

    # ── Max Pain (simplified: strike with highest total OI) ────────────────
    strikes_with_total = []
    for strike, data in by_strike.items():
        total = data["call_oi"] + data["put_oi"]
        strikes_with_total.append((strike, total, data["call_oi"], data["put_oi"]))

    strikes_with_total.sort(key=lambda x: x[1], reverse=True)
    max_pain_strike = strikes_with_total[0][0] if strikes_with_total else None

    # ── Gamma Exposure Profile (estimated from OI distribution) ────────────
    # Normalized OI distribution as a proxy for gamma concentration
    gamma_profile = []
    for strike, total, call_oi, put_oi in strikes_with_total:
        if total_oi > 0:
            gamma_profile.append({
                "strike": strike,
                "total_oi": total,
                "call_oi": call_oi,
                "put_oi": put_oi,
                "oi_pct": round(total / total_oi * 100, 2),
            })
    # Sort by strike for profile
    gamma_profile.sort(key=lambda x: x["strike"])
    # Take top 20 for reasonable size
    gamma_profile = gamma_profile[:20]

    # ── Top Strikes ────────────────────────────────────────────────────────
    top_strikes = [
        {
            "strike": s[0],
            "total_oi": s[1],
            "call_oi": s[2],
            "put_oi": s[3],
        }
        for s in strikes_with_total[:10]
    ]

    return {
        "max_pain": max_pain_strike,
        "put_call_ratio": put_call_ratio,
        "total_oi": total_oi,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "top_strikes": top_strikes,
        "gamma_profile": gamma_profile,
        "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main():
    print("=" * 60)
    print("Deribit Options Data Collector")
    print("=" * 60)
    print()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    result = {}

    for currency in ("BTC", "ETH"):
        options = fetch_book_summaries(currency)
        metrics = compute_metrics(options)
        result[currency] = metrics

        print(f"  ── {currency} Metrics ──")
        mp = metrics["max_pain"]
        pcr = metrics["put_call_ratio"]
        print(f"  Max Pain:       {mp:,}" if mp else "  Max Pain:       N/A")
        print(f"  Put/Call Ratio: {pcr}" if pcr else "  Put/Call Ratio: N/A")
        print(f"  Total OI:       {metrics['total_oi']:,.0f}")
        print(f"  Call OI:        {metrics['total_call_oi']:,.0f}")
        print(f"  Put OI:         {metrics['total_put_oi']:,.0f}")
        print(f"  Top strikes:    {[s['strike'] for s in metrics['top_strikes'][:5]]}")
        print()

    # ── Save to JSON ──────────────────────────────────────────────────────
    output = {
        "source": "Deribit Public API",
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": result,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  ✓ Saved to {OUTPUT_FILE}")
    print(f"  ✓ File size: {os.path.getsize(OUTPUT_FILE):,} bytes")
    print("=" * 60)


if __name__ == "__main__":
    main()
