#!/usr/bin/env python3
"""
Final Indicator Library — ETH/SOL Long-Only Signals
═══════════════════════════════════════════════════════
Tested 2025-01-01 → 2026-05-14 on Binance 1h/4h data.
All signals are Long-Only. Use with trailing stop (2×ATR).

INDICATORS (5 survived out of 7 tested):
  VP    ETH 1h — 92% WR, +3.4% avg PnL (cooldown: none needed)
  VP    ETH 4h — 80% WR, +4.8% avg PnL (cooldown: none needed)
  BB    ETH 4h — 67% WR, +4.1% avg PnL (cooldown=5 bars)
  BB    ETH 1h — 57% WR, +0.8% avg PnL (cooldown=10 bars, marginal)
  OBV   SOL 1h — 68% WR, +2.0% avg PnL (cooldown=12 bars)

KILLED:
  MACD — all combos negative PnL
  RSI  — 1/9 combos profitable, signals too sparse
  EMA  — 45% WR, unsalvageable
"""
import numpy as np

# ═══════════════════════════════════════════════════════════
# HELPERS
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

# ═══════════════════════════════════════════════════════════
# VP — Volume Profile (Mean Reversion + Volume Spike)
# ═══════════════════════════════════════════════════════════

def vp_signal(close, volume,
              vp_lookback=20, dist_pct=0.5, vol_thresh=1.75, vol_lookback=30):
    """
    Signal when price is near SMA (within dist_pct%) AND below SMA,
    AND volume spikes above vol_thresh × recent average.

    ETH 1h best: lookback=20, dist=0.3, thresh=1.75, vol_lb=30 (92% WR)
    ETH 4h best: lookback=20, dist=1.0, thresh=2.0,  vol_lb=30 (80% WR)
    """
    n = len(close)
    # Use last 2000 bars for efficiency
    start = max(0, n - 2000)
    c = close[start:]; v = volume[start:]
    ns = len(c)

    sma = np.full(ns, np.nan)
    sma[vp_lookback-1:] = np.convolve(c, np.ones(vp_lookback)/vp_lookback, mode='valid')

    vm = np.full(ns, np.nan)
    for i in range(vol_lookback, ns+1):
        vm[i-1] = np.mean(v[i-vol_lookback:i])

    sigs = np.zeros(ns, dtype=int)
    w = max(vp_lookback, vol_lookback) + 10
    for i in range(w, ns):
        if np.isnan(sma[i]) or np.isnan(vm[i-1]):
            continue
        dist = abs(c[i] - sma[i]) / sma[i] * 100
        if dist < dist_pct and c[i] < sma[i] and v[i] > vm[i-1] * vol_thresh:
            sigs[i] = 1

    full = np.zeros(n, dtype=int)
    full[start:] = sigs
    return full

# ═══════════════════════════════════════════════════════════
# BB — Bollinger Band Squeeze (Volatility Breakout)
# ═══════════════════════════════════════════════════════════

def bb_signal(close, volume,
              period=30, std=1.5, squeeze_ratio=0.5,
              min_bars=3, vol_confirm=True, cooldown=10):
    """
    Signal when BB bandwidth compresses below squeeze_ratio × recent avg,
    holds for min_bars, then breaks upward with optional volume confirm.

    ETH 4h best: p=30,std=1.5,ratio=0.4,mb=1,vol=True,cooldown=5  (67% WR)
    ETH 1h best: p=30,std=1.5,ratio=0.5,mb=3,vol=True,cooldown=10 (57% WR)
    """
    n = len(close)
    # BB bands
    ma = np.full(n, np.nan)
    ma[period-1:] = np.convolve(close, np.ones(period)/period, mode='valid')
    rs = np.full(n, np.nan)
    for i in range(period, n):
        rs[i] = np.std(close[i-period:i])
    upper = ma + std * rs
    lower = ma - std * rs

    # Volume 20-bar mean
    v20 = np.full(n, np.nan)
    for i in range(20, n+1):
        v20[i-1] = np.mean(volume[i-20:i])

    sigs = np.zeros(n, dtype=int)
    w = period + 25
    in_squeeze = 0
    last_signal = -999

    for i in range(w, n):
        if i - last_signal < cooldown:
            continue
        if np.isnan(upper[i]):
            continue

        bw = upper[i] - lower[i]
        bw20 = np.nanmean(upper[max(0, i-20):i] - lower[max(0, i-20):i])
        if bw20 == 0 or np.isnan(bw20):
            continue

        if bw < bw20 * squeeze_ratio:
            in_squeeze += 1
            if in_squeeze >= min_bars and close[i] > close[i-1]:
                ok = True
                if vol_confirm:
                    ok = not np.isnan(v20[i-1]) and volume[i] > v20[i-1] * 1.2
                if ok:
                    sigs[i] = 1
                    last_signal = i
        else:
            in_squeeze = 0

    return sigs

# ═══════════════════════════════════════════════════════════
# OBV — On-Balance Volume Divergence
# ═══════════════════════════════════════════════════════════

def obv_signal(close, volume,
               smooth=8, diverge_lookback=10, sig_mult=1.3, cooldown=12):
    """
    Signal when price makes a lower low but OBV makes a higher low
    (bullish divergence), with significance filter.

    SOL 1h best: smooth=8, lookback=10, sig_mult=1.3, cooldown=12 (68% WR)
    """
    n = len(close)

    # OBV
    obv = np.zeros(n); obv[0] = volume[0]
    for i in range(1, n):
        if close[i] > close[i-1]:
            obv[i] = obv[i-1] + volume[i]
        elif close[i] < close[i-1]:
            obv[i] = obv[i-1] - volume[i]
        else:
            obv[i] = obv[i-1]

    # Smooth
    if smooth > 1:
        obv_s = np.convolve(obv, np.ones(smooth)/smooth, mode='same')
    else:
        obv_s = obv

    sigs = np.zeros(n, dtype=int)
    w = max(diverge_lookback, smooth) + 10
    last_signal = -999

    for i in range(w, n):
        if i - last_signal < cooldown:
            continue
        # Price down, OBV up = bullish divergence
        if close[i] < close[i-diverge_lookback] and obv_s[i] > obv_s[i-diverge_lookback]:
            chg = obv_s[i] - obv_s[i-diverge_lookback]
            std = np.std(obv_s[max(0, i-50):i])
            if std > 0 and abs(chg) >= std * sig_mult:
                sigs[i] = 1
                last_signal = i

    return sigs

# ═══════════════════════════════════════════════════════════
# SHORT (INVERSE) INDICATORS — WFA-validated 2026-05
# ═══════════════════════════════════════════════════════════

def vp_short_signal(close, volume,
                    vp_lookback=20, dist_pct=1.0, vol_thresh=2.0, vol_lookback=30):
    """SELL: price ABOVE SMA + volume spike → overbought bounce down."""
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
        if dist < dist_pct and c[i] > sma[i] and v[i] > vm[i-1] * vol_thresh:
            sigs[i] = 1
    full = np.zeros(n, dtype=int)
    full[start:] = sigs
    return full


def bb_short_signal(close, volume,
                    period=30, std=1.5, squeeze_ratio=0.5,
                    min_bars=3, vol_confirm=True, cooldown=10):
    """SELL: squeeze breaks DOWNWARD."""
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
            if in_squeeze >= min_bars and close[i] < close[i-1]:
                ok = True
                if vol_confirm: ok = not np.isnan(v20[i-1]) and volume[i] > v20[i-1] * 1.2
                if ok: sigs[i] = 1; last_signal = i
        else:
            in_squeeze = 0
    return sigs


def obv_short_signal(close, volume,
                     smooth=8, diverge_lookback=10, sig_mult=1.3, cooldown=12):
    """SELL: price HIGHER high, OBV LOWER high = bearish divergence."""
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
        if close[i] > close[i-diverge_lookback] and obv_s[i] < obv_s[i-diverge_lookback]:
            chg = obv_s[i] - obv_s[i-diverge_lookback]
            std = np.std(obv_s[max(0,i-50):i])
            if std > 0 and abs(chg) >= std * sig_mult: sigs[i] = 1; last_signal = i
    return sigs


def rsi_short_signal(close, volume=None, period=14, overbought=70):
    """SELL: RSI crosses above overbought (>70) threshold."""
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
        full = np.zeros(len(close), dtype=int); full[-n:] = sigs; return full
    return sigs


# ═══════════════════════════════════════════════════════════
# TRADING SIMULATOR (for testing)
# ═══════════════════════════════════════════════════════════

def simulate_trades(open_px, high, low, close, signals,
                    fee=0.001, slippage=0.0005, exit_bars=48):
    """
    Long-Only backtest with trailing stop (2×ATR).
    Returns dict with ret%, wr%, maxdd%, sharpe, pf, trades, avg_r%.
    Position sizing: 95% of capital per trade.
    """
    n = len(close)
    atr_arr = _atr(high, low, close)
    capital = 10000; eq = [10000]
    pos = 0; qty = 0; ep = 0; trail = 0; bar_count = 0
    trades = []
    warmup = 150

    for i in range(warmup, n):
        px = open_px[i] if i+1 < n else close[i]

        if pos == 0:
            if signals[i] == 1:
                invest = capital * 0.95
                qty = invest / px
                capital -= invest * (1 + fee + slippage)
                ep = px; pos = 1
                trail = px - atr_arr[i]*2.0 if not np.isnan(atr_arr[i]) else px*0.95
                bar_count = 0
        else:
            bar_count += 1
            exit_now = False
            if bar_count >= exit_bars:
                exit_now = True
            if not np.isnan(atr_arr[i]):
                trail = max(trail, px - atr_arr[i]*2.0)
                if px <= trail:
                    exit_now = True
            if exit_now:
                capital += qty * px * (1 - fee - slippage)
                trades.append(px/ep - 1)
                pos = 0; qty = 0

        if pos == 1:
            eq.append(capital + qty * px)
        else:
            eq.append(capital)

    if pos == 1:
        capital += qty * close[-1] * (1 - fee - slippage)
        trades.append(close[-1]/ep - 1)

    eq = np.array(eq)
    if len(trades) < 3:
        return None

    rets = np.diff(eq)/(eq[:-1]+1e-10)
    total_ret = round((eq[-1]-10000)/100, 2)
    wr = round(sum(1 for t in trades if t>0)/len(trades)*100, 1)
    sh = round(np.mean(rets)/np.std(rets)*np.sqrt(len(rets)), 2) if np.std(rets)>0 else 0
    peak = np.maximum.accumulate(eq)
    mdd = round(np.min((eq-peak)/(peak+1e-10))*100, 2)
    gp = sum(t for t in trades if t>0)
    gl = abs(sum(t for t in trades if t<=0))
    pf = round(gp/gl, 2) if gl>0 else 999

    return {"ret%": total_ret, "wr%": wr, "sharpe": sh, "maxdd%": mdd,
            "pf": min(pf, 99), "trades": len(trades), "avg_r%": round(np.mean(trades)*100, 2)}


# ═══════════════════════════════════════════════════════════
# QUICK TEST (run with: python3 final_indicators.py)
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os
    DATA = os.path.expanduser("~/market_data/phase1")

    print("Final Indicator Quick Test")
    print("=" * 60)

    for sym, tf, fn, params in [
        ("ETH", "1h", vp_signal, {"vp_lookback":20,"dist_pct":0.3,"vol_thresh":1.75,"vol_lookback":30}),
        ("ETH", "4h", vp_signal, {"vp_lookback":20,"dist_pct":1.0,"vol_thresh":2.0,"vol_lookback":30}),
        ("ETH", "4h", bb_signal, {"period":30,"std":1.5,"squeeze_ratio":0.4,"min_bars":1,"vol_confirm":True,"cooldown":5}),
        ("ETH", "1h", bb_signal, {"period":30,"std":1.5,"squeeze_ratio":0.5,"min_bars":3,"vol_confirm":True,"cooldown":10}),
        ("SOL", "1h", obv_signal, {"smooth":8,"diverge_lookback":10,"sig_mult":1.3,"cooldown":12}),
    ]:
        d = np.load(os.path.join(DATA, f"{sym}_{tf}.npz"))
        sigs = fn(d['close'], d['volume'], **params)
        bt = simulate_trades(d['open'], d['high'], d['low'], d['close'], sigs,
                             exit_bars={"1h":48,"4h":24}.get(tf,48))
        if bt:
            print(f"  {sym} {tf} ({fn.__name__}): ret={bt['ret%']:>+6.1f}% "
                  f"wr={bt['wr%']:>5.1f}% maxdd={bt['maxdd%']:>6.1f}% "
                  f"trades={bt['trades']:>3}")
        else:
            print(f"  {sym} {tf} ({fn.__name__}): <3 trades")
    print("Done.")
