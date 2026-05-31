#!/usr/bin/env python3
"""
P2 缠論結構研究 — 中樞 Detection + 買賣點分類 + 錯殺 Pattern
===========================================================
核心假設：缠論中樞結構 + MACD 背離 = 真正嘅 alpha
（而唔係 standalone MACD 背離）

P2.1: ZigZag 中樞 Detection
P2.2: 基於中樞位置嘅買賣點分類
P2.3: 錯殺 Pattern（暴跌後結構 intact → recovery）
"""
import numpy as np
import os, json
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.path.expanduser("~/workspace/data/market/phase1")
OUT_DIR = os.path.expanduser("~/workspace/research")
os.makedirs(OUT_DIR, exist_ok=True)

# ══════════════════════════════════════════════════════
# 1. ZigZag Swing Detection（缠論「笔」嘅基礎）
# ══════════════════════════════════════════════════════

def find_zigzag_swings(high, low, min_pct=0.5):
    """
    Find swing highs and lows using ZigZag with minimum retracement %.
    Returns list of (idx, price, type: 'H' or 'L')
    """
    swings = []
    last_extreme = None  # (idx, price, type)
    direction = None  # 'up' or 'down'
    
    for i in range(1, len(high)):
        if direction is None:
            # Initialize: find first significant move
            if last_extreme is None:
                if i > 20:  # need some bars to start
                    local_max = np.argmax(high[i-20:i]) + (i-20)
                    local_min = np.argmin(low[i-20:i]) + (i-20)
                    if high[local_max] / low[local_min] - 1 > min_pct / 100:
                        if local_max < local_min:
                            last_extreme = (local_max, high[local_max], 'H')
                            direction = 'down'
                        else:
                            last_extreme = (local_min, low[local_min], 'L')
                            direction = 'up'
            continue
        
        if direction == 'up':
            # Looking for next swing HIGH
            if high[i] > last_extreme[1]:
                last_extreme = (i, high[i], 'H')
            # Check for reversal: price drops min_pct% from last high
            drop_pct = (last_extreme[1] - low[i]) / last_extreme[1] * 100
            if drop_pct >= min_pct:
                swings.append(last_extreme)
                direction = 'down'
                last_extreme = (np.argmin(low[last_extreme[0]:i+1]) + last_extreme[0],
                              low[np.argmin(low[last_extreme[0]:i+1]) + last_extreme[0]], 'L')
        else:
            # Looking for next swing LOW
            if low[i] < last_extreme[1]:
                last_extreme = (i, low[i], 'L')
            # Check for reversal: price rises min_pct% from last low
            rise_pct = (high[i] - last_extreme[1]) / last_extreme[1] * 100
            if rise_pct >= min_pct:
                swings.append(last_extreme)
                direction = 'up'
                last_extreme = (np.argmax(high[last_extreme[0]:i+1]) + last_extreme[0],
                              high[np.argmax(high[last_extreme[0]:i+1]) + last_extreme[0]], 'H')
    
    if last_extreme and not swings:
        swings.append(last_extreme)
    
    return swings

# ══════════════════════════════════════════════════════
# 2. 中樞 Detection（缠論核心）
# ══════════════════════════════════════════════════════

def detect_pivots(swings, close, min_width_pct=1.0, min_bars=15):
    """
    Detect 中樞 (consolidation zones) from swing points.
    
    缠論定義：至少 3 段連續的「笔」有價格重疊 → 形成中樞
    Filter: minimum width % and minimum duration
    """
    pivots = []
    
    if len(swings) < 4:
        return pivots
    
    i = 0
    while i < len(swings) - 3:
        s0, s1, s2, s3 = swings[i], swings[i+1], swings[i+2], swings[i+3]
        
        # Minimum duration check
        duration = s3[0] - s0[0]
        if duration < min_bars:
            i += 1
            continue
        
        highs_involved = [s[1] for s in [s0, s1, s2, s3] if s[2] == 'H']
        lows_involved = [s[1] for s in [s0, s1, s2, s3] if s[2] == 'L']
        
        if len(highs_involved) >= 2 and len(lows_involved) >= 2:
            overlap_upper = min(highs_involved)
            overlap_lower = max(lows_involved)
            width_pct = (overlap_upper / overlap_lower - 1) * 100
            
            # Must have meaningful overlap width
            if overlap_upper > overlap_lower and width_pct >= min_width_pct:
                pivot = {
                    "start_idx": s0[0],
                    "end_idx": s3[0],
                    "upper": overlap_upper,
                    "lower": overlap_lower,
                    "mid": (overlap_upper + overlap_lower) / 2,
                    "width_pct": width_pct,
                    "duration": duration,
                    "swings": [(s[0], s[1], s[2]) for s in [s0, s1, s2, s3]],
                }
                pivots.append(pivot)
                i += 3  # Skip ahead to avoid overlapping pivots
                continue
        
        i += 1
    
    return pivots

# ══════════════════════════════════════════════════════
# 3. 買賣點分類
# ══════════════════════════════════════════════════════

def classify_entry_points(pivots, close, high, low, macd_line, macd_hist):
    """
    Classify entry points relative to 中樞:
    
    1買 (First Buy): Price breaks BELOW 中枢, MACD shows bullish divergence
    2買 (Second Buy): Pullback that doesn't break below previous low, near 中枢 lower
    3買 (Third Buy): Price breaks ABOVE 中枢, then pulls back to test upper bound
    
    Returns dict of {type: [(idx, price, pivot_info)]}
    """
    entries = {"1买": [], "2买": [], "3买": [], "1卖": [], "2卖": [], "3卖": []}
    
    for pivot in pivots:
        p_start = pivot["start_idx"]
        p_end = pivot["end_idx"]
        p_upper = pivot["upper"]
        p_lower = pivot["lower"]
        
        # ── Look AFTER the pivot for entry points ──
        search_start = p_end
        search_end = min(search_start + 500, len(close))
        
        # --- 1买 (First Buy): breaks below 中枢 lower + MACD divergence ---
        below_pivot = False
        lowest_idx = search_start
        
        for i in range(search_start, search_end):
            if close[i] < p_lower:
                below_pivot = True
            if below_pivot and low[i] < low[lowest_idx]:
                lowest_idx = i
            
            # Detect: price back above pivot lower after being below
            if below_pivot and close[i] > p_lower and i > lowest_idx:
                # Check MACD divergence at the low point
                div_confirmed = check_local_divergence(close, macd_line, lowest_idx, lookback=30)
                if div_confirmed:
                    entries["1买"].append({
                        "idx": i,
                        "price": float(close[i]),
                        "pivot_lower": p_lower,
                        "pivot_upper": p_upper,
                        "divergence_at": int(lowest_idx),
                    })
                break
        
        # --- 2买 (Second Buy): pullback near pivot lower, above prev low ---
        for i in range(search_start + 10, search_end):
            # Price approaching pivot lower zone from above
            in_lower_zone = p_lower <= close[i] <= p_lower * 1.02
            
            # Check: recent swing low above pivot lower
            recent_low = min(low[max(p_end, i-20):i])
            
            if in_lower_zone and recent_low > p_lower * 0.98:
                # Bouncing from near the lower bound
                if close[i] > low[i] and i < search_end - 5:
                    entries["2买"].append({
                        "idx": i,
                        "price": float(close[i]),
                        "pivot_lower": p_lower,
                        "pivot_upper": p_upper,
                    })
                    break
        
        # --- 3买 (Third Buy): breaks above 中枢 + pulls back to upper bound ---
        found_breakout = False
        breakout_idx = search_start
        
        for i in range(search_start, search_end):
            if close[i] > p_upper and high[i] > p_upper * 1.01:
                found_breakout = True
                breakout_idx = i
                break
        
        if found_breakout:
            # Now look for pullback to upper bound
            for i in range(breakout_idx + 5, min(breakout_idx + 200, search_end)):
                in_upper_zone = p_upper * 0.99 <= close[i] <= p_upper * 1.02
                if in_upper_zone and close[i] > low[i]:
                    entries["3买"].append({
                        "idx": i,
                        "price": float(close[i]),
                        "pivot_lower": p_lower,
                        "pivot_upper": p_upper,
                    })
                    break
    
    return entries

def check_local_divergence(close, macd_line, low_idx, lookback=30):
    """Check if there's MACD bullish divergence at a local low"""
    start = max(0, low_idx - lookback * 3)
    end = min(len(close), low_idx + 10)
    segment_close = close[start:end]
    segment_macd = macd_line[start:end]
    
    # Find price lows before current
    prev_lows = []
    for i in range(lookback, low_idx - start - 5):
        if segment_close[i] == min(segment_close[max(0,i-5):min(len(segment_close),i+6)]):
            prev_lows.append(i)
    
    if not prev_lows:
        return False
    
    # Compare: current low < previous low AND MACD > previous MACD
    prev_low_idx = prev_lows[-1]  # most recent previous low
    curr_low_local = low_idx - start
    
    price_making_lower_low = segment_close[curr_low_local] < segment_close[prev_low_idx]
    macd_making_higher_low = segment_macd[curr_low_local] > segment_macd[prev_low_idx]
    
    return price_making_lower_low and macd_making_higher_low

# ══════════════════════════════════════════════════════
# 4. EMA helper (for MACD)
# ══════════════════════════════════════════════════════

def ema(data, period):
    alpha = 2 / (period + 1)
    result = np.zeros_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
    return result

def macd(close, fast=12, slow=26, signal=9):
    e_fast = ema(close, fast)
    e_slow = ema(close, slow)
    m_line = e_fast - e_slow
    s_line = ema(m_line, signal)
    return m_line, s_line, m_line - s_line

# ══════════════════════════════════════════════════════
# 5. Backtest: Structure vs No Structure
# ══════════════════════════════════════════════════════

def backtest_entries(entries, close, horizon_bars):
    """Backtest entry signals"""
    results = {}
    for entry_type in entries:
        trades = []
        for e in entries[entry_type]:
            idx = e["idx"]
            if idx + horizon_bars < len(close):
                ret = (close[idx + horizon_bars] / close[idx] - 1) * 100
                trades.append(ret)
        if trades:
            n = len(trades)
            wr = sum(1 for r in trades if r > 0) / n * 100
            avg = np.mean(trades)
            results[entry_type] = {
                "n": n, "wr": round(wr, 1), "avg": round(avg, 2),
                "max": round(max(trades), 2), "min": round(min(trades), 2),
            }
    return results

def backtest_standalone_macd(close, m_line, horizon_bars):
    """Standalone MACD divergence backtest (for comparison)"""
    from collections import defaultdict
    # Simple divergence detection (same as P1.1)
    signals = []
    for i in range(50, len(close) - horizon_bars - 1):
        lookback = 30
        price_low = min(close[i-lookback:i])
        macd_low = min(m_line[i-lookback:i])
        
        if close[i] < price_low * 0.99 and m_line[i] > macd_low:
            signals.append(i)
    
    rets = [(close[i+horizon_bars]/close[i]-1)*100 for i in signals if i+horizon_bars<len(close)]
    if rets:
        return {"n": len(rets), "wr": round(sum(1 for r in rets if r>0)/len(rets)*100,1),
                "avg": round(np.mean(rets), 2)}
    return {"n": 0, "wr": 0, "avg": 0}

# ══════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════

print("╔══════════════════════════════════════════════════════════════╗")
print("║  P2 缠論結構研究                                              ║")
print("║  假設：缠論中樞 + MACD 背離 >  standalone MACD 背離           ║")
print("╚══════════════════════════════════════════════════════════════╝")

configs = [
    # 缠論主力用喺 1h/15min，唔係 4h。改用 1h + 更大 min_pct
    ("BTC", "1h", "1h", [(12, "12h"), (24, "1d"), (72, "3d")], 2.0, 1.0),
    ("ETH", "1h", "1h", [(12, "12h"), (24, "1d"), (72, "3d")], 2.5, 1.5),
    ("SOL", "1h", "1h", [(12, "12h"), (24, "1d"), (72, "3d")], 3.0, 1.5),
    ("BTC", "4h", "4h", [(6, "1d"), (18, "3d")], 3.0, 2.0),
    ("ETH", "4h", "4h", [(6, "1d"), (18, "3d")], 4.0, 2.5),
    ("SOL", "4h", "4h", [(6, "1d"), (18, "3d")], 5.0, 3.0),
]

all_summary = {}

for sym, tf, tf_label, horizons, min_pct, min_width in configs:
    fpath = os.path.join(DATA_DIR, f"{sym}_{tf}.npz")
    if not os.path.exists(fpath):
        continue
    
    data = np.load(fpath)
    close = data["close"]
    high = data["high"]
    low = data["low"]
    
    m_line, s_line, hist = macd(close)
    
    print(f"\n{'='*70}")
    print(f"📊 {sym} {tf_label} — 缠論中樞分析 (min_pct={min_pct}%, min_width={min_width}%)")
    
    # P2.1: ZigZag + 中樞 Detection
    swings = find_zigzag_swings(high, low, min_pct=min_pct)
    pivots = detect_pivots(swings, close, min_width_pct=min_width, min_bars=15)
    
    print(f"  ZigZag swings: {len(swings)}")
    print(f"  中樞 detected: {len(pivots)}")
    
    if pivots:
        widths = [p["width_pct"] for p in pivots]
        print(f"    平均寬度: {np.mean(widths):.2f}%")
        print(f"    最寬/最窄: {max(widths):.2f}% / {min(widths):.2f}%")
        
        # Show last 3 pivots
        for p in pivots[-3:]:
            print(f"    [{p['start_idx']}→{p['end_idx']}] "
                  f"${p['lower']:.0f}-${p['upper']:.0f} "
                  f"(width={p['width_pct']:.1f}%)")
    
    # P2.2: 買賣點分類
    entries = classify_entry_points(pivots, close, high, low, m_line, hist)
    
    print(f"\n  買賣點:")
    for etype in ["1买", "2买", "3买"]:
        count = len(entries[etype])
        print(f"    {etype}: {count} signals")
    
    # P2.3: Backtest
    for h_bars, h_label in horizons:
        print(f"\n  ── Forward {h_label} ──")
        
        # Structure-based entries
        struct_results = backtest_entries(entries, close, h_bars)
        
        # Standalone MACD (baseline)
        standalone = backtest_standalone_macd(close, m_line, h_bars)
        
        print(f"  {'Type':<12} {'n':>5} {'WR':>7} {'Avg':>8} | vs Standalone")
        print(f"  {'-'*45}")
        
        best_wr = standalone["wr"]
        best_type = "Standalone MACD"
        
        for etype in ["1买", "2买", "3买"]:
            if etype in struct_results:
                sr = struct_results[etype]
                delta = sr["wr"] - standalone["wr"]
                mark = "⭐" if delta > 5 else ("✅" if delta > 0 else "➡️")
                print(f"  {etype:<12} {sr['n']:>5} {sr['wr']:>6.0f}% {sr['avg']:>+7.2f}% | {mark} Δ{delta:+.0f}%")
                
                if sr["wr"] > best_wr and sr["n"] >= 3:
                    best_wr = sr["wr"]
                    best_type = etype
        
        if standalone["n"] > 0:
            print(f"  {'Standalone':<12} {standalone['n']:>5} {standalone['wr']:>6.0f}% {standalone['avg']:>+7.2f}% | (baseline)")
        
        print(f"  → Best: {best_type} (WR={best_wr:.0f}%)")
        
        key = f"{sym}_{tf_label}_{h_label}"
        all_summary[key] = {
            "standalone": standalone,
            "structured": struct_results,
            "best": best_type,
            "best_wr": best_wr,
        }

# ══════════════════════════════════════════════════════
# Grand Summary
# ══════════════════════════════════════════════════════

print(f"\n\n{'='*70}")
print("🏆 P2 總結：缠論結構能提升 edge 嗎？")
print(f"{'='*70}")

structure_wins = 0
standalone_wins = 0
total_cases = 0

for key, s in all_summary.items():
    if s["best"] != "Standalone MACD" and s["structured"].get(s["best"], {}).get("n", 0) >= 3:
        structure_wins += 1
        total_cases += 1
    elif s["standalone"]["n"] > 0:
        standalone_wins += 1
        total_cases += 1

print(f"  缠論結構勝: {structure_wins}/{total_cases}")
print(f"  Standalone 勝: {standalone_wins}/{total_cases}")

if structure_wins > standalone_wins:
    print(f"\n  ✅ 假設成立：缠論中樞結構可以提升 MACD 背離 edge")
else:
    print(f"\n  ❌ 假設不成立（暫時）：可能係中樞 detection 唔夠準，或者 min_pct 參數要 tune")

# Save
output_path = os.path.join(OUT_DIR, "p2_pivot_structure.json")
with open(output_path, "w") as f:
    json.dump({
        "study": "P2 缠論結構",
        "timestamp": datetime.now(HKT).isoformat(),
        "summary": {k: {"best": v["best"], "best_wr": v["best_wr"]} for k, v in all_summary.items()}
    }, f, indent=2)
print(f"\n結果已儲存: {output_path}")
