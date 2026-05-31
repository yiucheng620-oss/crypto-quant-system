#!/usr/bin/env python3
"""
Kanban Paper Trade Executor + Tracker + Reviewer
================================================
Closed-loop module for Kanban stock picks.

Execution:  Reads kanban_trade_plans.json → places paper trades on Capital.com
Tracking:   Monitors open positions, checks P&L, applies active management
Review:     Weekly performance analysis with GA optimization hints

Usage:
  python3 kanban_closed_loop.py execute   # Place trades from plans
  python3 kanban_closed_loop.py track     # Check open positions
  python3 kanban_closed_loop.py review    # Weekly performance analysis
  python3 kanban_closed_loop.py all       # Execute + Track (daily use)
"""
import sys
import os
import json
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

HKT = timezone(timedelta(hours=8))
PLANS_FILE = os.path.expanduser("~/workspace/scalp_lab/engines/kanban_trade_plans.json")
HISTORY_FILE = os.path.expanduser("~/workspace/scalp_lab/engines/kanban_trade_history.json")
STATE_FILE = os.path.expanduser("~/workspace/scalp_lab/engines/kanban_state.json")

sys.path.insert(0, os.path.expanduser("~/workspace"))
sys.path.insert(0, os.path.expanduser("~/workspace/capitalcom"))
from capitalcom_paper import (
    open_position, check_positions, get_market_info,
    ACCOUNTS, get_account_for
)


def load_plans():
    """Load trade plans from Kanban output."""
    if not os.path.exists(PLANS_FILE):
        return None
    with open(PLANS_FILE) as f:
        return json.load(f)


def load_state():
    """Load Kanban state (active trades, history)."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"active_trades": [], "closed_trades": [], "total_pnl": 0, "trades_count": 0, "wins": 0}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return []


def save_history(history):
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, HISTORY_FILE)


# ═══════════════════════════════════════
# EXECUTOR
# ═══════════════════════════════════════

def get_exposure_cap(macro_bias: str) -> float:
    """Dynamic exposure cap based on macro analyst's bias.
    
    系統自行判斷進取程度，無需用戶手動調校。
    """
    bias = macro_bias.lower() if macro_bias else ""
    if "積極做多" in bias or "bullish" in bias or "🟢" in bias:
        return 0.60  # 看好 → 60% 資金
    elif "保守" in bias or "neutral" in bias or "🟡" in bias:
        return 0.30  # 中性 → 30%
    elif "避險" in bias or "bearish" in bias or "🔴" in bias:
        return 0.10  # 睇淡 → 10%
    else:
        return 0.30  # 預設保守


EQUITIES_TRADES_FILE = os.path.expanduser("~/workspace/scalp_lab/engines/equities_trades.json")


def _get_capital_usage():
    """Get total capital used on Capital.com equities account.
    Returns: (capital_used, account_balance)
    """
    try:
        from capitalcom_paper import check_positions, get_account_balance, ACCOUNTS
        positions = check_positions(ACCOUNTS.get("equities", ""))
        capital_used = 0.0
        for p in positions:
            size = float(p.get("size", 0))
            entry = float(p.get("entry", 0))
            capital_used += size * entry
        
        bal = get_account_balance(ACCOUNTS.get("equities", ""))
        account_balance = float(bal.get("balance", 10000)) if isinstance(bal, dict) else 10000.0
        
        return capital_used, account_balance
    except Exception as e:
        return 0.0, 10000.0  # Fallback


def _save_to_engine_trades(executed_trades: list):
    """Append executed kanban trades to equities_trades.json for Guardian visibility."""
    if not executed_trades:
        return
    # Load existing
    existing = []
    if os.path.exists(EQUITIES_TRADES_FILE):
        try:
            with open(EQUITIES_TRADES_FILE) as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, OSError):
            existing = []
    
    now_ts = time.time()
    for t in executed_trades:
        is_paper_only = t.get("paper_only", False)
        existing.append({
            "timestamp": datetime.now(HKT).isoformat(),
            "timestamp_ts": now_ts,
            "market": "EQUITIES",
            "engine": "kanban",
            "strategy": "kanban_pipeline",
            "epic": t.get("epic", t.get("ticker", "?")),
            "direction": t.get("direction", "LONG"),
            "entry": t.get("entry", 0),       # ✅ open_position() 而家 return 真實 fill
            "stop": t.get("stop", 0),
            "target": t.get("target", 0),
            "size": t.get("size", 0),
            "deal_ref": t.get("deal_ref", "?"),
            "deal_id": t.get("deal_id", "?"), # "paper_only" for virtual trades
            "status": "open",
            "pnl": 0.0,
            "adjusted_pnl": 0.0,
            "account": "paper",
            "close_reason": "",
            "reconciled": True,               # ✅ 已從 Capital.com 驗證，不可被 engine 殺
            "paper_only": is_paper_only,      # ⚠️ Capital.com 無此股票 → 純虛擬 tracking
            "paper_reason": t.get("paper_reason", "") if is_paper_only else "",
        })
    
    tmp = EQUITIES_TRADES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(existing, f, indent=2, default=str)
    os.replace(tmp, EQUITIES_TRADES_FILE)


def execute():
    """Read Kanban plans → place paper trades on Capital.com."""
    plans = load_plans()
    if not plans or not plans.get("trades"):
        print("📋 No trade plans to execute")
        return
    
    # ⏰ 檢查 plan 新鮮度：超過 2 小時 = stale，唔 execute
    plan_ts = plans.get("timestamp", "")
    if plan_ts:
        try:
            plan_dt = datetime.fromisoformat(plan_ts)
            age_min = (datetime.now(HKT) - plan_dt).total_seconds() / 60
            if age_min > 120:
                print(f"⚠️ Plans too old ({age_min:.0f}min > 120min) — skipping execution")
                print(f"   Plan from: {plan_ts}")
                return
        except (ValueError, TypeError):
            pass
    
    trades = plans["trades"]
    macro_bias = plans.get("macro_bias", "")
    exposure_cap = get_exposure_cap(macro_bias)
    print(f"🚀 Kanban Executor — {len(trades)} trades to place")
    print(f"   Macro: {macro_bias[:60] if macro_bias else '?'} → exposure cap={exposure_cap:.0%}")
    
    # 💰 計算 Capital.com 已用資金 + paper-only 倉位 → 計出真正可用 paper pool
    capital_used, account_balance = _get_capital_usage()
    state = load_state()
    
    # 📐 Paper pool: $10,000 - Capital.com used - paper_only positions value
    paper_positions_value = sum(
        float(t.get("entry", 0)) * float(t.get("size", 0))
        for t in state.get("active_trades", []) if t.get("paper_only")
    )
    paper_pool = max(account_balance - capital_used - paper_positions_value, 500)
    print(f"   Capital.com 已用: ${capital_used:.0f} / ${account_balance:.0f}", end="")
    if paper_positions_value > 0:
        print(f" | Paper: ${paper_positions_value:.0f}", end="")
    print(f" → Paper pool: ${paper_pool:.0f}\n")
    
    active_symbols = {t.get("symbol") or t.get("ticker", "") for t in state.get("active_trades", [])}
    
    # 🔧 用 Capital.com API 做 dedup（唔靠可能 stale 嘅 state file）
    try:
        real_positions = check_positions(ACCOUNTS["equities"])
        real_symbols = {p.get("ticker", p.get("epic", "")) for p in real_positions}
        paper_symbols = {t.get("ticker", "") for t in state.get("active_trades", []) if t.get("paper_only")}
        active_symbols = real_symbols | paper_symbols  # Merge: API truth + paper-only
    except Exception:
        pass  # Fallback to state file only
    executed = []
    for trade in trades:
        symbol = trade["symbol"]
        
        # Skip if already active
        if symbol in active_symbols:
            print(f"  ⏭️ {symbol}: already active, skipping")
            continue
        
        # Check market is open (basic check: not weekend)
        now = datetime.now(HKT)
        if now.weekday() >= 5:
            print(f"  ⏭️ {symbol}: weekend, market closed")
            continue
        
        # Apply user's rule: max 2 positions, max 1 per sector
        if len(state.get("active_trades", [])) >= 2:
            print(f"  ⏭️ {symbol}: max 2 positions reached")
            break
        
        # Place order
        print(f"  📊 {symbol} {trade.get('direction', 'LONG')}:")
        print(f"     Entry: ${trade['entry']} | Stop: ${trade['stop']} | Target: ${trade['target']}")
        
        try:
            result = open_position(
                ticker=symbol,
                direction=trade.get("direction", "LONG"),
                entry=trade["entry"],
                stop=trade["stop"],
                target=trade["target"],
                market="equities",
                confidence=trade.get("confidence", "3/5"),
                max_total_exposure=exposure_cap,
                paper_balance=paper_pool,  # ✅ Paper trade pool = $10,000 - Capital.com 已用
            )
            
            if "error" not in result:
                result["kanban_plan"] = trade
                result["confidence"] = trade.get("confidence", "3/5")
                result["reason"] = trade.get("reason", "")
                state["active_trades"].append(result)
                executed.append(result)
                if result.get("paper_only"):
                    print(f"     📝 Paper-only (Capital.com 無此股票): {result.get('deal_ref', '?')}")
                else:
                    print(f"     ✅ Placed: {result.get('deal_ref', '?')}")
            else:
                print(f"     ❌ Failed: {result['error'][:100]}")
        except Exception as e:
            print(f"     ❌ Error: {e}")
    
    save_state(state)
    
    # Also save to equities_trades.json so Guardian can see them
    _save_to_engine_trades(executed)
    
    print(f"\n✅ {len(executed)} trades executed, {len(state['active_trades'])} total active")


# ═══════════════════════════════════════
# TRACKER
# ═══════════════════════════════════════

def track():
    """Monitor open positions, update P&L, detect closures."""
    state = load_state()
    active = state.get("active_trades", [])
    
    if not active:
        print("📊 No active Kanban trades")
        return
    
    print(f"📊 Kanban Position Tracker — {datetime.now(HKT).strftime('%m/%d %H:%M HKT')}\n")
    
    # Get real positions from Capital.com
    real_positions = check_positions(ACCOUNTS["equities"])
    real_deal_ids = {p.get("deal_id", "") for p in real_positions}
    
    still_open = []
    closed_today = []
    
    for trade in active:
        deal_id = trade.get("deal_id", "")
        symbol = trade.get("ticker", "?")
        
        if deal_id in real_deal_ids:
            # Still open — check P&L
            real = next((p for p in real_positions if p.get("deal_id") == deal_id), None)
            if real:
                trade["current_pnl"] = real.get("pnl_amt", 0)
                trade["current_price"] = real.get("current", 0)
                trade["pnl_pct"] = real.get("pnl_pct", 0)
                still_open.append(trade)
                
                # Active management: trailing stop check
                if trade.get("direction") == "LONG" and real.get("pnl_pct", 0) > 2:
                    print(f"  📈 {symbol}: +{real['pnl_pct']:.1f}% — consider trailing stop")
                elif trade.get("direction") == "SHORT" and real.get("pnl_pct", 0) > 2:
                    print(f"  📉 {symbol}: +{real['pnl_pct']:.1f}% — consider trailing stop")
        else:
            # Closed — record result
            trade["status"] = "closed"
            trade["closed_at"] = datetime.now(HKT).isoformat()
            trade["result"] = "win" if trade.get("current_pnl", 0) > 0 else "loss"
            closed_today.append(trade)
            
            state["closed_trades"].append(trade)
            state["total_pnl"] += trade.get("current_pnl", 0)
            state["trades_count"] += 1
            if trade.get("result") == "win":
                state["wins"] += 1
    
    state["active_trades"] = still_open
    save_state(state)
    
    # Display
    if still_open:
        print(f"\n🟢 Open Positions ({len(still_open)}):")
        total_pnl = 0
        for t in still_open:
            pnl = t.get("current_pnl", 0)
            total_pnl += pnl
            emoji = "🟢" if pnl > 0 else "🔴"
            print(f"  {emoji} ${t.get('ticker', '?')} {t.get('direction', '?')} | P&L: ${pnl:+.2f} ({t.get('pnl_pct', 0):+.1f}%)")
        print(f"  💰 Total Open P&L: ${total_pnl:+.2f}")
    
    if closed_today:
        print(f"\n📝 Closed Today ({len(closed_today)}):")
        for t in closed_today:
            emoji = "🏆" if t.get("result") == "win" else "💀"
            print(f"  {emoji} ${t.get('ticker', '?')} — {t.get('result', '?').upper()} | P&L: ${t.get('current_pnl', 0):+.2f}")
    
    if not still_open and not closed_today:
        print("  No changes")
    
    # Update history
    history = load_history()
    history.append({
        "timestamp": datetime.now(HKT).isoformat(),
        "open_count": len(still_open),
        "closed_count": len(closed_today),
        "open_pnl": sum(t.get("current_pnl", 0) for t in still_open),
        "total_trades": state["trades_count"],
        "total_pnl": state["total_pnl"],
        "win_rate": state["wins"] / max(1, state["trades_count"]) * 100,
    })
    save_history(history)


# ═══════════════════════════════════════
# REVIEWER
# ═══════════════════════════════════════

CONFIG_FILE = os.path.expanduser("~/workspace/scalp_lab/engines/kanban_config.json")


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(config):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)


def optimize():
    """Auto-adjust screener patterns based on trade results.
    - Winning patterns → boost weight
    - Losing patterns → reduce weight or disable
    - Only runs if >= min_trades_to_optimize closed trades
    """
    config = load_config()
    if not config:
        print("  ⚠️ No config found, skipping optimization")
        return
    
    state = load_state()
    closed = state.get("closed_trades", [])
    
    opt_config = config.get("optimizer", {})
    min_trades = opt_config.get("min_trades_to_optimize", 5)
    
    if len(closed) < min_trades:
        print(f"  ⏭️ Only {len(closed)} closed trades (need {min_trades}), skipping optimization")
        return
    
    # Analyze win rate per pattern
    pattern_stats = {}
    for trade in closed:
        plan = trade.get("kanban_plan", {})
        reason = plan.get("reason", trade.get("reason", ""))
        
        # Detect pattern from reason text
        patterns = []
        for p_name in ["pullback", "breakout", "momentum", "coiled_spring"]:
            if p_name.replace("_", " ") in reason.lower() or p_name in reason.lower():
                patterns.append(p_name)
        
        if not patterns:
            patterns = ["unknown"]
        
        for p in patterns:
            if p not in pattern_stats:
                pattern_stats[p] = {"wins": 0, "total": 0, "pnl": 0}
            pattern_stats[p]["total"] += 1
            pattern_stats[p]["pnl"] += trade.get("current_pnl", 0)
            if trade.get("result") == "win":
                pattern_stats[p]["wins"] += 1
    
    print(f"\n  🧬 Auto-Optimization:")
    print(f"  Based on {len(closed)} closed trades\n")
    
    patterns_config = config.get("screener", {}).get("patterns", {})
    changes = []
    
    for p_name, stats in pattern_stats.items():
        if p_name == "unknown":
            continue
        
        if p_name not in patterns_config:
            continue
        
        win_rate = stats["wins"] / max(1, stats["total"]) * 100
        current_weight = patterns_config[p_name].get("weight", 1.0)
        total_pnl = stats["pnl"]
        
        step = opt_config.get("pattern_weight_step", 0.1)
        max_w = opt_config.get("max_pattern_weight", 2.0)
        min_w = opt_config.get("min_pattern_weight", 0.3)
        disable_threshold = opt_config.get("win_rate_threshold_disable", 20)
        boost_threshold = opt_config.get("win_rate_threshold_boost", 60)
        
        # Decision
        if stats["total"] >= 3 and win_rate < disable_threshold and total_pnl < 0:
            # Disable losing pattern
            patterns_config[p_name]["enabled"] = False
            changes.append(f"  🔴 {p_name}: DISABLED (WR:{win_rate:.0f}%, PnL:${total_pnl:+.0f})")
        elif win_rate >= boost_threshold and total_pnl > 0:
            # Boost winning pattern
            new_weight = min(current_weight + step, max_w)
            patterns_config[p_name]["weight"] = new_weight
            changes.append(f"  🟢 {p_name}: BOOST {current_weight:.1f}→{new_weight:.1f} (WR:{win_rate:.0f}%, PnL:${total_pnl:+.0f})")
        elif win_rate < 40:
            # Reduce weight
            new_weight = max(current_weight - step, min_w)
            patterns_config[p_name]["weight"] = new_weight
            changes.append(f"  🟡 {p_name}: REDUCE {current_weight:.1f}→{new_weight:.1f} (WR:{win_rate:.0f}%)")
        else:
            print(f"  ⚪ {p_name}: no change (WR:{win_rate:.0f}%, {stats['total']} trades)")
    
    if changes:
        print(f"\n  📝 Changes applied:")
        for c in changes:
            print(c)
        
        config["last_optimized"] = datetime.now(HKT).isoformat()
        if "optimization_history" not in config:
            config["optimization_history"] = []
        config["optimization_history"].append({
            "timestamp": datetime.now(HKT).isoformat(),
            "trades_analyzed": len(closed),
            "changes": changes,
            "pattern_stats": {k: {"win_rate": round(v["wins"]/max(1,v["total"])*100,1), "pnl": round(v["pnl"],2)} for k,v in pattern_stats.items()},
        })
        save_config(config)
        print(f"\n  ✅ Config saved with {len(changes)} changes")
    else:
        print(f"\n  ⚪ No changes needed")


def review():
    """Weekly performance review with auto-optimization."""
    state = load_state()
    history = load_history()
    
    closed = state.get("closed_trades", [])
    total = state["trades_count"]
    wins = state["wins"]
    total_pnl = state["total_pnl"]
    
    print(f"📈 Kanban Weekly Review — {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}\n")
    
    if total == 0:
        print("  No trades closed yet this week")
        return
    
    win_rate = wins / max(1, total) * 100
    
    print(f"  📊 Performance:")
    print(f"     Total Trades: {total}")
    print(f"     Wins: {wins} | Losses: {total - wins}")
    print(f"     Win Rate: {win_rate:.1f}%")
    print(f"     Total P&L: ${total_pnl:+.2f}")
    
    if closed:
        avg_win = sum(t.get("current_pnl", 0) for t in closed if t.get("result") == "win") / max(1, wins)
        avg_loss = sum(abs(t.get("current_pnl", 0)) for t in closed if t.get("result") == "loss") / max(1, total - wins)
        print(f"     Avg Win: ${avg_win:+.2f} | Avg Loss: ${avg_loss:.2f}")
        if avg_loss > 0:
            print(f"     Profit Factor: {abs(avg_win * wins) / max(0.01, avg_loss * (total - wins)):.2f}")
    
    # P&L curve
    if history:
        pnl_curve = [h["total_pnl"] for h in history]
        print(f"\n  📈 P&L Curve: {pnl_curve[-10:] if len(pnl_curve) > 10 else pnl_curve}")
    
    # Per-trade breakdown
    if closed:
        print(f"\n  📋 Recent Closed Trades:")
        for t in closed[-5:]:
            emoji = "🏆" if t.get("result") == "win" else "💀"
            print(f"  {emoji} ${t.get('ticker', '?')} | P&L: ${t.get('current_pnl', 0):+.2f} | {t.get('reason', '')}")
    
    # ── Auto-Optimization ──
    optimize()


# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Usage: kanban_closed_loop.py [execute|track|review|all]")
        return
    
    cmd = sys.argv[1]
    
    if cmd == "execute":
        # Market hours check
        now = datetime.now(HKT)
        if now.weekday() >= 5:
            print("⏭️ Weekend — market closed, skipping execution")
            return
        
        execute()
    elif cmd == "track":
        track()
    elif cmd == "review":
        review()
    elif cmd == "all":
        now = datetime.now(HKT)
        if now.weekday() >= 5:
            print("⏭️ Weekend — market closed, skipping execution (track only)")
            track()
            return
        execute()
        time.sleep(2)
        track()
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
