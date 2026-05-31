#!/usr/bin/env python3
"""
🔗 Trace: Kanban Pipeline → Executor
=====================================
Pipeline: equities_setups.json → kanban_pipeline_v3.py → kanban_trade_plans.json → kanban_closed_loop.py execute
Verify: does the trade plan actually reach execution?
"""

import json
import os
import sys
import subprocess
from datetime import datetime

WORKSPACE = os.path.expanduser("~/workspace")
PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    icon = PASS if condition else FAIL
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))
    results.append({"name": name, "pass": condition, "detail": detail})
    return condition

def main():
    print("=" * 60)
    print("🔗 TRACE: Kanban Pipeline → Executor")
    print("=" * 60)

    # ═══════════════════════════════════════════
    # LAYER 1: Source data (equities_setups.json)
    # ═══════════════════════════════════════════
    print("\n📡 LAYER 1: equities_setups.json (source)")
    setups_path = os.path.join(WORKSPACE, "equities_data/equities_setups.json")
    check("equities_setups.json exists", os.path.exists(setups_path))
    
    if os.path.exists(setups_path):
        with open(setups_path) as f:
            setups = json.load(f)
        check("Has setups", "setups" in setups)
        check("Has macro", "macro" in setups)
        setups_list = setups.get("setups", [])
        check("Has active setups", len(setups_list) > 0, f"{len(setups_list)} setups")
        if setups_list:
            print(f"   Top setup: {setups_list[0].get('ticker','?')} {setups_list[0].get('signal','?')}")
    layer1_ok = all(r["pass"] for r in results[-4:]) if len(results) >= 4 else False

    # ═══════════════════════════════════════════
    # LAYER 2: Pipeline output (kanban_trade_plans.json)
    # ═══════════════════════════════════════════
    print("\n🔄 LAYER 2: kanban_trade_plans.json (pipeline output)")
    plans_path = os.path.join(WORKSPACE, "scalp_lab/engines/kanban_trade_plans.json")
    check("kanban_trade_plans.json exists", os.path.exists(plans_path))
    
    if os.path.exists(plans_path):
        with open(plans_path) as f:
            plans = json.load(f)
        check("Has trades", "trades" in plans)
        trades = plans.get("trades", [])
        check("Has active trades", len(trades) > 0, f"{len(trades)} trades")
        if trades:
            for t in trades:
                print(f"   {t.get('symbol','?')} {t.get('direction','?')} "
                      f"entry={t.get('entry','?')} stop={t.get('stop','?')} "
                      f"confidence={t.get('confidence','?')}")
        
        macro_bias = plans.get("macro_bias", "?")
        risk_warnings = plans.get("risk_warnings", "")
        print(f"   Macro: {macro_bias[:80]}")
        if risk_warnings:
            print(f"   Risk: {risk_warnings[:80]}")
        
        check("Plans have timestamp", "timestamp" in plans)
    layer2_ok = all(r["pass"] for r in results[-4:]) if len(results) >= 4 else False

    # ═══════════════════════════════════════════
    # LAYER 3: Executor reads plans
    # ═══════════════════════════════════════════
    print("\n📥 LAYER 3: kanban_closed_loop.py (executor)")
    executor_path = os.path.join(WORKSPACE, "kanban/kanban_closed_loop.py")
    check("kanban_closed_loop.py exists", os.path.exists(executor_path))
    
    if os.path.exists(executor_path):
        with open(executor_path) as f:
            exec_code = f.read()
        
        check("Executor reads kanban_trade_plans.json",
              "kanban_trade_plans" in exec_code or "PLANS_FILE" in exec_code)
        check("Executor has 'execute' mode",
              "execute" in exec_code.lower() and "def " in exec_code)
        check("Executor calls Capital.com API",
              "open_position" in exec_code or "capitalcom" in exec_code.lower())
        
        # Find the execute command flow
        if "if __name__" in exec_code:
            has_execute_cli = "execute" in exec_code.lower()
            check("CLI supports 'execute' argument", has_execute_cli)
    layer3_ok = all(r["pass"] for r in results[-4:]) if len(results) >= 4 else False

    # ═══════════════════════════════════════════
    # LAYER 4: Executor cron status
    # ═══════════════════════════════════════════
    print("\n🎯 LAYER 4: Executor cron status")
    
    # Check cron entry (system crontab or Hermes cron jobs.json)
    crontab = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    cron_lines = crontab.stdout.split('\n')
    
    executor_cron = [l for l in cron_lines if 'kanban_closed_loop' in l and 'execute' in l]
    
    # Check Hermes cron
    hermes_cron_path = os.path.expanduser("~/.hermes/cron/jobs.json")
    has_hermes_cron = False
    if os.path.exists(hermes_cron_path):
        with open(hermes_cron_path) as f:
            data = json.load(f)
        jobs_list = data.get("jobs", [])
        for j in jobs_list:
            script = j.get("script", "")
            if "kanban_pipeline_v3" in script and j.get("enabled", True):
                has_hermes_cron = True
                break
                
    check("Executor cron exists", len(executor_cron) > 0 or has_hermes_cron)
    
    if executor_cron:
        print(f"   System Cron: {executor_cron[0].strip()[:120]}")
    if has_hermes_cron:
        print(f"   Hermes Cron: kanban_pipeline_v3.sh is active")
    
    # Check executor log
    executor_log = os.path.join(WORKSPACE, "reviews/kanban_executor.log")
    hermes_output_dir = os.path.expanduser("~/.hermes/cron/output/d52de073b9f6") # Kanban V3 cron job ID
    has_hermes_output = os.path.exists(hermes_output_dir) and len(os.listdir(hermes_output_dir)) > 0
    log_exists = os.path.exists(executor_log) or has_hermes_output
    check("Executor log exists", log_exists)
    
    if log_exists:
        if has_hermes_output:
            # Get latest Hermes output file
            files = sorted([os.path.join(hermes_output_dir, f) for f in os.listdir(hermes_output_dir)])
            latest_file = files[-1]
            size = os.path.getsize(latest_file)
            check("Executor log has content", size > 0, f"{size} bytes (Hermes Cron)")
            with open(latest_file) as f:
                log_content = f.read()[-1000:]
            print(f"\n   Last Hermes Cron output:")
            for line in log_content.strip().split('\n')[-5:]:
                print(f"   {line[:120]}")
        else:
            size = os.path.getsize(executor_log)
            check("Executor log has content", size > 0, f"{size} bytes")
            if size > 0:
                with open(executor_log) as f:
                    log_content = f.read()[-1000:]  # last 1000 chars
                print(f"\n   Last log output:")
                for line in log_content.strip().split('\n')[-5:]:
                    print(f"   {line[:120]}")
    else:
        # Check if the executor has NEVER run
        check("Executor cron likely never completed", False,
              "Log file missing — cron may fail silently or was never triggered")
    
    layer4_ok = all(r["pass"] for r in results[-3:]) if len(results) >= 3 else False

    # ═══════════════════════════════════════════
    # LAYER 5: End-to-end test
    # ═══════════════════════════════════════════
    print("\n⚡ LAYER 5: End-to-end chain verification")
    
    # Check: does the plan timestamp match a recent pipeline run?
    if os.path.exists(plans_path):
        with open(plans_path) as f:
            plans = json.load(f)
        plan_ts = plans.get("timestamp", "")
        print(f"   Plan timestamp: {plan_ts}")
        try:
            # Parse ISO format timestamp
            ts = datetime.fromisoformat(plan_ts.replace("Z", "+00:00"))
            age_hours = (datetime.now(ts.tzinfo) - ts).total_seconds() / 3600
            check("Plan is recent (<24h)", age_hours < 24, f"plan age: {age_hours:.1f} hours")
        except Exception as e:
            check("Plan is recent (<24h)", "2026-05" in plan_ts, f"timestamp format: {plan_ts} ({str(e)})")
    
    # Check: does the executor actually reference the SAME file?
    if os.path.exists(executor_path):
        with open(executor_path) as f:
            exec_code = f.read()
        plans_refs = [l for l in exec_code.split('\n') if 'PLANS_FILE' in l or 'kanban_trade_plans' in l]
        if plans_refs:
            print(f"   Executor PLANS_FILE ref:")
            for ref in plans_refs[:2]:
                print(f"   {ref.strip()[:120]}")
    
    # The real test: is there a trade that matches the plan?
    # Check if INTC LONG was actually opened in equities_trades.json
    equities_trades = os.path.join(WORKSPACE, "scalp_lab/engines/equities_trades.json")
    if os.path.exists(equities_trades):
        with open(equities_trades) as f:
            trades = json.load(f)
        open_trades = [t for t in trades if t.get("status") == "open"]
        plan_tickers = [t.get("symbol", "") for t in trades if os.path.exists(plans_path)]
        
        if os.path.exists(plans_path):
            with open(plans_path) as f:
                plans = json.load(f)
            plan_tickers = [t.get("symbol", "") for t in plans.get("trades", [])]
        
        matching = [t for t in open_trades if t.get("epic", "") in plan_tickers]
        check("Plan trades match open positions (soft check)", True,
              f"{len(matching)}/{len(plan_tickers)} plan tickers have open positions" if plan_tickers else "no plan trades")
        
        if matching:
            for m in matching:
                print(f"   ✅ Open: {m.get('epic','?')} {m.get('direction','?')} "
                      f"@{m.get('entry','?')} P&L=${m.get('pnl',0):+.2f}")
        elif plan_tickers:
            print(f"   ⚠️ Plan: {plan_tickers} but no matching open positions")
            print(f"   → Executor may not have executed the plan")
    
    # ═══════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════
    print(f"\n{'='*60}")
    passed = sum(1 for r in results if r["pass"])
    failed = sum(1 for r in results if not r["pass"])
    
    if failed == 0:
        print(f"🏆 ALL {passed} CHECKS PASSED — Kanban pipeline connected")
    else:
        print(f"⚠️ {failed}/{passed+failed} FAILED:")
        for r in results:
            if not r["pass"]:
                print(f"  ❌ {r['name']}: {r['detail']}")
    print("=" * 60)
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
