#!/usr/bin/env python3
"""
🏦 CAPITAL.COM DEMO API — Paper Trading 整合模組
================================================
用 Capital.com demo API 真實落 order + track P&L。

Accounts (TEST — updated 2026-05-24):
  加密貨幣TEST ($1,000) — accountId: 321895128687259934
  美股TEST ($10,000)     — accountId: 321895132982227230
  指數TEST ($1,000)      — accountId: 321895137277194526
  黃金TEST ($1,000)      — accountId: 321895124392292638

EPIC mapping:
  US Stocks: ticker = EPIC (AAPL, NVDA, etc.)
  Crypto: BTC -> BTCUSD, ETH -> ETHUSD
  Gold: XAUUSD -> GOLD
  SPY/QQQ -> SPY, US500 (QQQ not available, use US500)
  Sectors: XLK, SMH etc. -> may not be available on Capital.com
"""

import os
import sys
import json
import base64
import time
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

load_dotenv(dotenv_path=os.path.expanduser('~/crypto_quant/.env'))

API_KEY = os.getenv('CAPITALCOM_API_KEY')
PASSWORD = os.getenv('CAPITALCOM_PASSWORD')
LOGIN = os.getenv('CAPITALCOM_LOGIN')

DEMO_URL = 'https://demo-api-capital.backend-capital.com'

ACCOUNTS = {
    "crypto": "321895128687259934",    # 加密貨幣TEST $1,000
    "equities": "321895132982227230",  # 美股TEST $10,000
    "indices": "321895137277194526",   # 指數TEST $1,000 (SPX/NDX)
    "gold": "321895124392292638",      # 黃金TEST $1,000 (XAUUSD)
}

# Market → account routing
MARKET_ACCOUNT = {
    "BTC": "crypto", "ETH": "crypto", "SOL": "crypto",
    "BTCUSD": "crypto", "ETHUSD": "crypto",
    "XAUUSD": "gold", "GOLD": "gold",
    "SPX": "indices", "US500": "indices", "NDX": "indices", "US100": "indices",
    "US TECH 100": "indices",
    "INDICES": "indices",
    "EURUSD": "indices",
    # Equities — everything else goes to equities
}

# EPIC mapping: our ticker → Capital.com EPIC
EPIC_MAP = {
    # US Stocks (EPIC = ticker for most)
    "AAPL": "AAPL", "MSFT": "MSFT", "NVDA": "NVDA", "GOOGL": "GOOGL",
    "AMZN": "AMZN", "META": "META", "TSLA": "TSLA", "AMD": "AMD",
    "AVGO": "AVGO", "CRM": "CRM", "UBER": "UBER", "PLTR": "PLTR",
    "INTC": "INTC", "MU": "MU", "MRVL": "MRVL", "ARM": "ARM",
    "LRCX": "LRCX", "TXN": "TXN", "DELL": "DELL", "AMAT": "AMAT",
    "ADI": "ADI", "CAT": "CAT", "DDOG": "DDOG", "CRWD": "CRWD",
    "QCOM": "QCOM", "SBUX": "SBUX", "GS": "GS", "XOM": "XOM",
    "CVX": "CVX", "DIS": "DIS", "PYPL": "PYPL", "COIN": "COIN",
    "HOOD": "HOOD", "SMCI": "SMCI", "NFLX": "NFLX", "SPOT": "SPOT",
    "SNAP": "SNAP", "PINS": "PINS", "RBLX": "RBLX", "ABNB": "ABNB",
    "DASH": "DASH", "JPM": "JPM", "BAC": "BAC", "WFC": "WFC",
    "V": "VISA", "MA": "MASTERCARD", "AXP": "AXP", "BLK": "BLK",
    "SCHW": "SCHW", "LLY": "LLY", "UNH": "UNH", "JNJ": "JNJ",
    "PFE": "PFE", "ABBV": "ABBV", "MRK": "MRK", "TMO": "TMO",
    "ISRG": "ISRG", "VRTX": "VRTX", "COST": "COST", "HD": "HD",
    "WMT": "WMT", "NKE": "NKE", "LULU": "LULU", "CMG": "CMG",
    "BA": "BA", "GE": "GE", "RTX": "RTX", "LMT": "LMT",
    "RIVN": "RIVN", "LCID": "LCID",
    # Crypto
    "BTC": "BTCUSD", "ETH": "ETHUSD",
    # Gold & Indices
    "XAUUSD": "GOLD", "GOLD": "GOLD",
    "SPY": "SPY", "QQQ": "US500", "US500": "US500",
    "US100": "US100", "SPX": "US500", "NDX": "US100",
    "US TECH 100": "US100",
}

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
CAPITAL_DIR = os.path.join(WORKSPACE, "capitalcom")
os.makedirs(CAPITAL_DIR, exist_ok=True)

# === UNIFIED: All paper trades → engines/paper_trades.json (engine format) ===
TRACKER_FILE = os.path.join(WORKSPACE, "scalp_lab", "engines", "paper_trades.json")
os.makedirs(os.path.dirname(TRACKER_FILE), exist_ok=True)
HISTORY_FILE = os.path.join(CAPITAL_DIR, "capital_history.json")

_session_cache = {"cst": None, "xst": None, "expires": 0}


def _auth():
    """Get or refresh CST + XST tokens."""
    now = time.time()
    if _session_cache["cst"] and now < _session_cache["expires"] - 60:
        return _session_cache["cst"], _session_cache["xst"]
    
    headers = {'X-CAP-API-KEY': API_KEY}
    ek_resp = requests.get(f'{DEMO_URL}/api/v1/session/encryptionKey', headers=headers, timeout=10)
    ek_resp.raise_for_status()
    ek_data = ek_resp.json()
    
    key_der = base64.b64decode(ek_data['encryptionKey'])
    public_key = serialization.load_der_public_key(key_der)
    msg = f'{PASSWORD}|{ek_data["timeStamp"]}'
    enc = base64.b64encode(public_key.encrypt(msg.encode('utf-8'), padding.PKCS1v15())).decode()
    
    sess_resp = requests.post(f'{DEMO_URL}/api/v1/session', json={
        'identifier': LOGIN, 'password': enc, 'encryptedPassword': True
    }, headers=headers, timeout=10)
    sess_resp.raise_for_status()
    
    _session_cache["cst"] = sess_resp.headers.get('CST')
    _session_cache["xst"] = sess_resp.headers.get('X-SECURITY-TOKEN')
    _session_cache["expires"] = now + 3600
    
    print(f"[capitalcom] ✅ Authenticated (CST: {_session_cache['cst'][:15]}...)")
    return _session_cache["cst"], _session_cache["xst"]


def _headers(account_id: str = None):
    """Get authenticated headers, optionally switching account."""
    cst, xst = _auth()
    h = {'X-CAP-API-KEY': API_KEY, 'CST': cst, 'X-SECURITY-TOKEN': xst}
    
    if account_id:
        # Switch to specific account
        r = requests.put(f'{DEMO_URL}/api/v1/session', json={'accountId': account_id}, headers=h, timeout=10)
        if r.status_code == 200:
            new_cst = r.headers.get('CST')
            if new_cst:
                h['CST'] = new_cst
                _session_cache["cst"] = new_cst
    
    return h


def get_epic(ticker: str) -> str:
    """Map our ticker → Capital.com EPIC."""
    epic = EPIC_MAP.get(ticker.upper(), ticker.upper())
    return epic


def get_account_for(market: str) -> str:
    """Return the correct account ID for a market.
    
    Args:
        market: ticker, epic, or account key ('crypto'/'equities'/'macro')
    """
    # Direct account key
    if market in ACCOUNTS:
        return ACCOUNTS[market]
    # Market ticker routing
    acct_key = MARKET_ACCOUNT.get(market, "equities")
    return ACCOUNTS[acct_key]


def get_market_info(epic: str, account_id: str) -> dict:
    """Get market details (min size, spread, etc.)."""
    h = _headers(account_id)
    r = requests.get(f'{DEMO_URL}/api/v1/markets/{epic}', headers=h, timeout=10)
    if r.status_code == 200:
        data = r.json()
        snap = data.get('snapshot', {})
        return {
            "epic": epic,
            "name": data.get('instrumentName', epic),
            "bid": snap.get('bid'),
            "offer": snap.get('offer'),
            "status": data.get('marketStatus', '?'),
            "min_size": data.get('minDealSize', {}).get('value', 1),
            "value_per_pip": data.get('valueOfOnePip'),
        }
    return {"epic": epic, "error": f"HTTP {r.status_code}: {r.text[:200]}"}


def calculate_equities_size(balance: float, confidence_str: str, rr: float, 
                           vix: float = 20, max_positions: int = 5, 
                           max_total_exposure: float = 0.30) -> tuple:
    """
    Smart position sizing for equities.
    
    Returns: (risk_amount, size_multiplier_description)
    
    Multipliers:
      Confidence: 2/5→0.5x, 3/5→1x, 4/5→1.5x, 5/5→2x
      VIX: <15→0.8x, 15-25→1x, 25-30→0.7x, >30→0.3x
      R:R: <1.5→0.5x, 1.5-2.5→1x, >2.5→1.3x
    """
    # Parse confidence score
    conf_score = int(confidence_str[0]) if confidence_str and confidence_str[0].isdigit() else 3
    
    # Base risk: 1% of balance per trade
    base_risk_pct = 0.01
    
    # Confidence multiplier (higher conviction → bigger bet)
    conf_mult = {1: 0.3, 2: 0.5, 3: 1.0, 4: 1.5, 5: 2.0}.get(conf_score, 1.0)
    
    # VIX multiplier (higher fear → smaller bets)
    if vix > 30:
        vix_mult = 0.3
        vix_note = "VIX>30 恐慌 — 倉位×0.3"
    elif vix > 25:
        vix_mult = 0.5
        vix_note = "VIX>25 偏高 — 倉位×0.5"
    elif vix < 15:
        vix_mult = 0.8
        vix_note = "VIX<15 自滿 — 倉位×0.8"
    else:
        vix_mult = 1.0
        vix_note = "VIX正常"
    
    # R:R multiplier (better R:R → slightly bigger)
    if rr >= 3.0:
        rr_mult = 1.3
    elif rr >= 1.5:
        rr_mult = 1.0
    else:
        rr_mult = 0.5
    
    # Max positions cap: if 5 already open → reduce new position size
    existing_positions = len(check_positions(ACCOUNTS["equities"]))
    position_cap_mult = max(0.2, 1.0 - (existing_positions / max_positions))
    
    # Total exposure cap
    total_exposure = sum(
        p.get("size", 0) * p.get("current", p.get("entry", 0))
        for p in check_positions(ACCOUNTS["equities"])
    )
    exposure_remaining = max(0, balance * max_total_exposure - total_exposure)
    exposure_cap_mult = min(1.0, exposure_remaining / (balance * base_risk_pct * 100) if balance > 0 else 1.0)
    exposure_cap_mult = max(0.1, exposure_cap_mult)
    
    # Combined risk
    risk_amount = balance * base_risk_pct * conf_mult * vix_mult * rr_mult * position_cap_mult * exposure_cap_mult
    risk_amount = max(risk_amount, balance * 0.002)  # Min 0.2% risk (avoids tiny dust positions)
    risk_amount = min(risk_amount, balance * 0.03)    # Max 3% risk per trade
    
    desc = (
        f"Base 1% → Conf ×{conf_mult:.1f} → VIX ×{vix_mult:.1f} → R:R ×{rr_mult:.1f} "
        f"→ PosCap ×{position_cap_mult:.1f} → ExpCap ×{exposure_cap_mult:.1f} = ${risk_amount:.2f} risk"
    )
    
    return risk_amount, desc


def open_position(ticker: str, direction: str, entry: float, stop: float, target: float, 
                  market: str = "equities", size: float = None,
                  confidence: str = "3/5 🔥", vix: float = 20,
                  max_total_exposure: float = 0.30,
                  paper_balance: float = None) -> dict:
    """
    Open a paper position on Capital.com demo.
    
    Args:
        ticker: Our ticker (NVDA, BTC, etc.)
        direction: LONG or SHORT
        entry: Entry price (used for reference)
        stop: Stop loss price
        target: Take profit price (limit order)
        market: 'crypto' or 'equities'
        size: Position size in units. If None, auto-calculate (1% risk of account balance)
        paper_balance: 用於 paper_only sizing。如果 None，用 full account balance。
                       傳入時應為 account_balance - Capital.com 已用資金。
    
    Returns: order dict with dealReference
    """
    account_id = get_account_for(market)
    epic = get_epic(ticker)
    h = _headers(account_id)
    
    # Get market info
    info = get_market_info(epic, account_id)
    if "error" in info:
        err_msg = info["error"]
        # 📝 Stock 唔喺 Capital.com → 轉做 paper-only virtual trade
        if "404" in str(err_msg) or "not found" in str(err_msg).lower() or "instrument" in str(err_msg).lower():
            print(f"  📝 {ticker}: 唔喺 Capital.com → 記錄為 paper-only virtual trade")
            
            # 📐 Paper-only sizing: 用 paper_balance（= account_balance - Capital.com 已用）
            #    如果冇傳 paper_balance，用預設 $10,000
            p_balance = paper_balance if paper_balance is not None else 10000.0
            
            # Auto-calculate size if not provided
            if size is None or size <= 0:
                risk_amount = p_balance * 0.01  # 1% risk
                risk_per_unit = abs(entry - stop) if direction == "LONG" else abs(stop - entry)
                if risk_per_unit <= 0:
                    risk_per_unit = entry * 0.02
                calc_size = risk_amount / risk_per_unit
                calc_size = round(calc_size, 4)
                print(f"     💰 Paper pool: ${p_balance:.0f} | 1% risk=${risk_amount:.2f} → size={calc_size}")
                size = calc_size
            
            now = datetime.now(HKT)
            order_record = {
                "deal_ref": f"paper_{ticker}_{int(time.time())}",
                "deal_id": "paper_only",
                "ticker": ticker,
                "epic": epic,
                "direction": direction,
                "entry": entry,           # 用建議價（無真實成交）
                "stop": stop,
                "target": target,
                "size": size,
                "market": market,
                "account_id": account_id,
                "opened_at": now.isoformat(),
                "status": "open",
                "paper_only": True,       # ⚠️ 虛擬 paper trade，無真實 Capital.com 執行
                "paper_reason": f"Capital.com 無此股票: {err_msg[:100]}",
                "paper_balance_used": p_balance,  # 🔍 記錄 paper pool 大小
            }
            return order_record
        else:
            print(f"  ❌ {ticker}: {info['error']}")
            return {"error": info["error"]}
    
    # Auto-calculate size if not provided
    if size is None:
        # Get balance
        acct_resp = requests.get(f'{DEMO_URL}/api/v1/accounts', headers=h, timeout=10)
        balance = 1000.0
        if acct_resp.status_code == 200:
            accounts = acct_resp.json().get('accounts', [])
            for a in accounts:
                if a.get('accountId') == account_id:
                    balance = float(a.get('balance', {}).get('balance', 1000))
                    break
        
        if market == "equities":
            # Smart sizing: confidence × VIX × R:R × position cap × exposure cap
            rr = (target - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 1
            if direction == "SHORT":
                rr = (entry - target) / abs(stop - entry) if abs(stop - entry) > 0 else 1
            risk_amount, sizing_desc = calculate_equities_size(balance, confidence, rr, vix,
                                                               max_total_exposure=max_total_exposure)
            print(f"  📐 Sizing: {sizing_desc}")
        else:
            # Crypto: simple 1% risk (scalp mode → smaller)
            risk_amount = balance * 0.01
        
        if direction == "LONG":
            risk_per_unit = abs(entry - stop)
        else:
            risk_per_unit = abs(stop - entry)
        
        if risk_per_unit <= 0:
            risk_per_unit = entry * 0.02
        
        size = risk_amount / risk_per_unit
        size = max(size, float(info.get('min_size', 1)))
        size = round(size, 4)
    
    # Capital.com uses CFD contracts
    # For stocks, size = number of shares
    # For crypto, size = number of contracts
    
    cap_direction = "BUY" if direction == "LONG" else "SELL"
    
    order_payload = {
        "epic": epic,
        "expiry": "-",  # Daily funded bet (no expiry)
        "direction": cap_direction,
        "size": size,
        "orderType": "MARKET",
        "guaranteedStop": False,
        "stopLevel": round(stop, 2),
        "profitLevel": round(target, 2),  # Capital.com uses profitLevel, NOT limitLevel!
        "currencyCode": "USD",
        "forceOpen": True,
    }
    
    try:
        r = requests.post(f'{DEMO_URL}/api/v1/positions', json=order_payload, headers=h, timeout=15)
        if r.status_code == 200:
            data = r.json()
            deal_ref = data.get('dealReference', '?')
            deal_id = data.get('dealId', '?')
            
            # 🔧 從 Capital.com API response 攞真實 fill price
            # POST /positions 成功回應包含 actual level (成交價)
            actual_entry = float(data.get('level', 0))
            if actual_entry <= 0:
                # Fallback: try nested structure
                pos = data.get('position', {})
                actual_entry = float(pos.get('level', 0))
            if actual_entry <= 0:
                # ⚠️ No fill price in response — use plan entry with warning
                actual_entry = entry
                print(f"  ⚠️ {ticker}: No fill price in API response, using plan entry=${entry}")
            else:
                print(f"  📍 {ticker}: Plan entry=${entry:.2f} → Actual fill=${actual_entry:.2f} (Δ={actual_entry-entry:+.2f})")
            
            order_record = {
                "deal_ref": deal_ref,
                "deal_id": deal_id,
                "ticker": ticker,
                "epic": epic,
                "direction": direction,
                "entry": actual_entry,       # ✅ 真實成交價，唔係建議價
                "stop": stop,
                "target": target,
                "size": size,
                "market": market,
                "account_id": account_id,
                "opened_at": datetime.now(HKT).isoformat(),
                "status": "open",
            }
            
            # Save to tracker
            _save_order(order_record)
            
            print(f"  ✅ {ticker} {direction} | {size:.4f} units | Stop ${stop:.2f} | Target ${target:.2f} | Ref: {deal_ref}")
            return order_record
        else:
            print(f"  ⚠️ {ticker}: HTTP {r.status_code} — verifying position...")
            # ⛔ API returned non-200, but Capital.com may have executed anyway.
            # Wait 2s then verify by checking open positions (deal_id may be in response body).
            import time as _time
            _time.sleep(2)
            # Try to extract deal_id from error response
            deal_ref = "?"
            deal_id = "?"
            try:
                err_data = r.json()
                deal_ref = err_data.get('dealReference', '?')
                deal_id = err_data.get('dealId', '?')
            except:
                pass
            
            # Verify: check if position actually exists on Capital.com
            positions = check_positions(account_id)
            matched = None
            for p in positions:
                p_epic = p.get('epic', '')
                p_dir = p.get('direction', '')
                p_size = p.get('size', 0)
                if p_epic == epic and p_dir == direction and abs(p_size - size) < size * 0.1:
                    matched = p
                    if deal_id == '?':
                        deal_id = p.get('deal_id', '?')
                    if deal_ref == '?':
                        deal_ref = p.get('deal_ref', '?')
                    break
            
            if matched:
                # 🔧 從 matched position 攞真實 fill price
                actual_entry = float(matched.get('entry', entry))
                print(f"  ✅ {ticker}: Position confirmed despite HTTP {r.status_code} (deal_id={deal_id})")
                print(f"  📍 {ticker}: Plan entry=${entry:.2f} → Actual fill=${actual_entry:.2f}")
                order_record = {
                    "deal_ref": deal_ref,
                    "deal_id": deal_id,
                    "ticker": ticker,
                    "epic": epic,
                    "direction": direction,
                    "entry": actual_entry,       # ✅ 真實成交價
                    "stop": stop,
                    "target": target,
                    "size": size,
                    "market": market,
                    "account_id": account_id,
                    "opened_at": datetime.now(HKT).isoformat(),
                    "status": "open",
                }
                _save_order(order_record)
                return order_record
            else:
                print(f"  ❌ {ticker}: Position NOT found after API error — order likely failed")
                return {"error": f"HTTP {r.status_code}: {r.text[:150]}"}
    
    except Exception as e:
        print(f"  ❌ {ticker}: {e}")
        return {"error": str(e)}


def check_positions(account_id: str = None) -> list:
    """Get all open positions and their current P&L."""
    if account_id is None:
        account_id = ACCOUNTS["equities"]
    
    h = _headers(account_id)
    r = requests.get(f'{DEMO_URL}/api/v1/positions', headers=h, timeout=10)
    
    if r.status_code != 200:
        print(f"  ⚠️ Check failed: {r.status_code}")
        return []
    
    positions = r.json().get('positions', [])
    results = []
    
    for p in positions:
        deal_id = p.get('position', {}).get('dealId', '?')
        epic = p.get('market', {}).get('epic', '?')
        cap_dir = p.get('position', {}).get('direction', '?')
        size = p.get('position', {}).get('size', 0)
        
        # Reverse-map EPIC → our ticker
        ticker = epic
        for t, e in EPIC_MAP.items():
            if e == epic:
                ticker = t
                break
        
        pos = {
            "deal_id": deal_id,
            "ticker": ticker,
            "epic": epic,
            "direction": "LONG" if cap_dir == "BUY" else "SHORT",
            "size": size,
            "entry": float(p.get('position', {}).get('level', 0)),
            "current": float(p.get('market', {}).get('bid', 0)),
            "pnl_amt": float(p.get('position', {}).get('upl', 0)),
            "pnl_pct": 0,
            "stop": float(p.get('position', {}).get('stopLevel', 0)) if p.get('position', {}).get('stopLevel') else 0,
            "limit": float(p.get('position', {}).get('profitLevel', 0)) if p.get('position', {}).get('profitLevel') else 0,
        }
        
        if pos["entry"] > 0:
            pos["pnl_pct"] = round(pos["pnl_amt"] / (pos["entry"] * size) * 100, 2)
        
        results.append(pos)
    
    # Also log closed positions for tracking
    _log_closed_positions(results, account_id)
    
    return results


def _log_closed_positions(current_positions: list, account_id: str):
    """Detect and log positions that were closed since last check.
    Updates paper_trades.json (unified engine format).
    
    🔒 V2 FIX: Only closes entries that belong to the current account,
    AND skips reconciled entries (managed by Guardian/sync).
    Previously this function would kill ALL non-matching entries across
    all accounts whenever check_positions() was called for a single account.
    """
    import os as _os
    log_path = TRACKER_FILE  # Unified: same file as _save_order
    if not _os.path.exists(log_path):
        return

    try:
        with open(log_path, "r") as f:
            trades = json.load(f)
    except Exception:
        return

    current_deal_ids = {p.get("deal_id", "") for p in current_positions}
    updated = False

    for t in trades:
        if t.get("status") != "open":
            continue
        # 🔒 NEVER close reconciled entries (managed by Guardian/sync)
        if t.get("reconciled"):
            continue
        deal_id = t.get("deal_id", "")
        if deal_id and deal_id not in current_deal_ids and deal_id != "?":
            # 🔒 Only close if this entry belongs to the current account
            entry_account = t.get("account", "")
            # Map account_id to account name
            account_name = next((k for k, v in ACCOUNTS.items() if v == account_id), None)
            if account_name and entry_account != account_name:
                continue  # Skip — this entry belongs to a different account
            # Position closed! Record exit
            t["status"] = "closed"
            t["closed_at"] = datetime.now(HKT).isoformat()
            # Compute per-position P&L from trade data, NOT total account P&L
            if t.get("entry", 0) > 0 and t.get("size", 0) > 0:
                if t["direction"] == "LONG":
                    exit_est = t.get("target", t["entry"]) or t["entry"]
                    pnl = (exit_est - t["entry"]) * t["size"]
                else:
                    exit_est = t.get("stop", t["entry"]) or t["entry"]
                    pnl = (t["entry"] - exit_est) * t["size"]
            else:
                pnl = 0
            t["pnl"] = round(pnl, 2)
            t["adjusted_pnl"] = t["pnl"]
            t["raw_pnl"] = t["pnl"]
            t["result"] = "win" if t.get("pnl", 0) > 0 else "loss"
            updated = True
            print(f"  📝 Closed: {t.get('label', t.get('epic','?'))} {t.get('direction','')} | Result: {t['result']} | P&L: ${t['pnl']:+.2f}")

    if updated:
        # Atomic write
        tmp = log_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(trades, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, log_path)


def close_position(deal_id: str, account_id: str = None):
    """Close a position by deal ID."""
    if account_id is None:
        account_id = ACCOUNTS["equities"]
    
    h = _headers(account_id)
    r = requests.delete(f'{DEMO_URL}/api/v1/positions/{deal_id}', headers=h, timeout=10)
    
    if r.status_code == 200:
        data = r.json()
        print(f"  ✅ Closed {deal_id}: {data.get('dealReference', '?')}")
        return data
    else:
        print(f"  ❌ Close failed: {r.status_code} {r.text[:200]}")
        return {"error": r.text[:200]}


def update_stop_loss(deal_id: str, stop_loss: float, take_profit: float = None, account_id: str = None) -> dict:
    """Update stop loss (and optionally take profit) on an open position.
    
    Capital.com API: PUT /api/v1/positions/{deal_id}
    Body: {"stopLoss": float, "takeProfit": float (optional)}
    """
    if account_id is None:
        account_id = ACCOUNTS["equities"]
    
    h = _headers(account_id)
    body = {"stopLoss": round(stop_loss, 2)}
    if take_profit is not None:
        body["takeProfit"] = round(take_profit, 2)
    
    # Capital.com requires these headers for PUT
    h["Content-Type"] = "application/json"
    h["Version"] = "2"
    
    # Capital.com uses "stopLevel" and "profitLevel" in PUT body
    body = {"stopLevel": round(stop_loss, 2)}
    if take_profit is not None:
        body["profitLevel"] = round(take_profit, 2)
    
    r = requests.put(
        f'{DEMO_URL}/api/v1/positions/{deal_id}',
        headers=h,
        json=body,
        timeout=15
    )
    
    if r.status_code == 200:
        # Verify by reading back
        data = r.json()
        deal_ref = data.get('dealReference', deal_id[:20])
        print(f"  ✅ SL set: {deal_ref} stopLevel=${stop_loss:.2f}" +
              (f" profitLevel=${take_profit:.2f}" if take_profit else ""))
        return data
    else:
        print(f"  ❌ SL update failed ({r.status_code}): {r.text[:200]}")
        return {"error": r.text[:200]}


def get_account_balance(account_id: str) -> dict:
    """Get account balance info."""
    h = _headers(account_id)
    r = requests.get(f'{DEMO_URL}/api/v1/accounts', headers=h, timeout=10)
    if r.status_code == 200:
        accounts = r.json().get('accounts', [])
        for a in accounts:
            if a.get('accountId') == account_id:
                return {
                    "name": a.get('accountName', '?'),
                    "balance": float(a.get('balance', {}).get('balance', 0)),
                    "deposit": float(a.get('balance', {}).get('deposit', 0)),
                    "pnl": float(a.get('balance', {}).get('profitLoss', 0)),
                    "available": float(a.get('balance', {}).get('available', 0)),
                }
    return {}


def _to_engine_format(order: dict) -> dict:
    """Convert raw Capital.com order into engine trade format."""
    now = datetime.now(HKT)
    try:
        ts_ts = time.mktime(
            datetime.fromisoformat(order.get("opened_at", now.isoformat())).timetuple()
        ) if order.get("opened_at") else time.time()
    except (ValueError, TypeError):
        ts_ts = time.time()
    try:
        closed_ts = time.mktime(
            datetime.fromisoformat(order["closed_at"]).timetuple()
        ) if order.get("closed_at") else 0
    except (ValueError, TypeError):
        closed_ts = 0
    return {
        "timestamp": order.get("opened_at", now.isoformat()),
        "timestamp_ts": ts_ts,
        "market": order.get("market", "paper").upper(),
        "engine": "paper",
        "strategy": order.get("strategy", "manual"),
        "epic": order.get("epic", ""),
        "direction": order.get("direction", ""),
        "entry": order.get("entry", 0),
        "stop": order.get("stop", 0),
        "target": order.get("target", 0),
        "size": order.get("size", 0),
        "deal_ref": order.get("deal_ref", ""),
        "deal_id": order.get("deal_id", ""),
        "status": order.get("status", "open"),
        "pnl": order.get("pnl", 0.0),
        "adjusted_pnl": order.get("pnl", 0.0),
        "raw_pnl": order.get("pnl", 0.0),
        "slippage_cost": 0.0,
        "slippage_applied": False,
        "exit_price": order.get("exit_price", 0.0),
        "closed_at": order.get("closed_at", ""),
        "closed_at_ts": closed_ts,
        "account": order.get("account", "paper"),
        "reason": order.get("reason", ""),
        "highest_price": order.get("entry", 0),
        "lowest_price": order.get("entry", 0),
        "trailing_active": False,
        "time_stop_minutes": 240,
    }


def _save_order(order: dict):
    """Save order to unified paper_trades.json in engine format."""
    trades = []
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, "r") as f:
                trades = json.load(f)
        except (json.JSONDecodeError, OSError):
            trades = []
    engine_trade = _to_engine_format(order)
    # Deduplicate by deal_ref
    deal_ref = engine_trade.get("deal_ref", "")
    if deal_ref and any(t.get("deal_ref") == deal_ref for t in trades):
        return  # Already written
    trades.append(engine_trade)
    # Atomic write: .tmp → os.replace (防 corrupt)
    tmp = TRACKER_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, TRACKER_FILE)


def open_all_from_setups(equities_file: str = None, min_confidence: int = 2):
    """
    Read setups from equities_engine.json + crypto data, 
    open positions on Capital.com for all qualifying setups.
    """
    if equities_file is None:
        equities_file = os.path.join(os.path.expanduser("~/workspace"), "equities_data", "equities_setups.json")
    
    now = datetime.now(HKT)
    print("=" * 60)
    print("🏦 CAPITAL.COM PAPER TRADING — Open Setups")
    print(f"   {now.strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 60)
    
    results = {"equities": [], "crypto": []}
    
    # ── Equities with smart sizing ──
    if os.path.exists(equities_file):
        with open(equities_file, "r") as f:
            eq_data = json.load(f)
        
        vix = eq_data.get("macro", {}).get("vix", 20)
        
        print(f"\\n📊 Equities setups ({len(eq_data.get('setups', []))} total, VIX={vix}):")
        opened_count = 0
        seen_sectors = set()
        for s in eq_data.get("setups", []):
            if opened_count >= 2:  # 🆕 最多開 2 個倉
                print(f"  🛑 Max positions (2) reached, stopping")
                break
            conf_score = int(s.get("confidence", "0")[0]) if s.get("confidence") else 0
            if conf_score < min_confidence:
                continue
            
            ticker = s["ticker"]
            sector = s.get("sector", "Unknown")
            if sector in seen_sectors:  # 🆕 每板塊最多 1 倉
                print(f"  ⏭️  {ticker} — sector '{sector}' already taken, skip")
                continue
            seen_sectors.add(sector)
            
            existing = check_positions(ACCOUNTS["equities"])
            already_open = any(p["ticker"] == ticker for p in existing)
            if already_open:
                print(f"  ⏭️  {ticker} — already open, skip")
                continue
            
            r = open_position(
                ticker=ticker,
                direction=s.get("direction", "LONG"),
                entry=s["entry"],
                stop=s["stop"],
                target=s["target"],
                market="equities",
                confidence=s.get("confidence", "3/5 🔥"),
                vix=vix,
            )
            if "error" not in r:
                opened_count += 1
            results["equities"].append({"ticker": ticker, "result": "ok" if "error" not in r else r.get("error")})
    
    # ── Crypto ── (now handled by scalp_runner.py — 每30分鐘高頻掃描)
    # The Commander's Briefing still analyzes crypto but execution is separate
    print(f"\n🪙 Crypto: Handled by scalp_runner (every 30 min scan)")
    print(f"   Run: python3 scalp_runner.py --status for current positions")
    
    # ── Summary ──
    eq_ok = sum(1 for r in results["equities"] if r["result"] == "ok")
    cr_ok = sum(1 for r in results["crypto"] if r["result"] == "ok")
    print(f"\n📊 Opened: {eq_ok} equities + {cr_ok} crypto | {now.strftime('%H:%M')}")
    
    return results


def check_all():
    """Check all positions across both accounts."""
    print("=" * 60)
    print("🏦 CAPITAL.COM — Position Status")
    print(f"   {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 60)
    
    for name, acct_id in [("美股", ACCOUNTS["equities"]), ("加密貨幣", ACCOUNTS["crypto"])]:
        bal = get_account_balance(acct_id)
        print(f"\n📂 {name} (${bal.get('balance', 0):.2f} | P&L ${bal.get('pnl', 0):.2f})")
        
        positions = check_positions(acct_id)
        if not positions:
            print("   📭 No open positions")
            continue
        
        total_pnl = 0
        for p in positions:
            emoji = "🟢" if p["pnl_amt"] > 0 else "🔴" if p["pnl_amt"] < 0 else "⚪"
            print(f"   {emoji} {p['ticker']:<6s} {p['direction']:<5s} | Entry ${p['entry']:.2f} → Now ${p['current']:.2f} | P&L ${p['pnl_amt']:+.2f} ({p['pnl_pct']:+.2f}%) | Stop ${p['stop']:.2f} | Target ${p['limit']:.2f}")
            total_pnl += p["pnl_amt"]
        print(f"   💰 Total P&L: ${total_pnl:+.2f}")


def weekly_review():
    """Generate weekly P&L report from Capital.com accounts."""
    print("=" * 60)
    print("📊 CAPITAL.COM WEEKLY P&L REVIEW")
    print(f"   {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 60)
    
    total_pnl = 0
    
    for name, acct_id in [
        ("加密貨幣", ACCOUNTS["crypto"]),
        ("美股", ACCOUNTS["equities"]),
        ("指數", ACCOUNTS["indices"]),
        ("黃金", ACCOUNTS["gold"]),
    ]:
        bal = get_account_balance(acct_id)
        deposit = bal.get("deposit", 0)
        balance = bal.get("balance", 0)
        pnl = bal.get("pnl", 0)
        available = bal.get("available", 0)
        pnl_pct = (pnl / deposit * 100) if deposit > 0 else 0
        
        print(f"\n📂 {name}")
        print(f"   Deposit: ${deposit:,.2f}")
        print(f"   Balance: ${balance:,.2f}")
        print(f"   P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
        print(f"   Available: ${available:,.2f}")
        
        # Open positions
        positions = check_positions(acct_id)
        if positions:
            print(f"   Open: {len(positions)} positions")
            for p in positions:
                print(f"     {p['ticker']} {p['direction']} | P&L ${p['pnl_amt']:+.2f}")
        
        total_pnl += pnl
    
    print(f"\n💰 Combined P&L: ${total_pnl:+.2f}")
    
    return {
        "timestamp": datetime.now(HKT).isoformat(),
        "accounts": {
            "crypto": get_account_balance(ACCOUNTS["crypto"]),
            "equities": get_account_balance(ACCOUNTS["equities"]),
        },
        "combined_pnl": total_pnl,
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    
    if mode == "--open":
        open_all_from_setups()
    elif mode == "--check":
        check_all()
    elif mode == "--review":
        weekly_review()
    else:
        print("Usage: capitalcom_paper.py [--open|--check|--review]")
