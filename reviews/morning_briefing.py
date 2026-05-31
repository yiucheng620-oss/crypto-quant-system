#!/usr/bin/env python3
"""
⚔️ COMMANDER'S BRIEFING — 作戰指揮簡報
======================================
多市場統一戰情報告。Crypto (Capital.com 20x) + Equities (Firstrade long-only)。

P&L 審查後砍剩嘅組件：
✅ Multi-exchange OB (唯一信號源)
✅ Risk-Off gate (防止逆勢輸錢)
✅ Contradiction filter (ETF矛盾降級)
✅ ETF flow (機構資金方向)
✅ VIX → sizing multiplier (防止恐慌輸大錢)
✅ Funding extreme (squeeze trigger, conditional)
❌ Max Pain / Put-Call / Deribit details (砍 — 分析師工具)
❌ ML Regime (砍 — 零預測力)
❌ Sentiment (砍 — 永遠 neutral)
❌ On-chain (砍 — scalper 用唔到)
❌ XAUUSD/DXY for crypto (砍 — 相關性不穩定)
"""

import json
import os
import csv
from datetime import datetime, timezone, timedelta

# === Paths ===
WORKSPACE = os.path.expanduser("~/workspace")
MARKET_DATA = os.path.expanduser("~/market_data")
HKT = timezone(timedelta(hours=8))

PATHS = {
    "orderbook": os.path.join(WORKSPACE, "orderbook_data", "orderbook_latest.json"),
    "funding": os.path.join(WORKSPACE, "funding_data", "funding_latest.json"),
    "etf": os.path.join(WORKSPACE, "etf_data", "etf_flow_latest.json"),
    "multi_ob": os.path.join(WORKSPACE, "orderbook_data", "multi_exchange_ob.json"),
    "equities": os.path.join(WORKSPACE, "equities_data", "equities_setups.json"),
}

SYMBOLS = {
    "BTC_USD": "BTC",
    "ETH_USD": "ETH",
    "SOL_USD": "SOL",
}


def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def load_csv_history(safe_name: str) -> list:
    csv_path = os.path.join(MARKET_DATA, f"{safe_name}_history.csv")
    if not os.path.exists(csv_path):
        return []
    try:
        rows = []
        with open(csv_path, "r") as f:
            reader = csv.reader(f)
            header = next(reader)
            for row in reader:
                if len(row) < 5:
                    continue
                rows.append({
                    "date": row[0][:10],
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                })
        return rows[-30:] if len(rows) >= 30 else rows
    except (ValueError, IndexError, OSError):
        return []


# ============================================================
# HELPERS
# ============================================================

def calc_sr_levels(history: list):
    """Support/Resistance from swing points."""
    if not history or len(history) < 5:
        return None, None, "N/A", None, None
    closes = [r["close"] for r in history]
    highs = [r["high"] for r in history]
    lows = [r["low"] for r in history]
    d20_high = max(highs)
    d20_low = min(lows)
    recent_high = max(highs[-10:])
    recent_low = min(lows[-10:])
    first_avg = sum(closes[:10]) / 10 if len(closes) >= 10 else sum(closes) / len(closes)
    second_avg = sum(closes[-10:]) / 10 if len(closes) >= 10 else sum(closes) / len(closes)
    if second_avg > first_avg * 1.01:
        trend = "📈 上升"
    elif second_avg < first_avg * 0.99:
        trend = "📉 下降"
    else:
        trend = "📊 盤整"
    return recent_low, recent_high, trend, d20_low, d20_high


def fmt_price(v, asset=""):
    if v is None:
        return "N/A"
    if v >= 1000:
        return f"${v:,.2f}"
    if v >= 1:
        return f"${v:.2f}"
    return f"${v:.4f}"


def fmt_pct(v):
    if v is None:
        return "N/A"
    return f"{'+' if v > 0 else ''}{v:.2f}%"


# ============================================================
# SECTION BUILDERS
# ============================================================

def build_part0_exec_summary(market: dict, multi_ob: dict, equities_data: dict) -> str:
    """📋 Part 0: Executive Summary — 30-second read."""
    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("**📋 PART 0: 執行摘要**")
    lines.append("")

    # ── Market Regime ──
    regime_parts = []
    # Crypto risk check
    btc_mc = (multi_ob or {}).get("coins", {}).get("BTC", {})
    eth_mc = (multi_ob or {}).get("coins", {}).get("ETH", {})
    if btc_mc.get("consensus") == "sell" and eth_mc.get("consensus") == "sell":
        regime_parts.append("🔴 Crypto Risk-Off")
    elif btc_mc.get("consensus") == "buy" and eth_mc.get("consensus") == "buy":
        regime_parts.append("🟢 Crypto Risk-On")
    else:
        regime_parts.append("🟡 Crypto Mixed")

    # VIX regime
    vix = market.get("VIX", {}).get("close", 0)
    if vix > 30:
        regime_parts.append("🔴 VIX 恐慌")
    elif vix > 25:
        regime_parts.append("⚠️ VIX 偏高")
    elif vix < 15:
        regime_parts.append("⚠️ VIX 自滿")
    else:
        regime_parts.append("✅ VIX 正常")

    # Equities bias
    if equities_data:
        eq_macro = equities_data.get("macro", {})
        eq_bias = eq_macro.get("macro_bias", "N/A")
        regime_parts.append(eq_bias)

    lines.append(f"  市場體制: {' | '.join(regime_parts)}")
    lines.append("")

    # ── VIX Sizing ──
    if vix > 30:
        lines.append("  ⚠️ VIX > 30 — **所有倉位減半 (0.5x)**")
    elif vix > 25:
        lines.append("  ⚠️ VIX > 25 — **倉位 ×0.7**")
    lines.append("")

    # ── Top 3 Setups (cross-market) ──
    all_setups = _collect_all_setups(market, equities_data)
    if all_setups:
        lines.append("**🔥 Top 3 跨市場機會:**")
        for i, s in enumerate(all_setups[:3], 1):
            lines.append(f"  {i}. {s['sym']} {s['side']} — {s['confidence']} | R:R 1:{s['rr']}")
            lines.append(f"     Entry {fmt_price(s['entry'], s['sym'])} | Stop {fmt_price(s['stop'], s['sym'])} | Target {fmt_price(s['target'], s['sym'])}")
        lines.append("")
    else:
        lines.append("  📭 無高確定性交易機會")
        lines.append("")

    return "\n".join(lines)


def _collect_all_setups(market, equities_data) -> list:
    """Collect + sort all setups from crypto + equities."""
    setups = []

    # Crypto setups (generated below, placeholder)
    crypto_setups = _generate_crypto_setups(market)
    setups.extend(crypto_setups)

    # Equities setups
    if equities_data:
        for s in equities_data.get("setups", []):
            setups.append({
                "sym": s["ticker"],
                "side": "📈 LONG" if s.get("direction") == "LONG" else "📉 SHORT",
                "entry": s.get("entry"),
                "stop": s.get("stop"),
                "target": s.get("target"),
                "rr": s.get("rr", 0),
                "confidence": s.get("confidence", "1/5 ⚠️"),
                "signal": s.get("signal", ""),
                "market": "equities",
            })

    # Sort: confidence × rr
    setups.sort(key=lambda s: int(s["confidence"][0]) * 10 + s.get("rr", 0), reverse=True)
    return setups


def _generate_crypto_setups(market: dict) -> list:
    """Generate crypto trade setups (mirrors existing logic, cleaned)."""
    ob = load_json(PATHS["orderbook"])
    funding = load_json(PATHS["funding"])
    etf_data = load_json(PATHS["etf"])
    multi_ob = load_json(PATHS["multi_ob"])

    setups = []
    for sym in ["BTC", "ETH", "SOL"]:
        info = market.get(sym, {})
        price = info.get("close")
        if not price:
            continue

        safe_name = {"BTC": "BTC_USD", "ETH": "ETH_USD", "SOL": "SOL_USD"}[sym]
        history = load_csv_history(safe_name)
        support, resistance, trend, wk_sup, wk_res = calc_sr_levels(history)
        if not support or not resistance:
            continue

        dist_s = abs(price - support) / price * 100
        dist_r = abs(price - resistance) / price * 100

        confluences = []

        # Order book depth (crypto)
        coin_ob = ob.get("coins", {}).get(sym, {})
        bid_vol = float(coin_ob.get("bid_volume_2pct", 0) or 0)
        ask_vol = float(coin_ob.get("ask_volume_2pct", 0) or 0)
        if bid_vol > ask_vol * 1.5:
            confluences.append("🟢 OB買方優勢")
        elif ask_vol > bid_vol * 1.5:
            confluences.append("🔴 OB賣方優勢")

        # Multi-exchange consensus
        mc = multi_ob.get("coins", {}).get(sym, {})
        consensus = mc.get("consensus", "")
        if consensus == "buy":
            confluences.append("🟢 多所買方共識")
        elif consensus == "sell":
            confluences.append("🔴 多所賣方共識")

        # ETF flow
        etf_excess = None
        if sym in ["BTC", "ETH"]:
            etf_key = "btc_etf" if sym == "BTC" else "eth_etf"
            proxy = etf_data.get(etf_key, {}).get("estimated_net_flow_proxy", {})
            etf_excess = proxy.get("estimated_excess_volume")
            if etf_excess and etf_excess > 0:
                confluences.append("🟢 ETF流入")
            elif etf_excess and etf_excess < 0:
                confluences.append("🔴 ETF流出")

        # Funding extreme
        if funding:
            fc = funding.get("funding", {}).get(sym, {})
            fr = fc.get("annualized_pct")
            if fr and fr > 30:
                confluences.append("🔴 費率極端過熱")
            elif fr and fr < -15:
                confluences.append("🟢 費率負值")

        # Trend
        confluences.append(trend)

        # ── LONG setup ──
        if dist_s < 1.0:
            entry = price
            stop = support * 0.97
            target = resistance
            rr = (target - entry) / (entry - stop) if (entry - stop) > 0 else 0

            confidence = _score_crypto_confidence(dist_s, confluences, rr, "上升" in trend, etf_excess, "LONG")

            setups.append({
                "sym": sym, "side": "📈 LONG", "entry": entry,
                "stop": stop, "target": target, "rr": round(rr, 2),
                "confidence": confidence, "confluences": confluences,
                "market": "crypto",
            })

        # ── SHORT setup ──
        elif dist_r < 1.0:
            entry = price
            stop = resistance * 1.03
            target = support
            rr = (entry - target) / (stop - entry) if (stop - entry) > 0 else 0

            confidence = _score_crypto_confidence(dist_r, confluences, rr, "下降" in trend, etf_excess, "SHORT")

            setups.append({
                "sym": sym, "side": "📉 SHORT", "entry": entry,
                "stop": stop, "target": target, "rr": round(rr, 2),
                "confidence": confidence, "confluences": confluences,
                "market": "crypto",
            })

    # Correlation check
    crypto_s = [s for s in setups if s["market"] == "crypto"]
    if len(crypto_s) >= 2:
        sides = set(s["side"] for s in crypto_s)
        if len(sides) == 1:
            for s in crypto_s:
                s["confluences"].append("⚠️ 全幣同向 — 揀最佳")

    return setups


def _score_crypto_confidence(dist: float, confluences: list, rr: float, trend_aligned: bool, etf_excess, side: str) -> str:
    """5-point crypto confidence scoring (P&L-reviewed)."""
    score = 0

    # Entry proximity
    if dist < 1.0:
        score += 1

    # R:R quality
    if rr >= 2.5:
        score += 2
    elif rr >= 1.5:
        score += 1

    # OB + ETF confluence
    ob_etf = len([c for c in confluences if "OB" in c or "ETF" in c or "共識" in c])
    if ob_etf >= 2:
        score += 1
    elif ob_etf >= 1:
        score += 0.5

    # Trend aligned
    if trend_aligned:
        score += 0.5

    # Contradiction: ETF flow opposite direction
    if side == "LONG" and etf_excess is not None and etf_excess < 0:
        return "2/5 ⚡"
    if side == "SHORT" and etf_excess is not None and etf_excess > 0:
        return "2/5 ⚡"

    # Map score
    if score >= 4:
        return "5/5 🔥🔥🔥"
    elif score >= 3:
        return "4/5 🔥🔥"
    elif score >= 2:
        return "3/5 🔥"
    elif score >= 1:
        return "2/5 ⚡"
    return "1/5 ⚠️"


def build_part1_macro(market: dict, equities_data: dict) -> str:
    """🌍 Part 1: Macro Environment."""
    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("**🌍 PART 1: 宏觀環境**")
    lines.append("")

    # VIX
    vix = market.get("VIX", {}).get("close", 0)
    vix_chg = market.get("VIX", {}).get("change_pct")
    if vix:
        if vix > 30:
            regime = "🔴 恐慌 — 清倉觀望"
        elif vix > 25:
            regime = "⚠️ 偏高 — 減倉 ×0.7"
        elif vix < 15:
            regime = "⚠️ 自滿 — 留意反轉"
        else:
            regime = "✅ 正常"
        lines.append(f"  **VIX**: {vix:.1f} ({fmt_pct(vix_chg)}) — {regime}")

    # SPY/QQQ from equities engine
    if equities_data:
        macro = equities_data.get("macro", {})
        spy_p = macro.get("spy_price")
        spy_t = macro.get("spy_trend", "")
        qqq_p = macro.get("qqq_price")
        qqq_t = macro.get("qqq_trend", "")
        eq_bias = macro.get("macro_bias_detail", "")

        lines.append(f"  **SPY**: {fmt_price(spy_p, 'SPY')} — {spy_t}")
        lines.append(f"  **QQQ**: {fmt_price(qqq_p, 'QQQ')} — {qqq_t}")
        lines.append(f"  美股偏向: {eq_bias}")

    lines.append("")
    return "\n".join(lines)


def build_part2_crypto(market: dict) -> str:
    """🪙 Part 2: Crypto Theatre."""
    ob = load_json(PATHS["orderbook"])
    funding = load_json(PATHS["funding"])
    multi_ob_data = load_json(PATHS["multi_ob"])
    etf_data = load_json(PATHS["etf"])
    multi = multi_ob_data.get("coins", {}) if multi_ob_data else {}

    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("**🪙 PART 2: 加密貨幣作戰區**")
    lines.append("")

    # ── Key Levels ──
    lines.append("*關鍵價位*")
    for sym in ["BTC", "ETH", "SOL"]:
        info = market.get(sym, {})
        price = info.get("close")
        chg = info.get("change_pct")

        safe_name = {"BTC": "BTC_USD", "ETH": "ETH_USD", "SOL": "SOL_USD"}[sym]
        history = load_csv_history(safe_name)
        support, resistance, trend, wk_sup, wk_res = calc_sr_levels(history)

        # Order book walls
        coin_ob = ob.get("coins", {}).get(sym, {})
        bid_levels = coin_ob.get("bid_levels", [])
        ask_levels = coin_ob.get("ask_levels", [])
        bid_wall = ""
        ask_wall = ""
        if bid_levels:
            bw = max(bid_levels, key=lambda x: x.get("sz", 0))
            bid_wall = f"🟢 Bid ${bw.get('px', 0):,.0f}({bw.get('sz', 0):.0f})"
        if ask_levels:
            aw = max(ask_levels, key=lambda x: x.get("sz", 0))
            ask_wall = f"🔴 Ask ${aw.get('px', 0):,.0f}({aw.get('sz', 0):.0f})"

        parts = [trend, f"**{sym}**"]
        if price:
            parts.append(fmt_price(price, sym))
        if chg is not None:
            parts.append(f"({fmt_pct(chg)})")
        parts.append(f"S:{fmt_price(support, sym)} R:{fmt_price(resistance, sym)}")
        if bid_wall:
            parts.append(bid_wall)
        if ask_wall:
            parts.append(ask_wall)

        lines.append("  " + "  ".join(parts))
    lines.append("")

    # ── Order Flow (multi-exchange) ──
    lines.append("*多交易所訂單流*")
    for coin in ["BTC", "ETH", "SOL"]:
        mc = multi.get(coin, {})
        consensus = mc.get("consensus", "unknown")
        conf = mc.get("confidence", "unknown")

        if consensus == "sell":
            if conf == "high":
                pressure = "🔴🔴 全所賣壓 (3/3)"
            elif conf == "medium":
                pressure = "🔴 賣方主導 (2/3)"
            else:
                pressure = "🔴 弱賣壓 (1/3)"
        elif consensus == "buy":
            if conf == "high":
                pressure = "🟢🟢 全所買壓 (3/3)"
            elif conf == "medium":
                pressure = "🟢 買方主導 (2/3)"
            else:
                pressure = "🟡 弱買壓 (1/3)"
        else:
            pressure = "🟡 信號分歧"

        # Funding extreme
        fr = None
        if funding:
            fc = funding.get("funding", {}).get(coin, {})
            fr = fc.get("annualized_pct")
        fr_str = ""
        if fr is not None and fr > 30:
            fr_str = "🔴 費率過熱"
        elif fr is not None and fr < -15:
            fr_str = "🟢 費率負值"

        coin_ob = ob.get("coins", {}).get(coin, {})
        mid = coin_ob.get("mid", "")
        parts = [f"**{coin}**", f"${mid:,.2f}" if mid else "", pressure]
        if fr_str:
            parts.append(fr_str)
        lines.append("  " + "  ".join(parts))

    # Risk-Off gate
    btc_mc = multi.get("BTC", {})
    eth_mc = multi.get("ETH", {})
    if btc_mc.get("consensus") == "sell" and btc_mc.get("confidence") == "high" and \
       eth_mc.get("consensus") == "sell" and eth_mc.get("confidence") == "high":
        lines.append("")
        lines.append("  🛑 **Risk-Off：BTC+ETH 全所賣壓 — 今日不宜做多**")
    lines.append("")

    # ── ETF Flow ──
    lines.append("*ETF 資金流*")
    for label, key in [("BTC", "btc_etf"), ("ETH", "eth_etf")]:
        proxy = etf_data.get(key, {}).get("estimated_net_flow_proxy", {})
        excess = proxy.get("estimated_excess_volume")
        if excess is not None:
            direction = "🟢 流入 ↑" if excess > 0 else "🔴 流出 ↓" if excess < 0 else "🟡 中性"
            lines.append(f"  **{label} ETF**: {direction}")
    lines.append("")

    return "\n".join(lines)


def build_part3_equities(equities_data: dict) -> str:
    """📊 Part 3: Equities Theatre."""
    if not equities_data:
        return ""

    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("**📊 PART 3: 美股作戰區**")
    lines.append("")

    macro = equities_data.get("macro", {})
    lines.append(f"  偏向: {macro.get('macro_bias', 'N/A')}")
    lines.append(f"  VIX sizing: {macro.get('risk_multiplier', 1.0):.1f}x")
    lines.append("")

    # Top sectors
    sectors = equities_data.get("sectors", [])
    if sectors:
        lines.append("*最強板塊 (RS vs SPY)*")
        for s in sectors[:3]:
            lines.append(f"  {s['name']} ({s['etf']}): RS {s['rs_vs_spy']:+.1f}% {s.get('trend', '')}")

    lines.append("")

    # Setups
    setups = equities_data.get("setups", [])
    if setups:
        lines.append("*交易設置*")
        for s in setups:
            lines.append(f"  **{s['ticker']} 📈 LONG** — {s.get('confidence', '?')} | {s.get('signal', '')}")
            lines.append(f"  Entry {fmt_price(s['entry'], s['ticker'])} | Stop {fmt_price(s['stop'], s['ticker'])} | Target {fmt_price(s['target'], s['ticker'])} | R:R 1:{s.get('rr', '?')}")
            if s.get("rs_vs_spy"):
                lines.append(f"  RS vs SPY: {s['rs_vs_spy']:+.1f}% | {s.get('trend', '')}")
            if s.get("vix_note"):
                lines.append(f"  {s['vix_note']}")
            lines.append("")
    else:
        lines.append("  📭 今日無美股設置")
        lines.append("")

    # No-trade flags
    no_trade = equities_data.get("no_trade", [])
    if no_trade:
        lines.append("*🚫 禁止開倉*")
        for nt in no_trade:
            lines.append(f"  {nt['ticker']}: {nt['reason']}")
        lines.append("")

    # Earnings risk
    earnings = equities_data.get("earnings_risk", [])
    if earnings:
        lines.append("*📅 本週財報風險*")
        for er in earnings:
            lines.append(f"  {er['ticker']}: {er['date']}")
        lines.append("")

    return "\n".join(lines)


def build_part4_trade_setups(all_setups: list) -> str:
    """🎯 Part 4: Unified Trade Setups."""
    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("**🎯 PART 4: 統一交易設置**")
    lines.append("")

    if not all_setups:
        lines.append("  📭 今日無明確交易機會 — 等待突破確認")
        lines.append("")
        return "\n".join(lines)

    # Show all, sorted by confidence × R:R
    for s in all_setups:
        market_tag = "🪙" if s.get("market") == "crypto" else "📊"
        lines.append(f"  {market_tag} **{s['sym']} {s['side']}** — {s['confidence']}")
        lines.append(f"  Entry {fmt_price(s['entry'], s['sym'])} | Stop {fmt_price(s['stop'], s['sym'])} | Target {fmt_price(s['target'], s['sym'])}")
        lines.append(f"  R:R 1:{s['rr']} | Signal: {s.get('signal', 'S/R bounce')}")

        # Confluences for crypto
        if s.get("confluences"):
            lines.append(f"  └ {' · '.join(s['confluences'][:3])}")
        lines.append("")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    now = datetime.now(HKT)

    # Load data
    ob = load_json(PATHS["orderbook"])
    funding = load_json(PATHS["funding"])
    etf_data = load_json(PATHS["etf"])
    multi_ob_data = load_json(PATHS["multi_ob"])
    equities_data = load_json(PATHS["equities"])

    # Build market dict
    market = {}
    for safe_name, label in SYMBOLS.items():
        csv_path = os.path.join(MARKET_DATA, f"{safe_name}_history.csv")
        if not os.path.exists(csv_path):
            continue
        try:
            with open(csv_path, "r") as f:
                rows = list(csv.reader(f))
                if len(rows) < 2:
                    continue
                last_row = rows[-1]
                close = float(last_row[4]) if len(last_row) > 4 else None
                close_prev = float(rows[-2][4]) if len(rows) > 2 and len(rows[-2]) > 4 else None
                change_pct = ((close - close_prev) / close_prev * 100) if close and close_prev else None
                market[label] = {
                    "close": close,
                    "change_pct": round(change_pct, 2) if change_pct else None,
                }
        except (ValueError, IndexError, OSError):
            continue

    # VIX
    vix_path = os.path.join(MARKET_DATA, "VIX_history.csv")
    if os.path.exists(vix_path):
        try:
            with open(vix_path, "r") as f:
                rows = list(csv.reader(f))
                if len(rows) >= 2:
                    close = float(rows[-1][4]) if len(rows[-1]) > 4 else None
                    close_prev = float(rows[-2][4]) if len(rows) > 2 and len(rows[-2]) > 4 else None
                    change_pct = ((close - close_prev) / close_prev * 100) if close and close_prev else None
                    market["VIX"] = {
                        "close": close,
                        "change_pct": round(change_pct, 2) if change_pct else None,
                    }
        except (ValueError, IndexError, OSError):
            pass

    # Collect all setups
    all_setups = _collect_all_setups(market, equities_data)

    # Build briefing
    parts = []
    parts.append(f"⚔️ **Commander's Briefing**")
    parts.append(f"📅 {now.strftime('%Y-%m-%d %A')} ｜ {now.strftime('%H:%M')} HKT")
    parts.append("")

    # Part 0: Executive Summary
    parts.append(build_part0_exec_summary(market, multi_ob_data, equities_data))

    # Part 1: Macro
    parts.append(build_part1_macro(market, equities_data))

    # Part 2: Crypto
    parts.append(build_part2_crypto(market))

    # Part 3: Equities
    eq_section = build_part3_equities(equities_data)
    if eq_section:
        parts.append(eq_section)

    # Part 4: Unified Setups
    parts.append(build_part4_trade_setups(all_setups))

    # Footer
    parts.append("━━━━━━━━━━━━━━━━━━━━━━")
    parts.append("🤖 Hermes Agent · Commander's Briefing")
    parts.append(f"⚡ {now.strftime('%H:%M HKT')}")
    parts.append("")
    parts.append("📡 Crypto: Multi-exchange OB + ETF flow | Equities: RS + Squeeze + Earnings gate")
    parts.append("🛡️ Defense: Risk-Off gate · Contradiction filter · VIX sizing · Earnings block")

    print("\n".join(parts))


if __name__ == "__main__":
    main()
