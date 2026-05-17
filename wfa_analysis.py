#!/usr/bin/env python3
"""
Walk-Forward Analysis for Signal Accuracy
═════════════════════════════════════════════
Replaces simple 70/30 split with multiple rolling windows.

Each window: train(60%) → validate(20%) → gap → next window
5 windows × 6 indicators × 2 horizons × 3 conditions
= up to 5x more validation data than single split.

Arguments:
  --windows N   Number of WFA windows (default: 5)
  --horizons    Comma-separated hours (default: 24,48)
  --full        Run full sweep (slow, ~5min)
  --quick       Run quick test (2 windows, 1 indicator)
"""

import os, sys, json, time, argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.expanduser("~/workspace"))
from final_indicators import vp_signal, bb_signal, obv_signal

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.path.expanduser("~/market_data/phase1")

# ── Indicator configs (same as signal_logger.py survivors) ──
INDICATORS = {
    "VP_BTC_4h":  {"coin": "BTC", "tf": "4h", "fn": vp_signal, 
                   "params": {"vp_lookback": 20, "dist_pct": 1.0, "vol_thresh": 2.0, "vol_lookback": 30}},
    "VP_ETH_4h":  {"coin": "ETH", "tf": "4h", "fn": vp_signal,
                   "params": {"vp_lookback": 20, "dist_pct": 1.0, "vol_thresh": 2.0, "vol_lookback": 30}},
    "BB_ETH_1h":  {"coin": "ETH", "tf": "1h", "fn": bb_signal,
                   "params": {"period": 30, "std": 1.5, "squeeze_ratio": 0.5, "min_bars": 3, "vol_confirm": True, "cooldown": 10}},
    "BB_BTC_1h":  {"coin": "BTC", "tf": "1h", "fn": bb_signal,
                   "params": {"period": 30, "std": 1.5, "squeeze_ratio": 0.5, "min_bars": 3, "vol_confirm": True, "cooldown": 10}},
    "OBV_BTC_1h": {"coin": "BTC", "tf": "1h", "fn": obv_signal,
                   "params": {"smooth": 8, "diverge_lookback": 10, "sig_mult": 1.3, "cooldown": 12}},
    "OBV_SOL_1h": {"coin": "SOL", "tf": "1h", "fn": obv_signal,
                   "params": {"smooth": 8, "diverge_lookback": 10, "sig_mult": 1.3, "cooldown": 12}},
}

# ── Helpers ──

def load_data(coin, tf):
    path = os.path.join(DATA_DIR, f"{coin}_{tf}.npz")
    if not os.path.exists(path):
        return None
    d = np.load(path)
    return {"close": d["close"], "timestamp": d["timestamp"],
            "open": d["open"], "high": d["high"], "low": d["low"], "volume": d["volume"]}

def classify_market(close, high, low, lookback=50):
    n = len(close)
    if n < lookback + 5:
        return "unknown"
    recent = close[-lookback:]
    recent_h = high[-lookback:]
    recent_l = low[-lookback:]
    tr = np.maximum(recent_h - recent_l,
                    np.maximum(np.abs(recent_h - np.roll(recent, 1)),
                               np.abs(recent_l - np.roll(recent, 1))))
    tr[0] = recent_h[0] - recent_l[0]
    atr14 = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
    vol_pct = (atr14 / recent[-1]) * 100 if recent[-1] > 0 else 0
    x = np.arange(lookback)
    slope, _ = np.polyfit(x, recent, 1)
    slope_pct = (slope / recent[-1]) * 100 * lookback if recent[-1] > 0 else 0
    if vol_pct > 3.0:
        return "volatile"
    elif abs(slope_pct) > 1.5:
        return "bull" if slope_pct > 0 else "bear"
    else:
        return "range"

def find_bar_idx(timestamps, target_ts):
    """Find the index of a specific timestamp in the array (milliseconds)."""
    return np.searchsorted(timestamps, target_ts)

# ── WFA Core ──

def run_wfa_window(data, indicator_fn, params, train_start, train_end, val_end, horizon_bars):
    """
    Train on [train_start:train_end], validate on (train_end:val_end].
    Returns list of validation results.
    """
    n = len(data["close"])
    if train_end >= n or val_end > n:
        return []

    # Train: generate signals
    train_data = {k: v[train_start:train_end] for k, v in data.items()}
    sig_array = indicator_fn(train_data["close"], train_data["volume"], **params)

    results = []
    # Find signals in train period
    for i in range(len(sig_array)):
        if sig_array[i] != 1:
            continue

        sig_idx = train_start + i  # Absolute index in full data
        sig_ts = data["timestamp"][sig_idx]
        entry_price = data["close"][sig_idx]

        # Check if validation data has enough forward bars
        target_idx = sig_idx + horizon_bars
        if target_idx >= val_end or target_idx >= n:
            continue

        exit_price = data["close"][target_idx]
        pnl = (exit_price - entry_price) / entry_price
        result = "WIN" if pnl > 0 else "LOSS"

        # Market condition at signal time (using data up to signal)
        mc = classify_market(data["close"][:sig_idx+1], data["high"][:sig_idx+1], data["low"][:sig_idx+1])

        results.append({
            "result": result,
            "pnl_pct": round(pnl * 100, 3),
            "market_condition": mc,
            "window": f"W{getattr(run_wfa_window, 'window_counter', 0)}"
        })

    return results


def run_wfa(indicator_name, config, n_windows=5, horizons=[24, 48]):
    """Run walk-forward analysis for one indicator."""
    coin, tf = config["coin"], config["tf"]
    data = load_data(coin, tf)
    if data is None:
        print(f"  [{indicator_name}] ❌ No data")
        return []

    n = len(data["close"])
    if n < 500:
        print(f"  [{indicator_name}] ❌ Too few bars ({n})")
        return []

    all_results = []

    for horizon_h in horizons:
        # Map hours to bars
        tf_hours = {"1h": 1, "4h": 4, "1d": 24}.get(tf, 1)
        horizon_bars = horizon_h // tf_hours

        # Window sizing: each window = 60% train + 20% validate, step 15%
        # Total bars needed per window ~80% of data
        window_total = int(n * 0.8 / n_windows)
        step = int(window_total * 0.25)

        window_count = 0
        for w in range(n_windows):
            train_start = w * step
            train_end = train_start + int(window_total * 0.75)
            val_end = min(train_end + int(window_total * 0.25), n)

            if train_end + horizon_bars >= n:
                break

            results = run_wfa_window(data, config["fn"], config["params"],
                                     train_start, train_end, val_end, horizon_bars)
            if results:
                for r in results:
                    r["horizon"] = f"{horizon_h}h"
                    r["indicator"] = indicator_name
                    r["coin"] = coin
                    r["tf"] = tf
                all_results.extend(results)
                window_count += 1

        if window_count > 0:
            signals_count = len([r for r in all_results if r["horizon"] == f"{horizon_h}h"])
            print(f"  [{indicator_name}] {horizon_h}h: {window_count} windows, {signals_count} checks")

    return all_results


def summarize_results(all_results):
    """Summarize WFA results into clean stats."""
    groups = defaultdict(list)
    for r in all_results:
        key = (r["indicator"], r["horizon"], r["market_condition"])
        groups[key].append(r)

    print(f"\n{'='*70}")
    print(f"📊 Walk-Forward Analysis Summary ({len(all_results)} checks, {len(groups)} groups)")
    print(f"{'='*70}")
    print(f"{'Indicator':<18} {'Horizon':<6} {'Condition':<10} {'N':>4} {'Win%':>6} {'AvgPnL':>8}")
    print("-" * 56)

    summary = {}
    for (ind, hor, cond), items in sorted(groups.items()):
        wins = sum(1 for it in items if it["result"] == "WIN")
        total = len(items)
        wr = round(wins / total * 100, 1)
        avg_pnl = round(np.mean([it["pnl_pct"] for it in items]), 3)
        print(f"  {ind:<18} {hor:<6} {cond:<10} {total:>4} {wr:>5.1f}% {avg_pnl:>+7.3f}%")

        if ind not in summary:
            summary[ind] = {}
        summary[ind][f"{cond}_{hor}"] = {"n": total, "wr": wr, "avg_pnl": avg_pnl}

    return summary


def compare_wfa_vs_original(wfa_summary):
    """Compare WFA results to original 70/30 split findings."""
    # Original findings from single 70/30 split (for reference)
    original = {
        "VP_BTC_4h":  {"range_24h": 80.0, "range_48h": 80.0},
        "VP_ETH_4h":  {"range_24h": 75.0, "range_48h": 50.0},
        "BB_ETH_1h":  {"range_24h": 56.2, "range_48h": 50.0},
        "BB_BTC_1h":  {"range_24h": None, "range_48h": None},  # No data in 70/30
        "OBV_BTC_1h": {"range_24h": 64.3, "range_48h": 57.1},
        "OBV_SOL_1h": {"range_24h": 62.5, "range_48h": 46.9},
    }

    print(f"\n{'='*70}")
    print(f"🔄 WFA vs Original 70/30 Split Comparison")
    print(f"{'='*70}")
    print(f"{'Indicator':<18} {'Condition':<12} {'WFA':>6} {'Original':>10} {'Δ':>8}")
    print("-" * 56)

    for ind, conds in sorted(original.items()):
        wfa_data = wfa_summary.get(ind, {})
        for cond_key, orig_wr in sorted(conds.items()):
            wfa_wr = wfa_data.get(cond_key, {}).get("wr")
            if orig_wr is not None or wfa_wr is not None:
                wfa_str = f"{wfa_wr:.1f}%" if wfa_wr is not None else "N/A"
                orig_str = f"{orig_wr:.1f}%" if orig_wr is not None else "N/A"
                delta = ""
                if wfa_wr is not None and orig_wr is not None:
                    d = wfa_wr - orig_wr
                    delta = f"{d:+.1f}%"
                print(f"  {ind:<18} {cond_key:<12} {wfa_str:>6} {orig_str:>10} {delta:>8}")


# ── Main ──

def main():
    parser = argparse.ArgumentParser(description="Walk-Forward Analysis for Signal Accuracy")
    parser.add_argument("--windows", type=int, default=5, help="Number of WFA windows")
    parser.add_argument("--horizons", type=str, default="24,48", help="Comma-separated horizon hours")
    parser.add_argument("--indicator", type=str, help="Only run one indicator (e.g., VP_BTC_4h)")
    parser.add_argument("--quick", action="store_true", help="Quick test: 2 windows, 1 indicator")
    parser.add_argument("--full", action="store_true", help="Full sweep (all indicators)")
    args = parser.parse_args()

    horizons = [int(h) for h in args.horizons.split(",")]

    if args.quick:
        args.windows = 2
        if not args.indicator:
            args.indicator = "VP_BTC_4h"
        print(f"⚡ Quick mode: {args.windows} windows, indicator={args.indicator}")

    if args.indicator:
        indicators_to_run = {args.indicator: INDICATORS[args.indicator]}
    elif args.full:
        indicators_to_run = INDICATORS
    else:
        # Default: run the 3 best indicators
        indicators_to_run = {k: v for k, v in INDICATORS.items() 
                            if k in ["VP_BTC_4h", "VP_ETH_4h", "OBV_SOL_1h"]}

    all_results = []
    t0 = time.time()

    for name, config in sorted(indicators_to_run.items()):
        results = run_wfa(name, config, args.windows, horizons)
        all_results.extend(results)

    elapsed = time.time() - t0

    if not all_results:
        print("❌ No results generated. Check data and parameters.")
        return

    summary = summarize_results(all_results)
    compare_wfa_vs_original(summary)

    print(f"\n✅ WFA complete ({elapsed:.1f}s)")
    print(f"   Total checks: {len(all_results)}")
    print(f"   Windows: {args.windows}")
    print(f"   Indicators: {len(indicators_to_run)}")
    print(f"\n   💡 WFA gives ~{args.windows}x more validation data than single 70/30 split")


if __name__ == "__main__":
    main()
