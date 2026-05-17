#!/usr/bin/env python3
"""
Cross-Asset Signal Matrix — 跨資產信號矩陣
═══════════════════════════════════════════════
合併 crypto signal + 美股技術面 → detect macro regime + high-conviction trades

Patterns detected:
  🔴 Risk-Off: crypto Short + SPY below MA50 + VIX >20 → 提高 Short 注碼
  🟢 Risk-On: crypto Long + SPY above MA50 + VIX <20 → 可加大 Long
  🟡 Divergence: crypto ≠ stocks → 可能反轉
  🟣 Opportunity: stock pullback + crypto oversold → 潛在底部

Run: python3 cross_asset_matrix.py
Cron: 每日美股收市後 06:00 HKT
"""

import os, sys, json, sqlite3, urllib.request
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import numpy as np

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
DATA_DIR = os.path.expanduser("~/market_data/phase1")


# ═══════════════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════════════

def fetch_yahoo(symbol, range_days='3mo'):
    """Fetch daily data from Yahoo Finance."""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_days}&interval=1d'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        r = data['chart']['result'][0]
        closes = [x for x in r['indicators']['quote'][0]['close'] if x is not None]
        return closes
    except:
        return None


def sma(data, period):
    clean = [x for x in data if x is not None]
    if len(clean) < period:
        return None
    return sum(clean[-period:]) / period


def get_recent_crypto_signals(hours=72):
    """Get crypto signals from last N hours."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int((datetime.now(HKT) - timedelta(hours=hours)).timestamp())
    c.execute("""
        SELECT indicator, coin, direction, price, timestamp
        FROM signals WHERE created_at > ?
        ORDER BY timestamp DESC
    """, (cutoff,))
    rows = c.fetchall()
    conn.close()
    return rows


def get_crypto_market_state():
    """Get latest crypto market states."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = int((datetime.now(HKT) - timedelta(hours=8)).timestamp())
    c.execute("""
        SELECT coin, timeframe, condition, adx
        FROM market_state WHERE timestamp > ?
        ORDER BY timestamp DESC
    """, (cutoff,))
    rows = c.fetchall()
    conn.close()

    seen = set()
    states = {}
    for coin, tf, cond, adx in rows:
        key = (coin, tf)
        if key not in seen:
            seen.add(key)
            states[f"{coin}_{tf}"] = cond
    return states


# ═══════════════════════════════════════════
# MATRIX LOGIC
# ═══════════════════════════════════════════

def analyze():
    """Run cross-asset analysis."""
    results = []

    # 1. Get crypto state
    crypto_state = get_crypto_market_state()
    btc_4h = crypto_state.get("BTC_4h", "unknown")
    btc_1h = crypto_state.get("BTC_1h", "unknown")
    eth_4h = crypto_state.get("ETH_4h", "unknown")

    # Check if crypto is predominantly bearish
    # Count bear vs bull across all coins/timeframes
    bear_count = sum(1 for v in crypto_state.values() if v == "bear")
    bull_count = sum(1 for v in crypto_state.values() if v == "bull")
    crypto_sentiment = "bearish" if bear_count > bull_count else ("bullish" if bull_count > bear_count else "mixed")

    # 2. Get stock market state
    spy = fetch_yahoo("SPY")
    qqq = fetch_yahoo("QQQ")
    vix = fetch_yahoo("^VIX")
    nvda = fetch_yahoo("NVDA")

    spy_ma50 = sma(spy, 50) if spy else None
    spy_ma20 = sma(spy, 20) if spy else None
    spy_current = spy[-1] if spy else None
    spy_above_ma50 = spy_current > spy_ma50 if spy_current and spy_ma50 else None
    spy_above_ma20 = spy_current > spy_ma20 if spy_current and spy_ma20 else None

    vix_current = vix[-1] if vix else None
    vix_elevated = vix_current > 20 if vix_current else None

    qqq_current = qqq[-1] if qqq else None
    qqq_ma50 = sma(qqq, 50) if qqq else None
    qqq_above_ma50 = qqq_current > qqq_ma50 if qqq_current and qqq_ma50 else None

    nvda_current = nvda[-1] if nvda else None
    nvda_ma50 = sma(nvda, 50) if nvda else None
    nvda_above_ma50 = nvda_current > nvda_ma50 if nvda_current and nvda_ma50 else None

    # 3. Get crypto signals
    signals = get_recent_crypto_signals(72)
    long_sigs = [s for s in signals if s[2] == "LONG"]
    short_sigs = [s for s in signals if s[2] == "SHORT"]
    has_long = len(long_sigs) > 0
    has_short = len(short_sigs) > 0

    # 4. Determine regime
    regime_score = 0
    regime_signals = []

    if spy_above_ma50:
        regime_score += 2
        regime_signals.append("✅ SPY > MA50")
    else:
        regime_score -= 2
        regime_signals.append("❌ SPY < MA50")

    if vix_elevated:
        regime_score -= 2
        regime_signals.append(f"⚠️ VIX {vix_current:.1f} >20")
    else:
        regime_score += 1
        regime_signals.append(f"✅ VIX {vix_current:.1f} <20")

    if nvda_above_ma50:
        regime_score += 1
        regime_signals.append("✅ NVDA > MA50 (AI sector OK)")
    else:
        regime_score -= 1
        regime_signals.append("⚠️ NVDA < MA50 (AI weak)")

    if crypto_sentiment == "bearish":
        regime_score -= 2
        regime_signals.append("🔴 Crypto predominantly bearish")
    elif crypto_sentiment == "bullish":
        regime_score += 2
        regime_signals.append("🟢 Crypto predominantly bullish")

    # Regime classification
    if regime_score >= 5:
        regime = "🟢 RISK-ON"
        regime_desc = "股市強 + Crypto 強 → 可以進取"
    elif regime_score >= 2:
        regime = "🟡 NEUTRAL-BULLISH"
        regime_desc = "偏強但唔一致 → 審慎樂觀"
    elif regime_score >= -1:
        regime = "🟡 NEUTRAL"
        regime_desc = "信號混亂 → 觀望為主"
    elif regime_score >= -4:
        regime = "🟠 CAUTION"
        regime_desc = "偏弱 → 減倉/對沖"
    else:
        regime = "🔴 RISK-OFF"
        regime_desc = "全面弱勢 → 防守/Short only"

    # 5. Confidence matrix
    matrix = {
        "regime": regime,
        "regime_desc": regime_desc,
        "score": regime_score,
        "signals": regime_signals,
        "spy": f"${spy_current:,.0f}" if spy_current else "N/A",
        "vix": f"{vix_current:.1f}" if vix_current else "N/A",
        "nvda": f"${nvda_current:,.0f}" if nvda_current else "N/A",
        "crypto_sentiment": crypto_sentiment,
        "bear_count": bear_count,
        "bull_count": bull_count,
        "has_long_signal": has_long,
        "has_short_signal": has_short,
        "long_sigs": long_sigs,
        "short_sigs": short_sigs,
    }

    # 6. Actionable insights
    insights = []

    # Pattern: Risk-On confirmed
    if regime_score >= 5 and has_long and not spy_above_ma50:
        # Actually: if everything looks strong, suggest sizing up
        pass

    if regime in ("🔴 RISK-OFF", "🟠 CAUTION") and has_short:
        insights.append(f"🔴 Risk-Off + Short signal confirmed → 可加 Short 注碼到 1.5x")

    if crypto_sentiment == "bearish" and spy_above_ma50:
        insights.append("🔀 Crypto bear 但美股強 → crypto 可能落後，Short crypto / Long 美股")

    if crypto_sentiment == "bullish" and regime_score < 0:
        insights.append("⚠️ Crypto 獨自 bullish 但 macro 弱 → crypto rally 可能唔持久")

    if nvda_above_ma50 is False and has_short:
        insights.append("🤖 NVDA weak + Short signals → AI sector 資金流出，crypto 跟跌風險高")

    # Look for stock pullback opportunities
    rklb = fetch_yahoo("RKLB")
    asts = fetch_yahoo("ASTS")
    if rklb:
        rklb_current = rklb[-1]
        rklb_ma50 = sma(rklb, 50)
        if rklb_ma50 and rklb_current < rklb_ma50:
            dist = (rklb_current - rklb_ma50) / rklb_ma50 * 100
            if dist > -10:
                insights.append(f"🚀 RKLB ${rklb_current:.2f} 低於 MA50 ({dist:+.1f}%) — SpaceX IPO catalyst ahead")

    matrix["insights"] = insights
    return matrix


# ═══════════════════════════════════════════
# OUTPUT
# ═══════════════════════════════════════════

def format_output(m):
    """Format matrix for Telegram."""
    now = datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')
    lines = [f"🎯 **Cross-Asset Signal Matrix** — {now}", ""]

    # Regime
    lines.append(f"## {m['regime']}: {m['regime_desc']}")
    lines.append(f"Score: {m['score']}")
    lines.append("")

    # Market snapshot
    lines.append("**📊 市場快照**")
    lines.append(f"  SPY: {m['spy']} | VIX: {m['vix']} | NVDA: {m['nvda']}")
    lines.append(f"  Crypto: {m['crypto_sentiment']} ({m['bull_count']}🟢 / {m['bear_count']}🔴)")
    lines.append(f"  Signals: {'🔺Long' if m['has_long_signal'] else ''} {'🔻Short' if m['has_short_signal'] else ''}")
    lines.append("")

    # Regime signals
    lines.append("**🔍 體制信號**")
    for s in m["signals"]:
        lines.append(f"  {s}")
    lines.append("")

    # Actionable insights
    if m["insights"]:
        lines.append("**🎯 行動建議**")
        for ins in m["insights"]:
            lines.append(f"  {ins}")
        lines.append("")

    lines.append(f"_Next update: {(datetime.now(HKT) + timedelta(days=1)).strftime('%m/%d %H:%M')}_")

    return "\n".join(lines)


def main():
    m = analyze()
    output = format_output(m)
    print(output)


if __name__ == "__main__":
    main()
