#!/usr/bin/env python3
"""
Gemini Daily Analyst — AI-powered market analysis
═══════════════════════════════════════════════════
合併 crypto signal + market state + macro context + stock scanner
→ 俾 Gemini 寫 actionable 每日分析

Cron: 每日 09:00 HKT (01:00 UTC) — 取代 daily_brief
"""

import os, sys, json, sqlite3, requests, time
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
DATA_DIR = os.path.expanduser("~/market_data/phase1")
VERTEX_URL = "http://localhost:8899/v1/chat/completions"


# ═══════════════════════════════════════════
# DATA GATHERING
# ═══════════════════════════════════════════

def get_market_state():
    """Get latest market state from DB."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int((datetime.now(HKT) - timedelta(hours=6)).timestamp())
    c.execute("""
        SELECT coin, timeframe, condition, adx, volatility_pct
        FROM market_state
        WHERE timestamp > ?
        ORDER BY timestamp DESC
    """, (cutoff,))
    rows = c.fetchall()
    conn.close()

    seen = set()
    states = []
    for coin, tf, cond, adx, vol in rows:
        key = (coin, tf)
        if key not in seen:
            seen.add(key)
            emoji = {"bull": "🟢", "range": "🟡", "bear": "🔴", "volatile": "🟣"}.get(cond, "⚪")
            states.append(f"{emoji} {coin} {tf}: {cond} (ADX={adx}, Vol={vol}%)")
    return states


def get_recent_signals(hours=24):
    """Get signals from last N hours."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int((datetime.now(HKT) - timedelta(hours=hours)).timestamp())
    c.execute("""
        SELECT indicator, coin, direction, price, timestamp
        FROM signals
        WHERE created_at > ?
        ORDER BY timestamp DESC LIMIT 15
    """, (cutoff,))
    rows = c.fetchall()
    conn.close()

    signals = []
    for ind, coin, dir_, price, ts in rows:
        ts_str = datetime.fromtimestamp(ts/1000, HKT).strftime("%m/%d %H:%M") if ts > 1e10 else ""
        dir_emoji = "🔻" if dir_ == "SHORT" else "🔺"
        signals.append(f"{dir_emoji} {ind} {coin} @ ${price:,.0f} ({ts_str})")
    return signals


def get_recent_accuracy(days=14):
    """Get accuracy stats from last N days."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int((datetime.now(HKT) - timedelta(days=days)).timestamp())
    c.execute("""
        SELECT s.indicator, a.horizon, a.market_condition,
               COUNT(*) as total,
               SUM(CASE WHEN a.result='WIN' THEN 1 ELSE 0 END) as wins
        FROM accuracy a
        JOIN signals s ON a.signal_id = s.id
        WHERE a.checked_at > ?
        GROUP BY s.indicator, a.horizon, a.market_condition
        HAVING total >= 2
    """, (cutoff,))
    rows = c.fetchall()
    conn.close()

    acc = []
    for ind, hor, mc, total, wins in rows:
        wr = round(wins/total*100, 1)
        acc.append(f"  {ind} {hor} {mc}: {wr}% WR ({total} checks)")
    return acc


def get_fear_greed():
    """Fetch Fear & Greed."""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = r.json().get("data", [])
        if data:
            return f"{data[0]['value']}/100 — {data[0]['value_classification']}"
    except:
        pass
    return "N/A"


def get_macro_context():
    """Get latest macro monitor output."""
    macro_dir = os.path.expanduser("~/.hermes/cron/output/406f2275189e/")
    if not os.path.exists(macro_dir):
        return ""
    files = sorted(os.listdir(macro_dir), reverse=True)
    for f in files:
        if f.endswith(".md"):
            with open(os.path.join(macro_dir, f)) as fh:
                content = fh.read()
                # Extract non-header lines
                lines = [l for l in content.split("\n") if l.startswith("  ") and "🚀" in l or "🏦" in l or "📊" in l or "⚖️" in l]
                if lines:
                    return "\n".join(lines[:8])
    return ""


def get_btc_price():
    """Get current BTC price."""
    try:
        import numpy as np
        d = np.load(os.path.join(DATA_DIR, "BTC_1h.npz"))
        return d['close'][-1]
    except:
        return None


# ═══════════════════════════════════════════
# GEMINI PROMPT
# ═══════════════════════════════════════════

def build_prompt(market, signals, accuracy, fg, macro, btc_price):
    """Build structured prompt for Gemini."""
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")

    prompt = f"""你係一個專業加密貨幣交易分析師。請根據以下數據，寫一份簡潔嘅每日市場分析（繁體中文）。

**時間**: {now}
**BTC 現價**: ${btc_price:,.0f}（如果 N/A 代表冇數據）

## 市場狀態
{chr(10).join(market) if market else '冇近期數據'}

## 最近 Signal
{chr(10).join(signals) if signals else '冇近期 signal'}

## 近期勝率
{chr(10).join(accuracy) if accuracy else '冇足夠 accuracy data'}

## 情緒指標
Fear & Greed: {fg}

## 宏觀背景
{macro if macro else '冇近期 macro data'}

---

請用以下格式輸出（**繁體中文**，保持簡潔，唔好太長）：

📊 **每日市場速報** — {now[:10]}

**🩸 市況**
（一句總結市場狀態 + 最主要風險）

**📡 Signal**
（最重要嘅 1-3 個 signal，附價格）

**🎯 今日建議**
（crypto trading 建議：Long/Short/觀望 + 原因 + 注碼建議）

**🔮 短期關注**
（1-2 個本週要留意嘅 catalyst）

格式規則：
- 用 bullet point，唔好太長
- 數字要有單位（$ / %）
- 建議要 actionable，唔好模稜兩可
- 唔好講廢話"""

    return prompt


def call_gemini(prompt, market_state, signal_list):
    """Call Gemini via vertex proxy with fallback."""
    try:
        payload = {
            "model": "gemini-2.5-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你係一個數據驅動嘅加密貨幣交易分析師。你嘅分析必須：\n"
                        "1. 引用實際數據（不可憑空推測）\n"
                        "2. 先讀數據後判斷（bull/bear/mixed 必須來自市場狀態數據，不可自創）\n"
                        "3. 如果數據顯示全線 bear，就寫 bear；不可扭曲數據\n"
                        "4. 保持簡潔但完整 — 每個 section 至少要有 1-2 行實質內容\n"
                        "5. 不可跳過任何 section\n"
                        "6. 用繁體中文輸出"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 4096,
            "extra_body": {
                "thinking_config": {
                    "thinking_budget": 512,
                }
            },
        }
        r = requests.post(VERTEX_URL, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if content:
            return content
        else:
            raise ValueError("Gemini returned null content")
    except Exception as e:
        # Fallback: build analysis without Gemini
        btc_price = get_btc_price() or 0
        now = datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')

        fallback = [f"📊 **每日市場速報** — {now[:10]}"]
        fallback.append("")
        fallback.append(f"⚠️ Gemini 暫時 unavailable（{str(e)[:80]}），以下係 rule-based 分析：")
        fallback.append("")

        # Market summary
        bear_count = sum(1 for s in market_state if "bear" in s.lower())
        bull_count = sum(1 for s in market_state if "bull" in s.lower())
        if bear_count >= 4:
            fallback.append("**🩸 市況：全線 Bear**")
            fallback.append("  BTC/ETH/SOL 多時間框架都係 bear market → Short bias")
        elif bull_count >= 4:
            fallback.append("**🟢 市況：全線 Bull**")
            fallback.append("  多時間框架 bullish → Long 可進取")
        else:
            fallback.append("**🟡 市況：Mixed**")
            fallback.append("  信號混亂 → 觀望為主，等方向確認")

        fallback.append("")

        # Signals
        if signal_list:
            fallback.append("**📡 最近 Signal**")
            for s in signal_list[:5]:
                fallback.append(f"  {s}")
        else:
            fallback.append("**📡 最近 Signal**: 冇")

        fallback.append("")

        # Market states
        if market_state:
            fallback.append("**🔍 市場狀態**")
            for s in market_state[:6]:
                fallback.append(f"  {s}")

        fallback.append("")
        fallback.append(f"BTC: ${btc_price:,.0f}" if btc_price else "BTC: N/A")

        return "\n".join(fallback)


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

def main():
    now = datetime.now(HKT)

    # Gather all data
    market = get_market_state()
    signals = get_recent_signals(24)
    accuracy = get_recent_accuracy(14)
    fg = get_fear_greed()
    macro = get_macro_context()
    btc_price = get_btc_price() or 0

    # Build prompt + call Gemini
    prompt = build_prompt(market, signals, accuracy, fg, macro, btc_price)
    analysis = call_gemini(prompt, market, signals)

    # Add data freshness note
    freshness = f"\n\n---\n📡 Data: market_state + signals + macro context\n🤖 Analysis: Gemini 2.5 Pro via Vertex AI\n🕐 Generated: {now.strftime('%Y-%m-%d %H:%M HKT')}"

    print(analysis + freshness)


if __name__ == "__main__":
    main()
