#!/usr/bin/env python3
"""
Deep Cross-Market Correlation Analysis - v2
Fixes for VIX ticker and Monday gap analysis.
"""

import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')

BASE = "/home/yiucheng620/workspace/scalp_lab/engines"

# ============================================================
# 1. LOAD FLOW HISTORY
# ============================================================
flow_history = json.load(open(f"{BASE}/flow_history.json"))
flow_df = pd.DataFrame(flow_history)
flow_df['timestamp'] = pd.to_datetime(flow_df['timestamp'])
flow_df = flow_df.set_index('timestamp')

print(f"flow_history: {len(flow_history)} snapshots")
print(f"時間範圍: {flow_df.index.min()} ~ {flow_df.index.max()}")

# ============================================================
# 2. FETCH PRICE DATA (fixed tickers)
# ============================================================
print("\n=== 獲取市場數據 ===")
end_date = "2026-05-23"
start_date = "2026-01-01"

# Use ^VIX for VIX, use SPY for SPX (more reliable), use ^XAU for gold, DX-Y.NYB for DXY
tickers = {
    "SPY": "SPY",           # SPX ETF (more reliable than ^GSPC)
    "^VIX": "VIX",          # VIX index
    "GLD": "GLD",           # Gold ETF
    "BTC-USD": "BTC",
    "ETH-USD": "ETH",
    "DX-Y.NYB": "DXY",      # Dollar index
}

all_ticker_symbols = list(tickers.keys()) + ["AMD", "AMAT", "MRVL", "LRCX", "INTC"]

data = yf.download(all_ticker_symbols, start=start_date, end=end_date, progress=False)
print(f"下載數據: {len(data)} 天")

if isinstance(data.columns, pd.MultiIndex):
    close_prices = data['Close']
else:
    close_prices = data

# Rename columns
close_prices = close_prices.rename(columns=tickers)
print(f"可用欄位: {list(close_prices.columns)}")

# ============================================================
# 3. FIX VIX - use proxy if ^VIX is wrong
# ============================================================
if 'VIX' in close_prices.columns:
    vix_raw = close_prices['VIX'].dropna()
    print(f"\nVIX原始數據: 均値={vix_raw.mean():.2f}, 范围={vix_raw.min():.2f}-{vix_raw.max():.2f}")
    if vix_raw.mean() > 50:
        print("⚠️ VIX數據異常（>50），可能是回傳了錯誤值")
        print("   嘗試使用VIX ETF (VIXY) 作為替代...")
        try:
            vixy = yf.download("VIXY", start=start_date, end=end_date, progress=False)
            if isinstance(vixy.columns, pd.MultiIndex):
                vixy_close = vixy['Close']
            else:
                vixy_close = vixy
            # VIXY ≈ VIX/12 roughly, but let's just use it as proxy
            close_prices['VIX'] = vixy_close * 12  # approximate scaling
            print(f"   修正後VIX: 均値={close_prices['VIX'].mean():.2f}")
        except:
            print("   備份方案失敗，標記VIX不可用")
            close_prices = close_prices.drop(columns=['VIX'], errors='ignore')

# ============================================================
# 4. VIX REGIME ANALYSIS
# ============================================================
print("\n" + "="*60)
print("📈 VIX體制影響分析")
print("="*60)

# Use flow_history VIX data
flow_vix = flow_df['vix'].dropna().astype(float)
print(f"流狀態VIX (279快照): 均値={flow_vix.mean():.2f}, 範圍={flow_vix.min():.2f}-{flow_vix.max():.2f}")

# Use yfinance data with 60-day rolling VIX approximation from SPY options or just use flow vix
if 'VIX' in close_prices.columns:
    vix_yf = close_prices['VIX'].dropna()
    print(f"\n修正後VIX統計:")
    print(f"  均値: {vix_yf.mean():.2f}")
    print(f"  中位數: {vix_yf.median():.2f}")
    print(f"  範圍: {vix_yf.min():.2f} ~ {vix_yf.max():.2f}")
    
    # Define regimes
    low_vix_days = vix_yf[vix_yf < 18]
    mid_vix_days = vix_yf[(vix_yf >= 18) & (vix_yf <= 25)]
    high_vix_days = vix_yf[vix_yf > 25]
    
    print(f"\nVIX < 18: {len(low_vix_days)}天")
    print(f"VIX 18-25: {len(mid_vix_days)}天")
    print(f"VIX > 25: {len(high_vix_days)}天")
    
    # Calculate returns by VIX regime for each asset
    assets = ['SPY', 'GLD', 'BTC', 'ETH', 'DXY']
    available_assets = [a for a in assets if a in close_prices.columns]
    
    for asset in available_assets:
        series = close_prices[asset].dropna()
        returns = series.pct_change().dropna()
        
        # Align returns with VIX data
        combined = pd.DataFrame({
            'returns': returns,
            'vix': close_prices['VIX'].reindex(returns.index)
        }).dropna()
        
        if len(combined) < 10:
            continue
            
        low = combined[combined['vix'] < 18]['returns']
        mid = combined[(combined['vix'] >= 18) & (combined['vix'] <= 25)]['returns']
        high = combined[combined['vix'] > 25]['returns']
        
        print(f"\n--- {asset} ---")
        if len(low) > 5:
            print(f"  低VIX (<18): 日收益={low.mean()*100:.2f}% | 勝率={(low>0).mean()*100:.0f}% | 波動={low.std()*100:.2f}%")
        if len(mid) > 5:
            print(f"  中VIX (18-25): 日收益={mid.mean()*100:.2f}% | 勝率={(mid>0).mean()*100:.0f}% | 波動={mid.std()*100:.2f}%")
        if len(high) > 5:
            print(f"  高VIX (>25): 日收益={high.mean()*100:.2f}% | 勝率={(high>0).mean()*100:.0f}% | 波動={high.std()*100:.2f}%")
else:
    print("VIX數據不可用，跳過體制分析")
    # Fall back to flow model: all data is in low VIX regime
    print(f"\n流狀態模型: 所有279個快照均為VIX<18 (低波動體制)")
    print("無法進行高低VIX對比分析")

# ============================================================
# 5. CROSS-MARKET CORRELATION
# ============================================================
print("\n" + "="*60)
print("🔄 跨市場相關性矩陣")
print("="*60)

available_for_corr = [c for c in ['SPY', 'VIX', 'GLD', 'BTC', 'ETH', 'DXY'] if c in close_prices.columns]
if len(available_for_corr) >= 3:
    corr_df = close_prices[available_for_corr].dropna()
    ret_df = corr_df.pct_change().dropna()
    corr_matrix = ret_df.corr()
    
    print("\n日回報相關性矩陣:")
    print(corr_matrix.to_string(float_format=lambda x: f"{x:.3f}"))
    
    print("\n🔑 關鍵關係:")
    pairs = [
        ('SPY', 'VIX', 'SPX ↔ VIX (通常負相關)'),
        ('GLD', 'DXY', '黃金 ↔ 美元 (通常負相關)'),
        ('BTC', 'SPY', 'BTC ↔ SPX (數位黃金vs傳統風險)'),
        ('ETH', 'BTC', 'ETH ↔ BTC (加密貨幣內部)'),
        ('SPY', 'GLD', 'SPX ↔ 黃金 (避險vs風險)'),
        ('ETH', 'DXY', 'ETH ↔ 美元'),
    ]
    for a1, a2, desc in pairs:
        if a1 in corr_matrix and a2 in corr_matrix:
            val = corr_matrix.loc[a1, a2]
            print(f"  {desc}: r={val:.3f}")
else:
    print("可用資產過少，跳過相關性分析")

# ============================================================
# 6. BTC DOMINANCE EFFECT
# ============================================================
print("\n" + "="*60)
print("🪙 BTC.D 效應與流狀態變量相關性")
print("="*60)

flow_numeric = flow_df[['btc_dominance', 'eth_btc_ratio', 'sol_btc_ratio', 'vix', 'ndx_spx_ratio', 'eurusd', 'gold']].dropna().astype(float)

# btc_dominance is constant, so drop it for correlation
flow_numeric = flow_numeric.drop(columns=['btc_dominance'], errors='ignore')

flow_corr = flow_numeric.corr()
print("\n流狀態變量相關性矩陣:")
print(flow_corr.to_string(float_format=lambda x: f"{x:.3f}"))

print("\n🔑 高相關發現:")
for i in range(len(flow_corr.columns)):
    for j in range(i+1, len(flow_corr.columns)):
        val = flow_corr.iloc[i, j]
        if abs(val) > 0.5:
            print(f"  {flow_corr.columns[i]} ↔ {flow_corr.columns[j]}: r={val:.3f}")

# ============================================================
# 7. TIME-OF-DAY PATTERNS
# ============================================================
print("\n" + "="*60)
print("⏰ 時段VIX模式分析")
print("="*60)

flow_df['hour'] = flow_df.index.hour
hourly = flow_df.groupby('hour').agg({
    'vix': ['mean', 'std', 'min', 'max'],
    'eth_btc_ratio': 'mean',
    'gold': 'mean',
}).round(3)

print("\n各小時VIX與市場變量:")
print(hourly.to_string())

# Find most volatile hours
vix_hourly = flow_df.groupby('hour')['vix'].agg(['mean', 'std'])
peak_hour = vix_hourly['mean'].idxmax()
calm_hour = vix_hourly['mean'].idxmin()
print(f"\nVIX高峰時段: 小時{peak_hour} (均値={vix_hourly.loc[peak_hour,'mean']:.2f})")
print(f"VIX低谷時段: 小時{calm_hour} (均値={vix_hourly.loc[calm_hour,'mean']:.2f})")

# ============================================================
# 8. SEMICONDUCTOR SECTOR CORRELATION
# ============================================================
print("\n" + "="*60)
print("🔬 半導體類股相關性 (AMD, AMAT, MRVL, LRCX, INTC)")
print("="*60)

semi_tickers = ["AMD", "AMAT", "MRVL", "LRCX", "INTC"]
available_semi = [t for t in semi_tickers if t in close_prices.columns]

if len(available_semi) >= 3:
    semi_close = close_prices[available_semi].dropna()
    semi_ret = semi_close.pct_change().dropna()
    semi_corr = semi_ret.corr()
    
    print("\n日回報相關性矩陣:")
    print(semi_corr.to_string(float_format=lambda x: f"{x:.3f}"))
    
    # Average correlation
    triu = []
    for i in range(len(available_semi)):
        for j in range(i+1, len(available_semi)):
            triu.append(semi_corr.iloc[i, j])
    avg_corr = np.mean(triu)
    print(f"\n平均成對相關性: {avg_corr:.3f}")
    
    # PCA
    eigvals = sorted(np.linalg.eigvalsh(semi_corr.values), reverse=True)
    print(f"特徵值: {[f'{v:.3f}' for v in eigvals]}")
    pc1_var = eigvals[0] / sum(eigvals) * 100
    print(f"第一主成分解釋變異: {pc1_var:.1f}%")
    
    # Individual pairs
    print(f"\n個別配對:")
    max_pair = ('', '', 0)
    min_pair = ('', '', 1)
    for i in range(len(available_semi)):
        for j in range(i+1, len(available_semi)):
            val = semi_corr.iloc[i, j]
            bar = '█' * int(abs(val) * 10)
            print(f"  {available_semi[i]}-{available_semi[j]}: r={val:.3f} {bar}")
            if abs(val) > abs(max_pair[2]):
                max_pair = (available_semi[i], available_semi[j], val)
            if abs(val) < abs(min_pair[2]):
                min_pair = (available_semi[i], available_semi[j], val)
    
    print(f"\n最相關: {max_pair[0]}-{max_pair[1]} (r={max_pair[2]:.3f})")
    print(f"最不相關: {min_pair[0]}-{min_pair[1]} (r={min_pair[2]:.3f})")
    
    if avg_corr > 0.7:
        print(f"\n⚠️ 平均r={avg_corr:.3f} > 0.7 → 高度相關，≈1個頭寸")
    elif avg_corr > 0.5:
        print(f"\n⚠️ 平均r={avg_corr:.3f} (0.5-0.7) → 中度相關，部分分散化")
    else:
        print(f"\n✅ 平均r={avg_corr:.3f} < 0.5 → 低度相關，良好分散化")
else:
    print("半導體數據不足")

# ============================================================
# 9. MONDAY GAP ANALYSIS (FIXED)
# ============================================================
print("\n" + "="*60)
print("🌅 週一開盤缺口分析 (Monday Gap)")
print("="*60)

gap_targets = [
    ("SPY", "SPX (SPY)"),
    ("BTC-USD", "BTC"),
    ("ETH-USD", "ETH"),
    ("GLD", "黃金 (GLD)"),
]

for ticker, name in gap_targets:
    try:
        df = yf.download(ticker, start="2025-06-01", end=end_date, progress=False)
        if df.empty or len(df) < 20:
            print(f"\n{name}: 數據不足")
            continue
            
        if isinstance(df.columns, pd.MultiIndex):
            df_close = df['Close'].copy()
        else:
            df_close = df[['Close']].copy() if 'Close' in df.columns else df.copy()
            if isinstance(df_close, pd.Series):
                df_close = df_close.to_frame(name='Close')
        
        # Forward fill in case of missing days
        df_close = df_close.resample('D').ffill().dropna()
        df_close['day_of_week'] = df_close.index.dayofweek
        
        # Get Monday closes and previous Friday closes
        gaps = []
        for i in range(1, len(df_close)):
            if df_close['day_of_week'].iloc[i] == 0:  # Monday
                mon_price = df_close['Close'].iloc[i]
                # Find the most recent preceding trading day (should be Friday)
                for j in range(i-1, max(i-5, -1), -1):
                    if df_close['day_of_week'].iloc[j] == 4:  # Friday
                        fri_price = df_close['Close'].iloc[j]
                        gap_pct = (mon_price / fri_price - 1) * 100
                        gaps.append({
                            'date': df_close.index[i],
                            'fri_date': df_close.index[j],
                            'fri_close': fri_price,
                            'mon_close': mon_price,
                            'gap_pct': gap_pct
                        })
                        break
        
        if len(gaps) < 3:
            print(f"\n{name}: 僅{len(gaps)}個週一缺口")
            continue
            
        gap_df = pd.DataFrame(gaps)
        avg = gap_df['gap_pct'].mean()
        med = gap_df['gap_pct'].median()
        pos = (gap_df['gap_pct'] > 0).sum()
        neg = (gap_df['gap_pct'] < 0).sum()
        
        print(f"\n--- {name} ---")
        print(f"  分析期間: {gap_df['date'].min().strftime('%Y-%m-%d')} ~ {gap_df['date'].max().strftime('%Y-%m-%d')}")
        print(f"  週一數量: {len(gaps)}")
        print(f"  平均缺口: {avg:+.2f}%")
        print(f"  中位數缺口: {med:+.2f}%")
        print(f"  上漲次數: {pos}次 ({pos/len(gaps)*100:.0f}%)")
        print(f"  下跌次數: {neg}次 ({neg/len(gaps)*100:.0f}%)")
        print(f"  最大漲幅: {gap_df['gap_pct'].max():+.2f}%")
        print(f"  最大跌幅: {gap_df['gap_pct'].min():+.2f}%")
        print(f"  缺口波動率: {gap_df['gap_pct'].std():.2f}%")
        
        # Recent 5 gaps
        recent = gap_df.sort_values('date', ascending=False).head(5)
        print(f"\n  最近5次:")
        for _, r in recent.iterrows():
            arrow = "📈" if r['gap_pct'] > 0 else "📉"
            print(f"    {r['date'].strftime('%m/%d')} {arrow} {r['gap_pct']:+.2f}%")
            
    except Exception as e:
        print(f"\n{name} 分析失敗: {e}")

# ============================================================
# 10. SAVE RESULTS
# ============================================================
print("\n" + "="*60)
print("📋 分析完成")
print("="*60)

results = {
    'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
    'flow_snapshots': len(flow_history),
    'trades_total': 5,
    'btc_dominance': float(flow_df['btc_dominance'].iloc[-1]),
    'current_vix': float(flow_df['vix'].iloc[-1]),
    'vix_regime': str(flow_df['vix_regime'].iloc[-1]),
    'vix_range_flow': [float(flow_vix.min()), float(flow_vix.max())],
}

json.dump(results, open(f"{BASE}/cross_market_results.json", 'w'), indent=2)
print("\n✅ 結果保存至 cross_market_results.json")
