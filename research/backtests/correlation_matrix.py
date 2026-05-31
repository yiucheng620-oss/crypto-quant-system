#!/usr/bin/env python3
"""
correlation_matrix.py — 計算跨市場滾動相關性矩陣
讀取 ~/market_data/ 累積數據，產出：
1. 21日滾動相關性矩陣
2. 63日滾動相關性矩陣
3. 相關性變動（vs 上日）
4. 異常脫勾警報
"""

import pandas as pd
import numpy as np
import os
import json
from datetime import datetime, timedelta

DATA_DIR = os.path.expanduser("~/market_data")
os.makedirs(DATA_DIR, exist_ok=True)

# === 要計算相關性嘅資產（需要有成交價數據） ===
CORR_ASSETS = {
    "SPY": "標普500", "QQQ": "納指100", "DIA": "道指", "IWM": "羅素2000",
    "SMH": "半導體", "ANET": "Arista", "NVDA": "NVIDIA", "AMD": "AMD",
    "BTC_USD": "比特幣", "ETH_USD": "以太幣", "SOL_USD": "Solana",
    "GLD": "黃金", "HYG": "垃圾債", "LQD": "投資級債",
    "VIX": "恐慌指數", "DX_Y_NYB": "美元指數", "TNX": "10年國債",
}

def load_prices():
    """加載所有資產嘅收市價"""
    # 檔案名 mapping（collector 存檔時做咗 safe_name 轉換）
    FILE_MAP = {
        "DX_Y_NYB": "DX_Y.NYB_history.csv",  # yfinance 保留咗 dot
    }
    prices = {}
    for symbol, name in CORR_ASSETS.items():
        # Check file mapping
        if symbol in FILE_MAP:
            path = os.path.join(DATA_DIR, FILE_MAP[symbol])
        else:
            path = os.path.join(DATA_DIR, f"{symbol}_history.csv")
            
        if os.path.exists(path):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if "Close" in df.columns and len(df) >= 63:
                prices[symbol] = df["Close"]
            else:
                print(f"  ⚠️ {name}: 數據不足 ({len(df)} 行)")
        else:
            print(f"  ⚠️ {name}: 冇檔案 ({os.path.basename(path)})")
    return prices

def compute_correlation_matrix(prices, window=21):
    """計算滾動相關性矩陣（pairwise，只用雙方都有數據嘅日期）"""
    # Dedup each series BEFORE constructing DataFrame (pandas rejects dup indices in dict→DataFrame)
    deduped = {}
    dup_total = 0
    for k, s in prices.items():
        if hasattr(s, 'index') and s.index.duplicated().any():
            n = s.index.duplicated().sum()
            dup_total += n
            s = s[~s.index.duplicated(keep='first')]
        deduped[k] = s
    if dup_total > 0:
        print(f"  ⚠️ 發現 {dup_total} 個重複 index，已自動去重")
    df = pd.DataFrame(deduped)
    returns = df.pct_change(fill_method=None).dropna(how='all')
    
    # Pairwise correlation on overlapping dates only
    assets = list(returns.columns)
    corr_matrix = pd.DataFrame(index=assets, columns=assets, dtype=float)
    
    for i, a1 in enumerate(assets):
        for j, a2 in enumerate(assets):
            if i == j:
                corr_matrix.loc[a1, a2] = 1.0
            elif pd.isna(corr_matrix.loc[a1, a2]):
                # Get overlapping returns
                pair = returns[[a1, a2]].dropna()
                if len(pair) >= 10:  # need at least 10 overlapping days
                    corr_val = pair[a1].corr(pair[a2])
                    corr_matrix.loc[a1, a2] = corr_val
                    corr_matrix.loc[a2, a1] = corr_val
                else:
                    corr_matrix.loc[a1, a2] = np.nan
                    corr_matrix.loc[a2, a1] = np.nan
    
    return corr_matrix, returns

def detect_anomalies(corr_21, corr_63, threshold=0.3):
    """檢測相關性異常變動"""
    anomalies = []
    
    for i in corr_21.index:
        for j in corr_21.columns:
            if i >= j:
                continue
            val_21 = corr_21.loc[i, j]
            val_63 = corr_63.loc[i, j]
            
            if pd.notna(val_21) and pd.notna(val_63):
                diff = val_21 - val_63
                if abs(diff) > threshold:
                    direction = "升" if diff > 0 else "降"
                    anomalies.append({
                        "pair": f"{i}-{j}",
                        "corr_21d": round(val_21, 3),
                        "corr_63d": round(val_63, 3),
                        "change": round(diff, 3),
                        "direction": direction,
                        "severity": "🔴" if abs(diff) > 0.5 else "🟡"
                    })
    
    return sorted(anomalies, key=lambda x: abs(x["change"]), reverse=True)

def format_matrix(matrix, assets):
    """Format correlation matrix as text table"""
    lines = []
    header = "           " + " ".join(f"{a:>7}" for a in assets)
    lines.append(header)
    lines.append("-" * len(header))
    for i, row_asset in enumerate(assets):
        row = f"{row_asset:>10} "
        for j, col_asset in enumerate(assets):
            val = matrix.loc[row_asset, col_asset]
            if pd.isna(val):
                row += "    N/A "
            else:
                row += f"{val:7.3f}"
        lines.append(row)
    return "\n".join(lines)

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"📊 Correlation Matrix — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} HKT")
    print(f"{'='*50}")
    
    # Load prices
    prices = load_prices()
    if len(prices) < 4:
        print("❌ 數據不足，需要至少 4 個資產有數據")
        return
    
    print(f"✅ 加載 {len(prices)} 個資產價格數據")
    
    # Normalize all timezones to date-only (fixes UTC vs ET mismatch)
    for sym in list(prices.keys()):
        s = prices[sym]
        # Convert any index to date-only strings for reliable alignment
        try:
            s.index = pd.to_datetime(s.index, utc=True).strftime('%Y-%m-%d')
        except Exception:
            s.index = [str(x)[:10] for x in s.index]  # fallback
        prices[sym] = s
    
    print()
    
    # Compute correlations
    corr_21, returns = compute_correlation_matrix(prices, 21)
    corr_63, _ = compute_correlation_matrix(prices, 63)
    
    # Key pair correlations
    key_pairs = [
        ("BTC_USD", "SPY", "BTC-SPX 相關"),
        ("BTC_USD", "QQQ", "BTC-納指 相關"),
        ("ETH_USD", "QQQ", "ETH-納指 相關"),
        ("SOL_USD", "SMH", "SOL-半導體 相關"),
        ("GLD", "SPY", "黃金-SPX 相關"),
        ("GLD", "BTC_USD", "黃金-BTC 相關"),
        ("SMH", "SPY", "半導體-SPX 相關"),
        ("VIX", "SPY", "VIX-SPX 相關"),
        ("TNX", "SPY", "利率-SPX 相關"),
        ("HYG", "SPY", "信用-SPX 相關"),
    ]
    
    output_lines = []
    output_lines.append(f"📊 跨市場相關性報告 — {datetime.now().strftime('%Y-%m-%d %H:%M')} HKT")
    output_lines.append("="*50)
    output_lines.append("")
    output_lines.append("## 🔑 關鍵相關性 (21日滾動)")
    output_lines.append("")
    
    for sym1, sym2, label in key_pairs:
        if sym1 in corr_21.index and sym2 in corr_21.columns:
            v21 = corr_21.loc[sym1, sym2]
            v63 = corr_63.loc[sym1, sym2] if sym1 in corr_63.index and sym2 in corr_63.columns else None
            if pd.notna(v21):
                chg_str = ""
                if v63 is not None and pd.notna(v63):
                    diff = v21 - v63
                    chg_str = f" (Δ{diff:+.3f} vs 63d)"
                arrow = "🟢" if v21 > 0 else "🔴" if v21 < 0 else "⚪"
                output_lines.append(f"  {arrow} {label:20s}: {v21:+.3f}{chg_str}")
    
    output_lines.append("")
    output_lines.append("## ⚠️ 相關性異常變動 (|Δ| > 0.3)")
    output_lines.append("")
    
    anomalies = detect_anomalies(corr_21, corr_63)
    if anomalies:
        for a in anomalies[:10]:
            output_lines.append(f"  {a['severity']} {a['pair']:15s}: {a['corr_21d']:+.3f} (21d) vs {a['corr_63d']:+.3f} (63d) | {a['direction']} {abs(a['change']):.3f}")
    else:
        output_lines.append("  ✅ 無異常變動")
    
    output_lines.append("")
    output_lines.append("## 📋 完整 21日相關性矩陣")
    output_lines.append("")
    
    # Select top 10 assets for display
    display_assets = ["SPY", "QQQ", "BTC_USD", "ETH_USD", "SOL_USD", "GLD", "SMH", "VIX", "HYG", "TNX"]
    display_assets = [a for a in display_assets if a in corr_21.index]
    
    matrix_text = format_matrix(corr_21, display_assets)
    output_lines.append(matrix_text)
    
    # Save report
    report_dir = os.path.join(DATA_DIR, "daily", today)
    os.makedirs(report_dir, exist_ok=True)
    
    report_path = os.path.join(report_dir, "_correlation_report.txt")
    with open(report_path, "w") as f:
        f.write("\n".join(output_lines))
    
    # Save correlation matrices as CSV
    corr_21.to_csv(os.path.join(report_dir, "_corr_21d.csv"))
    corr_63.to_csv(os.path.join(report_dir, "_corr_63d.csv"))
    
    # Print to stdout
    print("\n".join(output_lines))
    print(f"\n📁 報告: {report_path}")

if __name__ == "__main__":
    main()
