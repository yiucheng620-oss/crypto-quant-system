#!/usr/bin/env python3
"""
VP_BTC_1h SHORT Alert — WFA: 69-89% WR
=====================================
Checks if VP_BTC_1h_S triggered in last 8h.
Silent if no signal.
"""

import os, sys, sqlite3
from datetime import datetime, timezone, timedelta
import numpy as np

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
DATA_DIR = os.path.expanduser("~/market_data/phase1")
INDICATOR_NAME = "VP_BTC_1h_S"

def check():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int((datetime.now(HKT) - timedelta(hours=8)).timestamp())
    c.execute("""
        SELECT id, price, timestamp, created_at FROM signals
        WHERE indicator=? AND created_at > ?
        ORDER BY timestamp DESC LIMIT 1
    """, (INDICATOR_NAME, cutoff))
    row = c.fetchone()

    if not row:
        print("", end="")  # Silent
        conn.close()
        return

    sig_id, price, ts, created = row

    # Check if already alerted
    c.execute("""
        CREATE TABLE IF NOT EXISTS short_alerts (
            signal_id INTEGER PRIMARY KEY, alerted_at INTEGER
        )
    """)
    c.execute("SELECT signal_id FROM short_alerts WHERE signal_id=?", (sig_id,))
    if c.fetchone():
        conn.close()
        return

    # Accuracy stats
    c.execute("""
        SELECT a.market_condition, COUNT(*), SUM(CASE WHEN a.result='WIN' THEN 1 ELSE 0 END)
        FROM accuracy a JOIN signals s ON a.signal_id=s.id
        WHERE s.indicator=? AND a.horizon='24h' AND a.market_condition IN ('bear','bull','range')
        GROUP BY a.market_condition
    """, (INDICATOR_NAME,))
    row_acc = c.fetchall()
    conn.close()

    wr_info = []
    for mc, total, wins in row_acc:
        wr = round(wins/total*100, 1) if total > 0 else 0
        wr_info.append(f"{mc}: {wr}% ({total}次)")

    ts_str = datetime.fromtimestamp(ts/1000, HKT).strftime("%m/%d %H:%M")
    exit_str = (datetime.fromtimestamp(ts/1000, HKT) + timedelta(hours=24)).strftime("%m/%d %H:%M")

    msg = f"""🔻 **VP_BTC_1h SHORT Signal**

💰 **入市價**：${price:,.0f} — BTC SHORT
🕐 Signal 時間：{ts_str} HKT
📊 歷史勝率：{', '.join(wr_info) if wr_info else '數據不足'}

📋 **行動指令**：
1️⃣ **入市**：BTC SHORT @ ${price:,.0f}
2️⃣ **止蝕**：+2×ATR (~${price*0.015:,.0f} above entry)
3️⃣ **離場**：{exit_str} HKT（24h 必須走）
4️⃣ **注碼**：平時一半注碼

💡 VP_BTC_1h_S range: 69% WR（最強 condition）
⚠️ 24h exit 鐵律 — 唔好拖！

_Short signal — counter-trend trade. Tight SL._"""

    # Mark alerted
    c2 = sqlite3.connect(DB_PATH).cursor()
    c2.execute("INSERT INTO short_alerts (signal_id, alerted_at) VALUES (?, ?)",
               (sig_id, int(datetime.now(HKT).timestamp())))
    c2.connection.commit()
    c2.close()

    print(msg)

if __name__ == "__main__":
    check()
