#!/usr/bin/env python3
"""
🌊 CROSS-MARKET FLOW MONITOR — 跨市場資金流分析
================================================
追蹤資金點樣喺 BTC/ETH/SOL/GOLD/SPX/NDX 之間流動。
目的：偵測 regime shift，獨立 engine 可以因應資金流向調整 bias。

Metrics:
  1. BTC Dominance — BTC 市值佔 crypto 總市值%
  2. ETH/BTC Ratio — alt season 指標
  3. SOL/BTC Ratio — meme season proxy
  4. Crypto vs GOLD correlation — 資金風險偏好
  5. NDX/SPX Ratio — tech vs broad market
  6. VIX regime — 全市場風險指標
  7. DXY proxy (EURUSD) — USD strength

輸出：flow signal JSON → 各 engine 讀取
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENGINES_DIR = os.path.join(WORKSPACE, "scalp_engines")
FLOW_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
os.makedirs(FLOW_DIR, exist_ok=True)

FLOW_FILE = os.path.join(FLOW_DIR, "flow_state.json")
FLOW_HISTORY = os.path.join(FLOW_DIR, "flow_history.json")

sys.path.insert(0, WORKSPACE)
sys.path.insert(0, ENGINES_DIR)
from engine_base import _locked_save_json, _locked_load_json
from capitalcom_paper import get_market_info, get_account_for, _auth

HL_API = "https://api.hyperliquid.xyz/info"


def post_hl(payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        HL_API, data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def get_hl_meta() -> dict:
    """Get all coin prices + funding from Hyperliquid."""
    try:
        ctx = post_hl({"type": "metaAndAssetCtxs"})
        if not ctx or len(ctx) < 2 or "error" in ctx:
            return {}
        universe = ctx[0].get("universe", [])
        assets = ctx[1]
        result = {}
        for i, u in enumerate(universe):
            name = u.get("name", "")
            if name in ("BTC", "ETH", "SOL"):
                result[name] = {
                    "mark": float(assets[i].get("markPx", 0)),
                    "funding": float(assets[i].get("funding", 0)) * 100,
                    "open_interest": float(assets[i].get("openInterest", 0)),
                }
        return result
    except Exception as e:
        return {"error": str(e)}


def get_capital_price(epic: str, label: str) -> float:
    """Get mid price from Capital.com for a macro market."""
    try:
        acct_id = get_account_for(label)
        info = get_market_info(epic, acct_id)
        bid = info.get("bid", 0) or 0
        offer = info.get("offer", 0) or 0
        if bid and offer:
            return (bid + offer) / 2
        return bid or offer or 0
    except Exception:
        return 0


def get_vix() -> float:
    """Get VIX from yfinance."""
    try:
        import yfinance as yf
        t = yf.Ticker("^VIX")
        hist = t.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 0


def collect_flow() -> dict:
    """Collect all cross-market flow metrics."""
    result = {
        "timestamp": datetime.now(HKT).isoformat(),
        "btc_dominance": 0,
        "eth_btc_ratio": 0,
        "sol_btc_ratio": 0,
        "vix": 0,
        "vix_regime": "unknown",
        "ndx_spx_ratio": 0,
        "eurusd": 0,
        "gold": 0,
        "signals": [],
    }

    # === Crypto Layer ===
    hl = get_hl_meta()
    if hl and "error" not in hl:
        btc = hl.get("BTC", {})
        eth = hl.get("ETH", {})
        sol = hl.get("SOL", {})

        btc_price = btc.get("mark", 0)
        eth_price = eth.get("mark", 0)
        sol_price = sol.get("mark", 0)

        if btc_price:
            if eth_price:
                result["eth_btc_ratio"] = round(eth_price / btc_price, 6)
            if sol_price:
                result["sol_btc_ratio"] = round(sol_price / btc_price, 6)

            # BTC Dominance (simplified: BTC / (BTC+ETH+SOL))
            total = btc_price + eth_price + sol_price
            if total > 0:
                result["btc_dominance"] = round(btc_price / total * 100, 1)

        # Funding signals
        btc_funding = btc.get("funding", 0)
        eth_funding = eth.get("funding", 0)
        sol_funding = sol.get("funding", 0)

        if btc_funding > 0.02:
            result["signals"].append("BTC_FUNDING_OVERHEATED")
        if eth_funding < -0.01:
            result["signals"].append("ETH_FUNDING_SHORT_CROWDED")
        if sol_funding > 0.03:
            result["signals"].append("SOL_FUNDING_EXTREME")

    # === Macro Layer ===
    vix = get_vix()
    result["vix"] = round(vix, 2) if vix else 0

    if vix:
        if vix < 15:
            result["vix_regime"] = "complacency"
        elif vix < 20:
            result["vix_regime"] = "low"
        elif vix < 25:
            result["vix_regime"] = "elevated"
        elif vix < 30:
            result["vix_regime"] = "high"
        else:
            result["vix_regime"] = "fear"
            result["signals"].append("VIX_FEAR")

    # NDX/SPX ratio
    ndx = get_capital_price("US100", "NDX")
    spx = get_capital_price("US500", "SPX")
    if ndx and spx:
        result["ndx_spx_ratio"] = round(ndx / spx, 4)
        # Previous for trend
        prev = _locked_load_json(FLOW_FILE, {})
        prev_ratio = prev.get("ndx_spx_ratio", result["ndx_spx_ratio"])
        if result["ndx_spx_ratio"] > prev_ratio * 1.002:
            result["signals"].append("TECH_OUTPERFORMING")
        elif result["ndx_spx_ratio"] < prev_ratio * 0.998:
            result["signals"].append("BROAD_MARKET_ROTATION")

    # EURUSD (DXY proxy)
    eurusd = get_capital_price("EURUSD", "EURUSD")
    result["eurusd"] = round(eurusd, 5) if eurusd else 0
    # Calculate EURUSD change percentage vs previous state
    prev_flow = _locked_load_json(FLOW_FILE, {})
    prev_eurusd = prev_flow.get("eurusd", 0)
    if prev_eurusd and eurusd:
        result["eurusd_change_pct"] = round((eurusd - prev_eurusd) / prev_eurusd, 6)
    else:
        result["eurusd_change_pct"] = 0.0

    # GOLD
    gold = get_capital_price("GOLD", "XAUUSD")
    result["gold"] = round(gold, 2) if gold else 0

    # === Cross-Asset Signals ===
    if result["vix_regime"] == "fear" and result["gold"] > 0:
        prev_gold = _locked_load_json(FLOW_FILE, {}).get("gold", result["gold"])
        if result["gold"] > prev_gold * 1.005:
            result["signals"].append("RISK_OFF_FLOW_TO_GOLD")

    if result["btc_dominance"] > 55:
        result["signals"].append("BTC_DOMINANCE_HIGH")
    elif result["btc_dominance"] < 45:
        result["signals"].append("ALT_SEASON_SIGNAL")

    return result


def run_flow_monitor():
    """Main entry: collect + persist flow data."""
    flow = collect_flow()

    # Save current state with file locking
    _locked_save_json(FLOW_FILE, flow)

    # Append to history (keep last 1000 entries) with file locking
    history = _locked_load_json(FLOW_HISTORY, [])
    history.append(flow)
    if len(history) > 1000:
        history = history[-1000:]
    _locked_save_json(FLOW_HISTORY, history)

    # Print summary
    print(f"[{flow['timestamp'][11:19]}] 🌊 Flow Monitor")
    print(f"  VIX: {flow['vix']:.1f} ({flow['vix_regime']})")
    print(f"  BTC Dominance: {flow['btc_dominance']:.1f}%")
    print(f"  ETH/BTC: {flow['eth_btc_ratio']:.6f}")
    print(f"  SOL/BTC: {flow['sol_btc_ratio']:.6f}")
    print(f"  NDX/SPX: {flow['ndx_spx_ratio']:.4f}")
    print(f"  EURUSD: {flow['eurusd']:.5f}  GOLD: {flow['gold']:.2f}")
    if flow["signals"]:
        print(f"  ⚠️ Signals: {', '.join(flow['signals'])}")

    return flow


def get_latest_flow() -> dict:
    """Get the most recent flow data (for engines to read)."""
    return _locked_load_json(FLOW_FILE, {})


def _load_json(path: str, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: str, data):
    # Atomic write：先寫 .tmp，再用 os.replace 原子性取代原檔，避免寫入一半 crash 產生損毀 JSON
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


if __name__ == "__main__":
    _auth()
    run_flow_monitor()
