#!/usr/bin/env python3
"""
⚡ CRYPTO SCALP LAB — 加密貨幣短線實驗室
=========================================
Capital.com demo 加密貨幣戶口 ($1,000)。測試不同短線策略，搵出最有效嘅打法。

策略：
  1. OB Imbalance scalp — 買賣牆失衡即入，快出
  2. Funding squeeze — 極端資金費率逆向操作
  3. Support bounce — 價到支撐位即彈

每 trade: 小倉 (~$10-20 notional)、tight stop (0.5-1%)、quick target (1-2%)
每 trade 記錄：策略/入場/出場/止損/止賺/原因/結果/P&L
每週 review：邊個策略最賺錢 → 加大嗰個策略

Data sources:
  - Hyperliquid order book (free, fast, real-time)
  - Hyperliquid funding rate
  - Multi-exchange consensus
"""

import json
import os
import time
import urllib.request
import urllib.error
import csv
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
SCALP_DIR = os.path.join(WORKSPACE, "scalp_lab")
os.makedirs(SCALP_DIR, exist_ok=True)

LOG_FILE = os.path.join(SCALP_DIR, "scalp_log.json")
SUMMARY_FILE = os.path.join(SCALP_DIR, "scalp_summary.json")

# Hyperliquid API
HL_API = "https://api.hyperliquid.xyz/info"

# === Strategy Parameters ===
SCALP_CONFIG = {
    "max_notional_per_trade": 20,   # $20 max per scalp
    "max_positions": 3,              # Max 3 concurrent scalps
    "stop_loss_pct": 0.008,          # 0.8% stop
    "take_profit_pct": 0.02,         # 2% target
    "hold_max_minutes": 60,          # Max hold 60 min
}

STRATEGIES = ["ob_imbalance", "funding_squeeze", "support_bounce"]


def post_hl(payload: dict) -> dict:
    """POST to Hyperliquid info API."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(HL_API, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def get_orderbook(coin: str = "BTC") -> dict:
    """Get Hyperliquid L2 order book for a coin."""
    price = None
    # Get all mids first
    meta = post_hl({"type": "meta"})
    if "error" in meta:
        # Fallback: use allMids
        mids = post_hl({"type": "allMids"})
        if "error" in mids:
            return {"error": str(mids)}
        price = None
        for m in mids:
            if coin in str(m):
                price = float(mids[m])
                break
    else:
        universe = meta.get("universe", [])
        for u in universe:
            if u.get("name") == coin:
                price = float(u.get("midPrice", 0))
                break
    
    # Get L2 order book
    coin_map = {"BTC": 0, "ETH": 1, "SOL": 2}
    idx = coin_map.get(coin, 0)
    l2 = post_hl({"type": "l2Book", "coin": str(idx)})
    
    if l2 is None or "error" in l2:
        return {"error": str(l2) if l2 else "No L2 data"}
    
    levels = l2.get("levels", [[], []])
    bids = []
    asks = []
    for b in levels[0][:20]:
        bids.append({"px": float(b["px"]), "sz": float(b["sz"])})
    for a in levels[1][:20]:
        asks.append({"px": float(a["px"]), "sz": float(a["sz"])})
    
    bid_vol = sum(b["sz"] for b in bids[:5])
    ask_vol = sum(a["sz"] for a in asks[:5])
    
    return {
        "coin": coin,
        "mid": price,
        "bids": bids,
        "asks": asks,
        "bid_vol_5": round(bid_vol, 1),
        "ask_vol_5": round(ask_vol, 1),
        "imbalance": round(bid_vol / ask_vol, 2) if ask_vol > 0 else 99,
    }


def get_funding(coin: str = "BTC") -> float:
    """Get current funding rate (annualized %)."""
    coin_map = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL"}
    ctx = post_hl({"type": "metaAndAssetCtxs"})
    if not ctx or "error" in ctx:
        return 0
    if len(ctx) < 2:
        return 0
    universe = ctx[0].get("universe", [])
    asset_ctx = ctx[1]
    for i, u in enumerate(universe):
        if u.get("name") == coin and i < len(asset_ctx):
            fr = float(asset_ctx[i].get("funding", 0))
            return fr * 100
    return 0


def load_csv_history(coin: str, days: int = 5) -> list:
    """Load recent price history for support calculation."""
    safe = {"BTC": "BTC_USD", "ETH": "ETH_USD", "SOL": "SOL_USD"}[coin]
    csv_path = os.path.expanduser(f"~/market_data/{safe}_history.csv")
    if not os.path.exists(csv_path):
        return []
    try:
        with open(csv_path, "r") as f:
            reader = csv.reader(f)
            next(reader)
            return [float(r[4]) for r in reader if len(r) >= 5][-days:]
    except Exception:
        return []


def strategy_ob_imbalance(coin: str, ob: dict) -> dict:
    """OB Imbalance: if bid wall >> ask wall → scalp LONG."""
    imbalance = ob.get("imbalance", 1)
    mid = ob.get("mid", 0)
    if not mid:
        return None
    
    if imbalance >= 2.0:  # 2:1 bid dominance
        entry = mid
        stop = mid * (1 - SCALP_CONFIG["stop_loss_pct"])
        target = mid * (1 + SCALP_CONFIG["take_profit_pct"])
        return {
            "strategy": "ob_imbalance",
            "coin": coin,
            "direction": "LONG",
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "reason": f"Bid/Ask imbalance {imbalance:.1f}:1",
            "ob_bid_vol": ob["bid_vol_5"],
            "ob_ask_vol": ob["ask_vol_5"],
        }
    elif imbalance <= 0.3:  # 3:1 ask dominance
        entry = mid
        stop = mid * (1 + SCALP_CONFIG["stop_loss_pct"])
        target = mid * (1 - SCALP_CONFIG["take_profit_pct"])
        return {
            "strategy": "ob_imbalance",
            "coin": coin,
            "direction": "SHORT",
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "reason": f"Ask/Bid imbalance {1/imbalance:.1f}:1",
        }
    return None


def strategy_funding_squeeze(coin: str, fr: float, price: float) -> dict:
    """Funding squeeze: extreme funding → counter-trade."""
    if fr > 30:  # Longs too crowded → expect dump
        entry = price
        stop = price * 1.008
        target = price * 0.98
        return {
            "strategy": "funding_squeeze",
            "coin": coin,
            "direction": "SHORT",
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "reason": f"Funding {fr:.1f}% → longs crowded, expect squeeze down",
            "funding_rate": round(fr, 1),
        }
    elif fr < -15:  # Shorts too crowded → expect pump
        entry = price
        stop = price * 0.992
        target = price * 1.02
        return {
            "strategy": "funding_squeeze",
            "coin": coin,
            "direction": "LONG",
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "reason": f"Funding {fr:.1f}% → shorts crowded, expect squeeze up",
            "funding_rate": round(fr, 1),
        }
    return None


def strategy_support_bounce(coin: str, price: float, history: list) -> dict:
    """Support bounce: price near 5-day low → scalp LONG."""
    if not history or len(history) < 5:
        return None
    
    support_5d = min(history)
    dist = abs(price - support_5d) / price * 100
    
    if dist < 0.5:  # Within 0.5% of support
        entry = price
        stop = support_5d * 0.995  # Just below support
        target = price * 1.015  # 1.5% bounce
        return {
            "strategy": "support_bounce",
            "coin": coin,
            "direction": "LONG",
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "reason": f"Price ${price:.2f} near 5d low ${support_5d:.2f} ({dist:.1f}%)",
            "support_5d": round(support_5d, 2),
        }
    return None


def scan_all() -> list:
    """Run all 3 strategies on BTC/ETH/SOL. Return list of signal dicts."""
    signals = []
    
    for coin in ["BTC", "ETH", "SOL"]:
        # Get order book
        ob = get_orderbook(coin)
        if "error" in ob or not ob.get("mid"):
            continue
        
        price = ob["mid"]
        
        # Strategy 1: OB Imbalance
        s1 = strategy_ob_imbalance(coin, ob)
        if s1:
            signals.append(s1)
        
        # Strategy 2: Funding Squeeze
        fr = get_funding(coin)
        s2 = strategy_funding_squeeze(coin, fr, price)
        if s2:
            signals.append(s2)
        
        # Strategy 3: Support Bounce
        history = load_csv_history(coin, 5)
        s3 = strategy_support_bounce(coin, price, history)
        if s3:
            signals.append(s3)
    
    return signals


def log_scalp(signal: dict, result: str, exit_price: float = 0, pnl_pct: float = 0):
    """Log a scalp trade to JSON."""
    trades = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                trades = json.load(f)
        except (json.JSONDecodeError, OSError):
            trades = []
    
    trade = {
        "timestamp": datetime.now(HKT).isoformat(),
        "coin": signal["coin"],
        "strategy": signal["strategy"],
        "direction": signal["direction"],
        "entry": signal["entry"],
        "stop": signal["stop"],
        "target": signal["target"],
        "exit": exit_price,
        "result": result,  # "win", "loss", "open"
        "pnl_pct": round(pnl_pct, 2),
        "reason": signal.get("reason", ""),
    }
    trades.append(trade)
    
    with open(LOG_FILE, "w") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2, default=str)
    
    return trade


def run_scan():
    """Run a full scan and log results."""
    now = datetime.now(HKT)
    print("=" * 60)
    print("⚡ CRYPTO SCALP LAB — Scan")
    print(f"   {now.strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 60)
    
    signals = scan_all()
    
    if not signals:
        print("\n📭 No scalp signals today.")
        return []
    
    print(f"\n🔍 Found {len(signals)} signals:")
    for s in signals:
        emoji = "🟢" if s["direction"] == "LONG" else "🔴"
        print(f"  {emoji} {s['coin']} {s['strategy']}: Entry ${s['entry']:.2f} | Stop ${s['stop']:.2f} | Target ${s['target']:.2f} | {s['reason']}")
    
    return signals


def run_summary():
    """Generate strategy performance summary."""
    trades = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                trades = json.load(f)
        except (json.JSONDecodeError, OSError):
            trades = []
    
    if not trades:
        print("📭 No scalp trades recorded yet.")
        return
    
    now = datetime.now(HKT)
    week_ago = now - timedelta(days=7)
    
    print("=" * 60)
    print("⚡ CRYPTO SCALP LAB — Performance Summary")
    print(f"   {now.strftime('%Y-%m-%d %H:%M HKT')}")
    print(f"   Total trades: {len(trades)}")
    print("=" * 60)
    
    # Per-strategy stats
    for strat in STRATEGIES:
        strat_trades = [t for t in trades if t["strategy"] == strat]
        if not strat_trades:
            continue
        
        wins = [t for t in strat_trades if t["result"] == "win"]
        losses = [t for t in strat_trades if t["result"] == "loss"]
        total = len(strat_trades)
        wr = len(wins) / total * 100 if total > 0 else 0
        avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
        total_pnl = sum(t["pnl_pct"] for t in strat_trades)
        
        print(f"\n📊 {strat}")
        print(f"   Trades: {total} | Win Rate: {wr:.0f}% | P&L: {total_pnl:+.2f}%")
        print(f"   Avg Win: {avg_win:+.2f}% | Avg Loss: {avg_loss:+.2f}%")
    
    # Overall
    wins_all = [t for t in trades if t["result"] == "win"]
    wr_all = len(wins_all) / len(trades) * 100 if trades else 0
    pnl_all = sum(t["pnl_pct"] for t in trades)
    print(f"\n🎯 OVERALL: {len(trades)} trades | WR {wr_all:.0f}% | P&L {pnl_all:+.2f}%")
    
    # Save summary
    summary = {
        "generated": now.isoformat(),
        "total_trades": len(trades),
        "win_rate": round(wr_all, 1),
        "total_pnl_pct": round(pnl_all, 2),
        "strategies": {}
    }
    for strat in STRATEGIES:
        st = [t for t in trades if t["strategy"] == strat]
        if st:
            summary["strategies"][strat] = {
                "trades": len(st),
                "win_rate": round(len([t for t in st if t["result"] == "win"]) / len(st) * 100, 1),
                "pnl_pct": round(sum(t["pnl_pct"] for t in st), 2),
            }
    
    with open(SUMMARY_FILE, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    
    # Best strategy
    if summary["strategies"]:
        best = max(summary["strategies"].items(), key=lambda x: x[1]["pnl_pct"])
        print(f"\n🏆 Best Strategy: {best[0]} (P&L {best[1]['pnl_pct']:+.2f}%)")


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "--scan"
    
    if mode == "--scan":
        run_scan()
    elif mode == "--summary":
        run_summary()
    else:
        print("Usage: crypto_scalper.py [--scan|--summary]")
