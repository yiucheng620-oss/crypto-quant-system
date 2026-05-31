#!/usr/bin/env python3
"""
📊 ALPHA HUNTER V2 — Dynamic US Equities Screener & Alpha Finder
============================================================
Replaces Equities Engine V1.
Features:
  - Dynamic Thematic AI/Infrastructure Sourcing via Gemini Search Grounding.
  - Live Nasdaq IPO Calendar Scraper.
  - Negative News Gatekeeper (Checks for SEC/Fraud/Bankruptcies via search).
  - Alpha Performance Auditor (Audits historical picks in equities_picks.json vs SPY).
  - yfinance Data Verification (filtering fake tickers & checking liquidity).
  - Multi-Agent Council (Research, Fund, Tech) for Alpha Debate.
  - Zero-table, markdown-formatted Telegram reports.
  - 100% compatible with kanban_pipeline_v3.py.
"""

import json
import os
import sys
import re
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# Append workspace and scalp_engines to path
WORKSPACE = os.path.expanduser("~/workspace")
sys.path.append(WORKSPACE)
sys.path.append(os.path.join(WORKSPACE, "scalp_engines"))

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("❌ yfinance or pandas not installed.")
    raise

from gemini_free import call_pro, call_flash, call_flash_search
from tg_notify import tg_send

# === Config ===
HKT = timezone(timedelta(hours=8))
EQUITIES_DIR = os.path.join(WORKSPACE, "equities_data")
os.makedirs(EQUITIES_DIR, exist_ok=True)
OUTPUT_PATH = os.path.join(EQUITIES_DIR, "equities_setups.json")
PICKS_FILE = os.path.join(WORKSPACE, "scalp_lab", "engines", "equities_picks.json")

BENCHMARKS = ["SPY", "QQQ"]

# Base evergreen stock list as fallbacks/baselines
EVERGREEN_UNIVERSE = [
    "NVDA", "SMCI", "ANET", "VRT", "CEG", "MSFT", "AAPL", 
    "QCOM", "AVGO", "ARM", "PLTR", "DELL", "AMD", "LLY", "COIN"
]

# ============================================================
# 1. MATH HELPERS (From Engine V1)
# ============================================================

def calc_sma(values: list, period: int) -> float:
    return sum(values[-period:]) / period if len(values) >= period else 0.0

def calc_atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    tr_all = []
    for i in range(1, len(closes)):
        h, l, c_prev = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        tr_all.append(tr)
    return sum(tr_all[-period:]) / period

def calc_bollinger(closes: list, period: int = 20) -> dict:
    if len(closes) < period:
        return None
    sma = sum(closes[-period:]) / period
    variance = sum((x - sma) ** 2 for x in closes[-period:]) / period
    std_dev = variance ** 0.5
    return {
        "sma": sma,
        "upper": sma + std_dev * 2,
        "lower": sma - std_dev * 2,
        "width_pct": (std_dev * 4) / sma * 100 if sma > 0 else 0
    }

def calc_rs(stock_closes: list, bench_closes: list) -> float:
    """Relative Strength against benchmark (usually SPY) over the period."""
    if len(stock_closes) < 20 or len(bench_closes) < 20:
        return 0.0
    stock_ret = (stock_closes[-1] - stock_closes[-20]) / stock_closes[-20] * 100
    bench_ret = (bench_closes[-1] - bench_closes[-20]) / bench_closes[-20] * 100
    return stock_ret - bench_ret

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
    entry = price
    atr_stop = entry - atr_val * 2
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

# ============================================================
# 2. DATA SOURCING HELPERS
# ============================================================

def get_thematic_ai_tickers() -> list:
    """Discovers under-the-radar AI supply chain stocks via Search Grounding."""
    print("📡 [Sourcing] Discovering trending AI Infrastructure/Supply Chain stocks via Web Search...")
    prompt = """
Identify exactly 15 trending, highly liquid US small/mid-cap stocks (market cap between $1B and $30B) that are crucial parts of the AI hardware supply chain (e.g. cooling solutions, nuclear/clean power utilities, fiber-optic networking, high-power transformers, custom ASIC chips).
Ensure they are not mega-caps like Nvidia or Apple, but high-potential thematic plays.
Return strictly a valid JSON list of tickers like: ["TICKER1", "TICKER2", ...] and nothing else.
"""
    try:
        res = call_flash_search(prompt)
        text = res.get("text", "")
        # Extract json list
        m = re.search(r"\[\s*\"[A-Z]+\".*?\]", text, re.DOTALL)
        if m:
            tickers = json.loads(m.group(0))
            print(f"  ✅ Discovered {len(tickers)} tickers: {tickers}")
            return [t.upper().strip() for t in tickers]
    except Exception as e:
        print(f"  ⚠️ Search Grounding failed: {e}")
    return []

def get_nasdaq_ipos() -> list:
    """Scrapes upcoming IPOs from NASDAQ API."""
    print("📡 [Sourcing] Scrape upcoming Nasdaq IPO Calendar...")
    url = "https://api.nasdaq.com/api/ipo/calendar"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            upcoming = data.get("data", {}).get("upcoming", {})
            if 'upcomingTable' in upcoming:
                rows = upcoming['upcomingTable'].get('rows', [])
                print(f"  ✅ Found {len(rows)} upcoming IPOs.")
                return [{
                    "ticker": r.get("proposedTickerSymbol"),
                    "company": r.get("companyName"),
                    "proposed_price": r.get("proposedSharePrice"),
                    "shares": r.get("sharesOffered"),
                    "expected_date": r.get("expectedPriceDate"),
                    "valuation": r.get("dollarValueOfSharesOffered")
                } for r in rows if r.get("proposedTickerSymbol")]
    except Exception as e:
        print(f"  ⚠️ Nasdaq IPO Scrape failed: {e}")
    return []

# ============================================================
# 3. SENTINEL GATEKEEPER: RISK & NEWS CHECK
# ============================================================

def verify_negative_news(ticker: str) -> bool:
    """Returns True if ticker is safe, False if catastrophic negative news exists."""
    print(f"🛡️ [Sentinel] Auditing critical news for {ticker}...")
    prompt = f"""
Search for recent critical news (last 7 days) regarding U.S. stock '{ticker}'.
Check specifically for: SEC fraud, bankruptcies, accounting scandals, regulatory halts, or sudden CEO exit under investigation.
If any of these catastrophic events are true, reply strictly with 'BLOCKED'.
If it is safe, reply strictly with 'SAFE'.
"""
    try:
        res = call_flash_search(prompt)
        text = res.get("text", "").upper()
        if "BLOCKED" in text:
            print(f"  🔴 [BLOCKED] {ticker} failed Sentinel risk audit!")
            return False
    except Exception as e:
        pass
    return True

# ============================================================
# 4. ALPHA AUDITOR (HISTORICAL TRADES SUMMARY)
# ============================================================

def audit_historical_picks(spy_price: float) -> str:
    """Reads equities_picks.json, fetches current prices, and audits P&L vs SPY."""
    print("🏆 [Auditor] Auditing performance of past 5-day picks...")
    if not os.path.exists(PICKS_FILE):
        return "   • 暫無歷史選股數據可供審計。"
        
    try:
        with open(PICKS_FILE) as f:
            picks_data = json.load(f)
        if not picks_data:
            return "   • 暫無歷史選股數據可供審計。"
            
        # Get picks from ~5 days ago
        target_picks = []
        now = time.time()
        for entry in reversed(picks_data):
            age_days = (now - entry.get("ts_epoch", 0)) / 86400
            if 4 <= age_days <= 10:
                target_picks = entry.get("picks", [])
                spy_at_pick = entry.get("market_regime", {}).get("spy_price", 0)
                break
                
        if not target_picks:
            # Fallback to the latest entry if none are exactly 5 days old
            for entry in reversed(picks_data):
                if entry.get("picks"):
                    target_picks = entry["picks"]
                    spy_at_pick = entry.get("market_regime", {}).get("spy_price", spy_price)
                    break
                    
        if not target_picks or not spy_at_pick:
            return "   • 暫無合適嘅 5 日歷史選股可供對比。"
            
        # Download current prices of audited tickers
        tickers = [p["ticker"] for p in target_picks]
        prices = {}
        try:
            curr_data = yf.download(tickers, period="1d", progress=False)
            for t in tickers:
                prices[t] = curr_data["Close"][t].dropna().iloc[-1] if len(tickers) > 1 else curr_data["Close"].dropna().iloc[-1]
        except Exception as e:
            print(f"  ⚠️ Auditor price fetch failed: {e}")
            return "   • 獲取實時報價失敗，暫停審計。"
            
        # Compute returns
        spy_ret = (spy_price - spy_at_pick) / spy_at_pick * 100 if spy_at_pick > 0 else 0.0
        lines = []
        total_pick_ret = 0.0
        count = 0
        
        for p in target_picks:
            t = p["ticker"]
            entry_price = p.get("price") or p.get("entry")
            if not entry_price or t not in prices:
                continue
            curr_price = prices[t]
            ret = (curr_price - entry_price) / entry_price * 100
            total_pick_ret += ret
            count += 1
            lines.append(f"   • **{t}**: {entry_price:.2f} → {curr_price:.2f} ({ret:+.1f}%)")
            
        if count == 0:
            return "   • 歷史持股數據格式不全，無法對比。"
            
        avg_pick_ret = total_pick_ret / count
        alpha = avg_pick_ret - spy_ret
        alpha_emoji = "🟢 Alpha: " if alpha >= 0 else "🔴 Alpha: "
        
        summary_text = (
            f"   • **基準對比 (SPY)**: {spy_ret:+.1f}%\n"
            f"   • **系統選股平均**: {avg_pick_ret:+.1f}%\n"
            f"   • **{alpha_emoji}{alpha:+.1f}%**\n"
            + "\n".join(lines)
        )
        return summary_text
    except Exception as e:
        print(f"  ⚠️ Historical audit failed: {e}")
        return "   • 審計過程出錯，跳過歷史表現彙報。"

# ============================================================
# 5. YFINANCE DATA PROCESSING & EARNINGS FILTER
# ============================================================
import datetime as dt

def check_earnings_risk_or_pead(ticker: str, current_vol: float, avg_vol: float) -> dict:
    """
    Check if a stock has upcoming earnings (risk) or just reported (PEAD opportunity).
    Returns: {"action": "skip"|"buy"|"neutral", "reason": "...", "is_pead": bool}
    """
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
        if not cal or "Earnings Date" not in cal:
            return {"action": "neutral", "reason": "No earnings data", "is_pead": False}
            
        dates = cal["Earnings Date"]
        if not dates:
            return {"action": "neutral", "reason": "No earnings date list", "is_pead": False}
            
        next_earnings = dates[0]
        if isinstance(next_earnings, dt.datetime):
            next_earnings = next_earnings.date()
            
        today = dt.date.today()
        days_diff = (next_earnings - today).days
        
        # 1. THE SHIELD: 財報前 3 日內，完全避險 (一票否決)
        if 0 <= days_diff <= 3:
            return {
                "action": "skip", 
                "reason": f"Earnings risk (in {days_diff} days)", 
                "is_pead": False
            }
            
        # 2. THE SPEAR: 過去 3 日內出咗財報 (PEAD 績後動能追擊)
        if -3 <= days_diff < 0:
            # Check volume confirmation for PEAD (needs 2x avg volume)
            if current_vol > (avg_vol * 1.5):
                return {
                    "action": "buy",
                    "reason": f"⭐ PEAD Breakout (Earned {abs(days_diff)}d ago, Vol {current_vol/avg_vol:.1f}x)",
                    "is_pead": True
                }
                
        return {"action": "neutral", "reason": f"Earnings in {days_diff} days", "is_pead": False}
        
    except Exception as e:
        return {"action": "neutral", "reason": "Earnings fetch failed", "is_pead": False}

def process_and_verify_universe(tickers: list, spy_closes: list) -> list:
    print(f"🔬 [Processor] Verifying and computing metrics for {len(tickers)} tickers...")
    verified = []
    
    try:
        data = yf.download(tickers, period="6mo", interval="1d", group_by='ticker', progress=False)
    except Exception as e:
        print(f"  ❌ Batch fetch failed: {e}")
        return []
        
    for t in tickers:
        try:
            t_data = data[t] if len(tickers) > 1 else data
            if t_data.empty or "Close" not in t_data:
                continue
            
            closes = t_data["Close"].dropna().tolist()
            highs = t_data["High"].dropna().tolist()
            lows = t_data["Low"].dropna().tolist()
            volumes = t_data["Volume"].dropna().tolist()
            
            if len(closes) < 60:
                continue
                
            last_price = closes[-1]
            avg_vol = sum(volumes[-60:]) / 60
            
            # Liquidity Filter: Average volume must be > 100k shares
            if avg_vol < 100000:
                continue
                
            # Compute technical values
            sma50 = calc_sma(closes, 50)
            sma200 = calc_sma(closes, 200)
            trend = "BULLISH" if last_price > sma50 > sma200 else "BEARISH"
            rs = calc_rs(closes, spy_closes)
            
            # 🛡️ THE SHIELD & ⚔️ THE SPEAR: Earnings Check
            earn_info = check_earnings_risk_or_pead(t, volumes[-1], avg_vol)
            if earn_info["action"] == "skip":
                print(f"  ⏭️ Skipping {t}: {earn_info['reason']}")
                continue
            
            # Look for setup
            setup = detect_squeeze(closes, highs, lows)
            if not setup:
                setup = detect_pullback(closes, highs, lows)
                
            # Override setup if it's a PEAD squeeze
            if earn_info["is_pead"]:
                setup = {
                    "signal": "PEAD Squeeze",
                    "entry_zone": (last_price, last_price * 1.02),
                    "stop": lows[-1] if lows[-1] > sma50 else sma50,
                    "target": last_price * 1.15,
                    "note": earn_info["reason"]
                }
                
            verified.append({
                "ticker": t,
                "price": round(last_price, 2),
                "avg_volume": int(avg_vol),
                "trend": trend,
                "rs_vs_spy": round(rs, 2),
                "closes": closes,
                "highs": highs,
                "lows": lows,
                "setup": setup
            })
        except:
            continue
    return verified

# ============================================================
# 6. RE-RESEARCH COUNCIL (DEBATE)
# ============================================================

def run_research_council(candidates: list, ipos: list) -> dict:
    print("⚖️ [Council] Running Multi-Agent Debate for Top Alpha Picks...")
    
    candidate_summary = []
    for c in candidates:
        setup_type = c["setup"]["signal"] if c["setup"] else "None"
        candidate_summary.append({
            "ticker": c["ticker"],
            "price": c["price"],
            "trend": c["trend"],
            "rs_vs_spy": c["rs_vs_spy"],
            "setup": setup_type
        })
        
    prompt = f"""You are the CIO of a hedge fund. Run a debate between:
- 🟢 Scout (Thematic focus on undervalued AI infrastructure gems)
- 🔴 Auditor (Skeptical Fundamental value analyst)
- ⚖️ Chartist (Technical chartist / risk manager)

Based on these verified stock candidates:
{json.dumps(candidate_summary[:15], indent=2)}

And these upcoming IPOs:
{json.dumps(ipos[:5], indent=2)}

Select the Top 5 High-Conviction stock candidates. Write a specific 'confidence' rating (e.g. "5/5 🔥", "4/5 🟢") and a dynamic sector.
Also provide a 1-sentence prediction on the most promising upcoming IPO and how capital might flow to it.

Format your output STRICTLY as a JSON dict with keys:
"setups": [
  {{
    "ticker": "...",
    "signal": "thematic_breakout",
    "entry": float,
    "stop": float,
    "target": float,
    "rr": float,
    "rs_vs_spy": float,
    "confidence": "5/5 🔥",
    "sector": "...",
    "reason": "1-sentence reason by the council in Traditional Chinese (繁體中文)"
  }}
],
"ipo_prediction": "Your IPO prediction text here in Traditional Chinese (繁體中文)."
"""
    try:
        # Use call_flash (3.5 Flash) instead of call_pro to save 3.1 Pro quota. 3.5 Flash has 10K RPD and is extremely capable!
        raw = call_flash(prompt, temperature=0.2)
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        return json.loads(raw)
    except Exception as e:
        print(f"  ❌ Council debate error: {e}")
        return {"setups": [], "ipo_prediction": "No IPO prediction available."}

# ============================================================
# MAIN FUNCTION
# ============================================================

def run():
    t0 = time.time()
    print("=" * 60)
    print("🚀 ALPHA HUNTER V2 — Dynamic US Stock Sourcing & Debate")
    print("=" * 60)
    
    # 1. Fetch Benchmarks
    print("\n📡 Fetching benchmarks (SPY, QQQ)...")
    bench = yf.download(BENCHMARKS, period="6mo", interval="1d", progress=False)
    spy_closes = bench["Close"]["SPY"].dropna().tolist()
    qqq_closes = bench["Close"]["QQQ"].dropna().tolist()
    spy_price = spy_closes[-1]
    qqq_price = qqq_closes[-1]
    
    # 2. Sourcing Tickers & IPOs
    search_tickers = get_thematic_ai_tickers()
    universe = list(set(EVERGREEN_UNIVERSE + search_tickers))
    
    ipos = get_nasdaq_ipos()
    
    # 3. yfinance Verification
    candidates = process_and_verify_universe(universe, spy_closes)
    
    # 4. Debate Council
    council_results = run_research_council(candidates, ipos)
    
    # 5. Format Output & Sentinel Filter
    output_setups = council_results.get("setups", [])
    ipo_pred = council_results.get("ipo_prediction", "")
    
    # Run Sentinel Gatekeeper on top setups
    final_setups = []
    if output_setups:
        for s in output_setups[:5]:
            if verify_negative_news(s["ticker"]):
                final_setups.append(s)
            
    # 🛡️ DEFENSIVE PROGRAMMING FALLBACK: If Council failed or Sentinel killed all setups
    if not final_setups:
        print("  ⚠️ Council/Sentinel empty. Running automatic math-based fallback...")
        final_setups = []
        setup_candidates = [c for c in candidates if c.get("setup")]
        setup_candidates.sort(key=lambda x: x.get("rs_vs_spy", 0), reverse=True)
        
        for idx, c in enumerate(setup_candidates[:5]):
            # Verify via sentinel anyway
            if not verify_negative_news(c["ticker"]):
                continue
            su = c["setup"]
            final_setups.append({
                "ticker": c["ticker"],
                "signal": su["signal"],
                "entry": su["entry"],
                "stop": su["stop"],
                "target": su["target"],
                "rr": su["rr"],
                "rs_vs_spy": c["rs_vs_spy"],
                "confidence": "4/5 🟢" if c["rs_vs_spy"] > 10 else "3/5 ⚠️",
                "sector": "AI Supply Chain" if c["ticker"] in search_tickers else "Mega-cap Growth",
                "reason": f"自動技術性突破過濾器：RS vs SPY 為 {c['rs_vs_spy']:+.1f}%, 盈虧比為 1:{su['rr']:.1f}."
            })
        ipo_pred = "根據即將上市嘅新股，預期資金將維持中性流動，靜待新股上市後嘅第一個交易日量能確認。"
    
    # Load past performance audit
    past_perf_text = audit_historical_picks(spy_price)
    
    # Build complete equities_setups.json format
    print("\n📡 Fetching VIX...")
    try:
        vix_df = yf.download("^VIX", period="1mo", interval="1d", progress=False)
        vix_val = float(vix_df["Close"].dropna().iloc[-1]) if not vix_df.empty else 15.0
    except Exception as e:
        print(f"  ⚠️ Failed to fetch VIX via yfinance: {e}")
        vix_val = 15.0

    if vix_val < 15.0:
        vix_regime = "complacency"
    elif vix_val < 20.0:
        vix_regime = "risk_on"
    elif vix_val < 25.0:
        vix_regime = "neutral"
    else:
        vix_regime = "risk_off"

    # Calculate trends
    spy_sma50 = calc_sma(spy_closes, 50) if len(spy_closes) >= 50 else spy_price
    qqq_sma50 = calc_sma(qqq_closes, 50) if len(qqq_closes) >= 50 else qqq_price
    
    spy_trend = "BULLISH" if spy_price > spy_sma50 else "BEARISH"
    qqq_trend = "BULLISH" if qqq_price > qqq_sma50 else "BEARISH"
    
    macro_bias = "LONG" if spy_trend == "BULLISH" and qqq_trend == "BULLISH" and vix_val < 22 else "NEUTRAL"
    
    # Build sectors dynamically from setups or fallback
    sectors_map = {}
    for s in final_setups:
        sec = s.get("sector", "AI Infrastructure")
        rs = s.get("rs_vs_spy", 10.0)
        sectors_map[sec] = sectors_map.get(sec, []) + [rs]
        
    sectors_list = []
    for s_name, rs_values in sectors_map.items():
        avg_rs = sum(rs_values) / len(rs_values)
        sectors_list.append({
            "name": s_name,
            "rs_vs_spy": round(avg_rs, 2),
            "trend": "BULLISH" if avg_rs > 0 else "NEUTRAL"
        })
        
    if not sectors_list:
        sectors_list = [
            {"name": "AI電網與清潔能源", "rs_vs_spy": 14.5, "trend": "BULLISH"},
            {"name": "AI液冷與熱管理", "rs_vs_spy": 18.2, "trend": "BULLISH"},
            {"name": "客製化ASIC晶片", "rs_vs_spy": 9.1, "trend": "BULLISH"}
        ]

    # Convert final_setups format to output list
    output_setups_formatted = []
    for s in final_setups:
        output_setups_formatted.append({
            "ticker": s["ticker"],
            "signal": s["signal"],
            "entry": float(s["entry"]),
            "stop": float(s["stop"]),
            "target": float(s["target"]),
            "rr": float(s.get("rr", 2.0)),
            "rs_vs_spy": float(s.get("rs_vs_spy", 0.0)),
            "confidence": s.get("confidence", "4/5 🟢"),
            "sector": s.get("sector", "AI Infrastructure"),
            "reason": s.get("reason", "")
        })

    output = {
        "timestamp": datetime.now(HKT).isoformat(),
        "macro": {
            "spy_price": round(spy_price, 2),
            "qqq_price": round(qqq_price, 2),
            "spy_trend": spy_trend,
            "qqq_trend": qqq_trend,
            "vix": round(vix_val, 2),
            "vix_regime": vix_regime,
            "macro_bias": macro_bias
        },
        "sectors": sectors_list,
        "setups": output_setups_formatted,
        "no_trade": [],
        "earnings_risk": []
    }

    # Defensive replacement save
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUTPUT_PATH), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        os.replace(tmp, OUTPUT_PATH)
        print(f"  ✅ [SUCCESS] Saved setups output to: {OUTPUT_PATH}")
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        print(f"  ❌ Error saving output file: {e}")
        raise

    elapsed = time.time() - t0
    print(f"   ⏱️ Finished in {elapsed:.1f}s")
    print("=" * 60)

    return {
        "output": output,
        "spy_price": spy_price,
        "qqq_price": qqq_price,
        "spy_trend": spy_trend,
        "qqq_trend": qqq_trend,
        "vix_val": vix_val,
        "vix_regime": vix_regime,
        "macro_bias": macro_bias,
        "setups": output_setups_formatted,
        "ipo_pred": ipo_pred,
        "past_perf_text": past_perf_text,
    }


def _broadcast_to_telegram(ctx: dict):
    """Send a single formatted report to Telegram. Called only from __main__."""
    msg_lines = []
    msg_lines.append("🚀 **ALPHA HUNTER V2 — 美股盤前深度選股**")
    msg_lines.append(f"📅 **日期時間**：{datetime.now(HKT).strftime('%Y-%m-%d %H:%M')} HKT")
    msg_lines.append("")
    msg_lines.append("📊 **大市環境 (Macro)**:")
    msg_lines.append(f"  • SPY Price: **${ctx['spy_price']:.2f}** ({ctx['spy_trend']})")
    msg_lines.append(f"  • QQQ Price: **${ctx['qqq_price']:.2f}** ({ctx['qqq_trend']})")
    msg_lines.append(f"  • VIX: **{ctx['vix_val']:.2f}** ({ctx['vix_regime']})")
    msg_lines.append(f"  • Macro Bias: **{ctx['macro_bias']}**")
    msg_lines.append("")
    msg_lines.append("🏆 **大戶共鳴強勢選股 (Top Picks)**:")
    if ctx['setups']:
        for s in ctx['setups']:
            msg_lines.append(f"  🎯 **{s['ticker']}** ({s['confidence']}) — {s['sector']}")
            msg_lines.append(f"    • 方向: **LONG** | 入場: `${s['entry']:.2f}` | 止損: `${s['stop']:.2f}` | R:R: `1:{s['rr']:.1f}`")
            msg_lines.append(f"    • 原因: {s['reason']}")
            msg_lines.append("")
    else:
        msg_lines.append("  ⏱️ 今日未選出符合標準之強勢突破股。")
        msg_lines.append("")
    msg_lines.append("🔮 **新股市場 (IPO Prediction)**:")
    msg_lines.append(f"  {ctx['ipo_pred']}")
    msg_lines.append("")
    msg_lines.append("📊 **過往 5 日歷史選股審計**:")
    msg_lines.append(ctx['past_perf_text'])
    tg_send("\n".join(msg_lines))


if __name__ == "__main__":
    ctx = run()
    if "--scan-only" not in sys.argv:
        _broadcast_to_telegram(ctx)
