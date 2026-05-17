#!/usr/bin/env python3
"""
cross_market_sentinel.py — 跨市場脫勾 + VIX 期限結構監察
每小時檢查 (silent unless alert):
- BTC-SPX 相關性急跌 (>0.3 change)
- VIX 期限結構 (contango/backwardation)
- 信用市場裂縫 (HYG/LQD ratio)
"""

import json, os, urllib.request
from datetime import datetime, timezone
import pandas as pd
import numpy as np

DATA_DIR = os.path.expanduser("~/market_data")
ALERT_FILE = os.path.join(DATA_DIR, "cross_market_alerts.jsonl")
os.makedirs(DATA_DIR, exist_ok=True)

def load_prices(symbol):
    """加載價格，正規化日期（解決時區問題）"""
    path = os.path.join(DATA_DIR, f"{symbol}_history.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if "Close" in df.columns:
            try:
                df.index = pd.to_datetime(df.index, utc=True).date
            except:
                df.index = pd.Index([pd.Timestamp(str(x)[:10]).date() for x in df.index])
            return df["Close"]
    return None

def save_alert(atype, data):
    alert = {"timestamp": datetime.now(timezone.utc).isoformat(), "type": atype, "data": data}
    with open(ALERT_FILE, "a") as f:
        f.write(json.dumps(alert) + "\n")

def check_correlation_break():
    """BTC-SPX correlation break detection"""
    btc = load_prices("BTC_USD")
    spy = load_prices("SPY")
    if btc is None or spy is None:
        return
    
    # Align on common business days
    common = btc.index.intersection(spy.index)
    if len(common) < 15:
        return
    
    br = btc[common].pct_change(fill_method=None).dropna()
    sr = spy[common].pct_change(fill_method=None).dropna()
    common_ret = br.index.intersection(sr.index)
    if len(common_ret) < 10:
        return
    
    br, sr = br[common_ret], sr[common_ret]
    n = len(br)
    
    n21 = min(21, n)
    corr_21 = br.tail(n21).corr(sr.tail(n21))
    n5 = min(5, n)
    corr_5 = br.tail(n5).corr(sr.tail(n5))
    n3 = min(3, n)
    corr_3 = br.tail(n3).corr(sr.tail(n3)) if n3 >= 3 else 0
    
    diff = corr_5 - corr_21
    print(f"  BTC-SPX ({n} days): 21d={corr_21:.3f} 5d={corr_5:.3f} 3d={corr_3:.3f}")
    
    if abs(diff) >= 0.3:
        save_alert("correlation_break", {
            "pair": "BTC-SPX",
            "corr_21d": round(corr_21, 3),
            "corr_5d": round(corr_5, 3),
            "corr_3d": round(corr_3, 3),
            "change": round(diff, 3),
            "aligned_days": n,
            "direction": "rising" if diff > 0 else "falling"
        })

def check_vix_term():
    """VIX term structure: compare spot to 20-day MA as stress proxy"""
    try:
        import yfinance as yf
        
        vix = yf.Ticker("^VIX")
        vix_hist = vix.history(period="2mo")
        if len(vix_hist) < 21:
            return
        
        closes = vix_hist["Close"]
        vix_spot = closes.iloc[-1]
        vix_ma20 = closes.tail(20).mean()
        vix_ma50 = closes.tail(50).mean() if len(closes) >= 50 else vix_ma20
        
        # Structure: spot vs MA is a cleaner signal than ETF proxy
        if vix_spot > vix_ma20 * 1.1:
            structure = "backwardation (stress)"
        elif vix_spot > vix_ma20:
            structure = "mild backwardation"
        elif vix_spot < vix_ma20 * 0.9:
            structure = "contango (complacent)"
        else:
            structure = "normal"
        
        print(f"  VIX: spot={vix_spot:.1f} MA20={vix_ma20:.1f} MA50={vix_ma50:.1f} [{structure}]")
        
        if vix_spot > vix_ma20 * 1.15:
            save_alert("vix_backwardation", {
                "vix_spot": round(vix_spot, 1),
                "vix_ma20": round(vix_ma20, 1),
                "ratio": round(vix_spot/vix_ma20, 2),
                "structure": structure,
                "note": "VIX > MA20 by 15% = elevated fear"
            })
        
        if vix_spot >= 25:
            save_alert("vix_elevated", {"vix": round(vix_spot, 1)})
    
    except Exception as e:
        print(f"  VIX check: {e}")

def check_credit_spread():
    """HYG/LQD ratio as credit stress indicator"""
    hyg = load_prices("HYG")
    lqd = load_prices("LQD")
    if hyg is None or lqd is None:
        return
    
    hyg_val = hyg.iloc[-1]
    lqd_val = lqd.iloc[-1]
    ratio = hyg_val / lqd_val if lqd_val > 0 else 1
    
    if ratio < 0.72:
        save_alert("credit_stress", {
            "hyg": round(hyg_val, 2),
            "lqd": round(lqd_val, 2),
            "ratio": round(ratio, 3),
            "note": "HYG/LQD < 0.72 = credit stress"
        })

def main():
    alert_file = os.path.join(DATA_DIR, "cross_market_alerts.jsonl")
    before = sum(1 for _ in open(alert_file)) if os.path.exists(alert_file) else 0
    
    check_correlation_break()
    check_vix_term()
    check_credit_spread()
    
    after = sum(1 for _ in open(alert_file)) if os.path.exists(alert_file) else 0
    new_alerts = after - before
    
    if new_alerts > 0:
        print(f"📊 Cross-Market Sentinel — {datetime.now().strftime('%Y-%m-%d %H:%M')} HKT")
        print(f"🚨 {new_alerts} new alert(s)")
        if os.path.exists(alert_file):
            with open(alert_file) as f:
                lines = f.readlines()
                for line in lines[-new_alerts:]:
                    try:
                        a = json.loads(line.strip())
                        d = a.get("data", {})
                        t = a.get("type", "?")
                        # Clean display
                        if t == "correlation_break":
                            print(f"  🔗 {d.get('pair')}: {d.get('corr_21d')}→{d.get('corr_5d')} ({d.get('direction')}, {d.get('aligned_days')}d aligned)")
                        elif t == "vix_backwardation":
                            print(f"  📈 VIX {d.get('vix_spot')} vs MA20 {d.get('vix_ma20')} — {d.get('structure')}")
                        elif t == "vix_elevated":
                            print(f"  ⚠️ VIX elevated: {d.get('vix')}")
                        elif t == "credit_stress":
                            print(f"  💳 HYG/LQD: {d.get('ratio')} — {d.get('note')}")
                        else:
                            print(f"  {t}: {json.dumps(d)[:80]}")
                    except:
                        pass

if __name__ == "__main__":
    main()
