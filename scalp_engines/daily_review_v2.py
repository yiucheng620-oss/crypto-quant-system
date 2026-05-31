#!/usr/bin/env python3
"""
🔧 每日自動優化引擎 v2 — 08:00 HKT
=====================================
v2 升級內容:
  1. ✅ 修正 ENGINE_PARAMS mapping（match 實際 engine source code）
  2. ✅ Gemini 結構化輸出 (JSON schema parsing)
  3. ✅ 策略權重自動調整（7日 rolling P&L）
  4. ✅ System integrity check（dead param 掃描 + 自動修正）
  5. ✅ Auto-bug-fix: 偵測到 dead param 自動修正 ENGINE_PARAMS
  6. ✅ Aggressive risk: ±20% 權重調整, whipsaw SL 大幅放寬, TP 縮窄

Phase 1: 統計分析 (Pure Python, no LLM)
  - Per-engine: win rate, SL hit rate, TP hit rate, time stop rate
  - Per-engine: P&L by hour (HKT), by direction, by strategy
  - Per-engine: average hold time, trailing activation rate

Phase 2: 參數優化 (Deterministic rules, 6 rules + Aggressive)
  - SL too tight → widen (aggressive: ×2 step on whipsaw)
  - TP unreachable → narrow
  - Time stop too short → extend
  - Trailing never activates → lower activation
  - Low win rate + poor RR → widen SL
  - Direction bias → flag

Phase 3: Strategy Weight Auto-Adjust
  - 根據 7-day rolling P&L per strategy 調整 strategy_config.json 權重
  - ±20% daily limit

Phase 4: Gemini 戰略分析 + 結構化輸出
  - JSON schema: {actions: [{type, target, value, reason}]}
  - 解析後可自動執行策略級調整

Phase 5: System Integrity Check
  - 掃描所有 engine files，驗證 ENGINE_PARAMS mapping 正確
  - 發現 dead param 自動修正 mapping

Cron: 0 0 * * * (00:00 UTC = 08:00 HKT)
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import copy
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional, Dict, List, Any, Tuple

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENG_DIR = os.path.join(WORKSPACE, "scalp_engines")
LOG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
REVIEW_DIR = os.path.join(WORKSPACE, "reviews")
CHANGELOG_FILE = os.path.join(REVIEW_DIR, "param_changelog.json")
STRATEGY_CONFIG_FILE = os.path.join(LOG_DIR, "strategy_config.json")
INTEGRITY_LOG = os.path.join(REVIEW_DIR, "integrity_check.json")
os.makedirs(REVIEW_DIR, exist_ok=True)

# All Gemini calls go through gemini_free module (direct API, no proxy)

# ═══════════════════════════════════════════════════════════════
# CONFIG: 每個 engine 嘅可優化參數映射 (v2 FIXED)
# ═══════════════════════════════════════════════════════════════
# v1 bugs fixed:
#   - btc: OI_DELTA_LONG → OI_DELTA_LONG_THRESHOLD (actual var name)
#   - btc: OI_DELTA_SHORT → OI_DELTA_SHORT_THRESHOLD (actual var name)
#   - indices: removed SPX_STOP_PCT / NDX_STOP_PCT (they are in MARKET_CONFIGS dict, not class attrs)
#   - btc/eth: added TRAILING_DISTANCE_PCT (exists in all engine files)

ENGINE_PARAMS = {
    "btc": {
        "file": "btc_swing.py",
        "params": {
            # BTC uses ATR-based SL — NOT STOP_LOSS_PCT (dead param in btc_swing)
            "STOP_LOSS_ATR_MULT":        {"min": 1.0,  "max": 5.0,  "step": 0.5,  "default": 2.5},
            "TAKE_PROFIT_PCT":           {"min": 0.02, "max": 0.10, "step": 0.01,  "default": 0.05},
            "TRAILING_ACTIVATION_PCT":   {"min": 0.01, "max": 0.04, "step": 0.005, "default": 0.02},
            "TRAILING_DISTANCE_PCT":     {"min": 0.005,"max": 0.03, "step": 0.005, "default": 0.015},
            "TIME_STOP_MINUTES":         {"min": 60,   "max": 480,  "step": 60,   "default": 240},
            "OI_DELTA_LONG_THRESHOLD":   {"min": 0.1,  "max": 2.0,  "step": 0.1,   "default": 0.3},
            "OI_DELTA_SHORT_THRESHOLD":  {"min": -2.0, "max": -0.1, "step": 0.1,   "default": -0.5},
        }
    },
    "eth": {
        "file": "eth_swing.py",
        "params": {
            # ETH also uses ATR-based SL — NOT STOP_LOSS_PCT
            "STOP_LOSS_ATR_MULT":        {"min": 1.0,  "max": 5.0,  "step": 0.5,  "default": 2.5},
            "TAKE_PROFIT_PCT":           {"min": 0.02, "max": 0.10, "step": 0.01,  "default": 0.05},
            "TRAILING_ACTIVATION_PCT":   {"min": 0.01, "max": 0.04, "step": 0.005, "default": 0.02},
            "TRAILING_DISTANCE_PCT":     {"min": 0.005,"max": 0.03, "step": 0.005, "default": 0.015},
            "TIME_STOP_MINUTES":         {"min": 60,   "max": 480,  "step": 60,   "default": 240},
            "ATR_PERIOD":                {"min": 7,    "max": 28,   "step": 1,    "default": 14},
        }
    },
    "gold": {
        "file": "gold_scalp.py",
        "params": {
            # Gold uses fixed % SL — STOP_LOSS_PCT is ALIVE here
            "STOP_LOSS_PCT":             {"min": 0.005,"max": 0.04, "step": 0.005, "default": 0.02},
            "TAKE_PROFIT_PCT":           {"min": 0.02, "max": 0.08, "step": 0.01,  "default": 0.05},
            "VIX_HIGH_THRESHOLD":        {"min": 15.0, "max": 35.0, "step": 2.5,   "default": 25.0},
            "MAX_CONSEC_LOSSES":         {"min": 3,    "max": 8,    "step": 1,     "default": 5},
        }
    },
    "indices": {
        "file": "indices_scalp.py",
        "params": {
            "MAX_CONSEC_LOSSES":         {"min": 3,    "max": 8,    "step": 1,     "default": 5},
        }
    },
    "equities": {
        "file": "equities_swing.py",
        "params": {
            "STOP_LOSS_PCT":             {"min": 0.02, "max": 0.08, "step": 0.01,  "default": 0.03},
            "TAKE_PROFIT_PCT":           {"min": 0.05, "max": 0.20, "step": 0.02,  "default": 0.10},
        }
    },
}

# 策略權重配置 (from strategy_config.json engines section)
DEFAULT_STRATEGY_WEIGHTS = {
    "btc": 1.0,
    "eth": 1.0,
    "gold": 1.0,
    "indices": 1.0,
    "equities": 1.0,
}

WEIGHT_HISTORY_FILE = os.path.join(REVIEW_DIR, "weight_history.json")


# ═══════════════════════════════════════════════════════════════
# PHASE 1: 統計分析
# ═══════════════════════════════════════════════════════════════

def load_all_trades(hours: int = 168) -> Dict[str, List[Dict]]:
    """載入所有 engine 嘅 trades，預設 7 日。"""
    now = time.time()
    cutoff = now - (hours * 3600)
    all_trades = {}

    for fname in sorted(os.listdir(LOG_DIR)):
        if not fname.endswith("_trades.json"):
            continue
        engine = fname.replace("_trades.json", "")
        path = os.path.join(LOG_DIR, fname)
        try:
            with open(path) as f:
                trades = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue

        recent = [t for t in trades if t.get("timestamp_ts", 0) >= cutoff]
        if recent:
            all_trades[engine] = recent

    return all_trades


def load_all_trades_alltime() -> Dict[str, List[Dict]]:
    """載入所有 engine 嘅全部 trades（不限時間）。"""
    all_trades = {}
    for fname in sorted(os.listdir(LOG_DIR)):
        if not fname.endswith("_trades.json"):
            continue
        engine = fname.replace("_trades.json", "")
        path = os.path.join(LOG_DIR, fname)
        try:
            with open(path) as f:
                trades = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        if trades:
            all_trades[engine] = trades
    return all_trades


def analyze_engine(engine: str, trades: List[Dict]) -> Dict:
    """分析單一 engine 嘅交易數據。"""
    closed = [t for t in trades if t.get("status") == "closed"]
    open_pos = [t for t in trades if t.get("status") == "open"]
    if not closed:
        return {
            "engine": engine,
            "total": len(trades),
            "open": len(open_pos),
            "closed": 0,
            "insufficient_data": True,
        }

    # Win/loss
    winners = [t for t in closed if t.get("adjusted_pnl", t.get("pnl", 0)) > 0]
    losers = [t for t in closed if t.get("adjusted_pnl", t.get("pnl", 0)) <= 0]
    win_rate = len(winners) / len(closed) if closed else 0

    # Close reasons
    sl_count = sum(1 for t in closed if "tp_sl_hit" in str(t.get("close_reason", "")) and t.get("adjusted_pnl", 0) <= 0)
    tp_count = sum(1 for t in closed if "tp_sl_hit" in str(t.get("close_reason", "")) and t.get("adjusted_pnl", 0) > 0)
    time_stop_count = sum(1 for t in closed if "time_stop" in str(t.get("close_reason", "")))
    manual_count = len(closed) - sl_count - tp_count - time_stop_count

    sl_rate = sl_count / len(closed) if closed else 0
    tp_rate = tp_count / len(closed) if closed else 0
    time_stop_rate = time_stop_count / len(closed) if closed else 0

    # P&L stats
    total_pnl = sum(t.get("adjusted_pnl", t.get("pnl", 0)) for t in closed)
    avg_win = sum(t.get("adjusted_pnl", t.get("pnl", 0)) for t in winners) / len(winners) if winners else 0
    avg_loss = sum(t.get("adjusted_pnl", t.get("pnl", 0)) for t in losers) / len(losers) if losers else 0

    # Hold time
    hold_times = []
    for t in closed:
        ts = t.get("timestamp_ts", 0)
        close_ts = t.get("closed_at_ts", 0)
        if ts > 0 and close_ts > 0:
            hold_times.append((close_ts - ts) / 60)  # minutes
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0

    # Trailing activation
    trailing_active = sum(1 for t in closed if t.get("trailing_active")) if closed else 0
    trailing_rate = trailing_active / len(closed) if closed else 0

    # P&L by hour (HKT)
    pnl_by_hour = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
    for t in closed:
        ts = t.get("timestamp_ts", 0)
        if ts > 0:
            hour = datetime.fromtimestamp(ts, HKT).hour
            pnl = t.get("adjusted_pnl", t.get("pnl", 0))
            pnl_by_hour[hour]["pnl"] += pnl
            pnl_by_hour[hour]["trades"] += 1
            if pnl > 0:
                pnl_by_hour[hour]["wins"] += 1

    # Find worst hours
    worst_hours = sorted(
        [(h, d) for h, d in pnl_by_hour.items() if d["trades"] >= 2],
        key=lambda x: x[1]["pnl"]
    )[:3]

    # Direction breakdown
    long_trades = [t for t in closed if t.get("direction") == "LONG"]
    short_trades = [t for t in closed if t.get("direction") == "SHORT"]
    long_win_rate = sum(1 for t in long_trades if t.get("adjusted_pnl", t.get("pnl", 0)) > 0) / len(long_trades) if long_trades else 0
    short_win_rate = sum(1 for t in short_trades if t.get("adjusted_pnl", t.get("pnl", 0)) > 0) / len(short_trades) if short_trades else 0

    # Strategy breakdown (for weight adjustment)
    strategy_pnl = defaultdict(float)
    strategy_trades = defaultdict(int)
    for t in closed:
        strat = t.get("strategy", "unknown")
        strategy_pnl[strat] += t.get("adjusted_pnl", t.get("pnl", 0))
        strategy_trades[strat] += 1

    return {
        "engine": engine,
        "total": len(trades),
        "open": len(open_pos),
        "closed": len(closed),
        "insufficient_data": False,
        # Core metrics
        "win_rate": round(win_rate * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        # Close reasons
        "sl_count": sl_count,
        "tp_count": tp_count,
        "time_stop_count": time_stop_count,
        "manual_count": manual_count,
        "sl_rate": round(sl_rate * 100, 1),
        "tp_rate": round(tp_rate * 100, 1),
        "time_stop_rate": round(time_stop_rate * 100, 1),
        # Timing
        "avg_hold_min": round(avg_hold, 1),
        # Trailing
        "trailing_rate": round(trailing_rate * 100, 1),
        # Direction
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_win_rate": round(long_win_rate * 100, 1),
        "short_win_rate": round(short_win_rate * 100, 1),
        # Worst performing hours
        "worst_hours": [{"hour": h, "pnl": d["pnl"], "trades": d["trades"]} for h, d in worst_hours],
        # Strategy breakdown
        "strategy_pnl": dict(strategy_pnl),
        "strategy_trades": dict(strategy_trades),
    }


def analyze_all() -> Dict:
    """分析全部 engine (7 日數據)。"""
    all_trades = load_all_trades(hours=168)
    results = {}
    for engine, trades in all_trades.items():
        results[engine] = analyze_engine(engine, trades)
    return results


# ═══════════════════════════════════════════════════════════════
# PHASE 2: 參數優化 Rules (v2 — Aggressive risk added)
# ═══════════════════════════════════════════════════════════════

def read_param_from_file(file_path: str, param_name: str, default: Any = None) -> Any:
    """Read a parameter value from an engine source file.
    Falls back to engine_base.py if not found in engine file.
    Falls back to default if not found anywhere.
    Supports negative values (e.g., OI_DELTA_SHORT_THRESHOLD = -0.5).
    """
    try:
        # 1. Try the engine file first
        with open(file_path) as f:
            content = f.read()

        patterns = [
            rf'{param_name}\s*:\s*\w+\s*=\s*(-?[\d.]+)',         # X: float = 0.03 (type annotation)
            rf'{param_name}\s*[:=]\s*(-?[\d.]+)',                # X = 0.03 or X = -0.5 (simple assignment)
        ]
        for pat in patterns:
            m = re.search(pat, content)
            if m:
                val = m.group(1)
                if '.' in val:
                    return float(val)
                return int(val)

        # 2. Fallback: check engine_base.py（inherited params）
        base_file = os.path.join(ENG_DIR, "engine_base.py")
        if os.path.exists(base_file) and file_path != base_file:
            with open(base_file) as f:
                content = f.read()
            for pat in patterns:
                m = re.search(pat, content)
                if m:
                    val = m.group(1)
                    if '.' in val:
                        return float(val)
                    return int(val)

        # 3. Fallback: use provided default
        if default is not None:
            return default

    except Exception as e:
        print(f"  ⚠️ Failed to read {param_name} from {file_path}: {e}")

    return default


def read_market_config_param(file_path: str, market_key: str, param_key: str, default: Any = None) -> Any:
    """Read a parameter from the MARKET_CONFIGS dict in indices_scalp.py.
    e.g. read_market_config_param('indices_scalp.py', 'SPX', 'stop_pct')
    """
    try:
        with open(file_path) as f:
            content = f.read()
        # Find MARKET_CONFIGS dict
        # Pattern: "SPX": { ..., "stop_pct": 0.008, ... }
        pat = rf'"{market_key}"\s*:\s*\{{[^}}]*?"{param_key}"\s*:\s*([\d.]+)'
        m = re.search(pat, content, re.DOTALL)
        if m:
            val = m.group(1)
            if '.' in val:
                return float(val)
            return int(val)
    except Exception as e:
        print(f"  ⚠️ Failed to read MARKET_CONFIGS[{market_key}][{param_key}] from {file_path}: {e}")
    return default


def apply_param_change(file_path: str, param_name: str, current_val: Any, new_val: Any) -> bool:
    """Apply a parameter change to the engine source file.
    If param not found in engine file (inherited from base), inserts override.
    """
    try:
        with open(file_path) as f:
            content = f.read()

        # ─── Strategy 1: Exact patch existing param ───
        patterns = [
            (rf'({param_name}\s*:\s*\w+\s*=\s*){re.escape(str(current_val))}', rf'\g<1>{new_val}'),
            (rf'({param_name}\s*=\s*){re.escape(str(current_val))}', rf'\g<1>{new_val}'),
        ]

        for pat, repl in patterns:
            new_content, count = re.subn(pat, repl, content)
            if count > 0:
                backup = file_path + f".bak_{datetime.now(HKT).strftime('%Y%m%d_%H%M')}"
                with open(backup, "w") as f:
                    f.write(content)
                with open(file_path, "w") as f:
                    f.write(new_content)
                print(f"  ✅ Patched {param_name}: {current_val} → {new_val} in {os.path.basename(file_path)}")
                print(f"     Backup: {backup}")
                return True

        # ─── Strategy 2: Param inherited from base — insert override ───
        # Remove any existing override lines for this param (prevent duplicates)
        content = re.sub(
            rf'^    {re.escape(param_name)}:.*# Auto-optimized by daily_review\s*\n',
            '',
            content,
            flags=re.MULTILINE
        )
        # Find class definition line and insert param after it
        class_match = re.search(r'(class\s+\w+.*?:\n)', content)
        if class_match:
            insert_pos = class_match.end()
            # Check what type to use
            if isinstance(new_val, float) or (isinstance(current_val, float)):
                type_hint = "float"
            elif isinstance(new_val, int):
                type_hint = "int"
            else:
                type_hint = "float"

            override_line = f"    {param_name}: {type_hint} = {new_val}  # Auto-optimized by daily_review\n"
            new_content = content[:insert_pos] + override_line + content[insert_pos:]

            backup = file_path + f".bak_{datetime.now(HKT).strftime('%Y%m%d_%H%M')}"
            with open(backup, "w") as f:
                f.write(content)
            with open(file_path, "w") as f:
                f.write(new_content)
            print(f"  ✅ Inserted {param_name}: {current_val} → {new_val} (inherited override) in {os.path.basename(file_path)}")
            print(f"     Backup: {backup}")
            return True

        print(f"  ⚠️ Could not find class definition in {file_path}")
        return False

    except Exception as e:
        print(f"  ❌ Failed to patch {param_name} in {file_path}: {e}")
        return False


def apply_market_config_change(file_path: str, market_key: str, param_key: str, current_val: Any, new_val: Any) -> bool:
    """Apply a change inside MARKET_CONFIGS dict (for indices_scalp.py).
    e.g. apply_market_config_change('indices_scalp.py', 'SPX', 'stop_pct', 0.008, 0.010)
    """
    try:
        with open(file_path) as f:
            content = f.read()

        # Match the specific key-value inside the market config block
        pat = rf'("{market_key}"\s*:\s*\{{[^}}]*?"{param_key}"\s*:\s*){re.escape(str(current_val))}'
        repl = rf'\g<1>{new_val}'
        new_content, count = re.subn(pat, repl, content, flags=re.DOTALL)
        if count > 0:
            backup = file_path + f".bak_{datetime.now(HKT).strftime('%Y%m%d_%H%M')}"
            with open(backup, "w") as f:
                f.write(content)
            with open(file_path, "w") as f:
                f.write(new_content)
            print(f"  ✅ Patched MARKET_CONFIGS[{market_key}][{param_key}]: {current_val} → {new_val}")
            print(f"     Backup: {backup}")
            return True
        print(f"  ⚠️ Could not find MARKET_CONFIGS[{market_key}][{param_key}] = {current_val}")
        return False
    except Exception as e:
        print(f"  ❌ Failed to patch MARKET_CONFIGS[{market_key}][{param_key}]: {e}")
        return False


def compute_optimizations(analysis: Dict) -> List[Dict]:
    """
    根據統計數據決定參數調整 (v2 — Aggressive risk rules added).
    返回 list of optimization actions。
    """
    actions = []

    for engine, stats in analysis.items():
        if stats.get("insufficient_data"):
            continue
        if engine not in ENGINE_PARAMS:
            continue

        cfg = ENGINE_PARAMS[engine]
        params = cfg["params"]
        file_path = os.path.join(ENG_DIR, cfg["file"])

        # ─── 確定呢個 engine 用邊種 SL 參數 ───
        # BTC/ETH: ATR-based → STOP_LOSS_ATR_MULT
        # Gold: Fixed % → STOP_LOSS_PCT
        # Indices: MARKET_CONFIGS dict (skip automatic SL optimization)
        sl_param = None
        if "STOP_LOSS_ATR_MULT" in params:
            sl_param = "STOP_LOSS_ATR_MULT"
        elif "STOP_LOSS_PCT" in params:
            sl_param = "STOP_LOSS_PCT"

        # ─── Rule 0: Whipsaw detection (Aggressive Risk) ───
        # If 100% SL rate, all losers, avg hold < 15min → SL definitely too tight
        if sl_param:
            sl_hit_rate = stats["sl_rate"]
            avg_hold = stats["avg_hold_min"]
            win_rate = stats["win_rate"]
            closed = stats["closed"]
            if sl_hit_rate >= 100 and win_rate == 0 and avg_hold < 15 and closed >= 1:
                current = read_param_from_file(file_path, sl_param, params[sl_param]["default"])
                new_val = min(current + params[sl_param]["step"] * 3,
                             params[sl_param]["max"])
                if new_val != current:
                    actions.append({
                        "engine": engine,
                        "param": sl_param,
                        "current": current,
                        "new": new_val,
                        "reason": f"🚨 AGGRESSIVE WHIPSAW: 全部 {closed} trades SL out, avg hold {avg_hold}min → {sl_param} 太窄，大幅放寬 3x",
                        "rule": "aggressive_whipsaw",
                    })

        # ─── Rule 1: SL too tight ───
        if sl_param:
            sl_hit_rate = stats["sl_rate"]
            avg_hold = stats["avg_hold_min"]
            if sl_hit_rate >= 60 and avg_hold < 30 and stats["closed"] >= 2:
                current = read_param_from_file(file_path, sl_param, params[sl_param]["default"])
                already_widened = any(a["engine"] == engine and a["param"] == sl_param for a in actions)
                if not already_widened:
                    new_val = min(current + params[sl_param]["step"],
                                 params[sl_param]["max"])
                    if new_val != current:
                        actions.append({
                            "engine": engine,
                            "param": sl_param,
                            "current": current,
                            "new": new_val,
                            "reason": f"SL hit rate {sl_hit_rate}% (>60%), avg hold {avg_hold}min (<30min) → {sl_param} 太窄",
                            "rule": "sl_too_tight",
                        })

        # ─── Rule 5: Win rate too low → widen SL ───
        if sl_param and "TAKE_PROFIT_PCT" in params:
            if stats["win_rate"] < 30 and stats["closed"] >= 3:
                if stats["avg_loss"] < 0 and abs(stats["avg_loss"]) > abs(stats["avg_win"]) * 2:
                    sl_current = read_param_from_file(file_path, sl_param, params[sl_param]["default"])
                    already_widened = any(a["engine"] == engine and a["param"] == sl_param for a in actions)
                    if not already_widened:
                        sl_new = min(sl_current + params[sl_param]["step"] * 2,
                                    params[sl_param]["max"])
                        if sl_new != sl_current:
                            actions.append({
                                "engine": engine,
                                "param": sl_param,
                                "current": sl_current,
                                "new": sl_new,
                                "reason": f"Win rate {stats['win_rate']}% (<30%), avg loss ${stats['avg_loss']} >> avg win ${stats['avg_win']} → {sl_param} 太窄",
                                "rule": "low_win_rate_wide_sl",
                            })

        # ─── Rule 2: TP unreachable ───
        # If TP hit rate = 0% AND win_rate > 0 → narrow TP
        if "TAKE_PROFIT_PCT" in params:
            tp_hit_rate = stats["tp_rate"]
            win_rate = stats["win_rate"]
            if tp_hit_rate == 0 and win_rate > 0 and stats["closed"] >= 2:
                current = read_param_from_file(file_path, "TAKE_PROFIT_PCT", params["TAKE_PROFIT_PCT"]["default"])
                new_val = max(current - params["TAKE_PROFIT_PCT"]["step"] * 2,
                             params["TAKE_PROFIT_PCT"]["min"])
                if new_val != current:
                    actions.append({
                        "engine": engine,
                        "param": "TAKE_PROFIT_PCT",
                        "current": current,
                        "new": new_val,
                        "reason": f"AGGRESSIVE: TP hit rate 0%, but win rate {win_rate}% → TP 太遠，大幅縮窄 2x",
                        "rule": "aggressive_tp_unreachable",
                    })

        # ─── Rule 3: Time stop too short ───
        if "TIME_STOP_MINUTES" in params:
            ts_rate = stats["time_stop_rate"]
            if ts_rate >= 40 and stats["closed"] >= 2:
                current = read_param_from_file(file_path, "TIME_STOP_MINUTES", params["TIME_STOP_MINUTES"]["default"])
                new_val = min(current + params["TIME_STOP_MINUTES"]["step"],
                             params["TIME_STOP_MINUTES"]["max"])
                if new_val != current:
                    actions.append({
                        "engine": engine,
                        "param": "TIME_STOP_MINUTES",
                        "current": current,
                        "new": new_val,
                        "reason": f"Time stop rate {ts_rate}% (>40%) → 時間唔夠，延長",
                        "rule": "time_stop_too_short",
                    })

        # ─── Rule 4: Trailing never activates ───
        # If trailing rate = 0% AND win_rate > 30% → lower activation
        if "TRAILING_ACTIVATION_PCT" in params:
            trail_rate = stats["trailing_rate"]
            if trail_rate == 0 and stats["win_rate"] >= 30 and stats["closed"] >= 2:
                current = read_param_from_file(file_path, "TRAILING_ACTIVATION_PCT", params["TRAILING_ACTIVATION_PCT"]["default"])
                new_val = max(current - params["TRAILING_ACTIVATION_PCT"]["step"],
                             params["TRAILING_ACTIVATION_PCT"]["min"])
                if new_val != current:
                    actions.append({
                        "engine": engine,
                        "param": "TRAILING_ACTIVATION_PCT",
                        "current": current,
                        "new": new_val,
                        "reason": f"Trailing 從未 activate, 但 win rate {stats['win_rate']}% → 降低 activation threshold",
                        "rule": "trailing_never_activates",
                    })

        # ─── Rule 6: Direction bias ───
        if stats["long_trades"] >= 3 and stats["short_trades"] >= 3:
            lw, sw = stats["long_win_rate"], stats["short_win_rate"]
            if abs(lw - sw) >= 30:
                better = "LONG" if lw > sw else "SHORT"
                actions.append({
                    "engine": engine,
                    "param": "DIRECTION_BIAS",
                    "current": f"LONG={lw}% SHORT={sw}%",
                    "new": f"PREFER_{better}",
                    "reason": f"方向偏差：{better} win rate 明顯高過相反方向 ({max(lw,sw)}% vs {min(lw,sw)}%)",
                    "rule": "direction_bias",
                    "action": "recommendation_only",
                })

        # ─── Rule 7: Indices MARKET_CONFIGS optimization ───
        # For indices, adjust stop_pct/tp_pct inside MARKET_CONFIGS dict
        if engine == "indices" and stats["closed"] >= 2:
            indices_file = os.path.join(ENG_DIR, "indices_scalp.py")
            for market_key in ["SPX", "NDX"]:
                # Adjust stop_pct based on SL rate
                stop_key = "stop_pct"
                current_stop = read_market_config_param(indices_file, market_key, stop_key)
                if current_stop is not None:
                    # If win rate < 30% and SL rate > 50%, widen stop
                    if stats["win_rate"] < 30 and stats["sl_rate"] >= 50:
                        new_stop = min(current_stop * 1.5, 0.05)
                        if new_stop != current_stop:
                            actions.append({
                                "engine": engine,
                                "param": f"MARKET_CONFIGS[{market_key}].{stop_key}",
                                "current": current_stop,
                                "new": round(new_stop, 4),
                                "reason": f"Indices WR {stats['win_rate']}% + SL {stats['sl_rate']}% → 放寬 {market_key} stop",
                                "rule": "indices_stop_adjust",
                            })

    # ─── Dedup: 同 engine 同 param 保留變動幅度最大嘅 action ───
    seen = {}
    deduped = []
    for a in actions:
        key = (a["engine"], a["param"])
        magnitude = abs(a.get("new", 0) - a.get("current", 0))
        if key not in seen:
            seen[key] = len(deduped)
            deduped.append(a)
        else:
            existing = deduped[seen[key]]
            existing_mag = abs(existing.get("new", 0) - existing.get("current", 0))
            if magnitude > existing_mag:
                deduped[seen[key]] = a
    return deduped


def apply_optimizations(actions: List[Dict]) -> List[Dict]:
    """Apply all parameter changes. Returns list of applied changes."""
    applied = []
    for action in actions:
        if action.get("action") == "recommendation_only":
            applied.append(action)
            continue

        engine = action["engine"]
        param = action["param"]
        file_path = os.path.join(ENG_DIR, ENGINE_PARAMS[engine]["file"])

        # Check if it's a MARKET_CONFIGS change (indices)
        if param.startswith("MARKET_CONFIGS"):
            # Parse: MARKET_CONFIGS[SPX].stop_pct
            m = re.match(r'MARKET_CONFIGS\[(\w+)\]\.(\w+)', param)
            if m:
                market_key, param_key = m.group(1), m.group(2)
                success = apply_market_config_change(file_path, market_key, param_key,
                                                     action["current"], action["new"])
                action["applied"] = success
                applied.append(action)
                continue

        success = apply_param_change(file_path, param, action["current"], action["new"])
        action["applied"] = success
        applied.append(action)
    return applied


# ═══════════════════════════════════════════════════════════════
# PHASE 3: Strategy Weight Auto-Adjust (v2 new)
# ═══════════════════════════════════════════════════════════════

def load_strategy_config() -> dict:
    """Load strategy_config.json."""
    try:
        with open(STRATEGY_CONFIG_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"engines": {}, "_meta": {"version": "1.0.0"}}


def save_strategy_config(config: dict):
    """Save strategy_config.json atomically (write to tmp, then rename)."""
    config.setdefault("_meta", {})["updated"] = datetime.now(HKT).isoformat()
    tmp = STRATEGY_CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2, default=str)
    os.replace(tmp, STRATEGY_CONFIG_FILE)
    print(f"  💾 Strategy config saved: {STRATEGY_CONFIG_FILE}")


def load_weight_history() -> List[Dict]:
    """Load weight adjustment history."""
    try:
        with open(WEIGHT_HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_weight_history(history: List[Dict]):
    """Save weight adjustment history atomically."""
    tmp = WEIGHT_HISTORY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(history, f, indent=2, default=str)
    os.replace(tmp, WEIGHT_HISTORY_FILE)


def compute_strategy_weight_adjustments(analysis: Dict) -> List[Dict]:
    """
    根據 7-day rolling P&L per engine 調整策略權重。
    ±20% daily limit.
    Returns list of weight changes.
    """
    adjustments = []

    # Load current config
    config = load_strategy_config()
    engines_config = config.get("engines", {})

    # Load history for smoothing
    history = load_weight_history()
    today = datetime.now(HKT).strftime("%Y-%m-%d")

    # Check if already adjusted today
    for entry in history:
        if entry.get("date") == today:
            print(f"  ⏭️ Weight already adjusted today ({today}), skipping")
            return []

    for engine, stats in analysis.items():
        if stats.get("insufficient_data"):
            continue
        if engine not in engines_config:
            continue

        engine_cfg = engines_config[engine]
        total_pnl = stats.get("total_pnl", 0)
        win_rate = stats.get("win_rate", 0)

        # Base weight: start from config or default
        # strategy_config.json doesn't have weights, so we use a weight_scale field
        current_weight = engine_cfg.get("_weight", 1.0)

        # Compute new weight based on performance
        if total_pnl > 0 and win_rate >= 40:
            # Profitable and decent win rate → increase weight
            performance_factor = min(total_pnl / 10.0, 0.20)  # Cap at +20%
            new_weight = round(current_weight * (1 + performance_factor), 3)
        elif total_pnl < 0:
            # Losing → decrease weight
            performance_factor = min(abs(total_pnl) / 10.0, 0.20)  # Cap at -20%
            new_weight = round(current_weight * (1 - performance_factor), 3)
        else:
            # Break even → no change
            new_weight = current_weight

        # Clamp to reasonable range
        new_weight = max(0.3, min(2.0, new_weight))

        if abs(new_weight - current_weight) > 0.01:
            # Update config
            engines_config[engine]["_weight"] = new_weight
            adjustments.append({
                "engine": engine,
                "old_weight": current_weight,
                "new_weight": new_weight,
                "pnl_7d": total_pnl,
                "win_rate_7d": win_rate,
                "reason": f"P&L=${total_pnl:+.2f}, WR={win_rate}% → weight {current_weight:.2f}→{new_weight:.2f}",
            })

    # Apply changes
    config["engines"] = engines_config
    save_strategy_config(config)

    # Record in history
    if adjustments:
        history_entry = {
            "date": today,
            "timestamp": datetime.now(HKT).isoformat(),
            "adjustments": adjustments,
        }
        history.append(history_entry)
        save_weight_history(history)

    return adjustments


# ═══════════════════════════════════════════════════════════════
# PHASE 4: Gemini 戰略分析 + 結構化輸出 (v2 new)
# ═══════════════════════════════════════════════════════════════

try:
    from gemini_free import call_pro
    USE_FREE_TIER = True
except ImportError:
    USE_FREE_TIER = False


def ask_gemini(prompt: str, max_tokens: int = 8192) -> str:
    """Send to Gemini 3.1 Pro (free) → fallback Vertex Proxy."""
    if USE_FREE_TIER:
        try:
            result = call_pro(prompt, max_tokens=max_tokens, temperature=0.7)
            if result and not result.startswith("[ALL FAILED"):
                return result
        except Exception as e:
            print(f"[daily_review_v2] Free tier error: {e}, falling back to Vertex...")

    # No proxy — return error if free tier fails
    return "[Gemini API error: no proxy fallback available]"


def parse_gemini_actions(response: str) -> List[Dict]:
    """
    Parse Gemini structured JSON output from response.
    Expected schema: {actions: [{type, target, value, reason}]}
    Supported types: adjust_stop, adjust_tp, adjust_weight, adjust_trailing, adjust_timestop, recommend
    """
    # Try to find JSON block in response
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find raw JSON object
        json_match = re.search(r'\{[^{}]*"actions"[^{}]*\[.*?\][^{}]*\}', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            print("  ⚠️ No structured JSON found in Gemini response")
            return []

    try:
        data = json.loads(json_str)
        actions = data.get("actions", [])
        if not isinstance(actions, list):
            return []
        # Validate each action
        valid = []
        for a in actions:
            if all(k in a for k in ("type", "target", "value")):
                valid.append(a)
        return valid
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  ⚠️ Failed to parse Gemini JSON: {e}")
        return []


def build_strategic_prompt(analysis: Dict, actions: List[Dict], weight_adjustments: List[Dict]) -> str:
    """Build strategic prompt with structured output request."""
    now_str = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")
    yesterday = (datetime.now(HKT) - timedelta(days=1)).strftime("%Y-%m-%d")

    lines = [
        f"你是專業交易員的AI策略顧問。這是{yesterday}的交易統計摘要。",
        f"分析時間: {now_str}",
        "",
        "## 各Engine表現統計（7日數據）",
    ]

    for engine, s in analysis.items():
        if s.get("insufficient_data"):
            lines.append(f"\n### {engine}: 數據不足")
            continue
        lines.append(f"\n### {engine}")
        lines.append(f"- 交易: {s['closed']} closed ({s['open']} open)")
        lines.append(f"- 勝率: {s['win_rate']}% | P&L: ${s['total_pnl']:+.2f}")
        lines.append(f"- 均贏: ${s['avg_win']:+.2f} | 均輸: ${s['avg_loss']:+.2f}")
        lines.append(f"- SL率: {s['sl_rate']}% | TP率: {s['tp_rate']}% | TimeStop率: {s['time_stop_rate']}%")
        lines.append(f"- 平均持倉: {s['avg_hold_min']}分鐘")
        lines.append(f"- LONG: {s['long_trades']} trades ({s['long_win_rate']}%) | SHORT: {s['short_trades']} trades ({s['short_win_rate']}%)")
        if s.get("worst_hours"):
            worst = ", ".join(f"{h['hour']}:00(${h['pnl']:+.0f}/{h['trades']}t)" for h in s["worst_hours"])
            lines.append(f"- ⚠️ 最差時段: {worst}")

    if actions:
        lines.append("\n## 參數優化")
        for a in actions:
            if a.get("applied"):
                lines.append(f"- ✅ [{a['engine']}] {a['param']}: {a['current']} → {a['new']} — {a['reason']}")
            elif a.get("action") == "recommendation_only":
                lines.append(f"- 💡 [{a['engine']}] {a['param']}: {a['reason']} (需人手確認)")
            else:
                lines.append(f"- ❌ [{a['engine']}] {a['param']}: 未能套用")

    if weight_adjustments:
        lines.append("\n## 策略權重調整")
        for wa in weight_adjustments:
            lines.append(f"- ⚖️ [{wa['engine']}] {wa['old_weight']:.2f} → {wa['new_weight']:.2f} — {wa['reason']}")

    lines.append("""
## 請分析並以 JSON 格式回覆結構化行動建議

請回覆一個 JSON object，schema:
```json
{
  "market_regime": "trending|ranging|volatile|unknown",
  "confidence": "HIGH|MED|LOW",
  "actions": [
    {
      "type": "adjust_stop|adjust_tp|adjust_weight|adjust_trailing|adjust_timestop|adjust_risk|recommend",
      "target": "btc|eth|gold|indices|equities",
      "value": <number>,
      "reason": "簡短原因（繁體中文）"
    }
  ],
  "analysis": "簡短市場分析（繁體中文，唔好講廢話）"
}
```

請根據以上數據，分析：
1. 市場結構/regime
2. 邊個 engine 需要緊急調整參數
3. 資金配置建議（權重增減）
4. 風險警告
""")
    return "\n".join(lines)


def execute_gemini_actions(gemini_actions: List[Dict]) -> List[Dict]:
    """
    Execute Gemini-proposed actions.
    Maps type to actual parameter changes:
      - adjust_stop → STOP_LOSS_PCT
      - adjust_tp → TAKE_PROFIT_PCT
      - adjust_weight → _weight in strategy_config
      - adjust_trailing → TRAILING_ACTIVATION_PCT
      - adjust_timestop → TIME_STOP_MINUTES
      - adjust_risk → RISK_PER_TRADE_PCT
      - recommend → skip (advisory only)
    """
    executed = []
    # Base param map — STOP_LOSS is engine-specific
    base_param_map = {
        "adjust_tp": "TAKE_PROFIT_PCT",
        "adjust_trailing": "TRAILING_ACTIVATION_PCT",
        "adjust_timestop": "TIME_STOP_MINUTES",
        "adjust_risk": "RISK_PER_TRADE_PCT",
    }

    for action in gemini_actions:
        action_type = action.get("type", "")
        target = action.get("target", "")
        value = action.get("value")

        if action_type == "recommend":
            executed.append({
                "engine": target,
                "param": "recommendation",
                "value": value,
                "reason": action.get("reason", ""),
                "applied": False,
            })
            continue

        # Market-specific STOP_LOSS mapping
        if action_type == "adjust_stop":
            if target in ("btc", "eth"):
                param_name = "STOP_LOSS_ATR_MULT"  # ATR-based
            elif target == "gold":
                param_name = "STOP_LOSS_PCT"  # Fixed %
            else:
                param_name = "STOP_LOSS_PCT"  # default
        else:
            param_name = base_param_map.get(action_type)

        if action_type == "adjust_weight":
            # Update strategy_config.json _weight
            config = load_strategy_config()
            if target in config.get("engines", {}):
                old_weight = config["engines"][target].get("_weight", 1.0)
                config["engines"][target]["_weight"] = round(value, 3)
                save_strategy_config(config)
                executed.append({
                    "engine": target,
                    "param": "_weight",
                    "current": old_weight,
                    "new": value,
                    "reason": action.get("reason", "Gemini weight adjustment"),
                    "applied": True,
                })
            continue

        if param_name is None or target not in ENGINE_PARAMS:
            executed.append({
                "engine": target,
                "param": action_type,
                "value": value,
                "reason": f"Unknown type/target: {action_type}/{target}",
                "applied": False,
            })
            continue

        # Apply parameter change
        params_cfg = ENGINE_PARAMS[target]["params"]
        if param_name not in params_cfg:
            executed.append({
                "engine": target,
                "param": param_name,
                "value": value,
                "reason": f"Param {param_name} not in ENGINE_PARAMS for {target}",
                "applied": False,
            })
            continue

        # Clamp value to valid range
        pmin = params_cfg[param_name]["min"]
        pmax = params_cfg[param_name]["max"]
        clamped = max(pmin, min(pmax, value))

        file_path = os.path.join(ENG_DIR, ENGINE_PARAMS[target]["file"])
        current_val = read_param_from_file(file_path, param_name, params_cfg[param_name]["default"])

        if clamped != current_val:
            success = apply_param_change(file_path, param_name, current_val, clamped)
            executed.append({
                "engine": target,
                "param": param_name,
                "current": current_val,
                "new": clamped,
                "reason": action.get("reason", ""),
                "applied": success,
            })
        else:
            executed.append({
                "engine": target,
                "param": param_name,
                "current": current_val,
                "new": clamped,
                "reason": f"No change needed (already {clamped})",
                "applied": False,
            })

    return executed


# ═══════════════════════════════════════════════════════════════
# PHASE 5: System Integrity Check (v2 new)
# ═══════════════════════════════════════════════════════════════

def system_integrity_check() -> Dict:
    """
    Scan all engine files and verify ENGINE_PARAMS mapping is correct.
    Detect dead params (params in ENGINE_PARAMS that don't exist in files).
    Auto-correct ENGINE_PARAMS if dead param detected.

    Returns report dict with:
      - dead_params: list of params in ENGINE_PARAMS not found in source files
      - missing_params: list of params in source files not in ENGINE_PARAMS
      - auto_fixes: list of corrections applied
    """
    report = {
        "timestamp": datetime.now(HKT).isoformat(),
        "dead_params": [],
        "missing_params": [],
        "auto_fixes": [],
        "issues_found": 0,
        "issues_fixed": 0,
    }

    for engine, cfg in ENGINE_PARAMS.items():
        file_path = os.path.join(ENG_DIR, cfg["file"])
        if not os.path.exists(file_path):
            report["dead_params"].append({
                "engine": engine,
                "issue": f"File not found: {cfg['file']}",
            })
            report["issues_found"] += 1
            continue

        with open(file_path) as f:
            content = f.read()

        # Also check engine_base.py for inherited params
        base_file = os.path.join(ENG_DIR, "engine_base.py")
        base_content = ""
        if os.path.exists(base_file) and file_path != base_file:
            with open(base_file) as f:
                base_content = f.read()

        for param_name, param_cfg in cfg["params"].items():
            # Check if param exists in source files
            pat1 = rf'{param_name}\s*:\s*\w+\s*=\s*-?[\d.]+'
            pat2 = rf'{param_name}\s*[:=]\s*-?[\d.]+'
            in_file = bool(re.search(pat1, content) or re.search(pat2, content))
            in_base = bool(re.search(pat1, base_content) or re.search(pat2, base_content))

            if not in_file and not in_base:
                report["dead_params"].append({
                    "engine": engine,
                    "param": param_name,
                    "file": cfg["file"],
                    "default": param_cfg.get("default"),
                })
                report["issues_found"] += 1

                # Auto-fix: correct the param name by searching for the closest match
                fix = auto_fix_param_name(engine, param_name, content, base_content)
                if fix:
                    report["auto_fixes"].append(fix)
                    report["issues_fixed"] += 1

        # Check for params in file that should be in ENGINE_PARAMS but aren't
        # Find class-level float/int params (including negative values)
        for m in re.finditer(r'^\s+([A-Z][A-Z_0-9]+)\s*(?::\s*\w+\s*)?=\s*(-?[\d.]+)', content, re.MULTILINE):
            var_name = m.group(1)
            val = m.group(2)
            # Skip non-param vars
            if var_name in ("WORKSPACE", "ENG_DIR", "LOG_DIR", "REVIEW_DIR", "CHANGELOG_FILE",
                            "MARKET", "EPIC", "ACCOUNT_KEY", "OI_STATE_FILE",
                            "GEMINI_API_URL", "BINANCE_API",
                            "MARKET_CONFIGS", "VIX_RISK_ON", "VIX_RISK_OFF",
                            "RATIO_UP_THRESHOLD", "RATIO_DOWN_THRESHOLD",
                            "SPREAD_TIGHT", "SPREAD_WIDE",
                            "HL_API", "BASE_URL", "MARKET_DATA_URL",
                            "MAX_POSITIONS", "MAX_EXPOSURE_PCT", "RISK_PER_TRADE_PCT",
                            "ATR_PERIOD", "STOP_LOSS_ATR_MULT"):
                continue
            if var_name not in cfg["params"] and var_name not in (
                "STOP_LOSS_ATR_MULT", "ATR_PERIOD", "RISK_PER_TRADE_PCT",
                "MAX_POSITIONS", "MAX_EXPOSURE_PCT", "VIX_LOW_THRESHOLD",
                "FUNDING_LONG_BIAS", "FUNDING_SHORT_BIAS",
                "_comment", "_note",
            ):
                report["missing_params"].append({
                    "engine": engine,
                    "param": var_name,
                    "value": val,
                    "file": cfg["file"],
                })

    # Save integrity report
    os.makedirs(os.path.dirname(INTEGRITY_LOG), exist_ok=True)
    with open(INTEGRITY_LOG, "w") as f:
        json.dump(report, f, indent=2, default=str)

    return report


def auto_fix_param_name(engine: str, dead_param: str, file_content: str, base_content: str) -> Optional[Dict]:
    """
    Try to auto-correct a dead param name by finding the closest match
    in the source files. If found, update ENGINE_PARAMS in-memory.
    """
    # Common known misnames from v1
    known_fixes = {
        "OI_DELTA_LONG": "OI_DELTA_LONG_THRESHOLD",
        "OI_DELTA_SHORT": "OI_DELTA_SHORT_THRESHOLD",
        "SPX_STOP_PCT": None,  # Not a class attr — in MARKET_CONFIGS dict
        "NDX_STOP_PCT": None,  # Not a class attr — in MARKET_CONFIGS dict
    }

    fixed_name = known_fixes.get(dead_param)
    if fixed_name:
        # Verify the fixed name actually exists in the file
        combined = file_content + "\n" + base_content
        pat = rf'{fixed_name}\s*[:=]\s*[\d.]+'
        if re.search(pat, combined):
            return {
                "engine": engine,
                "dead_param": dead_param,
                "auto_fixed_to": fixed_name,
                "action": "updated_ENGINE_PARAMS",
                "note": f"Auto-corrected {dead_param} → {fixed_name} in ENGINE_PARAMS mapping",
            }
        else:
            return {
                "engine": engine,
                "dead_param": dead_param,
                "auto_fixed_to": None,
                "action": "removed_from_ENGINE_PARAMS",
                "note": f"{dead_param} not found in source files either — removing from ENGINE_PARAMS",
            }

    # Generic fallback: try to find any param in file that contains the dead_param as substring
    combined = file_content + "\n" + base_content
    for m in re.finditer(r'^\s+([A-Z][A-Z_0-9]+)\s*(?::\s*\w+\s*)?=\s*(-?[\d.]+)', combined, re.MULTILINE):
        var = m.group(1)
        if dead_param in var or var in dead_param:
            return {
                "engine": engine,
                "dead_param": dead_param,
                "auto_fixed_to": var,
                "action": "suggested_fix",
                "note": f"Found similar param '{var}' — may need manual verification",
            }

    return None


def apply_integrity_fixes(report: Dict) -> int:
    """
    Apply auto-fixes from integrity check to ENGINE_PARAMS.
    This modifies ENGINE_PARAMS in memory (the global dict).
    Returns number of fixes applied.
    """
    fixes_applied = 0
    for fix in report.get("auto_fixes", []):
        engine = fix["engine"]
        dead_param = fix["dead_param"]
        fixed_name = fix.get("auto_fixed_to")
        action = fix.get("action")

        if engine not in ENGINE_PARAMS:
            continue

        if action == "updated_ENGINE_PARAMS" and fixed_name:
            # Copy the config from the dead param to the correct name
            if dead_param in ENGINE_PARAMS[engine]["params"]:
                ENGINE_PARAMS[engine]["params"][fixed_name] = ENGINE_PARAMS[engine]["params"].pop(dead_param)
                fixes_applied += 1
                print(f"  🔧 Auto-fix: ENGINE_PARAMS[{engine}] {dead_param} → {fixed_name}")
            else:
                print(f"  ⚠️ Can't fix: {engine}.{dead_param} not found in ENGINE_PARAMS")

        elif action == "removed_from_ENGINE_PARAMS":
            if dead_param in ENGINE_PARAMS[engine]["params"]:
                del ENGINE_PARAMS[engine]["params"][dead_param]
                fixes_applied += 1
                print(f"  🔧 Auto-fix: Removed dead param {dead_param} from ENGINE_PARAMS[{engine}]")

    return fixes_applied


# ═══════════════════════════════════════════════════════════════
# PHASE 6: Changelog
# ═══════════════════════════════════════════════════════════════

def load_changelog() -> List[Dict]:
    try:
        with open(CHANGELOG_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_changelog(actions: List[Dict], analysis: Dict, weight_adjustments: List[Dict] = None,
                   gemini_actions: List[Dict] = None, integrity_report: Dict = None):
    """Append today's changes to the changelog."""
    log = load_changelog()
    entry = {
        "timestamp": datetime.now(HKT).isoformat(),
        "date": datetime.now(HKT).strftime("%Y-%m-%d"),
        "actions": actions,
        "weight_adjustments": weight_adjustments or [],
        "gemini_actions": gemini_actions or [],
        "integrity_check": integrity_report or {},
        "version": "v2",
        "analysis_summary": {
            eng: {
                "win_rate": s.get("win_rate"),
                "total_pnl": s.get("total_pnl"),
                "closed": s.get("closed"),
                "sl_rate": s.get("sl_rate"),
                "tp_rate": s.get("tp_rate"),
            }
            for eng, s in analysis.items()
        },
    }
    log.append(entry)
    tmp = CHANGELOG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(log, f, indent=2, default=str)
    os.replace(tmp, CHANGELOG_FILE)
    print(f"\n📝 Changelog updated: {CHANGELOG_FILE} ({len(log)} entries)")


# ═══════════════════════════════════════════════════════════════
# OUTPUT
# ═══════════════════════════════════════════════════════════════

def save_report(content: str, analysis: Dict, actions: List[Dict],
                weight_adjustments: List[Dict] = None,
                gemini_actions: List[Dict] = None,
                integrity_report: Dict = None):
    """Save daily review + optimization report (atomic writes)."""
    now = datetime.now(HKT)
    fname = now.strftime("daily_review_v2_%Y%m%d")
    txt_path = os.path.join(REVIEW_DIR, f"{fname}.txt")
    json_path = os.path.join(REVIEW_DIR, f"{fname}.json")

    tmp_txt = txt_path + ".tmp"
    with open(tmp_txt, "w") as f:
        f.write(content)
    os.replace(tmp_txt, txt_path)

    tmp_json = json_path + ".tmp"
    with open(tmp_json, "w") as f:
        json.dump({
            "timestamp": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "version": "v2",
            "analysis": analysis,
            "optimizations": actions,
            "weight_adjustments": weight_adjustments or [],
            "gemini_actions": gemini_actions or [],
            "integrity_check": integrity_report or {},
            "report": content,
        }, f, indent=2, default=str)
    os.replace(tmp_json, json_path)

    print(f"\n💾 Report saved: {txt_path}")
    return txt_path


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print(f"🔧 每日自動優化引擎 v2 — {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 70)

    # ─── Phase 0: System Integrity Check ───
    print("\n🔍 Phase 0: System Integrity Check...")
    integrity_report = system_integrity_check()
    print(f"   Dead params: {len(integrity_report['dead_params'])}")
    print(f"   Missing params (candidates): {len(integrity_report['missing_params'])}")
    if integrity_report["auto_fixes"]:
        print(f"   Auto-fixes: {len(integrity_report['auto_fixes'])}")
        for fix in integrity_report["auto_fixes"]:
            print(f"     🔧 [{fix['engine']}] {fix['dead_param']} → {fix.get('auto_fixed_to', 'REMOVED')}: {fix['note']}")
        fixes_applied = apply_integrity_fixes(integrity_report)
        print(f"   ✅ {fixes_applied} auto-fixes applied to ENGINE_PARAMS")
    else:
        print("   ✅ No dead params found — ENGINE_PARAMS mapping is correct")

    # ─── Phase 1: 統計分析 ───
    print("\n📊 Phase 1: 統計分析 (7日交易數據)...")
    analysis = analyze_all()

    total_closed = sum(s.get("closed", 0) for s in analysis.values())
    total_pnl = sum(s.get("total_pnl", 0) for s in analysis.values())
    engines_with_data = sum(1 for s in analysis.values() if not s.get("insufficient_data"))

    print(f"   Engines with data: {engines_with_data}/{len(analysis)}")
    print(f"   Total closed trades (7d): {total_closed}")
    print(f"   Total P&L (7d): ${total_pnl:+.2f}")

    for engine, s in analysis.items():
        if s.get("insufficient_data"):
            print(f"   [{engine:8s}] ⚠️ 數據不足 ({s.get('closed',0)} trades)")
        else:
            print(f"   [{engine:8s}] WR={s['win_rate']}% P&L=${s['total_pnl']:+.2f} "
                  f"SL={s['sl_rate']}% TP={s['tp_rate']}% Hold={s['avg_hold_min']}m")

    # ─── Phase 2: 參數優化 ───
    print("\n🔍 Phase 2: 參數優化分析...")
    actions = compute_optimizations(analysis)

    if not actions:
        print("   ✅ 無需優化 — 所有參數在合理範圍內")
    else:
        print(f"   📋 發現 {len(actions)} 個優化機會:")
        for a in actions:
            if a.get("action") == "recommendation_only":
                print(f"   💡 [{a['engine']}] {a['param']}: {a['reason']}")
            else:
                print(f"   🔧 [{a['engine']}] {a['param']}: {a['current']} → {a['new']} — {a['reason']}")

    # ─── Phase 2b: Auto-Apply ───
    executable = [a for a in actions if a.get("action") != "recommendation_only"]
    if executable:
        print(f"\n⚡ Applying {len(executable)} parameter changes...")
        applied = apply_optimizations(executable)
        success_count = sum(1 for a in applied if a.get("applied"))
        fail_count = len(applied) - success_count
        print(f"   ✅ {success_count} applied, ❌ {fail_count} failed")
    else:
        print("\n⚡ No parameter changes to apply")
        applied = []

    # ─── Phase 3: Strategy Weight Auto-Adjust ───
    print("\n⚖️ Phase 3: Strategy Weight Auto-Adjust...")
    weight_adjustments = compute_strategy_weight_adjustments(analysis)
    if weight_adjustments:
        print(f"   📋 {len(weight_adjustments)} weight adjustments:")
        for wa in weight_adjustments:
            print(f"     ⚖️ [{wa['engine']}] {wa['old_weight']:.2f} → {wa['new_weight']:.2f} ({wa['reason']})")
    else:
        print("   ✅ No weight adjustments needed")

    # ─── Save changelog ───
    save_changelog(applied, analysis, weight_adjustments=weight_adjustments,
                   integrity_report=integrity_report)

    # ─── Phase 4: Gemini 戰略分析 + 結構化輸出 ───
    gemini_actions = []
    if total_closed >= 2:
        print("\n🤖 Phase 4: Gemini 3.1 Pro 戰略分析 + 結構化輸出...")
        prompt = build_strategic_prompt(analysis, applied, weight_adjustments)
        strategic_analysis = ask_gemini(prompt, max_tokens=8192)

        print(f"\n   🧠 Gemini Response (first 500 chars):")
        print(f"   {strategic_analysis[:500]}...")

        # Parse structured actions from Gemini
        parsed_actions = parse_gemini_actions(strategic_analysis)
        if parsed_actions:
            print(f"\n   📋 Parsed {len(parsed_actions)} structured actions from Gemini:")
            for pa in parsed_actions:
                print(f"     [{pa['type']}] {pa['target']} → {pa.get('value', '?')}: {pa.get('reason', '')[:60]}")
            gemini_actions = execute_gemini_actions(parsed_actions)
            print(f"\n   ✅ {sum(1 for g in gemini_actions if g.get('applied'))} Gemini actions executed")
        else:
            print("   ⚠️ No structured actions parsed from Gemini response")
    else:
        strategic_analysis = "數據不足（少過 2 個 closed trades），跳過 Gemini 分析。"

    # ─── Build final report ───
    now_str = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")
    opt_summary = ""
    if applied:
        opt_summary = "\n## ⚡ 今日參數優化\n"
        for a in applied:
            icon = "✅" if a.get("applied") else ("💡" if a.get("action") == "recommendation_only" else "❌")
            opt_summary += f"- {icon} [{a['engine']}] `{a['param']}`: {a['current']} → {a['new']}\n"
            opt_summary += f"  _{a['reason']}_\n"

    weight_summary = ""
    if weight_adjustments:
        weight_summary = "\n## ⚖️ 策略權重調整\n"
        for wa in weight_adjustments:
            weight_summary += f"- [{wa['engine']}] {wa['old_weight']:.2f} → {wa['new_weight']:.2f} — {wa['reason']}\n"

    integrity_summary = ""
    if integrity_report and integrity_report.get("auto_fixes"):
        integrity_summary = "\n## 🔧 System Integrity Fixes\n"
        for fix in integrity_report["auto_fixes"]:
            integrity_summary += f"- [{fix['engine']}] {fix['dead_param']} → {fix.get('auto_fixed_to', 'REMOVED')}: {fix['note']}\n"

    gemini_summary = ""
    if gemini_actions:
        gemini_summary = "\n## 🤖 Gemini 結構化行動\n"
        for ga in gemini_actions:
            icon = "✅" if ga.get("applied") else "ℹ️"
            gemini_summary += f"- {icon} [{ga['engine']}] `{ga['param']}`: {ga.get('current', '?')} → {ga.get('new', ga.get('value', '?'))}\n"
            gemini_summary += f"  _{ga.get('reason', '')}_\n"

    full_report = f"""# 🔧 每日自動優化引擎 v2 報告 — {now_str}

## 📊 交易統計 (7日)
- Engines: {engines_with_data} | Closed: {total_closed} | P&L: ${total_pnl:+.2f}
{integrity_summary}{opt_summary}{weight_summary}{gemini_summary}
---
## 🧠 Gemini 3.1 Pro 戰略分析

{strategic_analysis}

---
_參數改動已直接套用到 engine source files。Changelog: {CHANGELOG_FILE}_
"""

    print(f"\n{full_report}")
    report_path = save_report(full_report, analysis, applied,
                              weight_adjustments=weight_adjustments,
                              gemini_actions=gemini_actions,
                              integrity_report=integrity_report)
    print("\n✅ 每日自動優化 v2 完成")

    # ── TG通知：每日 summary ──
    try:
        from tg_notify import tg_daily_summary
        tg_daily_summary(report_path)
    except Exception as e:
        print(f"  ⚠️ TG daily summary failed: {e}")


if __name__ == "__main__":
    main()
