#!/usr/bin/env python3
"""
🛡️ SYSTEM GUARDIAN — Hourly Health Check + Dead Param Scan + Auto-Fix
=====================================================================
Runs via cron every hour. Detects and auto-fixes issues in the trading system.

Checks:
  1. Import test — verify all engines, capitalcom_paper, flow_monitor, etc.
  2. API health — ping Hyperliquid, Capital.com, Gemini proxy
  3. Dead param scan — ENGINE_PARAMS vs actual variable usage in engine files
  4. Trade log consistency — open trades in JSON vs capital.com API
  5. Cron log check — cron_entry.py ran in last 10 minutes
  6. Disk space — warn if < 5 GB free
  7. Memory — warn if > 80% used

Auto-fix:
  - Dead param → auto-correct ENGINE_PARAMS mapping in daily_review_v2.py
  - Import broken → attempt pip install / sys.path fix
  - Trade sync mismatch → force reconcile

Output: health JSON to ~/workspace/data/health_reports/
TG notify only if broken status detected.
"""

import json
import os
import sys
import time
import shutil
import subprocess
import urllib.request
import urllib.error
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

# ── Paths ───────────────────────────────────────────────────────────────
HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENG_DIR = os.path.join(WORKSPACE, "scalp_engines")
LOG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
HEALTH_DIR = os.path.join(WORKSPACE, "data", "health_reports")
CAPITALCOM_DIR = os.path.join(WORKSPACE, "capitalcom")
COLLECTORS_DIR = os.path.join(WORKSPACE, "collectors")
CRON_LOG = os.path.join(WORKSPACE, "cron.log")
VENV_PYTHON = os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python3")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
HL_API = "https://api.hyperliquid.xyz/info"
os.makedirs(HEALTH_DIR, exist_ok=True)

# Add workspace paths to sys.path for import tests
for p in [WORKSPACE, ENG_DIR, CAPITALCOM_DIR, COLLECTORS_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── ENGINE_PARAMS reference from daily_review_v2.py ────────────────────
# This is the canonical mapping of engine → params to optimize
ENGINE_PARAMS = {
    "btc": {
        "file": "btc_swing.py",
        "params": [
            "STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "TRAILING_ACTIVATION_PCT",
            "TRAILING_DISTANCE_PCT", "TIME_STOP_MINUTES",
            "OI_DELTA_LONG_THRESHOLD", "OI_DELTA_SHORT_THRESHOLD",
            "STOP_LOSS_ATR_MULT", "ATR_PERIOD",
        ]
    },
    "eth": {
        "file": "eth_swing.py",
        "params": [
            "STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "TRAILING_ACTIVATION_PCT",
            "TRAILING_DISTANCE_PCT", "TIME_STOP_MINUTES",
            "STOP_LOSS_ATR_MULT", "ATR_PERIOD",
        ]
    },
    "gold": {
        "file": "gold_scalp.py",
        "params": [
            "STOP_LOSS_PCT", "TAKE_PROFIT_PCT",
            "VIX_HIGH_THRESHOLD", "VIX_LOW_THRESHOLD",
            "MAX_CONSEC_LOSSES",
        ]
    },
    "indices": {
        "file": "indices_scalp.py",
        "params": [
            "MAX_CONSEC_LOSSES",
        ]
    },
    "equities": {
        "file": "equities_swing.py",
        "params": [
            "STOP_LOSS_PCT", "TAKE_PROFIT_PCT",
            "MAX_POSITIONS_PER_SECTOR",
        ]
    },
}

# Engines and modules to verify during import test
IMPORT_CHECKLIST = {
    "engines": [
        ("btc_swing", "BtcSwingEngine"),
        ("eth_swing", "EthSwingEngine"),
        ("gold_scalp", "GoldScalpEngine"),
        ("indices_scalp", "IndicesScalpEngine"),
        ("equities_swing", "EquitiesSwingEngine"),
    ],
    "modules": [
        "engine_base",
        "engine_coordinator",
        "tg_notify",
        "cron_entry",
    ],
    "external": [
        ("capitalcom.capitalcom_paper", None),
        ("collectors.flow_monitor", None),
    ],
}


# =======================================================================
#  CHECK 1: IMPORT TEST
# =======================================================================
def check_imports() -> Dict:
    """Verify all engines and critical modules can be imported."""
    results = {"status": "ok", "failed_imports": [], "details": []}

    # Test basic Python stdlib
    stdlib_ok = all((
        __import__("json"),
        __import__("os"),
        __import__("re"),
        __import__("time"),
        __import__("datetime"),
    ))
    if not stdlib_ok:
        results["failed_imports"].append("stdlib")
        results["status"] = "broken"

    # Test engine modules
    for module_name, class_name in IMPORT_CHECKLIST["engines"]:
        try:
            mod = __import__(module_name)
            if class_name and not hasattr(mod, class_name):
                results["failed_imports"].append(f"{module_name}.{class_name}")
            else:
                results["details"].append(f"✅ {module_name}.{class_name}")
        except ImportError as e:
            results["failed_imports"].append(f"{module_name}: {e}")
            results["details"].append(f"❌ {module_name}: {e}")

    for module_name in IMPORT_CHECKLIST["modules"]:
        try:
            __import__(module_name)
            results["details"].append(f"✅ {module_name}")
        except ImportError as e:
            results["failed_imports"].append(f"{module_name}: {e}")
            results["details"].append(f"❌ {module_name}: {e}")

    for module_path, _ in IMPORT_CHECKLIST["external"]:
        try:
            __import__(module_path)
            results["details"].append(f"✅ {module_path}")
        except ImportError as e:
            results["failed_imports"].append(f"{module_path}: {e}")
            results["details"].append(f"❌ {module_path}: {e}")

    if results["failed_imports"]:
        results["status"] = "broken"

    return results


# =======================================================================
#  CHECK 2: API HEALTH
# =======================================================================
def check_api_health() -> Dict:
    """Ping Hyperliquid, Capital.com, and Gemini proxy."""
    results = {
        "hyperliquid": "unknown",
        "capitalcom": "unknown",
        "gemini_proxy": "unknown",
        "details": {},
    }

    # --- Hyperliquid ---
    try:
        payload = json.dumps({"type": "allMids"}).encode()
        req = urllib.request.Request(
            HL_API, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if isinstance(data, dict) and len(data) > 0:
                results["hyperliquid"] = "ok"
                results["details"]["hyperliquid"] = f"✅ {len(data)} mids"
            else:
                results["hyperliquid"] = "degraded"
                results["details"]["hyperliquid"] = "⚠️ empty response"
    except Exception as e:
        results["hyperliquid"] = "dead"
        results["details"]["hyperliquid"] = f"❌ {str(e)[:80]}"

    # --- Capital.com ---
    try:
        from capitalcom.capitalcom_paper import _auth, get_account_balance
        _auth()
        bal = get_account_balance("321895128687259934")
        if bal and bal.get("balance", 0) > 0:
            results["capitalcom"] = "ok"
            results["details"]["capitalcom"] = f"✅ balance=${bal['balance']:.2f}"
        elif bal:
            results["capitalcom"] = "degraded"
            results["details"]["capitalcom"] = "⚠️ balance=0"
        else:
            results["capitalcom"] = "dead"
            results["details"]["capitalcom"] = "❌ no balance response"
    except ImportError:
        try:
            from capitalcom_paper import _auth, get_account_balance
            _auth()
            bal = get_account_balance("321895128687259934")
            if bal and bal.get("balance", 0) > 0:
                results["capitalcom"] = "ok"
                results["details"]["capitalcom"] = f"✅ balance=${bal['balance']:.2f}"
            else:
                results["capitalcom"] = "dead"
                results["details"]["capitalcom"] = "❌ no balance"
        except Exception as e:
            results["capitalcom"] = "dead"
            results["details"]["capitalcom"] = f"❌ {str(e)[:80]}"
    except Exception as e:
        results["capitalcom"] = "dead"
        results["details"]["capitalcom"] = f"❌ {str(e)[:80]}"

    # --- Gemini API (direct, no proxy) ---
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.expanduser("~/.hermes/.env"))
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        
        url = f"{GEMINI_API_URL}?key={api_key}"
        payload = json.dumps({
            "contents": [{"parts": [{"text": "ping"}]}],
            "generationConfig": {"maxOutputTokens": 10, "thinkingConfig": {"thinkingBudget": 0}},
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        # 🔧 Retry once with longer timeout to avoid false alarms
        gemini_ok = False
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                    if data.get("candidates"):
                        gemini_ok = True
                        break
            except Exception:
                if attempt == 0:
                    time.sleep(2)  # Brief pause before retry
        
        if gemini_ok:
            results["gemini_api"] = "ok"
            results["details"]["gemini_api"] = "✅ responding"
        else:
            results["gemini_api"] = "dead"
            results["details"]["gemini_api"] = "❌ no response after retry"
    except Exception as e:
        results["gemini_api"] = "dead"
        results["details"]["gemini_api"] = f"❌ {str(e)[:80]}"

    return results


# =======================================================================
#  CHECK 3: DEAD PARAM SCAN
# =======================================================================
def _read_engine_file(filepath: str, engine_name: str) -> Tuple[str, str]:
    """Read engine source file content. Falls back to engine_base.py for inherited params."""
    content = ""
    base_content = ""

    if os.path.exists(filepath):
        with open(filepath) as f:
            content = f.read()

    base_path = os.path.join(ENG_DIR, "engine_base.py")
    if os.path.exists(base_path):
        with open(base_path) as f:
            base_content = f.read()

    return content, base_content


def _param_exists_in_file(param_name: str, content: str) -> bool:
    """Check if a param name appears as a class variable assignment in the file."""
    import re
    # Match patterns like: PARAM_NAME = value  or  PARAM_NAME: type = value
    patterns = [
        rf'\b{param_name}\s*[:=]\s*',           # PARAM_NAME: type =  or PARAM_NAME = 
        rf'self\.{param_name}\b',                  # self.PARAM_NAME
        rf'\"{param_name}\"',                      # "PARAM_NAME" in string (mapping)
    ]
    for pat in patterns:
        if re.search(pat, content):
            return True
    return False


def scan_dead_params() -> Dict:
    """
    Compare ENGINE_PARAMS against actual variable usage in engine files.
    Returns list of mismatches: dead params (in config but not in code) and
    missing params (in code but not in config).
    """
    results = {
        "status": "ok",
        "dead_params": [],      # in ENGINE_PARAMS but not found in engine file
        "missing_params": [],   # in engine file but not in ENGINE_PARAMS
        "mismatches": [],       # combined for auto-fix
    }
    import re

    for engine_name, cfg in ENGINE_PARAMS.items():
        filepath = os.path.join(ENG_DIR, cfg["file"])
        if not os.path.exists(filepath):
            results["dead_params"].append({
                "engine": engine_name, "file": cfg["file"],
                "issue": "file_not_found",
            })
            continue

        content, base_content = _read_engine_file(filepath, engine_name)
        combined = content + "\n" + base_content

        # Check each configured param
        for param in cfg["params"]:
            if not _param_exists_in_file(param, combined):
                results["dead_params"].append({
                    "engine": engine_name,
                    "file": cfg["file"],
                    "param": param,
                    "issue": "dead_param_not_in_source",
                })
                results["mismatches"].append({
                    "engine": engine_name,
                    "file": cfg["file"],
                    "param": param,
                    "type": "dead_param",
                })

        # Find class-level variables in the engine file that might be missing from ENGINE_PARAMS
        # Look for lines like:    PARAM_NAME = value  or  PARAM_NAME: type = value
        class_vars = re.findall(
            r'^\s{4}([A-Z][A-Z_0-9]+)\s*[:=]\s*(?:float|int|str|bool)?\s*=',
            content, re.MULTILINE
        )
        # Also find type-annotated:    PARAM_NAME: float = value
        class_vars += re.findall(
            r'^\s{4}([A-Z][A-Z_0-9]+)\s*:\s*\w+\s*=',
            content, re.MULTILINE
        )

        # Filter out known base class params and non-optimizable params
        known_base = {
            "MARKET", "EPIC", "INITIAL_BALANCE", "ACCOUNT_KEY",
            "MAX_POSITIONS", "MAX_EXPOSURE_PCT", "RISK_PER_TRADE_PCT",
            "SLIPPAGE_PCT", "SLIPPAGE_MAP",
        }
        for var in set(class_vars):
            if var in known_base:
                continue
            if var not in cfg["params"]:
                # Check if it's actually an engine-specific tunable param
                if var.endswith("_THRESHOLD") or var.endswith("_PCT") or \
                   var.endswith("_MULT") or var.endswith("_PERIOD") or \
                   var.startswith("MAX_") or var.startswith("MIN_") or \
                   var.startswith("FUNDING_"):
                    results["missing_params"].append({
                        "engine": engine_name,
                        "file": cfg["file"],
                        "param": var,
                        "issue": "missing_from_engine_params",
                    })
                    results["mismatches"].append({
                        "engine": engine_name,
                        "file": cfg["file"],
                        "param": var,
                        "type": "missing_param",
                    })

    if results["dead_params"] or results["missing_params"]:
        results["status"] = "mismatch"

    return results


# =======================================================================
#  CHECK 4: TRADE LOG CONSISTENCY
# =======================================================================
def check_trade_consistency() -> Dict:
    """
    Count open trades in *_trades.json vs open positions via Capital.com API.
    """
    results = {
        "status": "ok",
        "engines": {},
        "discrepancies": [],
        "open_in_json": 0,
        "open_in_api": 0,
    }

    # Count open trades from JSON files
    # 🔧 FIX: Always count from paper_trades.json FIRST (single source of truth).
    # Engine-specific files get overwritten by 5-min engine scan → stale.
    # Then merge engine files, dedup by deal_id (and epic+direction for deal_id="?").
    json_open = {}
    json_open_count = 0
    seen_deal_ids = set()
    seen_epic_dirs = set()
    all_open_trades = []
    
    # Primary source: paper_trades.json (consolidated truth, includes reconciled entries)
    files_ordered = ["paper_trades.json"] if os.path.exists(os.path.join(LOG_DIR, "paper_trades.json")) else []
    # Secondary: engine-specific files (may be overwritten by scan, but catch anything missing)
    files_ordered += [f for f in os.listdir(LOG_DIR) if f.endswith("_trades.json") and f != "paper_trades.json"]
    
    for fname in files_ordered:
        path = os.path.join(LOG_DIR, fname)
        try:
            with open(path) as f:
                trades = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for t in trades:
            if t.get("status") != "open":
                continue
            if t.get("paper_only"):
                continue  # ⚠️ Skip paper_only — 唔 match Capital.com API
            
            deal_id = t.get("deal_id", "")
            epic = t.get("epic", "")
            direction = t.get("direction", "")
            key = (epic, direction)
            
            if deal_id and deal_id != "?" and deal_id != "paper_only":
                if deal_id in seen_deal_ids:
                    continue  # Already counted from higher-priority source
                seen_deal_ids.add(deal_id)
                seen_epic_dirs.add(key)
            else:
                # If no valid deal_id, dedup by epic+direction
                if key in seen_epic_dirs:
                    continue
                seen_epic_dirs.add(key)
                
            all_open_trades.append(t)
    
    json_open_count = len(all_open_trades)
    results["open_in_json"] = json_open_count

    # Count open positions from Capital.com API — ALL accounts
    try:
        sys.path.insert(0, CAPITALCOM_DIR)
        sys.path.insert(0, WORKSPACE)
        from capitalcom_paper import _auth, ACCOUNTS, _headers, DEMO_URL
        import requests as _requests
        _auth()

        api_positions = []
        api_by_account = {}
        for acct_name, acct_id in ACCOUNTS.items():
            try:
                h = _headers(acct_id)
                r = _requests.get(f'{DEMO_URL}/api/v1/positions', headers=h, timeout=10)
                if r.status_code == 200:
                    positions = r.json().get('positions', [])
                    api_positions.extend(positions)
                    if positions:
                        api_by_account[acct_name] = len(positions)
            except Exception:
                continue
        api_open_count = len(api_positions)

        results["open_in_api"] = api_open_count
        results["engines"]["api"] = api_by_account

        # Compare: if JSON says open > 0 but API says 0 (or vice versa) → mismatch
        if json_open_count != api_open_count:
            results["status"] = "mismatch"
            results["discrepancies"].append(
                f"JSON has {json_open_count} open trades, "
                f"API has {api_open_count} open positions"
            )

    except ImportError:
        try:
            from capitalcom_paper import check_positions, _auth
            _auth()
            api_positions = check_positions()
            api_open_count = len(api_positions) if api_positions else 0
            results["open_in_api"] = api_open_count
            if json_open_count != api_open_count:
                results["status"] = "mismatch"
                results["discrepancies"].append(
                    f"JSON has {json_open_count} open trades, "
                    f"API has {api_open_count} open positions"
                )
        except Exception as e:
            results["status"] = "api_error"
            results["discrepancies"].append(f"API call failed: {str(e)[:80]}")
    except Exception as e:
        results["status"] = "api_error"
        results["discrepancies"].append(f"API call failed: {str(e)[:80]}")

    return results


# =======================================================================
#  CHECK 5: CRON LOG FRESHNESS
# =======================================================================
def check_cron_log() -> Dict:
    """
    Check if cron_entry.py ran recently.
    Reads cron.log modification time. Falls back to cron.log.old if rotated.
    """
    results = {"status": "ok", "last_run": None, "minutes_since_last": None}

    # Check both current and rotated log
    log_to_check = CRON_LOG
    if not os.path.exists(CRON_LOG) or os.path.getsize(CRON_LOG) < 100:
        old_log = CRON_LOG + ".old"
        if os.path.exists(old_log) and os.path.getsize(old_log) > 100:
            log_to_check = old_log

    if not os.path.exists(log_to_check):
        results["status"] = "stale"
        results["last_run"] = "never"
        return results

    try:
        mtime = os.path.getmtime(log_to_check)
        now = time.time()
        minutes_ago = (now - mtime) / 60
        results["minutes_since_last"] = round(minutes_ago, 1)

        last_modified = datetime.fromtimestamp(mtime, HKT).isoformat()
        results["last_run"] = last_modified

        if minutes_ago > 25:
            results["status"] = "stale"
        elif minutes_ago > 15:
            results["status"] = "warning"
        else:
            results["status"] = "ok"

        # Also check the last line for a CRON: pattern
        try:
            with open(log_to_check, "rb") as f:
                f.seek(-500, os.SEEK_END)
                tail = f.read().decode("utf-8", errors="replace")
                for line in reversed(tail.split("\n")):
                    line = line.strip()
                    if line and ("CRON" in line or "FULL SCAN" in line or "cron" in line.lower()):
                        results["last_cron_line"] = line[:120]
                        break
        except (OSError, ValueError):
            pass

    except Exception as e:
        results["status"] = "error"
        results["error"] = str(e)[:80]

    return results


# =======================================================================
#  CHECK 6: DISK SPACE
# =======================================================================
def check_disk_space() -> Dict:
    """Check free disk space, warn if < 5 GB."""
    results = {"status": "ok", "free_gb": None, "total_gb": None}

    try:
        usage = shutil.disk_usage(WORKSPACE)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        results["free_gb"] = round(free_gb, 1)
        results["total_gb"] = round(total_gb, 1)

        if free_gb < 1:
            results["status"] = "critical"
        elif free_gb < 5:
            results["status"] = "warning"
        else:
            results["status"] = "ok"
    except Exception as e:
        results["status"] = "error"
        results["error"] = str(e)[:60]

    return results


# =======================================================================
#  CHECK 7: MEMORY USAGE
# =======================================================================
def check_memory() -> Dict:
    """Check memory usage, warn if > 80% used."""
    results = {"status": "ok", "used_pct": None}

    try:
        with open("/proc/meminfo") as f:
            meminfo = f.read()

        mem_total = None
        mem_available = None
        for line in meminfo.split("\n"):
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available = int(line.split()[1])

        if mem_total and mem_available:
            used_kb = mem_total - mem_available
            used_pct = (used_kb / mem_total) * 100
            results["used_pct"] = round(used_pct, 1)

            if used_pct > 90:
                results["status"] = "critical"
            elif used_pct > 80:
                results["status"] = "warning"
            else:
                results["status"] = "ok"
        else:
            results["status"] = "error"
            results["error"] = "could not parse meminfo"
    except Exception as e:
        results["status"] = "error"
        results["error"] = str(e)[:60]

    return results


# =======================================================================
#  AUTO-FIX: DEAD PARAM IN daily_review_v2.py
# =======================================================================
def auto_fix_dead_params(dead_param_scan: Dict) -> Dict:
    """
    Auto-correct ENGINE_PARAMS mapping in daily_review_v2.py.
    - Removes dead params (in config but not in source)
    - Adds missing params (in source but not in config)
    """
    fixes = {"applied": [], "failed": [], "skipped": []}
    dr_path = os.path.join(ENG_DIR, "daily_review_v2.py")

    if not os.path.exists(dr_path):
        fixes["skipped"].append("daily_review_v2.py not found")
        return fixes

    import re

    try:
        with open(dr_path) as f:
            content = f.read()
    except Exception as e:
        fixes["failed"].append(f"read error: {str(e)[:60]}")
        return fixes

    # ── Fix 1: Remove dead params from ENGINE_PARAMS dict ──
    for mismatch in dead_param_scan.get("mismatches", []):
        if mismatch["type"] != "dead_param":
            continue

        engine = mismatch["engine"]
        param = mismatch["param"]

        # Find and remove the dead param line from that engine's params dict
        # Pattern: "PARAM_NAME":   {...},\n
        # But we need to be careful not to break the dict structure
        # Strategy: comment out the line
        patterns = [
            (rf'(\s*"{param}":\s*\{{[^}}]*?\}}\s*,?\s*\n)', rf'# REMOVED_dead_param: \1'),
            (rf'(\s*#.*{param}.*\n)', r''),  # Remove any existing comments
        ]

        for pat, repl in patterns:
            new_content, count = re.subn(pat, repl, content)
            if count > 0:
                content = new_content
                fixes["applied"].append(f"dead_param:{engine}/{param} → commented out in ENGINE_PARAMS")
                break
        else:
            fixes["skipped"].append(f"dead_param:{engine}/{param} → pattern not found")

    # ── Fix 2: Add missing params to ENGINE_PARAMS dict ──
    for mismatch in dead_param_scan.get("mismatches", []):
        if mismatch["type"] != "missing_param":
            continue

        engine = mismatch["engine"]
        param = mismatch["param"]

        # Try to read the current value from the engine file
        filepath = os.path.join(ENG_DIR, mismatch["file"])
        current_val = None
        if os.path.exists(filepath):
            with open(filepath) as f:
                econtent = f.read()
            val_match = re.search(
                rf'{param}\s*:\s*\w+\s*=\s*([-\d.]+)',
                econtent
            )
            if not val_match:
                val_match = re.search(
                    rf'{param}\s*=\s*([-\d.]+)',
                    econtent
                )
            if val_match:
                raw = val_match.group(1)
                current_val = float(raw) if "." in raw else int(raw)

        if current_val is None:
            current_val = 0.0  # fallback

        # Infer min/max/step based on param type
        if "_PCT" in param:
            step = 0.005
            pmin, pmax = 0.001, 0.50
        elif "_THRESHOLD" in param:
            step = 0.05
            pmin, pmax = 0.0, 10.0
        elif "_MULT" in param:
            step = 0.25
            pmin, pmax = 0.5, 10.0
        elif "_PERIOD" in param:
            step = 1
            pmin, pmax = 5, 100
        else:
            step = 1
            pmin, pmax = 1, 100

        # Try to insert into the correct engine's params dict
        engine_block_pattern = rf'("{engine}":\s*\{{[^}}]*?"file":\s*"[^"]+"\s*,\s*"params":\s*\{{)'
        engine_match = re.search(engine_block_pattern, content)
        if engine_match:
            insert_pos = engine_match.end()
            insert_line = f'\n            "{param}":   {{"min": {pmin}, "max": {pmax}, "step": {step}, "default": {current_val}}},'
            content = content[:insert_pos] + insert_line + content[insert_pos:]
            fixes["applied"].append(f"missing_param:{engine}/{param} → inserted into ENGINE_PARAMS (default={current_val})")
        else:
            fixes["skipped"].append(f"missing_param:{engine}/{param} → engine block not found")

    # ── Write changes ──
    if fixes["applied"]:
        try:
            # Backup original
            ts = datetime.now(HKT).strftime("%Y%m%d_%H%M%S")
            backup_path = dr_path + f".guardian_bak_{ts}"
            with open(dr_path) as f:
                original = f.read()
            with open(backup_path, "w") as f:
                f.write(original)
            
            # Write fixed version
            with open(dr_path, "w") as f:
                f.write(content)
            fixes["backup"] = backup_path
        except Exception as e:
            fixes["failed"].append(f"write error: {str(e)[:60]}")

    return fixes


# =======================================================================
#  AUTO-FIX: IMPORT BROKEN
# =======================================================================
def auto_fix_imports(import_results: Dict) -> Dict:
    """Attempt pip install or sys.path fix for broken imports."""
    fixes = {"applied": [], "failed": [], "skipped": []}

    if not import_results.get("failed_imports"):
        fixes["skipped"].append("no broken imports to fix")
        return fixes

    for failed in import_results["failed_imports"]:
        # Check if it's a module name we can pip install
        failed_module = failed.split(":")[0].strip()

        # Check for common missing packages
        pip_map = {
            "yfinance": "yfinance",
            "requests": "requests",
            "dotenv": "python-dotenv",
            "cryptography": "cryptography",
            "pandas": "pandas",
            "numpy": "numpy",
            "ta": "ta",
        }

        for pkg_name, pip_name in pip_map.items():
            if pkg_name in failed_module or pkg_name in failed:
                try:
                    result = subprocess.run(
                        [VENV_PYTHON, "-m", "pip", "install", pip_name, "--quiet"],
                        capture_output=True, text=True, timeout=60,
                    )
                    if result.returncode == 0:
                        fixes["applied"].append(f"pip install {pip_name} → OK")
                    else:
                        fixes["failed"].append(
                            f"pip install {pip_name}: {result.stderr[:100]}"
                        )
                except Exception as e:
                    fixes["failed"].append(f"pip {pip_name}: {str(e)[:60]}")
                break
        else:
            # Try sys.path fix: ensure workspace paths are in sys.path
            for p in [WORKSPACE, ENG_DIR, CAPITALCOM_DIR, COLLECTORS_DIR]:
                if p not in sys.path:
                    sys.path.insert(0, p)
            # Try the import again
            try:
                exec(f"import {failed_module.split('.')[0]}")
                fixes["applied"].append(f"sys.path fix → {failed_module} now importable")
            except ImportError:
                fixes["failed"].append(f"pip install not available for {failed_module}")

    return fixes


# =======================================================================
#  AUTO-FIX: TRADE SYNC MISMATCH
# =======================================================================
def auto_fix_trade_sync(trade_results: Dict) -> Dict:
    """Force reconcile: update trade JSONs to match API reality."""
    fixes = {"applied": [], "failed": [], "skipped": []}

    if trade_results.get("status") != "mismatch":
        fixes["skipped"].append("no mismatch to fix")
        return fixes

    # 🛡️ Anti-wipeout Sanity Check (防暴走機制)
    json_count = trade_results.get("open_in_json", 0)
    api_count = trade_results.get("open_in_api", 0)
    if api_count == 0 and json_count >= 3:
        fixes["skipped"].append(f"Sanity Check: API returned 0 positions but JSON has {json_count}. Full wipeout prevented.")
        return fixes

    try:
        from capitalcom_paper import check_positions, _auth, ACCOUNTS, _headers, DEMO_URL
        import requests as _requests
        _auth()
        # 🔧 Query ALL accounts, not just equities (default)
        api_positions = []
        for acct_name, acct_id in ACCOUNTS.items():
            try:
                h = _headers(acct_id)
                r = _requests.get(f'{DEMO_URL}/api/v1/positions', headers=h, timeout=10)
                if r.status_code == 200:
                    raw_positions = r.json().get('positions', [])
                    for p in raw_positions:
                        pos_data = p.get('position', {})
                        mkt_data = p.get('market', {})
                        # Get epic — may be null in API, fallback to reverse-lookup
                        epic = mkt_data.get('epic', None) or '?'
                        cap_dir = pos_data.get('direction', '?')
                        direction = 'LONG' if cap_dir == 'BUY' else 'SHORT'
                        size = pos_data.get('size', 0)
                        entry = float(pos_data.get('level', 0))
                        deal_id = pos_data.get('dealId', '?')
                        # Reverse-map epic → our ticker
                        ticker = epic
                        from capitalcom_paper import EPIC_MAP
                        for t, e in EPIC_MAP.items():
                            if e == epic:
                                ticker = t
                                break
                        api_positions.append({
                            'epic': epic,
                            'ticker': ticker,
                            'direction': direction,
                            'size': size,
                            'entry': entry,
                            'deal_id': deal_id,
                            'account': acct_name,
                        })
            except Exception:
                continue
    except Exception as e:
        fixes["failed"].append(f"API call failed: {str(e)[:60]}")
        return fixes

    # ── Build API truth sets: both by deal_id AND by (market, direction) ──
    # Primary: deal_id matching (exact, no ambiguity)
    # Secondary: (market, direction) matching for entries without deal_id
    api_deal_ids = set()
    api_market_dir = set()
    api_entries_by_deal_id = {}  # For rebuilding paper_trades.json

    for pos in api_positions:
        deal_id = pos.get("deal_id", "")
        ticker = pos.get("ticker") or pos.get("epic") or "?"
        direction = pos.get("direction", "?")
        account = pos.get("account", "")

        if deal_id and deal_id != "?":
            api_deal_ids.add(deal_id)
            api_entries_by_deal_id[deal_id] = pos

        # Build (market, direction) match set with comprehensive normalization
        api_market_dir.add((ticker, direction))

        # Account-based market name (what engine files use)
        if account == "crypto":
            api_market_dir.add(("BTC", direction))
            api_market_dir.add(("CRYPTO", direction))
        elif account == "equities":
            api_market_dir.add((ticker, direction))  # e.g. ("INTC", "LONG")
        elif account == "indices":
            api_market_dir.add(("INDICES", direction))
            api_market_dir.add(("SPX", direction))
            api_market_dir.add(("US500", direction))
        elif account == "gold":
            api_market_dir.add(("GOLD", direction))
            api_market_dir.add(("XAUUSD", direction))

    # ── Step 1: Rebuild paper_trades.json from API truth ──
    # paper_trades.json is the SINGLE SOURCE OF TRUTH. It should ALWAYS
    # reflect Capital.com API reality, never been auto-closed by heuristics.
    paper_path = os.path.join(LOG_DIR, "paper_trades.json")
    try:
        if os.path.exists(paper_path):
            with open(paper_path) as f:
                paper_trades = json.load(f)
        else:
            paper_trades = []
    except (json.JSONDecodeError, OSError):
        paper_trades = []

    # Count how many open entries in paper right now
    paper_open_before = len([t for t in paper_trades if t.get("status") == "open"])

    # Mark all existing paper entries as handled (will be updated/replaced)
    paper_by_deal_id = {}
    other_entries = []  # entries without deal_id or closed
    for t in paper_trades:
        did = t.get("deal_id", "")
        if did and did != "?" and did != "paper_only":
            paper_by_deal_id[did] = t
        else:
            other_entries.append(t)

    # Merge: API positions overwrite/add to paper_trades.json
    now = datetime.now(HKT)
    now_ts = time.time()
    paper_rebuilt = []
    api_synced_count = 0

    for pos in api_positions:
        deal_id = pos.get("deal_id", "")
        ticker = pos.get("ticker", "?")
        epic = pos.get("epic", ticker)
        direction = pos.get("direction", "?")
        entry = pos.get("entry", 0)
        size = pos.get("size", 0)
        account = pos.get("account", "")

        # Existing entry?
        existing = paper_by_deal_id.get(deal_id)

        if existing:
            # Update live P&L, keep historical fields
            existing["status"] = "open"
            existing["reconciled"] = True
            existing["reconciled_note"] = f"Guardian sync {now.strftime('%m/%d %H:%M')}"
            existing["pnl"] = round(float(pos.get("pnl_amt", 0)), 2) if "pnl_amt" in pos else existing.get("pnl", 0)
            if "pnl_amt" not in pos:
                pass  # keep existing pnl
            paper_rebuilt.append(existing)
        else:
            # New entry from API — create full record
            market_map = {"crypto": "CRYPTO", "equities": "EQUITIES", "indices": "INDICES", "gold": "GOLD"}
            market = market_map.get(account, account.upper())
            paper_rebuilt.append({
                "timestamp": now.isoformat(),
                "timestamp_ts": now_ts,
                "market": market,
                "engine": "guardian",
                "strategy": "api_sync",
                "epic": epic,
                "ticker": ticker,
                "direction": direction,
                "entry": entry,
                "stop": 0,
                "target": 0,
                "size": size,
                "deal_ref": f"api_{deal_id[:20] if deal_id else '?'}",
                "deal_id": deal_id,
                "status": "open",
                "pnl": 0.0,
                "adjusted_pnl": 0.0,
                "raw_pnl": 0.0,
                "slippage_cost": 0.0,
                "slippage_applied": False,
                "exit_price": 0.0,
                "closed_at": "",
                "closed_at_ts": 0,
                "account": account,
                "reason": "",
                "highest_price": entry,
                "lowest_price": entry,
                "trailing_active": False,
                "time_stop_minutes": 240,
                "reconciled": True,
                "reconciled_note": f"Guardian auto-sync from API {now.strftime('%m/%d %H:%M')}",
            })
            api_synced_count += 1

    # Keep non-API entries (paper_only, closed, no deal_id)
    # 🔧 Clean ghost entries: if an unreconciled entry with deal_id="?" 
    # matches a newly-synced API entry (same epic+direction), remove it
    reconciled_epic_dir = {
        (x.get("epic", ""), x.get("direction", ""))
        for x in paper_rebuilt
        if x.get("reconciled") and x.get("deal_id") and x["deal_id"] != "?"
    }
    
    for t in other_entries:
        did = t.get("deal_id", "")
        # Ghost: deal_id="?" AND not reconciled AND matches a reconciled entry
        if did == "?" and not t.get("reconciled"):
            key = (t.get("epic", ""), t.get("direction", ""))
            if key in reconciled_epic_dir:
                continue  # 🧹 Skip ghost — already have real reconciled entry
                
            # If not in reconciled, check if it's still alive on API (by epic+direction)
            if t.get("status") == "open" and not t.get("paper_only"):
                # api_market_dir contains things like ("BTC", "LONG") or ("INTC", "LONG")
                market = t.get("market", key[0])
                if (market, key[1]) not in api_market_dir and (key[0], key[1]) not in api_market_dir:
                    t["status"] = "closed"
                    t["close_reason"] = "guardian_detected_close_ghost"
                    t["closed_at"] = now.isoformat()
                    t["closed_at_ts"] = now_ts
                    
        if did and did != "?" and did != "paper_only" and did not in api_deal_ids:
            # Entry was on API before but now closed → mark as closed if still open
            if t.get("status") == "open":
                t["status"] = "closed"
                t["close_reason"] = "guardian_detected_close"
                t["closed_at"] = now.isoformat()
                t["closed_at_ts"] = now_ts
        paper_rebuilt.append(t)

    paper_open_after = len([t for t in paper_rebuilt if t.get("status") == "open"])

    # Atomic write
    tmp = paper_path + ".guardian_tmp"
    with open(tmp, "w") as f:
        json.dump(paper_rebuilt, f, indent=2, default=str, ensure_ascii=False)
    os.replace(tmp, paper_path)

    if api_synced_count > 0:
        fixes["applied"].append(
            f"paper_trades.json: synced {api_synced_count} new from API "
            f"(open: {paper_open_before}→{paper_open_after}, API has {len(api_positions)})"
        )
    elif paper_open_before != paper_open_after:
        fixes["applied"].append(
            f"paper_trades.json: reconciled closes ({paper_open_before}→{paper_open_after} open)"
        )
    else:
        fixes["skipped"].append("paper_trades.json already in sync with API")

    # ── Step 2: Clean engine-specific files (ghost entries without deal_id match) ──
    for fname in os.listdir(LOG_DIR):
        if not fname.endswith("_trades.json"):
            continue
        if fname == "paper_trades.json":
            continue  # Already handled above
        path = os.path.join(LOG_DIR, fname)
        try:
            with open(path) as f:
                trades = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        modified = False
        for t in trades:
            if t.get("status") != "open":
                continue
            if t.get("paper_only"):
                continue  # ⛔ Never touch paper_only virtual trades

            deal_id = t.get("deal_id", "")

            if t.get("reconciled"):
                # Reconciled but position may have closed legitimately on API
                # → check deal_id: if no longer on API, mark closed (JSON only, no API call)
                if deal_id and deal_id != "?" and deal_id != "paper_only" and deal_id not in api_deal_ids:
                    t["status"] = "closed"
                    t["close_reason"] = "guardian_detected_close"
                    t["closed_at"] = now.isoformat()
                    t["closed_at_ts"] = now_ts
                    t["adjusted_pnl"] = t.get("pnl", 0.0)
                    modified = True
                    print(f"  📝 Guardian: reconciled {t.get('epic','?')} {t.get('direction','?')} no longer on API → closed in JSON")
                continue  # Don't apply ghost-closing logic to reconciled entries

            # Primary: exact deal_id match
            if deal_id and deal_id != "?" and deal_id != "paper_only" and deal_id in api_deal_ids:
                continue  # ✅ Confirmed open on API

            # Secondary: (market, direction) match for entries without deal_id
            market = t.get("market", t.get("epic", "?"))
            direction = t.get("direction", "?")
            if (market, direction) in api_market_dir:
                continue  # ✅ Matched by market + direction

            # 🚨 No match → truly a ghost entry, safe to close
            t["status"] = "closed"
            t["close_reason"] = "guardian_reconcile"
            t["closed_at"] = now.isoformat()
            t["closed_at_ts"] = now_ts
            t["adjusted_pnl"] = t.get("pnl", 0.0)
            modified = True
            print(f"  🧹 Guardian: closed ghost {market} {direction} in {fname}")

        if modified:
            tmp = path + ".guardian_tmp"
            with open(tmp, "w") as f:
                json.dump(trades, f, indent=2, default=str)
            os.replace(tmp, path)
            fixes["applied"].append(f"reconciled {fname}: closed ghost trades")

    if not fixes["applied"]:
        fixes["skipped"].append("no ghost trades found to close")

    return fixes


# =======================================================================
#  MAIN: RUN ALL CHECKS + AUTO-FIX + REPORT
# =======================================================================
def run_all_checks() -> Dict:
    """Run all 7 health checks and return consolidated results."""
    report = {
        "generated_at": datetime.now(HKT).isoformat(),
        "host": os.uname().nodename,
        "checks": {},
        "overall_status": "ok",
        "broken_count": 0,
        "warning_count": 0,
        "alerts": [],
        "auto_fixes": {},
    }

    # ── Check 1: Imports ──
    try:
        import_result = check_imports()
        report["checks"]["imports"] = import_result
        if import_result["status"] == "broken":
            report["broken_count"] += 1
            report["alerts"].append(
                f"❌ Import broken: {', '.join(import_result['failed_imports'][:3])}"
            )
    except Exception as e:
        report["checks"]["imports"] = {"status": "error", "error": str(e)[:80]}
        report["broken_count"] += 1
        report["alerts"].append(f"❌ Import check crashed: {str(e)[:60]}")

    # ── Check 2: API Health ──
    try:
        api_result = check_api_health()
        # Compute overall status
        services = {k: api_result.get(k) for k in ("hyperliquid", "capitalcom", "gemini_api")}
        if "dead" in services.values():
            api_result["status"] = "dead"
        elif "degraded" in services.values():
            api_result["status"] = "degraded"
        else:
            api_result["status"] = "ok"
        report["checks"]["api_health"] = api_result
        for service_key in ("hyperliquid", "capitalcom", "gemini_api"):
            status = api_result.get(service_key, "unknown")
            if status == "dead":
                report["broken_count"] += 1
                report["alerts"].append(f"❌ API dead: {service_key}")
            elif status == "degraded":
                report["warning_count"] += 1
                report["alerts"].append(f"⚠️ API degraded: {service_key}")
    except Exception as e:
        report["checks"]["api_health"] = {"status": "error", "error": str(e)[:80]}

    # ── Check 3: Dead Param Scan ──
    try:
        param_result = scan_dead_params()
        report["checks"]["dead_params"] = param_result
        if param_result["status"] == "mismatch":
            report["warning_count"] += len(param_result["dead_params"]) + len(param_result["missing_params"])
            for dp in param_result["dead_params"]:
                report["alerts"].append(
                    f"⚠️ Dead param: {dp['engine']}/{dp['param']} (in config, not in source)"
                )
            for mp in param_result["missing_params"]:
                report["alerts"].append(
                    f"⚠️ Missing param: {mp['engine']}/{mp['param']} (in source, not in config)"
                )
    except Exception as e:
        report["checks"]["dead_params"] = {"status": "error", "error": str(e)[:80]}

    # ── Check 4: Trade Consistency ──
    try:
        trade_result = check_trade_consistency()
        report["checks"]["trade_consistency"] = trade_result
        if trade_result["status"] == "mismatch":
            report["broken_count"] += 1
            for d in trade_result["discrepancies"]:
                report["alerts"].append(f"❌ Trade mismatch: {d}")
        elif trade_result["status"] == "api_error":
            report["broken_count"] += 1
            for d in trade_result["discrepancies"]:
                report["alerts"].append(f"❌ Trade API error: {d}")
    except Exception as e:
        report["checks"]["trade_consistency"] = {"status": "error", "error": str(e)[:80]}

    # ── Check 5: Cron Log ──
    try:
        cron_result = check_cron_log()
        report["checks"]["cron_log"] = cron_result
        if cron_result["status"] == "stale":
            report["broken_count"] += 1
            report["alerts"].append(
                f"❌ Cron stale: last run {cron_result.get('minutes_since_last', '?')}min ago"
            )
        elif cron_result["status"] == "warning":
            report["warning_count"] += 1
            report["alerts"].append(
                f"⚠️ Cron infrequent: last run {cron_result.get('minutes_since_last', '?')}min ago"
            )
    except Exception as e:
        report["checks"]["cron_log"] = {"status": "error", "error": str(e)[:80]}

    # ── Check 6: Disk Space ──
    try:
        disk_result = check_disk_space()
        report["checks"]["disk_space"] = disk_result
        if disk_result["status"] == "critical":
            report["broken_count"] += 1
            report["alerts"].append(
                f"❌ Disk critical: {disk_result['free_gb']} GB free"
            )
        elif disk_result["status"] == "warning":
            report["warning_count"] += 1
            report["alerts"].append(
                f"⚠️ Disk low: {disk_result['free_gb']} GB free"
            )
    except Exception as e:
        report["checks"]["disk_space"] = {"status": "error", "error": str(e)[:80]}

    # ── Check 7: Memory ──
    try:
        mem_result = check_memory()
        report["checks"]["memory"] = mem_result
        if mem_result["status"] == "critical":
            report["broken_count"] += 1
            report["alerts"].append(
                f"❌ Memory critical: {mem_result['used_pct']}% used"
            )
        elif mem_result["status"] == "warning":
            report["warning_count"] += 1
            report["alerts"].append(
                f"⚠️ Memory high: {mem_result['used_pct']}% used"
            )
    except Exception as e:
        report["checks"]["memory"] = {"status": "error", "error": str(e)[:80]}

    # ── Overall Status ──
    if report["broken_count"] > 0:
        report["overall_status"] = "broken"
    elif report["warning_count"] > 0:
        report["overall_status"] = "warning"
    else:
        report["overall_status"] = "ok"

    return report


def run_auto_fixes(report: Dict) -> Dict:
    """Run auto-fix routines based on check results."""
    fixes = {}

    # Fix 1: Dead params in daily_review_v2.py
    if "dead_params" in report.get("checks", {}):
        param_result = report["checks"]["dead_params"]
        if param_result.get("status") == "mismatch" and param_result.get("mismatches"):
            fixes["dead_params"] = auto_fix_dead_params(param_result)

    # Fix 2: Broken imports
    if "imports" in report.get("checks", {}):
        import_result = report["checks"]["imports"]
        if import_result.get("status") == "broken":
            fixes["imports"] = auto_fix_imports(import_result)

    # Fix 3: Trade sync mismatch
    if "trade_consistency" in report.get("checks", {}):
        trade_result = report["checks"]["trade_consistency"]
        if trade_result.get("status") == "mismatch":
            fixes["trade_sync"] = auto_fix_trade_sync(trade_result)

    return fixes


def save_report(report: Dict, fixes: Dict = None) -> str:
    """Save health report to JSON file."""
    full_report = dict(report)
    if fixes:
        full_report["auto_fixes"] = fixes

    ts = datetime.now(HKT).strftime("%Y%m%d_%H%M")
    filename = f"guardian_{ts}.json"
    filepath = os.path.join(HEALTH_DIR, filename)

    with open(filepath, "w") as f:
        json.dump(full_report, f, indent=2, default=str)

    # Update latest symlink
    latest_path = os.path.join(HEALTH_DIR, "guardian_latest.json")
    tmp = latest_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(full_report, f, indent=2, default=str)
    os.replace(tmp, latest_path)

    return filepath


def send_tg_alert(report: Dict) -> bool:
    """Send Telegram notification if broken OR if auto-fixes were applied."""
    has_fixes = any(fix.get("applied") for fix in report.get("auto_fixes", {}).values())
    if report["overall_status"] == "ok" and not has_fixes:
        return False  # Silent — nothing broken and no fixes applied

    try:
        from tg_notify import tg_send
    except ImportError:
        try:
            sys.path.insert(0, ENG_DIR)
            from tg_notify import tg_send
        except ImportError:
            print("[GUARDIAN] tg_notify not importable, skipping alert")
            return False

    now = datetime.now(HKT).strftime("%m/%d %H:%M HKT")
    if report["overall_status"] == "ok":
        status_emoji = "🟢"
    else:
        status_emoji = "🔴" if report["overall_status"] == "broken" else "🟡"
    
    lines = [
        f"{status_emoji} <b>System Guardian</b> — {now}",
        f"Status: <b>{report['overall_status'].upper()}</b>",
        f"Broken: {report['broken_count']} | Warnings: {report['warning_count']}",
    ]

    if report["alerts"]:
        lines.append("")
        for alert in report["alerts"][:8]:  # Max 8 alerts per message
            lines.append(alert)

    # Add auto-fix summary if any fixes were applied
    if report.get("auto_fixes"):
        fix_lines = []
        for fix_type, fix_result in report["auto_fixes"].items():
            applied = fix_result.get("applied", [])
            failed = fix_result.get("failed", [])
            if applied:
                fix_lines.append(f"  ✅ {fix_type}: {len(applied)} fix(es)")
            if failed:
                fix_lines.append(f"  ❌ {fix_type}: {len(failed)} failed")
        if fix_lines:
            lines.append("\n🛠️ Auto-Fixes:")
            lines.extend(fix_lines)

    msg = "\n".join(lines)
    return tg_send(msg, silent=(report["overall_status"] in ["warning", "ok"]))


def main():
    print(f"🛡️ System Guardian — {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 50)

    # ── Run all checks ──
    print("\n📋 Running health checks...")
    report = run_all_checks()

    for check_name, check_result in report["checks"].items():
        status = check_result.get("status", "?")
        emoji = "✅" if status == "ok" else ("⚠️" if status in ("warning", "mismatch", "degraded") else "❌")
        print(f"  {emoji} {check_name}: {status.upper()}")

    print(f"\n📊 Overall: {report['overall_status'].upper()}")
    print(f"   Broken: {report['broken_count']} | Warnings: {report['warning_count']}")
    if report["alerts"]:
        for alert in report["alerts"]:
            print(f"   {alert}")

    # ── Auto-fix ──
    if report["broken_count"] > 0 or report["warning_count"] > 0:
        print("\n🛠️ Running auto-fixes...")
        fixes = run_auto_fixes(report)
        report["auto_fixes"] = fixes
        for fix_type, fix_result in fixes.items():
            applied = fix_result.get("applied", [])
            failed = fix_result.get("failed", [])
            if applied:
                for a in applied:
                    print(f"  ✅ {a}")
            if failed:
                for f in failed:
                    print(f"  ❌ {f}")

        # 🔧 Re-check after auto-fix: if trade sync was fixed, re-verify
        trade_fixed = fixes.get("trade_sync", {}).get("applied", [])
        if trade_fixed:
            print("\n🔄 Re-checking trade consistency after auto-fix...")
            try:
                recheck = check_trade_consistency()
                if recheck["status"] == "ok":
                    # Auto-fix succeeded → downgrade from BROKEN to OK
                    report["checks"]["trade_consistency"] = recheck
                    # Remove the trade mismatch alert
                    report["alerts"] = [a for a in report["alerts"] if "Trade mismatch" not in a and "Trade API" not in a]
                    report["broken_count"] = max(0, report["broken_count"] - 1)
                    print(f"  ✅ trade_consistency: OK (auto-fixed)")
            except Exception as e:
                print(f"  ⚠️ Re-check failed: {e}")

        # Recalculate overall status
        if report["broken_count"] == 0 and report["warning_count"] == 0:
            report["overall_status"] = "ok"
        elif report["broken_count"] == 0:
            report["overall_status"] = "warning"
    else:
        report["auto_fixes"] = {}

    # ── Save report ──
    filepath = save_report(report)
    print(f"\n📝 Report saved: {filepath}")

    # ── TG notify ──
    tg_ok = send_tg_alert(report)
    if tg_ok:
        print("📬 TG alert sent")

    return 0 if report["overall_status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
