#!/usr/bin/env python3
"""
Daily Trader's Brief
══════════════════════════════════════════════
Reads SQLite: current market state + recent signals.
Output: Telegram-friendly brief (no LLM needed).

Cron: 0 9 * * * (HKT = UTC+8 → 01:00 UTC)
"""

import os, sys, sqlite3, requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")

# ──── FEAR & GREED FETCH ────

def fetch_fear_greed():
    """Fetch Crypto Fear & Greed Index from alternative.me."""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if data:
            val = int(data[0]["value"])
            label = data[0]["value_classification"]
            return {"value": val, "label": label}
    except Exception as e:
        print(f"[WARN] Fear & Greed fetch failed: {e}", file=sys.stderr)
    return None


def format_fear_greed(fg):
    """Format Fear & Greed for display."""
    if not fg:
        return "😐 Fear & Greed: 冇數據"
    val = fg["value"]
    label = fg["label"]
    # Emoji based on zone
    if val <= 25:
        emoji = "😨"
        tip = "極度恐懼 → 歷史多數係底，可以考慮 accumulate"
    elif val <= 45:
        emoji = "😟"
        tip = "恐懼 → 觀望為主"
    elif val <= 55:
        emoji = "😐"
        tip = "中性 → 跟 indicator"
    elif val <= 75:
        emoji = "😊"
        tip = "貪婪 → 小心 FOMO"
    else:
        emoji = "🤤"
        tip = "極度貪婪 → 歷史多數係頂，考慮 take profit"
    return f"{emoji} **{label}** ({val}/100)\n   {tip}"


# ── Main ──

now = datetime.now(HKT)

# ── Load data ──
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Current market state (last 6h)
cutoff_ts = int((now - timedelta(hours=6)).timestamp())
c.execute("""
    SELECT coin, timeframe, condition, adx, volatility_pct, trend_strength
    FROM market_state
    WHERE timestamp > ?
    ORDER BY timestamp DESC
""", (cutoff_ts,))
market_rows = c.fetchall()

# Recent signals (last 24h)
sig_cutoff = int((now - timedelta(hours=24)).timestamp())
c.execute("""
    SELECT coin, timeframe, indicator, price, timestamp, direction
    FROM signals
    WHERE created_at > ?
    ORDER BY timestamp DESC
""", (sig_cutoff,))
recent_signals = c.fetchall()

# Accuracy summary (last 30 days)
acc_cutoff = int((now - timedelta(days=30)).timestamp())
c.execute("""
    SELECT s.indicator, s.coin, s.timeframe, a.market_condition,
           COUNT(*) as total,
           SUM(CASE WHEN a.result='WIN' THEN 1 ELSE 0 END) as wins,
           AVG(CASE WHEN a.result='WIN' THEN a.pnl_pct ELSE NULL END) as avg_win
    FROM accuracy a
    JOIN signals s ON a.signal_id = s.id
    WHERE a.checked_at > ? AND a.horizon='24h'
    GROUP BY s.indicator, s.coin, s.timeframe, a.market_condition
    HAVING total >= 3
""", (acc_cutoff,))
acc_rows = c.fetchall()
conn.close()

# ── Build brief ──

lines = []
lines.append(f"📊 **Daily Brief** — {now.strftime('%Y-%m-%d %a')}")
lines.append("")

# Market state
lines.append("**🌤 市況**")
if market_rows:
    seen = set()
    for r in market_rows:
        key = (r["coin"], r["timeframe"])
        if key in seen:
            continue
        seen.add(key)
        emoji = {"bull": "🟢", "range": "🟡", "bear": "🔴", "volatile": "🟣"}.get(r["condition"], "⚪")
        lines.append(f"  {emoji} {r['coin']} {r['timeframe']}: {r['condition']} (ADX={r['adx']}, Vol={r['volatility_pct']}%)")
else:
    lines.append("  ⚠️ 冇近期數據")

lines.append("")

# Active signals
lines.append("**📡 最近 Signal (24h)**")
if recent_signals:
    for r in recent_signals[:10]:
        ts = datetime.fromtimestamp(r["timestamp"]/1000, HKT).strftime("%m/%d %H:%M")
        dir_emoji = "🔻" if r["direction"] == "SHORT" else "🔺"
        lines.append(f"  {dir_emoji} {r['indicator']} {r['coin']} {r['timeframe']} @ {r['price']:.2f} ({ts})")
else:
    lines.append("  冇 signal")

lines.append("")

# Best indicators for current conditions
lines.append("**🎯 今日建議用**")
current_conditions = set()
for r in market_rows:
    current_conditions.add((r["coin"], r["timeframe"], r["condition"]))

best_for_today = []
for coin_tf_cond in current_conditions:
    coin, tf, cond = coin_tf_cond
    # Skip 1h indicators in range/bear — they're provably bad
    if tf == "1h" and cond in ("range", "bear"):
        continue
    matching = [r for r in acc_rows if r["coin"] == coin and r["timeframe"] == tf and r["market_condition"] == cond]
    for r in matching:
        wr = round(r["wins"] / r["total"] * 100, 1)
        if wr >= 55 and r["total"] >= 5:  # Need enough signals to trust
            best_for_today.append((wr, r["total"], f"{r['indicator']} {r['coin']} {r['timeframe']} ({wr}% WR, n={r['total']})"))

best_for_today.sort(reverse=True)
if best_for_today:
    for wr, total, desc in best_for_today[:5]:
        lines.append(f"  ✅ {desc}")
else:
    lines.append("  ⚠️ 今日冇 indicator 有明確優勢")
    lines.append("  → 建議：唔Trade，等方向")

lines.append("")
lines.append("**😨 情緒指標**")
fg = fetch_fear_greed()
lines.append(f"  {format_fear_greed(fg)}")

# ── Signal preview: check if any fresh alert-worthy signal ──
lines.append("")
lines.append("**🚨 Signal 速報**")
alert_cutoff = int((now - timedelta(hours=8)).timestamp())
c2 = sqlite3.connect(DB_PATH).cursor()
# Check recent VP_BTC_4h
c2.execute("""
    SELECT price, timestamp FROM signals
    WHERE indicator='VP_BTC_4h_L' AND created_at > ?
    ORDER BY timestamp DESC LIMIT 1
""", (alert_cutoff,))
btc_vp = c2.fetchone()

# Check recent OBV signals in their active conditions
c2.execute("""
    SELECT s.indicator, s.coin, s.price, s.timestamp
    FROM signals s
    JOIN market_state ms ON s.coin=ms.coin AND s.timeframe=ms.timeframe
    WHERE s.created_at > ? AND s.indicator IN ('OBV_SOL_1h','OBV_BTC_1h')
    AND ms.timestamp > ?
    ORDER BY s.timestamp DESC LIMIT 1
""", (alert_cutoff, int((now - timedelta(hours=6)).timestamp())))
obv_sig = c2.fetchone()
c2.close()

if btc_vp:
    price, ts = btc_vp
    ts_str = datetime.fromtimestamp(ts/1000, HKT).strftime("%m/%d %H:%M")
    exit_str = (datetime.fromtimestamp(ts/1000, HKT) + timedelta(hours=24)).strftime("%m/%d %H:%M")
    lines.append(f"  🔔 **VP_BTC_4h trigger！**")
    lines.append(f"     → BTC Long @ ${price:,.0f}")
    lines.append(f"     → 24h exit: {exit_str} HKT")
    lines.append(f"     → 止蝕: 2×ATR (~${price*0.015:,.0f})")
elif obv_sig:
    ind, coin, price, ts = obv_sig
    ts_str = datetime.fromtimestamp(ts/1000, HKT).strftime("%m/%d %H:%M")
    exit_str = (datetime.fromtimestamp(ts/1000, HKT) + timedelta(hours=24)).strftime("%m/%d %H:%M")
    lines.append(f"  ⚡ **{ind} trigger！**")
    lines.append(f"     → {coin} Long @ ${price:,.0f}")
    lines.append(f"     → 24h exit: {exit_str} HKT（唔可以拖！）")
else:
    lines.append("  而家冇 active signal")
    # Show what indicator to watch
    if best_for_today:
        lines.append(f"  💡 留意：{best_for_today[0][2].split('(')[0].strip()}")
    lines.append("  如果 trigger → 跟上面嘅行動指令做")

lines.append("")
lines.append(f"_Next update: {(now + timedelta(hours=24)).strftime('%m/%d %H:%M')}_")

print("\n".join(lines))
