#!/usr/bin/env python3
"""
P1.3 Vegas 通道研究
===================
研究問題：Vegas 通道 (EMA 144/169) 作為 dynamic S/R 有幾大 edge？
大漠哥核心 insight：Vegas 通道快軌(EMA 144)同幔軌(EMA 169)形成 dynamic 支撐/壓力區。

方法：
1. 計算 EMA 144 + EMA 169 通道
2. Backtest 幾種 setup：
   a) 價格回調踩通道 → LONG（支撐有效）
   b) 價格突破通道 → LONG（趨勢確認）
   c) 價格被通道壓制 → SHORT（壓力有效）
3. 同現有 BB/VP indicator edge 對比
"""
import numpy as np
import os, json
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.path.expanduser("~/workspace/data/market/phase1")
OUT_DIR = os.path.expanduser("~/workspace/research")

def ema(data, period):
    alpha = 2 / (period + 1)
    result = np.zeros_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
    return result

def vegas_channel(close):
    """Return EMA 144 (fast rail) and EMA 169 (slow rail)"""
    fast = ema(close, 144)
    slow = ema(close, 169)
    return fast, slow

# ── Setup Detection ──
def detect_setups(close, high, low, ema_fast, ema_slow, lookback=20):
    """
    Detect 4 setup types:
    1. BOUNCE_LONG: Price dips into channel zone then bounces up
    2. BREAKOUT_LONG: Price breaks above channel after being below
    3. REJECT_SHORT: Price rises into channel zone then gets rejected
    4. BREAKDOWN_SHORT: Price breaks below channel after being above
    """
    setups = []
    channel_upper = np.maximum(ema_fast, ema_slow)
    channel_lower = np.minimum(ema_fast, ema_slow)
    channel_mid = (ema_fast + ema_slow) / 2
    
    for i in range(lookback + 10, len(close) - 10):
        # ── BOUNCE LONG: price was above channel, dips into/below, then bounces ──
        if close[i] > channel_lower[i] and low[i] <= channel_lower[i] * 1.005:
            # Previous bars: price above channel
            above_count = sum(1 for j in range(i-lookback, i) if close[j] > channel_upper[j])
            if above_count >= lookback * 0.6:
                # Current bar: showing bounce (low touched channel, close > open)
                if close[i] > low[i] and close[i] >= channel_lower[i]:
                    setups.append({
                        "type": "BOUNCE_LONG",
                        "idx": i,
                        "price": float(close[i]),
                        "channel_lower": float(channel_lower[i]),
                        "distance_pct": float((close[i] / channel_lower[i] - 1) * 100),
                    })
        
        # ── BREAKOUT LONG: price breaks above channel ──
        if i > 5:
            was_below = all(close[j] < channel_upper[j] for j in range(i-5, i))
            is_above = close[i] > channel_upper[i]
            if was_below and is_above:
                setups.append({
                    "type": "BREAKOUT_LONG",
                    "idx": i,
                    "price": float(close[i]),
                    "channel_upper": float(channel_upper[i]),
                    "distance_pct": float((close[i] / channel_upper[i] - 1) * 100),
                })
        
        # ── REJECT SHORT: price rises into channel then rejects ──
        if close[i] < channel_upper[i] and high[i] >= channel_lower[i] * 0.995:
            below_count = sum(1 for j in range(i-lookback, i) if close[j] < channel_lower[j])
            if below_count >= lookback * 0.6:
                if close[i] < high[i] and close[i] <= channel_lower[i]:
                    setups.append({
                        "type": "REJECT_SHORT",
                        "idx": i,
                        "price": float(close[i]),
                        "channel_lower": float(channel_lower[i]),
                        "distance_pct": float((channel_lower[i] / close[i] - 1) * 100),
                    })
        
        # ── BREAKDOWN SHORT: price breaks below channel ──
        if i > 5:
            was_above = all(close[j] > channel_lower[j] for j in range(i-5, i))
            is_below = close[i] < channel_lower[i]
            if was_above and is_below:
                setups.append({
                    "type": "BREAKDOWN_SHORT",
                    "idx": i,
                    "price": float(close[i]),
                    "channel_lower": float(channel_lower[i]),
                    "distance_pct": float((channel_lower[i] / close[i] - 1) * 100),
                })
    
    return setups

# ── Backtest ──
def backtest_setups(setups, close, horizon_bars):
    """Backtest all setups"""
    results = {}
    for stype in ["BOUNCE_LONG", "BREAKOUT_LONG", "REJECT_SHORT", "BREAKDOWN_SHORT"]:
        results[stype] = []
    
    for setup in setups:
        idx = setup["idx"]
        if idx + horizon_bars >= len(close):
            continue
        
        entry = close[idx]
        exit_p = close[idx + horizon_bars]
        ret = (exit_p / entry - 1) * 100
        
        # For SHORT setups, expected return is negative
        s_type = setup["type"]
        results[s_type].append({
            "entry": entry,
            "exit": exit_p,
            "ret": ret,
            "idx": idx,
        })
    
    return results

# ── MAIN ──
def analyze_vegas(symbol, tf, tf_label):
    fname = f"{symbol}_{tf}.npz"
    fpath = os.path.join(DATA_DIR, fname)
    
    if not os.path.exists(fpath):
        return None
    
    data = np.load(fpath)
    close = data["close"]
    high = data["high"]
    low = data["low"]
    
    if len(close) < 200:
        return None
    
    e_fast, e_slow = vegas_channel(close)
    
    # ── Channel stats ──
    channel_width = np.abs(e_fast - e_slow) / close * 100
    price_above = sum(1 for i in range(len(close)) if close[i] > max(e_fast[i], e_slow[i]))
    
    print(f"\n{'='*60}")
    print(f"📊 {symbol} {tf_label} — Vegas 通道 (EMA 144/169)")
    print(f"  Data: {len(close)} bars")
    print(f"  平均通道寬度: {np.mean(channel_width):.2f}%")
    print(f"  價格在通道上方: {price_above/len(close)*100:.0f}% 時間")
    
    # ── Detect setups ──
    setups = detect_setups(close, high, low, e_fast, e_slow)
    
    print(f"\n  Setups detected:")
    for stype in ["BOUNCE_LONG", "BREAKOUT_LONG", "REJECT_SHORT", "BREAKDOWN_SHORT"]:
        count = sum(1 for s in setups if s["type"] == stype)
        print(f"    {stype:<20}: {count}")
    
    # ── Backtest at different horizons ──
    horizon_map = {
        "1h": {"12 bars": 12, "24 bars": 24, "48 bars": 48},
        "4h": {"3 bars": 3, "6 bars": 6, "12 bars": 12},
        "1d": {"1 bar": 1, "3 bars": 3, "5 bars": 5},
    }
    
    horizons = horizon_map.get(tf, {"12 bars": 12, "24 bars": 24})
    
    all_bt_results = {}
    for h_label, h_bars in horizons.items():
        bt = backtest_setups(setups, close, h_bars)
        all_bt_results[h_label] = bt
        
        print(f"\n  --- Forward {h_label} ---")
        for stype, expected_dir in [
            ("BOUNCE_LONG", "LONG ↑"),
            ("BREAKOUT_LONG", "LONG ↑"),
            ("REJECT_SHORT", "SHORT ↓"),
            ("BREAKDOWN_SHORT", "SHORT ↓"),
        ]:
            trades = bt[stype]
            if not trades:
                continue
            
            rets = [t["ret"] for t in trades]
            # For SHORT setups, check if price went down
            if "SHORT" in stype:
                wins = sum(1 for r in rets if r < 0)
            else:
                wins = sum(1 for r in rets if r > 0)
            
            n = len(trades)
            avg_ret = np.mean(rets)
            wr = wins / n * 100
            
            # Quality metric: how many met expectation
            print(f"    {stype:<20} n={n:>4}  avg={avg_ret:>+7.2f}%  "
                  f"wr={wr:>5.0f}%  ({expected_dir})")
    
    return {
        "symbol": symbol,
        "tf": tf_label,
        "setups": {stype: sum(1 for s in setups if s["type"] == stype) for stype in 
                   ["BOUNCE_LONG", "BREAKOUT_LONG", "REJECT_SHORT", "BREAKDOWN_SHORT"]},
        "results": all_bt_results,
    }

# ── RUN ALL ──
print("╔══════════════════════════════════════════════════════════╗")
print("║  P1.3 Vegas 通道 (EMA 144/169) 研究                       ║")
print("║  假設：Vegas 通道可作為 dynamic S/R，提高 entry edge        ║")
print("╚══════════════════════════════════════════════════════════╝")

configs = [
    ("BTC", "1h", "1h"),
    ("BTC", "4h", "4h"),
    ("ETH", "1h", "1h"),
    ("ETH", "4h", "4h"),
    ("SOL", "1h", "1h"),
    ("SOL", "4h", "4h"),
]

summary = {}
for sym, tf, tfl in configs:
    res = analyze_vegas(sym, tf, tfl)
    if res:
        summary[f"{sym}_{tfl}"] = res

# ── Summary ──
print(f"\n\n{'='*60}")
print("📋 總結：Vegas 通道 Setup Edge")
print(f"{'='*60}")

best_overall = {}
for key, res in summary.items():
    for stype in ["BOUNCE_LONG", "BREAKOUT_LONG", "REJECT_SHORT", "BREAKDOWN_SHORT"]:
        for h_label, bt in res["results"].items():
            trades = bt[stype]
            if len(trades) < 5:
                continue
            rets = [t["ret"] for t in trades]
            if "SHORT" in stype:
                wr = sum(1 for r in rets if r < 0) / len(rets) * 100
            else:
                wr = sum(1 for r in rets if r > 0) / len(rets) * 100
            avg_ret = np.mean(rets)
            combo = f"{stype}"
            if combo not in best_overall or wr > best_overall[combo][1]:
                best_overall[combo] = (f"{key} {h_label}", wr, avg_ret, len(trades))

print("\n🏆 Best setup per type:")
for stype, (loc, wr, avg, n) in sorted(best_overall.items()):
    print(f"  {stype:<20}: {loc:<25} WR={wr:.0f}% avg={avg:+.2f}% n={n}")

output = {
    "study": "P1.3 Vegas Channel",
    "timestamp": datetime.now(HKT).isoformat(),
    "hypothesis": "Vegas EMA 144/169 通道可作為 dynamic S/R",
    "summary": {k: {"setups": v["setups"]} for k, v in summary.items()},
}
with open(os.path.join(OUT_DIR, "p1_3_vegas_channel.json"), "w") as f:
    json.dump(output, f, indent=2, default=str)
print(f"\n結果已儲存: {os.path.join(OUT_DIR, 'p1_3_vegas_channel.json')}")
