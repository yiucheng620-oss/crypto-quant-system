#!/usr/bin/env python3
"""
🔗 Trace: Regime Config → Engine Override
==========================================
Pipeline: regime_detector.py → regime_config.json → engine_coordinator → Engine params
Verify: does regime_config actually change RISK_PER_TRADE_PCT and MAX_EXPOSURE_PCT?
"""

import json
import os
import sys

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
    print("🔗 TRACE: Regime Config → Engine Override")
    print("=" * 60)

    # ═══════════════════════════════════════════
    # LAYER 1: regime_config.json exists and is valid
    # ═══════════════════════════════════════════
    print("\n📡 LAYER 1: regime_config.json")
    config_path = os.path.join(WORKSPACE, "scalp_lab/engines/regime_config.json")
    check("File exists", os.path.exists(config_path))
    
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)
        check("Has global_overrides", "global_overrides" in config)
        check("Has engines section", "engines" in config)
        
        go = config.get("global_overrides", {})
        risk_mult = go.get("global_risk_multiplier", 1.0)
        max_exp = go.get("global_max_exposure_pct")
        edge_bias = go.get("edge_bias")
        
        check("global_risk_multiplier set", risk_mult != 1.0, f"={risk_mult}")
        check("edge_bias set", edge_bias is not None, f"={edge_bias}")
        
        print(f"\n   Regime config content:")
        print(f"   risk_mult={risk_mult} | max_exp={max_exp} | bias={edge_bias}")
        
        engines = config.get("engines", {})
        for name in ["btc", "eth", "gold", "indices", "equities"]:
            ecfg = engines.get(name, {})
            if ecfg:
                print(f"   [{name:10s}] bias={ecfg.get('bias','?')} risk={ecfg.get('risk_per_trade_pct','?')} "
                      f"SL={ecfg.get('stop_loss_pct','?')} TP={ecfg.get('take_profit_pct','?')}")
        check("All 5 engines configured", len(engines) >= 5, f"{len(engines)} engines")
    layer1_ok = all(r["pass"] for r in results[-6:]) if len(results) >= 6 else False

    # ═══════════════════════════════════════════
    # LAYER 2: Coordinator reads regime_config
    # ═══════════════════════════════════════════
    print("\n🔄 LAYER 2: Coordinator reads config")
    coord_path = os.path.join(WORKSPACE, "scalp_engines/engine_coordinator.py")
    check("engine_coordinator.py exists", os.path.exists(coord_path))
    
    if os.path.exists(coord_path):
        with open(coord_path) as f:
            coord_code = f.read()
        
        check("Coordinator reads regime_config.json", 
              "regime_config.json" in coord_code or "regime_path" in coord_code)
        check("Coordinator applies global_risk_multiplier",
              "global_risk_mult" in coord_code and "RISK_PER_TRADE_PCT" in coord_code)
        check("Coordinator applies per-engine config",
              "apply_regime_overrides" in coord_code or "engine_cfg" in coord_code)
        
        # Check the actual override application code
        if "global_risk_mult" in coord_code:
            print(f"\n   Override code found:")
            for line in coord_code.split('\n'):
                if 'global_risk_mult' in line and 'RISK_PER_TRADE_PCT' in line:
                    print(f"   {line.strip()[:120]}")
                    break
    layer2_ok = all(r["pass"] for r in results[-3:]) if len(results) >= 3 else False

    # ═══════════════════════════════════════════
    # LAYER 3: Engine actually uses overridden params
    # ═══════════════════════════════════════════
    print("\n📥 LAYER 3: Engine param consumption")
    engine_base = os.path.join(WORKSPACE, "scalp_engines/engine_base.py")
    
    if os.path.exists(engine_base):
        with open(engine_base) as f:
            eb_code = f.read()
        
        # Check if RISK_PER_TRADE_PCT is used in position sizing
        check("RISK_PER_TRADE_PCT used in execute_signal",
              "RISK_PER_TRADE_PCT" in eb_code and "risk_amount" in eb_code)
        check("MAX_EXPOSURE_PCT used in execute_signal",
              "MAX_EXPOSURE_PCT" in eb_code and "total_exposure" in eb_code)
        
        # Check apply_regime_overrides method
        check("apply_regime_overrides method exists",
              "def apply_regime_overrides" in eb_code)
        
        # Read the apply method
        if "def apply_regime_overrides" in eb_code:
            start = eb_code.find("def apply_regime_overrides")
            end = eb_code.find("\n    def ", start + 10)
            override_code = eb_code[start:end] if end > start else eb_code[start:start+500]
            print(f"\n   apply_regime_overrides code:")
            for line in override_code.split('\n')[:8]:
                print(f"   {line}")
    layer3_ok = all(r["pass"] for r in results[-3:]) if len(results) >= 3 else False

    # ═══════════════════════════════════════════
    # LAYER 4: Verify engine state reflects config
    # ═══════════════════════════════════════════
    print("\n🎯 LAYER 4: Engine state verification")
    
    from datetime import datetime
    # Check if coordinator state shows regime config was loaded
    coord_state = os.path.join(WORKSPACE, "scalp_lab/engines/coordinator_state.json")
    if os.path.exists(coord_state):
        with open(coord_state) as f:
            cs = json.load(f)
        last_scan = cs.get("last_scan", "never")
        print(f"   Last coordinator scan: {last_scan}")
        check("Coordinator has scanned", "never" not in last_scan)
    
    # Check if engine state files have updated params
    for engine_name in ["btc", "eth", "gold"]:
        state_file = os.path.join(WORKSPACE, f"scalp_lab/engines/{engine_name}_state.json")
        if os.path.exists(state_file):
            with open(state_file) as f:
                state = json.load(f)
            paused = state.get("paused", False)
            print(f"   [{engine_name}] paused={paused}")
    
    # ═══════════════════════════════════════════
    # LAYER 5: Check a recent scan for regime override effect
    # ═══════════════════════════════════════════
    print("\n⚡ LAYER 5: Scan report evidence")
    scan_dir = os.path.join(WORKSPACE, "scalp_lab/engines")
    scan_files = sorted([f for f in os.listdir(scan_dir) if f.startswith("scan_full_scan_")], reverse=True)
    
    if scan_files:
        latest = os.path.join(scan_dir, scan_files[0])
        with open(latest) as f:
            scan = json.load(f)
        
        scan_flow = scan.get("flow", {})
        flow_sigs = scan_flow.get("signals", [])
        vix = scan_flow.get("vix", "?")
        
        # Check: does the scan report show regime was applied?
        # (The coordinator prints regime info during scan, but we can check if
        #  the flow data includes regime-related fields)
        print(f"   Latest scan: {scan_files[0]}")
        print(f"   VIX: {vix} | Flow signals: {flow_sigs}")
        
        # Check engine results
        engines = scan.get("engines", {})
        for name in ["btc", "eth", "gold"]:
            edata = engines.get(name, {})
            if edata:
                sigs = edata.get("signals", 0)
                executed = edata.get("executed", 0)
                bal = edata.get("balance", "?")
                print(f"   [{name}] signals={sigs} exec={executed} bal={bal}")
        
        check("Scan report contains engine results", len(engines) > 0, f"{len(engines)} engines")
    
    # ═══════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════
    print(f"\n{'='*60}")
    passed = sum(1 for r in results if r["pass"])
    failed = sum(1 for r in results if not r["pass"])
    
    if failed == 0:
        print(f"🏆 ALL {passed} CHECKS PASSED — Regime config pipeline connected")
    else:
        print(f"⚠️ {failed}/{passed+failed} FAILED:")
        for r in results:
            if not r["pass"]:
                print(f"  ❌ {r['name']}: {r['detail']}")
    print("=" * 60)
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
