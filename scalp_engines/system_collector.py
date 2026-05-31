#!/usr/bin/env python3
"""
📊 System State Collector — gathers all engine data for retrospective analysis.

Run daily before the LLM retrospective. Outputs a structured JSON that the 
LLM can read and analyze. No LLM calls here — pure data collection.

Output: ~/workspace/scalp_lab/engines/system_snapshot.json
"""

import json
import os
import glob
import tempfile
import time
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENGINES_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
OUTPUT_FILE = os.path.join(ENGINES_DIR, "system_snapshot.json")


def collect_engine_state(engine_name: str) -> dict:
    """Collect one engine's state + trade summary."""
    result = {
        "name": engine_name,
        "state": {},
        "trades": {"total": 0, "open": 0, "closed": 0, "last_24h": 0, "recent_pnl": []},
        "signals": {"total_found": 0, "total_executed": 0},
        "errors": [],
    }

    # State file
    state_file = os.path.join(ENGINES_DIR, f"{engine_name}_state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                result["state"] = json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            result["errors"].append(f"state_read: {e}")

    # Trade file
    trade_file = os.path.join(ENGINES_DIR, f"{engine_name}_trades.json")
    if os.path.exists(trade_file):
        try:
            with open(trade_file) as f:
                trades = json.load(f)
            if isinstance(trades, list):
                now_ts = time.time()
                result["trades"]["total"] = len(trades)
                result["trades"]["open"] = sum(1 for t in trades if t.get("status") == "open")
                result["trades"]["closed"] = sum(1 for t in trades if t.get("status") == "closed")

                # Last 24h trades
                recent = [t for t in trades
                          if t.get("status") == "closed"
                          and now_ts - t.get("timestamp_ts", t.get("closed_at_ts", 0)) < 86400]
                result["trades"]["last_24h"] = len(recent)

                # Recent P&L
                for t in recent[-20:]:
                    pnl = t.get("pnl") or t.get("raw_pnl") or t.get("pnl_amt") or 0
                    result["trades"]["recent_pnl"].append({
                        "pnl": round(float(pnl), 2),
                        "direction": t.get("direction", "?"),
                        "strategy": t.get("strategy", "?"),
                        "close_reason": t.get("close_reason", "?"),
                        "closed_at": t.get("closed_at", "?"),
                    })
        except (json.JSONDecodeError, Exception) as e:
            result["errors"].append(f"trades_read: {e}")

    # Signal file
    signal_file = os.path.join(ENGINES_DIR, f"{engine_name}_signals.json")
    # Also try alternate names
    for sf in [signal_file, os.path.join(ENGINES_DIR, f"{engine_name}_signal.json")]:
        if os.path.exists(sf):
            try:
                with open(sf) as f:
                    sig = json.load(f)
                if isinstance(sig, dict):
                    result["signals"]["total_found"] = sig.get("signals_found", 0)
                    result["signals"]["total_executed"] = sig.get("executed", 0)
                elif isinstance(sig, list):
                    result["signals"] = {"total_found": len(sig)}
                break
            except (json.JSONDecodeError, Exception):
                pass

    return result


def collect_flow_data() -> dict:
    """Collect cross-market flow data."""
    flow_file = os.path.join(ENGINES_DIR, "flow_state.json")
    if os.path.exists(flow_file):
        try:
            with open(flow_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            pass
    return {}


def collect_coordinator_state() -> dict:
    """Collect coordinator state."""
    coord_file = os.path.join(ENGINES_DIR, "coordinator_state.json")
    if os.path.exists(coord_file):
        try:
            with open(coord_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            pass
    return {}


def collect_error_logs() -> list:
    """Collect recent error log entries from relevant log files only."""
    errors = []
    log_files = [
        os.path.join(ENGINES_DIR, "coordinator_state.json"),
    ]
    
    # Check coordinator for errors
    for log_file in log_files:
        if os.path.exists(log_file):
            try:
                with open(log_file) as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("breaker_tripped"):
                    errors.append(f"COORDINATOR: circuit breaker tripped")
            except Exception:
                pass
    
    # Check for flow_state.json errors
    flow_file = os.path.join(ENGINES_DIR, "flow_state.json")
    if os.path.exists(flow_file):
        try:
            with open(flow_file) as f:
                flow = json.load(f)
            if flow.get("raw_data", {}).get("error"):
                errors.append(f"FLOW: {flow['raw_data']['error']}")
        except Exception:
            pass
    
    return errors[-20:]  # Max 20 errors


def collect_strategy_config() -> dict:
    """Collect current strategy config for comparison."""
    config_file = os.path.join(ENGINES_DIR, "strategy_config.json")
    if os.path.exists(config_file):
        try:
            with open(config_file) as f:
                cfg = json.load(f)
            # Return engines section only (exclude _meta, _ga_search_space)
            return cfg.get("engines", {})
        except (json.JSONDecodeError, Exception):
            pass
    return {}


def main():
    now = datetime.now(HKT)

    # Engine name mapping (config key → trade file name)
    engine_map = {
        "btc": "btc_swing",
        "eth": "eth_swing",
        "gold": "GOLD",
        "indices": "indices",
        "equities": "equities",
    }

    snapshot = {
        "collected_at": now.isoformat(),
        "collected_at_ts": time.time(),
        "day_of_week": now.strftime("%A"),
        "engines": {},
        "flow": collect_flow_data(),
        "coordinator": collect_coordinator_state(),
        "strategy_config": collect_strategy_config(),
        "errors": collect_error_logs(),
        "summary": {
            "total_closed_trades": 0,
            "total_open_positions": 0,
            "total_pnl_24h": 0.0,
            "engines_paused": [],
        },
    }

    for config_key, file_name in engine_map.items():
        eng = collect_engine_state(file_name)
        snapshot["engines"][config_key] = eng

        # Accumulate summary
        snapshot["summary"]["total_closed_trades"] += eng["trades"]["closed"]
        snapshot["summary"]["total_open_positions"] += eng["trades"]["open"]

        recent_pnl = sum(t["pnl"] for t in eng["trades"]["recent_pnl"])
        snapshot["summary"]["total_pnl_24h"] += recent_pnl

        if eng["state"].get("paused"):
            snapshot["summary"]["engines_paused"].append(config_key)

    snapshot["summary"]["total_pnl_24h"] = round(snapshot["summary"]["total_pnl_24h"], 2)

    # Write snapshot
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUTPUT_FILE), suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(snapshot, f, indent=2, default=str)
    os.replace(tmp, OUTPUT_FILE)

    # Print quick summary
    print(f"📊 System Snapshot: {now.strftime('%Y-%m-%d %H:%M HKT')}")
    print(f"   Closed trades: {snapshot['summary']['total_closed_trades']}")
    print(f"   Open positions: {snapshot['summary']['total_open_positions']}")
    print(f"   24h P&L: ${snapshot['summary']['total_pnl_24h']:+.2f}")
    if snapshot["summary"]["engines_paused"]:
        print(f"   ⚠️  Paused engines: {', '.join(snapshot['summary']['engines_paused'])}")
    if snapshot["errors"]:
        print(f"   ❌ Errors found: {len(snapshot['errors'])}")
    print(f"   Snapshot saved: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
