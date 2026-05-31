#!/usr/bin/env python3
import os, sys, json

WORKSPACE = os.path.expanduser("~/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "capitalcom"))
sys.path.insert(0, os.path.join(WORKSPACE, "scalp_engines"))

PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    icon = PASS if condition else FAIL
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))
    results.append({"name": name, "pass": condition, "detail": detail})

print("=" * 60)
print("🔗 INTEGRATION TRACE: VIX Fetcher Fix")
print("=" * 60)

print("\n📡 LAYER 1: VIX direct fetch (yfinance)")
from engine_base import SwingEngine
import yfinance as yf
try:
    vix = float(yf.Ticker("^VIX").history(period="1d")["Close"].iloc[-1])
    check("yfinance VIX fetch works", vix > 0, f"VIX={vix:.2f}")
except Exception as e:
    check("yfinance VIX fetch", False, str(e))

print("\n🔄 LAYER 2: get_flow_context() fallback")
class Dummy(SwingEngine): pass
d = Dummy()
ctx = d.get_flow_context()
check("Fallback VIX injected", ctx.get("vix", 0) > 0, f"VIX={ctx.get('vix')}")

print("\n📥 LAYER 3: Consumers (Indices & Gold)")
from indices_scalp import IndicesScalpEngine
idx = IndicesScalpEngine()
idx_data = idx.get_market_data()
check("Indices gets live VIX", idx_data.get("vix", 0) > 0, f"VIX={idx_data.get('vix')}")

from gold_scalp import GoldScalpEngine
gld = GoldScalpEngine()
gld_data = gld.get_market_data()
check("Gold gets live VIX", gld_data.get("vix", 0) > 0, f"VIX={gld_data.get('vix')}")

print("\n" + "=" * 60)
passed = sum(1 for r in results if r["pass"])
if passed == len(results):
    print("🏆 ALL CHECKS PASSED")
else:
    print("⚠️ FAILED")
