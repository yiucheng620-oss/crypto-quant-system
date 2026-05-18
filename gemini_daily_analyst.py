#!/usr/bin/env python3
"""
Gemini Daily Analyst V2.1 — Unified Market Intelligence
═══════════════════════════════════════════════════════
V2.1 改善:
  1. BTC/ETH/SOL 價格統一用 market_data（唔再用 phase1 NPZ）
  2. 反 hallucination：禁止憑空創數字，所有數字必須有 source
  3. Stock scanner diff：只喺有變化時先寫入 prompt
  4. 短期關注要求具體 catalyst（日期 + 事件）

Cron: 每日 09:00 HKT (01:00 UTC)
"""

import os, sys, json, sqlite3, requests, time
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
MKT_DIR = os.path.expanduser("~/market_data")
VERTEX_URL = "http://localhost:8899/v1/chat/completions"


# ═══════════════════════════════════════════
# 1. CRYPTO DATA
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


def get_crypto_prices():
    """Get BTC/ETH/SOL prices from market_data (SAME source as stock data)."""
    summary = get_stock_summary()
    prices = {}
    mapping = {"BTC-USD": "BTC", "ETH-USD": "ETH", "SOL-USD": "SOL"}
    for key, label in mapping.items():
        info = summary.get(key, {})
        if "close" in info:
            prices[label] = {"price": info["close"], "change_pct": info["change_pct"]}
    return prices


# ═══════════════════════════════════════════
# 2. STOCK MARKET DATA
# ═══════════════════════════════════════════

def get_stock_summary():
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    summary_path = os.path.join(MKT_DIR, "daily", today, "_summary.json")
    if not os.path.exists(summary_path):
        yesterday = (datetime.now(HKT) - timedelta(days=1)).strftime("%Y-%m-%d")
        summary_path = os.path.join(MKT_DIR, "daily", yesterday, "_summary.json")
    if not os.path.exists(summary_path):
        return {}
    with open(summary_path) as f:
        return json.load(f)


def get_correlation_report():
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    corr_path = os.path.join(MKT_DIR, "daily", today, "_correlation_report.txt")
    if not os.path.exists(corr_path):
        yesterday = (datetime.now(HKT) - timedelta(days=1)).strftime("%Y-%m-%d")
        corr_path = os.path.join(MKT_DIR, "daily", yesterday, "_correlation_report.txt")
    if not os.path.exists(corr_path):
        return ""
    with open(corr_path) as f:
        lines = f.readlines()
    key_lines = []
    for line in lines:
        if "關鍵相關性" in line or "相關性異常變動" in line or "完整" in line:
            key_lines.append(line.strip())
        if "🟢" in line or "🔴" in line or "🟡" in line:
            key_lines.append(line.strip())
        if "───" in line:
            key_lines.append("（完整矩陣見下方）")
    return "\n".join(key_lines[:20])


def run_stock_scanner():
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


def get_stock_scanner_diff():
    """Compare today's scanner output with yesterday's. Return None if no meaningful change."""
    today_output = run_stock_scanner()
    if not today_output:
        return None

    yesterday = (datetime.now(HKT) - timedelta(days=1)).strftime("%Y-%m-%d")
    cache_path = os.path.join(MKT_DIR, "daily", yesterday, "_stock_scanner_output.txt")
    if not os.path.exists(cache_path):
        # No yesterday data → first run, include it
        return today_output

    with open(cache_path) as f:
        yesterday_output = f.read().strip()

    # Extract stock symbols from each output
    import re
    def extract_symbols(text):
        return set(re.findall(r'\*\*([A-Z]+)\*\*', text))

    today_syms = extract_symbols(today_output)
    yesterday_syms = extract_symbols(yesterday_output)

    if today_syms == yesterday_syms:
        return None  # No change

    return today_output


def save_stock_scanner_output(output):
    """Save today's scanner output for tomorrow's diff."""
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    daily_dir = os.path.join(MKT_DIR, "daily", today)
    os.makedirs(daily_dir, exist_ok=True)
    cache_path = os.path.join(daily_dir, "_stock_scanner_output.txt")
    with open(cache_path, "w") as f:
        f.write(output)


def get_macro_context():
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
# 3. PROMPT BUILDER
# ═══════════════════════════════════════════

def build_prompt(market, signals, accuracy, fg, crypto_prices,
                 stock_summary, correlation, stock_scans, macro):
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")

    # Crypto price line (from market_data, same source as stocks)
    btc = crypto_prices.get("BTC", {})
    eth = crypto_prices.get("ETH", {})
    sol = crypto_prices.get("SOL", {})
    crypto_price_line = (
        f"BTC ${btc.get('price', 'N/A'):,.0f} ({btc.get('change_pct', 0):+.2f}%)  "
        f"ETH ${eth.get('price', 'N/A'):,.0f} ({eth.get('change_pct', 0):+.2f}%)  "
        f"SOL ${sol.get('price', 'N/A'):,.0f} ({sol.get('change_pct', 0):+.2f}%)"
    )

    # Build stock snapshot
    stock_lines = []
    if stock_summary:
        for sym, info in stock_summary.items():
            if "close" in info and not sym.endswith("-USD"):  # Exclude crypto (shown above)
                arrow = "🟢" if info.get("change_pct", 0) > 0 else "🔴"
                stock_lines.append(
                    f"  {arrow} {info['name']:12s} ${info['close']:>10.2f} {info['change_pct']:+.2f}%"
                )
    stock_snapshot = "\n".join(stock_lines[:12]) if stock_lines else "冇美股數據"

    # Scanner: only show if there's a diff (get_stock_scanner_diff already filters)
    scanner_section = f"## 🎯 美股回調掃描（⚠️ 僅列出變化）\n{stock_scans}" if stock_scans else ""

    prompt = f"""你係一個專業全市場交易分析師，覆蓋加密貨幣 + 美股 + 宏觀。請根據以下數據，寫一份每日全市場分析（繁體中文）。

**時間**: {now}
**📡 Crypto 現價**（來源：market_data，同美股同一數據源）
  {crypto_price_line}

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

{scanner_section}

## 🌍 宏觀背景
{macro if macro else '冇近期 macro data'}

---

請用以下格式輸出（**繁體中文**，每個 section 至少要有內容）：

📊 **全市場速報** — {now[:10]}

**🩸 加密貨幣**
（一句總結 + 最主要風險 + 支持/阻力位）
⚠️ BTC/ETH/SOL 價格必須用上面「Crypto 現價」嘅數字，唔好用其他 source

**🇺🇸 美股**
（一句總結 + AI/半導體焦點 + 回調機會）

**🔗 跨市場信號**
（BTC-美股相關性 + 資金流向判斷 + 矛盾/共振）

**📡 今日 Signal**
（最重要嘅 1-3 個 crypto signal + 美股 pullback（如有））

**🎯 今日建議**
（crypto + 美股 trading 建議，分開寫，注碼建議）

**🔮 短期關注**
（1-2 個本週具體 catalyst，必須包含日期/事件名稱，例如「5/20 聯儲局 Waller 講話」而非「留意聯儲局講話」）

**嚴格規則：**
- 所有數字必須直接來自上面提供嘅數據，不可憑空創造任何數字（包括 PPI、CPI、利率等，除非上面宏觀背景有寫）
- 如果某個數字唔喺數據入面，就唔好寫出嚟
- 用 bullet point，唔好太長
- 建議要 actionable，唔好模稜兩可
- 唔好講廢話"""

    return prompt


# ═══════════════════════════════════════════
# 4. GEMINI CALL
# ═══════════════════════════════════════════

def call_gemini(prompt, market_state, signal_list):
    try:
        payload = {
            "model": "gemini-2.5-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你係一個數據驅動嘅全市場交易分析師，同時覆蓋加密貨幣同美股。你嘅分析必須：\n"
                        "1. 引用實際數據（不可憑空推測任何數字）\n"
                        "2. 先讀數據後判斷（bull/bear 必須來自數據，不可自創）\n"
                        "3. 跨市場分析要指出矛盾（例如 crypto bear 但美股強）\n"
                        "4. 保持簡潔但完整 — 每個 section 至少 1-2 行實質內容\n"
                        "5. 不可跳過任何 section\n"
                        "6. 美股同 crypto 建議要分開寫\n"
                        "7. BTC/ETH/SOL 價格必須用 prompt 入面「Crypto 現價」嘅數字\n"
                        "8. 絕不憑空創造數字 — 如果 prompt 冇提供某個數字（例如 PPI），就唔好寫\n"
                        "9. 短期關注必須具體（日期+事件），唔可以寫通用廢話\n"
                        "10. 用繁體中文輸出"
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
        btc = get_crypto_prices().get("BTC", {}).get("price", 0)
        now = datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')

        fallback = [f"📊 **全市場速報** — {now[:10]}"]
        fallback.append("")
        fallback.append(f"⚠️ Gemini unavailable（{str(e)[:80]}），rule-based fallback：")
        fallback.append("")

        bear_count = sum(1 for s in market_state if "bear" in s.lower())
        bull_count = sum(1 for s in market_state if "bull" in s.lower())
        fallback.append("**🩸 加密貨幣**")
        if bear_count >= 4:
            fallback.append(f"  🔴 全線 Bear ({bear_count}/6) → Short bias, BTC=${btc:,.0f}")
        elif bull_count >= 4:
            fallback.append(f"  🟢 全線 Bull ({bull_count}/6) → Long bias")
        else:
            fallback.append("  🟡 Mixed → 觀望")
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

        fallback.append(f"BTC: ${btc:,.0f}" if btc else "BTC: N/A")
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
    crypto_prices = get_crypto_prices()
    btc_price = crypto_prices.get("BTC", {}).get("price", 0)
    stock_summary = get_stock_summary()
    correlation = get_correlation_report()
    stock_scans = get_stock_scanner_diff()  # Only if changed vs yesterday
    macro = get_macro_context()

    # Save scanner output for tomorrow's diff
    if stock_scans:
        save_stock_scanner_output(stock_scans)
    else:
        # Still save today's raw output for diff purposes
        raw_scans = run_stock_scanner()
        if raw_scans:
            save_stock_scanner_output(raw_scans)

    # Build prompt + call Gemini
    prompt = build_prompt(
        market, signals, accuracy, fg, crypto_prices,
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
