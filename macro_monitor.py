#!/usr/bin/env python3
"""
Macro Event Monitor — 掃描影響 Crypto 嘅重大事件
═══════════════════════════════════════════════════════
每日 scan Google News RSS，檢查：
  1. 大型 IPO（SpaceX、OpenAI 等 → liquidity drain）
  2. 聯儲局政策（利率、QT/QE）
  3. 監管新聞（SEC、CFTC crypto rulings）
  4. 宏觀事件（戰爭、制裁、銀行危機）

觸發條件 → Telegram alert
Cron: 每日 07:00 + 19:00 HKT
"""

import requests
import xml.etree.ElementTree as ET
import re
import sys
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════
# KEYWORD TRIGGERS（任何 mention 都要 alert）
# ═══════════════════════════════════════════════

CRITICAL_KEYWORDS = [
    # IPO / Liquidity drain
    ("SpaceX IPO", "🚀", "liquidity drain → crypto 風險"),
    ("SpaceX 上市", "🚀", "liquidity drain → crypto 風險"),
    ("SpaceX stock split", "💰", "pre-IPO signal"),
    ("OpenAI IPO", "🤖", "另一個 mega IPO"),
    ("Anthropic IPO", "🧠", "AI IPO wave"),
    ("Cerebras IPO", "💻", "AI晶片IPO已完成"),
    ("CoreWeave IPO", "☁️", "AI基建IPO"),

    # Fed / Macro
    ("Federal Reserve rate", "🏦", "利率決策 → 直接影響所有風險資產"),
    ("Fed rate cut", "📉", "減息 → crypto bullish"),
    ("Fed rate hike", "📈", "加息 → crypto bearish"),
    ("quantitative tightening", "💧", "QT → liquidity drain"),
    ("quantitative easing", "💉", "QE → liquidity injection"),

    # Crypto regulation
    ("SEC crypto", "⚖️", "美國監管"),
    ("SEC bitcoin", "⚖️", "BTC 監管"),
    ("CFTC crypto", "⚖️", "期貨監管"),
    ("crypto ban", "🚫", "加密貨幣禁令"),
    ("bitcoin ETF", "📊", "BTC ETF 消息"),

    # Systemic risk
    ("bank failure", "🏚️", "銀行倒閉 → systemic risk"),
    ("financial crisis", "🚨", "金融危機"),
    ("market crash", "📉", "市場崩盤"),
    ("recession", "📉", "經濟衰退"),

    # Geopolitical
    ("Taiwan conflict", "🔥", "地緣政治 → risk-off"),
    ("oil shock", "🛢️", "能源危機"),
    ("trade war", "🌐", "貿易戰"),
]

IMPORTANT_KEYWORDS = [
    ("CPI", "📊", "通脹數據"),
    ("PPI", "📊", "生產者價格"),
    ("nonfarm payroll", "👷", "就業數據"),
    ("Treasury yield", "📈", "債息走勢"),
    ("DXY", "💵", "美元指數"),
    ("gold price surge", "🥇", "避險情緒"),
    ("VIX spike", "😱", "恐慌指數"),
]


def fetch_news_rss(query: str, limit: int = 15) -> list:
    """Fetch Google News RSS."""
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.text)
        items = []
        for item in root.findall(".//item")[:limit]:
            title = item.find("title")
            pubdate = item.find("pubDate")
            link = item.find("link")
            if title is not None and title.text:
                items.append({
                    "title": title.text,
                    "date": pubdate.text if pubdate is not None else "",
                    "link": link.text if link is not None else "",
                })
        return items
    except Exception as e:
        print(f"[ERROR] fetch_news_rss({query}): {e}", file=sys.stderr)
        return []


def scan_news() -> list:
    """Scan critical keywords against news, return alerts."""
    alerts = []
    seen = set()

    # Check critical keywords
    for keyword, emoji, note in CRITICAL_KEYWORDS:
        news = fetch_news_rss(keyword.replace(" ", "+"), limit=5)
        for n in news:
            key = n["title"][:80]
            if key in seen:
                continue
            seen.add(key)
            alerts.append({
                "level": "🔴 CRITICAL",
                "emoji": emoji,
                "keyword": keyword,
                "title": n["title"],
                "date": n["date"][:22],
                "note": note,
                "link": n["link"],
            })

    # Check important keywords (fewer results)
    for keyword, emoji, note in IMPORTANT_KEYWORDS:
        news = fetch_news_rss(keyword.replace(" ", "+"), limit=3)
        for n in news:
            key = n["title"][:80]
            if key in seen:
                continue
            seen.add(key)
            alerts.append({
                "level": "🟡 IMPORTANT",
                "emoji": emoji,
                "keyword": keyword,
                "title": n["title"],
                "date": n["date"][:22],
                "note": note,
                "link": n["link"],
            })

    return alerts


def format_alerts(alerts: list) -> str:
    """Format alerts for Telegram."""
    if not alerts:
        return ""

    critical = [a for a in alerts if "CRITICAL" in a["level"]]
    important = [a for a in alerts if "IMPORTANT" in a["level"]]

    lines = []
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")
    lines.append(f"🌍 **Macro Monitor** — {now}")
    lines.append("")

    if critical:
        lines.append(f"🔴 **CRITICAL ({len(critical)} 件)**")
        lines.append("")
        for a in critical[:10]:
            lines.append(f"{a['emoji']} **{a['keyword']}**")
            lines.append(f"   {a['title'][:150]}")
            lines.append(f"   📅 {a['date']} | {a['note']}")
            lines.append("")

    if important:
        lines.append(f"🟡 **IMPORTANT ({len(important)} 件)**")
        lines.append("")
        for a in important[:5]:
            lines.append(f"{a['emoji']} {a['title'][:130]}")
            lines.append(f"   {a['note']}")
            lines.append("")

    if not critical and not important:
        lines.append("   ✅ 冇重大警報")
        lines.append("")

    lines.append(f"_Next scan: {(datetime.now(HKT) + timedelta(hours=12)).strftime('%H:%M')}_")

    return "\n".join(lines)


if __name__ == "__main__":
    alerts = scan_news()
    output = format_alerts(alerts)
    if output.strip():
        print(output)
