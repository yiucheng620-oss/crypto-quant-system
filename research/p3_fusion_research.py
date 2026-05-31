#!/usr/bin/env python3
"""
P3 多信號融合研究（完整版 — 真．數據驅動）
===========================================
整合：MACD 背離 + 缠論 1买/1卖 + Vegas 通道 + SMA 支撐壓力
研究問題：多信號 fusion 係咪 edge > 單一最強信號？

輸出：
  - p3_fusion_results.json  → 結構化統計
  - p3_fusion_report.md     → 人類可讀報告
"""
import numpy as np
import os, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.path.expanduser("~/workspace/data/market/phase1")
OUT_DIR = os.path.expanduser("~/workspace/research")
os.makedirs(OUT_DIR, exist_ok=True)

# ═══════════════════════════════════════
# 1. MATH HELPERS
# ═══════════════════════════════════════

def ema(d, p):
    a = 2 / (p + 1)
    r = np.zeros_like(d)
    r[0] = d[0]
    for i in range(1, len(d)):
        r[i] = a * d[i] + (1 - a) * r[i - 1]
    return r


def macd(c, f=12, s=26, sig=9):
    ef = ema(c, f)
    es = ema(c, s)
    m = ef - es
    sl = ema(m, sig)
    return m, sl, m - sl


def sma(d, p):
    """Simple moving average — padding with NaN for alignment."""
    out = np.full_like(d, np.nan)
    for i in range(p - 1, len(d)):
        out[i] = np.mean(d[i - p + 1 : i + 1])
    return out


# ═══════════════════════════════════════
# 2. SIGNAL DETECTORS
# ═══════════════════════════════════════

def detect_macd_divergence(close, macd_line, macd_hist, lookback=40):
    """
    Detect MACD bullish/bearish divergences.
    Returns: signal array (-1=bearish div, 0=none, +1=bullish div)
    """
    n = len(close)
    signals = np.zeros(n, dtype=int)

    for i in range(lookback, n):
        window_c = close[i - lookback : i + 1]
        window_m = macd_line[i - lookback : i + 1]
        window_h = macd_hist[i - lookback : i + 1]

        # Price making lower low, MACD making higher low → bullish divergence
        price_low_idx = np.argmin(window_c)
        macd_low_idx = np.argmin(window_m[price_low_idx:]) + price_low_idx

        if price_low_idx > lookback // 2:
            prev_low = np.argmin(window_c[: price_low_idx])
            if window_c[price_low_idx] < window_c[prev_low] * 0.98:
                # Price made lower low
                macd_at_prev = np.min(window_m[prev_low : prev_low + 10])
                macd_at_now = np.min(window_m[price_low_idx : price_low_idx + 10])
                if macd_at_now > macd_at_prev:
                    # MACD higher low — bullish divergence
                    signals[i] = 1
                    continue

        # Price making higher high, MACD making lower high → bearish divergence
        price_high_idx = np.argmax(window_c)
        if price_high_idx > lookback // 2:
            prev_high = np.argmax(window_c[: price_high_idx])
            if window_c[price_high_idx] > window_c[prev_high] * 1.02:
                macd_at_prev = np.max(window_m[prev_high : prev_high + 10])
                macd_at_now = np.max(window_m[price_high_idx : price_high_idx + 10])
                if macd_at_now < macd_at_prev:
                    signals[i] = -1

    return signals


def detect_chanlun_1buy_1sell(high, low, min_pct=3.0, min_width=1.0, min_bars=8):
    """
    Detect 缠論 1买 (bullish) / 1卖 (bearish) using zigzag + pivot detection.
    Returns: signal array (-1=1卖, 0=none, +1=1买)
    """
    n = len(high)
    signals = np.zeros(n, dtype=int)

    # Find zigzag swings
    swings = []
    le = None
    direction = None

    for i in range(20, n):
        if direction is None:
            lm = np.argmax(high[i - 20 : i]) + (i - 20)
            ln = np.argmin(low[i - 20 : i]) + (i - 20)
            if high[lm] / low[ln] - 1 > min_pct / 100:
                if lm < ln:
                    le = (lm, high[lm], 'H')
                    direction = 'down'
                else:
                    le = (ln, low[ln], 'L')
                    direction = 'up'
            continue

        if direction == 'up':
            if high[i] > le[1]:
                le = (i, high[i], 'H')
            if (le[1] - low[i]) / le[1] * 100 >= min_pct:
                swings.append(le)
                direction = 'down'
                bi = np.argmin(low[le[0] : i + 1]) + le[0]
                le = (bi, low[bi], 'L')
        else:
            if low[i] < le[1]:
                le = (i, low[i], 'L')
            if (high[i] - le[1]) / le[1] * 100 >= min_pct:
                swings.append(le)
                direction = 'up'
                bi = np.argmax(high[le[0] : i + 1]) + le[0]
                le = (bi, high[bi], 'H')

    if not swings or len(swings) < 4:
        return signals

    # Detect 1买/1卖: price breaks below the last pivot low (oversold) +
    # MACD shows bullish divergence → 1买
    for idx in range(3, len(swings)):
        s0, s1, s2, s3 = swings[idx - 3], swings[idx - 2], swings[idx - 1], swings[idx]
        span = s3[0] - s0[0]
        if span < min_bars:
            continue

        hs = [s[1] for s in [s0, s1, s2, s3] if s[2] == 'H']
        ls = [s[1] for s in [s0, s1, s2, s3] if s[2] == 'L']
        if len(hs) < 2 or len(ls) < 2:
            continue

        upper = min(hs)
        lower = max(ls)
        width = (upper / lower - 1) * 100
        if width < min_width or upper <= lower:
            continue

        # Pivot structure confirmed
        now = s3[0]
        price_now = low[now] if s3[2] == 'L' else high[now]

        # 1买: last swing is a low → bullish reversal zone
        if s3[2] == 'L':
            for j in range(now, min(now + 5, n)):
                signals[j] = 1

    return signals


def detect_vegas_bounce(close, high, low):
    """
    Vegas通道: EMA144 + EMA169.
    Signal: price bounces off Vegas channel support (bullish).
    """
    n = len(close)
    signals = np.zeros(n, dtype=int)

    ema144 = ema(close, 144)
    ema169 = ema(close, 169)

    for i in range(200, n):
        vegas_upper = max(ema144[i], ema169[i])
        vegas_lower = min(ema144[i], ema169[i])

        # Price near Vegas support + bullish candle
        if low[i] <= vegas_upper * 1.005 and low[i] >= vegas_lower * 0.995:
            if close[i] > close[i - 1]:
                signals[i] = 1
        # Price near Vegas resistance + bearish candle
        elif high[i] >= vegas_upper * 0.995 and high[i] <= vegas_upper * 1.005:
            if close[i] < close[i - 1]:
                signals[i] = -1

    return signals


def detect_sma_support_resistance(close):
    """
    SMA 20/50/200 confluence.
    Signal: price near SMA cluster → potential bounce/reversal zone.
    """
    n = len(close)
    signals = np.zeros(n, dtype=int)

    sma20 = sma(close, 20)
    sma50 = sma(close, 50)
    sma200 = sma(close, 200)

    for i in range(200, n):
        if np.isnan(sma20[i]) or np.isnan(sma50[i]) or np.isnan(sma200[i]):
            continue

        # Price near SMA cluster
        smas = [sma20[i], sma50[i], sma200[i]]
        sma_high = max(smas)
        sma_low = min(smas)

        if low[i] <= sma_high and low[i] >= sma_low * 0.998:
            if close[i] > close[i - 1]:
                signals[i] = 1
        elif high[i] >= sma_low and high[i] <= sma_high * 1.002:
            if close[i] < close[i - 1]:
                signals[i] = -1

    return signals


# ═══════════════════════════════════════
# 3. FUSION ANALYSIS
# ═══════════════════════════════════════

FORWARD_PERIODS = [12, 24, 48, 72]  # bars: ~12h, 1d, 2d, 3d on 1h; ~2d, 4d, 8d, 12d on 4h

def compute_forward_returns(close, signals, periods=FORWARD_PERIODS):
    """For each signal bar, compute forward return over N periods."""
    n = len(close)
    results = {p: [] for p in periods}

    for i in range(n):
        if signals[i] == 0:
            continue
        direction = 1 if signals[i] > 0 else -1

        for p in periods:
            if i + p < n:
                ret = (close[i + p] / close[i] - 1) * 100 * direction
                results[p].append(ret)

    return results


def analyze_fusion(symbol, tf, close, high, low, params):
    """
    Full fusion analysis for one symbol/timeframe.
    Returns stats dict.
    """
    mp, mw, mb = params
    n = len(close)
    print(f"  {symbol} {tf}: {n} bars, zigzag params ({mp}%, {mw}%, >={mb} bars)")

    # Detect all signal types
    m_line, s_line, m_hist = macd(close)
    sig_macd = detect_macd_divergence(close, m_line, m_hist)
    sig_chanlun = detect_chanlun_1buy_1sell(high, low, mp, mw, mb)
    sig_vegas = detect_vegas_bounce(close, high, low)
    sig_sma = detect_sma_support_resistance(close)

    # Count signals per bar
    n_bullish = np.zeros(n, dtype=int)
    for sig, name in [(sig_macd, "macd"), (sig_chanlun, "chanlun"),
                       (sig_vegas, "vegas"), (sig_sma, "sma")]:
        for i in range(n):
            if sig[i] > 0:
                n_bullish[i] += 1

    # ── Single signal analysis ──
    signal_stats = {}
    signal_arrays = {
        "macd_divergence": sig_macd,
        "chanlun_1buy": sig_chanlun,
        "vegas_bounce": sig_vegas,
        "sma_cluster": sig_sma,
    }

    for name, sig in signal_arrays.items():
        signal_count = np.sum(np.abs(sig) > 0)
        forward = compute_forward_returns(close, sig)
        stats_entry = {"count": int(signal_count)}
        for p, rets in forward.items():
            if rets:
                wr = sum(1 for r in rets if r > 0) / len(rets) * 100
                avg = np.mean(rets)
                med = np.median(rets)
                stats_entry[f"p{p}"] = {
                    "samples": len(rets),
                    "win_rate": round(wr, 1),
                    "avg_return_pct": round(avg, 2),
                    "median_return_pct": round(med, 2),
                }
        signal_stats[name] = stats_entry

    # ── Multi-signal fusion analysis ──
    fusion_stats = {}
    for level in [1, 2, 3, 4]:
        mask = n_bullish == level
        if np.sum(mask) == 0:
            fusion_stats[f"{level}_signal"] = {"count": 0, "note": "no bars with this many signals"}
            continue

        sig = np.zeros(n, dtype=int)
        sig[mask] = 1
        forward = compute_forward_returns(close, sig)

        fusion_entry = {"count": int(np.sum(mask))}
        for p, rets in forward.items():
            if rets:
                wr = sum(1 for r in rets if r > 0) / len(rets) * 100
                avg = np.mean(rets)
                med = np.median(rets)
                fusion_entry[f"p{p}"] = {
                    "samples": len(rets),
                    "win_rate": round(wr, 1),
                    "avg_return_pct": round(avg, 2),
                    "median_return_pct": round(med, 2),
                }
        fusion_stats[f"{level}_signal"] = fusion_entry

    return {
        "symbol": symbol,
        "tf": tf,
        "bars": n,
        "single_signals": signal_stats,
        "fusion": fusion_stats,
    }


# ═══════════════════════════════════════
# 4. MAIN
# ═══════════════════════════════════════

# Optimized zigzag params per symbol/TF (from P2 research)
PARAMS = {
    "BTC": {"1h": (2.0, 1.0, 15), "4h": (3.0, 2.0, 8)},
    "ETH": {"1h": (1.5, 2.5, 15), "4h": (3.0, 0.5, 8)},
    "SOL": {"1h": (3.0, 1.5, 15), "4h": (5.0, 3.0, 8)},
}

print("=" * 60)
print("P3 多信號融合研究 — 真．數據驅動完整版")
print(f"開始時間: {datetime.now(HKT).strftime('%Y-%m-%d %H:%M')} HKT")
print("=" * 60)

all_results = []

for symbol in ["BTC", "ETH", "SOL"]:
    for tf in ["1h", "4h"]:
        mp, mw, mb = PARAMS[symbol][tf]
        fpath = os.path.join(DATA_DIR, f"{symbol}_{tf}.npz")

        if not os.path.exists(fpath):
            print(f"  ⚠️ {symbol} {tf}: data file not found → skip")
            all_results.append({
                "symbol": symbol, "tf": tf,
                "error": f"Data file missing: {fpath}"
            })
            continue

        data = np.load(fpath)
        close = data["close"]
        high = data["high"]
        low = data["low"]

        result = analyze_fusion(symbol, tf, close, high, low, (mp, mw, mb))
        all_results.append(result)

# ═══════════════════════════════════
# 5. SAVE RESULTS
# ═══════════════════════════════════

output = {
    "study": "P3 Multi-Signal Fusion — Complete",
    "generated_at": datetime.now(HKT).isoformat(),
    "method": "MACD divergence + Chanlun 1buy/1sell + Vegas bounce + SMA cluster",
    "forward_periods_bars": FORWARD_PERIODS,
    "results": all_results,
}

results_path = os.path.join(OUT_DIR, "p3_fusion_results.json")
with open(results_path, "w") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n✅ Results saved: {results_path}")

# ═══════════════════════════════════
# 6. GENERATE HUMAN-READABLE REPORT
# ═══════════════════════════════════

report_lines = []
report_lines.append("# 🧬 P3 多信號融合研究報告")
report_lines.append(f"> 生成時間: {datetime.now(HKT).strftime('%Y-%m-%d %H:%M')} HKT")
report_lines.append("")
report_lines.append(f"## 研究方法")
report_lines.append(
    "對每個 K 線 bar 同時掃描 **4 種信號**：MACD 背離、缠論結構性 1买/1卖、"
    "Vegas 通道（EMA144+169）回踩、SMA（20/50/200）集群支撐壓力。"
)
report_lines.append("計算每個 bar 上 **重疊信號數**（0-4），然後追蹤未來 12/24/48/72 根 bar 嘅方向性回報。")
report_lines.append("")
report_lines.append("## 📊 單一信號 vs 多信號融合 Edge 比較")
report_lines.append("")

for r in all_results:
    if "error" in r:
        report_lines.append(f"### {r['symbol']} {r['tf']} — ⚠️ 數據缺失")
        continue

    report_lines.append(f"### {r['symbol']} {r['tf']} ({r['bars']} bars)")
    report_lines.append("")

    # Single signals table
    report_lines.append("#### 單一信號表現")
    for name, stats in r["single_signals"].items():
        if stats["count"] == 0:
            report_lines.append(f"- **{name}**: 無信號觸發")
            continue
        best_p = max(stats.keys(), key=lambda k: stats[k]["win_rate"] if k.startswith("p") else 0)
        report_lines.append(
            f"- **{name}**: {stats['count']} 次觸發 | "
            f"最佳勝率: {stats[best_p]['win_rate']}% @ {best_p}bar "
            f"(avg {stats[best_p]['avg_return_pct']:+.2f}%)"
        )

    # Fusion table
    report_lines.append("")
    report_lines.append("#### 多信號融合表現（重疊數愈多 → Edge 愈強？）")
    fusion = r["fusion"]
    for level in ["1_signal", "2_signal", "3_signal", "4_signal"]:
        f = fusion.get(level, {})
        if f.get("count", 0) == 0:
            report_lines.append(f"- **{level}**: 無足夠樣本")
            continue
        best_p = max([k for k in f.keys() if k.startswith("p")],
                     key=lambda k: f[k]["win_rate"]) if any(k.startswith("p") for k in f) else None
        if best_p:
            report_lines.append(
                f"- **{level}**: {f['count']} 次觸發 | "
                f"最佳勝率: {f[best_p]['win_rate']}% @ {best_p}bar "
                f"(avg {f[best_p]['avg_return_pct']:+.2f}%)"
            )
        else:
            report_lines.append(f"- **{level}**: {f['count']} 次觸發 | 無足夠 forward data")

    report_lines.append("")

report_lines.append("## 🎯 結論")
report_lines.append("")
report_lines.append(
    "如果 **3-4 重信號重疊** 的勝率顯著高於單一信號（差距 > 5%），"
    "則多信號 fusion 有真實 edge，可以作為 System 1 實盤信號 filter。"
    "反之，如果多信號冇明顯提升 → 保持單一最強信號策略。"
)

report_path = os.path.join(OUT_DIR, "p3_fusion_report.md")
with open(report_path, "w") as f:
    f.write("\n".join(report_lines))
print(f"✅ Report saved: {report_path}")

# Also update status
status = {
    "study": "P3 Multi-Signal Fusion (Complete)",
    "completed_at": datetime.now(HKT).isoformat(),
    "status": "complete",
    "result_files": ["p3_fusion_results.json", "p3_fusion_report.md"],
}
with open(os.path.join(OUT_DIR, "p3_fusion_status.json"), "w") as f:
    json.dump(status, f, indent=2)

print(f"\n{'=' * 60}")
print("P3 fusion research complete.")
print(f"  Results: {results_path}")
print(f"  Report:  {report_path}")
print("=" * 60)
