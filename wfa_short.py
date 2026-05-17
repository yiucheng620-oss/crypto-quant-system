#!/usr/bin/env python3
"""
Short-Only Walk-Forward Analysis
══════════════════════════════════════
从零开始：所有 original indicator 嘅 inverse（做空）版本。
完整 pipeline：Phase 8 → 70/30 → WFA 5 windows。
"""

import os, sys, time, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.expanduser("~/workspace"))

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.path.expanduser("~/market_data/phase1")

# ═══════════════════════════════════════════════════════════
# INVERSE INDICATOR FUNCTIONS (Short Only)
# ═══════════════════════════════════════════════════════════

def _ema(d, p):
    a = 2/(p+1); r = np.zeros_like(d); r[0] = d[0]
    for i in range(1, len(d)): r[i] = a*d[i] + (1-a)*r[i-1]
    return r

def _atr(high, low, close, p=14):
    n = len(close); tr = np.zeros(n); tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
    r = np.full(n, np.nan)
    for i in range(p-1, n): r[i] = np.mean(tr[i-p+1:i+1])
    return r

# ── INV-VP: SELL when price ABOVE SMA + volume spike (inverse of VP buy) ──

def vp_short_signal(close, volume, vp_lookback=20, dist_pct=1.0, vol_thresh=2.0, vol_lookback=30):
    n = len(close)
    start = max(0, n - 2000)
    c = close[start:]; v = volume[start:]
    ns = len(c)
    sma = np.full(ns, np.nan)
    sma[vp_lookback-1:] = np.convolve(c, np.ones(vp_lookback)/vp_lookback, mode='valid')
    vm = np.full(ns, np.nan)
    for i in range(vol_lookback, ns+1): vm[i-1] = np.mean(v[i-vol_lookback:i])
    sigs = np.zeros(ns, dtype=int)
    w = max(vp_lookback, vol_lookback) + 10
    for i in range(w, ns):
        if np.isnan(sma[i]) or np.isnan(vm[i-1]): continue
        dist = abs(c[i] - sma[i]) / sma[i] * 100
        # SELL: price ABOVE SMA + volume spike → overbought bounce down
        if dist < dist_pct and c[i] > sma[i] and v[i] > vm[i-1] * vol_thresh:
            sigs[i] = 1
    full = np.zeros(n, dtype=int)
    full[start:] = sigs
    return full

# ── INV-BB: SELL when squeeze breaks downward ──

def bb_short_signal(close, volume, period=30, std=1.5, squeeze_ratio=0.5, min_bars=3, vol_confirm=True, cooldown=10):
    n = len(close)
    ma = np.full(n, np.nan); ma[period-1:] = np.convolve(close, np.ones(period)/period, mode='valid')
    rs = np.full(n, np.nan)
    for i in range(period, n): rs[i] = np.std(close[i-period:i])
    upper = ma + std * rs; lower = ma - std * rs
    v20 = np.full(n, np.nan)
    for i in range(20, n+1): v20[i-1] = np.mean(volume[i-20:i])
    sigs = np.zeros(n, dtype=int)
    w = period + 25; in_squeeze = 0; last_signal = -999
    for i in range(w, n):
        if i - last_signal < cooldown: continue
        if np.isnan(upper[i]): continue
        bw = upper[i] - lower[i]
        bw20 = np.nanmean(upper[max(0,i-20):i] - lower[max(0,i-20):i])
        if bw20 == 0 or np.isnan(bw20): continue
        if bw < bw20 * squeeze_ratio:
            in_squeeze += 1
            # SELL: squeeze breaks DOWNWARD (close < previous)
            if in_squeeze >= min_bars and close[i] < close[i-1]:
                ok = True
                if vol_confirm: ok = not np.isnan(v20[i-1]) and volume[i] > v20[i-1] * 1.2
                if ok: sigs[i] = 1; last_signal = i
        else:
            in_squeeze = 0
    return sigs

# ── INV-OBV: SELL when bearish divergence (price HH, OBV LH) ──

def obv_short_signal(close, volume, smooth=8, diverge_lookback=10, sig_mult=1.3, cooldown=12):
    n = len(close)
    obv = np.zeros(n); obv[0] = volume[0]
    for i in range(1, n):
        if close[i] > close[i-1]: obv[i] = obv[i-1] + volume[i]
        elif close[i] < close[i-1]: obv[i] = obv[i-1] - volume[i]
        else: obv[i] = obv[i-1]
    if smooth > 1: obv_s = np.convolve(obv, np.ones(smooth)/smooth, mode='same')
    else: obv_s = obv
    sigs = np.zeros(n, dtype=int)
    w = max(diverge_lookback, smooth) + 10; last_signal = -999
    for i in range(w, n):
        if i - last_signal < cooldown: continue
        # SELL: price HIGHER high, OBV LOWER high = bearish divergence
        if close[i] > close[i-diverge_lookback] and obv_s[i] < obv_s[i-diverge_lookback]:
            chg = obv_s[i] - obv_s[i-diverge_lookback]
            std = np.std(obv_s[max(0,i-50):i])
            if std > 0 and abs(chg) >= std * sig_mult: sigs[i] = 1; last_signal = i
    return sigs

# ── INV-MACD: SELL on bearish crossover ──

def macd_short_signal(close, volume=None, fast=12, slow=26, sig=9):
    if volume is not None and len(volume) < len(close):
        close_for_macd = close[len(close)-len(volume):]
    else:
        close_for_macd = close
    n = len(close_for_macd)
    ema_fast = _ema(close_for_macd, fast)
    ema_slow = _ema(close_for_macd, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, sig)
    hist = macd_line - signal_line
    sigs = np.zeros(n, dtype=int)
    for i in range(1, n):
        if hist[i] < 0 and hist[i-1] > 0:
            sigs[i] = 1
    if len(close) > n:
        full = np.zeros(len(close), dtype=int)
        full[-n:] = sigs
        return full
    return sigs

# ── INV-RSI: SELL on overbought (>70) ──

def rsi_short_signal(close, volume=None, period=14, overbought=70):
    if volume is not None and len(volume) < len(close):
        close_for_rsi = close[len(close)-len(volume):]
    else:
        close_for_rsi = close
    n = len(close_for_rsi)
    deltas = np.diff(close_for_rsi, prepend=close_for_rsi[0])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.full(n, np.nan); avg_loss = np.full(n, np.nan)
    avg_gain[period] = np.mean(gains[:period+1]); avg_loss[period] = np.mean(losses[:period+1])
    for i in range(period+1, n):
        avg_gain[i] = (avg_gain[i-1]*(period-1) + gains[i]) / period
        avg_loss[i] = (avg_loss[i-1]*(period-1) + losses[i]) / period
    rs_arr = np.where(avg_loss > 0, avg_gain/avg_loss, 100)
    rsi_arr = 100 - (100/(1+rs_arr))
    sigs = np.zeros(n, dtype=int)
    for i in range(1, n):
        if not np.isnan(rsi_arr[i]) and rsi_arr[i] > overbought and rsi_arr[i-1] <= overbought:
            sigs[i] = 1
    if len(close) > n:
        full = np.zeros(len(close), dtype=int)
        full[-n:] = sigs
        return full
    return sigs

# ── INV-EMA: SELL on bearish EMA crossover (fast below slow) ──

def ema_short_signal(close, volume=None, fast=20, slow=50):
    if volume is not None and len(volume) < len(close):
        close_for_ema = close[len(close)-len(volume):]
    else:
        close_for_ema = close
    n = len(close_for_ema)
    ema_fast = _ema(close_for_ema, fast)
    ema_slow = _ema(close_for_ema, slow)
    sigs = np.zeros(n, dtype=int)
    for i in range(1, n):
        if ema_fast[i] < ema_slow[i] and ema_fast[i-1] > ema_slow[i-1]:
            sigs[i] = 1
    if len(close) > n:
        full = np.zeros(len(close), dtype=int)
        full[-n:] = sigs
        return full
    return sigs


# ═══════════════════════════════════════════════════════════
# ALL INDICATOR CONFIGS (Short-Only)
# ═══════════════════════════════════════════════════════════

INDICATORS = {
    # VP short
    "VP_BTC_4h_S":  {"coin": "BTC", "tf": "4h", "fn": vp_short_signal, "params": {"vp_lookback": 20, "dist_pct": 1.0, "vol_thresh": 2.0, "vol_lookback": 30}},
    "VP_BTC_1h_S":  {"coin": "BTC", "tf": "1h", "fn": vp_short_signal, "params": {"vp_lookback": 20, "dist_pct": 0.5, "vol_thresh": 1.75, "vol_lookback": 30}},
    "VP_ETH_4h_S":  {"coin": "ETH", "tf": "4h", "fn": vp_short_signal, "params": {"vp_lookback": 20, "dist_pct": 1.0, "vol_thresh": 2.0, "vol_lookback": 30}},
    "VP_ETH_1h_S":  {"coin": "ETH", "tf": "1h", "fn": vp_short_signal, "params": {"vp_lookback": 20, "dist_pct": 0.5, "vol_thresh": 1.75, "vol_lookback": 30}},
    "VP_SOL_1h_S":  {"coin": "SOL", "tf": "1h", "fn": vp_short_signal, "params": {"vp_lookback": 20, "dist_pct": 0.5, "vol_thresh": 1.75, "vol_lookback": 30}},
    # BB short
    "BB_BTC_4h_S":  {"coin": "BTC", "tf": "4h", "fn": bb_short_signal, "params": {"period": 30, "std": 1.5, "squeeze_ratio": 0.4, "min_bars": 1, "vol_confirm": True, "cooldown": 5}},
    "BB_BTC_1h_S":  {"coin": "BTC", "tf": "1h", "fn": bb_short_signal, "params": {"period": 30, "std": 1.5, "squeeze_ratio": 0.5, "min_bars": 3, "vol_confirm": True, "cooldown": 10}},
    "BB_ETH_4h_S":  {"coin": "ETH", "tf": "4h", "fn": bb_short_signal, "params": {"period": 30, "std": 1.5, "squeeze_ratio": 0.4, "min_bars": 1, "vol_confirm": True, "cooldown": 5}},
    "BB_ETH_1h_S":  {"coin": "ETH", "tf": "1h", "fn": bb_short_signal, "params": {"period": 30, "std": 1.5, "squeeze_ratio": 0.5, "min_bars": 3, "vol_confirm": True, "cooldown": 10}},
    "BB_SOL_1h_S":  {"coin": "SOL", "tf": "1h", "fn": bb_short_signal, "params": {"period": 30, "std": 1.5, "squeeze_ratio": 0.5, "min_bars": 3, "vol_confirm": True, "cooldown": 10}},
    # OBV short
    "OBV_BTC_4h_S": {"coin": "BTC", "tf": "4h", "fn": obv_short_signal, "params": {"smooth": 8, "diverge_lookback": 10, "sig_mult": 1.3, "cooldown": 12}},
    "OBV_BTC_1h_S": {"coin": "BTC", "tf": "1h", "fn": obv_short_signal, "params": {"smooth": 8, "diverge_lookback": 10, "sig_mult": 1.3, "cooldown": 12}},
    "OBV_ETH_1h_S": {"coin": "ETH", "tf": "1h", "fn": obv_short_signal, "params": {"smooth": 8, "diverge_lookback": 10, "sig_mult": 1.3, "cooldown": 12}},
    "OBV_SOL_1h_S": {"coin": "SOL", "tf": "1h", "fn": obv_short_signal, "params": {"smooth": 8, "diverge_lookback": 10, "sig_mult": 1.3, "cooldown": 12}},
    # MACD short
    "MACD_BTC_1h_S": {"coin": "BTC", "tf": "1h", "fn": macd_short_signal, "params": {"sig": 9}},
    "MACD_ETH_1h_S": {"coin": "ETH", "tf": "1h", "fn": macd_short_signal, "params": {"sig": 9}},
    "MACD_SOL_1h_S": {"coin": "SOL", "tf": "1h", "fn": macd_short_signal, "params": {"sig": 9}},
    # RSI short
    "RSI_BTC_1h_S":  {"coin": "BTC", "tf": "1h", "fn": rsi_short_signal,  "params": {"overbought": 70}},
    "RSI_ETH_1h_S":  {"coin": "ETH", "tf": "1h", "fn": rsi_short_signal,  "params": {"overbought": 70}},
    "RSI_SOL_1h_S":  {"coin": "SOL", "tf": "1h", "fn": rsi_short_signal,  "params": {"overbought": 70}},
    # EMA short
    "EMA_BTC_4h_S":  {"coin": "BTC", "tf": "4h", "fn": ema_short_signal,  "params": {}},
    "EMA_ETH_4h_S":  {"coin": "ETH", "tf": "4h", "fn": ema_short_signal,  "params": {}},
}

# ═══════════════════════════════════════════════════════════
# HELPERS (same as WFA)
# ═══════════════════════════════════════════════════════════

def load_data(coin, tf):
    path = os.path.join(DATA_DIR, f"{coin}_{tf}.npz")
    if not os.path.exists(path): return None
    d = np.load(path)
    return {"close": d["close"], "timestamp": d["timestamp"],
            "open": d["open"], "high": d["high"], "low": d["low"], "volume": d["volume"]}

def classify_market(close, high, low, lookback=50):
    n = len(close)
    if n < lookback + 5: return "unknown"
    recent = close[-lookback:]; recent_h = high[-lookback:]; recent_l = low[-lookback:]
    tr = np.maximum(recent_h - recent_l,
                    np.maximum(np.abs(recent_h - np.roll(recent, 1)),
                               np.abs(recent_l - np.roll(recent, 1))))
    tr[0] = recent_h[0] - recent_l[0]
    atr14 = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
    vol_pct = (atr14 / recent[-1]) * 100 if recent[-1] > 0 else 0
    x = np.arange(lookback)
    slope, _ = np.polyfit(x, recent, 1)
    slope_pct = (slope / recent[-1]) * 100 * lookback if recent[-1] > 0 else 0
    if vol_pct > 3.0: return "volatile"
    elif abs(slope_pct) > 1.5: return "bull" if slope_pct > 0 else "bear"
    else: return "range"

def run_wfa_window(data, indicator_fn, params, train_start, train_end, val_end, horizon_bars):
    n = len(data["close"])
    if train_end >= n or val_end > n: return []
    train_data = {k: v[train_start:train_end] for k, v in data.items()}
    sig_array = indicator_fn(train_data["close"], train_data["volume"], **params)
    results = []
    for i in range(len(sig_array)):
        if sig_array[i] != 1: continue
        sig_idx = train_start + i
        target_idx = sig_idx + horizon_bars
        if target_idx >= val_end or target_idx >= n: continue
        entry = data["close"][sig_idx]; exit_px = data["close"][target_idx]
        # SHORT: profit when price goes DOWN
        pnl = (entry - exit_px) / entry  # positive when exit < entry
        result = "WIN" if pnl > 0 else "LOSS"
        mc = classify_market(data["close"][:sig_idx+1], data["high"][:sig_idx+1], data["low"][:sig_idx+1])
        results.append({"result": result, "pnl_pct": round(pnl * 100, 3), "market_condition": mc})
    return results

# ═══════════════════════════════════════════════════════════
# WFA RUNNER
# ═══════════════════════════════════════════════════════════

def run_wfa(name, config, n_windows=5):
    coin, tf = config["coin"], config["tf"]
    data = load_data(coin, tf)
    if data is None: return []
    n = len(data["close"])
    if n < 500: return []
    all_results = []
    for horizon_h in [24, 48]:
        tf_hours = {"1h": 1, "4h": 4, "1d": 24}.get(tf, 1)
        horizon_bars = horizon_h // tf_hours
        window_total = int(n * 0.8 / n_windows)
        step = int(window_total * 0.25)
        for w in range(n_windows):
            train_start = w * step
            train_end = train_start + int(window_total * 0.75)
            val_end = min(train_end + int(window_total * 0.25), n)
            if train_end + horizon_bars >= n: break
            res = run_wfa_window(data, config["fn"], config["params"],
                                train_start, train_end, val_end, horizon_bars)
            for r in res:
                r["horizon"] = f"{horizon_h}h"; r["indicator"] = name; r["coin"] = coin; r["tf"] = tf
            all_results.extend(res)
    return all_results

# ═══════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════

def summarize_and_filter(all_results, min_signals=3, min_wr=55):
    """Summarize, then apply survival criteria."""
    groups = defaultdict(list)
    for r in all_results:
        key = (r["indicator"], r["horizon"], r["market_condition"])
        groups[key].append(r)
    
    survivors = {}
    killed = {}
    
    for (ind, hor, cond), items in sorted(groups.items()):
        wins = sum(1 for it in items if it["result"] == "WIN")
        total = len(items)
        wr = round(wins / total * 100, 1)
        avg_pnl = round(np.mean([it["pnl_pct"] for it in items]), 3)
        
        if wr >= min_wr and total >= min_signals:
            key2 = ind
            if key2 not in survivors: survivors[key2] = []
            survivors[key2].append(f"  {hor} {cond:8s} {total:3d}sigs {wr:5.1f}% WR {avg_pnl:+.3f}%")
        else:
            kill_reason = f"WR={wr}%" if wr < min_wr else f"n={total}(<{min_signals})"
            if ind not in killed: killed[ind] = []
            killed[ind].append(f"  {hor} {cond:8s} {kill_reason} {total}sigs")
    
    # Only survivors with >=2 alive conditions
    final_survivors = {k: v for k, v in survivors.items() if len(v) >= 2}
    final_killed = {k: v for k, v in killed.items() if k not in final_survivors}
    for k in survivors:
        if k not in final_survivors:
            if k not in final_killed: final_killed[k] = survivors[k]
            else: final_killed[k].extend(survivors[k])
    
    return final_survivors, final_killed

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("🔻 SHORT-ONLY Walk-Forward Analysis")
    print("=" * 70)
    print(f"Indicators: {len(INDICATORS)} | Windows: 5 | Horizons: 24h,48h")
    print()
    
    all_results = []
    t0 = time.time()
    
    for name, config in sorted(INDICATORS.items()):
        results = run_wfa(name, config, 5)
        all_results.extend(results)
        sig_count = len([r for r in results if r["horizon"] == "24h"])
        if sig_count > 0:
            print(f"  [{name:<18}] {sig_count:>4} checks (24h)")
    
    elapsed = time.time() - t0
    print(f"\nTotal: {len(all_results)} checks in {elapsed:.1f}s")
    
    if not all_results:
        print("❌ No results")
        return
    
    # Pass 1: raw summary (all indicator × condition combos)
    groups = defaultdict(list)
    for r in all_results:
        key = (r["indicator"], r["horizon"], r["market_condition"])
        groups[key].append(r)
    
    print(f"\n{'='*70}")
    print(f"📊 All Results ({len(groups)} groups)")
    print(f"{'='*70}")
    print(f"{'Indicator':<18} {'Hor':<5} {'Cond':<9} {'N':>4} {'Win%':>6} {'AvgPnL':>8}")
    print("-" * 54)
    for (ind, hor, cond), items in sorted(groups.items()):
        wins = sum(1 for it in items if it["result"] == "WIN")
        total = len(items)
        wr = round(wins / total * 100, 1)
        avg_pnl = round(np.mean([it["pnl_pct"] for it in items]), 3)
        print(f"  {ind:<18} {hor:<5} {cond:<9} {total:>4} {wr:>5.1f}% {avg_pnl:>+7.3f}%")
    
    # Pass 2: Filter for survivors
    print(f"\n{'='*70}")
    print(f"🔍 SURVIVOR FILTER (≥3 signals, ≥55% WR, ≥2 conditions alive)")
    print(f"{'='*70}")
    
    survivors, killed = summarize_and_filter(all_results)
    
    if survivors:
        print(f"\n✅ SURVIVORS ({len(survivors)}):")
        for ind, lines in sorted(survivors.items()):
            print(f"  [{ind}]")
            for line in lines:
                print(line)
    else:
        print("\n❌ NO SURVIVORS — 全部 short indicator 都唔达标")
    
    if killed:
        print(f"\n❌ KILLED ({len(killed)}):")
        for ind, lines in sorted(killed.items()):
            print(f"  [{ind}] — KILLED:")
            for line in lines:
                print(line)
    
    # Compare with Long results
    print(f"\n{'='*70}")
    print(f"🔄 SHORT vs LONG Comparison")
    print(f"{'='*70}")
    long_wr = {
        "VP_BTC_4h": 77.8, "OBV_SOL_1h": 66.7, "OBV_BTC_1h": 100.0,
        "BB_ETH_1h": 50.0, "BB_BTC_1h": 50.0, "VP_ETH_4h": 100.0,
    }
    
    print(f"{'Indicator':<18} {'Long WR':>8} {'Short WR':>9} {'Δ':>8} {'Verdict':>10}")
    print("-" * 58)
    for ind, lwr in sorted(long_wr.items()):
        short_ind = ind + "_S"
        short_data = groups.get((short_ind, "24h", "bull"), [])
        swr = round(sum(1 for r in short_data if r["result"]=="WIN")/len(short_data)*100, 1) if short_data else None
        swr_str = f"{swr}%" if swr is not None else "N/A"
        delta = f"{(swr or 0) - lwr:+.1f}%" if swr is not None else "N/A"
        # Best for this indicator (short or long)
        if swr is not None and swr >= 55: verdict = "⚡ SHORT"
        elif lwr >= 55: verdict = "🔥 LONG"
        else: verdict = "❌ BOTH"
        print(f"  {ind:<19} {lwr:>7.1f}% {swr_str:>9} {delta:>8} {verdict:>10}")
    
    print(f"\n✅ Done ({elapsed:.1f}s)")
    print(f"   Total checks: {len(all_results)}")
    print(f"   Survivors: {len(survivors)}")
    print(f"   Killed: {len(killed)}")

if __name__ == "__main__":
    main()