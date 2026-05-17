#!/usr/bin/env python3
"""
ai_bubble_weekly.py V4 — 每週市場深度報告
═══════════════════════════════════════════
框架：
  1. TL;DR 一句總結
  2. 宏觀催化劑 & 事件風險（IPO pipeline、Fed、監管、AI bubble news）
  3. 上週市場總結（漲跌排行榜 + 關鍵事件）
  4. 市場體檢（各資產：趨勢/動能/偏離度/RSI/支撐阻力）
  5. 加密貨幣深度（BTC/ETH/SOL：on-chain proxy + 技術面）
  6. 跨市場關聯（BTC-SPX/ETH-納指/黃金/VIX/信用）
  7. 市場體制判斷（Risk-On / Neutral / Risk-Off）
  8. 本週關鍵價位 + 前瞻
"""
import json, os, sys, re
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
import requests
import xml.etree.ElementTree as ET

DATA_DIR = os.path.expanduser("~/market_data")
HKT = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════

def load_prices(symbol):
    """Load from daily data dir (newest file). Normalize dates."""
    daily_dir = os.path.join(DATA_DIR, "daily")
    if not os.path.exists(daily_dir):
        path = os.path.join(DATA_DIR, f"{symbol}_history.csv")
        if os.path.exists(path):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if "Close" in df.columns:
                s = df["Close"]
                # Normalize timezone-aware index to date-only (UTC)
                idx = pd.to_datetime(s.index, utc=True).normalize()
                s.index = idx
                return s
        return None

    dates = sorted(os.listdir(daily_dir), reverse=True)
    for d in dates:
        fpath = os.path.join(daily_dir, d, f"{symbol.replace('-','_')}.csv")
        if os.path.exists(fpath):
            df = pd.read_csv(fpath, index_col=0, parse_dates=True)
            if "Close" in df.columns:
                s = df["Close"]
                # Normalize timezone-aware index to date-only (UTC)
                idx = pd.to_datetime(s.index, utc=True).normalize()
                s.index = idx
                return s
    return None
def load_history_all(symbol):
    """Load full history by merging all daily files. Normalize dates to date-only for cross-asset comparison."""
    daily_dir = os.path.join(DATA_DIR, "daily")
    if not os.path.exists(daily_dir):
        path = os.path.join(DATA_DIR, f"{symbol}_history.csv")
        if os.path.exists(path):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if "Close" in df.columns:
                s = df["Close"]
                # Normalize timezone-aware index to date-only (UTC)
                idx = pd.to_datetime(s.index, utc=True).normalize()
                s.index = idx
                return s
        return None

    series_list = []
    for d in sorted(os.listdir(daily_dir)):
        fpath = os.path.join(daily_dir, d, f"{symbol.replace('-','_')}.csv")
        if os.path.exists(fpath):
            df = pd.read_csv(fpath, index_col=0, parse_dates=True)
            if "Close" in df.columns:
                s = df["Close"]
                idx = pd.to_datetime(s.index, utc=True).normalize()
                s.index = idx
                series_list.append(s)
    if series_list:
        result = pd.concat(series_list)
        result = result[~result.index.duplicated(keep='last')]
        return result.sort_index()
    return None

# ═══════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════

def rsi(prices, period=14):
    if prices is None or len(prices) < period + 1: return None
    delta = prices.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_val = 100 - (100 / (1 + rs))
    return round(rsi_val.iloc[-1], 1) if not pd.isna(rsi_val.iloc[-1]) else None

def sma(prices, period):
    if prices is None or len(prices) < period: return None
    return round(prices.rolling(period).mean().iloc[-1], 2)

def ema(prices, period):
    if prices is None or len(prices) < period: return None
    return round(prices.ewm(span=period, adjust=False).mean().iloc[-1], 2)

def momentum(prices, period=10):
    """Rate of change"""
    if prices is None or len(prices) < period: return None
    return round((prices.iloc[-1] / prices.iloc[-period] - 1) * 100, 1)

def volatility(prices, period=20):
    """Annualized volatility"""
    if prices is None or len(prices) < period: return None
    rets = prices.pct_change().dropna().tail(period)
    return round(rets.std() * np.sqrt(252) * 100, 1)

def support_resistance(prices, window=60):
    """Find nearest support/resistance using rolling min/max"""
    if prices is None or len(prices) < window: return None, None
    recent = prices.tail(window)
    current = prices.iloc[-1]
    # Support: recent swing lows
    lows = recent.rolling(20).min().dropna().unique()
    support = None
    for lvl in sorted(lows, reverse=True):
        if lvl < current:
            support = round(lvl, 2)
            break
    # Resistance: recent swing highs
    highs = recent.rolling(20).max().dropna().unique()
    resistance = None
    for lvl in sorted(highs):
        if lvl > current:
            resistance = round(lvl, 2)
            break
    return support, resistance

def trend_strength(prices):
    """Trend strength: % of days above 20MA in last 30 days"""
    if prices is None or len(prices) < 30: return None
    ma20 = prices.rolling(20).mean()
    recent = prices.tail(30)
    ma20_recent = ma20.tail(30)
    above = (recent > ma20_recent).sum()
    return round(above / 30 * 100, 1)

# ═══════════════════════════════════════════
# CORRELATION
# ═══════════════════════════════════════════

def rolling_corr(s1, s2, window=21):
    """Rolling correlation between two series (daily returns)"""
    if s1 is None or s2 is None: return None, None
    # Align on dates
    common_idx = s1.index.intersection(s2.index)
    if len(common_idx) < window + 5: return None, None
    r1 = s1[common_idx].pct_change().dropna()
    r2 = s2[common_idx].pct_change().dropna()
    common_idx2 = r1.index.intersection(r2.index)
    if len(common_idx2) < window: return None, None
    corr_series = r1[common_idx2].rolling(window).corr(r2[common_idx2])
    # Drop NaN, get most recent valid
    valid = corr_series.dropna()
    if len(valid) < 2: return None, None
    current = round(valid.iloc[-1], 3)
    prev = round(valid.iloc[-6], 3) if len(valid) >= 6 else None
    return current, prev

# ═══════════════════════════════════════════
# ASSET REGISTRY
# ═══════════════════════════════════════════

ASSETS = {
    # (display_name, symbol, category, show_full_analysis)
    "SPY":  ("標普500", "SPY", "美股", True),
    "QQQ":  ("納指100", "QQQ", "美股", False),
    "IWM":  ("羅素2000", "IWM", "美股", False),
    "SMH":  ("半導體", "SMH", "美股", True),
    "NVDA": ("NVIDIA", "NVDA", "AI龍頭", True),
    "ANET": ("Arista", "ANET", "AI網路", False),
    "GLD":  ("黃金", "GLD", "避險", True),
    "HYG":  ("高收益債", "HYG", "信用", True),
    "VIX":  ("恐慌指數", "VIX", "波動", True),
    "BTC":  ("比特幣", "BTC-USD", "加密", True),
    "ETH":  ("以太幣", "ETH-USD", "加密", True),
    "SOL":  ("Solana", "SOL-USD", "加密", True),
}

# ═══════════════════════════════════════════
# MACRO CATALYSTS (RSS-based)
# ═══════════════════════════════════════════

MACRO_QUERIES = [
    # (keyword, emoji, label, category)
    ("SpaceX+IPO", "🚀", "SpaceX IPO", "IPO Pipeline"),
    ("OpenAI+IPO+2026", "🤖", "OpenAI IPO", "IPO Pipeline"),
    ("Anthropic+IPO+2026", "🧠", "Anthropic IPO", "IPO Pipeline"),
    ("Cerebras+CoreWeave+IPO", "💻", "AI Infra IPO", "IPO Pipeline"),
    ("Federal+Reserve+rate+cut+hike", "🏦", "Fed Policy", "Macro"),
    ("CPI+inflation+PPI+data", "📊", "Inflation Data", "Macro"),
    ("SEC+crypto+regulation+bitcoin", "⚖️", "Crypto Regulation", "Regulation"),
    ("AI+bubble+crash+warning", "🫧", "AI Bubble Risk", "Bubble Watch"),
    ("Nvidia+earnings+AI+chip", "🔴", "NVDA/AI Chips", "AI Sector"),
]


def fetch_macro_catalysts():
    """Fetch latest macro catalysts from Google News RSS. Returns list of news items."""
    results = []
    seen = set()

    for query, emoji, label, category in MACRO_QUERIES:
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.text)
            items = root.findall(".//item")[:3]  # Max 3 per query
            for item in items:
                title_el = item.find("title")
                pubdate_el = item.find("pubDate")
                if title_el is None or not title_el.text:
                    continue
                title = title_el.text
                if title in seen:
                    continue
                seen.add(title)
                pubdate = pubdate_el.text[:22] if pubdate_el is not None and pubdate_el.text else ""
                results.append({
                    "emoji": emoji,
                    "label": label,
                    "category": category,
                    "title": title,
                    "date": pubdate,
                })
        except Exception as e:
            print(f"  [WARN] RSS fetch failed for '{label}': {e}", file=sys.stderr)

    return results


def format_macro_section(catalysts: list) -> str:
    """Format macro catalysts for weekly report."""
    if not catalysts:
        return "  ⚠️ 本週冇重大宏觀事件\n"

    # Group by category
    from collections import defaultdict
    groups = defaultdict(list)
    for c in catalysts:
        groups[c["category"]].append(c)

    lines = []

    # IPO Pipeline (most important for AI bubble)
    if "IPO Pipeline" in groups:
        lines.append("**🚀 IPO Pipeline**")
        for c in groups["IPO Pipeline"][:6]:
            lines.append(f"  {c['emoji']} {c['title'][:130]}")
        lines.append("")

    # Bubble Watch
    if "Bubble Watch" in groups:
        lines.append("**🫧 AI 泡沫警號**")
        for c in groups["Bubble Watch"][:4]:
            lines.append(f"  {c['emoji']} {c['title'][:130]}")
        lines.append("")

    # Macro
    if "Macro" in groups:
        lines.append("**🌍 宏觀**")
        for c in groups["Macro"][:4]:
            lines.append(f"  {c['emoji']} {c['title'][:130]}")
        lines.append("")

    # Regulation
    if "Regulation" in groups:
        lines.append("**⚖️ 監管**")
        for c in groups["Regulation"][:3]:
            lines.append(f"  {c['emoji']} {c['title'][:130]}")
        lines.append("")

    # AI Sector
    if "AI Sector" in groups:
        lines.append("**🤖 AI 行業**")
        for c in groups["AI Sector"][:4]:
            lines.append(f"  {c['emoji']} {c['title'][:130]}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════
# MAIN REPORT
# ═══════════════════════════════════════════

def main():
    now = datetime.now(HKT)
    print(f"# 📋 AI 泡沫每週深度報告 V5")
    print(f"## {now.strftime('%Y-%m-%d %H:%M')} HKT")
    print()

    # Pre-load all data
    print("⏳ 載入市場數據...", end=" ", flush=True)
    data = {}
    for sym, (name, ticker, cat, full) in ASSETS.items():
        prices = load_history_all(ticker) if full else load_prices(ticker)
        if prices is not None and len(prices) > 20:
            data[sym] = prices
    print(f"✅ {len(data)} 資產")
    print()

    # ═══════════════════════════════════════
    # 2. MACRO CATALYSTS & EVENT RISK
    # ═══════════════════════════════════════

    print("---")
    print()
    print("## 🌍 二：宏觀催化劑 & 事件風險")
    print()
    print("⏳ 掃描最新宏觀事件...", end=" ", flush=True)
    catalysts = fetch_macro_catalysts()
    print(f"✅ {len(catalysts)} 件")
    print()
    print(format_macro_section(catalysts))
    print()

    # ═══════════════════════════════════════
    # 3. WEEKLY PERFORMANCE RANKING
    # ═══════════════════════════════════════

    print("---")
    print()
    print("## 📊 一：上週漲跌排行榜")
    print()

    rankings = []
    for sym, prices in data.items():
        if len(prices) < 5: continue
        current = prices.iloc[-1]
        week_ago = prices.iloc[-6] if len(prices) >= 6 else prices.iloc[0]
        chg = round((current - week_ago) / week_ago * 100, 1)
        month_ago = prices.iloc[-22] if len(prices) >= 22 else prices.iloc[0]
        m_chg = round((current - month_ago) / month_ago * 100, 1)
        rankings.append((sym, ASSETS[sym][0], current, chg, m_chg))

    rankings.sort(key=lambda x: x[3], reverse=True)

    print(f"| 排名 | 資產 | 現價 | 週變動 | 月變動 | 評級 |")
    print(f"|:--|:--|--:|--:|--:|:--|")
    for rank, (sym, name, price, wk, mk) in enumerate(rankings, 1):
        w_emoji = "🟢" if wk > 2 else ("🔴" if wk < -2 else "🟡")
        m_emoji = "🟢" if mk > 5 else ("🔴" if mk < -5 else "🟡")
        rating = "強勢" if wk > 2 and mk > 0 else ("弱勢" if wk < -2 and mk < 0 else "中性")
        print(f"| {rank} | {name} | \${price:,.2f} | {w_emoji} {wk:+.1f}% | {m_emoji} {mk:+.1f}% | {rating} |")
    print()

    # ═══════════════════════════════════════
    # 4. MARKET HEALTH CHECK
    # ═══════════════════════════════════════

    print("---")
    print()
    print("## 🩺 三：市場體檢")
    print()

    key_assets = ["SPY", "QQQ", "SMH", "NVDA", "GLD", "HYG", "VIX", "BTC", "ETH", "SOL"]
    print(f"| 資產 | 現價 | 20MA | 偏離% | RSI | 動能10d | 波幅% | 趨勢強度 | 支撐 | 阻力 |")
    print(f"|:--|--:|--:|--:|--:|--:|--:|--:|--:|--:|")

    for sym in key_assets:
        if sym not in data: continue
        p = data[sym]; name = ASSETS[sym][0]
        current = round(p.iloc[-1], 2)
        ma20 = sma(p, 20)
        dev = round((current - ma20) / ma20 * 100, 1) if ma20 else None
        r = rsi(p)
        mom = momentum(p, 10)
        vol = volatility(p, 20)
        ts = trend_strength(p)
        sup, res = support_resistance(p)

        dev_str = f"{dev:+.1f}%" if dev is not None else "—"
        rsi_str = f"{r:.0f}" if r is not None else "—"
        mom_str = f"{mom:+.1f}%" if mom is not None else "—"
        vol_str = f"{vol:.1f}" if vol is not None else "—"
        ts_str = f"{ts:.0f}%" if ts is not None else "—"
        sup_str = f"\${sup:,.0f}" if sup else "—"
        res_str = f"\${res:,.0f}" if res else "—"

        # Color code deviations
        if dev is not None:
            if dev > 10: dev_str = f"🔴 {dev_str}"
            elif dev > 5: dev_str = f"🟡 {dev_str}"
            elif dev < -5: dev_str = f"🟢 {dev_str}"

        print(f"| {name} | \${current:,.2f} | \${ma20:,.2f} | {dev_str} | {rsi_str} | {mom_str} | {vol_str} | {ts_str} | {sup_str} | {res_str} |")
    print()

    # ═══════════════════════════════════════
    # 5. CRYPTO DEEP DIVE
    # ═══════════════════════════════════════

    print("---")
    print()
    print("## ₿ 四：加密貨幣深度分析")
    print()

    for sym in ["BTC", "ETH", "SOL"]:
        if sym not in data: continue
        p = data[sym]; name = ASSETS[sym][0]
        current = p.iloc[-1]

        # Multi-timeframe trend
        wk_ret = round((p.iloc[-1] / p.iloc[-7] - 1) * 100, 1) if len(p) >= 7 else None
        m_ret = round((p.iloc[-1] / p.iloc[-30] - 1) * 100, 1) if len(p) >= 30 else None
        q_ret = round((p.iloc[-1] / p.iloc[-90] - 1) * 100, 1) if len(p) >= 90 else None

        # BTC dominance proxy (if this is BTC)
        btc_p = data.get("BTC")
        eth_p = data.get("ETH")

        # Key levels
        ma50 = sma(p, 50); ma200 = sma(p, 200)
        sup, res = support_resistance(p)
        r = rsi(p)
        vol = volatility(p, 20)

        print(f"### {name} (\${current:,.2f})")
        print()
        print(f"| 指標 | 數值 | 判讀 |")
        print(f"|:--|:--|:--|")

        if wk_ret is not None:
            emoji = "🟢" if wk_ret > 0 else "🔴"
            print(f"| 本週變動 | {emoji} {wk_ret:+.1f}% | — |")
        if m_ret is not None:
            emoji = "🟢" if m_ret > 0 else "🔴"
            print(f"| 月度變動 | {emoji} {m_ret:+.1f}% | — |")
        if q_ret is not None:
            emoji = "🟢" if q_ret > 0 else "🔴"
            print(f"| 季度變動 | {emoji} {q_ret:+.1f}% | — |")

        if ma50 and ma200:
            above50 = "✅" if current > ma50 else "❌"
            above200 = "✅" if current > ma200 else "❌"
            print(f"| 50日 MA | \${ma50:,.0f} | {above50} 現價{'高於' if current > ma50 else '低於'} MA50 |")
            print(f"| 200日 MA | \${ma200:,.0f} | {above200} 現價{'高於' if current > ma200 else '低於'} MA200 |")

        if r: print(f"| RSI(14) | {r:.0f} | {'🟡 超買' if r > 70 else ('🟢 超賣' if r < 30 else '➡ 中性')} |")
        if vol: print(f"| 年化波幅 | {vol:.1f}% | — |")
        if sup: print(f"| 最近支撐 | \${sup:,.0f} | 距離 {(current/sup-1)*100:+.1f}% |")
        if res: print(f"| 最近阻力 | \${res:,.0f} | 距離 {(res/current-1)*100:+.1f}% |")
        print()

    # ═══════════════════════════════════════
    # 6. CROSS-MARKET CORRELATION
    # ═══════════════════════════════════════

    print("---")
    print()
    print("## 🔗 五：跨市場關聯分析")
    print()

    pairs = [
        ("BTC", "SPY", "BTC-美股"),
        ("ETH", "QQQ", "ETH-納指"),
        ("SOL", "SMH", "SOL-半導體"),
        ("BTC", "GLD", "BTC-黃金"),
        ("SPY", "HYG", "美股-信用"),
        ("SPY", "VIX", "美股-恐慌"),
    ]

    print(f"| 關聯對 | 21日相關 | 週變化 | 趨勢 | 解讀 |")
    print(f"|:--|--:|--:|:--|:--|")

    for s1, s2, label in pairs:
        if s1 not in data or s2 not in data: continue
        corr, prev = rolling_corr(data[s1], data[s2], 21)
        if corr is None: continue
        chg = round(corr - prev, 3) if prev is not None else None
        chg_str = f"{chg:+.3f}" if chg is not None else "—"

        if abs(corr) > 0.7: trend = "強關聯"
        elif abs(corr) > 0.4: trend = "中等"
        else: trend = "脫勾"

        if corr > 0.7: interpretation = "高度同向，跟隨大市"
        elif corr > 0.4: interpretation = "部分跟隨，有獨立性"
        elif corr > 0: interpretation = "弱關聯，行自己路"
        else: interpretation = "反向關聯，避險屬性"

        print(f"| {label} | {corr:+.3f} | {chg_str} | {trend} | {interpretation} |")
    print()

    # ═══════════════════════════════════════
    # 7. MARKET REGIME
    # ═══════════════════════════════════════

    print("---")
    print()
    print("## 🎯 六：市場體制判斷")
    print()

    # Determine regime from multiple signals
    score = 0
    signals = []

    # SPY trend
    spy_p = data.get("SPY")
    if spy_p is not None and len(spy_p) >= 20:
        spy_ma20 = sma(spy_p, 20)
        if spy_ma20 and spy_p.iloc[-1] > spy_ma20:
            score += 2; signals.append("✅ SPY 高於 20MA")
        else:
            score -= 2; signals.append("❌ SPY 低於 20MA")

    # VIX level
    vix_p = data.get("VIX")
    if vix_p is not None:
        vix_val = vix_p.iloc[-1]
        if vix_val < 20:
            score += 2; signals.append(f"✅ VIX {vix_val:.1f} <20（平靜）")
        elif vix_val < 30:
            score += 1; signals.append(f"🟡 VIX {vix_val:.1f} 20-30（警戒）")
        else:
            score -= 2; signals.append(f"🔴 VIX {vix_val:.1f} >30（恐慌）")

    # Credit
    hyg_p = data.get("HYG")
    if hyg_p is not None and len(hyg_p) >= 20:
        hyg_ma20 = sma(hyg_p, 20)
        if hyg_ma20 and hyg_p.iloc[-1] > hyg_ma20:
            score += 2; signals.append("✅ HYG 高於 20MA（信用穩）")
        else:
            score -= 2; signals.append("❌ HYG 低於 20MA（信用裂縫）")

    # BTC correlation to SPY
    btc_p = data.get("BTC")
    if btc_p is not None and spy_p is not None:
        btc_corr, _ = rolling_corr(btc_p, spy_p, 21)
        if btc_corr is not None:
            if btc_corr > 0.5:
                score += 1; signals.append(f"🟢 BTC-SPX corr {btc_corr:.2f}（risk-on mode）")
            elif btc_corr < 0.3:
                score -= 1; signals.append(f"🟡 BTC-SPX corr {btc_corr:.2f}（脫勾）")

    # Crypto momentum
    btc_mom = momentum(btc_p, 10) if btc_p is not None else None
    if btc_mom is not None:
        if btc_mom > 5:
            score += 2; signals.append(f"🟢 BTC 動能 {btc_mom:+.1f}%（強勢）")
        elif btc_mom < -5:
            score -= 2; signals.append(f"🔴 BTC 動能 {btc_mom:+.1f}%（弱勢）")

    # Regime classification
    if score >= 6: regime = "🟢 Risk-On（進取）"
    elif score >= 2: regime = "🟡 Neutral（中性）"
    elif score >= -2: regime = "🟠 Caution（審慎）"
    else: regime = "🔴 Risk-Off（防守）"

    print(f"**體制：{regime}** （綜合分數：{score}）")
    print()
    for s in signals:
        print(f"- {s}")
    print()

    # ═══════════════════════════════════════
    # 8. WEEK AHEAD — KEY LEVELS
    # ═══════════════════════════════════════

    print("---")
    print()
    print("## 🔮 七：本週前瞻")
    print()

    print("### 關鍵價位表")
    print()
    print(f"| 資產 | 現價 | 支撐 | 阻力 | 本週關注 |")
    print(f"|:--|--:|--:|--:|:--|")

    for sym in ["SPY", "SMH", "NVDA", "BTC", "ETH", "SOL"]:
        if sym not in data: continue
        p = data[sym]; name = ASSETS[sym][0]
        current = p.iloc[-1]
        sup, res = support_resistance(p)
        sup_str = f"\${sup:,.0f}" if sup else "—"
        res_str = f"\${res:,.0f}" if res else "—"

        # What to watch
        watch = ""
        if res and current > 0:
            dist_res = (res / current - 1) * 100
            if dist_res < 5: watch = f"⚠️ 接近阻力 ({dist_res:+.1f}%)"
            elif dist_res < 10: watch = f"阻力尚有 {dist_res:.0f}% 距離"
        if sup and current > 0:
            dist_sup = (current / sup - 1) * 100
            if dist_sup < 5: watch += f" | 🛡 接近支撐 ({dist_sup:+.1f}%)"

        print(f"| {name} | \${current:,.2f} | {sup_str} | {res_str} | {watch} |")
    print()

    # ═══════════════════════════════════════
    # 7. ACTIONABLE INSIGHTS
    # ═══════════════════════════════════════

    print("### 本週操作備忘")
    print()

    insights = []

    # SOX extreme check
    sox_p = data.get("SMH")
    if sox_p is not None:
        sox_dev = (sox_p.iloc[-1] / sma(sox_p, 20) - 1) * 100 if sma(sox_p, 20) else 0
        if sox_dev > 10:
            insights.append(f"🔴 **SOX 極端偏離** (+{sox_dev:.1f}%) — 本週若有回調至 MA20，可考慮減倉或對沖")
        elif sox_dev > 5:
            insights.append(f"🟡 SOX 偏高 (+{sox_dev:.1f}%) — 追高風險大，等 pullback")

    # VIX opportunity
    if vix_p is not None and vix_p.iloc[-1] > 25:
        insights.append(f"🟢 **VIX 高企** ({vix_p.iloc[-1]:.1f}) — 歷史顯示恐慌時係買入機會，留意 SPY 支撐位")

    # BTC divergence
    spy_p = data.get("SPY")
    if btc_p is not None and spy_p is not None:
        spy_wk = (spy_p.iloc[-1] / spy_p.iloc[-7] - 1) * 100 if len(spy_p) >= 7 else 0
        btc_wk = (btc_p.iloc[-1] / btc_p.iloc[-7] - 1) * 100 if len(btc_p) >= 7 else 0
        if spy_wk > 1 and btc_wk < -2:
            insights.append(f"⚠️ **BTC 落後美股** (SPY {spy_wk:+.1f}% vs BTC {btc_wk:+.1f}%) — 可能補漲，亦可能反映加密資金流出")
        elif spy_wk < -1 and btc_wk > 1:
            insights.append(f"🟢 **BTC 強於美股** (SPY {spy_wk:+.1f}% vs BTC {btc_wk:+.1f}%) — 加密獨立強勢")

    # Credit check
    hyg_p = data.get("HYG")
    if hyg_p is not None and hyg_p.iloc[-1] < 79:
        insights.append(f"🔴 **信用市場裂縫** — HYG \${hyg_p.iloc[-1]:.2f} < \$79，全面減倉")

    # Gold signal
    gld_p = data.get("GLD")
    if gld_p is not None and btc_p is not None:
        gld_wk = (gld_p.iloc[-1] / gld_p.iloc[-7] - 1) * 100 if len(gld_p) >= 7 else 0
        if gld_wk > 2:
            insights.append(f"🟡 黃金走強 (+{gld_wk:.1f}%) — 避險需求上升，留意風險資產回調")

    if not insights:
        insights.append("✅ 本週無特別警號，維持原有持倉")

    for ins in insights:
        print(f"- {ins}")
    print()

    # ═══════════════════════════════════════
    # 9. TL;DR
    # ═══════════════════════════════════════

    print("---")
    print()
    print("## 📌 八：TL;DR")
    print()

    # Auto-generate TL;DR from findings
    tldr = []

    # Market direction
    spy_p = data.get("SPY")
    if spy_p is not None:
        spy_wk = (spy_p.iloc[-1] / spy_p.iloc[-7] - 1) * 100 if len(spy_p) >= 7 else 0
        tldr.append(f"• 美股 { '上升' if spy_wk > 0 else '下跌' } {abs(spy_wk):.1f}%，市場體制：{regime}")

    # Crypto direction
    if btc_p is not None:
        btc_wk = (btc_p.iloc[-1] / btc_p.iloc[-7] - 1) * 100 if len(btc_p) >= 7 else 0
        tldr.append(f"• BTC { '升' if btc_wk > 0 else '跌' } {abs(btc_wk):.1f}%，加密{ '跑贏' if btc_wk > (spy_wk if spy_p is not None else 0) else '落後' }美股")

    # Key risk
    sox_p = data.get("SMH")
    if sox_p is not None:
        sox_dev = (sox_p.iloc[-1] / sma(sox_p, 20) - 1) * 100 if sma(sox_p, 20) else 0
        if sox_dev > 10:
            tldr.append(f"• ⚠️ 最大風險：SOX 極端偏離 +{sox_dev:.1f}%，回調風險高")

    # VIX
    if vix_p is not None:
        tldr.append(f"• VIX {vix_p.iloc[-1]:.1f}，市場{ '平靜' if vix_p.iloc[-1] < 20 else '警戒' if vix_p.iloc[-1] < 30 else '恐慌' }")

    for t in tldr:
        print(t)

    print()
    print(f"📁 數據源：{DATA_DIR}/daily/")
    print(f"🕐 下次報告：下週一 08:00 HKT")


if __name__ == "__main__":
    main()
