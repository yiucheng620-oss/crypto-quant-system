#!/usr/bin/env python3
"""
📰 NEWS-DRIVEN STOCK DISCOVERY ENGINE
=====================================
唔係新聞總結。係用新聞發掘有潛力嘅股票。

流程:
  1. 多源 RSS 採集（CNBC/MarketWatch/Reuters/Bloomberg/SeekingAlpha/TechCrunch）
  2. Gemini 2.5 Pro 分析 → 提取 ticker + catalyst + sector
  3. 對照 equities screener universe（100隻 S&P 100+ 流動股）
  4. 輸出：news-driven picks + screener cross-match + confidence score
  5. TG 推送 + 儲存 JSON（俾 morning briefing 用）

Cron: 建議 07:30 HKT（喺 equities screener 之後，morning briefing 之前）
"""

import json
import os
import re
import sys
import tempfile
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Optional

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENGINES_DIR = os.path.join(WORKSPACE, "scalp_engines")
EQUITIES_DIR = os.path.join(WORKSPACE, "equities_data")
OUTPUT_DIR = os.path.join(WORKSPACE, "reviews")
os.makedirs(EQUITIES_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

sys.path.insert(0, ENGINES_DIR)
try:
    from tg_notify import tg_send
except ImportError:
    tg_send = lambda *a, **kw: print("[TG] tg_notify unavailable")

# Gemini base — all calls go through gemini_free or direct API

# ══════════════════════════════════════════════════════════════
# HIGH-QUALITY RSS SOURCES
# ══════════════════════════════════════════════════════════════

RSS_SOURCES = {
    "CNBC": {
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "type": "direct",
    },
    "MarketWatch": {
        "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "type": "direct",
    },
    "Reuters": {
        "url": "https://news.google.com/rss/search?q=site:reuters.com+business+markets&hl=en-US&gl=US&ceid=US:en",
        "type": "gnews",
    },
    "Bloomberg": {
        "url": "https://news.google.com/rss/search?q=site:bloomberg.com+markets+technology+stocks&hl=en-US&gl=US&ceid=US:en",
        "type": "gnews",
    },
    "SeekingAlpha": {
        "url": "https://news.google.com/rss/search?q=site:seekingalpha.com+stock+earnings+analysis&hl=en-US&gl=US&ceid=US:en",
        "type": "gnews",
    },
    "TechCrunch": {
        "url": "https://techcrunch.com/feed/",
        "type": "direct",
    },
}

# ══════════════════════════════════════════════════════════════
# EQUITIES UNIVERSE (must match equities_engine.py)
# ══════════════════════════════════════════════════════════════

UNIVERSE = {
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA",
    "AMD","AVGO","INTC","QCOM","TXN","MU","AMAT","LRCX","ADI","MRVL","ARM","DELL","SMCI",
    "CRM","ADBE","NOW","SNOW","ORCL","INTU","PLTR","NET","DDOG","CRWD","ZS",
    "NFLX","SPOT","SNAP","PINS","RBLX","UBER","ABNB","DASH",
    "JPM","BAC","WFC","GS","MS","V","MA","AXP","BLK","SCHW",
    "LLY","UNH","JNJ","PFE","ABBV","MRK","TMO","ISRG","VRTX",
    "COST","HD","WMT","NKE","SBUX","LULU","CMG",
    "BA","CAT","GE","RTX","LMT","XOM","CVX","COP",
    "RIVN","LCID","DIS","PYPL","COIN","HOOD",
}

# ══════════════════════════════════════════════════════════════
# RSS FETCHING
# ══════════════════════════════════════════════════════════════

def fetch_rss(url: str, source_type: str = "direct") -> list[dict]:
    """Fetch RSS feed, return list of {source, title, pubDate}."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_data = resp.read().decode("utf-8", errors="replace")

        # Only strip Google News namespace prefixes (e.g., <dc:creator>)
        # Don't touch direct RSS feeds (CNBC, MarketWatch, TechCrunch)
        if source_type == "gnews":
            raw_data = re.sub(r'<(/?)[a-z]+:', r'<\1', raw_data)

        root = ET.fromstring(raw_data)
        items = root.findall(".//item")[:10]
        headlines = []
        for item in items:
            title = item.find("title")
            pubdate = item.find("pubDate")
            source_el = item.find("source")
            title_text = title.text.strip() if title is not None and title.text else "?"
            pubdate_text = pubdate.text.strip() if pubdate is not None and pubdate.text else "?"
            source_text = source_el.text.strip() if source_el is not None and source_el.text else ""

            # Skip generic/navigation items
            skip_words = ["Markets Wrap", "Business News", "Stock Markets", "Finance",
                         "Breaking & World News", "Bloomberg - "]
            if any(title_text.startswith(w) for w in skip_words):
                continue

            headlines.append({
                "source": source_text or source_type,
                "title": title_text,
                "date": pubdate_text,
            })
        return headlines
    except Exception as e:
        print(f"  ⚠️ RSS fetch failed: {e}")
        return []


def collect_all_headlines() -> list[dict]:
    """Collect headlines from all sources, deduplicate by title similarity."""
    all_headlines = []
    for name, cfg in RSS_SOURCES.items():
        print(f"  [{name}] fetching...")
        headlines = fetch_rss(cfg["url"], cfg["type"])
        print(f"    → {len(headlines)} headlines")
        
        for h in headlines:
            h["source_name"] = name
        all_headlines.extend(headlines)

    # Dedup: skip titles that are very similar to existing ones
    deduped = []
    seen_titles = set()
    for h in all_headlines:
        # Normalize for dedup: first 60 chars, lowercase, strip punctuation
        norm = re.sub(r'[^a-z0-9]', '', h["title"].lower())[:60]
        if norm not in seen_titles:
            seen_titles.add(norm)
            deduped.append(h)

    print(f"  Total: {len(all_headlines)} raw → {len(deduped)} deduped")
    return deduped


# ══════════════════════════════════════════════════════════════
# GEMINI ANALYSIS (Free Tier — Gemini 3.5 Flash + Google Search Grounding)
# ══════════════════════════════════════════════════════════════

try:
    from gemini_free import call_flash_search
    USE_FREE_TIER = True
except ImportError:
    USE_FREE_TIER = False
    print("[daily_news] gemini_free module not found, using Vertex Proxy fallback")

def ask_gemini_search(prompt: str, max_tokens: int = 8192) -> str:
    """Send to Gemini 3.5 Flash WITH Google Search Grounding → auto-search news."""
    if USE_FREE_TIER:
        try:
            result = call_flash_search(prompt, max_tokens=max_tokens, temperature=0.7)
            text = result.get("text", "") if isinstance(result, dict) else result
            sources = result.get("sources", []) if isinstance(result, dict) else []

            if text and not text.startswith("[ALL FAILED"):
                # Append sources
                if sources:
                    text += "\n\n📎 Sources:\n"
                    for s in sources[:5]:
                        text += f"- {s['title'][:80]}: {s['uri'][:100]}\n"
                return text
        except Exception as e:
            print(f"[daily_news] Free tier error: {e}, falling back to Vertex...")

    # No proxy — return error if free tier fails
    return f"[Gemini API error: free tier unavailable — no proxy fallback]"


def build_search_prompt() -> str:
    """Build prompt for Gemini with Google Search — let it search news directly."""
    return """You are a professional US equities analyst. Use Google Search to find the LATEST financial news and stock-moving events happening TODAY.

Search for and analyze:
1. Top 3-5 stock-moving news catalysts today (earnings, upgrades, product launches, M&A, macro)
2. Sector trends (which sectors are hot/cold)
3. Key risk events to watch

For each stock mentioned:
- TICKER | direction (LONG/SHORT) | catalyst | confidence (HIGH/MED/LOW) | why

Output format (Traditional Chinese, be concise):

## 🔥 新聞驅動推薦
TICKER | 方向 | catalyst | 信心 | 原因

## ⚠️ 風險警示
- 今日要避開嘅板塊/事件

## 📊 市場情緒
- 整體 risk-on/off 判斷
- 最強/最弱板塊

Focus on actionable trading ideas. Use tickers from S&P 100 (AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, etc)."""


def load_screener_picks() -> list[dict]:
    """Load latest equities screener output."""
    screener_path = os.path.join(EQUITIES_DIR, "equities_setups.json")
    if not os.path.exists(screener_path):
        return []
    try:
        with open(screener_path) as f:
            data = json.load(f)
        setups = data.get("setups", [])
        return setups[:10]  # top 10
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # fallback: return empty list if screener file is missing or corrupt
        return []


def load_all_screener() -> dict:
    """Load ALL screener setups as {ticker: setup} map."""
    screener_path = os.path.join(EQUITIES_DIR, "equities_setups.json")
    if not os.path.exists(screener_path):
        return {}
    try:
        with open(screener_path) as f:
            data = json.load(f)
        return {s["ticker"]: s for s in data.get("setups", []) if "ticker" in s}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # fallback: return empty dict if screener file is missing or corrupt
        return {}


def fetch_quick_prices(tickers: list[str]) -> dict:
    """Quick yfinance fetch for tickers not in screener. Returns {ticker: {price, atr, trend}}."""
    if not tickers:
        return {}
    try:
        import yfinance as yf
    except ImportError:
        print("  ⚠️ yfinance not available, skipping price fetch")
        return {}

    results = {}
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            hist = tk.history(period="20d")
            if len(hist) < 5:
                continue
            closes = hist["Close"].values
            highs = hist["High"].values
            lows = hist["Low"].values
            price = float(closes[-1])
            # Simple ATR (14-day)
            tr = []
            for i in range(1, len(hist)):
                h, l, pc = float(highs[i]), float(lows[i]), float(closes[i-1])
                tr.append(max(h-l, abs(h-pc), abs(l-pc)))
            atr = sum(tr[-14:])/len(tr[-14:]) if len(tr) >= 14 else (sum(tr)/len(tr) if tr else price*0.02)
            atr_pct = atr / price if price > 0 else 0.02
            # Trend: compare 5-day MA vs 20-day MA
            ma5 = float(closes[-5:].mean()) if len(closes) >= 5 else price
            ma20 = float(closes.mean())
            trend = "📈 上升" if ma5 > ma20 else "📉 下降"
            results[t] = {
                "price": round(price, 2),
                "atr": round(atr, 2),
                "atr_pct": round(atr_pct, 4),
                "trend": trend,
            }
        except Exception as e:
            print(f"  ⚠️ yfinance fetch failed for {t}: {e}")
    return results


def build_trade_setups(news_tickers: list[str], screener_map: dict) -> str:
    """Build trading setup appendix for news picks. Combines screener data + yfinance."""
    if not news_tickers:
        return ""

    # Fetch prices for tickers not in screener
    need_fetch = [t for t in news_tickers if t not in screener_map]
    quick_prices = {}
    if need_fetch:
        print(f"  Fetching prices for {len(need_fetch)} new tickers: {', '.join(need_fetch)}")
        quick_prices = fetch_quick_prices(need_fetch)

    lines = ["\n## 📐 Trading Setups", ""]
    lines.append("| Ticker | 方向 | Entry | SL | TP | RR | ATR | Trend | 來源 |")
    lines.append("|:--|:--|--:|--:|--:|--:|--:|:--|:--|")

    for ticker in news_tickers:
        if ticker in screener_map:
            s = screener_map[ticker]
            entry = s.get("entry", s.get("price", 0))
            stop = s.get("stop", 0)
            target = s.get("target", 0)
            rr = s.get("rr", 0)
            atr = s.get("atr", 0)
            trend = s.get("trend", "?")
            direction = s.get("direction", "LONG")
            source = "Screener"
        elif ticker in quick_prices:
            q = quick_prices[ticker]
            price = q["price"]
            atr = q["atr"]
            atr_pct = q["atr_pct"]
            trend = q["trend"]
            # Default: LONG with 1.5x ATR SL, 3x ATR TP
            entry = price
            stop = round(price * (1 - atr_pct * 1.5), 2)
            target = round(price * (1 + atr_pct * 3), 2)
            rr = round((target - entry) / (entry - stop), 1) if (entry - stop) > 0 else 0
            direction = "LONG"
            source = "YF calc"
        else:
            continue

        rr_str = f"{rr:.1f}" if rr else "?"
        lines.append(
            f"| **{ticker}** | {direction} | ${entry:,.2f} | ${stop:,.2f} | "
            f"${target:,.2f} | {rr_str} | ${atr:.2f} | {trend} | {source} |"
        )

    if len(lines) <= 4:
        return ""  # No setups generated
    
    # Add notes
    lines.append("")
    lines.append("> **Screener** = technical screener 計算 | **YF calc** = 快速 yfinance ATR-based (1.5x SL / 3x TP)")
    lines.append("> ⚠️ YF calc setups 係粗略估算，建議人手確認先落單")

    return "\n".join(lines)


def build_analysis_prompt(headlines: list[dict], screener_picks: list[dict]) -> str:
    """Build Gemini prompt for news-driven stock discovery."""
    
    # Format headlines (limit to 25 best)
    headline_block = "\n".join(
        f"[{h['source_name']}] {h['title']}"
        for h in headlines[:25]
    )

    # Format screener picks
    screener_block = "目前冇 screener picks" if not screener_picks else \
        "\n".join(
            f"- {p.get('ticker','?')}: ${p.get('price',0):.2f} | "
            f"RS={p.get('rs_score',0):.1f} | confidence={p.get('confidence','?')} | "
            f"{p.get('reason','')}"
            for p in screener_picks[:8]
        )

    # Known universe for reference
    universe_sample = ", ".join(sorted(UNIVERSE)[:40]) + "..."

    return f"""你是專業美股分析師。以下是今日高質素財經新聞 + screener 股票推薦。

請分析新聞，搵出有潛力嘅股票，然後對照 screener picks，輸出推薦。

**特別注意：**
- 新聞提到嘅公司 ticker → 標註 catalyst
- 行業趨勢 → 列出受惠股
- 市場風險 → 標註要避開嘅板塊

=== 今日新聞（CNBC/MarketWatch/Reuters/Bloomberg/SeekingAlpha/TechCrunch）===
{headline_block}

=== 現有 Screener Picks（技術面篩選）===
{screener_block}

=== 追蹤 Universe（S&P 100+ 流動股）===
{universe_sample}

請用繁體中文輸出以下格式（直接俾結論，唔好廢話）：

## 🔥 新聞驅動推薦

每隻推薦格式：`TICKER | 方向 | catalyst | 信心(HIGH/MED/LOW)`

1. 列出 3-8 隻有新聞 catalyst 嘅股票
2. 每隻解釋：咩新聞驅動 + 點解有潛力
3. 標註邊啲同 screener picks 重疊（✅ 重疊 / 🆕 新發現）

## ⚠️ 風險警示
- 新聞中提到嘅風險板塊/事件
- 要避開嘅股票（如有）

## 📊 Screener Cross-Check
- 新聞 picks 同 screener picks 嘅重疊率
- 邊個方向更可信（新聞 > screener / screener > 新聞）"""


def parse_tickers_from_text(text: str) -> list[str]:
    """Extract ticker symbols from text (uppercase 2-5 letter words)."""
    # Find all uppercase words that look like tickers
    words = re.findall(r'\b([A-Z]{2,5})\b', text)
    # Filter: must be in our universe
    tickers = [w for w in words if w in UNIVERSE]
    # Dedup while preserving order
    seen = set()
    result = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


# ══════════════════════════════════════════════════════════════
# OUTPUT & DELIVERY
# ══════════════════════════════════════════════════════════════

def save_and_send(report: str, headlines: list[dict], screener_picks: list[dict]):
    """Save report to JSON + TXT, send to Telegram."""
    now = datetime.now(HKT)
    date_str = now.strftime("%Y-%m-%d")
    ts = now.strftime("%Y%m%d_%H%M")

    # Extract tickers from report
    tickers = parse_tickers_from_text(report)

    # Save JSON
    json_path = os.path.join(OUTPUT_DIR, f"news_picks_{ts}.json")
    json_data = {
        "timestamp": now.isoformat(),
        "date": date_str,
        "headline_count": len(headlines),
        "screener_picks": [{"ticker": p.get("ticker"), "price": p.get("price"),
                           "rs_score": p.get("rs_score"), "confidence": p.get("confidence")}
                          for p in screener_picks],
        "news_tickers": tickers,
        "report": report,
    }
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(json_path), suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, json_path)
    print(f"  💾 JSON: {json_path}")

    # Save TXT
    txt_path = os.path.join(OUTPUT_DIR, f"news_picks_{ts}.txt")
    with open(txt_path, "w") as f:
        f.write(report)
    print(f"  💾 TXT: {txt_path}")

    # Also save as latest (for morning briefing to read)
    latest_json = os.path.join(EQUITIES_DIR, "news_picks_latest.json")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(latest_json), suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, latest_json)
    print(f"  💾 Latest: {latest_json}")

    # Telegram message
    # Strip markdown tables (Telegram no support), convert to readable format
    tg_report = report.replace("|", "·").replace("---", "").replace(":---", "").replace("---:", "")
    # Limit to 3800 chars for Telegram
    if len(tg_report) > 3800:
        tg_report = tg_report[:3770] + "\n\n...(truncated)"
    
    tg_msg = f"📰 <b>新聞選股</b> — {date_str}\n\n{tg_report}"
    # Simple HTML escaping
    tg_msg = tg_msg.replace("&", "&amp;").replace("<b>", "⟨B⟩").replace("</b>", "⟨/B⟩")
    tg_msg = tg_msg.replace("⟨B⟩", "<b>").replace("⟨/B⟩", "</b>")
    tg_msg = tg_msg.replace("&lt;", "&amp;lt;").replace("&gt;", "&amp;gt;")

    sent = tg_send(tg_msg, silent=True)
    print(f"  {'✅ TG sent' if sent else '❌ TG failed'}")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    now = datetime.now(HKT)
    print("=" * 60)
    print(f"📰 NEWS-DRIVEN STOCK DISCOVERY — {now.strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 60)

    # Phase 1: Load screener picks (keep as reference)
    print(f"\n[1/3] Loading screener picks...")
    screener_picks = load_screener_picks()
    print(f"  {len(screener_picks)} screener picks loaded")

    # Phase 2: Gemini 3.5 Flash + Google Search → one-shot news analysis
    print(f"\n[2/3] Gemini 3.5 Flash searching latest financial news...")
    prompt = build_search_prompt()
    report = ask_gemini_search(prompt, max_tokens=8192)

    if report.startswith("[Gemini error") or report.startswith("[ALL FAILED"):
        print(f"❌ Gemini failed: {report}")
        return

    print(f"  ✅ Response: {len(report)} chars")

    # Phase 3: Build trading setups + save + send
    print(f"\n[3/3] Building trading setups...")
    news_tickers = parse_tickers_from_text(report)
    print(f"  News tickers found: {news_tickers}")
    screener_map = load_all_screener()
    setups_section = build_trade_setups(news_tickers, screener_map)
    if setups_section:
        report += "\n" + setups_section
        print(f"  ✅ Added trading setups for {len(news_tickers)} tickers")

    print(f"\n📊 Preview:\n{report[:600]}...")

    # Save + Send
    print(f"\n💾 Saving and sending...")
    save_and_send(report, [], screener_picks)  # headlines param kept for compat

    print(f"\n✅ News-Driven Stock Discovery complete")


if __name__ == "__main__":
    main()
