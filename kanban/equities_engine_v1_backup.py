#!/usr/bin/env python3
"""
EQUITIES ENGINE V2 — Dynamic Screener 美股引擎
================================================
純 script，零 token。專為 Firstrade 冇槓桿 long-only position trade 設計。

兩層漏斗：
  Layer 1: 100 隻流動大股 batch download → RS vs SPY ranking → Top 20
  Layer 2: Top 20 deep dive（squeeze + pullback + earnings + 信心評分）

自動跟市場輪動，唔使人手 update 名單。

輸出 equities_setups.json
"""

import json
import os
import tempfile
import time
from datetime import datetime, timezone, timedelta

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("❌ yfinance not installed. Run: pip install yfinance pandas")
    raise

# === Config ===
HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
EQUITIES_DIR = os.path.join(WORKSPACE, "equities_data")
os.makedirs(EQUITIES_DIR, exist_ok=True)

OUTPUT_PATH = os.path.join(EQUITIES_DIR, "equities_setups.json")

# Layer 1: 100 liquid large-cap universe
# S&P 100 + key mega-caps + liquid growth names
UNIVERSE = [
    # Mega-cap Tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    # Semiconductors
    "AMD", "AVGO", "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX", "ADI",
    # Cloud / SaaS
    "CRM", "ADBE", "NOW", "SNOW", "ORCL", "INTU", "PLTR", "NET", "DDOG", "CRWD", "ZS",
    # Internet / Media
    "NFLX", "SPOT", "SNAP", "PINS", "RBLX", "UBER", "ABNB", "DASH",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BLK", "SCHW",
    # Healthcare
    "LLY", "UNH", "JNJ", "PFE", "ABBV", "MRK", "TMO", "ISRG", "VRTX",
    # Consumer / Retail
    "COST", "HD", "WMT", "NKE", "SBUX", "LULU", "CMG",
    # Industrial / Energy
    "BA", "CAT", "GE", "RTX", "LMT", "XOM", "CVX", "COP",
    # EV / Auto
    "RIVN", "LCID",
    # Other liquid names
    "DIS", "PYPL", "COIN", "HOOD", "ARM", "MRVL", "DELL", "SMCI",
]

TOP_N = 20  # Deep dive top N by RS
MIN_RS = -5  # Minimum RS to be considered (filter out deep losers)

# Sector ETFs
SECTORS = {
    "科技": "XLK", "半導體": "SMH", "通訊": "XLC",
    "消費": "XLY", "金融": "XLF", "工業": "XLI",
    "能源": "XLE", "醫療": "XLV",
}

# Stock → Sector mapping for individual stock classification
STOCK_SECTOR = {
    "AAPL": "科技", "MSFT": "科技", "NVDA": "半導體", "GOOGL": "通訊", "AMZN": "消費",
    "META": "通訊", "TSLA": "消費", "AMD": "半導體", "AVGO": "半導體", "INTC": "半導體",
    "QCOM": "半導體", "TXN": "半導體", "MU": "半導體", "AMAT": "半導體", "LRCX": "半導體",
    "ADI": "半導體", "MRVL": "半導體", "ARM": "半導體", "DELL": "科技", "SMCI": "科技",
    "CRM": "科技", "ADBE": "科技", "NOW": "科技", "SNOW": "科技", "ORCL": "科技",
    "INTU": "科技", "PLTR": "科技", "NET": "科技", "DDOG": "科技", "CRWD": "科技",
    "ZS": "科技", "NFLX": "通訊", "SPOT": "通訊", "SNAP": "通訊", "PINS": "通訊",
    "RBLX": "通訊", "UBER": "科技", "ABNB": "科技", "DASH": "科技",
    "JPM": "金融", "BAC": "金融", "WFC": "金融", "GS": "金融", "MS": "金融",
    "V": "金融", "MA": "金融", "AXP": "金融", "BLK": "金融", "SCHW": "金融",
    "LLY": "醫療", "UNH": "醫療", "JNJ": "醫療", "PFE": "醫療", "ABBV": "醫療",
    "MRK": "醫療", "TMO": "醫療", "ISRG": "醫療", "VRTX": "醫療",
    "COST": "消費", "HD": "消費", "WMT": "消費", "NKE": "消費", "SBUX": "消費",
    "LULU": "消費", "CMG": "消費", "DIS": "通訊", "PYPL": "金融", "COIN": "金融",
    "HOOD": "金融", "BA": "工業", "CAT": "工業", "GE": "工業", "RTX": "工業",
    "LMT": "工業", "XOM": "能源", "CVX": "能源", "COP": "能源",
    "RIVN": "消費", "LCID": "消費",
}
BENCHMARKS = ["SPY", "QQQ"]

# === Helpers ===

def batch_fetch(tickers: list, days: int = 120) -> dict:
    """Batch download multiple tickers at once. Returns {ticker: {closes, highs, lows, last_price}}."""
    if not tickers:
        return {}
    
    ticker_str = " ".join(tickers)
    try:
        df = yf.download(ticker_str, period=f"{days}d", interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            return {}
        
        results = {}
        for t in tickers:
            try:
                if isinstance(df.columns, pd.MultiIndex):
                    if ('Close', t) in df.columns:
                        closes = df[('Close', t)].dropna().tolist()
                        highs = df[('High', t)].dropna().tolist()
                        lows = df[('Low', t)].dropna().tolist()
                    else:
                        continue
                else:
                    closes = df["Close"].dropna().tolist()
                    highs = df["High"].dropna().tolist()
                    lows = df["Low"].dropna().tolist()
                
                if len(closes) >= 20:
                    results[t] = {
                        "ticker": t,
                        "closes": closes,
                        "highs": highs,
                        "lows": lows,
                        "last_price": closes[-1],
                    }
            except Exception:
                continue
        return results
    except Exception as e:
        print(f"  ⚠️ Batch fetch failed: {e}")
        return {}


def calc_sma(values: list, period: int) -> float:
    if len(values) < period:
        return 0
    return sum(values[-period:]) / period


def calc_atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0
    trs = []
    for i in range(1, min(len(closes), period + 5)):
        tr = max(highs[-i] - lows[-i], abs(highs[-i] - closes[-i-1]), abs(lows[-i] - closes[-i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / period if trs else 0


def calc_bollinger(closes: list, period: int = 20) -> dict:
    if len(closes) < period:
        return {}
    recent = closes[-period:]
    sma = sum(recent) / period
    variance = sum((x - sma) ** 2 for x in recent) / period
    std = variance ** 0.5
    return {"sma": sma, "upper": sma + 2*std, "lower": sma - 2*std, "width_pct": (2*std/sma*100) if sma > 0 else 0}


def calc_rs(stock_closes: list, bench_closes: list) -> float:
    """Relative Strength: stock % change / benchmark % change over aligned period."""
    if not stock_closes or not bench_closes:
        return 0
    min_len = min(len(stock_closes), len(bench_closes))
    if min_len < 2:
        return 0
    s_ratio = stock_closes[-1] / stock_closes[-min_len] if stock_closes[-min_len] > 0 else 1
    b_ratio = bench_closes[-1] / bench_closes[-min_len] if bench_closes[-min_len] > 0 else 1
    return round((s_ratio / b_ratio - 1) * 100, 1) if b_ratio > 0 else 0


def calc_trend(closes: list) -> str:
    if len(closes) < 20:
        return "N/A"
    sma10 = sum(closes[-10:]) / 10
    sma20 = sum(closes[-20:]) / 20
    if sma10 > sma20 * 1.01:
        return "📈 上升"
    elif sma10 < sma20 * 0.99:
        return "📉 下降"
    return "📊 盤整"


def detect_squeeze(closes: list, highs: list, lows: list) -> dict:
    if len(closes) < 40:
        return None
    bb = calc_bollinger(closes)
    if not bb:
        return None
    price = closes[-1]
    bb_width = bb["width_pct"]
    old_closes = closes[-40:-20]
    old_bb = calc_bollinger(old_closes)
    if not old_bb or bb_width >= old_bb["width_pct"] * 0.8:
        return None
    if price <= bb["sma"]:
        return None
    atr_val = calc_atr(highs, lows, closes)
    if atr_val <= 0:
        return None
    entry = price
    atr_stop = entry - atr_val * 2
    pct_stop = entry * 0.97
    stop = max(atr_stop, pct_stop)
    target = max(entry + atr_val * 3, entry + (entry - stop) * 1.5)
    rr = (target - entry) / (entry - stop) if (entry - stop) > 0 else 0
    if rr < 1.5:
        return None
    return {"signal": "squeeze_breakout", "direction": "LONG", "entry": round(entry, 2),
            "stop": round(stop, 2), "target": round(target, 2),
            "rr": round(rr, 2), "bb_width": round(bb_width, 2), "atr": round(atr_val, 2)}


def detect_pullback(closes: list, highs: list, lows: list) -> dict:
    if len(closes) < 20:
        return None
    price = closes[-1]
    sma20 = sum(closes[-20:]) / 20
    sma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else price
    if sma10 < sma20:
        return None
    if abs(price - sma20) / sma20 * 100 > 3:
        return None
    if len(closes) >= 3 and closes[-1] > closes[-3]:
        return None
    atr_val = calc_atr(highs, lows, closes)
    atr_stop = entry - atr_val * 2 if (entry := price) else 0
    pct_stop = entry * 0.97
    sma_stop = sma20 * 0.97
    stop = max(atr_stop, pct_stop, sma_stop)
    target = max(entry + atr_val * 3, entry + (entry - stop) * 1.5)
    rr = (target - entry) / (entry - stop) if (entry - stop) > 0 else 0
    if rr < 1.5:
        return None
    return {"signal": "pullback", "direction": "LONG", "entry": round(entry, 2),
            "stop": round(stop, 2), "target": round(target, 2),
            "rr": round(rr, 2), "atr": round(atr_val, 2)}


def score_confidence(setup: dict, rs: float, trend: str) -> str:
    score = 0
    rr = setup.get("rr", 0)
    signal = setup.get("signal", "")
    if rr >= 2.5:
        score += 2
    elif rr >= 1.5:
        score += 1
    else:
        return "1/5 ⚠️"
    if rs > 15:
        score += 3
    elif rs > 10:
        score += 2
    elif rs > 5:
        score += 1
    elif rs > 0:
        score += 0.5
    elif rs < -5:
        score -= 2
    elif rs < 0:
        score -= 1
    if "上升" in trend:
        score += 1
    elif "下降" in trend:
        score -= 1
    if signal == "squeeze_breakout":
        score += 0.5
    score = max(0, min(5, score))
    if score >= 4:
        return "5/5 🔥🔥🔥"
    elif score >= 3:
        return "4/5 🔥🔥"
    elif score >= 2:
        return "3/5 🔥"
    elif score >= 1:
        return "2/5 ⚡"
    return "1/5 ⚠️"


def get_earnings_calendar(tickers: list) -> dict:
    earnings_risk = {}
    now = datetime.now()
    week_end = now + timedelta(days=7)
    for t in tickers:
        try:
            stock = yf.Ticker(t)
            cal = stock.calendar
            if cal is None or cal.empty:
                continue
            ed_list = cal.get("Earnings Date", [])
            if ed_list is not None and len(ed_list) > 0:
                ed = ed_list[0]
                if isinstance(ed, datetime) and now <= ed <= week_end:
                    earnings_risk[t] = ed.strftime("%Y-%m-%d")
        except Exception:
            continue
    return earnings_risk


# ============================================================
# MAIN ENGINE
# ============================================================

def run():
    t0 = time.time()
    print("=" * 60)
    print("📊 EQUITIES ENGINE V2 — Dynamic Screener")
    print(f"   {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    print(f"   Universe: {len(UNIVERSE)} stocks → Top {TOP_N} RS → deep dive")
    print("=" * 60)

    output = {"timestamp": datetime.now(HKT).isoformat(), "macro": {}, "sectors": [],
              "rs_ranking": [], "setups": [], "no_trade": [], "earnings_risk": []}

    # ── Step 1: Macro + Benchmarks ────────────────────────────────
    print("\n📡 Fetching macro & benchmarks...")
    bench_data = batch_fetch(BENCHMARKS, days=120)
    spy = bench_data.get("SPY", {})
    qqq = bench_data.get("QQQ", {})
    spy_closes = spy.get("closes", [])
    
    vix_data = batch_fetch(["^VIX"], days=60)
    vix = vix_data.get("^VIX", {}).get("last_price", 0) if vix_data else 0

    spy_trend = calc_trend(spy_closes) if spy_closes else "N/A"
    qqq_trend = calc_trend(qqq.get("closes", [])) if qqq else "N/A"

    if vix > 30:
        vix_regime, risk_multiplier = "🔴 恐慌", 0.5
    elif vix > 25:
        vix_regime, risk_multiplier = "⚠️ 偏高", 0.7
    elif vix < 15:
        vix_regime, risk_multiplier = "⚠️ 自滿", 0.8
    else:
        vix_regime, risk_multiplier = "✅ 正常", 1.0

    if "上升" in spy_trend and "上升" in qqq_trend:
        macro_bias = "🟢 積極做多"
        detail = "SPY+QQQ 雙雙上升"
    elif "下降" in spy_trend and "下降" in qqq_trend:
        macro_bias = "🔴 現金為王"
        detail = "SPY+QQQ 雙雙下降"
    else:
        macro_bias = "🟡 謹慎選股"
        detail = "大市分化"

    output["macro"] = {"spy_price": spy.get("last_price"), "spy_trend": spy_trend,
                       "qqq_price": qqq.get("last_price"), "qqq_trend": qqq_trend,
                       "vix": round(vix, 1) if vix else None, "vix_regime": vix_regime,
                       "risk_multiplier": risk_multiplier, "macro_bias": macro_bias,
                       "macro_bias_detail": detail}
    print(f"  SPY ${spy.get('last_price', '?')} {spy_trend} | QQQ ${qqq.get('last_price', '?')} {qqq_trend}")
    print(f"  VIX {vix:.1f} — {vix_regime} | Bias: {macro_bias}")

    # ── Step 2: Sector RS ─────────────────────────────────────────
    print("\n📊 Sector RS...")
    sector_tickers = list(SECTORS.values())
    sector_data = batch_fetch(sector_tickers, days=120)
    sector_list = []
    for name, etf in SECTORS.items():
        d = sector_data.get(etf, {})
        if d and spy_closes:
            rs = calc_rs(d["closes"], spy_closes)
            sector_list.append({"name": name, "etf": etf, "price": d.get("last_price"),
                                "rs_vs_spy": rs, "trend": calc_trend(d["closes"])})
    sector_list.sort(key=lambda x: x["rs_vs_spy"], reverse=True)
    output["sectors"] = sector_list[:3]
    for s in output["sectors"]:
        print(f"  {s['name']}: RS {s['rs_vs_spy']:+.1f}% {s['trend']}")

    # ── Step 3: Layer 1 — Batch scan universe + RS ranking ─────────
    print(f"\n🔍 Layer 1: Scanning {len(UNIVERSE)} stocks...")
    all_data = batch_fetch(UNIVERSE, days=120)
    print(f"   Fetched {len(all_data)} / {len(UNIVERSE)} stocks ({time.time()-t0:.1f}s)")

    # Calculate RS + filter
    ranked = []
    for t, d in all_data.items():
        if not spy_closes:
            continue
        rs = calc_rs(d["closes"], spy_closes)
        trend = calc_trend(d["closes"])
        price = d["last_price"]
        if rs >= MIN_RS and "下降" not in trend:
            ranked.append({"ticker": t, "rs": rs, "trend": trend, "price": price, "data": d})
    
    ranked.sort(key=lambda x: x["rs"], reverse=True)
    output["rs_ranking"] = [{"ticker": r["ticker"], "rs": r["rs"], "trend": r["trend"],
                             "price": r["price"]} for r in ranked[:TOP_N]]

    print(f"\n📊 RS Ranking (Top {TOP_N}):")
    for r in ranked[:TOP_N]:
        print(f"  {r['ticker']:6s} RS {r['rs']:+.1f}% | ${r['price']:.2f} | {r['trend']}")

    # ── Step 4: Earnings risk (only check top N + margin) ──────────
    top_tickers = [r["ticker"] for r in ranked[:TOP_N+10]]
    all_check = list(set(top_tickers + ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]))
    print(f"\n📅 Earnings check ({len(all_check)} tickers)...")
    earnings_risk = get_earnings_calendar(all_check)
    output["earnings_risk"] = [{"ticker": t, "date": d} for t, d in earnings_risk.items()]
    if output["earnings_risk"]:
        for er in output["earnings_risk"]:
            print(f"  ⚠️ {er['ticker']} 財報 {er['date']}")

    # ── Step 5: Layer 2 — Deep dive top N ──────────────────────────
    print(f"\n🔬 Layer 2: Deep dive top {TOP_N}...")
    for r in ranked[:TOP_N]:
        t = r["ticker"]
        d = r["data"]
        
        if t in earnings_risk:
            output["no_trade"].append({"ticker": t, "reason": f"財報 ({earnings_risk[t]})", "price": r["price"]})
            print(f"  🚫 {t} — 財報禁止")
            continue
        
        setup = detect_squeeze(d["closes"], d["highs"], d["lows"])
        if not setup:
            setup = detect_pullback(d["closes"], d["highs"], d["lows"])
        
        if setup:
            setup["ticker"] = t
            setup["price"] = round(r["price"], 2)
            setup["rs_vs_spy"] = r["rs"]
            setup["trend"] = r["trend"]
            setup["sector"] = STOCK_SECTOR.get(t, "Other")
            setup["reason"] = f"{setup['signal']} | RS vs SPY {r['rs']:+.1f}% | R:R 1:{setup.get('rr',0):.1f} | BB width {setup.get('bb_width',0):.1f}%"
            setup["confidence"] = score_confidence(setup, r["rs"], r["trend"])
            if risk_multiplier < 1.0:
                setup["vix_note"] = f"VIX {vix_regime} — ×{risk_multiplier:.1f}"
            if int(setup["confidence"][0]) >= 2:
                output["setups"].append(setup)
                print(f"  ✅ {t:6s} ${r['price']:.2f} — {setup['confidence']} | {setup['signal']} | R:R 1:{setup['rr']} | RS {r['rs']:+.1f}%")
            else:
                print(f"  ⚪ {t:6s} ${r['price']:.2f} — {setup['confidence']} 信心不足")
        else:
            print(f"  ⚪ {t:6s} ${r['price']:.2f} — {r['trend']} (無 setup)")

    # ── Sort & Save ────────────────────────────────────────────────
    output["setups"].sort(key=lambda s: int(s["confidence"][0]) * 10 + s.get("rr", 0), reverse=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUTPUT_PATH), suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, OUTPUT_PATH)

    elapsed = time.time() - t0
    print(f"\n💾 Saved: {OUTPUT_PATH}")
    print(f"   {len(output['setups'])} setups | {len(output['no_trade'])} blocked | Top RS: {ranked[0]['ticker']} ({ranked[0]['rs']:+.1f}%)" if ranked else "   No stocks found")
    print(f"   ⏱️ {elapsed:.1f}s total")
    print("=" * 60)
    return output


if __name__ == "__main__":
    run()
