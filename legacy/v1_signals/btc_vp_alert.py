#!/usr/bin/env python3
"""
VP_BTC_4h Alert — Telegram notification when VP_BTC_4h fires
=============================================================
- Best indicator: 80% WR in range, 75% in bull
- Runs every 4h, checks if a new signal appeared
- Only sends alert if signal is fresh (<8h old)
"""

import os, sys, json, time
from datetime import datetime, timezone, timedelta
import numpy as np
import sqlite3

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
DATA_DIR = os.path.expanduser("~/market_data/phase1")

def check_vp_btc_4h():
    """Run VP_BTC_4h on latest data, check for new signal."""
    data = np.load(os.path.join(DATA_DIR, "BTC_4h.npz"))
    close, volume, ts = data["close"], data["volume"], data["timestamp"]

    # VP_BTC_4h params
    vp_lookback, dist_pct, vol_thresh, vol_lookback = 20, 1.0, 2.0, 30

    n = len(close)
    # SMA
    sma = np.full(n, np.nan)
    sma[vp_lookback-1:] = np.convolve(close, np.ones(vp_lookback)/vp_lookback, mode='valid')
    # Volume mean
    vm = np.full(n, np.nan)
    for i in range(vol_lookback, n+1):
        vm[i-1] = np.mean(volume[i-vol_lookback:i])

    # Find latest signal
    w = max(vp_lookback, vol_lookback) + 10
    latest_signal_ts = 0
    latest_signal_price = 0

    for i in range(n-1, w, -1):  # Search backward from end
        if np.isnan(sma[i]) or np.isnan(vm[i-1]):
            continue
        dist = abs(close[i] - sma[i]) / sma[i] * 100
        if dist < dist_pct and close[i] < sma[i] and volume[i] > vm[i-1] * vol_thresh:
            latest_signal_ts = int(ts[i] / 1000) if ts[i] > 1e10 else int(ts[i])
            latest_signal_price = float(close[i])
            break

    # Check if signal is fresh (<8h)
    now = int(time.time())
    age_hours = (now - latest_signal_ts) / 3600 if latest_signal_ts > 0 else 999

    # Market condition
    lookback = min(50, n)
    recent = close[-lookback:]
    slope, _ = np.polyfit(np.arange(lookback), recent, 1)
    slope_pct = (slope / recent[-1]) * 100 * lookback

    tr = np.maximum(data["high"][-lookback:] - data["low"][-lookback:],
                    np.abs(data["high"][-lookback:] - np.roll(recent, 1)))
    atr = np.mean(tr[-14:])
    vol_pct = (atr / recent[-1]) * 100

    if abs(slope_pct) > 1.5:
        mc = "bull" if slope_pct > 0 else "bear"
    elif vol_pct > 3.0:
        mc = "volatile"
    else:
        mc = "range"

    # Accuracy lookup from DB
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT s.indicator, a.market_condition, COUNT(*) as total,
               SUM(CASE WHEN a.result='WIN' THEN 1 ELSE 0 END) as wins
        FROM accuracy a JOIN signals s ON a.signal_id = s.id
        WHERE s.indicator='VP_BTC_4h_L' AND a.horizon='24h' AND a.market_condition=?
        GROUP BY a.market_condition
    """, (mc,))
    row = c.fetchone()
    conn.close()

    total = row[2] if row else 0
    wins = row[3] if row else 0
    hist_wr = round(wins / total * 100, 1) if total > 0 else None
    hist_n = total

    # Check DB for already-alerted
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT 1 FROM signals
        WHERE indicator='VP_BTC_4h_L' AND timestamp=? AND timeframe='4h' AND coin='BTC'
    """, (latest_signal_ts,))
    already_logged = c.fetchone() is not None
    conn.close()

    return {
        "signal": age_hours < 8,
        "price": latest_signal_price,
        "age_hours": round(age_hours, 1),
        "timestamp": latest_signal_ts,
        "market": mc,
        "vol_pct": round(vol_pct, 2),
        "hist_wr": hist_wr,
        "hist_n": hist_n,
        "already_logged": already_logged,
    }


if __name__ == "__main__":
    result = check_vp_btc_4h()

    if result["signal"]:
        signal_age = datetime.fromtimestamp(result["timestamp"], HKT).strftime("%m/%d %H:%M")
        mc_emoji = {"bull": "🟢", "bear": "🔴", "range": "🟡", "volatile": "⚡"}.get(result["market"], "❓")
        mc_str = result["market"].upper()
        wr_str = f"{result['hist_wr']}%（{result['hist_n']}次）" if result['hist_wr'] else "數據不足"

        # Stop-loss: 2×ATR below entry
        sl_price = result["price"] * (1 - result["vol_pct"]/100 * 2)
        sl_usd = result["price"] - sl_price

        # Exit time
        exit_hkt = (datetime.fromtimestamp(result["timestamp"], HKT) + timedelta(hours=24)).strftime("%m/%d %H:%M")

        # Cross-check: any other indicator also active?
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""SELECT indicator FROM signals WHERE timestamp>? AND timestamp<? AND coin='BTC' AND indicator!='VP_BTC_4h_L'""",
                  (result["timestamp"] - 8*3600, result["timestamp"]))
        other_sigs = [r[0] for r in c.fetchall()]
        conn.close()

        cross_confirm = "雙重確認！" if other_sigs else "單一 signal"

        msg = f"""🔔 **VP_BTC_4h BUY Signal**

{mc_emoji} 市況：{mc_str}
💰 **入市價**：${result['price']:,.0f}
🕐 Signal 時間：{signal_age} HKT
📊 歷史勝率：{wr_str}
🔒 確認度：{cross_confirm}

📋 **行動指令**：

1️⃣ **入市**：BTC Long  @ ${result['price']:,.0f}
2️⃣ **止蝕**：${sl_price:,.0f} 以下（約 -${sl_usd:,.0f} USD，2×ATR）
3️⃣ **離場**：{exit_hkt} HKT 之前（24h，唔可以拖！）
4️⃣ **注碼**：平時嘅 standard size，唔加唔減

⚠️ 24h WR {wr_str}，48h 跌到 55.6% → 一定準時走
💡 VP_BTC_4h 係全系統最強 indicator（WFA 驗證）"""

        print(msg)
    else:
        # Silent — no signal
        pass
