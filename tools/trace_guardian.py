#!/usr/bin/env python3
"""
🔗 Trace: Guardian → Trade Repair
==================================
Pipeline: system_guardian.py → health checks → reconciliation → trade repair
Verify: does guardian actually fix broken trades?
"""

import json
import os
import sys
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
    print("🔗 TRACE: Guardian → Trade Repair")
    print("=" * 60)

    # ═══════════════════════════════════════════
    # LAYER 1: Guardian runs and produces output
    # ═══════════════════════════════════════════
    print("\n📡 LAYER 1: Guardian log and health reports")
    
    guardian_log = os.path.join(WORKSPACE, "logs/guardian.log")
    check("guardian.log exists", os.path.exists(guardian_log))
    
    if os.path.exists(guardian_log):
        with open(guardian_log) as f:
            log = f.read()
        
        # Count runs
        runs = log.count("System Guardian")
        check("Guardian has run multiple times", runs > 5, f"{runs} runs")
        
        # Count broken items
        broken_count = log.count("Broken: ")
        check("Guardian reports broken count", broken_count > 0, f"broken mentioned {broken_count} times")
        
        # Count reconciliation mentions
        reconcil_count = log.count("reconcil")
        check("Guardian performs reconciliation", reconcil_count > 0, f"reconcil mentioned {reconcil_count} times")
        
        # Check last run result
        last_run_start = log.rfind("System Guardian")
        last_section = log[last_run_start:last_run_start+800] if last_run_start > 0 else ""
        
        if "Broken: 0" in last_section:
            print(f"   Last run: all OK (Broken: 0)")
        elif "Broken:" in last_section:
            import re
            match = re.search(r'Broken:\s*(\d+)', last_section)
            if match:
                print(f"   Last run: {match.group(0)}")
        
        print(f"   Total runs: {runs} | Reconcil mentions: {reconcil_count}")
    layer1_ok = all(r["pass"] for r in results[-3:]) if len(results) >= 3 else False

    # ═══════════════════════════════════════════
    # LAYER 2: Guardian code — what does reconciliation actually do?
    # ═══════════════════════════════════════════
    print("\n🔄 LAYER 2: Guardian reconciliation code")
    
    guardian_py = os.path.join(WORKSPACE, "scalp_engines/system_guardian.py")
    check("system_guardian.py exists", os.path.exists(guardian_py))
    
    if os.path.exists(guardian_py):
        with open(guardian_py) as f:
            gcode = f.read()
        
        check("Has trade_consistency check", "trade_consistency" in gcode)
        check("Reads trade files", "_trades.json" in gcode)
        check("Calls Capital.com API", "check_positions" in gcode or "capitalcom" in gcode.lower())
        check("Has repair logic", "fix" in gcode.lower() or "repair" in gcode.lower() or "reconcil" in gcode.lower())
        
        # Find reconciliation function
        if "def " in gcode and "reconcil" in gcode.lower():
            reconcil_funcs = [l for l in gcode.split('\n') if 'def ' in l and 'reconcil' in l.lower()]
            print(f"\n   Reconciliation functions:")
            for rf in reconcil_funcs[:3]:
                print(f"   {rf.strip()[:100]}")
    layer2_ok = all(r["pass"] for r in results[-4:]) if len(results) >= 4 else False

    # ═══════════════════════════════════════════
    # LAYER 3: Evidence of actual repairs
    # ═══════════════════════════════════════════
    print("\n📥 LAYER 3: Evidence of actual trade repairs")
    
    # Check trade files for reconciled entries
    trade_files = [
        "btc_trades.json", "eth_trades.json", "gold_trades.json",
        "indices_trades.json", "equities_trades.json"
    ]
    
    total_reconciled = 0
    total_broken = 0
    
    for tf in trade_files:
        tf_path = os.path.join(WORKSPACE, "scalp_lab/engines", tf)
        if not os.path.exists(tf_path):
            continue
        
        with open(tf_path) as f:
            trades = json.load(f)
        
        recon = [t for t in trades if t.get("reconciled")]
        broken = [t for t in trades if t.get("status") == "broken" or t.get("error")]
        
        total_reconciled += len(recon)
        total_broken += len(broken)
        
        if recon:
            print(f"   {tf}: {len(recon)} reconciled trades")
            for r in recon[:2]:
                print(f"      {r.get('epic','?')} {r.get('direction','?')} "
                      f"@{r.get('entry','?')} deal_id={r.get('deal_id','')[:20]}...")
    
    check("Reconciled trades exist", total_reconciled > 0, f"{total_reconciled} total")
    check("No broken trades", total_broken == 0, f"{total_broken} broken" if total_broken > 0 else "clean")
    
    # Check: are reconciled trades matched to real Capital.com positions?
    if total_reconciled > 0:
        # Check INTC as example
        equities_trades = os.path.join(WORKSPACE, "scalp_lab/engines/equities_trades.json")
        if os.path.exists(equities_trades):
            with open(equities_trades) as f:
                eq_trades = json.load(f)
            intc = [t for t in eq_trades if t.get("epic") == "INTC" and t.get("reconciled")]
            if intc:
                t = intc[0]
                check("Reconciled trade has real deal_id", 
                      t.get("deal_id") and ("00000000" not in str(t.get("deal_id", "")) or len(str(t.get("deal_id", ""))) > 15),
                      f"deal_id={t.get('deal_id','')[:30]}")
                check("Reconciled trade has real entry price",
                      t.get("entry", 0) > 0,
                      f"entry=${t.get('entry')}")
                check("Reconciled trade has P&L",
                      t.get("pnl") is not None,
                      f"P&L=${t.get('pnl', 0):+.2f}")
                print(f"\n   INTC reconciled trade verified:")
                print(f"      Entry=${t.get('entry')} | Current=${t.get('current_price','?')} "
                      f"| P&L=${t.get('pnl',0):+.2f}")
    layer3_ok = all(r["pass"] for r in results[-4:]) if len(results) >= 4 else False

    # ═══════════════════════════════════════════
    # LAYER 4: Health report structure
    # ═══════════════════════════════════════════
    print("\n🎯 LAYER 4: Health report consistency")
    
    health_dir = os.path.join(WORKSPACE, "data/health_reports")
    latest_health = os.path.join(health_dir, "guardian_latest.json")
    
    if os.path.exists(latest_health):
        with open(latest_health) as f:
            health = json.load(f)
        
        check("Health report has overall status", "overall" in health or "overall_status" in health or "Broken" in str(health))
        check("Health report covers all checks", len(str(health)) > 500, f"{len(str(health))} chars")
        
        # Check if health report mentions reconciliation
        health_str = json.dumps(health)
        has_reconcil = "reconcil" in health_str.lower() or "trade_consistency" in health_str.lower()
        check("Health report includes reconciliation status", has_reconcil)
    
    # ═══════════════════════════════════════════
    # LAYER 5: Guardian cron status
    # ═══════════════════════════════════════════
    print("\n⚡ LAYER 5: Guardian cron + run frequency")
    
    import subprocess
    crontab = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    has_guardian_cron = "system_guardian" in crontab.stdout
    check("Guardian cron exists", has_guardian_cron)
    
    # Check run frequency from log
    if os.path.exists(guardian_log):
        with open(guardian_log) as f:
            log = f.read()
        
        # Count runs in last 24h
        import re
        today = datetime.now().strftime("%Y-%m-%d")
        runs_today = log.count(today)
        check("Guardian ran today", runs_today > 5, f"{runs_today} runs today")
    
    # ═══════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════
    print(f"\n{'='*60}")
    passed = sum(1 for r in results if r["pass"])
    failed = sum(1 for r in results if not r["pass"])
    
    if failed == 0:
        print(f"🏆 ALL {passed} CHECKS PASSED — Guardian pipeline working")
    else:
        print(f"⚠️ {failed}/{passed+failed} FAILED:")
        for r in results:
            if not r["pass"]:
                print(f"  ❌ {r['name']}: {r['detail']}")
    print("=" * 60)
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
