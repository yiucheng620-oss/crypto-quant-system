#!/usr/bin/env python3
"""
Gemini Daily Analyst V2 — Unified Market Intelligence
═══════════════════════════════════════════════════════
合併 7 個 data source → Gemini 寫一份全市場分析報告
取代 daily_brief + cross_asset_matrix（已 kill）

Data sources:
  1. Crypto market state (ADX/bull/bear)
  2. Crypto signals (7 indicators)
  3. Signal accuracy (14-day WR)
  4. Fear & Greed
  5. 美股價格 + VIX/債息/黃金 (from market_data)
  6. 跨市場相關性矩陣 (from correlation_matrix)
  7. 美股回調掃描 (from stock_pullback_scanner)
  8. 宏觀背景 (from macro_monitor)

Cron: 每日 09:00 HKT (01:00 UTC)
"""

import os, sys, json, sqlite3, requests, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
PHASE1_DIR = os.path.expanduser("~/market_data/phase1")
MKT_DIR = os.path.expanduser("~/market_data")
VERTEX_URL = "http://localhost:8899/v1/chat/completions"


# ═══════════════════════════════════════════
# 1. CRYPTO DATA (existing, unchanged)
# ═══════════════════════════════════════════

def get_market_state():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int((datetime.now(HKT) - timedelta(hours=6)).timestamp())
    c.execute("""
        SELECT coin, timeframe, condition, adx, volatility_pct
        FROM market_state WHERE timestamp > ?
        ORDER BY timestamp DESC
    """, (cutoff,))
    rows = c.fetchall()
    conn.close()
    seen, states = set(), []
    for coin, tf, cond, adx, vol in rows:
        key = (coin, tf)
        if key not in seen:
            seen.add(key)
            emoji = {"bull":"🟢","range":"🟡","bear":"🔴","volatile":"🟣"}.get(cond,"⚪")
            states.append(f"{emoji} {coin} {tf}: {cond} (ADX={adx}, Vol={vol}%)")
    return states


def get_recent_signals(hours=24):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int((datetime.now(HKT) - timedelta(hours=hours)).timestamp())
    c.execute("""
        SELECT indicator, coin, direction, price, timestamp
        FROM signals WHERE created_at > ?
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int((datetime.now(HKT) - timedelta(days=days)).timestamp())
    c.execute("""
        SELECT s.indicator, a.horizon, a.market_condition,
               COUNT(*) as total,
               SUM(CASE WHEN a.result='WIN' THEN 1 ELSE 0 END) as wins
        FROM accuracy a JOIN signals s ON a.signal_id = s.id
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
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = r.json().get("data", [])
        if data:
            return f"{data[0]['value']}/100 — {data[0]['value_classification']}"
    except:
        pass
    return "N/A"


def get_btc_price():
    try:
        import numpy as np
        d = np.load(os.path.join(PHASE1_DIR, "BTC_1h.npz"))
        return d['close'][-1]
    except:
        return None


# ═══════════════════════════════════════════
# 2. STOCK MARKET DATA (NEW)
# ═══════════════════════════════════════════

def get_stock_summary():
    """Read latest market_data summary JSON."""
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    summary_path = os.path.join(MKT_DIR, "daily", today, "_summary.json")
    if not os.path.exists(summary_path):
        # Try yesterday
        yesterday = (datetime.now(HKT) - timedelta(days=1)).strftime("%Y-%m-%d")
        summary_path = os.path.join(MKT_DIR, "daily", yesterday, "_summary.json")
    if not os.path.exists(summary_path):
        return {}
    with open(summary_path) as f:
        return json.load(f)


def get_correlation_report():
    """Read latest correlation report."""
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    corr_path = os.path.join(MKT_DIR, "daily", today, "_correlation_report.txt")
    if not os.path.exists(corr_path):
        yesterday = (datetime.now(HKT) - timedelta(days=1)).strftime("%Y-%m-%d")
        corr_path = os.path.join(MKT_DIR, "daily", yesterday, "_correlation_report.txt")
    if not os.path.exists(corr_path):
        return ""
    with open(corr_path) as f:
        lines = f.readlines()
    # Extract key correlations and matrix
    key_lines = []
    in_matrix = False
    for line in lines:
        if "關鍵相關性" in line or "相關性異常變動" in line or "完整" in line:
            key_lines.append(line.strip())
        if "🟢" in line or "🔴" in line or "🟡" in line:
            key_lines.append(line.strip())
        if "───" in line and not in_matrix:
            in_matrix = True
            key_lines.append("（完整矩陣見下方）")
    return "\n".join(key_lines[:20])


def run_stock_scanner():
    """Run stock pullback scanner and capture output."""
    import subprocess
    scanner_path = os.path.expanduser("~/workspace/stock_pullback_scanner.py")
    if not os.path.exists(scanner_path):
        return ""
    try:
        result = subprocess.run(
            [sys.executable, scanner_path],
            capture_output=True, text=True, timeout=120,
            cwd=os.path.expanduser("~/workspace"),
        )
        return result.stdout.strip()
    except:
        return ""


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
                lines = [l for l in content.split("\n") if l.strip().startswith("📊")]
                if lines:
                    return "\n".join(lines[:5])
    return ""


# ═══════════════════════════════════════════
# 3. PROMPT BUILDER (UPGRADED)
# ═══════════════════════════════════════════

def build_prompt(market, signals, accuracy, fg, btc_price,
                 stock_summary, correlation, stock_scans, macro):
    """Build comprehensive prompt with all 8 data sources."""
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")

    # Build stock snapshot
    stock_lines = []
    if stock_summary:
        for sym, info in stock_summary.items():
            if "close" in info:
                arrow = "🟢" if info.get("change_pct", 0) > 0 else "🔴"
                stock_lines.append(
                    f"  {arrow} {info['name']:12s} ${info['close']:>10.2f} {info['change_pct']:+.2f}%"
                )
    stock_snapshot = "\n".join(stock_lines[:15]) if stock_lines else "冇美股數據"

    prompt = f"""你係一個專業全市場交易分析師，覆蓋加密貨幣 + 美股 + 宏觀。請根據以下數據，寫一份每日全市場分析（繁體中文）。

**時間**: {now}
**BTC 現價**: ${btc_price:,.0f}（如果 N/A = 冇 data）

## 🩸 Crypto 市場狀態
{chr(10).join(market) if market else '冇近期數據'}

## 📡 Crypto Signal
{chr(10).join(signals) if signals else '冇近期 signal'}

## 📊 Crypto 近期勝率
{chr(10).join(accuracy) if accuracy else '冇足夠 accuracy data'}

## 😱 情緒
Fear & Greed: {fg}

## 🇺🇸 美股快照
{stock_snapshot}

## 🔗 跨市場相關性
{correlation if correlation else '冇相關性數據'}

## 🎯 美股回調掃描
{stock_scans if stock_scans else '冇回調 signal'}

## 🌍 宏觀背景
{macro if macro else '冇近期 macro data'}

---

請用以下格式輸出（**繁體中文**，每個 section 至少要有內容）：

📊 **全市場速報** — {now[:10]}

**🩸 加密貨幣**
（一句總結 + 最主要風險 + 支持/阻力位）

**🇺🇸 美股**
（一句總結 + AI/半導體焦點 + 回調機會）

**🔗 跨市場信號**
（BTC-美股相關性 + 資金流向判斷 + 矛盾/共振）

**📡 今日 Signal**
（最重要嘅 1-3 個 crypto signal + 1-2 個美股 pullback）

**🎯 今日建議**
（crypto + 美股 trading 建議，分開寫，注碼建議）

**🔮 短期關注**
（1-2 個本週 catalyst）

格式規則：
- 用 bullet point，唔好太長
- 數字要有單位（$ / %），唔好省略
- 建議要 actionable，唔好模稜兩可
- 跨市場分析要指出矛盾點（例如 crypto bear 但美股強 = 脫勾風險）
- 唔好講廢話"""

    return prompt


# ═══════════════════════════════════════════
# 4. GEMINI CALL
# ═══════════════════════════════════════════

def call_gemini(prompt, market_state, signal_list):
    """Call Gemini via vertex proxy with fallback."""
    try:
        payload = {
            "model": "gemini-2.5-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你係一個數據驅動嘅全市場交易分析師，同時覆蓋加密貨幣同美股。你嘅分析必須：\n"
                        "1. 引用實際數據（不可憑空推測）\n"
                        "2. 先讀數據後判斷（bull/bear 必須來自數據，不可自創）\n"
                        "3. 跨市場分析要指出矛盾（例如 crypto bear 但美股強）\n"
                        "4. 保持簡潔但完整 — 每個 section 至少 1-2 行實質內容\n"
                        "5. 不可跳過任何 section\n"
                        "6. 美股同 crypto 建議要分開寫\n"
                        "7. 用繁體中文輸出"
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
        r = requests.post(VERTEX_URL, json=payload, timeout=90)
        r.raise_for_status()
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if content:
            return content
        else:
            raise ValueError("Gemini returned null content")
    except Exception as e:
        # Fallback
        btc_price = get_btc_price() or 0
        now = datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')

        fallback = [f"📊 **全市場速報** — {now[:10]}"]
        fallback.append("")
        fallback.append(f"⚠️ Gemini unavailable（{str(e)[:80]}），rule-based fallback：")
        fallback.append("")

        # Crypto
        bear_count = sum(1 for s in market_state if "bear" in s.lower())
        bull_count = sum(1 for s in market_state if "bull" in s.lower())
        fallback.append("**🩸 加密貨幣**")
        if bear_count >= 4:
            fallback.append(f"  🔴 全線 Bear ({bear_count}/6) → Short bias, BTC=${btc_price:,.0f}")
        elif bull_count >= 4:
            fallback.append(f"  🟢 全線 Bull ({bull_count}/6) → Long bias")
        else:
            fallback.append(f"  🟡 Mixed → 觀望")
        fallback.append("")

        if signal_list:
            fallback.append("**📡 Signal**")
            for s in signal_list[:5]:
                fallback.append(f"  {s}")
            fallback.append("")

        if market_state:
            fallback.append("**🔍 市場狀態**")
            for s in market_state[:6]:
                fallback.append(f"  {s}")
            fallback.append("")

        fallback.append(f"BTC: ${btc_price:,.0f}" if btc_price else "BTC: N/A")

        return "\n".join(fallback)


# ═══════════════════════════════════════════
# 5. MAIN
# ═══════════════════════════════════════════

def main():
    now = datetime.now(HKT)

    # Gather ALL data
    market = get_market_state()
    signals = get_recent_signals(24)
    accuracy = get_recent_accuracy(14)
    fg = get_fear_greed()
    btc_price = get_btc_price() or 0
    stock_summary = get_stock_summary()
    correlation = get_correlation_report()
    stock_scans = run_stock_scanner()
    macro = get_macro_context()

    # Build prompt + call Gemini
    prompt = build_prompt(
        market, signals, accuracy, fg, btc_price,
        stock_summary, correlation, stock_scans, macro
    )
    analysis = call_gemini(prompt, market, signals)

    # Footer
    sources = ["crypto_state", "signals", "accuracy", "F&G"]
    if stock_summary:
        sources.append("美股")
    if correlation:
        sources.append("相關性")
    if stock_scans:
        sources.append("pullback")

    footer = (
        f"\n\n---\n"
        f"📡 Data: {', '.join(sources)} + macro\n"
        f"🤖 Analysis: Gemini 2.5 Pro via Vertex AI\n"
        f"🕐 Generated: {now.strftime('%Y-%m-%d %H:%M HKT')}"
    )

    print(analysis + footer)


if __name__ == "__main__":
    main()
