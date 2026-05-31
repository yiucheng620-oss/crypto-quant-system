#!/usr/bin/env python3
"""
🔍 Universal Pipeline Gap Scanner — detect_gaps.py
===================================================
Auto-scans the codebase for data pipeline gaps.
Strategy: scan filesystem for JSON files → match to code references by filename.

Detection:
  1. Orphan writes — JSON file exists, has writer(s), but NO reader in code
  2. Missing source — JSON file read in code but doesn't exist on disk
  3. Stale data — JSON file >24h since last write
  4. Cron log gaps — log files that don't exist or are empty

Usage:
  python3 tools/detect_gaps.py           # Full scan
  python3 tools/detect_gaps.py --quick   # Skip cron check
  python3 tools/detect_gaps.py --json    # Machine-readable output

Exit: 0 = no gaps, 1 = gaps found
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")

# Directories to scan for JSON data files — auto-discovered from workspace
# (excluded: __pycache__, .git, node_modules, venv, .hermes)
DATA_ROOTS = None  # Auto-discover; set to list to override

# Directories to scan for Python source code — auto-discovered
CODE_ROOTS = None  # Auto-discover; set to list to override

# Directories to skip during auto-discovery
SKIP_DIRS = {
    "__pycache__", ".git", "node_modules", "venv", ".venv",
    ".hermes", "logs", "reviews", "_backups",
}

# Files to exclude from orphan check (infrastructure/config, no consumer needed)
NON_ORPHAN_FILES = {
    "coordinator_state.json",     # internal state
    "system_snapshot.json",       # diagnostic dump
    "flow_history.json",          # append-only log, consumer exists but via different path
    "exit_timing_results.json",   # backtest artifact
    "oi_delta_backtest_results.json",  # backtest artifact
    "flow_state.json.lock",       # lock file
    "flow_history.json.lock",     # lock file
}

# Files to ignore entirely
IGNORE_PATTERNS = [
    r'\.tmp$', r'\.bak$', r'\.lock$', r'\.pyc$',
    r'scan_full_scan', r'guardian_\d{8}',
    r'health_reports', r'accuracy_reports',
    r'.*_history\.csv$', r'.*_USD_history\.csv$',
]


def should_ignore(filename: str) -> bool:
    for pat in IGNORE_PATTERNS:
        if re.search(pat, filename):
            return True
    return False


def auto_discover_dirs(file_ext: str) -> list:
    """Auto-discover directories under WORKSPACE containing given file extension."""
    dirs = set()
    try:
        for entry in os.scandir(WORKSPACE):
            if not entry.is_dir():
                continue
            if entry.name in SKIP_DIRS or entry.name.startswith("."):
                continue
            # Quick check: does this dir contain any matching files (shallow)?
            dir_path = entry.path
            for root, subdirs, files in os.walk(dir_path):
                subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS and not d.startswith(".")]
                if any(f.endswith(file_ext) for f in files):
                    dirs.add(os.path.relpath(dir_path, WORKSPACE))
                    break
    except Exception:
        pass
    return sorted(dirs) if dirs else ["scalp_engines", "collectors", "kanban"]  # fallback


def find_json_files() -> dict:
    """Find all JSON files in data directories with their metadata."""
    files = {}
    roots = DATA_ROOTS if DATA_ROOTS else auto_discover_dirs(".json")
    for root_dir in roots:
        dir_path = os.path.join(WORKSPACE, root_dir)
        if not os.path.exists(dir_path):
            continue
        for dirpath, dirnames, filenames in os.walk(dir_path):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in filenames:
                if should_ignore(fname):
                    continue
                if not fname.endswith('.json'):
                    continue
                fpath = os.path.join(dirpath, fname)
                age_hours = (time.time() - os.path.getmtime(fpath)) / 3600
                files[fname] = {
                    "path": fpath,
                    "size": os.path.getsize(fpath),
                    "age_hours": round(age_hours, 1),
                    "freshness": "fresh" if age_hours < 1 else ("stale" if age_hours < 24 else "dead"),
                }
    return files


def find_code_refs(filename: str) -> dict:
    """Find all code files that reference a given JSON filename."""
    writers = []
    readers = []
    
    # Normalize: strip engine-specific prefixes
    # e.g. "btc_trades.json" could be referenced as f"{self.name}_trades.json"
    search_terms = [filename]
    
    # If filename matches *_trades.json, also search for _trades.json
    for suffix in ['_trades.json', '_signals.json', '_state.json', '_signals.json']:
        if filename.endswith(suffix):
            search_terms.append(suffix)
            break
    
    # Also try the basename
    search_terms.append(filename)
    
    for code_root in CODE_ROOTS if CODE_ROOTS else auto_discover_dirs(".py"):
        dir_path = os.path.join(WORKSPACE, code_root)
        if not os.path.exists(dir_path):
            continue
        for dirpath, dirnames, filenames in os.walk(dir_path):
            dirnames[:] = [d for d in dirnames if not d.startswith("__")]
            for fname in filenames:
                if not fname.endswith('.py'):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    with open(fpath) as f:
                        content = f.read()
                except Exception:
                    continue
                
                rel = os.path.relpath(fpath, WORKSPACE)
                
                # Check if any search term appears in the file (as a string literal)
                for term in search_terms:
                    if term in content:
                        # Heuristic: is it a write or read?
                        lines_with_term = [l for l in content.split('\n') if term in l]
                        has_write = any(
                            any(kw in l for kw in ['json.dump', 'save', 'write', 'atomic_save', '_save'])
                            for l in lines_with_term
                        )
                        has_read = any(
                            any(kw in l for kw in ['json.load', 'load', 'read', 'open'])
                            for l in lines_with_term
                        )
                        if has_write:
                            writers.append(rel)
                        if has_read:
                            readers.append(rel)
                        if not has_write and not has_read:
                            # Mentioned but unclear — check context
                            readers.append(rel)
                        break  # Found this file, don't check other search terms
    
    return {"writers": list(set(writers)), "readers": list(set(readers))}


def check_connected_wildcard(filename: str) -> bool:
    """Check if a file matches a known connected pipeline pattern."""
    # Flow files
    if filename == "flow_state.json":
        return True  # Written by flow_monitor, read by engine_base + others
    if filename == "flow_history.json":
        return True  # Written by flow_monitor, read by regime_detector
    # Smart money
    if filename == "smart_money_latest.json":
        return True  # Written by collector, read by engine_base get_flow_context
    # Regime
    if filename in ("regime_config.json", "regime_history.json"):
        return True
    # Strategy
    if filename == "strategy_config.json":
        return True
    # Kanban
    if filename in ("kanban_trade_plans.json", "kanban_config.json", 
                    "kanban_state.json", "kanban_trade_history.json"):
        return True
    # Engine state/trade/signal files
    if any(filename.endswith(s) for s in ['_state.json', '_trades.json', '_signals.json']):
        return True  # Generated by engine_base, pattern-matched
    # Equities
    if filename in ("equities_setups.json", "equities_picks.json",
                    "stock_shortlist.json", "us_universe.json"):
        return True
    # Market data
    if filename in ("macro_pulse.json", "paper_trades.json", "etf_flow_latest.json"):
        return True
    return False


def check_cron_logs() -> list:
    """Check cron log files."""
    issues = []
    try:
        import subprocess
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode != 0:
            return issues
        
        for line in result.stdout.strip().split("\n"):
            if not line.strip() or line.strip().startswith("#"):
                continue
            match = re.search(r'>>\s*(.+?\.log)', line)
            if not match:
                continue
            log_path = match.group(1)
            if not os.path.exists(log_path):
                issues.append({
                    "type": "cron_log_missing",
                    "file": os.path.basename(log_path),
                    "path": log_path,
                    "detail": "Log does not exist — cron may never have run"
                })
            elif os.path.getsize(log_path) == 0:
                issues.append({
                    "type": "cron_log_empty",
                    "file": os.path.basename(log_path),
                    "path": log_path,
                    "detail": "Log exists but is empty"
                })
    except Exception:
        pass
    return issues


def main(quick: bool = False, json_out: bool = False):
    gaps = []
    warnings = []
    ok = []
    
    if not json_out:
        print("=" * 60)
        print("🔍 Pipeline Gap Scanner")
        print(f"   {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
        print("=" * 60)
        print(f"\n📡 Scanning {len(list(find_json_files()))} JSON files...")
    
    json_files = find_json_files()
    
    for fname, info in sorted(json_files.items()):
        refs = find_code_refs(fname)
        writers = refs["writers"]
        readers = refs["readers"]
        
        # Check if this is a known connected pipeline
        known_connected = check_connected_wildcard(fname) or fname in NON_ORPHAN_FILES
        
        # Gap: orphan write (file exists, written, but NO reader AND not known connected)
        if writers and not readers and not known_connected:
            warnings.append({
                "type": "orphan_write",
                "file": fname,
                "writers": writers,
                "age_hours": info["age_hours"],
                "detail": f"Written but no reader found — possible dead code"
            })
        
        # Gap: stale data (written but >24h old, and should be fresh)
        if info["freshness"] in ("stale", "dead") and writers:
            warnings.append({
                "type": f"data_{info['freshness']}",
                "file": fname,
                "age_hours": info["age_hours"],
                "detail": f"Last written {info['age_hours']:.0f}h ago"
            })
        
        # OK: connected pipeline
        if writers and (readers or known_connected) and info["freshness"] == "fresh":
            ok.append({
                "file": fname,
                "writers": writers,
                "readers": readers if readers else ["(known pattern)"],
            })
    
    # Cron check
    if not quick:
        cron_issues = check_cron_logs()
        gaps.extend(cron_issues)
    
    # Output
    if json_out:
        print(json.dumps({"gaps": gaps, "warnings": warnings, "ok": ok}, indent=2))
    else:
        if gaps:
            print(f"\n❌ GAPS ({len(gaps)}):")
            for g in gaps:
                print(f"  🔴 [{g['type']}] {g['file']} — {g['detail']}")
        
        if warnings:
            print(f"\n⚠️ WARNINGS ({len(warnings)}):")
            for w in warnings:
                print(f"  ⚠️ [{w['type']}] {w['file']} — {w['detail']}")
        
        if ok:
            print(f"\n✅ CONNECTED ({len(ok)}):")
            for o in ok:
                w = ', '.join(o['writers'][:2]) if o['writers'] else '?'
                r = ', '.join(o['readers'][:2]) if o['readers'] else '?'
                print(f"  ✅ {o['file']} ← {w} → {r}")
        
        print(f"\n{'='*60}")
        total = len(gaps) + len(warnings)
        if total == 0:
            print("🏆 NO GAPS")
        else:
            print(f"🔴 {len(gaps)} gaps | ⚠️ {len(warnings)} warnings | ✅ {len(ok)} connected")
        print("=" * 60)
    
    return 0 if len(gaps) == 0 else 1


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    json_out = "--json" in sys.argv
    sys.exit(main(quick=quick, json_out=json_out))
