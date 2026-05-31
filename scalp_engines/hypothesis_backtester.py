#!/usr/bin/env python3
"""
📊 HYPOTHESIS BACKTESTER — Sunday WFA Backtesting & Paper Pool Promotion
=======================================================================
Runs Sunday 08:00 HKT. Reads weekly_hypotheses.json from Saturday's review.

Pipeline:
  1. Load hypotheses from weekly_hypotheses.json
  2. For each hypothesis with test_params:
     a. Create paper_trading config variant
     b. Walk-forward backtest (train 2mo, test 1mo) using historical trade data
     c. Score: Sharpe, MaxDD, WinRate, ProfitFactor, TotalP&L
     d. Gate: PASS → paper pool | MARGINAL → watchlist | FAIL → archive
  3. Check paper_trading_pool.json for promotion candidates
     - Max 3 active paper strategies simultaneously
     - 1 week shadow mode → positive P&L → promote to live_config.json
  4. Output: hypothesis_results.json, paper_trading_pool.json, live_config_updates

Key: AGGRESSIVE auto-promotion. No human approval needed for paper pool entry.
Paper pool promotion to live is automatic after 1 week positive.

Cron: 0 0 * * 0 (Sunday 00:00 UTC = 08:00 HKT)
"""

import json
import math
import os
import sys
import time
import copy
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple
from statistics import mean, stdev, StatisticsError

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
REVIEW_DIR = os.path.join(WORKSPACE, "reviews")
HYPOTHESES_FILE = os.path.join(REVIEW_DIR, "weekly_hypotheses.json")
STRATEGY_CONFIG_FILE = os.path.join(ENG_DIR, "strategy_config.json")

# ── Output files ──
HYPOTHESIS_RESULTS_FILE = os.path.join(REVIEW_DIR, "hypothesis_results.json")
PAPER_POOL_FILE = os.path.join(REVIEW_DIR, "paper_trading_pool.json")
LIVE_UPDATES_FILE = os.path.join(REVIEW_DIR, "live_config_updates.json")

os.makedirs(REVIEW_DIR, exist_ok=True)

# ── Constants ──
BACKTEST_MONTHS = 3
TRAIN_MONTHS = 2
TEST_MONTHS = 1
MAX_ACTIVE_PAPER = 3
PAPER_SHADOW_DAYS = 7
ANNUAL_TRADING_DAYS = 252


# ==================== I/O Helpers ====================

def _load_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path: str, data: Any):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


# ==================== Trade Data Loader ====================

def load_all_trades() -> Dict[str, List[Dict]]:
    """Load all engine trade files. Returns {engine_name: [trades]}."""
    all_trades = {}
    for fname in sorted(os.listdir(ENG_DIR)):
        if not fname.endswith("_trades.json"):
            continue
        engine = fname.replace("_trades.json", "")
        path = os.path.join(ENG_DIR, fname)
        try:
            with open(path) as f:
                trades = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        # Only closed trades with actual P&L
        closed = [
            t for t in trades
            if t.get("status") in ("closed",) and "adjusted_pnl" in t
        ]
        if closed:
            all_trades[engine] = closed
    return all_trades


def split_walk_forward(trades: List[Dict], now_ts: float
                       ) -> Tuple[List[Dict], List[Dict]]:
    """
    Walk-forward split:
    - Train: trades from 3 months ago to 1 month ago
    - Test: trades from 1 month ago to present
    Returns (train_trades, test_trades).
    """
    three_months = now_ts - (BACKTEST_MONTHS * 30 * 86400)
    one_month = now_ts - (TEST_MONTHS * 30 * 86400)

    train = []
    test = []
    for t in trades:
        ts = t.get("timestamp_ts", 0)
        if ts <= 0:
            # Fallback: try parsing ISO timestamp
            ts_str = t.get("timestamp", "")
            if ts_str:
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    ts = dt.timestamp()
                except (ValueError, TypeError):
                    continue
            else:
                continue
        if three_months <= ts < one_month:
            train.append(t)
        elif ts >= one_month:
            test.append(t)
    return train, test


# ==================== Scoring Engine ====================

def compute_sharpe(pnls: List[float], periods_per_year: int = ANNUAL_TRADING_DAYS) -> float:
    """Compute annualized Sharpe ratio from trade P&L list."""
    if len(pnls) < 3:
        return 0.0
    avg = mean(pnls)
    try:
        sd = stdev(pnls)
    except (StatisticsError, ValueError):
        return 0.0
    if sd == 0:
        return 0.0
    # Annualize
    return (avg / sd) * math.sqrt(periods_per_year)


def compute_max_drawdown(pnls: List[float]) -> float:
    """Compute maximum drawdown as a positive percentage."""
    if not pnls:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        if cumulative > peak:
            peak = cumulative
        dd = (peak - cumulative) / max(peak, 1)
        if dd > max_dd:
            max_dd = dd
    return max_dd


def compute_metrics(trades: List[Dict]) -> Dict:
    """Compute performance metrics from a list of trade dicts."""
    if not trades:
        return {
            "total_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
            "sharpe": 0.0, "max_drawdown": 0.0, "trades": 0,
            "wins": 0, "losses": 0, "avg_win": 0.0, "avg_loss": 0.0,
        }

    pnls = [t.get("adjusted_pnl", t.get("pnl", 0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total = sum(pnls)
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    profit_factor = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else float("inf") if wins else 0.0
    sharpe = compute_sharpe(pnls)
    max_dd = compute_max_drawdown(pnls)
    avg_win = mean(wins) if wins else 0.0
    avg_loss = mean(losses) if losses else 0.0

    return {
        "total_pnl": round(total, 2),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else 999.0,
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
    }


# ==================== Hypothesis Simulation ====================

def _classify_edge(engine: str, strategy: str, edge_map: dict) -> str:
    """Map strategy to edge type using edge map."""
    engine_edges = edge_map.get(engine, {}).get("edges", {})
    for edge_name, edge_info in engine_edges.items():
        for prefix in edge_info.get("strategies", []):
            if strategy.startswith(prefix):
                return edge_name
    return edge_map.get(engine, {}).get("default_edge", "unknown")


def _get_edge_map() -> dict:
    """Return the edge classification map (reused from weekly_review_v2)."""
    return {
        "btc": {
            "edges": {
                "oi_delta":         {"strategies": ["oi_long", "oi_short"]},
                "funding_bias":     {"strategies": ["funding_extreme_long", "funding_extreme_short"]},
                "atr_breakout":     {"strategies": ["flow_bias_long", "flow_bias_short"]},
            },
            "default_edge": "oi_delta",
        },
        "eth": {
            "edges": {
                "ratio_btc":        {"strategies": ["eth_swing_relative_value"]},
                "oi_delta":         {"strategies": ["oi_puking"]},
            },
            "default_edge": "ratio_btc",
        },
        "gold": {
            "edges": {
                "vix_regime":       {"strategies": ["gold_dxy_short", "gold_dxy_long", "gold_dxy_vix_momentum_long", "gold_dxy_vix_momentum_short"]},
            },
            "default_edge": "vix_regime",
        },
        "indices": {
            "edges": {
                "vix_direction":    {"strategies": ["idx_risk_on", "idx_risk_off", "idx_risk_on_flat", "idx_risk_on_momentum"]},
            },
            "default_edge": "vix_direction",
        },
        "equities": {
            "edges": {
                "stock_alpha":      {"strategies": ["eq_"]},
            },
            "default_edge": "stock_alpha",
        },
    }


def apply_hypothesis_to_trades(
    trades: List[Dict],
    hypothesis: Dict,
    baseline_config: Dict,
) -> List[Dict]:
    """
    Apply a hypothesis to a list of trades by simulating the proposed change.
    Returns the (possibly filtered/modified) trade list representing the hypothesis outcome.

    Actions:
      - set_param: Adjust P&L based on parameter change (e.g., wider SL → less
        slippage stop-outs, but larger losses when it hits)
      - adjust_weight: Re-weight trade P&Ls by the new weight multiplier
      - add_filter: Only include trades matching the filter condition
      - disable_edge: Exclude trades using the specified edge
      - enable_edge: Cannot simulate — returns empty (no data for disabled edges)
    """
    test_params = hypothesis.get("test_params", {})
    action = test_params.get("action", "")
    target_engine = test_params.get("target_engine", "")
    target_param = test_params.get("target_param", "")
    new_value = test_params.get("new_value")

    if not target_engine or not trades:
        return trades

    # Filter to trades from the target engine only
    engine_trades = [t for t in trades if t.get("engine", "").lower() == target_engine.lower()]
    if not engine_trades:
        return trades  # No trades for this engine, pass through

    if action == "disable_edge":
        # Remove all trades classified under the target edge
        edge_map = _get_edge_map()
        disabled_edge = target_param  # The edge name to disable
        filtered = []
        for t in engine_trades:
            strategy = t.get("strategy", "")
            edge = _classify_edge(target_engine, strategy, edge_map)
            if edge != disabled_edge:
                filtered.append(t)
        return filtered

    elif action == "enable_edge":
        # Can't simulate enabled edges without historical data — return empty
        return []

    elif action == "add_filter":
        # Apply filter: e.g., "vix_regime == low" or "entry_hour_hkt < 16"
        filtered = []
        for t in engine_trades:
            if _evaluate_filter(t, target_param, new_value):
                filtered.append(t)
        return filtered

    elif action == "adjust_weight":
        # Scale each trade's P&L by the weight ratio
        try:
            new_weight = float(new_value)
        except (TypeError, ValueError):
            new_weight = 1.0
        # Read current weight from config (default 1.0)
        engine_cfg = baseline_config.get("engines", {}).get(target_engine, {})
        current_weight = float(engine_cfg.get("_weight_boost", 1.0))
        scale = new_weight / current_weight if current_weight > 0 else 1.0

        weighted_trades = copy.deepcopy(engine_trades)
        for t in weighted_trades:
            pnl = t.get("adjusted_pnl", t.get("pnl", 0))
            t["adjusted_pnl"] = round(pnl * scale, 2)
        return weighted_trades

    elif action == "set_param":
        # Simulate parameter change impact on trade P&L
        # The most sophisticated simulation: estimate how changing SL/TP affects outcomes
        return _simulate_param_change(engine_trades, target_param, new_value, baseline_config, target_engine)

    return engine_trades


def _evaluate_filter(trade: Dict, target_param: str, new_value: Any) -> bool:
    """Evaluate a filter condition on a trade. Returns True if trade passes filter."""
    if not target_param:
        return True

    # Parse filter expression like "vix_regime == low" or "entry_hour_hkt < 16"
    param = target_param
    operator = "=="
    value = new_value

    # Allow expressions like "vix_regime_low" as shorthand
    if isinstance(target_param, str) and " " in target_param:
        parts = target_param.split()
        if len(parts) >= 3:
            param = parts[0]
            operator = parts[1]
            # Try to parse value as number first
            try:
                value = float(parts[2])
            except ValueError:
                value = parts[2]
        elif len(parts) == 2:
            param = parts[0]
            operator = "=="
            try:
                value = float(parts[1])
            except ValueError:
                value = parts[1]

    # Get the actual value from the trade
    # Check various locations in the trade dict
    actual = None
    # Check entry_attribution
    entry_attr = trade.get("entry_attribution", {})
    if param in entry_attr:
        actual = entry_attr[param]
    # Check close_attribution
    if actual is None:
        close_attr = trade.get("close_attribution", {})
        if param in close_attr:
            actual = close_attr[param]
    # Check top-level
    if actual is None:
        actual = trade.get(param)

    if actual is None:
        return True  # Can't evaluate, pass through

    # Evaluate
    try:
        if isinstance(value, str):
            return str(actual).lower() == value.lower()
        # Numeric comparison
        actual_f = float(actual)
        val_f = float(value)
        if operator == "==" or operator == "=":
            return abs(actual_f - val_f) < 0.001
        elif operator == "<":
            return actual_f < val_f
        elif operator == "<=":
            return actual_f <= val_f
        elif operator == ">":
            return actual_f > val_f
        elif operator == ">=":
            return actual_f >= val_f
        elif operator == "!=":
            return abs(actual_f - val_f) >= 0.001
    except (ValueError, TypeError):
        return str(actual).lower() == str(value).lower()

    return True


def _simulate_param_change(
    trades: List[Dict],
    target_param: str,
    new_value: Any,
    baseline_config: Dict,
    engine_name: str,
) -> List[Dict]:
    """
    Simulate the impact of changing a parameter like STOP_LOSS_ATR_MULT or
    risk_per_trade_pct on historical trade outcomes.

    For stop_loss changes: adjust the stop distance, then estimate whether
    trades that were stopped out would have been stopped differently.
    For risk changes: scale position sizes.
    """
    try:
        new_val = float(new_value)
    except (TypeError, ValueError):
        return trades  # Can't simulate non-numeric changes

    engine_cfg = baseline_config.get("engines", {}).get(engine_name, {})
    param_map = {
        # Map display names to config keys
        "STOP_LOSS_ATR_MULT": "stop_loss_atr_mult",
        "stop_loss_atr_mult": "stop_loss_atr_mult",
        "RISK_PER_TRADE_PCT": "risk_per_trade_pct",
        "risk_per_trade_pct": "risk_per_trade_pct",
        "TAKE_PROFIT_PCT": "take_profit_pct",
        "take_profit_pct": "take_profit_pct",
        "STOP_LOSS_PCT": "stop_loss_pct",
        "stop_loss_pct": "stop_loss_pct",
        "TRAILING_ACTIVATION_PCT": "trailing_activation_pct",
        "trailing_activation_pct": "trailing_activation_pct",
        "TRAILING_DISTANCE_PCT": "trailing_distance_pct",
        "trailing_distance_pct": "trailing_distance_pct",
        "oi_delta_long_threshold": "oi_delta_long_threshold",
        "oi_delta_short_threshold": "oi_delta_short_threshold",
        "atr_period": "atr_period",
    }

    config_key = param_map.get(target_param, target_param)
    old_val = engine_cfg.get(config_key, None)
    if old_val is None:
        return trades  # Unknown param, no simulation

    try:
        old_val = float(old_val)
    except (TypeError, ValueError):
        return trades

    ratio = new_val / old_val if old_val > 0 else 1.0

    simulated = copy.deepcopy(trades)
    for t in simulated:
        pnl = t.get("adjusted_pnl", t.get("pnl", 0))
        close_reason = t.get("close_reason", "")

        if config_key in ("stop_loss_atr_mult", "stop_loss_pct"):
            # SL change: wider SL → fewer stop-outs, but larger losses when hit
            # Narrower SL → more stop-outs, smaller losses
            if close_reason == "tp_sl_hit" and pnl <= 0:
                # Was stopped out. Wider SL means loss would be larger.
                # Narrower SL means loss would be smaller.
                t["adjusted_pnl"] = round(pnl * ratio, 2)
            elif pnl > 0:
                # Winner — SL change doesn't affect winners that hit TP
                pass
        elif config_key in ("take_profit_pct",):
            # TP change: wider TP → bigger wins but more reversal risk
            if close_reason == "tp_sl_hit" and pnl > 0:
                t["adjusted_pnl"] = round(pnl * ratio, 2)
        elif config_key == "risk_per_trade_pct":
            # Risk change scales all P&Ls proportionally
            t["adjusted_pnl"] = round(pnl * ratio, 2)
        elif config_key in ("trailing_activation_pct", "trailing_distance_pct"):
            # Trailing changes: tighter trailing = earlier exits, smaller wins/losses
            t["adjusted_pnl"] = round(pnl * (1 / ratio if ratio > 0 else 1.0), 2)

    return simulated


# ==================== Gate Decisions ====================

def score_vs_baseline(
    hypothesis_metrics: Dict,
    baseline_metrics: Dict,
) -> Tuple[str, float]:
    """
    Compare hypothesis metrics to baseline.
    Returns (gate_decision, composite_score).
    """
    # Composite score: weighted combination of improvements
    # Each metric delta normalized to 0-1 range
    scores = []

    # Sharpe improvement (most important)
    base_sharpe = baseline_metrics.get("sharpe", 0)
    hypo_sharpe = hypothesis_metrics.get("sharpe", 0)
    sharpe_delta = hypo_sharpe - base_sharpe
    # Normalize: +1.0 sharpe improvement = perfect, 0 = no change, negative = worse
    scores.append(max(-1.0, min(1.0, sharpe_delta / 1.0)) * 0.30)

    # Win rate improvement
    base_wr = baseline_metrics.get("win_rate", 0)
    hypo_wr = hypothesis_metrics.get("win_rate", 0)
    wr_delta = hypo_wr - base_wr
    scores.append(max(-1.0, min(1.0, wr_delta / 0.10)) * 0.15)

    # Profit factor improvement
    base_pf = baseline_metrics.get("profit_factor", 0)
    hypo_pf = hypothesis_metrics.get("profit_factor", 0)
    pf_delta = hypo_pf - base_pf
    scores.append(max(-1.0, min(1.0, pf_delta / 1.0)) * 0.15)

    # Total P&L improvement
    base_pnl = baseline_metrics.get("total_pnl", 0)
    hypo_pnl = hypothesis_metrics.get("total_pnl", 0)
    if base_pnl != 0:
        pnl_delta_pct = (hypo_pnl - base_pnl) / abs(base_pnl)
    else:
        pnl_delta_pct = 0.0 if hypo_pnl == 0 else 1.0
    scores.append(max(-1.0, min(1.0, pnl_delta_pct)) * 0.25)

    # Max drawdown improvement (lower is better)
    base_dd = baseline_metrics.get("max_drawdown", 0)
    hypo_dd = hypothesis_metrics.get("max_drawdown", 0)
    dd_improvement = base_dd - hypo_dd  # Positive = improvement
    scores.append(max(-1.0, min(1.0, dd_improvement / 0.10)) * 0.15)

    composite = sum(scores)

    # Gate decision
    if composite >= 0.15:
        decision = "PASS"
    elif composite >= -0.10:
        decision = "MARGINAL"
    else:
        decision = "FAIL"

    return decision, round(composite, 4)


def compute_baseline_metrics(all_trades: Dict[str, List[Dict]],
                              now_ts: float) -> Dict[str, Dict]:
    """Compute baseline metrics per engine using test period trades."""
    baseline = {}
    for engine, trades in all_trades.items():
        _, test_trades = split_walk_forward(trades, now_ts)
        if not test_trades:
            baseline[engine] = compute_metrics([])
        else:
            baseline[engine] = compute_metrics(test_trades)
    return baseline


# ==================== Paper Trading Pool Management ====================

def load_paper_pool() -> Dict:
    """Load current paper trading pool state."""
    pool = _load_json(PAPER_POOL_FILE, {
        "active": [],
        "watchlist": [],
        "archived": [],
        "last_updated": "",
    })
    return pool


def save_paper_pool(pool: Dict):
    """Save paper trading pool with timestamp."""
    pool["last_updated"] = datetime.now(HKT).isoformat()
    _save_json(PAPER_POOL_FILE, pool)


def add_to_paper_pool(hypothesis: Dict, backtest_score: Dict) -> bool:
    """
    Add a hypothesis to the paper trading pool.
    Max 3 active strategies at a time.
    Returns True if added, False if pool is full.
    """
    pool = load_paper_pool()
    active = pool.get("active", [])

    if len(active) >= MAX_ACTIVE_PAPER:
        # Try to evict the worst performer
        active.sort(key=lambda s: s.get("backtest_score", {}).get("composite", 0))
        if active and active[0].get("backtest_score", {}).get("composite", 0) < backtest_score.get("composite", 0):
            evicted = active.pop(0)
            evicted["archive_reason"] = "evicted_by_superior_hypothesis"
            pool.setdefault("archived", []).append(evicted)
            print(f"  🗑️ Evicted worst paper strategy: {evicted.get('hypothesis', '?')[:60]}")
        else:
            # Put on watchlist instead
            watch_entry = {
                "hypothesis": hypothesis,
                "backtest_score": backtest_score,
                "added_at": datetime.now(HKT).isoformat(),
                "reason": "paper_pool_full",
            }
            pool.setdefault("watchlist", []).append(watch_entry)
            save_paper_pool(pool)
            print(f"  ⏳ Paper pool full ({MAX_ACTIVE_PAPER}/{MAX_ACTIVE_PAPER}) — "
                  f"added to watchlist instead")
            return False

    entry = {
        "id": f"paper_{len(active) + len(pool.get('archived', [])) + 1}_{int(time.time())}",
        "hypothesis": hypothesis,
        "backtest_score": backtest_score,
        "added_at": datetime.now(HKT).isoformat(),
        "status": "active",
        "pnl_7d": 0.0,
        "promotion_check_at": "",
    }
    pool.setdefault("active", []).append(entry)
    save_paper_pool(pool)
    print(f"  ✅ Added to paper pool (now {len(pool['active'])} active)")
    return True


def check_paper_promotions() -> Dict:
    """
    Check all active paper strategies.
    If any have been active for >= 7 days AND P&L positive → promote to live.
    If P&L negative after 7 days → archive with reason.
    Returns dict of promotion results.
    """
    pool = load_paper_pool()
    active = pool.get("active", [])
    now = datetime.now(HKT)
    now_ts = now.timestamp()

    promotions = []
    archivals = []

    # Load paper trades to compute 7-day P&L
    paper_trades = []
    try:
        with open(os.path.join(ENG_DIR, "paper_trades.json")) as f:
            paper_trades = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        paper_trades = []

    cutoff_ts = now_ts - (PAPER_SHADOW_DAYS * 86400)

    remaining_active = []
    for strategy in active:
        added_at = strategy.get("added_at", "")
        try:
            added_dt = datetime.fromisoformat(added_at)
            added_ts = added_dt.timestamp()
        except (ValueError, TypeError):
            remaining_active.append(strategy)
            continue

        age_days = (now_ts - added_ts) / 86400

        if age_days < PAPER_SHADOW_DAYS:
            # Not yet ready for promotion check
            remaining_active.append(strategy)
            continue

        # Compute 7-day P&L from paper_trades for strategies added since this entry
        # Filter paper_trades to ones matching this strategy's target_engine
        target_engine = strategy.get("hypothesis", {}).get("test_params", {}).get("target_engine", "")
        relevant_trades = [
            t for t in paper_trades
            if t.get("timestamp_ts", 0) >= cutoff_ts
            and t.get("status") == "closed"
            and (not target_engine or t.get("market", "").lower() == target_engine.lower())
        ]
        total_pnl = sum(t.get("adjusted_pnl", t.get("pnl", 0)) for t in relevant_trades)
        strategy["pnl_7d"] = round(total_pnl, 2)

        if total_pnl > 0:
            # PROMOTE to live config
            promotions.append({
                "strategy_id": strategy.get("id", "?"),
                "hypothesis": strategy.get("hypothesis", {}),
                "pnl_7d": total_pnl,
                "promoted_at": now.isoformat(),
            })
            print(f"  🚀 PROMOTING to live: {strategy.get('id', '?')} "
                  f"(7d P&L=${total_pnl:+.2f})")
        else:
            # ARCHIVE
            strategy["archive_reason"] = f"negative_7d_pnl (${total_pnl:+.2f})"
            strategy["archived_at"] = now.isoformat()
            archivals.append(strategy)
            print(f"  🗂️ Archiving: {strategy.get('id', '?')} "
                  f"(7d P&L=${total_pnl:+.2f})")

    pool["active"] = remaining_active
    for a in archivals:
        pool.setdefault("archived", []).append(a)

    save_paper_pool(pool)

    return {
        "promotions": promotions,
        "archivals": archivals,
        "remaining_active": len(remaining_active),
    }


def apply_promotions_to_live(promotions: List[Dict]) -> Dict:
    """
    Apply promoted hypotheses to live_config.json (strategy_config.json).
    Returns update report.
    """
    if not promotions:
        return {"applied": 0, "changes": []}

    config = _load_json(STRATEGY_CONFIG_FILE, {"engines": {}})
    changes = []

    for promo in promotions:
        hypothesis = promo.get("hypothesis", {})
        test_params = hypothesis.get("test_params", {})
        action = test_params.get("action", "")
        target_engine = test_params.get("target_engine", "")
        target_param = test_params.get("target_param", "")
        new_value = test_params.get("new_value")

        if not target_engine or target_engine not in config.get("engines", {}):
            continue

        engine_cfg = config["engines"][target_engine]
        change_record = {
            "engine": target_engine,
            "action": action,
            "param": target_param,
            "old_value": None,
            "new_value": new_value,
            "hypothesis": hypothesis.get("hypothesis", ""),
        }

        if action == "set_param":
            param_map = {
                "STOP_LOSS_ATR_MULT": "stop_loss_atr_mult",
                "RISK_PER_TRADE_PCT": "risk_per_trade_pct",
                "TAKE_PROFIT_PCT": "take_profit_pct",
                "STOP_LOSS_PCT": "stop_loss_pct",
                "TRAILING_ACTIVATION_PCT": "trailing_activation_pct",
                "TRAILING_DISTANCE_PCT": "trailing_distance_pct",
                "oi_delta_long_threshold": "oi_delta_long_threshold",
                "oi_delta_short_threshold": "oi_delta_short_threshold",
                "atr_period": "atr_period",
                "vix_high_threshold": "vix_high_threshold",
                "vix_low_threshold": "vix_low_threshold",
                "max_exposure_pct": "max_exposure_pct",
                "max_positions": "max_positions",
                "time_stop_minutes": "time_stop_minutes",
                "risk_per_trade_pct": "risk_per_trade_pct",  # lowercase variations
            }
            config_key = param_map.get(target_param, target_param)
            if config_key in engine_cfg or config_key in [
                "stop_loss_atr_mult", "risk_per_trade_pct", "stop_loss_pct",
                "take_profit_pct", "trailing_activation_pct", "trailing_distance_pct",
                "oi_delta_long_threshold", "oi_delta_short_threshold", "atr_period",
                "vix_high_threshold", "vix_low_threshold", "max_exposure_pct",
                "max_positions", "time_stop_minutes",
            ]:
                old_val = engine_cfg.get(config_key)
                change_record["old_value"] = old_val
                try:
                    new_val = float(new_value) if new_value is not None else old_val
                    if isinstance(old_val, int) and not isinstance(old_val, bool):
                        new_val = int(new_val)
                    engine_cfg[config_key] = new_val
                except (ValueError, TypeError):
                    pass
                # Ensure new_val is defined (use old_val as fallback)
                if 'new_val' not in locals():
                    new_val = old_val
                changes.append(change_record)
                print(f"  🔧 LIVE UPDATE: {target_engine}.{config_key}: {old_val} → {new_val}")

        elif action == "adjust_weight":
            old_weight = engine_cfg.get("_weight_boost", 1.0)
            change_record["old_value"] = old_weight
            try:
                engine_cfg["_weight_boost"] = float(new_value)
            except (ValueError, TypeError):
                pass
            changes.append(change_record)
            print(f"  ⚖️ WEIGHT UPDATE: {target_engine}._weight_boost: {old_weight} → {new_value}")

        elif action == "add_filter":
            if "_filters" not in engine_cfg:
                engine_cfg["_filters"] = []
            filter_entry = {
                "param": target_param,
                "value": new_value,
                "hypothesis": hypothesis.get("hypothesis", ""),
                "promoted_at": datetime.now(HKT).isoformat(),
            }
            engine_cfg["_filters"].append(filter_entry)
            change_record["old_value"] = None
            changes.append(change_record)
            print(f"  🔍 FILTER ADDED: {target_engine}: {target_param}={new_value}")

        elif action == "disable_edge":
            if "_disabled_edges" not in engine_cfg:
                engine_cfg["_disabled_edges"] = []
            if target_param not in engine_cfg["_disabled_edges"]:
                engine_cfg["_disabled_edges"].append(target_param)
            change_record["old_value"] = None
            changes.append(change_record)
            print(f"  🚫 EDGE DISABLED: {target_engine}: {target_param}")

        elif action == "enable_edge":
            if "_disabled_edges" in engine_cfg and target_param in engine_cfg["_disabled_edges"]:
                engine_cfg["_disabled_edges"].remove(target_param)
            change_record["old_value"] = None
            changes.append(change_record)
            print(f"  ✅ EDGE ENABLED: {target_engine}: {target_param}")

    # Update meta
    if changes:
        if "_meta" not in config:
            config["_meta"] = {}
        config["_meta"]["last_hypothesis_update"] = datetime.now(HKT).isoformat()
        config["_meta"]["total_promotions"] = config["_meta"].get("total_promotions", 0) + len(promotions)
        _save_json(STRATEGY_CONFIG_FILE, config)
        print(f"  ✅ Live config updated with {len(changes)} changes")

    return {"applied": len(promotions), "changes": changes}


# ==================== Main Pipeline ====================

def run_backtest_pipeline(hypotheses: List[Dict], baseline_config: Dict,
                           all_trades: Dict[str, List[Dict]],
                           now_ts: float) -> Dict:
    """Run the full backtest pipeline for all hypotheses."""
    results = []

    # Compute baseline metrics per engine
    baseline_metrics = compute_baseline_metrics(all_trades, now_ts)

    for i, h in enumerate(hypotheses):
        print(f"\n  ─── Hypothesis {i+1}/{len(hypotheses)}: "
              f"{h.get('hypothesis', '')[:80]} ───")

        test_params = h.get("test_params", {})
        target_engine = test_params.get("target_engine", "")
        action = test_params.get("action", "")
        hypothesis_text = h.get("hypothesis", "")
        confidence = h.get("confidence", 0)
        edge = h.get("edge", "?")
        regime = h.get("regime", "?")

        if not test_params or not target_engine:
            print(f"  ⏭️ No test_params or target_engine — skipping")
            results.append({
                "hypothesis_index": i,
                "hypothesis": hypothesis_text[:100],
                "edge": edge,
                "regime": regime,
                "confidence": confidence,
                "action": action,
                "target_engine": target_engine,
                "gate": "SKIP",
                "reason": "missing_test_params_or_engine",
            })
            continue

        # Walk-forward split for this engine's trades
        engine_trades = all_trades.get(target_engine, [])
        if not engine_trades:
            print(f"  ⚠️ No trades found for engine '{target_engine}'")
            results.append({
                "hypothesis_index": i,
                "hypothesis": hypothesis_text[:100],
                "edge": edge,
                "regime": regime,
                "confidence": confidence,
                "action": action,
                "target_engine": target_engine,
                "gate": "FAIL",
                "reason": f"no_trades_for_engine_{target_engine}",
            })
            continue

        train_trades, test_trades = split_walk_forward(engine_trades, now_ts)
        print(f"     Engine: {target_engine} | Train: {len(train_trades)} trades "
              f"| Test: {len(test_trades)} trades")

        if len(test_trades) < 3:
            print(f"  ⚠️ Too few test trades ({len(test_trades)}) — MARGINAL")
            results.append({
                "hypothesis_index": i,
                "hypothesis": hypothesis_text[:100],
                "edge": edge,
                "regime": regime,
                "confidence": confidence,
                "action": action,
                "target_engine": target_engine,
                "gate": "MARGINAL",
                "reason": f"insufficient_test_data_{len(test_trades)}_trades",
                "test_metrics": compute_metrics(test_trades),
                "baseline_metrics": baseline_metrics.get(target_engine, {}),
            })
            continue

        # Apply hypothesis to test trades
        hypothesis_trades = apply_hypothesis_to_trades(
            test_trades, h, baseline_config
        )

        if action == "enable_edge" and not hypothesis_trades:
            print(f"  ⚠️ Cannot simulate 'enable_edge' — no historical data")
            results.append({
                "hypothesis_index": i,
                "hypothesis": hypothesis_text[:100],
                "edge": edge,
                "regime": regime,
                "confidence": confidence,
                "action": action,
                "target_engine": target_engine,
                "gate": "MARGINAL",
                "reason": "enable_edge_cannot_simulate",
                "test_metrics": compute_metrics([]),
                "baseline_metrics": baseline_metrics.get(target_engine, {}),
            })
            continue

        if not hypothesis_trades:
            print(f"  ⚠️ No trades passed hypothesis filter — FAIL")
            results.append({
                "hypothesis_index": i,
                "hypothesis": hypothesis_text[:100],
                "edge": edge,
                "regime": regime,
                "confidence": confidence,
                "action": action,
                "target_engine": target_engine,
                "gate": "FAIL",
                "reason": "no_trades_passed_filter",
                "test_metrics": compute_metrics([]),
                "baseline_metrics": baseline_metrics.get(target_engine, {}),
            })
            continue

        # Compute metrics
        hypo_metrics = compute_metrics(hypothesis_trades)
        base_metrics = baseline_metrics.get(target_engine, compute_metrics([]))

        # Gate decision
        gate, composite_score = score_vs_baseline(hypo_metrics, base_metrics)
        print(f"     Gate: {gate} (score={composite_score})")
        print(f"       Hypothesis: Sharpe={hypo_metrics['sharpe']} WR={hypo_metrics['win_rate']:.1%} "
              f"PF={hypo_metrics['profit_factor']} P&L=${hypo_metrics['total_pnl']}")
        print(f"       Baseline:   Sharpe={base_metrics['sharpe']} WR={base_metrics['win_rate']:.1%} "
              f"PF={base_metrics['profit_factor']} P&L=${base_metrics['total_pnl']}")

        result_entry = {
            "hypothesis_index": i,
            "hypothesis": hypothesis_text[:200],
            "edge": edge,
            "regime": regime,
            "confidence": confidence,
            "action": action,
            "target_engine": target_engine,
            "target_param": test_params.get("target_param", ""),
            "new_value": test_params.get("new_value"),
            "reasoning": test_params.get("reasoning", ""),
            "gate": gate,
            "composite_score": composite_score,
            "test_metrics": hypo_metrics,
            "baseline_metrics": base_metrics,
        }
        results.append(result_entry)

        # AGGRESSIVE: if PASS, immediately add to paper pool
        if gate == "PASS":
            backtest_score = {
                "composite": composite_score,
                "sharpe": hypo_metrics["sharpe"],
                "win_rate": hypo_metrics["win_rate"],
                "profit_factor": hypo_metrics["profit_factor"],
                "total_pnl": hypo_metrics["total_pnl"],
                "max_drawdown": hypo_metrics["max_drawdown"],
            }
            added = add_to_paper_pool(h, backtest_score)
            print(f"     {'✅ Paper pool entry' if added else '⏳ Watchlist entry'}")

    return results


def run_promotion_cycle() -> Dict:
    """Check paper pool for promotions to live config."""
    print("\n  ─── Paper Pool Promotion Check ───")
    result = check_paper_promotions()
    promotions = result.get("promotions", [])
    archivals = result.get("archivals", [])

    print(f"     Promotions to live: {len(promotions)}")
    print(f"     Archived: {len(archivals)}")
    print(f"     Remaining active: {result.get('remaining_active', 0)}")

    if promotions:
        update_result = apply_promotions_to_live(promotions)
        result["live_updates"] = update_result
        print(f"     Live config updated: {update_result['applied']} changes")

    return result


def save_results(hypothesis_results: List[Dict], promotion_result: Dict):
    """Save all results to output files."""
    now = datetime.now(HKT)

    # ── hypothesis_results.json ──
    results_payload = {
        "generated_at": now.isoformat(),
        "total_hypotheses": len(hypothesis_results),
        "pass_count": len([r for r in hypothesis_results if r.get("gate") == "PASS"]),
        "marginal_count": len([r for r in hypothesis_results if r.get("gate") == "MARGINAL"]),
        "fail_count": len([r for r in hypothesis_results if r.get("gate") == "FAIL"]),
        "skip_count": len([r for r in hypothesis_results if r.get("gate") == "SKIP"]),
        "results": hypothesis_results,
    }
    _save_json(HYPOTHESIS_RESULTS_FILE, results_payload)
    print(f"\n  💾 Results saved: {HYPOTHESIS_RESULTS_FILE}")

    # ── live_config_updates.json ──
    updates_payload = {
        "generated_at": now.isoformat(),
        "promotions": promotion_result.get("promotions", []),
        "archivals": promotion_result.get("archivals", []),
        "live_updates": promotion_result.get("live_updates", {"applied": 0, "changes": []}),
    }
    _save_json(LIVE_UPDATES_FILE, updates_payload)
    print(f"  💾 Live updates saved: {LIVE_UPDATES_FILE}")

    # TG notify
    tg_summary = _build_tg_summary(results_payload, promotion_result)
    _send_tg_notify(tg_summary)


def _build_tg_summary(results_payload: Dict, promotion_result: Dict) -> str:
    """Build a Telegram summary message."""
    lines = []
    lines.append("📊 *Backtest Results — Sunday WFA*")
    lines.append(f"Run: {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    lines.append("")

    r = results_payload
    lines.append(f"📈 *Hypotheses*: {r['total_hypotheses']} total")
    lines.append(f"  ✅ PASS: {r['pass_count']}")
    lines.append(f"  ⏸️ MARGINAL: {r['marginal_count']}")
    lines.append(f"  ❌ FAIL: {r['fail_count']}")
    lines.append(f"  ⏭️ SKIP: {r['skip_count']}")

    if r['pass_count'] > 0:
        lines.append("")
        lines.append("*New Paper Pool Entries:*")
        for res in r['results']:
            if res.get('gate') == 'PASS':
                lines.append(f"  - {res.get('edge', '?')}/{res.get('target_engine', '?')}: "
                             f"score={res.get('composite_score', 0)} "
                             f"S={res['test_metrics']['sharpe']} "
                             f"P&L=${res['test_metrics']['total_pnl']}")

    promotions = promotion_result.get("promotions", [])
    if promotions:
        lines.append("")
        lines.append(f"🚀 *Promoted to Live ({len(promotions)}):*")
        for p in promotions:
            hypo = p.get("hypothesis", {})
            lines.append(f"  - {hypo.get('hypothesis', '?')[:100]}")
            lines.append(f"    → 7d P&L=${p.get('pnl_7d', 0):+.2f}")

    archivals = promotion_result.get("archivals", [])
    if archivals:
        lines.append("")
        lines.append(f"🗂️ *Archived ({len(archivals)}):*")
        for a in archivals:
            lines.append(f"  - {a.get('id', '?')} ({a.get('archive_reason', '?')})")

    return "\n".join(lines)


def _send_tg_notify(text: str):
    """Send Telegram notification via tg_notify module."""
    try:
        from tg_notify import tg_send
        tg_send(text, silent=False)  # Important: not silent for Sunday review
    except Exception as e:
        print(f"  ⚠️ TG notify failed: {e}")


def main():
    print("=" * 60)
    print(f"📊 HYPOTHESIS BACKTESTER — {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 60)
    print("Mode: AGGRESSIVE — auto-promote to paper pool on PASS")

    now_ts = time.time()

    # ── Step 1: Load hypotheses ──
    print("\n📡 Step 1: Loading hypotheses...")
    hypotheses_data = _load_json(HYPOTHESES_FILE, {"hypotheses": []})
    hypotheses = hypotheses_data.get("hypotheses", [])
    if not hypotheses:
        print("  ⚠️ No hypotheses found in weekly_hypotheses.json")
        print("  ℹ️ Skipping backtest — running promotion cycle check only")
        promotion_result = run_promotion_cycle()
        save_results([], promotion_result)
        print("\n✅ Hypothesis Backtester complete (no hypotheses to test)")
        return

    print(f"  Loaded {len(hypotheses)} hypotheses from {HYPOTHESES_FILE}")
    for h in hypotheses:
        tp = h.get("test_params", {})
        print(f"    [{h.get('confidence', 0):.0%}] {h.get('edge', '?')}/{h.get('regime', 'all')}: "
              f"{tp.get('action', '?')} on {tp.get('target_engine', '?')} "
              f"→ {h.get('hypothesis', '')[:80]}")

    # ── Step 2: Load baseline config ──
    print("\n📡 Step 2: Loading baseline config...")
    baseline_config = _load_json(STRATEGY_CONFIG_FILE, {"engines": {}})
    print(f"  Loaded strategy_config.json ({len(baseline_config.get('engines', {}))} engines)")

    # ── Step 3: Load trade data ──
    print("\n📡 Step 3: Loading trade data...")
    all_trades = load_all_trades()
    total_trades = sum(len(t) for t in all_trades.values())
    print(f"  Loaded {len(all_trades)} engines, {total_trades} closed trades")
    for engine, trades in sorted(all_trades.items()):
        print(f"    {engine}: {len(trades)} trades")

    # ── Step 4: Run backtest ──
    print("\n📡 Step 4: Running backtest pipeline...")
    hypothesis_results = run_backtest_pipeline(
        hypotheses, baseline_config, all_trades, now_ts
    )

    # ── Step 5: Run promotion cycle ──
    print("\n📡 Step 5: Checking paper pool promotions...")
    promotion_result = run_promotion_cycle()

    # ── Step 6: Save results ──
    print("\n📡 Step 6: Saving results...")
    save_results(hypothesis_results, promotion_result)

    # ── Summary ──
    pass_count = len([r for r in hypothesis_results if r.get("gate") == "PASS"])
    marginal_count = len([r for r in hypothesis_results if r.get("gate") == "MARGINAL"])
    fail_count = len([r for r in hypothesis_results if r.get("gate") == "FAIL"])
    promotions = len(promotion_result.get("promotions", []))

    print(f"\n{'=' * 60}")
    print(f"📊 SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Hypotheses tested: {len(hypothesis_results)}")
    print(f"  ✅ PASS (→ paper pool): {pass_count}")
    print(f"  ⏸️ MARGINAL (→ watchlist): {marginal_count}")
    print(f"  ❌ FAIL (→ archive): {fail_count}")
    print(f"  🚀 Promoted to live: {promotions}")
    print(f"  📄 Output files:")
    print(f"    - {HYPOTHESIS_RESULTS_FILE}")
    print(f"    - {PAPER_POOL_FILE}")
    print(f"    - {LIVE_UPDATES_FILE}")
    print(f"    - {STRATEGY_CONFIG_FILE} (updated)")
    print(f"\n✅ Hypothesis Backtester complete — AGGRESSIVE mode")


if __name__ == "__main__":
    main()
