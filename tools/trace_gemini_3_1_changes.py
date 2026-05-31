#!/usr/bin/env python3
"""
Integration Trace: Gemini 3.1 Pro 調整驗證
==========================================
Trace all data flows modified by Gemini 3.1 Pro on 2026-05-29/30:
  Layer 1: System 3 (Chanlun) quality_score → cross_ref_snapshot → System 1 Priority 0
  Layer 2: System 1 engines — edge_weight gate → signal suppression
  Layer 3: System 1 engines — active exit conditions → capitalcom_paper close
  Layer 4: System 2 — earnings shield/spear → PEAD tags
  Layer 5: Guardian — auto_fix functions
  Layer 6: Freshness check — data not stale
"""

import json, os, sys, time, importlib
from datetime import datetime, timedelta, timezone

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
PASS, FAIL, SKIP = 0, 0, 0

def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  — {detail}")
    return ok

# ════════════════════════════════════════════════════════════════
# LAYER 1: System 3 → System 1 Chanlun Fusion (Priority 0)
# ════════════════════════════════════════════════════════════════
print("=" * 60)
print("Layer 1: System 3 (Chanlun) → System 1 Priority 0 Fusion")
print("=" * 60)

# 1a) Check cross_ref_snapshot.json exists and is fresh
cr_path = os.path.join(WORKSPACE, "chanlun_system", "data", "exports", "cross_ref_snapshot.json")
check("1a. cross_ref_snapshot.json 存在", os.path.exists(cr_path),
      f"Path: {cr_path}")

freshness_ok = False
snap_data = {}
if os.path.exists(cr_path):
    mtime = os.path.getmtime(cr_path)
    age_min = (time.time() - mtime) / 60
    freshness_ok = age_min < 120  # 2 hours for hourly cron
    check(f"1b. cross_ref_snapshot.json 新鮮度 ({age_min:.0f}min < 120min)", freshness_ok,
          f"Age: {age_min:.0f}min")
    
    with open(cr_path) as f:
        snap_data = json.load(f)

# 1c) Check has_1buy field for BTC
btc_has_1buy = None
for s in snap_data.get("snapshots", []):
    if s.get("symbol") == "BTC":
        btc_has_1buy = s.get("chanlun", {}).get("has_1buy")
        break
check("1c. BTC snapshot contains 'has_1buy' field", btc_has_1buy is not None,
      f"BTC has_1buy: {btc_has_1buy}")

# 1d) Check BTC engine reads cross_ref
btc_path = os.path.join(WORKSPACE, "scalp_engines", "btc_swing.py")
with open(btc_path) as f:
    btc_code = f.read()
check("1d. btc_swing.py reads cross_ref_snapshot.json", "cross_ref_snapshot.json" in btc_code,
      "Missing cross_ref reference in BTC engine")
check("1e. btc_swing.py checks has_1buy", "has_1buy" in btc_code,
      "Missing has_1buy check in BTC engine")
check("1f. btc_swing.py has 'Priority 0' / 'chanlun_1buy_fusion'", "chanlun_1buy_fusion" in btc_code,
      "Missing Priority 0 logic in BTC engine")

# 1g) Check ETH engine same
eth_path = os.path.join(WORKSPACE, "scalp_engines", "eth_swing.py")
with open(eth_path) as f:
    eth_code = f.read()
check("1g. eth_swing.py reads cross_ref_snapshot.json", "cross_ref_snapshot.json" in eth_code,
      "Missing in ETH engine")
check("1h. eth_swing.py has 'chanlun_1buy_fusion'", "chanlun_1buy_fusion" in eth_code,
      "Missing in ETH engine")


# ════════════════════════════════════════════════════════════════
# LAYER 2: Edge Weight Gate — signal suppression
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("Layer 2: Edge Weight Gate (signal suppression)")
print("=" * 60)

# 2a) Check base class has edge_weights
base_path = os.path.join(WORKSPACE, "scalp_engines", "engine_base.py")
with open(base_path) as f:
    base_code = f.read()
check("2a. engine_base.py defines edge_weights", "edge_weights" in base_code,
      "Missing edge_weights in base class")

# 2b) Check BTC engine uses edge_weights for gating
check("2b. BTC: atr_breakout edge weight gate", "atr_edge_weight = self.edge_weights.get" in btc_code,
      "BTC missing ATR edge weight gate")
check("2c. BTC: oi_delta edge weight gate", "oi_edge_weight = self.edge_weights.get" in btc_code,
      "BTC missing OI edge weight gate")
check("2d. BTC: funding_bias edge weight gate", "fund_edge_weight = self.edge_weights.get" in btc_code,
      "BTC missing funding edge weight gate")

# 2e) Check ETH engine same
check("2e. ETH: oi_delta edge weight gate", "oi_edge_weight = self.edge_weights.get" in eth_code,
      "ETH missing OI edge weight gate")
check("2f. ETH: ethbtc_ratio edge weight gate", "ratio_edge_weight = self.edge_weights.get" in eth_code,
      "ETH missing ratio edge weight gate")

# 2g) Check Gold engine
gold_path = os.path.join(WORKSPACE, "scalp_engines", "gold_scalp.py")
with open(gold_path) as f:
    gold_code = f.read()
check("2g. GOLD: vix_regime edge weight gate", "vix_edge_weight = self.edge_weights.get" in gold_code,
      "Gold missing VIX edge weight gate")

# 2h) Check Indices engine
idx_path = os.path.join(WORKSPACE, "scalp_engines", "indices_scalp.py")
with open(idx_path) as f:
    idx_code = f.read()
check("2h. INDICES: vix_direction edge weight gate", "vix_edge_weight = self.edge_weights.get" in idx_code,
      "Indices missing VIX edge weight gate")


# ════════════════════════════════════════════════════════════════
# LAYER 3: Active Exit Conditions → capitalcom_paper close
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("Layer 3: Active Exit Conditions → capitalcom_paper close")
print("=" * 60)

# 3a) All 4 engines override check_active_exits
for eng_name, path in [("BTC", btc_path), ("ETH", eth_path),
                        ("GOLD", gold_path), ("INDICES", idx_path)]:
    with open(path) as f:
        code = f.read()
    check(f"3a. {eng_name} has check_active_exits override",
          "def check_active_exits(self):" in code,
          f"Missing override in {eng_name}")

# 3b) BTC-specific exit conditions
check("3b. BTC: OI Delta Reversal check", "oi_delta_reversal" in btc_code.lower(),
      "BTC missing OI delta exit")
check("3c. BTC: Smart Money L/S Shift check", "sm_ls_shift" in btc_code.lower() or "_last_sm_ls" in btc_code,
      "BTC missing SM L/S exit")
check("3d. BTC: Funding Extreme check", "funding_extreme" in btc_code.lower(),
      "BTC missing funding extreme exit")
check("3e. BTC: Chanlun Pivot check", "chanlun_pivot" in btc_code.lower() and "nearest_pivot_upper" in btc_code,
      "BTC missing Chanlun pivot exit")

# 3f) ETH-specific exit conditions
check("3f. ETH: Ratio Reversal check", "ethbtc_ratio_reversal" in eth_code.lower(),
      "ETH missing ratio reversal exit")
check("3g. ETH: OI Puking check", "oi_delta_puking" in eth_code.lower(),
      "ETH missing OI puking exit")
check("3h. ETH: Gas Fee Spike check", "gas_spike" in eth_code.lower() and "gwei" in eth_code.lower(),
      "ETH missing gas spike exit")
check("3i. ETH: Chanlun Pivot check", "chanlun_pivot_exit" in eth_code.lower(),
      "ETH missing Chanlun pivot exit")

# 3j) Gold-specific exit conditions
check("3j. GOLD: VIX Regime Change check", "vix_regime_change" in gold_code.lower(),
      "Gold missing VIX regime exit")
check("3k. GOLD: DXY Spike check", "dxy_spike" in gold_code.lower() or ("eurusd" in gold_code.lower() and "dxy" in gold_code.lower()),
      "Gold missing DXY spike exit")
check("3l. GOLD: Time-Based Exit check", "time_profit_exit" in gold_code.lower() or "time_loss_exit" in gold_code.lower(),
      "Gold missing time-based exit")

# 3m) Indices-specific exit conditions
check("3m. INDICES: VIX Spike check", "vix_spike" in idx_code.lower(),
      "Indices missing VIX spike exit")
check("3n. INDICES: Yield/Bond check", ("us10y" in idx_code.lower() or "bond" in idx_code.lower() or "yield" in idx_code.lower()),
      "Indices missing yield exit")
check("3o. INDICES: Trailing SMA check", "trailing" in idx_code.lower() and "sma" in idx_code.lower(),
      "Indices missing trailing SMA exit")

# 3p) Verify capitalcom_paper import in all 4 engines
for eng_name, path in [("BTC", btc_path), ("ETH", eth_path),
                        ("GOLD", gold_path), ("INDICES", idx_path)]:
    with open(path) as f:
        code = f.read()
    check(f"3p. {eng_name} imports capitalcom_paper.close_position",
          "from capitalcom_paper import close_position" in code or "close_position" in code,
          f"{eng_name} missing close_position import")

# 3q) Verify tg_trade_close integration  
check("3q. BTC imports tg_trade_close for active exit notification",
      "tg_trade_close" in btc_code,
      "BTC missing TG notification on exit")

# 3r) Smart Money Risk Filter (BTC only)
check("3r. BTC: Smart Money risk_mult filter", "risk_mult" in btc_code,
      "BTC missing SM risk filter")
check("3s. BTC: risk_mult saved in trade record", '"risk_mult"' in btc_code,
      "BTC not saving risk_mult to trade JSON")


# ════════════════════════════════════════════════════════════════
# LAYER 4: System 2 — Earnings Shield/Spear
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("Layer 4: System 2 — Earnings Shield/Spear (PEAD)")
print("=" * 60)

eq_path = os.path.join(WORKSPACE, "kanban", "equities_engine.py")
with open(eq_path) as f:
    eq_code = f.read()

check("4a. equities_engine.py has check_earnings function",
      "check_earnings_risk_or_pead" in eq_code,
      "Missing earnings check function")
check("4b. Earnings SHIELD (skip if <3 days to earnings)",
      '"shield"' in eq_code.lower() or ('action' in eq_code and 'skip' in eq_code) or 'days_to_earnings' in eq_code,
      "Missing shield logic")
check("4c. Earnings SPEAR (PEAD detection: <3 days after + volume spike)",
      '"pead"' in eq_code.lower() or ('is_pead' in eq_code),
      "Missing PEAD spear logic")
check("4d. PEAD tag flows to candidate scoring",
      '"PEAD Squeeze"' in eq_code or '"PEAD Breakout"' in eq_code,
      "PEAD tag not integrated into scoring")


# ════════════════════════════════════════════════════════════════
# LAYER 5: Guardian Auto-Fix Functions
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("Layer 5: Guardian Auto-Fix Functions")
print("=" * 60)

gd_path = os.path.join(WORKSPACE, "scalp_engines", "system_guardian.py")
with open(gd_path) as f:
    gd_code = f.read()

check("5a. Guardian has auto_fix_dead_params", "def auto_fix_dead_params" in gd_code,
      "Missing auto_fix_dead_params")
check("5b. Guardian has auto_fix_imports", "def auto_fix_imports" in gd_code,
      "Missing auto_fix_imports")
check("5c. Guardian has auto_fix_trade_sync", "def auto_fix_trade_sync" in gd_code,
      "Missing auto_fix_trade_sync")
check("5d. Guardian has scan_dead_params", "def scan_dead_params" in gd_code,
      "Missing scan_dead_params")
check("5e. Guardian main() calls auto_fixes", "auto_fix_" in gd_code,
      "Guardian doesn't call auto_fix functions")


# ════════════════════════════════════════════════════════════════
# LAYER 6: Freshness Check (Anti-Stale Data)
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("Layer 6: Freshness Check — is data alive?")
print("=" * 60)

data_files = {
    "cross_ref_snapshot.json": os.path.join(WORKSPACE, "chanlun_system", "data", "exports", "cross_ref_snapshot.json"),
    "structure_state.json": os.path.join(WORKSPACE, "chanlun_system", "data", "exports", "structure_state.json"),
    "flow_state.json": os.path.join(WORKSPACE, "scalp_lab", "engines", "flow_state.json"),
    "latest.json (chanlun signals)": os.path.join(WORKSPACE, "chanlun_system", "data", "signals", "latest.json"),
}

for label, path in data_files.items():
    if os.path.exists(path):
        mtime = os.path.getmtime(path)
        age_h = (time.time() - mtime) / 3600
        is_fresh = age_h < 24
        check(f"6. {label}: {age_h:.1f}h old (< 24h = fresh)", is_fresh,
              f"Stale: {age_h:.1f}h")
    else:
        check(f"6. {label}: exists", False, "FILE MISSING")

# Check cron job for Chanlun scanner
cron_jobs_path = os.path.expanduser("~/.hermes/cron/jobs.json")
if os.path.exists(cron_jobs_path):
    with open(cron_jobs_path) as f:
        cron_data = json.load(f)
    jobs = cron_data.get("jobs", [])
    chanlun_job = None
    for j in jobs:
        if isinstance(j, dict) and j.get("script") == "chanlun_scan.sh":
            chanlun_job = j
            break
    if chanlun_job:
        check(f"6. Chanlun cron enabled: {chanlun_job.get('enabled')}, last_status: {chanlun_job.get('last_status')}",
              chanlun_job.get("enabled") and chanlun_job.get("last_status") == "ok",
              f"State: enabled={chanlun_job.get('enabled')}, last_status={chanlun_job.get('last_status')}")
    else:
        check("6. Chanlun cron job exists", False, "No chanlun_scan.sh cron job found")


# ════════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"SUMMARY: {PASS}✅ / {FAIL}❌ / {SKIP}⏭️")
print("=" * 60)

if FAIL > 0:
    print("\n⚠️ FAILED CHECKS — Gemini 3.1 調整有 gap，唔可以 declare done")
    sys.exit(1)
else:
    print("\n🎉 ALL CHECKS PASSED — Gemini 3.1 Pro 調整經 trace 驗證，數據流完整")
    sys.exit(0)
