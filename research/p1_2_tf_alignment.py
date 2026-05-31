#!/usr/bin/env python3
"""
P1.2 多時間框架確認層研究
========================
研究問題：大級別 bias + 小級別 signal = 更高 edge？
大漠哥核心 insight：唔睇單一 TF，大級別定方向 → 中級別定結構 → 小級別定入場。

方法：
1. 用大級別 EMA trend 定 bias（4h/1d）
2. 用小級別 MACD 背離定 signal（1h/15m）
3. 比較：aligned (bias=signal) vs misaligned (bias≠signal) 嘅 forward return
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

def macd(close, fast=12, slow=26, signal=9):
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

# ── TF Alignment Logic ──
def resample_to_higher_tf(timestamps, data, target_tf_minutes):
    """
    Resample lower TF data to higher TF.
    Returns aligned high-TF series matching low-TF index.
    """
    high_tf_data = np.zeros_like(data)
    
    bucket = int(data[0])
    bucket_val = data[0]
    
    for i in range(len(data)):
        ts_minutes = timestamps[i] // 60
        current_bucket = ts_minutes // target_tf_minutes
        
        if current_bucket != bucket or i == len(data) - 1:
            # Fill previous bucket
            for j in range(max(0, i-100), i):
                if j < len(high_tf_data):
                    pass
        
        bucket = current_bucket
    
    return high_tf_data  # placeholder

def analyze_tf_alignment(symbol, high_tf, low_tf, high_label, low_label):
    """
    Analyze: does high TF trend alignment improve low TF signal edge?
    
    high_tf: 4h or 1d (direction bias)
    low_tf: 1h or 15m (entry signal)
    """
    high_file = os.path.join(DATA_DIR, f"{symbol}_{high_tf}.npz")
    low_file = os.path.join(DATA_DIR, f"{symbol}_{low_tf}.npz")
    
    if not os.path.exists(high_file) or not os.path.exists(low_file):
        return None
    
    high_data = np.load(high_file)
    low_data = np.load(low_file)
    
    high_close = high_data["close"]
    high_ts = high_data["timestamp"]
    low_close = low_data["close"]
    low_ts = low_data["timestamp"]
    
    # ── High TF: Direction Bias (EMA trend) ──
    high_ema_fast = ema(high_close, 20)
    high_ema_slow = ema(high_close, 50)
    high_bias = np.where(high_ema_fast > high_ema_slow, 1, -1)  # 1=LONG, -1=SHORT
    high_bias_bars = np.arange(len(high_bias))
    
    # ── Low TF: Entry Signal (MACD bullish divergence) ──
    _, _, l_hist = macd(low_close)
    
    # Map each low-TF bar to its corresponding high-TF bias
    # For each low TF timestamp, find the most recent high TF bar
    low_bias = np.zeros(len(low_close), dtype=int)
    for i, ts in enumerate(low_ts):
        # Find most recent high TF bar before this time
        idx = np.searchsorted(high_ts, ts, side='right') - 1
        idx = max(0, min(idx, len(high_bias) - 1))
        low_bias[i] = high_bias[idx]
    
    # ── Simple entry signal: MACD histogram turning positive ──
    l_signal = np.zeros(len(low_close), dtype=int)
    for i in range(1, len(low_close)):
        if l_hist[i] > 0 and l_hist[i-1] <= 0:
            l_signal[i] = 1  # Bullish crossover
        elif l_hist[i] < 0 and l_hist[i-1] >= 0:
            l_signal[i] = -1  # Bearish crossover
    
    # ── Backtest: Aligned vs Misaligned ──
    results = {
        "aligned_long": [],   # high_bias=LONG + low_signal=LONG
        "aligned_short": [],  # high_bias=SHORT + low_signal=SHORT
        "misaligned": [],     # bias ≠ signal
        "all_signals": [],    # all signals regardless of bias
    }
    
    for horizon_bars, h_label in [(12, "12 bars"), (24, "24 bars"), (48, "48 bars")]:
        for i in range(len(low_close) - horizon_bars):
            sig = l_signal[i]
            if sig == 0:
                continue
            
            bias = low_bias[i]
            entry = low_close[i]
            exit_p = low_close[i + horizon_bars]
            ret = (exit_p / entry - 1) * 100
            
            # Directional return (positive if signal direction matches)
            dir_ret = ret if sig == 1 else -ret
            
            trade = {"ret": ret, "dir_ret": dir_ret, "h_label": h_label}
            
            if sig == 1 and bias == 1:
                results["aligned_long"].append(trade)
            elif sig == -1 and bias == -1:
                results["aligned_short"].append(trade)
            elif sig != 0 and bias != 0 and sig != bias:
                results["misaligned"].append(trade)
            
            results["all_signals"].append(trade)
    
    # ── Print Results ──
    print(f"\n{'='*60}")
    print(f"📊 {symbol}: {high_label}(bias) → {low_label}(signal) — TF Alignment")
    
    for h_label in ["12 bars", "24 bars", "48 bars"]:
        print(f"\n  --- Forward {h_label} ---")
        
        best_wr = 0
        best_label = ""
        
        for cat in ["aligned_long", "aligned_short", "misaligned", "all_signals"]:
            trades = [t for t in results[cat] if t["h_label"] == h_label]
            if not trades:
                continue
            
            rets = [t["ret"] for t in trades]
            dir_rets = [t["dir_ret"] for t in trades]
            n = len(trades)
            avg_ret = np.mean(rets)
            avg_dir = np.mean(dir_rets)
            wr = sum(1 for r in dir_rets if r > 0) / n * 100
            
            if wr > best_wr:
                best_wr = wr
                best_label = cat
            
            print(f"    {cat:<20} n={n:>4}  avg={avg_ret:>+7.2f}%  "
                  f"dir_avg={avg_dir:>+7.2f}%  wr={wr:>5.0f}%")
        
        print(f"    → Best: {best_label} (WR={best_wr:.0f}%)")
    
    # Special: compare aligned vs misaligned statistically
    aligned = results["aligned_long"] + results["aligned_short"]
    
    for h_label in ["12 bars", "24 bars", "48 bars"]:
        a_rets = [t["dir_ret"] for t in aligned if t["h_label"] == h_label]
        m_rets = [t["dir_ret"] for t in results["misaligned"] if t["h_label"] == h_label]
        
        if a_rets and m_rets:
            a_wr = sum(1 for r in a_rets if r > 0) / len(a_rets) * 100
            m_wr = sum(1 for r in m_rets if r > 0) / len(m_rets) * 100
            a_avg = np.mean(a_rets)
            m_avg = np.mean(m_rets)
            
            improvement = a_wr - m_wr
            symbol_emoji = "✅" if improvement > 3 else ("➡️" if improvement > 0 else "❌")
            
            print(f"\n  🔬 {h_label} Aligned vs Misaligned:")
            print(f"    Aligned:    WR={a_wr:.0f}%, avg={a_avg:+.2f}% (n={len(a_rets)})")
            print(f"    Misaligned: WR={m_wr:.0f}%, avg={m_avg:+.2f}% (n={len(m_rets)})")
            print(f"    ΔWR: {improvement:+.1f}% {symbol_emoji}")
    
    return results

# ── RUN ──
print("╔══════════════════════════════════════════════════════════╗")
print("║  P1.2 多 TF 確認層研究                                    ║")
print("║  假設：大級別 bias 對齊小級別 signal → 更高 win rate      ║")
print("╚══════════════════════════════════════════════════════════╝")

configs = [
    ("BTC", "4h", "1h", "4h", "1h"),
    ("BTC", "1d", "4h", "1d", "4h"),
    ("ETH", "4h", "1h", "4h", "1h"),
    ("ETH", "1d", "4h", "1d", "4h"),
    ("SOL", "4h", "1h", "4h", "1h"),
    ("SOL", "1d", "4h", "1d", "4h"),
]

all_results = {}
for sym, htf, ltf, hl, ll in configs:
    res = analyze_tf_alignment(sym, htf, ltf, hl, ll)
    if res:
        all_results[f"{sym}_{hl}→{ll}"] = res

print(f"\n\n{'='*60}")
print("📋 總結：Cross-TF Alignment Edge")
print(f"{'='*60}")

output = {
    "study": "P1.2 Cross-TF Alignment",
    "timestamp": datetime.now(HKT).isoformat(),
    "hypothesis": "大級別 bias 對齊小級別 signal 可提高 win rate",
}
with open(os.path.join(OUT_DIR, "p1_2_tf_alignment.json"), "w") as f:
    json.dump(output, f, indent=2, default=str)
print(f"結果已儲存: {os.path.join(OUT_DIR, 'p1_2_tf_alignment.json')}")
