#!/usr/bin/env python3
"""
📊 每週邊緣 (Edge) 績效檢討 v2 — 週日 20:00 HKT
============================================
v2 升級內容:
  1. ✅ 7-day edge P&L breakdown per-engine
  2. ✅ Regime analysis: which VIX regime produced P&L
  3. ✅ Edge P&L matrix: cross-tab of edge × regime × weekday
  4. ✅ Gemini deep analysis → structured JSON hypotheses
  5. ✅ Auto-save hypotheses for Sunday backtest
  6. ✅ Update strategy_config.json with new edge weights
  7. ✅ Auto-TG notify if actionable
  8. ✅ Fixed \\n escape char bugs from original weekly_review.py

Phase 1: Edge P&L Breakdown (Pure Python)
  - Per-engine: which EDGE won/loss
    - BTC: OI delta vs funding bias vs ATR breakout
    - ETH: ETH/BTC ratio vs OI delta
    - Gold: VIX regime
    - Indices: VIX direction
    - Equities: stock selection alpha
  - Edge won if adjusted_pnl > 0, lost if <= 0

Phase 2: Regime Analysis
  - VIX regime "low" / "high" / "fear" / "complacency" / "unknown"
  - Per-regime: total P&L, win rate, trade count
  - Identify which regimes produce positive edge

Phase 3: Edge × Regime × Weekday Matrix
  - Cross-tabulation heatmap data
  - Find combinations with strongest P&L

Phase 4: Gemini Hypothesis Generation
  - Send structured edge/regime data to Gemini
  - Return structured JSON: {hypotheses: [{edge, regime, hypothesis, confidence, test_params}]}

Phase 5: Save + Apply
  - Save hypotheses to weekly_hypotheses.json
  - Update strategy_config.json edge weights
  - TG notify only if actionable

Cron: 0 12 * * 0 (週日 12:00 UTC = 20:00 HKT)
"""

import json
import os
import sys
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
REVIEW_DIR = os.path.join(WORKSPACE, "reviews")
HYPOTHESES_FILE = os.path.join(REVIEW_DIR, "weekly_hypotheses.json")
STRATEGY_CONFIG_FILE = os.path.join(ENG_DIR, "strategy_config.json")
os.makedirs(REVIEW_DIR, exist_ok=True)

# All Gemini calls go through gemini_free module (direct API, no proxy)

# ── Gemini free tier (3.1 Pro) → fallback Vertex Proxy ──
USE_FREE_TIER = False
try:
    from gemini_free import call_pro
    USE_FREE_TIER = True
except ImportError:
    pass

# ── Edge definitions per engine ──
# strategy name prefixes map to edge types
ENGINE_EDGE_MAP = {
    "btc": {
        "edges": {
            "oi_delta":         {"desc": "OI Delta momentum",      "strategies": ["oi_long", "oi_short"]},
            "funding_bias":     {"desc": "Funding rate extreme",   "strategies": ["funding_extreme_long", "funding_extreme_short"]},
            "atr_breakout":     {"desc": "ATR-based flow bias",    "strategies": ["flow_bias_long", "flow_bias_short"]},
        },
        "default_edge": "oi_delta",
    },
    "eth": {
        "edges": {
            "ratio_btc":        {"desc": "ETH/BTC ratio trend",     "strategies": ["eth_swing_relative_value"]},
            "oi_delta":         {"desc": "OI Delta (puking)",       "strategies": ["oi_puking"]},
        },
        "default_edge": "ratio_btc",
    },
    "gold": {
        "edges": {
            "vix_regime":       {"desc": "VIX regime + DXY inverse","strategies": ["gold_dxy_short", "gold_dxy_long", "gold_dxy_vix_momentum_long", "gold_dxy_vix_momentum_short"]},
        },
        "default_edge": "vix_regime",
    },
    "indices": {
        "edges": {
            "vix_direction":    {"desc": "VIX direction + ratio",   "strategies": ["idx_risk_on", "idx_risk_off", "idx_risk_on_flat", "idx_risk_on_momentum"]},
        },
        "default_edge": "vix_direction",
    },
    "equities": {
        "edges": {
            "stock_alpha":      {"desc": "Stock selection alpha",   "strategies": ["eq_"]},
        },
        "default_edge": "stock_alpha",
    },
}

# ── VIX regime classification ──
VIX_REGIMES = ["low", "mid", "high", "fear", "complacency", "unknown"]


# ═══════════════════════════════════════════════════════════════
# Phase 1: Load & classify trades
# ═══════════════════════════════════════════════════════════════

def classify_edge(engine: str, strategy: str) -> str:
    """Map strategy name to edge type per engine."""
    edge_cfg = ENGINE_EDGE_MAP.get(engine, {})
    edges = edge_cfg.get("edges", {})
    for edge_name, edge_info in edges.items():
        strat_prefixes = edge_info["strategies"]
        for prefix in strat_prefixes:
            if strategy.startswith(prefix):
                return edge_name
    return edge_cfg.get("default_edge", "unknown")


def _edge_default() -> Dict:
    """Factory for edge stats dict — avoids pyright defaultdict typing issues."""
    return {"trades": 0, "wins": 0, "pnl": 0.0, "pnls": []}


def collect_weekly_edges() -> Dict:
    """Load 7 days of trades, return per-engine edge breakdown."""
    now = time.time()
    cutoff = now - (7 * 86400)
    result = {}

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

        # Filter last 7 days
        recent = [t for t in trades if t.get("timestamp_ts", 0) >= cutoff]
        closed = [t for t in recent if t.get("status") == "closed"]
        if not closed:
            continue

        # Per-edge aggregation — using explicit dict to avoid pyright errors
        edge_stats = {}
        all_trades_detail = []

        for t in closed:
            strategy = t.get("strategy", "unknown")
            edge = classify_edge(engine, strategy)
            pnl = t.get("adjusted_pnl", t.get("pnl", 0))
            vix_regime = t.get("entry_attribution", {}).get("vix_regime", "unknown")
            entry_day = t.get("entry_attribution", {}).get("entry_day", "Unknown")

            if edge not in edge_stats:
                edge_stats[edge] = _edge_default()
            edge_stats[edge]["trades"] += 1
            edge_stats[edge]["pnl"] += pnl
            edge_stats[edge]["pnls"].append(pnl)
            if pnl > 0:
                edge_stats[edge]["wins"] += 1

            all_trades_detail.append({
                "edge": edge,
                "strategy": strategy,
                "direction": t.get("direction"),
                "pnl": pnl,
                "vix_regime": vix_regime,
                "entry_day": entry_day,
                "timestamp": t.get("timestamp"),
            })

        # Compute derived stats
        edges_out = {}
        for edge, stats in edge_stats.items():
            wins = stats["wins"]
            total = stats["trades"]
            pnls = stats["pnls"]
            wins_list = [p for p in pnls if p > 0]
            losses_list = [p for p in pnls if p <= 0]
            edges_out[edge] = {
                "trades": total,
                "wins": wins,
                "losses": total - wins,
                "win_rate": round(wins / total * 100, 1) if total else 0,
                "pnl": round(stats["pnl"], 2),
                "avg_win": round(sum(wins_list) / len(wins_list), 2) if wins_list else 0,
                "avg_loss": round(sum(losses_list) / len(losses_list), 2) if losses_list else 0,
                "profit_factor": round(abs(sum(wins_list) / sum(losses_list)), 2) if sum(losses_list) != 0 else float("inf"),
            }

        total_pnl = sum(s["pnl"] for s in edges_out.values())
        total_trades = sum(s["trades"] for s in edges_out.values())
        total_wins = sum(s["wins"] for s in edges_out.values())

        result[engine] = {
            "edges": edges_out,
            "total_trades": total_trades,
            "total_wins": total_wins,
            "total_losses": total_trades - total_wins,
            "total_pnl": round(total_pnl, 2),
            "overall_win_rate": round(total_wins / total_trades * 100, 1) if total_trades else 0,
            "recent_trades": all_trades_detail[-20:],
        }

    return result


# ═══════════════════════════════════════════════════════════════
# Phase 2: Regime Analysis
# ═══════════════════════════════════════════════════════════════

def _regime_default() -> Dict:
    """Factory for regime stats dict."""
    return {"trades": 0, "wins": 0, "pnl": 0.0, "pnls": []}


def collect_regime_analysis(weekly: Dict) -> Dict:
    """Per-regime P&L analysis across all engines."""
    regime_stats = {}

    for engine, edata in weekly.items():
        for trade in edata.get("recent_trades", []):
            regime = trade.get("vix_regime", "unknown")
            pnl = trade.get("pnl", 0)
            if regime not in regime_stats:
                regime_stats[regime] = _regime_default()
            regime_stats[regime]["trades"] += 1
            regime_stats[regime]["pnl"] += pnl
            regime_stats[regime]["pnls"].append(pnl)
            if pnl > 0:
                regime_stats[regime]["wins"] += 1

    result = {}
    for regime, stats in sorted(regime_stats.items()):
        wins_list = [p for p in stats["pnls"] if p > 0]
        losses_list = [p for p in stats["pnls"] if p <= 0]
        result[regime] = {
            "trades": stats["trades"],
            "wins": stats["wins"],
            "losses": stats["trades"] - stats["wins"],
            "win_rate": round(stats["wins"] / stats["trades"] * 100, 1) if stats["trades"] else 0,
            "pnl": round(stats["pnl"], 2),
            "avg_win": round(sum(wins_list) / len(wins_list), 2) if wins_list else 0,
            "avg_loss": round(sum(losses_list) / len(losses_list), 2) if losses_list else 0,
        }
    return result


# ═══════════════════════════════════════════════════════════════
# Phase 3: Edge × Regime × Weekday Matrix
# ═══════════════════════════════════════════════════════════════

def _matrix_default() -> Dict:
    """Factory for edge matrix cell stats."""
    return {"trades": 0, "wins": 0, "pnl": 0.0}


def collect_edge_matrix(weekly: Dict) -> Dict:
    """Cross-tabulation: edge × vix_regime × entry_day → P&L."""
    matrix = {}
    edges_set = set()
    regimes_set = set()
    days_set = set()

    for engine, edata in weekly.items():
        for trade in edata.get("recent_trades", []):
            edge = trade.get("edge", "unknown")
            regime = trade.get("vix_regime", "unknown")
            day = trade.get("entry_day", "Unknown")
            pnl = trade.get("pnl", 0)

            key = (edge, regime, day)
            if key not in matrix:
                matrix[key] = _matrix_default()
            matrix[key]["trades"] += 1
            matrix[key]["pnl"] += pnl
            if pnl > 0:
                matrix[key]["wins"] += 1

            edges_set.add(edge)
            regimes_set.add(regime)
            days_set.add(day)

    # Convert to serializable dict
    matrix_out = {}
    for (edge, regime, day), stats in matrix.items():
        dict_key = f"{edge}||{regime}||{day}"
        matrix_out[dict_key] = {
            "trades": stats["trades"],
            "wins": stats["wins"],
            "pnl": round(stats["pnl"], 2),
            "win_rate": round(stats["wins"] / stats["trades"] * 100, 1) if stats["trades"] else 0,
        }

    return {
        "matrix": matrix_out,
        "dimensions": {
            "edges": sorted(edges_set),
            "regimes": sorted(regimes_set),
            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        }
    }


# ═══════════════════════════════════════════════════════════════
# Phase 4: Gemini Hypothesis Generation
# ═══════════════════════════════════════════════════════════════

def ask_gemini(prompt: str, max_tokens: int = 8192) -> str:
    """Send to Gemini 3.1 Pro (free) → fallback Vertex Proxy."""
    if USE_FREE_TIER:
        try:
            result = call_pro(prompt, max_tokens=max_tokens, temperature=0.7)
            if result and not result.startswith("[ALL FAILED"):
                return result
        except Exception as e:
            print(f"[weekly_review_v2] Free tier error: {e}, falling back to Vertex...")

    # No proxy — return error if free tier fails
    return json.dumps({"error": "Gemini API unavailable — no proxy fallback", "hypotheses": []})


def build_hypothesis_prompt(weekly: Dict, regime: Dict, matrix: Dict) -> str:
    """Build prompt for Gemini to generate structured hypotheses."""
    week_end = datetime.now(HKT).strftime("%Y-%m-%d")
    week_start = (datetime.now(HKT) - timedelta(days=7)).strftime("%Y-%m-%d")

    lines = [
        "You are a professional quantitative trading analyst. Analyze the following weekly edge P&L data and generate structured hypotheses for backtesting.",
        "",
        f"Week: {week_start} → {week_end}",
        "",
        "## Per-Engine Edge P&L Breakdown",
    ]

    total_pnl = 0
    total_trades = 0
    for engine, edata in sorted(weekly.items()):
        total_pnl += edata["total_pnl"]
        total_trades += edata["total_trades"]
        lines.append(f"\n### {engine}")
        lines.append(f"Total: {edata['total_trades']} trades, ${edata['total_pnl']:+.2f} P&L, WR={edata['overall_win_rate']}%")
        for edge, estats in sorted(edata["edges"].items()):
            lines.append(f"  Edge '{edge}': {estats['trades']} trades, ${estats['pnl']:+.2f} P&L, WR={estats['win_rate']}%, PF={estats['profit_factor']}")

    lines.append(f"\n## Overall: {total_trades} trades, ${total_pnl:+.2f}\n")

    lines.append("## Regime Analysis (by VIX regime)")
    for regime, rstats in sorted(regime.items()):
        lines.append(f"  {regime}: {rstats['trades']} trades, ${rstats['pnl']:+.2f} P&L, WR={rstats['win_rate']}%")

    lines.append("\n## Edge × Regime × Weekday Matrix (top cells by abs P&L)")
    sorted_cells = sorted(matrix["matrix"].items(), key=lambda x: abs(x[1]["pnl"]), reverse=True)
    for cell_key, cstats in sorted_cells[:20]:
        edge, regime, day = cell_key.split("||")
        lines.append(f"  {edge} | {regime} | {day}: {cstats['trades']} trades, ${cstats['pnl']:+.2f} P&L, WR={cstats['win_rate']}%")

    lines.append("""
## Task

Generate structured trading hypotheses based on this data. Each hypothesis must be testable in backtest.

Return a JSON object with this exact schema:
{
  "hypotheses": [
    {
      "edge": "string (edge name like 'oi_delta')",
      "regime": "string (VIX regime like 'low', or 'all')",
      "hypothesis": "string (detailed hypothesis in English, max 200 chars)",
      "confidence": "number (0.0 to 1.0 based on data strength)",
      "test_params": {
        "action": "string (one of: increase_weight, decrease_weight, add_filter, remove_filter, change_threshold)",
        "target_engine": "string (engine name like 'btc')",
        "target_param": "string (param name if applicable, or null)",
        "new_value": "number or string (the proposed change)",
        "reasoning": "string (why this change is expected to improve P&L)"
      }
    }
  ]
}

Rules:
1. Only generate hypotheses with sufficient data (>= 3 trades minimum per cell)
2. Confidence reflects: sample size, P&L magnitude, consistency
3. Focus on actionable edge changes (increase/decrease weight, add/remove filters)
4. If P&L is negative for an edge+regime combo, suggest decreasing weight or adding a filter
5. If P&L is strongly positive, suggest increasing weight or replicating the pattern
6. Maximum 8 hypotheses
7. Output ONLY valid JSON, no markdown formatting
""")

    return "\n".join(lines)


def parse_hypotheses(gemini_response: str) -> List[Dict]:
    """Parse Gemini JSON response into hypotheses list."""
    try:
        # Try direct parse
        data = json.loads(gemini_response)
    except (json.JSONDecodeError, TypeError):
        # Try to extract JSON from markdown code block
        import re
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', gemini_response, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                # Try finding any JSON object
                match2 = re.search(r'\{.*"hypotheses".*\}', gemini_response, re.DOTALL)
                if match2:
                    try:
                        data = json.loads(match2.group(0))
                    except json.JSONDecodeError:
                        return []
                else:
                    return []
        else:
            return []

    hypotheses = data.get("hypotheses", [])
    # Validate structure
    valid = []
    for h in hypotheses:
        if isinstance(h, dict) and "edge" in h and "hypothesis" in h:
            valid.append(h)
    return valid


# ═══════════════════════════════════════════════════════════════
# Phase 5: Save & Apply
# ═══════════════════════════════════════════════════════════════

def save_hypotheses(hypotheses: List[Dict], weekly: Dict, regime: Dict, matrix: Dict):
    """Save hypotheses to weekly_hypotheses.json with full context."""
    payload = {
        "generated_at": datetime.now(HKT).isoformat(),
        "week_start": (datetime.now(HKT) - timedelta(days=7)).strftime("%Y-%m-%d"),
        "week_end": datetime.now(HKT).strftime("%Y-%m-%d"),
        "hypotheses": hypotheses,
        "summary": {
            "total_hypotheses": len(hypotheses),
            "high_confidence": len([h for h in hypotheses if h.get("confidence", 0) >= 0.7]),
            "actions": [h["test_params"]["action"] for h in hypotheses if "test_params" in h],
        },
        "context": {
            "weekly_summary": {e: {"total_pnl": d["total_pnl"], "total_trades": d["total_trades"]} for e, d in weekly.items()},
            "regime_summary": regime,
        },
    }
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(HYPOTHESES_FILE), suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(payload, f, indent=2, default=str)
    os.replace(tmp, HYPOTHESES_FILE)
    print(f"  💾 Hypotheses saved: {HYPOTHESES_FILE}")


def update_strategy_config(hypotheses: List[Dict]) -> bool:
    """
    Update strategy_config.json edge weights based on hypotheses.

    Returns True if changes were made.
    """
    if not hypotheses:
        return False

    try:
        with open(STRATEGY_CONFIG_FILE) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("  ⚠️ strategy_config.json not found or invalid")
        return False

    changed = False
    for h in hypotheses:
        test_params = h.get("test_params", {})
        action = test_params.get("action", "")
        target_engine = test_params.get("target_engine", "")
        target_param = test_params.get("target_param")
        new_value = test_params.get("new_value")

        if not target_engine or target_engine not in config.get("engines", {}):
            continue

        engine_config = config["engines"][target_engine]

        if action == "increase_weight":
            # Add a _weight_boost field if not present
            current = engine_config.get("_weight_boost", 1.0)
            boost = float(new_value) if isinstance(new_value, (int, float)) else 0.1
            engine_config["_weight_boost"] = round(min(current + boost, 2.0), 2)
            changed = True
            print(f"  📈 {target_engine}: _weight_boost {current} → {engine_config['_weight_boost']}")

        elif action == "decrease_weight":
            current = engine_config.get("_weight_boost", 1.0)
            reduce = float(new_value) if isinstance(new_value, (int, float)) else 0.1
            engine_config["_weight_boost"] = round(max(current - reduce, 0.1), 2)
            changed = True
            print(f"  📉 {target_engine}: _weight_boost {current} → {engine_config['_weight_boost']}")

        elif action == "change_threshold" and target_param:
            if target_param in engine_config:
                old_val = engine_config[target_param]
                try:
                    new_val = float(new_value) if isinstance(new_value, str) else new_value
                    engine_config[target_param] = new_val
                    changed = True
                    print(f"  🔧 {target_engine}.{target_param}: {old_val} → {new_val}")
                except (ValueError, TypeError):
                    pass

        elif action in ("add_filter", "remove_filter"):
            # Store filter suggestions in _filters list
            if "_filters" not in engine_config:
                engine_config["_filters"] = []
            filter_entry = {
                "action": action,
                "param": target_param,
                "value": new_value,
                "hypothesis": h.get("hypothesis", ""),
            }
            engine_config["_filters"].append(filter_entry)
            changed = True
            print(f"  🔍 {target_engine}: {action} filter on {target_param}={new_value}")

    if changed:
        config["_meta"]["updated"] = datetime.now(HKT).isoformat()
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STRATEGY_CONFIG_FILE), suffix='.tmp')
        with os.fdopen(fd, 'w') as f:
            json.dump(config, f, indent=2, default=str)
        os.replace(tmp, STRATEGY_CONFIG_FILE)
        print(f"  ✅ strategy_config.json updated")
    else:
        print(f"  ℹ️ No changes to strategy_config.json")

    return changed


def tg_notify_if_actionable(hypotheses: List[Dict], weekly: Dict, config_changed: bool, big_pnl_change: bool = False):
    """Send TG notification only if actionable (new hypotheses or big P&L change)."""
    has_high_confidence = any(h.get("confidence", 0) >= 0.7 for h in hypotheses)

    if not hypotheses and not config_changed and not big_pnl_change:
        print("  ℹ️ Nothing actionable — skipping TG notification")
        return

    total_pnl = sum(e["total_pnl"] for e in weekly.values())

    msg_parts = []
    if hypotheses:
        msg_parts.append(f"📊 *Weekly Review v2 — {len(hypotheses)} Hypotheses*")
        for h in hypotheses[:5]:
            conf = h.get("confidence", 0)
            star = "⭐" if conf >= 0.7 else "▫️"
            msg_parts.append(f"{star} `{h.get('edge', '?')}` [{h.get('regime', 'all')}] "
                           f"conf={conf:.0%}: {h.get('hypothesis', '')[:120]}")
        if len(hypotheses) > 5:
            msg_parts.append(f"... and {len(hypotheses)-5} more")
    else:
        msg_parts.append(f"🤖 *Weekly Review v2*")

    msg_parts.append(f"")
    msg_parts.append(f"Week P&L: *${total_pnl:+.2f}*")

    if config_changed:
        msg_parts.append(f"⚙️ strategy_config.json updated")

    if big_pnl_change:
        msg_parts.append(f"⚠️ Significant P&L change detected")

    msg_parts.append(f"`{datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}`")
    msg = "\n".join(msg_parts)

    try:
        from tg_notify import tg_send
        tg_send(msg, silent=not has_high_confidence)
        print("  ✅ TG notification sent")
    except Exception as e:
        print(f"  ⚠️ TG send failed: {e}")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(f"📊 每週邊緣績效檢討 v2 — {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 60)

    # ── Phase 1: Edge P&L Breakdown ──
    print("\n📡 Phase 1: Edge P&L Breakdown...")
    weekly = collect_weekly_edges()
    if not weekly:
        print("  ⚠️ No trade data in last 7 days")
        report = f"# Weekly Review v2 — {datetime.now(HKT).strftime('%Y-%m-%d')}\n\nNo trades in the last 7 days.\n"
        fname = datetime.now(HKT).strftime("weekly_review_v2_%Y%m%d")
        txt_path = os.path.join(REVIEW_DIR, f"{fname}.txt")
        with open(txt_path, "w") as f:
            f.write(report)
        tg_notify_if_actionable([], weekly, False, False)
        return

    total_pnl = sum(e["total_pnl"] for e in weekly.values())
    total_trades = sum(e["total_trades"] for e in weekly.values())
    print(f"  📊 {len(weekly)} engines, {total_trades} trades, P&L=${total_pnl:+.2f}")

    for engine, edata in sorted(weekly.items()):
        print(f"\n  🔹 {engine}: {edata['total_trades']} trades, ${edata['total_pnl']:+.2f} P&L, WR={edata['overall_win_rate']}%")
        for edge, estats in sorted(edata["edges"].items()):
            print(f"      {edge}: {estats['trades']} trades, ${estats['pnl']:+.2f} P&L, WR={estats['win_rate']}%, PF={estats['profit_factor']}")

    # ── Phase 2: Regime Analysis ──
    print("\n📡 Phase 2: Regime Analysis...")
    regime = collect_regime_analysis(weekly)
    for regime_name, rstats in sorted(regime.items()):
        print(f"  {regime_name}: {rstats['trades']} trades, ${rstats['pnl']:+.2f} P&L, WR={rstats['win_rate']}%")

    # ── Phase 3: Edge × Regime × Weekday Matrix ──
    print("\n📡 Phase 3: Edge × Regime × Day Matrix...")
    matrix = collect_edge_matrix(weekly)
    top_cells = sorted(matrix["matrix"].items(), key=lambda x: abs(x[1]["pnl"]), reverse=True)[:10]
    for cell_key, cstats in top_cells:
        edge, regime_name, day = cell_key.split("||")
        print(f"  {edge:20s} | {regime_name:12s} | {day:9s}: {cstats['trades']} trades, ${cstats['pnl']:+.2f} P&L, WR={cstats['win_rate']}%")

    # ── Phase 4: Gemini Hypothesis Generation ──
    print("\n🤖 Phase 4: Gemini Hypothesis Generation...")
    prompt = build_hypothesis_prompt(weekly, regime, matrix)
    gemini_response = ask_gemini(prompt, max_tokens=8192)
    hypotheses = parse_hypotheses(gemini_response)
    print(f"  🔮 Generated {len(hypotheses)} hypotheses")
    for h in hypotheses:
        print(f"      [{h.get('confidence', 0):.0%}] {h.get('edge', '?')}/{h.get('regime', 'all')}: {h.get('hypothesis', '')[:100]}")

    if not hypotheses:
        print("  ⚠️ No valid hypotheses parsed. Raw response saved for debugging.")
        debug_path = os.path.join(REVIEW_DIR, "gemini_raw_response.json")
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(debug_path), suffix='.tmp')
        with os.fdopen(fd, 'w') as f:
            json.dump({"raw": gemini_response, "prompt": prompt}, f, indent=2, default=str)
        os.replace(tmp, debug_path)

    # ── Phase 5: Save + Apply ──
    print("\n📡 Phase 5: Save & Apply...")
    save_hypotheses(hypotheses, weekly, regime, matrix)
    config_changed = update_strategy_config(hypotheses)

    # ── Save full report ──
    now = datetime.now(HKT)
    fname = now.strftime("weekly_review_v2_%Y%m%d")
    txt_path = os.path.join(REVIEW_DIR, f"{fname}.txt")
    json_path = os.path.join(REVIEW_DIR, f"{fname}.json")

    report_parts = [
        f"# 📊 Weekly Edge Review v2 — {now.strftime('%Y-%m-%d %H:%M HKT')}",
        "",
        f"## Executive Summary",
        f"- Engines active: {len(weekly)}",
        f"- Total trades: {total_trades}",
        f"- Total P&L: ${total_pnl:+.2f}",
        f"- Overall win rate: {sum(e['total_wins'] for e in weekly.values()) / total_trades * 100:.1f}%" if total_trades else "- No trades",
        "",
        f"## Per-Engine Edge Breakdown",
    ]

    for engine, edata in sorted(weekly.items()):
        report_parts.append(f"\n### {engine}")
        report_parts.append(f"Total: {edata['total_trades']} trades | P&L: ${edata['total_pnl']:+.2f} | WR: {edata['overall_win_rate']}%")
        for edge, estats in sorted(edata["edges"].items()):
            report_parts.append(f"- **{edge}**: {estats['trades']} trades, ${estats['pnl']:+.2f} P&L, WR={estats['win_rate']}%, PF={estats['profit_factor']}")

    report_parts.append(f"\n## Regime Analysis")
    for regime_name, rstats in sorted(regime.items()):
        report_parts.append(f"- **{regime_name}**: {rstats['trades']} trades, ${rstats['pnl']:+.2f} P&L, WR={rstats['win_rate']}%")

    report_parts.append(f"\n## Top Edge × Regime × Day Cells")
    for cell_key, cstats in top_cells:
        edge, regime_name, day = cell_key.split("||")
        report_parts.append(f"- {edge} / {regime_name} / {day}: {cstats['trades']} trades, ${cstats['pnl']:+.2f} P&L")

    report_parts.append(f"\n## Hypotheses ({len(hypotheses)})")
    for i, h in enumerate(hypotheses, 1):
        report_parts.append(f"\n{i}. **{h.get('edge', '?')}** | {h.get('regime', 'all')} | confidence={h.get('confidence', 0):.0%}")
        report_parts.append(f"   {h.get('hypothesis', '')}")
        tp = h.get("test_params", {})
        if tp.get("action"):
            report_parts.append(f"   → {tp['action']} on {tp.get('target_engine', '?')}")

    report = "\n".join(report_parts)

    with open(txt_path, "w") as f:
        f.write(report)

    full_json = {
        "timestamp": now.isoformat(),
        "type": "weekly_review_v2",
        "weekly_edges": weekly,
        "regime_analysis": regime,
        "edge_matrix": matrix,
        "hypotheses": hypotheses,
        "config_updated": config_changed,
    }
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(json_path), suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(full_json, f, indent=2, default=str)
    os.replace(tmp, json_path)

    print(f"\n💾 Report saved: {txt_path}")
    print(f"💾 JSON saved: {json_path}")

    # ── TG notify only if actionable ──
    tg_notify_if_actionable(hypotheses, weekly, config_changed)

    print("\n✅ Weekly Review v2 complete")


if __name__ == "__main__":
    main()
