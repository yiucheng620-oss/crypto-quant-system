#!/usr/bin/env python3
"""
缠論 System — Data Exporter（Passive Logging Only）
==================================================
不影響任何 system logic，只輸出結構化數據供其他系統使用。

輸出檔案（全部喺 chanlun_system/exports/）：
  1. pivot_registry.json  — 所有 detected 中樞（動態 S/R 參考）
  2. signal_outcomes.json — 1买 信號歷史結果（backtest 用）
  3. structure_state.json — 當前市場結構狀態（regime-like）
  4. cross_ref_snapshot.json — 中樞 + Smart Money 交叉參考
"""
import json, os, sys
from datetime import datetime, timezone, timedelta
import numpy as np

HKT = timezone(timedelta(hours=8))
SYS_DIR = os.path.expanduser("~/workspace/chanlun_system")
EXPORT_DIR = os.path.join(SYS_DIR, "data/exports")
SIGNAL_FILE = os.path.join(SYS_DIR, "data/signals", "latest.json")
TRACKER_FILE = os.path.join(SYS_DIR, "data/signals", "tracker.json")
SM_FILE = os.path.expanduser("~/workspace/data/smart_money/smart_money_latest.json")

os.makedirs(EXPORT_DIR, exist_ok=True)

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)

def export_all():
    now = datetime.now(HKT)
    timestamp = now.isoformat()
    
    # ════════════════════════════════════════════════
    # 1. Pivot Registry — growing list of all pivots
    # ════════════════════════════════════════════════
    latest = load_json(SIGNAL_FILE)
    stats = latest.get("stats", {})
    
    registry_file = os.path.join(EXPORT_DIR, "pivot_registry.json")
    registry = load_json(registry_file, {"pivots": [], "last_updated": ""})
    
    # Extract pivot data from scanner stats
    # Scanner saves per-symbol pivots, we log them here
    new_pivots = 0
    for key, stat in stats.items():
        if isinstance(stat, dict) and "pivots" in stat:
            # key format: "BTC_4h"
            parts = key.split("_")
            symbol = parts[0]
            tf = "_".join(parts[1:]) if len(parts) > 1 else "?"
            
            entry = {
                "timestamp": timestamp,
                "symbol": symbol,
                "tf": tf,
                "total_pivots": stat.get("pivots", 0),
                "total_signals": stat.get("total_1buy", 0),
                "active_signals": stat.get("active", 0),
                "swing_count": stat.get("swings", 0),
            }
            
            # Only add if changed from last entry
            last_entry = None
            for p in reversed(registry["pivots"]):
                if p.get("symbol") == symbol and p.get("tf") == tf:
                    last_entry = p
                    break
            
            if last_entry is None or last_entry.get("total_pivots") != entry["total_pivots"]:
                registry["pivots"].append(entry)
                new_pivots += 1
    
    # Keep last 1000 entries
    if len(registry["pivots"]) > 1000:
        registry["pivots"] = registry["pivots"][-1000:]
    
    registry["last_updated"] = timestamp
    registry["total_entries"] = len(registry["pivots"])
    save_json(registry_file, registry)
    
    # ════════════════════════════════════════════════
    # 2. Signal Outcomes — historical 1买 results
    # ════════════════════════════════════════════════
    tracker = load_json(TRACKER_FILE)
    outcomes_file = os.path.join(EXPORT_DIR, "signal_outcomes.json")
    outcomes = load_json(outcomes_file, {"signals": [], "summary": {}})
    
    # Process expired signals from tracker history
    history = tracker.get("history", [])
    new_outcomes = 0
    
    for h in history:
        key = h.get("key", "")
        # Check if already logged
        already_logged = any(o.get("key") == key for o in outcomes["signals"])
        if not already_logged and h.get("status") == "expired":
            outcome = {
                "key": key,
                "symbol": h.get("symbol"),
                "tf": h.get("tf"),
                "entry_price": h.get("entry_price"),
                "exit_price": h.get("current_price"),
                "pct_return": h.get("pct_from_entry"),
                "first_seen": h.get("first_seen"),
                "expired_at": h.get("expired_at", timestamp),
                "pivot_lower": h.get("pivot_lower"),
                "pivot_upper": h.get("pivot_upper"),
                "profitable": h.get("pct_from_entry", 0) > 0,
            }
            outcomes["signals"].append(outcome)
            new_outcomes += 1
    
    # Update summary
    if outcomes["signals"]:
        total = len(outcomes["signals"])
        wins = sum(1 for o in outcomes["signals"] if o.get("profitable"))
        avg_return = sum(o.get("pct_return", 0) for o in outcomes["signals"]) / total
        outcomes["summary"] = {
            "total": total,
            "wins": wins,
            "win_rate": round(wins / total * 100, 1),
            "avg_return_pct": round(avg_return, 2),
            "last_updated": timestamp,
        }
    
    save_json(outcomes_file, outcomes)
    
    # ════════════════════════════════════════════════
    # 3. Structure State — current market structure
    # ════════════════════════════════════════════════
    structure_file = os.path.join(EXPORT_DIR, "structure_state.json")
    
    # Load latest pivot data from scanner
    signals = latest.get("signals", [])
    
    state = {
        "timestamp": timestamp,
        "markets": {},
    }
    
    for sym in ["BTC", "ETH", "SOL"]:
        sym_signals = [s for s in signals if s.get("symbol") == sym]
        
        # Active 1买 signals → means price is recovering from pivot breakdown
        active = [s for s in sym_signals if s.get("bars_ago", 999) <= 50]
        
        # Determine structure state
        if not sym_signals and not active:
            structure = "above_all_pivots"  # no breakdown = healthy
        elif active:
            # Price recovering from pivot breakdown → potential bottom
            s = active[0]
            structure = "recovering_from_breakdown"
        else:
            structure = "between_pivots"
        
        state["markets"][sym] = {
            "structure": structure,
            "active_1buy": len(active),
            "nearest_pivot_lower": min(
                (s.get("pivot_lower", 0) for s in sym_signals), default=None
            ),
            "nearest_pivot_upper": max(
                (s.get("pivot_upper", 0) for s in sym_signals), default=None
            ),
        }
    
    save_json(structure_file, state)
    
    # ════════════════════════════════════════════════
    # 4. Cross-Reference: Pivot + Smart Money
    # ════════════════════════════════════════════════
    crossref_file = os.path.join(EXPORT_DIR, "cross_ref_snapshot.json")
    
    # Load Smart Money data
    sm_data = load_json(SM_FILE, {})
    sm_btc = sm_data.get("data", {}).get("BTCUSDT", {})
    sm_eth = sm_data.get("data", {}).get("ETHUSDT", {})
    sm_sol = sm_data.get("data", {}).get("SOLUSDT", {})
    
    crossref = {
        "timestamp": timestamp,
        "snapshots": [],
    }
    
    for sym, sm in [("BTC", sm_btc), ("ETH", sm_eth), ("SOL", sm_sol)]:
        sym_signals = [s for s in signals if s.get("symbol") == sym]

        # 按 quality 分類 1buy 信號 (quality < 50 → weak，只 paper track)
        sym_1buy_strong = [
            s for s in sym_signals
            if s.get("type") == "1buy" and (s.get("quality_score", 0) or 0) >= 50
        ]
        sym_1buy_weak = [
            s for s in sym_signals
            if s.get("type") == "1buy" and (s.get("quality_score", 0) or 0) < 50
        ]
        sym_other_cnt = len([s for s in sym_signals if s.get("type") != "1buy"])

        all_scores = [
            s.get("quality_score", 0) for s in sym_signals
            if s.get("type") == "1buy" and s.get("quality_score") is not None
        ]

        quality_info = {}
        if all_scores:
            quality_info = {
                "strong_1buy": len([s for s in sym_1buy_strong if (s.get("quality_score", 0) or 0) >= 75]),
                "normal_1buy": len([s for s in sym_1buy_strong if 50 <= (s.get("quality_score", 0) or 0) < 75]),
                "weak_1buy": len(sym_1buy_weak),
                "avg_score": round(sum(all_scores) / len(all_scores), 1),
                "max_score": max(all_scores),
            }

        snapshot = {
            "symbol": sym,
            "smart_money": {
                "ls_ratio": sm.get("longShortRatio"),
                "long_entry": sm.get("longWhalesAvgEntryPrice"),
                "short_entry": sm.get("shortWhalesAvgEntryPrice"),
                "long_profit_pct": (
                    sm.get("longProfitTraders", 0) / max(sm.get("longTraders", 1), 1) * 100
                ) if sm else None,
            },
            "chanlun": {
                "has_1buy": len(sym_1buy_strong) > 0,
                "active_signals": len([s for s in sym_signals if s.get("bars_ago", 999) <= 50]),
                "total_1buy": len(all_scores),
                "quality_scores": quality_info,
            },
        }
        crossref["snapshots"].append(snapshot)
    
    # Append to history (keep last 1500 snapshots to allow 3 symbols × 500 runs)
    existing = load_json(crossref_file, {"snapshots": []})
    existing["snapshots"].extend(crossref["snapshots"])
    if len(existing["snapshots"]) > 1500:
        existing["snapshots"] = existing["snapshots"][-1500:]
    existing["last_updated"] = timestamp
    existing["total_snapshots"] = len(existing["snapshots"])
    save_json(crossref_file, existing)
    
    # ── Summary ──
    return {
        "pivot_registry": f"{len(registry['pivots'])} entries ({new_pivots} new)",
        "signal_outcomes": f"{len(outcomes['signals'])} outcomes ({new_outcomes} new)",
        "structure_state": f"{len(state['markets'])} markets",
        "cross_ref": f"{len(existing['snapshots'])} snapshots",
    }

if __name__ == "__main__":
    result = export_all()
    
    if "--quiet" not in sys.argv:
        print("📤 缠論 Data Export")
        print("=" * 50)
        for name, status in result.items():
            print(f"  {name}: {status}")
        print(f"\n  Export dir: {EXPORT_DIR}")
