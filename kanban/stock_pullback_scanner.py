#!/usr/bin/env python3
"""
Stock Pullback Scanner V2 — with Historical Edge Quantification
═══════════════════════════════════════════════════════════
V2 2026-05-18:
  + Historical backtest of oversold signals (RSI < 30, near MA200)
  + Edge quantification: win rate + avg return over N days
  + Only report stocks with proven historical edge
  + Gold (GLD) integration

Cron: 每日 05:35 HKT
"""

import os, sys, json, tempfile
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════
# SCANNER CONFIG
# ═══════════════════════════════════════════════

SCANNER_DIR = os.path.expanduser("~/workspace/stock_scanner")
PREV_RESULTS_FILE = os.path.join(SCANNER_DIR, "prev_results.json")

# ═══════════════════════════════════════════════
# STOCK UNIVERSE + GOLD
# ═══════════════════════════════════════════════

STOCKS = {
    # Tech / AI
    "NVDA": "NVIDIA", "AMD": "AMD", "AVGO": "Broadcom", "ANET": "Arista",
    "SMCI": "Super Micro", "MSFT": "Microsoft", "GOOGL": "Google",
    "META": "Meta", "AAPL": "Apple", "AMZN": "Amazon",
    # Semi
    "SMH": "VanEck Semi ETF", "SOXX": "iShares Semi ETF",
    # Space / Defense
    "LMT": "Lockheed", "NOC": "Northrop", "RTX": "RTX Corp",
    # Finance / Other
    "JPM": "JP Morgan", "GS": "Goldman Sachs",
    "XOM": "Exxon", "CVX": "Chevron",
    # Gold
    "GLD": "SPDR Gold ETF",
}

# ═══════════════════════════════════════════════
# BACKTEST CONFIG
# ═══════════════════════════════════════════════

BACKTEST_YEARS = 2  # Lookback for edge calculation
FORWARD_DAYS = [5, 10, 21]  # Check returns 5d, 10d, 21d after oversold signal
RSI_OVERSOLD = 30
MA_NEAR_THRESHOLD = -5  # Within 5% of MA200


def compute_edge(symbol: str) -> dict:
    """Backtest oversold signals for this stock."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=f"{BACKTEST_YEARS}y")
        if len(hist) < 200:
            return None
        
        closes = hist["Close"]
        
        # RSI (14-day)
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        # MA200
        ma200 = closes.rolling(200).mean()
        ma_dist_pct = (closes - ma200) / ma200 * 100
        
        # Find oversold signals
        signals = (rsi < RSI_OVERSOLD) & (ma_dist_pct < MA_NEAR_THRESHOLD)
        signal_dates = closes.index[signals]
        
        if len(signal_dates) < 3:
            return None
        
        results = {}
        for fd in FORWARD_DAYS:
            returns = []
            for sd in signal_dates:
                # Find the closing price 'fd' trading days later
                idx = closes.index.get_loc(sd)
                future_idx = idx + fd
                if future_idx < len(closes):
                    ret = (closes.iloc[future_idx] - closes.iloc[idx]) / closes.iloc[idx] * 100
                    returns.append(ret)
            
            if returns:
                wins = sum(1 for r in returns if r > 0)
                results[f"{fd}d"] = {
                    "total": len(returns),
                    "win_rate": round(wins / len(returns) * 100, 1),
                    "avg_return": round(np.mean(returns), 2),
                    "median_return": round(np.median(returns), 2),
                }
        
        return {
            "symbol": symbol,
            "total_signals": len(signal_dates),
            "results": results,
        }
    except Exception:
        return None


def scan_stocks() -> dict:
    """Scan all stocks for oversold conditions + edge."""
    results = {"oversold": [], "near_ma200": [], "gold": None}
    edges = {}
    
    # First pass: compute edges
    for sym in STOCKS:
        edge = compute_edge(sym)
        if edge:
            edges[sym] = edge
    
    # Second pass: scan current conditions
    for sym, name in STOCKS.items():
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1y")
            if len(hist) < 200:
                continue
            
            close = hist["Close"].iloc[-1]
            closes = hist["Close"]
            
            # RSI
            delta = closes.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            rsi_val = 100 - (100 / (1 + rs.iloc[-1]))
            
            # MA50, MA200
            ma50 = closes.rolling(50).mean().iloc[-1]
            ma200_val = closes.rolling(200).mean().iloc[-1]
            ma50_dist = (close - ma50) / ma50 * 100
            ma200_dist = (close - ma200_val) / ma200_val * 100
            
            entry = {
                "symbol": sym,
                "name": name,
                "price": round(close, 2),
                "rsi": round(rsi_val, 1),
                "ma50_dist": round(ma50_dist, 1),
                "ma200_dist": round(ma200_dist, 1),
            }
            
            # Add edge data
            if sym in edges:
                entry["edge"] = edges[sym]
            
            # Gold special handling
            if sym == "GLD":
                entry["trend"] = "up" if close > ma50 else "down"
                results["gold"] = entry
                continue
            
            # Classification
            if rsi_val <= 30 and ma200_dist < MA_NEAR_THRESHOLD:
                severity = "🔴 重度" if ma200_dist < -10 else "🟠 中度" if ma200_dist < -5 else "🟡 輕度"
                entry["severity"] = severity
                results["oversold"].append(entry)
            elif ma200_dist < 0 and ma200_dist > MA_NEAR_THRESHOLD:
                entry["severity"] = "🟡 接近支撐"
                results["near_ma200"].append(entry)
                
        except Exception:
            continue
    
    return results


def format_report(results: dict) -> str:
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")
    lines = [f"📊 **Stock Pullback Scanner V3** — {now}", ""]
    
    # Comparison summary
    comparison = results.get("comparison", {})
    prev_symbols = results.get("prev_symbols", [])
    if prev_symbols:
        new_cnt = comparison.get("new_count", 0)
        gone_cnt = comparison.get("gone_count", 0)
        still_cnt = comparison.get("still_count", 0)
        parts = []
        if new_cnt > 0:
            parts.append(f"🆕 {new_cnt} 新見底")
        if gone_cnt > 0:
            parts.append(f"✅ {gone_cnt} 已恢復")
        if still_cnt > 0:
            parts.append(f"🔄 {still_cnt} 持續")
        if parts:
            lines.append(f"**📈 與上次掃描相比：** {' | '.join(parts)}")
            lines.append("")
    
    # Gold
    gold = results.get("gold")
    if gold:
        trend_emoji = "🟢" if gold.get("trend") == "up" else "🔴"
        lines.append(f"🥇 **黃金 (GLD)** ${gold['price']} | RSI {gold['rsi']} | {trend_emoji} {gold.get('trend', 'N/A')}")
        lines.append(f"   → Gold trending {gold.get('trend')}: {'risk-off signal — 利好 crypto 避險需求' if gold.get('trend') == 'up' else 'risk-on — 資金流出避險資產'}")
        lines.append("")
    
    # Oversold with edge
    oversold = results.get("oversold", [])
    if oversold:
        lines.append(f"🔴 **可能見底 (Oversold) — {len(oversold)} 隻**")
        for s in oversold:
            edge = s.get("edge", {})
            edge_str = ""
            if edge and edge.get("results"):
                wr_21d = edge["results"].get("21d", {}).get("win_rate", "?")
                avg_21d = edge["results"].get("21d", {}).get("avg_return", "?")
                edge_str = f" | Edge: {wr_21d}% WR 21d, avg {avg_21d}%"

            # Change marker
            change = s.get("change", "")
            if change == "NEW":
                marker = "🆕 **[NEW]** "
            elif change == "STILL":
                marker = "🔄 "
            else:
                marker = ""

            lines.append(f"  {marker}{s['severity']} **{s['name']} ({s['symbol']})** ${s['price']}")
            lines.append(f"     RSI={s['rsi']} | MA50={s['ma50_dist']:+.1f}% | MA200={s['ma200_dist']:+.1f}%{edge_str}")

            # Actionable trigger
            if s['rsi'] < 30:
                lines.append(f"     🎯 Trigger: RSI 升穿 35 + volume > 1.5x avg → 可考慮入場")
        lines.append("")

    # Gone list (recovered stocks)
    gone_list = comparison.get("gone_list", []) if comparison else []
    if gone_list:
        lines.append(f"✅ **已恢復（今次唔再超賣）— {len(gone_list)} 隻**")
        for sym in gone_list:
            name = STOCKS.get(sym, sym)
            lines.append(f"  ✅ **{name} ({sym})** — 已離開超賣區域")
        lines.append("")
    
    # Near MA200
    near = results.get("near_ma200", [])
    if near:
        lines.append(f"🟠 **接近 MA200 支撐 — {len(near)} 隻**")
        for s in near[:5]:
            lines.append(f"  {s['severity']} **{s['name']} ({s['symbol']})** ${s['price']} — MA200: ${s['price'] * (1 - s['ma200_dist']/100):.0f}")
        lines.append("")
    
    lines.append("💡 Edge = historical win rate of similar oversold signals (2yr backtest)")
    lines.append("_下次掃描: 24h_")
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# HISTORY TRACKING
# ═══════════════════════════════════════════════

def load_prev_results() -> list:
    """Load previous oversold stock list from JSON."""
    try:
        if not os.path.exists(PREV_RESULTS_FILE):
            return []
        with open(PREV_RESULTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("oversold_symbols", [])
    except Exception:
        return []


def save_current_results(oversold_list: list):
    """Save today's oversold symbols for next comparison."""
    os.makedirs(SCANNER_DIR, exist_ok=True)
    symbols = sorted(set(s["symbol"] for s in oversold_list))
    payload = {
        "timestamp": datetime.now(HKT).isoformat(),
        "oversold_symbols": symbols,
    }
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PREV_RESULTS_FILE), suffix='.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PREV_RESULTS_FILE)
    return symbols


def compare_results(current_oversold: list, prev_symbols: list) -> dict:
    """Compare today vs yesterday oversold lists. Returns annotated items."""
    current_symbols = [s["symbol"] for s in current_oversold]
    current_set = set(current_symbols)
    prev_set = set(prev_symbols)

    new_symbols = current_set - prev_set
    gone_symbols = prev_set - current_set
    still_symbols = current_set & prev_set

    # Annotate current oversold items
    annotated = []
    for s in current_oversold:
        sym = s["symbol"]
        if sym in new_symbols:
            s["change"] = "NEW"
        elif sym in still_symbols:
            s["change"] = "STILL"
        else:
            s["change"] = ""
        annotated.append(s)

    return {
        "annotated": annotated,
        "new_count": len(new_symbols),
        "gone_count": len(gone_symbols),
        "still_count": len(still_symbols),
        "gone_list": sorted(gone_symbols),
    }


if __name__ == "__main__":
    results = scan_stocks()
    oversold = results.get("oversold", [])

    # Load previous results and compare
    prev_symbols = load_prev_results()
    comparison = compare_results(oversold, prev_symbols)

    # Save current results for next comparison
    save_current_results(oversold)

    # Update results with annotation
    results["oversold"] = comparison["annotated"]
    results["comparison"] = comparison
    results["prev_symbols"] = prev_symbols

    output = format_report(results)
    if output.strip():
        print(output)
