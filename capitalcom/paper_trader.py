#!/usr/bin/env python3
"""
📊 PAPER TRADING TRACKER — 紙上交易追蹤系統
============================================
記錄所有 Commander's Briefing 推薦嘅 trade setup，模擬真實交易。

Modes:
  --open    讀取最新 setups，開新 paper position（每日晨報後行）
  --check   檢查 open positions 有冇中 stop/target（每日收市後行）
  --review  出本週 P&L 總結報告（星期六行）

Data:
  paper_positions.json  — 目前持倉
  paper_history.json    — 已平倉記錄（含 P&L）
  paper_summary.json    — 每週總結
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

try:
    import yfinance as yf
except ImportError:
    print("❌ yfinance required")
    sys.exit(1)

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
PAPER_DIR = os.path.join(WORKSPACE, "paper_trades")
os.makedirs(PAPER_DIR, exist_ok=True)

POSITIONS_FILE = os.path.join(PAPER_DIR, "paper_positions.json")
HISTORY_FILE = os.path.join(PAPER_DIR, "paper_history.json")
SUMMARY_FILE = os.path.join(PAPER_DIR, "paper_summary.json")
EQUITIES_SETUPS = os.path.join(WORKSPACE, "equities_data", "equities_setups.json")


def load_json(path):
    if not os.path.exists(path):
        return [] if "history" in path or "positions" in path else {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return [] if "history" in path or "positions" in path else {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def get_current_price(ticker: str, market: str) -> float:
    """Get current price for a ticker. market = 'crypto' or 'equities'."""
    if market == "crypto":
        # Use local market data CSV
        csv_map = {"BTC": "BTC_USD", "ETH": "ETH_USD", "SOL": "SOL_USD"}
        safe = csv_map.get(ticker, ticker)
        csv_path = os.path.expanduser(f"~/market_data/{safe}_history.csv")
        if os.path.exists(csv_path):
            import csv
            try:
                with open(csv_path, "r") as f:
                    rows = list(csv.reader(f))
                    if len(rows) >= 2:
                        return float(rows[-1][4])
            except Exception:
                pass
        return 0
    
    elif market == "equities":
        try:
            ticker_yf = "^VIX" if ticker == "VIX" else ticker
            d = yf.download(ticker_yf, period="2d", interval="1d", progress=False, auto_adjust=True)
            if not d.empty:
                if isinstance(d.columns, pd.MultiIndex if 'pd' in dir() else type(None)):
                    import pandas as pd
                # Handle multi-index
                close_col = ('Close', ticker_yf) if isinstance(d.columns, type(pd.MultiIndex([]))) and ('Close', ticker_yf) in d.columns else 'Close'
                val = d[close_col].iloc[-1] if close_col in d.columns else (d['Close'].iloc[-1] if 'Close' in d.columns else 0)
                return float(val) if val else 0
        except Exception:
            pass
        return 0
    
    return 0


def open_positions():
    """Read setups from equities engine + generate crypto setups, open paper positions."""
    now = datetime.now(HKT)
    positions = load_json(POSITIONS_FILE)
    existing_tickers = {(p["ticker"], p["market"]) for p in positions if p["status"] == "open"}
    
    new_count = 0
    
    # ── Equities setups ──
    eq_data = load_json(EQUITIES_SETUPS)
    for s in eq_data.get("setups", []):
        ticker = s["ticker"]
        if (ticker, "equities") in existing_tickers:
            continue
        
        pos = {
            "id": f"EQ-{ticker}-{now.strftime('%m%d%H%M')}",
            "ticker": ticker,
            "market": "equities",
            "direction": s.get("direction", "LONG"),
            "entry": s["entry"],
            "stop": s["stop"],
            "target": s["target"],
            "rr": s.get("rr", 0),
            "confidence": s.get("confidence", "?"),
            "signal": s.get("signal", ""),
            "rs": s.get("rs_vs_spy", 0),
            "opened_at": now.isoformat(),
            "status": "open",
            "current_price": s["entry"],
            "pnl_pct": 0,
        }
        positions.append(pos)
        existing_tickers.add((ticker, "equities"))
        new_count += 1
        print(f"  📊 OPEN {ticker} LONG @ ${s['entry']:.2f} | Stop ${s['stop']:.2f} | Target ${s['target']:.2f} | R:R 1:{s['rr']}")
    
    # ── Crypto setups ──
    # Parse from morning_briefing.py output via the same logic
    crypto_setups = _generate_crypto_setups_from_data()
    for s in crypto_setups:
        ticker = s["sym"]
        if (ticker, "crypto") in existing_tickers:
            continue
        
        pos = {
            "id": f"CR-{ticker}-{now.strftime('%m%d%H%M')}",
            "ticker": ticker,
            "market": "crypto",
            "direction": "LONG" if "LONG" in s["side"] else "SHORT",
            "entry": s["entry"],
            "stop": s["stop"],
            "target": s["target"],
            "rr": s.get("rr", 0),
            "confidence": s.get("confidence", "?"),
            "signal": "S/R bounce",
            "opened_at": now.isoformat(),
            "status": "open",
            "current_price": s["entry"],
            "pnl_pct": 0,
        }
        positions.append(pos)
        existing_tickers.add((ticker, "crypto"))
        new_count += 1
        print(f"  🪙 OPEN {ticker} {s['side']} @ ${s['entry']:.2f} | Stop ${s['stop']:.2f} | Target ${s['target']:.2f}")
    
    save_json(POSITIONS_FILE, positions)
    print(f"\n📊 Opened {new_count} new positions | Total open: {len(positions)}")
    return new_count


def _generate_crypto_setups_from_data() -> list:
    """Mirror the crypto setup logic from morning_briefing.py."""
    # Simplified version that reads market data and generates setups
    import csv
    
    market = {}
    for sym, safe in [("BTC", "BTC_USD"), ("ETH", "ETH_USD"), ("SOL", "SOL_USD")]:
        csv_path = os.path.expanduser(f"~/market_data/{safe}_history.csv")
        if not os.path.exists(csv_path):
            continue
        try:
            with open(csv_path, "r") as f:
                rows = list(csv.reader(f))
                if len(rows) >= 2:
                    close = float(rows[-1][4])
                    close_prev = float(rows[-2][4]) if len(rows[-2]) > 4 else close
                    market[sym] = {"close": close, "change_pct": round((close-close_prev)/close_prev*100, 2)}
        except Exception:
            continue
    
    setups = []
    for sym, safe in [("BTC", "BTC_USD"), ("ETH", "ETH_USD"), ("SOL", "SOL_USD")]:
        if sym not in market:
            continue
        price = market[sym]["close"]
        
        # Load history for S/R
        hist = []
        csv_path = os.path.expanduser(f"~/market_data/{safe}_history.csv")
        try:
            with open(csv_path, "r") as f:
                reader = csv.reader(f)
                next(reader)
                for row in reader:
                    if len(row) >= 5:
                        hist.append({"close": float(row[4]), "high": float(row[2]), "low": float(row[3])})
        except Exception:
            continue
        if len(hist) < 5:
            continue
        
        closes = [r["close"] for r in hist]
        highs = [r["high"] for r in hist]
        lows = [r["low"] for r in hist]
        support = min(lows[-10:]) if len(lows) >= 10 else min(lows)
        resistance = max(highs[-10:]) if len(highs) >= 10 else max(highs)
        
        dist_s = abs(price - support) / price * 100
        dist_r = abs(price - resistance) / price * 100
        
        if dist_s < 1.0:
            entry = price
            stop = support * 0.97
            target = resistance
            rr = (target - entry) / (entry - stop) if (entry - stop) > 0 else 0
            if rr >= 1.5:
                setups.append({"sym": sym, "side": "📈 LONG", "entry": entry, "stop": stop, "target": target, "rr": round(rr, 2), "confidence": _crypto_confidence(dist_s, rr)})
        elif dist_r < 1.0:
            entry = price
            stop = resistance * 1.03
            target = support
            rr = (entry - target) / (stop - entry) if (stop - entry) > 0 else 0
            if rr >= 1.5:
                setups.append({"sym": sym, "side": "📉 SHORT", "entry": entry, "stop": stop, "target": target, "rr": round(rr, 2), "confidence": _crypto_confidence(dist_r, rr)})
    
    return setups


def _crypto_confidence(dist: float, rr: float) -> str:
    score = 1 if dist < 1.0 else 0
    if rr >= 2.5:
        score += 2
    elif rr >= 1.5:
        score += 1
    if score >= 3:
        return "4/5 🔥🔥"
    elif score >= 2:
        return "3/5 🔥"
    return "2/5 ⚡"


def check_positions():
    """Check all open positions against current prices. Close if stop/target hit."""
    positions = load_json(POSITIONS_FILE)
    history = load_json(HISTORY_FILE)
    now = datetime.now(HKT)
    
    if not positions:
        print("📭 No open positions.")
        return 0
    
    closed_count = 0
    
    for pos in positions:
        if pos["status"] != "open":
            continue
        
        current = get_current_price(pos["ticker"], pos["market"])
        if current <= 0:
            continue
        
        pos["current_price"] = current
        entry = pos["entry"]
        
        if pos["direction"] == "LONG":
            pnl_pct = (current - entry) / entry * 100
            stopped = current <= pos["stop"]
            won = current >= pos["target"]
        else:  # SHORT
            pnl_pct = (entry - current) / entry * 100
            stopped = current >= pos["stop"]
            won = current <= pos["target"]
        
        pos["pnl_pct"] = round(pnl_pct, 2)
        pos["last_checked"] = now.isoformat()
        
        if stopped:
            pos["status"] = "stopped"
            pos["closed_at"] = now.isoformat()
            pos["exit_price"] = pos["stop"]
            pos["result"] = "LOSS"
            history.append(pos.copy())
            closed_count += 1
            print(f"  🔴 STOPPED {pos['ticker']} {pos['direction']} | Entry ${entry:.2f} | Stop ${pos['stop']:.2f} | P&L {pnl_pct:+.2f}% 💀")
        elif won:
            pos["status"] = "won"
            pos["closed_at"] = now.isoformat()
            pos["exit_price"] = pos["target"]
            pos["result"] = "WIN"
            history.append(pos.copy())
            closed_count += 1
            print(f"  🟢 WON {pos['ticker']} {pos['direction']} | Entry ${entry:.2f} | Target ${pos['target']:.2f} | P&L {pnl_pct:+.2f}% 🎯")
        else:
            emoji = "🟢" if pnl_pct > 0 else "🔴" if pnl_pct < 0 else "⚪"
            print(f"  {emoji} {pos['ticker']} {pos['direction']} | Entry ${entry:.2f} | Now ${current:.2f} | P&L {pnl_pct:+.2f}% | Stop ${pos['stop']:.2f} | Target ${pos['target']:.2f}")
    
    # Remove closed from positions
    positions = [p for p in positions if p["status"] == "open"]
    save_json(POSITIONS_FILE, positions)
    save_json(HISTORY_FILE, history)
    
    open_count = len([p for p in positions if p["status"] == "open"])
    print(f"\n📊 {open_count} open | {closed_count} closed today | {len(history)} total history")
    return closed_count


def weekly_review():
    """Generate weekly P&L summary."""
    history = load_json(HISTORY_FILE)
    positions = load_json(POSITIONS_FILE)
    now = datetime.now(HKT)
    week_ago = now - timedelta(days=7)
    
    # Filter this week's closed trades
    week_trades = []
    for t in history:
        closed_str = t.get("closed_at", "")
        if closed_str:
            try:
                closed_dt = datetime.fromisoformat(closed_str)
                if closed_dt >= week_ago:
                    week_trades.append(t)
            except ValueError:
                continue
    
    # Stats
    wins = [t for t in week_trades if t["result"] == "WIN"]
    losses = [t for t in week_trades if t["result"] == "LOSS"]
    
    total_pnl = sum(t["pnl_pct"] for t in week_trades)
    win_rate = len(wins) / len(week_trades) * 100 if week_trades else 0
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    
    # Open positions (unrealized)
    open_list = []
    for p in positions:
        if p["status"] == "open":
            current = get_current_price(p["ticker"], p["market"])
            if current > 0:
                p["current_price"] = current
                entry = p["entry"]
                pnl = (current - entry) / entry * 100 if p["direction"] == "LONG" else (entry - current) / entry * 100
                p["pnl_pct"] = round(pnl, 2)
                p["last_checked"] = now.isoformat()
                open_list.append(p)
    save_json(POSITIONS_FILE, positions)
    
    # Build report
    report = {
        "generated": now.isoformat(),
        "period": f"{week_ago.strftime('%m/%d')} - {now.strftime('%m/%d')}",
        "closed_trades": len(week_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "total_pnl_pct": round(total_pnl, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "open_positions": len(open_list),
        "open_unrealized_pnl": round(sum(p["pnl_pct"] for p in open_list), 2),
        "trades": [
            {
                "id": t["id"],
                "ticker": t["ticker"],
                "market": t["market"],
                "direction": t["direction"],
                "entry": t["entry"],
                "exit": t.get("exit_price", t.get("current_price", 0)),
                "pnl_pct": t["pnl_pct"],
                "result": t["result"],
                "confidence": t.get("confidence", "?"),
            }
            for t in week_trades
        ],
        "open_trades": [
            {
                "ticker": p["ticker"],
                "market": p["market"],
                "entry": p["entry"],
                "current": p.get("current_price", 0),
                "pnl_pct": p.get("pnl_pct", 0),
                "stop": p["stop"],
                "target": p["target"],
                "confidence": p.get("confidence", "?"),
            }
            for p in open_list
        ],
    }
    
    save_json(SUMMARY_FILE, report)
    
    # Print summary
    print("=" * 60)
    print("📊 WEEKLY PAPER TRADING REVIEW")
    print(f"   {report['period']}")
    print("=" * 60)
    print(f"\n⚔️ CLOSED: {len(week_trades)} trades | {len(wins)}W / {len(losses)}L | WR {win_rate:.0f}%")
    print(f"💰 Total P&L: {total_pnl:+.2f}%")
    print(f"📈 Avg Win: {avg_win:+.2f}% | Avg Loss: {avg_loss:+.2f}%")
    
    if open_list:
        print(f"\n📂 OPEN: {len(open_list)} positions (Unrealized {sum(p['pnl_pct'] for p in open_list):+.2f}%)")
        for p in sorted(open_list, key=lambda x: x.get('pnl_pct', 0), reverse=True):
            emoji = "🟢" if p.get('pnl_pct', 0) > 0 else "🔴"
            print(f"  {emoji} {p['ticker']} {p.get('direction','')} | {p.get('pnl_pct', 0):+.2f}% | Stop ${p['stop']:.2f} | Target ${p['target']:.2f}")
    
    for t in week_trades:
        emoji = "🎯 WIN" if t["result"] == "WIN" else "💀 LOSS"
        print(f"\n  {emoji} {t['ticker']} {t['direction']} | Entry ${t['entry']:.2f} → Exit ${t.get('exit', 0):.2f} | {t['pnl_pct']:+.2f}% | {t.get('confidence', '?')}")
    
    print(f"\n💾 Report: {SUMMARY_FILE}")
    return report


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    
    if mode == "--open":
        print("=" * 60)
        print(f"📊 PAPER TRADER — Opening new positions")
        print(f"   {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
        print("=" * 60)
        open_positions()
    elif mode == "--check":
        print("=" * 60)
        print(f"📊 PAPER TRADER — Checking positions")
        print(f"   {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
        print("=" * 60)
        check_positions()
    elif mode == "--review":
        weekly_review()
    elif mode == "--full":
        print("=" * 60)
        print(f"📊 PAPER TRADER — Full cycle")
        print(f"   {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
        print("=" * 60)
        print("\n▶️ Phase 1: Check existing positions")
        check_positions()
        print("\n▶️ Phase 2: Open new setups")
        open_positions()
    else:
        print(f"Usage: paper_trader.py [--open|--check|--review|--full]")
