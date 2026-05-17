#!/usr/bin/env python3
"""
Stock Pullback Scanner — 美股回調掃描器
═══════════════════════════════════════════════════
掃描 AI/太空/半導體/雲端股，搵回調到 MA50/MA200 嘅買入機會。

Output:
  🔴 Oversold（RSI <35 + near MA200）= 可能見底
  🟡 Near Support（price near MA50/MA200 within 5%）= 可考慮
  🟢 Strong Trend（price above MA20,MA50,MA200）= 繼續持有

Run: python3 stock_pullback_scanner.py
Cron: 每日美股收市後 05:30 HKT (21:30 UTC)
Deliver: origin (silent unless actionable signals found)
"""

import urllib.request
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════
# TICKERS TO SCAN
# ═══════════════════════════════════════════

TICKERS = {
    # AI 晶片
    "NVDA": "NVIDIA", "AMD": "AMD", "AVGO": "Broadcom",
    "MRVL": "Marvell", "MU": "Micron",
    # 半導體
    "SMH": "SOX ETF", "TSM": "台積電", "ASML": "ASML",
    "AMAT": "應用材料", "LRCX": "Lam Research", "KLAC": "KLA",
    # AI 基建/雲端
    "ANET": "Arista", "CRWV": "CoreWeave",
    "MSFT": "Microsoft", "GOOGL": "Google",
    "AMZN": "Amazon", "META": "Meta",
    # 太空
    "RKLB": "Rocket Lab", "ASTS": "AST SpaceMobile",
    "LUNR": "Intuitive Machines", "RDW": "Redwire",
    # 國防
    "LMT": "Lockheed", "NOC": "Northrop", "RTX": "RTX",
    # 其他科技
    "CRM": "Salesforce", "NOW": "ServiceNow", "SNOW": "Snowflake",
}

# ═══════════════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════════════

def fetch(symbol, range_days='6mo'):
    """Fetch daily OHLCV from Yahoo Finance."""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_days}&interval=1d'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        r = data['chart']['result'][0]
        closes = r['indicators']['quote'][0]['close']
        # Filter None
        closes = [x for x in closes if x is not None]
        if len(closes) < 100:
            return None
        return closes
    except Exception:
        return None


def sma(data, period):
    """Simple moving average."""
    clean = [x for x in data if x is not None]
    if len(clean) < period:
        return None
    return sum(clean[-period:]) / period


def rsi_calc(closes, period=14):
    """Calculate RSI."""
    clean = [x for x in closes if x is not None]
    if len(clean) < period + 1:
        return None
    deltas = [clean[i] - clean[i-1] for i in range(1, len(clean))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def categorize(closes):
    """Categorize a stock: oversold / near support / strong / weak."""
    if len(closes) < 200:
        return None

    current = closes[-1]
    ma20 = sma(closes, 20)
    ma50 = sma(closes, 50)
    ma200 = sma(closes, 200)
    rsi = rsi_calc(closes)

    if ma20 is None or ma50 is None or ma200 is None:
        return None

    # Calculate distances
    dist_ma50 = (current - ma50) / ma50 * 100
    dist_ma200 = (current - ma200) / ma200 * 100
    dist_ma20 = (current - ma20) / ma20 * 100

    # Trend: where is price relative to MAs
    above_ma20 = current > ma20
    above_ma50 = current > ma50
    above_ma200 = current > ma200

    # Weekly momentum
    if len(closes) >= 5:
        wk_chg = (closes[-1] - closes[-6]) / closes[-6] * 100
    else:
        wk_chg = 0

    # Monthly momentum
    if len(closes) >= 21:
        mo_chg = (closes[-1] - closes[-22]) / closes[-22] * 100
    else:
        mo_chg = 0

    result = {
        "price": round(current, 2),
        "ma20": round(ma20, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "dist_ma50": round(dist_ma50, 1),
        "dist_ma200": round(dist_ma200, 1),
        "rsi": rsi,
        "wk_chg": round(wk_chg, 1),
        "mo_chg": round(mo_chg, 1),
        "above_ma20": above_ma20,
        "above_ma50": above_ma50,
        "above_ma200": above_ma200,
    }

    # 🔴 OVERSOLD: price near/below MA200 + RSI <35
    if (dist_ma200 < 3 and rsi is not None and rsi < 35):
        result["signal"] = "oversold"
        result["emoji"] = "🔴"
        result["action"] = "可能見底 — 留意 reversal signal"
    # 🟡 PULLBACK: price near MA50 (within 5%) but above MA200
    elif dist_ma50 < 0 and dist_ma50 > -5 and above_ma200 and rsi and rsi < 45:
        result["signal"] = "pullback"
        result["emoji"] = "🟡"
        result["action"] = "回調到 MA50 附近 — 可考慮分批入"
    # 🟡 NEAR MA200: price near MA200 (within 3%)
    elif abs(dist_ma200) < 3 and not (above_ma20 and above_ma50):
        result["signal"] = "near_support"
        result["emoji"] = "🟠"
        result["action"] = "接近 MA200 支撐 — 觀察"
    # 🟢 STRONG: price above all MAs
    elif above_ma20 and above_ma50 and above_ma200:
        result["signal"] = "strong"
        result["emoji"] = "🟢"
        result["action"] = f"強勢 — MA20 ({dist_ma20:+.1f}%)"
    # ⚪ WEAK: price below all MAs
    elif not above_ma20 and not above_ma50:
        result["signal"] = "weak"
        result["emoji"] = "⚪"
        result["action"] = "弱勢 — 等企穩 MA50 先考慮"
    else:
        result["signal"] = "mixed"
        result["emoji"] = "🔵"
        result["action"] = "混合信號 — 觀望"

    return result


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

def main():
    results = {}
    for sym, name in TICKERS.items():
        closes = fetch(sym)
        if closes is None:
            continue
        info = categorize(closes)
        if info:
            info["name"] = name
            results[sym] = info

    # Group by signal type
    groups = defaultdict(list)
    for sym, info in results.items():
        groups[info["signal"]].append((sym, info))

    # Only print if there are actionable signals (oversold or pullback)
    actionable = len(groups.get("oversold", [])) + len(groups.get("pullback", [])) + len(groups.get("near_support", []))
    if actionable == 0:
        print("")  # Silent — no actionable signals
        return

    now = datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')
    lines = [f"📊 **Stock Pullback Scanner** — {now}", ""]

    # 🔴 OVERSOLD (top priority)
    if "oversold" in groups:
        lines.append("🔴 **可能見底 (Oversold)**")
        for sym, info in sorted(groups["oversold"], key=lambda x: x[1]["dist_ma200"]):
            lines.append(f"  {info['emoji']} **{info['name']} ({sym})** ${info['price']:.2f}")
            lines.append(f"     RSI={info['rsi']} | MA50 dist={info['dist_ma50']:+.1f}% | MA200 dist={info['dist_ma200']:+.1f}%")
            lines.append(f"     → {info['action']}")
        lines.append("")

    # 🟡 PULLBACK
    if "pullback" in groups:
        lines.append("🟡 **回調到支撐 (Pullback)**")
        for sym, info in sorted(groups["pullback"], key=lambda x: x[1]["dist_ma50"], reverse=True):
            lines.append(f"  {info['emoji']} **{info['name']} ({sym})** ${info['price']:.2f}")
            lines.append(f"     RSI={info['rsi']} | MA50 dist={info['dist_ma50']:+.1f}% | Wk={info['wk_chg']:+.1f}%")
            lines.append(f"     → {info['action']}")
        lines.append("")

    # 🟠 NEAR SUPPORT
    if "near_support" in groups:
        lines.append("🟠 **接近 MA200 支撐**")
        for sym, info in sorted(groups["near_support"], key=lambda x: abs(x[1]["dist_ma200"])):
            lines.append(f"  {info['emoji']} **{info['name']} ({sym})** ${info['price']:.2f} — MA200: ${info['ma200']:.0f}")
        lines.append("")

    # Quick summary of strong stocks for context
    strong = groups.get("strong", [])
    if strong and len(strong) <= 5:
        names = ", ".join(f"{s}" for s, _ in strong[:5])
        lines.append(f"🟢 強勢股: {names}")
        lines.append("")

    lines.append("💡 建議：Oversold = 等 confirm reversal 先入，Pullback = 可分批，Strong = 繼續持有唔好賣")
    lines.append(f"_下次掃描: {(datetime.now(HKT) + timedelta(days=1)).strftime('%m/%d %H:%M')}_")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
