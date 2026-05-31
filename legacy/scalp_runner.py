#!/usr/bin/env python3
"""
⚡ SCALP RUNNER — 槓桿短線執行引擎
====================================
Capital.com demo 加密貨幣戶口 ($1,000)。
高頻掃描 → 即時執行 → 每日檢討 → 動態調策略。

策略池（並行測試）：
  1. OB Imbalance — 買賣牆失衡 >2:1 即入
  2. Momentum — 短線趨勢跟進
  3. Mean Reversion — 極端偏離拉回
  4. Funding Squeeze — 極端資金費率逆向
  5. Breakout — 關鍵價位突破追入

每 strategy 獨立 tracking：
  - 勝率、平均贏/輸、總 P&L
  - 連續輸 N 次 → 自動暫停該策略
  - 勝率最高 → 自動加大倉位

執行頻率：每 15-30 分鐘（cron）
交易品種：BTC, ETH, SOL, GOLD, US500, US100, EURUSD
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
SCALP_DIR = os.path.join(WORKSPACE, "scalp_lab")
os.makedirs(SCALP_DIR, exist_ok=True)

LOG_FILE = os.path.join(SCALP_DIR, "scalp_trades.json")
STRATEGY_STATE_FILE = os.path.join(SCALP_DIR, "strategy_state.json")
DAILY_REPORT_FILE = os.path.join(SCALP_DIR, "daily_report.json")

# === Capital.com Integration ===
import sys
sys.path.insert(0, WORKSPACE)
from capitalcom_paper import (
    open_position, check_positions, close_position, get_account_balance,
    ACCOUNTS, get_account_for, _auth, _headers, get_market_info
)

CRYPTO_ACCOUNT = ACCOUNTS["crypto"]
MACRO_ACCOUNT = ACCOUNTS["macro"]

# === Hyperliquid API (for crypto OB data) ===
HL_API = "https://api.hyperliquid.xyz/info"


def post_hl(payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(HL_API, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


# === Config ===
SCAN_INTERVAL_MINUTES = 30  # Cron frequency

# Markets to trade (epic, our label, type)
MARKETS = [
    # Crypto
    {"epic": "BTCUSD", "label": "BTC", "type": "crypto"},
    {"epic": "ETHUSD", "label": "ETH", "type": "crypto"},
    {"epic": "SOLUSD", "label": "SOL", "type": "crypto"},
    # Macro — GOLD, SPX, NDX, EURUSD
    {"epic": "GOLD", "label": "XAUUSD", "type": "macro"},
    {"epic": "US500", "label": "SPX", "type": "macro"},
    {"epic": "US100", "label": "NDX", "type": "macro"},
    {"epic": "EURUSD", "label": "EURUSD", "type": "macro"},
]

# Strategy configs
STRATEGIES = {
    "ob_imbalance": {
        "active": True,
        "max_positions": 1,
        "risk_per_trade_pct": 0.015,  # 1.5% risk
        "stop_pct": 0.008,            # 0.8%
        "target_pct": 0.02,           # 2%
        "max_hold_minutes": 60,
        "min_imbalance": 1.5,         # bid/ask ratio threshold (lower = more signals)
    },
    "momentum": {
        "active": True,
        "max_positions": 1,
        "risk_per_trade_pct": 0.015,
        "stop_pct": 0.01,
        "target_pct": 0.025,
        "max_hold_minutes": 120,
        "lookback_bars": 5,           # Lookback for momentum
    },
    "mean_reversion": {
        "active": True,
        "max_positions": 1,
        "risk_per_trade_pct": 0.01,
        "stop_pct": 0.012,
        "target_pct": 0.015,
        "max_hold_minutes": 45,
        "deviation_threshold": 0.02,  # 2% from mean
    },
    "funding_squeeze": {
        "active": True,
        "max_positions": 1,
        "risk_per_trade_pct": 0.02,
        "stop_pct": 0.015,
        "target_pct": 0.03,
        "max_hold_minutes": 180,
        "funding_long_threshold": 30,  # >30% = longs crowded
        "funding_short_threshold": -15,  # <-15% = shorts crowded
    },
}

# Max total positions across all strategies
MAX_TOTAL_SCALP_POSITIONS = 3
MAX_TOTAL_EXPOSURE_PCT = 0.80  # 80% of account (leverage already has stop loss)


# === Helpers ===

def load_json(path, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def get_hyperliquid_ob(coin: str) -> dict:
    """Get mid price + funding for crypto coins. L2 depth from multi_exchange_ob.json."""
    idx_map = {"BTC": 0, "ETH": 1, "SOL": 2}
    if coin not in idx_map:
        return {}
    
    # Get mid price from allMids (works reliably)
    mids = post_hl({"type": "allMids"})
    mid = None
    if mids and "error" not in mids:
        mid = float(mids.get(coin, 0))
    
    # Get funding from metaAndAssetCtxs
    funding = 0
    ctx = post_hl({"type": "metaAndAssetCtxs"})
    if ctx and len(ctx) >= 2 and "error" not in ctx:
        universe = ctx[0].get("universe", [])
        assets = ctx[1]
        for i, u in enumerate(universe):
            if u.get("name") == coin and i < len(assets):
                funding = float(assets[i].get("funding", 0)) * 100
                break
    
    # Get OB direction from Capital.com (bid/offer spread as direction proxy)
    epic = {"BTC": "BTCUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}[coin]
    info = get_market_info(epic, CRYPTO_ACCOUNT)
    bid = info.get("bid", 0) or 0
    offer = info.get("offer", 0) or 0
    
    # Simple imbalance from Capital.com spread direction
    # Also try to load multi-exchange consensus
    imbalance = 1.0
    import os
    mo_path = os.path.join(os.path.expanduser("~/workspace"), "orderbook_data", "multi_exchange_ob.json")
    if os.path.exists(mo_path):
        try:
            with open(mo_path, "r") as f:
                mo = json.load(f)
            mc = mo.get("coins", {}).get(coin, {})
            if mc.get("consensus") == "buy":
                imbalance = 1.5
            elif mc.get("consensus") == "sell":
                imbalance = 0.5
        except Exception:
            pass
    
    return {
        "mid": mid,
        "bid": bid,
        "offer": offer,
        "imbalance": round(imbalance, 2),
        "funding": round(funding, 1),
    }


def get_capital_price(epic: str, label: str = None) -> float:
    """Get current bid/offer from Capital.com for non-crypto markets."""
    account_id = get_market_account(label) if label else MACRO_ACCOUNT
    info = get_market_info(epic, account_id)
    bid = info.get("bid", 0) or 0
    offer = info.get("offer", 0) or 0
    return (bid + offer) / 2 if bid and offer else (bid or offer)


# === Strategy Signal Generators ===

def signal_ob_imbalance(market: dict, hl_data: dict) -> dict:
    """OB imbalance signal. Works for crypto (HL data) + macro (Capital.com price)."""
    cfg = STRATEGIES["ob_imbalance"]
    
    if not cfg["active"]:
        return None
    
    # Get price — HL for crypto, Capital.com for macro
    if hl_data:
        imbalance = hl_data.get("imbalance", 1)
        mid = hl_data.get("mid", 0)
    else:
        # Macro market: use Capital.com bid/offer as imbalance proxy
        price = get_capital_price(market["epic"], market["label"])
        if not price or price <= 0:
            return None
        mid = price
        # Default neutral — macro OB signals come from momentum instead
        imbalance = 1.0
    
    if not mid:
        return None
    
    if imbalance >= cfg["min_imbalance"]:
        entry = mid
        stop = mid * (1 - cfg["stop_pct"])
        target = mid * (1 + cfg["target_pct"])
        return {"strategy": "ob_imbalance", "epic": market["epic"], "label": market["label"],
                "direction": "LONG", "entry": round(entry, 2),
                "stop": round(stop, 2), "target": round(target, 2),
                "reason": f"OB imbalance {imbalance:.1f}:1 bid/ask"}
    
    if imbalance <= (1 / cfg["min_imbalance"]):
        entry = mid
        stop = mid * (1 + cfg["stop_pct"])
        target = mid * (1 - cfg["target_pct"])
        return {"strategy": "ob_imbalance", "epic": market["epic"], "label": market["label"],
                "direction": "SHORT", "entry": round(entry, 2),
                "stop": round(stop, 2), "target": round(target, 2),
                "reason": f"OB imbalance {imbalance:.1f}:1 ask/bid"}
    
    return None


def signal_momentum(market: dict, hl_data: dict) -> dict:
    """Simple momentum: recent price direction + Capital.com price."""
    cfg = STRATEGIES["momentum"]
    if not cfg["active"]:
        return None
    
    price = hl_data.get("mid", 0) if hl_data else 0
    
    # For non-crypto: get Capital.com price
    if not price:
        price = get_capital_price(market["epic"], market["label"])
    
    if not price or price <= 0:
        return None
    
    # For crypto: use HL OB imbalance as directional proxy
    # For non-crypto: use Capital.com bid/offer spread as proxy
    if hl_data:
        imbalance = hl_data.get("imbalance", 1)
        if imbalance >= 1.4:
            direction = "LONG"
        elif imbalance <= 0.6:
            direction = "SHORT"
        else:
            return None
    else:
        # Non-crypto: use Capital.com market info for direction
        account_id = get_market_account(market["label"])
        info = get_market_info(market["epic"], account_id)
        bid = info.get("bid", 0) or 0
        offer = info.get("offer", 0) or 0
        if not bid or not offer:
            return None
        mid = (bid + offer) / 2
        price = mid
        spread_pct = (offer - bid) / mid * 100 if mid > 0 else 0
        # Skip if spread > 0.1% (too wide for macro scalping)
        if spread_pct > 0.1:
            return None
        # Direction: slight bias from bid/offer mid vs bid
        if bid > mid * 0.9999:
            direction = "LONG"
        else:
            direction = "SHORT"
    
    if direction == "LONG":
        stop = price * (1 - cfg["stop_pct"])
        target = price * (1 + cfg["target_pct"])
    else:
        stop = price * (1 + cfg["stop_pct"])
        target = price * (1 - cfg["target_pct"])
    
    return {"strategy": "momentum", "epic": market["epic"], "label": market["label"],
            "direction": direction, "entry": round(price, 2),
            "stop": round(stop, 2), "target": round(target, 2),
            "reason": f"Momentum {direction} ({market['label']} ${price:.2f})"}


def signal_funding_squeeze(market: dict, hl_data: dict) -> dict:
    """Funding squeeze signal."""
    cfg = STRATEGIES["funding_squeeze"]
    if not cfg["active"] or not hl_data:
        return None
    
    funding = hl_data.get("funding", 0)
    mid = hl_data.get("mid", 0)
    if not mid:
        return None
    
    if funding > cfg["funding_long_threshold"]:
        entry = mid
        stop = mid * (1 + cfg["stop_pct"])
        target = mid * (1 - cfg["target_pct"])
        return {"strategy": "funding_squeeze", "epic": market["epic"], "label": market["label"],
                "direction": "SHORT", "entry": round(entry, 2),
                "stop": round(stop, 2), "target": round(target, 2),
                "reason": f"Funding {funding:.1f}% → longs crowded, squeeze down"}
    
    if funding < cfg["funding_short_threshold"]:
        entry = mid
        stop = mid * (1 - cfg["stop_pct"])
        target = mid * (1 + cfg["target_pct"])
        return {"strategy": "funding_squeeze", "epic": market["epic"], "label": market["label"],
                "direction": "LONG", "entry": round(entry, 2),
                "stop": round(stop, 2), "target": round(target, 2),
                "reason": f"Funding {funding:.1f}% → shorts crowded, squeeze up"}
    
    return None


# === Execution ===

def get_active_strategies() -> list:
    """Get list of currently active strategies (auto-pause losers)."""
    state = load_json(STRATEGY_STATE_FILE, {})
    active = []
    for name, cfg in STRATEGIES.items():
        if not cfg["active"]:
            continue
        # Check if paused due to consecutive losses
        s = state.get(name, {"consecutive_losses": 0, "paused": False})
        if s.get("paused"):
            continue
        active.append(name)
    return active


def count_open_scalps(account_id: str = None) -> int:
    """Count current open scalp positions. If account_id given, filter to that account only."""
    if account_id:
        positions = check_positions(account_id)
        return len(positions)
    # All scalp accounts
    total = 0
    for acct_key in ["crypto", "macro"]:
        positions = check_positions(ACCOUNTS[acct_key])
        total += len(positions)
    return total


def get_market_account(label: str) -> str:
    """Get Capital.com account ID for a market label."""
    return get_account_for(label)


def execute_signal(signal: dict) -> dict:
    """Execute a scalp signal on Capital.com demo."""
    if not signal:
        return {"executed": False, "reason": "no_signal"}
    
    # Determine which account to use
    label = signal["label"]
    account_id = get_market_account(label)
    account_key = [k for k, v in ACCOUNTS.items() if v == account_id][0]
    
    # Check position limits
    open_count = count_open_scalps()
    if open_count >= MAX_TOTAL_SCALP_POSITIONS:
        return {"executed": False, "reason": f"max_positions ({open_count}/{MAX_TOTAL_SCALP_POSITIONS})"}
    
    # Check if we already have this epic
    existing = check_positions(account_id)
    if any(p["epic"] == signal["epic"] for p in existing):
        return {"executed": False, "reason": f"{signal['label']} already open"}
    
    # Check exposure on this account
    bal = get_account_balance(account_id)
    balance = bal.get("balance", 1000)
    total_exposure = sum(
        p.get("size", 0) * p.get("current", p.get("entry", 0))
        for p in existing
    )
    signal_exposure = balance * STRATEGIES[signal["strategy"]]["risk_per_trade_pct"] * 10
    if total_exposure + signal_exposure > balance * MAX_TOTAL_EXPOSURE_PCT:
        return {"executed": False, "reason": "exposure_limit"}
    
    # Calculate size
    cfg = STRATEGIES[signal["strategy"]]
    risk_amount = balance * cfg["risk_per_trade_pct"]
    if signal["direction"] == "LONG":
        risk_per_unit = abs(signal["entry"] - signal["stop"])
    else:
        risk_per_unit = abs(signal["stop"] - signal["entry"])
    
    if risk_per_unit <= 0:
        risk_per_unit = signal["entry"] * 0.005
    
    size = round(risk_amount / risk_per_unit, 4)
    size = max(size, 0.01)  # Minimum size
    
    # Execute!
    result = open_position(
        ticker=signal["label"],
        direction=signal["direction"],
        entry=signal["entry"],
        stop=signal["stop"],
        target=signal["target"],
        market=account_key,
        size=size,
    )
    
    if "error" in result:
        return {"executed": False, "reason": result["error"]}
    
    # Log trade
    trade = {
        "timestamp": datetime.now(HKT).isoformat(),
        "strategy": signal["strategy"],
        "epic": signal["epic"],
        "label": signal["label"],
        "direction": signal["direction"],
        "entry": signal["entry"],
        "stop": signal["stop"],
        "target": signal["target"],
        "size": size,
        "deal_ref": result.get("deal_ref", "?"),
        "deal_id": result.get("deal_id", "?"),
        "status": "open",
        "pnl": 0,
        "exit_price": 0,
        "account": account_key,
    }
    
    trades = load_json(LOG_FILE, [])
    trades.append(trade)
    save_json(LOG_FILE, trades)
    
    return {"executed": True, "trade": trade}


def update_strategy_state():
    """Update strategy performance: auto-pause losing strategies."""
    trades = load_json(LOG_FILE, [])
    state = load_json(STRATEGY_STATE_FILE, {})
    
    # Only look at last 24h
    cutoff = (datetime.now(HKT) - timedelta(hours=24)).isoformat()
    recent = [t for t in trades if t["timestamp"] >= cutoff and t["status"] != "open"]
    
    for name in STRATEGIES:
        if name not in state:
            state[name] = {"consecutive_losses": 0, "paused": False, "total_trades": 0, "total_pnl": 0}
        
        strat_trades = [t for t in recent if t["strategy"] == name]
        
        # Count consecutive losses
        consec = 0
        for t in reversed(strat_trades):
            if t.get("pnl", 0) < 0:
                consec += 1
            else:
                break
        
        state[name]["consecutive_losses"] = consec
        state[name]["total_trades"] = len(strat_trades)
        state[name]["total_pnl"] = round(sum(t.get("pnl", 0) for t in strat_trades), 2)
        
        # Auto-pause after 5 consecutive losses
        if consec >= 5:
            state[name]["paused"] = True
        elif consec == 0 and state[name].get("paused"):
            state[name]["paused"] = False  # Auto-resume if winning again
    
    save_json(STRATEGY_STATE_FILE, state)


def update_closed_trades():
    """Check Capital.com for closed positions across all scalp accounts and update our log."""
    trades = load_json(LOG_FILE, [])
    
    # Collect open deal IDs from both crypto and macro accounts
    open_deal_ids = set()
    for acct_key in ["crypto", "macro"]:
        positions = check_positions(ACCOUNTS[acct_key])
        for p in positions:
            if p.get("deal_id"):
                open_deal_ids.add(p["deal_id"])
    
    updated = False
    for t in trades:
        if t["status"] != "open":
            continue
        deal_id = t.get("deal_id", "")
        if deal_id and deal_id not in open_deal_ids:
            # Position closed — need to find exit info from history
            t["status"] = "closed"
            t["closed_at"] = datetime.now(HKT).isoformat()
            # P&L estimation from account balance change
            updated = True
    
    if updated:
        save_json(LOG_FILE, trades)


# === Main Scan Loop ===

def run_scan():
    """Single scan cycle. Meant to be called by cron every 15-30 min."""
    now = datetime.now(HKT)
    
    # Update closed positions first
    update_closed_trades()
    update_strategy_state()
    
    open_count = count_open_scalps()
    active_strategies = get_active_strategies()
    
    print(f"[{now.strftime('%H:%M')}] ⚡ Scalp scan | Open: {open_count}/{MAX_TOTAL_SCALP_POSITIONS} | Active strategies: {len(active_strategies)}")
    
    if open_count >= MAX_TOTAL_SCALP_POSITIONS:
        print(f"  📭 Max positions reached, skipping new entries")
        return
    
    signals_found = 0
    executed = 0
    
    for market in MARKETS:
        # Get HL data for crypto, Capital.com price for others
        hl_data = {}
        if market["type"] == "crypto":
            hl_data = get_hyperliquid_ob(market["label"])
        
        # Run each active strategy
        for strat_name in active_strategies:
            signal = None
            if strat_name == "ob_imbalance":
                signal = signal_ob_imbalance(market, hl_data)
            elif strat_name == "momentum":
                signal = signal_momentum(market, hl_data)
            elif strat_name == "funding_squeeze":
                signal = signal_funding_squeeze(market, hl_data)
            
            if signal:
                signals_found += 1
                result = execute_signal(signal)
                if result.get("executed"):
                    executed += 1
                    print(f"  ✅ {signal['label']} {signal['direction']} | {signal['strategy']} | Entry ${signal['entry']:.2f} | {signal['reason']}")
                else:
                    print(f"  ⏭️  {signal['label']} {signal['direction']} | {signal['strategy']} | SKIP: {result.get('reason')}")
    
    if signals_found == 0:
        print(f"  📭 No signals")
    
    print(f"  📊 {signals_found} signals | {executed} executed | {open_count + executed} total open")
    return {"signals": signals_found, "executed": executed}


def run_daily_report():
    """Generate daily scalp performance report across all scalp accounts."""
    now = datetime.now(HKT)
    trades = load_json(LOG_FILE, [])
    state = load_json(STRATEGY_STATE_FILE, {})
    
    # Get both account balances
    bal_crypto = get_account_balance(CRYPTO_ACCOUNT)
    bal_macro = get_account_balance(MACRO_ACCOUNT)
    
    # Filter today's trades
    today = now.strftime("%Y-%m-%d")
    today_trades = [t for t in trades if t["timestamp"][:10] == today]
    closed_today = [t for t in today_trades if t["status"] == "closed"]
    
    total_balance = bal_crypto.get("balance", 0) + bal_macro.get("balance", 0)
    total_pnl = bal_crypto.get("pnl", 0) + bal_macro.get("pnl", 0)
    
    print("=" * 60)
    print(f"⚡ SCALP DAILY REPORT — {now.strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 60)
    print(f"\n📂 Crypto Account: ${bal_crypto.get('balance', 0):.2f} | P&L: ${bal_crypto.get('pnl', 0):+.2f}")
    print(f"📂 Macro Account:  ${bal_macro.get('balance', 0):.2f} | P&L: ${bal_macro.get('pnl', 0):+.2f}")
    print(f"📂 TOTAL:          ${total_balance:.2f} | P&L: ${total_pnl:+.2f}")
    
    # Per-strategy
    print("\n📊 Per Strategy:")
    for name, cfg in STRATEGIES.items():
        st = [t for t in closed_today if t["strategy"] == name]
        if not st:
            print(f"  {name}: 0 trades")
            continue
        wins = [t for t in st if t.get("pnl", 0) > 0]
        total = sum(t.get("pnl", 0) for t in st)
        wr = len(wins) / len(st) * 100 if st else 0
        status = "⏸️ PAUSED" if state.get(name, {}).get("paused") else "▶️ ACTIVE"
        print(f"  {name} ({status}): {len(st)} trades | WR {wr:.0f}% | P&L ${total:+.2f}")
    
    # Open positions (both accounts)
    positions_crypto = check_positions(CRYPTO_ACCOUNT)
    positions_macro = check_positions(MACRO_ACCOUNT)
    all_positions = positions_crypto + positions_macro
    if all_positions:
        print(f"\n📂 Open ({len(all_positions)}):")
        print(f"  [Crypto Account]")
        if positions_crypto:
            for p in positions_crypto:
                print(f"  {p['ticker']} {p['direction']} | Entry ${p['entry']:.2f} | P&L ${p['pnl_amt']:+.2f}")
        else:
            print(f"  (none)")
        print(f"  [Macro Account]")
        if positions_macro:
            for p in positions_macro:
                print(f"  {p['ticker']} {p['direction']} | Entry ${p['entry']:.2f} | P&L ${p['pnl_amt']:+.2f}")
        else:
            print(f"  (none)")
    
    # Recommendations
    print(f"\n💡 建議:")
    best_strat = None
    best_pnl = -999
    for name in STRATEGIES:
        st = [t for t in closed_today if t["strategy"] == name]
        pnl = sum(t.get("pnl", 0) for t in st)
        if pnl > best_pnl:
            best_pnl = pnl
            best_strat = name
    
    if best_strat:
        cfg = STRATEGIES[best_strat]
        if best_pnl > 0 and cfg["risk_per_trade_pct"] < 0.03:
            print(f"  ✅ {best_strat} 表現最佳 (${best_pnl:+.2f}) → 建議加大倉位")
        else:
            print(f"  ⚠️ All strategies negative or break-even → 繼續觀察，保持現狀")
    
    # Save report
    report = {
        "timestamp": now.isoformat(),
        "crypto": {"balance": bal_crypto.get("balance", 0), "pnl": bal_crypto.get("pnl", 0)},
        "macro": {"balance": bal_macro.get("balance", 0), "pnl": bal_macro.get("pnl", 0)},
        "total_balance": total_balance,
        "total_pnl": total_pnl,
        "today_trades": len(closed_today),
        "strategies": {name: {
            "trades": len([t for t in closed_today if t["strategy"] == name]),
            "pnl": sum(t.get("pnl", 0) for t in closed_today if t["strategy"] == name),
        } for name in STRATEGIES},
    }
    save_json(DAILY_REPORT_FILE, report)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--scan"
    
    if mode == "--scan":
        run_scan()
    elif mode == "--daily":
        run_daily_report()
    elif mode == "--status":
        print("=" * 50)
        print("⚡ SCALP STATUS")
        # Crypto account
        bal_c = get_account_balance(CRYPTO_ACCOUNT)
        print(f"[Crypto] Account: ${bal_c.get('balance', 0):.2f} | P&L: ${bal_c.get('pnl', 0):+.2f}")
        positions_c = check_positions(CRYPTO_ACCOUNT)
        if positions_c:
            for p in positions_c:
                print(f"  {p['ticker']} {p['direction']} | ${p['entry']:.2f} → ${p['current']:.2f} | ${p['pnl_amt']:+.2f}")
        else:
            print(f"  (no positions)")
        # Macro account
        bal_m = get_account_balance(MACRO_ACCOUNT)
        print(f"[Macro]  Account: ${bal_m.get('balance', 0):.2f} | P&L: ${bal_m.get('pnl', 0):+.2f}")
        positions_m = check_positions(MACRO_ACCOUNT)
        if positions_m:
            for p in positions_m:
                print(f"  {p['ticker']} {p['direction']} | ${p['entry']:.2f} → ${p['current']:.2f} | ${p['pnl_amt']:+.2f}")
        else:
            print(f"  (no positions)")
        state = load_json(STRATEGY_STATE_FILE, {})
        for name, s in state.items():
            print(f"  {name}: paused={s.get('paused')} consec_loss={s.get('consecutive_losses')} total_pnl=${s.get('total_pnl', 0):.2f}")
    elif mode == "--reset":
        # Reset all strategy states (fresh start)
        state = {}
        for name in STRATEGIES:
            state[name] = {"consecutive_losses": 0, "paused": False, "total_trades": 0, "total_pnl": 0}
        save_json(STRATEGY_STATE_FILE, state)
        print("✅ All strategies reset to active")
    else:
        print("Usage: scalp_runner.py [--scan|--daily|--status|--reset]")
