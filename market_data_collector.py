#!/usr/bin/env python3
"""
market_data_collector.py — 每日自動採集多市場 OHLCV 數據
零 API key，用 Yahoo Finance (yfinance)
儲存到 ~/market_data/ 目錄
"""

import yfinance as yf
import pandas as pd
import os
from datetime import datetime, timedelta
import json

DATA_DIR = os.path.expanduser("~/market_data")
os.makedirs(DATA_DIR, exist_ok=True)

# === 資產清單 ===
ASSETS = {
    # 美股指數
    "SPY": "標普500",
    "QQQ": "納指100",
    "DIA": "道指",
    "IWM": "羅素2000",
    "SMH": "半導體SOX",
    # 個股
    "ANET": "Arista",
    "NVDA": "NVIDIA",
    "AMD": "AMD",
    # 加密
    "BTC-USD": "比特幣",
    "ETH-USD": "以太幣",
    "SOL-USD": "Solana",
    # 宏觀/避險
    "GLD": "黃金ETF",
    "HYG": "高收益債",
    "JNK": "垃圾債",
    "LQD": "投資級債",
    # 波動率/匯率/利率
    "^VIX": "恐慌指數",
    "DX-Y.NYB": "美元指數",
    "^TNX": "10年國債",
}

def fetch_data(symbol, period="6mo"):
    """Fetch OHLCV data for a symbol"""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period)
        if df.empty:
            print(f"  ⚠️ {symbol}: 冇數據")
            return None
        return df
    except Exception as e:
        print(f"  ❌ {symbol}: {e}")
        return None

def save_and_append(symbol, df):
    """Save to both daily snapshot and cumulative CSV"""
    # Daily snapshot
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = os.path.join(DATA_DIR, "daily", today)
    os.makedirs(daily_dir, exist_ok=True)
    
    safe_name = symbol.replace("^", "").replace("=", "_").replace("-", "_")
    df.to_csv(os.path.join(daily_dir, f"{safe_name}.csv"))
    
    # Cumulative CSV
    cum_path = os.path.join(DATA_DIR, f"{safe_name}_history.csv")
    if os.path.exists(cum_path):
        existing = pd.read_csv(cum_path, index_col=0, parse_dates=True)
        # Ensure index is datetime (handle mixed types from CSV)
        if not pd.api.types.is_datetime64_any_dtype(existing.index):
            existing.index = pd.to_datetime(existing.index, errors='coerce', utc=True)
        # Normalize both indices to timezone-naive UTC
        if existing.index.tz is not None:
            existing.index = existing.index.tz_localize(None)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        # Only append new rows
        new_rows = df[~df.index.isin(existing.index)]
        if not new_rows.empty:
            combined = pd.concat([existing, new_rows]).sort_index()
            combined.to_csv(cum_path)
    else:
        df.to_csv(cum_path)
    
    return len(df)

def main():
    print(f"📡 Market Data Collector — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} HKT")
    print(f"{'='*50}")
    
    results = {}
    total_new = 0
    
    for symbol, name in ASSETS.items():
        print(f"📥 {name} ({symbol})...", end=" ")
        df = fetch_data(symbol, period="6mo")
        if df is not None:
            n = save_and_append(symbol, df)
            latest = df.iloc[-1]
            chg = ((latest['Close'] - df.iloc[-2]['Close']) / df.iloc[-2]['Close'] * 100) if len(df) >= 2 else 0
            print(f"✅ {n}行 | ${latest['Close']:.1f} | {chg:+.2f}%")
            results[symbol] = {
                "name": name,
                "close": round(latest['Close'], 2),
                "change_pct": round(chg, 2),
                "volume": int(latest['Volume']),
                "rows": n
            }
            total_new += 1
        else:
            results[symbol] = {"name": name, "status": "FAILED"}
    
    # Save daily summary JSON
    today = datetime.now().strftime("%Y-%m-%d")
    summary_path = os.path.join(DATA_DIR, "daily", today, "_summary.json")
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    # Write human-readable summary
    summary_txt = os.path.join(DATA_DIR, "daily", today, "_summary.txt")
    with open(summary_txt, "w") as f:
        f.write(f"📡 Market Data Summary — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} HKT\n")
        f.write(f"{'='*50}\n")
        for sym, info in results.items():
            if "close" in info:
                arrow = "🟢" if info["change_pct"] > 0 else "🔴"
                f.write(f"{arrow} {info['name']:12s} ${info['close']:>10.2f} {info['change_pct']:+.2f}%\n")
            else:
                f.write(f"❌ {info['name']:12s} FAILED\n")
        f.write(f"\n✅ {total_new}/{len(ASSETS)} 資產成功採集\n")
    
    print(f"\n✅ 完成: {total_new}/{len(ASSETS)} 資產成功")
    print(f"📁 數據儲存: {DATA_DIR}/daily/{today}/")
    return total_new

if __name__ == "__main__":
    main()
