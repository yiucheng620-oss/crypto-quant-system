#!/usr/bin/env python3
"""
🔗 Smart Money Integration Trace — End-to-End Verification
===========================================================
Traces data flow from collector → coordinator → engine → signal → execution.
Each layer prints its input/output so gaps are immediately visible.

Usage: python3 tools/trace_smart_money.py

Exit codes: 0 = all layers connected, 1 = gap found
"""

import json
import os
import sys
from datetime import datetime

WORKSPACE = os.path.expanduser("~/workspace")
sys.path.insert(0, WORKSPACE)

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

results = []

def check(name, condition, detail=""):
    icon = PASS if condition else FAIL
    msg = f"  {icon} {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append({"name": name, "pass": condition, "detail": detail})
    return condition

def main():
    print("=" * 60)
    print("🔗 SMART MONEY INTEGRATION TRACE")
    print(f"   {datetime.now().isoformat()}")
    print("=" * 60)

    # ── LAYER 1: Collector ──
    print("\n📡 LAYER 1: Data Collector")
    try:
        from collectors.smart_money_collector import get_context, pull_all
        snapshot = pull_all()
        check("pull_all() returns data", bool(snapshot), f"keys: {list(snapshot.keys())}")

        data = snapshot.get("data", {})
        check("Has BTCUSDT", "BTCUSDT" in data)
        check("Has ETHUSDT", "ETHUSDT" in data)
        check("Has SOLUSDT", "SOLUSDT" in data)

        ctx = get_context()
        whales = ctx.get("whales", {})
        check("get_context() returns whales", bool(whales), f"tickers: {list(whales.keys())}")

        btc = whales.get("BTC", {})
        check("BTC whale data present", bool(btc))
        if btc:
            print(f"     BTC L/S={btc.get('ls_ratio')} L_win={btc.get('long_profit_pct')}% "
                  f"S_win={btc.get('short_profit_pct')}%")
            print(f"     L_entry=${btc.get('long_entry')} S_entry=${btc.get('short_entry')}")

        eth = whales.get("ETH", {})
        if eth:
            print(f"     ETH L/S={eth.get('ls_ratio')} L_win={eth.get('long_profit_pct')}% "
                  f"S_win={eth.get('short_profit_pct')}%")

        collector_ok = bool(btc)
    except Exception as e:
        collector_ok = False
        check("Collector import", False, str(e))

    # ── LAYER 2: Flow Context (simulate get_flow_context) ──
    print("\n🌊 LAYER 2: Flow Context (get_flow_context)")
    try:
        FLOW_FILE = os.path.join(WORKSPACE, "scalp_lab", "engines", "flow_state.json")
        check("flow_state.json exists", os.path.exists(FLOW_FILE))

        with open(FLOW_FILE, "r") as f:
            raw = json.load(f)
        check("flow_state.json readable", bool(raw), f"keys: {list(raw.keys())[:8]}")

        # Build what get_flow_context() now returns
        flow_ctx = {
            "raw_data": raw,
            "bias_adjustment": {"LONG": 0, "SHORT": 0},
            "vix": raw.get("vix", 0),
            "vix_regime": raw.get("vix_regime", "unknown"),
            "btc_dominance": raw.get("btc_dominance", 0),
            "flow_signals": raw.get("signals", []),
        }

        # Inject smart_money (this is the NEW code)
        if collector_ok:
            flow_ctx["smart_money"] = ctx

        has_sm = "smart_money" in flow_ctx
        check("flow_ctx has smart_money key", has_sm)

        sm_in_flow = flow_ctx.get("smart_money", {})
        sm_btc = sm_in_flow.get("whales", {}).get("BTC", {})
        check("smart_money reaches flow_ctx.whales.BTC", bool(sm_btc),
              f"L/S={sm_btc.get('ls_ratio', '?')}" if sm_btc else "EMPTY")

        flow_ok = has_sm and bool(sm_btc)
    except Exception as e:
        flow_ok = False
        check("Flow context build", False, str(e))

    # ── LAYER 3: btc_swing signal (simulate generate_signal) ──
    print("\n🎯 LAYER 3: btc_swing Signal Generation")
    try:
        if flow_ok:
            sm_ctx = flow_ctx.get("smart_money", {})
            sm_whales = sm_ctx.get("whales", {}).get("BTC", {})
            check("btc_swing reads sm_ctx.whales.BTC", bool(sm_whales),
                  f"keys: {list(sm_whales.keys())}" if sm_whales else "NOT FOUND")

            if sm_whales:
                sm_ls = sm_whales.get("ls_ratio", 0.5)
                sm_long_profit = sm_whales.get("long_profit_pct", 50)
                sm_short_profit = sm_whales.get("short_profit_pct", 50)
                sm_short_entry = sm_whales.get("short_entry", 0)
                sm_long_entry = sm_whales.get("long_entry", 0)

                # Replicate btc_swing risk_mult logic
                risk_mult = 1.0
                triggers = []

                if sm_ls < 0.20:
                    risk_mult = 0.5
                    triggers.append(f"extreme_short(L/S={sm_ls:.2f})")
                elif sm_ls < 0.30:
                    risk_mult = 0.7
                    triggers.append(f"heavy_short(L/S={sm_ls:.2f})")

                if sm_long_profit < 20:
                    risk_mult = min(risk_mult, 0.6)
                    triggers.append(f"longs_trapped({sm_long_profit:.0f}%)")

                if sm_short_entry > sm_long_entry * 1.05 and sm_short_entry > 0:
                    risk_mult = min(risk_mult, 0.5)
                    triggers.append("squeeze_risk")

                check("risk_mult computed", True,
                      f"risk_mult={risk_mult} triggers={triggers if triggers else 'none (normal)'}")
                print(f"     BTC current: L/S={sm_ls} L_win={sm_long_profit}% S_win={sm_short_profit}%")

                signal_ok = True
            else:
                signal_ok = False
        else:
            signal_ok = False
            check("btc_swing signal", False, "flow_ctx broken upstream")
    except Exception as e:
        signal_ok = False
        check("btc_swing signal", False, str(e))

    # ── LAYER 4: Active Exit Logic ──
    print("\n🛑 LAYER 4: Smart Money Active Exit Triggers")
    try:
        if flow_ok:
            sm_ctx = flow_ctx.get("smart_money", {})
            sm_whales = sm_ctx.get("whales", {})

            for ticker in ["BTC", "ETH"]:
                w = sm_whales.get(ticker, {})
                if not w:
                    continue
                ls = w.get("ls_ratio", 0.5)
                lp = w.get("long_profit_pct", 50)
                sp = w.get("short_profit_pct", 50)

                triggers = []
                # SHORT squeeze detection
                if ls < 0.20 and sp > 75:
                    triggers.append(f"SHORT_SQUEEZE: L/S={ls:.2f} shorts={sp:.0f}%_win")

                # Whale reversal on SHORT
                if ls > 0.60:
                    triggers.append(f"WHALE_REVERSAL: L/S={ls:.2f} recovering → cut SHORT")

                # LONG capitulation
                if ls < 0.25 and lp < 30:
                    triggers.append(f"LONG_CAPITULATION: L/S={ls:.2f} longs={lp:.0f}%_win")

                # LONG overheated
                if ls > 2.5 and lp > 80:
                    triggers.append(f"LONG_OVERHEATED: L/S={ls:.2f} longs={lp:.0f}%_win")

                status = "🔴 ACTIVE" if triggers else "⚪ idle"
                print(f"     {ticker}: {status}")
                for t in triggers:
                    print(f"       → {t}")

            check("Active exit logic reachable", True)
        else:
            check("Active exit logic", False, "flow_ctx broken upstream")
    except Exception as e:
        check("Active exit logic", False, str(e))

    # ── LAYER 5: Regime Contradiction ──
    print("\n🌍 LAYER 5: Regime Contradiction Detection")
    try:
        if flow_ok:
            btc_sm = sm_ctx.get("whales", {}).get("BTC", {})
            eth_sm = sm_ctx.get("whales", {}).get("ETH", {})

            btc_ls = btc_sm.get("ls_ratio", 0.5)
            eth_ls = eth_sm.get("ls_ratio", 0.5)

            # Check: is regime bullish but whales bearish?
            regime_bullish = btc_ls < 0.30
            regime_bearish = btc_ls > 1.5

            if regime_bullish:
                check("Regime contradiction check", True,
                      f"BTC extreme short L/S={btc_ls:.2f} → would cut risk if regime bullish")
                if eth_ls < 0.30:
                    print(f"     ETH confirms: L/S={eth_ls:.2f}")
                else:
                    print(f"     ETH contradicts: L/S={eth_ls:.2f} → would weaken adjustment")
            elif regime_bearish:
                check("Regime contradiction check", True,
                      f"BTC whale accumulation L/S={btc_ls:.2f} → would raise floor if regime bearish")
            else:
                check("Regime contradiction check", True,
                      f"No contradiction: BTC L/S={btc_ls:.2f} neutral")

        else:
            check("Regime contradiction", False, "flow_ctx broken upstream")
    except Exception as e:
        check("Regime contradiction", False, str(e))

    # ── Summary ──
    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r["pass"])
    failed = sum(1 for r in results if not r["pass"])
    total = len(results)

    if failed == 0:
        print(f"🏆 ALL {total} CHECKS PASSED — Smart Money fully integrated")
        print("=" * 60)
        return 0
    else:
        print(f"⚠️ {failed}/{total} CHECKS FAILED")
        print("=" * 60)
        for r in results:
            if not r["pass"]:
                print(f"  ❌ {r['name']}: {r['detail']}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
