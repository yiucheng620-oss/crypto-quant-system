#!/usr/bin/env python3
"""
RSI_ETH_1h SHORT Alert — WFA: 83.6% Bull, 100% Bear (TOP performer)
===================================================================
Checks if RSI_ETH_1h_S triggered in last 8h.
Silent if no signal.
"""

import os, sys, sqlite3
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
INDICATOR_NAME = "RSI_ETH_1h_S"

def check():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int((datetime.now(HKT) - timedelta(hours=8)).timestamp())
    c.execute("""
        SELECT id, price, timestamp FROM signals
        WHERE indicator=? AND created_at > ?
        ORDER BY timestamp DESC LIMIT 1
    """, (INDICATOR_NAME, cutoff))
    row = c.fetchone()

    if not row:
        conn.close()
        return

    sig_id, price, ts = row

    c.execute("""
        CREATE TABLE IF NOT EXISTS short_alerts (signal_id INTEGER PRIMARY KEY, alerted_at INTEGER)
    """)
    c.execute("SELECT signal_id FROM short_alerts WHERE signal_id=?", (sig_id,))
    if c.fetchone():
        conn.close()
        return

    # Accuracy stats
    c.execute("""
        SELECT a.market_condition, COUNT(*), SUM(CASE WHEN a.result='WIN' THEN 1 ELSE 0 END)
        FROM accuracy a JOIN signals s ON a.signal_id=s.id
        WHERE s.indicator=? AND a.horizon='24h'
        GROUP BY a.market_condition
    """, (INDICATOR_NAME,))
    rows = c.fetchall()
    conn.close()

    wr_info = []
    for mc, total, wins in rows:
        wr = round(wins/total*100, 1) if total > 0 else 0
        wr_info.append(f"{mc}: {wr}% ({total}次)")

    ts_str = datetime.fromtimestamp(ts/1000, HKT).strftime("%m/%d %H:%M")
    exit_str = (datetime.fromtimestamp(ts/1000, HKT) + timedelta(hours=24)).strftime("%m/%d %H:%M")

    msg = f"""🔻🔻 **RSI_ETH_1h SHORT Signal** 🔻🔻

💰 **入市價**：${price:,.0f} — ETH SHORT
🕐 Signal 時間：{ts_str} HKT
📊 歷史勝率：{', '.join(wr_info) if wr_info else 'N/A'}

📋 **行動指令**：
1️⃣ **入市**：ETH SHORT @ ${price:,.0f}
2️⃣ **止蝕**：+2×ATR (~${price*0.02:,.0f} above entry)
3️⃣ **離場**：{exit_str} HKT（24h！）
4️⃣ **注碼**：平時一半

🔥 RSI_ETH_1h_S = 全系統最強 Short indicator
   Bull: 83.6% WR | Bear: 100% WR | Range: 80.8%
💡 RSI > 70 = 超買 pullback — catch 佢 24h 內！

_Overbought reversal trade. Tight exit._"""

    c2 = sqlite3.connect(DB_PATH).cursor()
    c2.execute("INSERT INTO short_alerts (signal_id, alerted_at) VALUES (?, ?)",
               (sig_id, int(datetime.now(HKT).timestamp())))
    c2.connection.commit()
    c2.close()

    print(msg)

if __name__ == "__main__":
    check()
