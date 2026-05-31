#!/usr/bin/env python3
"""
P1.1 MACD 背離復活研究
======================
研究問題：「背了又背」（連續背離）係咪比 1次背離更可靠？
大漠哥核心 insight：MACD 背離要分級 — 1次、2次、連續背離 — 可靠性遞增。

方法：
1. Detect MACD 背離（價格新低但 MACD histogram 不跟隨）
2. 分級：1次背離 vs 2次/連續背離（背了又背）
3. Backtest forward return per signal grade
4. 多 TF analysis：1h / 4h / 1d
"""
import numpy as np
import os, json
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.path.expanduser("~/workspace/data/market/phase1")
OUT_DIR = os.path.expanduser("~/workspace/research")
os.makedirs(OUT_DIR, exist_ok=True)

# ── MACD 計算 ──
def ema(data, period):
    """Exponential Moving Average"""
    alpha = 2 / (period + 1)
    result = np.zeros_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
    return result

def macd(close, fast=12, slow=26, signal=9):
    """Return: macd_line, signal_line, histogram"""
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

# ── 背離 Detection ──
def find_swing_points(data, lookback=5):
    """Find local minima AND maxima"""
    mins = []
    maxs = []
    for i in range(lookback, len(data) - lookback):
        window = data[i-lookback:i+lookback+1]
        if data[i] == min(window) and data[i] < min(data[max(0,i-lookback):i]) and data[i] <= min(data[i+1:min(len(data),i+lookback+1)]):
            mins.append(i)
        if data[i] == max(window) and data[i] > max(data[max(0,i-lookback):i]) and data[i] >= max(data[i+1:min(len(data),i+lookback+1)]):
            maxs.append(i)
    return mins, maxs

def detect_divergences(price, macd_line, hist, lookback=5):
    """
    Detect both BULLISH and BEARISH divergences.
    
    Bullish (bottom): Price lower low, MACD higher low → LONG signal
    Bearish (top): Price higher high, MACD lower high → SHORT signal
    
    Grades: 1st divergence (fresh) vs 2nd+ (背了又背)
    """
    price_lows, price_highs = find_swing_points(price, lookback)
    macd_lows, macd_highs = find_swing_points(macd_line, lookback)
    hist_lows, hist_highs = find_swing_points(hist, lookback)
    
    divergences = []
    
    # ── Bullish Divergence (LONG signal) ──
    for i in range(1, len(price_lows)):
        p_prev, p_curr = price_lows[i-1], price_lows[i]
        
        # Price must make lower low
        if price[p_curr] >= price[p_prev]:
            continue
        
        # Find nearest MACD/histogram lows
        nearest_macd_prev = min(macd_lows, key=lambda x: abs(x - p_prev), default=None)
        nearest_macd_curr = min(macd_lows, key=lambda x: abs(x - p_curr), default=None)
        nearest_hist_prev = min(hist_lows, key=lambda x: abs(x - p_prev), default=None)
        nearest_hist_curr = min(hist_lows, key=lambda x: abs(x - p_curr), default=None)
        
        if nearest_macd_prev is None or nearest_macd_curr is None:
            continue
        
        # Must be within reasonable distance (10 bars)
        if abs(nearest_macd_prev - p_prev) > 10 or abs(nearest_macd_curr - p_curr) > 10:
            continue
        
        # Divergence: MACD or Histogram higher low
        macd_div = macd_line[nearest_macd_curr] > macd_line[nearest_macd_prev]
        hist_div = (nearest_hist_prev and nearest_hist_curr and 
                    hist[nearest_hist_curr] > hist[nearest_hist_prev])
        
        if macd_div or hist_div:
            # Grade: consecutive?
            count = 1
            for prev_div in reversed(divergences):
                if prev_div["type"] == "BULLISH" and p_curr - prev_div["price_idx"] < 200:
                    if price[p_curr] <= price[prev_div["price_idx"]]:
                        count = prev_div["count"] + 1
                        break
            
            divergences.append({
                "type": "BULLISH",
                "price_idx": p_curr,
                "price": float(price[p_curr]),
                "macd_val": float(macd_line[nearest_macd_curr]),
                "count": count,
                "macd_div": macd_div,
                "hist_div": hist_div,
            })
    
    # ── Bearish Divergence (SHORT signal) ──
    for i in range(1, len(price_highs)):
        p_prev, p_curr = price_highs[i-1], price_highs[i]
        
        # Price must make higher high
        if price[p_curr] <= price[p_prev]:
            continue
        
        nearest_macd_prev = min(macd_highs, key=lambda x: abs(x - p_prev), default=None)
        nearest_macd_curr = min(macd_highs, key=lambda x: abs(x - p_curr), default=None)
        nearest_hist_prev = min(hist_highs, key=lambda x: abs(x - p_prev), default=None)
        nearest_hist_curr = min(hist_highs, key=lambda x: abs(x - p_curr), default=None)
        
        if nearest_macd_prev is None or nearest_macd_curr is None:
            continue
        
        if abs(nearest_macd_prev - p_prev) > 10 or abs(nearest_macd_curr - p_curr) > 10:
            continue
        
        # Divergence: MACD or Histogram lower high
        macd_div = macd_line[nearest_macd_curr] < macd_line[nearest_macd_prev]
        hist_div = (nearest_hist_prev and nearest_hist_curr and
                    hist[nearest_hist_curr] < hist[nearest_hist_prev])
        
        if macd_div or hist_div:
            count = 1
            for prev_div in reversed(divergences):
                if prev_div["type"] == "BEARISH" and p_curr - prev_div["price_idx"] < 200:
                    if price[p_curr] >= price[prev_div["price_idx"]]:
                        count = prev_div["count"] + 1
                        break
            
            divergences.append({
                "type": "BEARISH",
                "price_idx": p_curr,
                "price": float(price[p_curr]),
                "macd_val": float(macd_line[nearest_macd_curr]),
                "count": count,
                "macd_div": macd_div,
                "hist_div": hist_div,
            })
    
    return divergences

# ── Backtest ──
def backtest_divergence(divergences, close, horizon_bars):
    """Calculate forward returns after divergence signals"""
    results = {"BULLISH_1次": [], "BULLISH_2次+": [], "BEARISH_1次": [], "BEARISH_2次+": []}
    
    for div in divergences:
        idx = div["price_idx"]
        if idx + horizon_bars >= len(close):
            continue
        
        entry = close[idx]
        exit_p = close[idx + horizon_bars]
        ret = (exit_p / entry - 1) * 100
        
        d_type = div["type"]
        label = f"{d_type}_{'1次' if div['count'] == 1 else '2次+'}"
        results[label].append({
            "idx": idx,
            "entry": entry,
            "exit": exit_p,
            "ret": ret,
            "count": div["count"],
            "type": d_type,
        })
    
    return results

# ── MAIN ──
def analyze_symbol(symbol, tf, tf_label, horizon_bars_list):
    """Full analysis for one symbol/timeframe"""
    fname = f"{symbol}_{tf}.npz"
    fpath = os.path.join(DATA_DIR, fname)
    
    if not os.path.exists(fpath):
        return None
    
    data = np.load(fpath)
    close = data["close"]
    
    if len(close) < 100:
        return None
    
    m_line, s_line, hist = macd(close)
    
    # Detect divergences (both BULLISH and BEARISH)
    divergences = detect_divergences(close, m_line, hist, lookback=max(5, len(close)//500))
    
    print(f"\n{'='*60}")
    print(f"📊 {symbol} {tf_label} — MACD 背離分析")
    print(f"  Data: {len(close)} bars")
    print(f"  背離信號: {len(divergences)} total")
    
    for d_type in ["BULLISH", "BEARISH"]:
        typed = [d for d in divergences if d["type"] == d_type]
        count_1 = sum(1 for d in typed if d["count"] == 1)
        count_2plus = sum(1 for d in typed if d["count"] >= 2)
        emoji = "🟢" if d_type == "BULLISH" else "🔴"
        print(f"    {emoji} {d_type}: {len(typed)} total — 1次={count_1}, 2次+={count_2plus}")
    
    # Backtest at different horizons
    all_results = {}
    for h_bars, h_label in horizon_bars_list:
        results = backtest_divergence(divergences, close, h_bars)
        all_results[h_label] = results
        
        print(f"\n  --- Forward {h_label} ---")
        for grade, expected in [
            ("BULLISH_1次", "LONG ↑"), ("BULLISH_2次+", "LONG ↑"),
            ("BEARISH_1次", "SHORT ↓"), ("BEARISH_2次+", "SHORT ↓"),
        ]:
            trades = results[grade]
            if not trades:
                continue
            
            rets = [t["ret"] for t in trades]
            # For BEARISH, expected direction is DOWN
            if "BEARISH" in grade:
                wins = sum(1 for r in rets if r < 0)
            else:
                wins = sum(1 for r in rets if r > 0)
            
            n = len(trades)
            avg_ret = np.mean(rets)
            wr = wins / n * 100
            max_ret = max(rets)
            min_ret = min(rets)
            
            star = "⭐" if wr > 55 else ("✅" if wr > 50 else "➡️")
            print(f"    {grade:<18} n={n:>4}  avg={avg_ret:>+7.2f}%  "
                  f"wr={wr:>5.0f}%  range=[{min_ret:+.1f}%,{max_ret:+.1f}%]  {star} ({expected})")
    
    return {
        "symbol": symbol,
        "tf": tf_label,
        "total_divergences": len(divergences),
        "bullish_1": sum(1 for d in divergences if d["type"]=="BULLISH" and d["count"]==1),
        "bullish_2": sum(1 for d in divergences if d["type"]=="BULLISH" and d["count"]>=2),
        "bearish_1": sum(1 for d in divergences if d["type"]=="BEARISH" and d["count"]==1),
        "bearish_2": sum(1 for d in divergences if d["type"]=="BEARISH" and d["count"]>=2),
        "results": all_results,
    }

# ── Run all ──
print("╔══════════════════════════════════════════════════════════╗")
print("║  P1.1 MACD 背離分級研究                                   ║")
print("║  假設：2次+背離（背了又背）比 1次背離更可靠                ║")
print("╚══════════════════════════════════════════════════════════╝")

configs = [
    ("BTC", "1h", "1h", [(24, "24h (1d)"), (72, "72h (3d)"), (168, "168h (7d)")]),
    ("BTC", "4h", "4h", [(6, "24h (1d)"), (18, "72h (3d)"), (42, "168h (7d)")]),
    ("BTC", "1d", "1d", [(1, "1d"), (3, "3d"), (7, "7d")]),
    ("ETH", "1h", "1h", [(24, "24h"), (72, "72h"), (168, "168h")]),
    ("ETH", "4h", "4h", [(6, "24h"), (18, "72h"), (42, "168h")]),
    ("SOL", "1h", "1h", [(24, "24h"), (72, "72h"), (168, "168h")]),
    ("SOL", "4h", "4h", [(6, "24h"), (18, "72h"), (42, "168h")]),
]

summary = {}
for sym, tf, tf_label, horizons in configs:
    result = analyze_symbol(sym, tf, tf_label, horizons)
    if result:
        key = f"{sym}_{tf_label}"
        summary[key] = result

# ── Final Summary ──
print(f"\n\n{'='*60}")
print("📋 總結：MACD 背離分級 Edge")
print(f"{'='*60}")

# Compile comparison: 2次+ vs 1次 for BULLISH and BEARISH separately
print("\n🟢 BULLISH Divergence (LONG signal):")
print(f"  {'Symbol_TF':<12} {'1次 WR':>8} {'2次+ WR':>8} {'ΔWR':>8} {'2次+ n':>7} {'Verdict':>12}")
print(f"  {'-'*55}")

bullish_wins = 0
bullish_total = 0
for key, res in summary.items():
    for h_label in res.get("results", {}):
        t1 = res["results"][h_label].get("BULLISH_1次", [])
        t2 = res["results"][h_label].get("BULLISH_2次+", [])
        if len(t1) >= 3 and len(t2) >= 3:
            wr1 = sum(1 for r in [x["ret"] for x in t1] if r > 0) / len(t1) * 100
            wr2 = sum(1 for r in [x["ret"] for x in t2] if r > 0) / len(t2) * 100
            delta = wr2 - wr1
            ver = "✅ 更好" if delta > 3 else ("➡️ 相同" if abs(delta) <= 3 else "❌ 更差")
            print(f"  {key:<12} {wr1:>7.0f}% {wr2:>7.0f}% {delta:>+7.0f}% {len(t2):>6}  {ver}")
            bullish_total += 1
            if delta > 0:
                bullish_wins += 1

print(f"\n🔴 BEARISH Divergence (SHORT signal):")
print(f"  {'Symbol_TF':<12} {'1次 WR':>8} {'2次+ WR':>8} {'ΔWR':>8} {'2次+ n':>7} {'Verdict':>12}")
print(f"  {'-'*55}")

bearish_wins = 0
bearish_total = 0
for key, res in summary.items():
    for h_label in res.get("results", {}):
        t1 = res["results"][h_label].get("BEARISH_1次", [])
        t2 = res["results"][h_label].get("BEARISH_2次+", [])
        if len(t1) >= 3 and len(t2) >= 3:
            wr1 = sum(1 for r in [x["ret"] for x in t1] if r < 0) / len(t1) * 100
            wr2 = sum(1 for r in [x["ret"] for x in t2] if r < 0) / len(t2) * 100
            delta = wr2 - wr1
            ver = "✅ 更好" if delta > 3 else ("➡️ 相同" if abs(delta) <= 3 else "❌ 更差")
            print(f"  {key:<12} {wr1:>7.0f}% {wr2:>7.0f}% {delta:>+7.0f}% {len(t2):>6}  {ver}")
            bearish_total += 1
            if delta > 0:
                bearish_wins += 1

print(f"\n{'='*60}")
print("🏆 最終結論：")
print(f"  BULLISH 2次+優於1次: {bullish_wins}/{bullish_total} cases")
print(f"  BEARISH 2次+優於1次: {bearish_wins}/{bearish_total} cases")
if bullish_wins + bearish_wins > (bullish_total + bearish_total) * 0.5:
    print(f"  ✅ 假設成立：「背了又背」總體上比單次背離更可靠")
else:
    print(f"  ❌ 假設不成立：2次+背離未必優於1次背離")

# Save results
output = {
    "study": "P1.1 MACD 背離分級",
    "timestamp": datetime.now(HKT).isoformat(),
    "hypothesis": "連續背離（2次+）比單次背離更可靠",
    "summary": summary,
}
with open(os.path.join(OUT_DIR, "p1_1_macd_divergence.json"), "w") as f:
    json.dump(output, f, indent=2, default=str)
print(f"\n結果已儲存: {os.path.join(OUT_DIR, 'p1_1_macd_divergence.json')}")
