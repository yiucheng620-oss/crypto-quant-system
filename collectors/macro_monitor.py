#!/usr/bin/env python3
"""
Macro Event Monitor V2 — Sentiment-scored + Market Implication
═══════════════════════════════════════════════════════════
V2 2026-05-18: 
  + Sentiment scoring per headline (🔴bearish / 🟢bullish / 🟡neutral)
  + Asset class impact mapping (crypto/stocks/gold)
  + Priority scoring (0-100) for ranking
  + Gold-specific keywords
  + Implication line — 唔只 headline dump

Cron: 每日 07:00 + 19:00 HKT
"""

import requests
import xml.etree.ElementTree as ET
import re
import sys
import json
import os
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.path.expanduser("~/workspace/macro_data")
OUTPUT_FILE = os.path.join(DATA_DIR, "macro_alerts.json")

# ═══════════════════════════════════════════════
# SENTIMENT DICTIONARY
# ═══════════════════════════════════════════════

BEARISH_PATTERNS = [
    r'\b(crash|plunge|tumble|slump|sell.off|meltdown)\b',
    r'\b(recession|stagflation|layoff|bankrupt)\b',
    r'\b(hike|tighten|hawkish|taper)\b',
    r'\b(fear|panic|worry|risk.off)\b',
    r'\b(inflation\s+(surge|jump|spike|soar))\b',
    r'\b(worse\s+than\s+expected|missed\s+estimates)\b',
]

BULLISH_PATTERNS = [
    r'\b(rally|surge|soar|boom|record\s+high)\b',
    r'\b(cut|ease|dovish|stimulus)\b',
    r'\b(beat\s+estimates|better\s+than\s+expected)\b',
    r'\b(breakthrough|approval|adoption)\b',
    r'\b(soft\s+landing|goldilocks)\b',
]

# ═══════════════════════════════════════════════
# ASSET IMPACT MAPPING
# ═══════════════════════════════════════════════

ASSET_IMPACT = {
    "Federal Reserve": ["stocks", "crypto", "gold"],
    "rate hike": ["stocks🔴", "crypto🔴", "gold🟡"],
    "rate cut": ["stocks🟢", "crypto🟢", "gold🟢"],
    "inflation": ["stocks🔴", "crypto🔴", "gold🟢"],
    "CPI": ["stocks", "crypto", "gold"],
    "PPI": ["stocks", "crypto"],
    "recession": ["stocks🔴", "crypto🔴", "gold🟢"],
    "earnings": ["stocks"],
    "IPO": ["stocks", "crypto🔴"],
    "SEC": ["crypto"],
    "Bitcoin ETF": ["crypto🟢"],
    "gold": ["gold🟢", "crypto🔴"],
    "oil": ["stocks🔴", "crypto🔴"],
    "war": ["stocks🔴", "crypto🔴", "gold🟢"],
    "trade war": ["stocks🔴", "crypto🔴"],
    "Treasury yield": ["stocks🔴", "crypto🔴", "gold🔴"],
    "DXY": ["crypto🔴", "gold🔴"],
    "bank failure": ["stocks🔴", "crypto🔴"],
    "sanction": ["crypto", "gold🟢"],
    "AI": ["stocks🟢"],
    "chip": ["stocks"],
    "NVIDIA": ["stocks🟢"],
    "semiconductor": ["stocks"],
    "regulation": ["crypto🔴"],
    "adoption": ["crypto🟢"],
    "halving": ["crypto🟢"],
    "bull market": ["crypto🟢", "stocks🟢"],
    "bear market": ["crypto🔴", "stocks🔴"],
}

# ═══════════════════════════════════════════════
# KEYWORD TRIGGERS
# ═══════════════════════════════════════════════

KEYWORDS = [
    # IPO / Liquidity drain
    ("SpaceX IPO", "🚀", "Liquidity drain → crypto risk", 85),
    ("OpenAI IPO", "🤖", "Mega IPO → liquidity drain", 80),
    ("Anthropic IPO", "🧠", "AI IPO wave", 70),
    ("Cerebras IPO", "💻", "AI chip IPO", 65),
    ("CoreWeave IPO", "☁️", "AI infra IPO", 65),
    
    # Fed / Macro
    ("Federal Reserve rate decision", "🏦", "直接影響所有風險資產", 95),
    ("Fed rate cut", "📉", "減息 → crypto/stocks bullish", 90),
    ("Fed rate hike", "📈", "加息 → risk-off", 90),
    ("quantitative tightening", "💧", "QT → liquidity drain", 80),
    ("quantitative easing", "💉", "QE → liquidity boost", 85),
    ("FOMC minutes", "📋", "政策路徑 insight", 75),
    
    # Inflation / Employment
    ("CPI report", "📊", "通脹 → rate expectation shift", 85),
    ("PPI data", "📊", "生產者通脹 → leading indicator", 80),
    ("nonfarm payroll", "👷", "就業 → Fed policy input", 75),
    ("inflation forecast", "🔮", "Forward inflation expectation", 70),
    
    # Crypto specific
    ("Bitcoin ETF inflow", "📥", "Institutional demand", 80),
    ("Bitcoin ETF outflow", "📤", "Institutional exit", 80),
    ("SEC crypto ruling", "⚖️", "US regulatory action", 85),
    ("crypto regulation", "⚖️", "Global regulatory shift", 75),
    ("Bitcoin halving", "⛏️", "Supply shock", 70),
    ("crypto adoption", "🌍", "Adoption catalyst", 65),
    
    # Gold specific (V2)
    ("gold price record", "🥇", "Gold ATH → risk-off signal", 80),
    ("gold rally", "🥇", "Safe haven demand", 75),
    ("central bank gold buying", "🏦", "CB gold reserve → de-dollarization", 80),
    ("gold forecast", "🔮", "Gold price outlook", 60),
    
    # Geopolitical / Systemic
    ("bank failure", "🏚️", "Systemic risk → flight to safety", 90),
    ("financial crisis", "🚨", "Systemic shock", 95),
    ("market crash", "📉", "Broad sell-off", 90),
    ("Taiwan conflict", "🔥", "Geopolitical → risk-off", 85),
    ("trade war escalate", "🌐", "Trade tension → supply shock", 80),
    ("oil price shock", "🛢️", "Energy → inflation", 75),
    
    # Stock specific
    ("NVIDIA earnings", "💻", "AI bellwether", 75),
    ("semiconductor shortage", "🔧", "Supply chain → chip stocks", 65),
    ("earnings recession", "📉", "Corporate profit decline", 70),
    ("Treasury yield surge", "📈", "Bond sell-off → rate pressure", 75),
    ("DXY surge", "💵", "Strong dollar → commodity/crypto pressure", 70),
    ("credit spread widen", "⚠️", "Credit stress → risk-off", 80),
]


def score_sentiment(title: str) -> tuple:
    """Score sentiment: -1 (bearish), +1 (bullish), 0 (neutral)"""
    title_lower = title.lower()
    bearish_score = 0
    bullish_score = 0
    
    for pat in BEARISH_PATTERNS:
        if re.search(pat, title_lower):
            bearish_score += 1
    
    for pat in BULLISH_PATTERNS:
        if re.search(pat, title_lower):
            bullish_score += 1
    
    if bearish_score > bullish_score:
        return ("🔴 bearish", -1)
    elif bullish_score > bearish_score:
        return ("🟢 bullish", 1)
    else:
        return ("🟡 neutral", 0)


def get_asset_impact(keyword: str, sentiment: int) -> list:
    """Map keyword to affected assets with sentiment direction."""
    impacts = []
    for kw, assets in ASSET_IMPACT.items():
        if kw.lower() in keyword.lower():
            for asset in assets:
                # Parse asset+emoji format
                base_asset = asset.replace("🔴", "").replace("🟢", "").replace("🟡", "")
                direction = "🟡"
                if "🔴" in asset:
                    direction = "🔴"
                elif "🟢" in asset:
                    direction = "🟢"
                # Override direction with headline sentiment if strong
                if sentiment == -1:
                    direction = "🔴"
                elif sentiment == 1:
                    direction = "🟢"
                impacts.append(f"{direction} {base_asset}")
    if not impacts:
        # Default: important macro → affects all
        impacts = ["🟡 stocks", "🟡 crypto"]
    return list(set(impacts))  # dedup


def fetch_news_rss(query: str, limit: int = 5) -> list:
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.text)
        items = []
        for item in root.findall(".//item")[:limit]:
            title = item.find("title")
            pubdate = item.find("pubDate")
            if title is not None and title.text:
                items.append({
                    "title": title.text,
                    "date": pubdate.text[:22] if pubdate is not None else "",
                })
        return items
    except Exception as e:
        return []


def scan_news() -> list:
    alerts = []
    seen = set()
    
    for keyword, emoji, note, priority in sorted(KEYWORDS, key=lambda x: -x[3]):
        news = fetch_news_rss(keyword.replace(" ", "+"), limit=3)
        for n in news:
            key = n["title"][:100]
            if key in seen:
                continue
            seen.add(key)
            
            sentiment_label, sentiment_score = score_sentiment(n["title"])
            asset_impacts = get_asset_impact(keyword, sentiment_score)
            
            alerts.append({
                "priority": priority,
                "emoji": emoji,
                "keyword": keyword,
                "title": n["title"],
                "date": n["date"],
                "note": note,
                "sentiment": sentiment_label,
                "assets": ", ".join(asset_impacts),
            })
    
    # Sort by priority descending
    alerts.sort(key=lambda x: -x["priority"])
    return alerts


def format_alerts(alerts: list) -> str:
    if not alerts:
        return ""
    
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")
    lines = [f"🌍 **Macro Monitor V2** — {now}", ""]
    
    # Group by priority tier
    critical = [a for a in alerts if a["priority"] >= 85]
    high = [a for a in alerts if 70 <= a["priority"] < 85]
    medium = [a for a in alerts if a["priority"] < 70]
    
    if critical:
        lines.append(f"🔴 **HIGH IMPACT ({len(critical)} 件)**")
        for a in critical[:8]:
            lines.append(f"{a['emoji']} **{a['keyword']}** [{a['sentiment']}]")
            lines.append(f"   {a['title'][:140]}")
            lines.append(f"   📅 {a['date']} | 🎯 {a['assets']}")
            lines.append(f"   💡 {a['note']}")
            lines.append("")
    
    if high:
        lines.append(f"🟡 **MEDIUM IMPACT ({len(high)} 件)**")
        for a in high[:5]:
            lines.append(f"{a['emoji']} {a['title'][:120]} [{a['sentiment']}]")
            lines.append(f"   🎯 {a['assets']} | {a['note']}")
            lines.append("")
    
    if medium and len(critical) + len(high) < 5:
        lines.append(f"🟢 **LOW IMPACT**")
        for a in medium[:3]:
            lines.append(f"   {a['emoji']} {a['title'][:100]}")
        lines.append("")
    
    if not critical and not high:
        lines.append("   ✅ 冇重大警報 — 市場 macro backdrop 穩定")
        lines.append("")
    
    next_scan = (datetime.now(HKT) + timedelta(hours=12)).strftime("%H:%M")
    lines.append(f"_Sentiment-scored | 下次掃描: {next_scan}_")
    
    return "\n".join(lines)


def save_alerts(alerts: list):
    """Save alerts to JSON file for downstream consumption."""
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "timestamp": datetime.now(HKT).isoformat(),
        "count": len(alerts),
        "alerts": alerts,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return len(alerts)


if __name__ == "__main__":
    alerts = scan_news()
    count = save_alerts(alerts)
    print(f"[macro_monitor] ✅ Saved {count} macro alerts to {OUTPUT_FILE}", file=sys.stderr)
