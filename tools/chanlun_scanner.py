#!/usr/bin/env python3
"""
纏論 1/2/3 買賣點 信號掃描器 (一步到位專業版)
==============================================
Standalone module: ZigZag → 中樞 Detection → 6大買賣點 Signal → JSON output
同時自動採集多維環境特徵 (Funding Rate, OI, Smart Money) 存儲到特徵數據庫。

參數設定 (經 P2 研究優化)：
  BTC: (1h min_pct=2.0/min_w=1.0) (4h min_pct=3.0/min_w=2.0)
  ETH: (1h min_pct=1.5/min_w=2.5) (4h min_pct=3.0/min_w=0.5)
  SOL: (1h min_pct=3.0/min_w=1.5) (4h min_pct=5.0/min_w=3.0)
"""
import numpy as np
import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

# 目錄與路徑設定
HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
DATA_DIR = os.path.join(WORKSPACE, "data/market/phase1")
SYS_DIR = os.path.join(WORKSPACE, "chanlun_system")
SIGNAL_FILE = os.path.join(SYS_DIR, "data/signals", "latest.json")
FEATURES_FILE = os.path.join(SYS_DIR, "data/exports", "signal_features.json")
SM_FILE = os.path.expanduser("~/workspace/data/smart_money/smart_money_latest.json")

os.makedirs(os.path.dirname(SIGNAL_FILE), exist_ok=True)
os.makedirs(os.path.dirname(FEATURES_FILE), exist_ok=True)

# ── 優化參數 ──
PARAMS = {
    "BTC": {"1h": (2.0, 1.0, 15), "4h": (3.0, 2.0, 8)},
    "ETH": {"1h": (1.5, 2.5, 15), "4h": (3.0, 0.5, 8)},
    "SOL": {"1h": (3.0, 1.5, 15), "4h": (5.0, 3.0, 8)},
}

# ── 核心指標函數 ──

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

def calculate_atr(high, low, close, period=14):
    tr = np.zeros_like(close)
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], 
                    abs(high[i] - close[i-1]), 
                    abs(low[i] - close[i-1]))
    atr = np.zeros_like(close)
    atr[period-1] = np.mean(tr[:period])
    alpha = 1.0 / period
    for i in range(period, len(close)):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i-1]
    return atr

def calculate_rsi(close, period=14):
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    
    avg_gain = np.zeros_like(close)
    avg_loss = np.zeros_like(close)
    
    avg_gain[period] = np.mean(gain[:period])
    avg_loss[period] = np.mean(loss[:period])
    
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i-1] * 13 + gain[i-1]) / 14
        avg_loss[i] = (avg_loss[i-1] * 13 + loss[i-1]) / 14
        
    rsi = np.zeros_like(close)
    for i in range(period, len(close)):
        if avg_loss[i] == 0:
            rsi[i] = 100
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100 - (100 / (1 + rs))
    return rsi

def find_zigzag_swings(high, low, min_pct):
    swings = []
    last_extreme = None
    direction = None
    for i in range(1, len(high)):
        if direction is None:
            if last_extreme is None and i > 20:
                lmax = np.argmax(high[i-20:i]) + (i-20)
                lmin = np.argmin(low[i-20:i]) + (i-20)
                if high[lmax] / low[lmin] - 1 > min_pct / 100:
                    if lmax < lmin:
                        last_extreme = (lmax, high[lmax], 'H')
                        direction = 'down'
                    else:
                        last_extreme = (lmin, low[lmin], 'L')
                        direction = 'up'
            continue
        if direction == 'up':
            if high[i] > last_extreme[1]:
                last_extreme = (i, high[i], 'H')
            drop_pct = (last_extreme[1] - low[i]) / last_extreme[1] * 100
            if drop_pct >= min_pct:
                swings.append(last_extreme)
                direction = 'down'
                bi = np.argmin(low[last_extreme[0]:i+1]) + last_extreme[0]
                last_extreme = (bi, low[bi], 'L')
        else:
            if low[i] < last_extreme[1]:
                last_extreme = (i, low[i], 'L')
            rise_pct = (high[i] - last_extreme[1]) / last_extreme[1] * 100
            if rise_pct >= min_pct:
                swings.append(last_extreme)
                direction = 'up'
                bi = np.argmax(high[last_extreme[0]:i+1]) + last_extreme[0]
                last_extreme = (bi, high[bi], 'H')
    return swings

def detect_pivots(swings, min_width_pct, min_bars):
    pivots = []
    if len(swings) < 4:
        return pivots
    i = 0
    while i < len(swings) - 3:
        s0, s1, s2, s3 = swings[i], swings[i+1], swings[i+2], swings[i+3]
        duration = s3[0] - s0[0]
        if duration < min_bars:
            i += 1; continue
        highs = [s[1] for s in [s0, s1, s2, s3] if s[2] == 'H']
        lows = [s[1] for s in [s0, s1, s2, s3] if s[2] == 'L']
        if len(highs) >= 2 and len(lows) >= 2:
            upper = min(highs)
            lower = max(lows)
            width_pct = (upper / lower - 1) * 100
            if upper > lower and width_pct >= min_width_pct:
                pivots.append({
                    "start": int(s0[0]), "end": int(s3[0]),
                    "upper": round(upper, 2), "lower": round(lower, 2),
                    "width_pct": round(width_pct, 2),
                })
                i += 3; continue
        i += 1
    return pivots

def check_divergence(close, macd_line, extreme_idx, lookback=60, direction='bullish'):
    start = max(0, extreme_idx - lookback)
    end = min(len(close), extreme_idx + 5)
    seg_close = close[start:end]
    seg_macd = macd_line[start:end]
    prev_points = []
    for j in range(5, len(seg_close) - 10):
        window = seg_close[max(0, j-5):min(len(seg_close), j+6)]
        if direction == 'bullish':
            if seg_close[j] == min(window):
                prev_points.append(j)
        else:
            if seg_close[j] == max(window):
                prev_points.append(j)
    if len(prev_points) < 2:
        return False
    prev_idx = prev_points[-2]
    curr_idx = len(seg_close) - 5
    if direction == 'bullish':
        price_lower_low = seg_close[curr_idx] < seg_close[prev_idx]
        macd_higher_low = seg_macd[curr_idx] > seg_macd[prev_idx]
        return price_lower_low and macd_higher_low
    else:
        price_higher_high = seg_close[curr_idx] > seg_close[prev_idx]
        macd_lower_high = seg_macd[curr_idx] < seg_macd[prev_idx]
        return price_higher_high and macd_lower_high

def score_signal_quality(signal, close, macd_hist, volume):
    """
    為 1buy 信號評分 (0-100)，基於 MACD 確認、中樞質量、成交量。
    Returns: (score: int, label: str)
    """
    score = 0

    # a. MACD Divergence Bonus (+40) — 用 histogram 確認
    lowest_idx = int(signal.get("lowest_idx", signal.get("idx", 0)))
    try:
        has_div = check_divergence(close, macd_hist, lowest_idx, direction='bullish')
        if has_div:
            score += 40
    except Exception:
        pass

    # b. Pivot Quality Bonus (+30) — 中樞大小
    width_pct = signal.get("pivot_width_pct", 0)
    if width_pct > 3.0:
        score += 30          # 大中樞
    elif width_pct > 1.0:
        score += 15          # 中等中樞
    else:
        score += 5           # 細中樞，可能假

    # c. Volume Confirmation (+30)
    v_idx = signal.get("idx", 0)
    start_vol = max(0, v_idx - 20)
    avg_vol = float(np.mean(volume[start_vol:v_idx + 1])) if v_idx > 0 and len(volume) > v_idx else 1.0
    vol_at_signal = float(volume[v_idx]) if v_idx < len(volume) else 1.0
    vol_ratio = vol_at_signal / avg_vol if avg_vol > 0 else 1.0

    if vol_ratio > 1.5:
        score += 30
    elif vol_ratio > 1.2:
        score += 15
    # else +0

    # Label
    if score >= 75:
        label = "strong"
    elif score >= 50:
        label = "normal"
    else:
        label = "weak"

    return score, label


def find_all_signals(pivots, close, high, low, macd_line, swings, atr, volume=None, macd_hist=None, search_window=500):
    signals = []
    swing_highs = [s for s in swings if s[2] == 'H']
    swing_lows = [s for s in swings if s[2] == 'L']
    
    for pivot in pivots:
        p_end = pivot["end"]
        p_lower = pivot["lower"]
        p_upper = pivot["upper"]
        search_end = min(p_end + search_window, len(close))
        
        # --- 1买 (1buy) ---
        below_pivot = False
        lowest_idx = p_end
        lowest_price = close[p_end]
        for i in range(p_end, search_end):
            if close[i] < p_lower:
                below_pivot = True
                if low[i] < lowest_price:
                    lowest_price = low[i]
                    lowest_idx = i
            if below_pivot and close[i] > p_lower and i > lowest_idx + 1:
                if check_divergence(close, macd_line, lowest_idx, direction='bullish'):
                    sig = {
                        "idx": int(i), "type": "1buy", "entry_price": round(float(close[i]), 2),
                        "pivot_lower": p_lower, "pivot_upper": p_upper, "pivot_width_pct": pivot["width_pct"],
                        "lowest_price": round(float(lowest_price), 2), "lowest_idx": int(lowest_idx),
                        "highest_price": None, "atr": round(float(atr[i]), 2)
                    }
                    # Signal quality scoring
                    if volume is not None and macd_hist is not None:
                        q_score, q_label = score_signal_quality(sig, close, macd_hist, volume)
                        sig["quality_score"] = q_score
                        sig["quality_label"] = q_label
                    else:
                        sig["quality_score"] = 0
                        sig["quality_label"] = "unknown"
                    signals.append(sig)
                break
                
        # --- 1卖 (1sell) ---
        above_pivot = False
        highest_idx = p_end
        highest_price = close[p_end]
        for i in range(p_end, search_end):
            if close[i] > p_upper:
                above_pivot = True
                if high[i] > highest_price:
                    highest_price = high[i]
                    highest_idx = i
            if above_pivot and close[i] < p_upper and i > highest_idx + 1:
                if check_divergence(close, macd_line, highest_idx, direction='bearish'):
                    signals.append({
                        "idx": int(i), "type": "1sell", "entry_price": round(float(close[i]), 2),
                        "pivot_lower": p_lower, "pivot_upper": p_upper, "pivot_width_pct": pivot["width_pct"],
                        "lowest_price": None, "highest_price": round(float(highest_price), 2), "atr": round(float(atr[i]), 2)
                    })
                break
                
        # --- 3买 (3buy) ---
        broken_up = False
        for i in range(p_end, search_end):
            if close[i] > p_upper:
                broken_up = True
            if broken_up:
                sub_lows = [s for s in swing_lows if s[0] > p_end and s[0] <= i]
                if sub_lows:
                    last_sl = sub_lows[-1]
                    if last_sl[1] > p_upper:
                        signals.append({
                            "idx": int(last_sl[0]), "type": "3buy", "entry_price": round(float(close[last_sl[0]]), 2),
                            "pivot_lower": p_lower, "pivot_upper": p_upper, "pivot_width_pct": pivot["width_pct"],
                            "lowest_price": round(float(last_sl[1]), 2), "highest_price": None, "atr": round(float(atr[last_sl[0]]), 2)
                        })
                        break
                        
        # --- 3卖 (3sell) ---
        broken_down = False
        for i in range(p_end, search_end):
            if close[i] < p_lower:
                broken_down = True
            if broken_down:
                sub_highs = [s for s in swing_highs if s[0] > p_end and s[0] <= i]
                if sub_highs:
                    last_sh = sub_highs[-1]
                    if last_sh[1] < p_lower:
                        signals.append({
                            "idx": int(last_sh[0]), "type": "3sell", "entry_price": round(float(close[last_sh[0]]), 2),
                            "pivot_lower": p_lower, "pivot_upper": p_upper, "pivot_width_pct": pivot["width_pct"],
                            "lowest_price": None, "highest_price": round(float(last_sh[1]), 2), "atr": round(float(atr[last_sh[0]]), 2)
                        })
                        break
                        
        # --- 2买 (2buy) ---
        one_buy_signals = [s for s in signals if s["type"] == "1buy" and s["pivot_lower"] == p_lower]
        if one_buy_signals:
            one_buy_idx = one_buy_signals[0]["idx"]
            sub_lows = [s for s in swing_lows if s[0] > one_buy_idx and s[0] < search_end]
            for sl in sub_lows:
                if sl[1] > one_buy_signals[0]["lowest_price"] and sl[1] < p_lower * 1.05:
                    signals.append({
                        "idx": int(sl[0]), "type": "2buy", "entry_price": round(float(close[sl[0]]), 2),
                        "pivot_lower": p_lower, "pivot_upper": p_upper, "pivot_width_pct": pivot["width_pct"],
                        "lowest_price": round(float(sl[1]), 2), "highest_price": None, "atr": round(float(atr[sl[0]]), 2)
                    })
                    break
                    
        # --- 2卖 (2sell) ---
        one_sell_signals = [s for s in signals if s["type"] == "1sell" and s["pivot_upper"] == p_upper]
        if one_sell_signals:
            one_sell_idx = one_sell_signals[0]["idx"]
            sub_highs = [s for s in swing_highs if s[0] > one_sell_idx and s[0] < search_end]
            for sh in sub_highs:
                if sh[1] < one_sell_signals[0]["highest_price"] and sh[1] > p_upper * 0.95:
                    signals.append({
                        "idx": int(sh[0]), "type": "2sell", "entry_price": round(float(close[sh[0]]), 2),
                        "pivot_lower": p_lower, "pivot_upper": p_upper, "pivot_width_pct": pivot["width_pct"],
                        "lowest_price": None, "highest_price": round(float(sh[1]), 2), "atr": round(float(atr[sh[0]]), 2)
                    })
                    break

    return signals

# ── 多維環境採集 ──

def capture_features(signal, rsi_val, vol_ratio):
    """採集實時 Funding, OI 以及本地 Smart Money"""
    symbol = signal["symbol"]
    binance_symbol = f"{symbol}USDT"
    key = f"{symbol}_{signal['tf']}_{signal['type']}_{signal['entry_price']}_{signal['idx']}"
    
    # 讀取現有 features 防止重複寫入
    features = {}
    if os.path.exists(FEATURES_FILE):
        try:
            with open(FEATURES_FILE) as f:
                features = json.load(f)
        except:
            pass
            
    if key in features:
        return features[key]  # 已存在直接返回
        
    print(f"📥 正在採集信號環境特徵 {key}...")
    
    # 1. 獲取實時 Funding Rate
    live_funding = None
    live_mark = None
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": binance_symbol}, timeout=10)
        if r.status_code == 200:
            res = r.json()
            live_funding = float(res.get("lastFundingRate", 0))
            live_mark = float(res.get("markPrice", 0))
    except Exception as e:
        print(f"  ⚠️ Funding fetch 失敗: {e}")
        
    # 2. 獲取實時 Open Interest
    live_oi = None
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": binance_symbol}, timeout=10)
        if r.status_code == 200:
            res = r.json()
            live_oi = float(res.get("openInterest", 0))
    except Exception as e:
        print(f"  ⚠️ OI fetch 失敗: {e}")
        
    # 3. 讀取本地 Smart Money
    sm_ratio = None
    sm_long_entry = None
    sm_short_entry = None
    sm_long_profit = None
    if os.path.exists(SM_FILE):
        try:
            with open(SM_FILE) as f:
                sm_all = json.load(f)
            sm_coin = sm_all.get("data", {}).get(f"{symbol}USDT", {})
            if sm_coin:
                sm_ratio = sm_coin.get("longShortRatio")
                sm_long_entry = sm_coin.get("longWhalesAvgEntryPrice")
                sm_short_entry = sm_coin.get("shortWhalesAvgEntryPrice")
                long_traders = max(sm_coin.get("longTraders", 1), 1)
                sm_long_profit = sm_coin.get("longProfitTraders", 0) / long_traders * 100
        except Exception as e:
            print(f"  ⚠️ Smart Money 讀取失敗: {e}")
            
    # 4. 讀取本地 Macro (DXY, VIX) 
    vix = None
    dxy = None
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        summary_path = os.path.expanduser(f"~/market_data/daily/{today}/_summary.json")
        if os.path.exists(summary_path):
            with open(summary_path) as f:
                mac = json.load(f)
            vix = mac.get("^VIX", {}).get("close")
            dxy = mac.get("DX-Y.NYB", {}).get("close")
    except:
        pass
        
    feature_entry = {
        "timestamp": datetime.now(HKT).isoformat(),
        "symbol": symbol,
        "tf": signal["tf"],
        "type": signal["type"],
        "entry_price": signal["entry_price"],
        "idx": signal["idx"],
        "pivot_lower": signal["pivot_lower"],
        "pivot_upper": signal["pivot_upper"],
        "lowest_price": signal["lowest_price"],
        "highest_price": signal["highest_price"],
        "atr_14": signal["atr"],
        "rsi_14": round(float(rsi_val), 2) if rsi_val else None,
        "volume_ratio": round(float(vol_ratio), 2) if vol_ratio else None,
        "funding_rate": live_funding,
        "open_interest": live_oi,
        "mark_price": live_mark,
        "whale_ls_ratio": sm_ratio,
        "whale_long_avg_entry": sm_long_entry,
        "whale_short_avg_entry": sm_short_entry,
        "whale_long_profit_pct": sm_long_profit,
        "vix": vix,
        "dxy": dxy,
        "quality_score": signal.get("quality_score"),
        "quality_label": signal.get("quality_label"),
        "pnl_pips": None,  # 待後續分析回填
        "win": None        # 待後續分析回填
    }
    
    features[key] = feature_entry
    with open(FEATURES_FILE, "w") as f:
        json.dump(features, f, indent=2, default=str)
        
    return feature_entry

# ── 掃描與彙整 ──

def scan_symbol(symbol, tf):
    fname = f"{symbol}_{tf}.npz"
    fpath = os.path.join(DATA_DIR, fname)
    if not os.path.exists(fpath):
        return []
        
    min_pct, min_width, min_bars = PARAMS[symbol][tf]
    data = np.load(fpath)
    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"]
    
    m_line, _, macd_hist = macd(close)
    atr = calculate_atr(high, low, close)
    rsi = calculate_rsi(close)
    
    swings = find_zigzag_swings(high, low, min_pct)
    pivots = detect_pivots(swings, min_width, min_bars)
    
    if not pivots:
        return []
        
    signals = find_all_signals(pivots, close, high, low, m_line, swings, atr, volume=volume, macd_hist=macd_hist)
    
    # 篩選最新 50 根 K 線內活躍的信號 (保留去重)
    active_signals = []
    seen_keys = set()
    for s in signals:
        bars_ago = len(close) - 1 - s["idx"]
        key = f"{s['type']}_{s['idx']}"
        if bars_ago <= 50 and key not in seen_keys:
            seen_keys.add(key)
            s["symbol"] = symbol
            s["tf"] = tf
            s["bars_ago"] = int(bars_ago)
            s["current_price"] = round(float(close[-1]), 2)
            s["pct_from_entry"] = round((close[-1] / s["entry_price"] - 1) * 100, 2)
            
            # 計算 volume ratio
            v_idx = s["idx"]
            avg_vol = np.mean(volume[max(0, v_idx-20):v_idx+1]) if v_idx > 0 else 1.0
            vol_ratio = volume[v_idx] / avg_vol if avg_vol > 0 else 1.0
            
            # 採集環境特徵
            capture_features(s, rsi[v_idx], vol_ratio)
            active_signals.append(s)
            
    return active_signals

def scan_all(save=False):
    all_signals = []
    stats = {}
    
    for symbol in ["BTC", "ETH", "SOL"]:
        for tf in ["1h", "4h"]:
            fname = f"{symbol}_{tf}.npz"
            fpath = os.path.join(DATA_DIR, fname)
            if not os.path.exists(fpath):
                continue
                
            data = np.load(fpath)
            close = data["close"]
            high = data["high"]
            low = data["low"]
            volume = data["volume"]
            
            min_pct, min_width, min_bars = PARAMS[symbol][tf]
            swings = find_zigzag_swings(high, low, min_pct)
            pivots = detect_pivots(swings, min_width, min_bars)
            
            m_line, _, macd_hist = macd(close)
            atr = calculate_atr(high, low, close)
            rsi = calculate_rsi(close)
            
            signals = find_all_signals(pivots, close, high, low, m_line, swings, atr, volume=volume, macd_hist=macd_hist)
            
            # 補全 meta
            for s in signals:
                s["symbol"] = symbol
                s["tf"] = tf
                s["bars_ago"] = int(len(close) - 1 - s["idx"])
                s["current_price"] = round(float(close[-1]), 2)
                s["pct_from_entry"] = round((close[-1] / s["entry_price"] - 1) * 100, 2)
                
            active = [s for s in signals if s["bars_ago"] <= 50]
            
            # 採集 active 信號的環境特徵
            for s in active:
                v_idx = s["idx"]
                avg_vol = np.mean(volume[max(0, v_idx-20):v_idx+1]) if v_idx > 0 else 1.0
                vol_ratio = volume[v_idx] / avg_vol if avg_vol > 0 else 1.0
                capture_features(s, rsi[v_idx], vol_ratio)
                
            all_signals.extend(active)
            
            # 統計
            stats[f"{symbol}_{tf}"] = {
                "swings": len(swings),
                "pivots": len(pivots),
                "total_signals": len(signals),
                "active_signals": len(active),
                "breakdown": {
                    t: len([x for x in signals if x["type"] == t])
                    for t in ["1buy", "2buy", "3buy", "1sell", "2sell", "3sell"]
                }
            }
            
    output = {
        "scanned_at": datetime.now(HKT).isoformat(),
        "timestamp_ts": datetime.now(HKT).timestamp(),
        "stats": stats,
        "signals": all_signals,
        "summary": [],
    }
    
    if all_signals:
        output["summary"].append(f"🔔 纏論 買賣點 信號 ({len(all_signals)} 活躍):")
        for s in all_signals:
            direction = "🟢" if s["pct_from_entry"] >= 0 else "🔴"
            output["summary"].append(
                f"  {s['symbol']} {s['tf']} ({s['type'].upper()}): entry=${s['entry_price']:.1f} "
                f"(now ${s['current_price']:.1f}, {direction}{s['pct_from_entry']:+.1f}%) "
                f"[{s['bars_ago']} bars ago]"
            )
            output["summary"].append(
                f"    Pivot: ${s['pivot_lower']:.1f}-${s['pivot_upper']:.1f} "
                f"(w={s['pivot_width_pct']:.1f}%), atr=${s['atr']:.2f}"
            )
    else:
        output["summary"].append("✅ 無活躍 纏論 買賣點 信號")
        
    if save:
        tmp = SIGNAL_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(output, f, indent=2, default=str)
        os.replace(tmp, SIGNAL_FILE)
        
    return output

if __name__ == "__main__":
    save_mode = "--save" in sys.argv
    output = scan_all(save=save_mode)
    
    # 輸出摘要
    for line in output["summary"]:
        print(line)
        
    if save_mode:
        print(f"\n✅ 信號與特徵採集完成！檔案已儲存。")
