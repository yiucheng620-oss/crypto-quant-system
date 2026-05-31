#!/usr/bin/env python3
"""
update_phase1_data.py — 纏論系統 K 線數據自動更新器 (Step 0)
=========================================================
從 Binance 公共 API 獲取最新的 1h, 4h, 15m, 30m, 1d, 1w K 線數據，
並動態追加到 ~/workspace/data/market/phase1/ 的 .npz 檔案中。
保證纏論掃描器 (chanlun_scanner.py) 每次運行都能讀到 100% 實時的最新價格。
"""
import os
import sys
import numpy as np
import requests
from datetime import datetime, timezone, timedelta

# 目錄設定
WORKSPACE = os.path.expanduser("~/workspace")
DATA_DIR = os.path.join(WORKSPACE, "data/market/phase1")
HKT = timezone(timedelta(hours=8))

os.makedirs(DATA_DIR, exist_ok=True)

def fetch_binance_klines(symbol, interval, limit=1000):
    """從 Binance 免費公共 API 獲取 K 線"""
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ❌ Fetch 失敗 {symbol} {interval}: {e}", file=sys.stderr)
        return []

def update_npz_file(fpath):
    """更新單個 .npz 檔案，只追加最新 K 線"""
    fname = os.path.basename(fpath)
    parts = fname.replace(".npz", "").split("_")
    if len(parts) != 2:
        return
    symbol, tf = parts[0], parts[1]
    
    # 轉換符號：BTC -> BTCUSDT, ETH -> ETHUSDT, SOL -> SOLUSDT
    binance_symbol = f"{symbol}USDT"
    binance_tf = tf
    
    # 載入現有數據
    if os.path.exists(fpath):
        try:
            old = np.load(fpath)
            old_ts = old["timestamp"]
            old_open = old["open"]
            old_high = old["high"]
            old_low = old["low"]
            old_close = old["close"]
            old_vol = old["volume"]
        except Exception as e:
            print(f"  ❌ 讀取舊檔案失敗 {fname}: {e}", file=sys.stderr)
            return
    else:
        print(f"  ⚠️ 檔案不存在 {fname}，跳過。")
        return
    
    # 獲取新數據
    klines = fetch_binance_klines(binance_symbol, binance_tf, limit=1000)
    if not klines:
        return
    
    new_ts = []
    new_open = []
    new_high = []
    new_low = []
    new_close = []
    new_vol = []
    
    last_old_ts = old_ts[-1] if len(old_ts) > 0 else 0
    
    for k in klines:
        ts = int(k[0])
        if ts > last_old_ts:
            new_ts.append(ts)
            new_open.append(float(k[1]))
            new_high.append(float(k[2]))
            new_low.append(float(k[3]))
            new_close.append(float(k[4]))
            new_vol.append(float(k[5]))
            
    if not new_ts:
        if "--quiet" not in sys.argv:
            print(f"  ✅ {fname} 已是最新。最後時間: {datetime.fromtimestamp(last_old_ts/1000, HKT).isoformat()}, 價格: {old_close[-1]}")
        return
    
    # 追加數據
    updated_ts = np.concatenate([old_ts, np.array(new_ts, dtype=np.int64)])
    updated_open = np.concatenate([old_open, np.array(new_open, dtype=np.float64)])
    updated_high = np.concatenate([old_high, np.array(new_high, dtype=np.float64)])
    updated_low = np.concatenate([old_low, np.array(new_low, dtype=np.float64)])
    updated_close = np.concatenate([old_close, np.array(new_close, dtype=np.float64)])
    updated_vol = np.concatenate([old_vol, np.array(new_vol, dtype=np.float64)])
    
    # 儲存
    try:
        np.savez(fpath, 
                 timestamp=updated_ts, 
                 open=updated_open, 
                 high=updated_high, 
                 low=updated_low, 
                 close=updated_close, 
                 volume=updated_vol)
        print(f"  🎉 {fname} 更新成功: +{len(new_ts)} 根 K 線。最後時間: {datetime.fromtimestamp(updated_ts[-1]/1000, HKT).isoformat()}, 最新價: {updated_close[-1]}")
    except Exception as e:
        print(f"  ❌ 儲存 {fname} 失敗: {e}", file=sys.stderr)

def main():
    if "--quiet" not in sys.argv:
        print("📡 開始自動更新 System 3 (纏論) 歷史 K 線數據...")
    
    updated_count = 0
    for f in sorted(os.listdir(DATA_DIR)):
        if f.endswith(".npz"):
            update_npz_file(os.path.join(DATA_DIR, f))
            updated_count += 1
            
    if "--quiet" not in sys.argv:
        print(f"✅ 更新完成。共掃描 {updated_count} 個數據檔案。")

if __name__ == "__main__":
    main()
