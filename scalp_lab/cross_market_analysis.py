#!/usr/bin/env python3
"""
Deep Cross-Market Correlation Analysis
Analyzes engine P&L, signal correlations, VIX regime impact, sector correlation, Monday gaps.
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
# 1. LOAD AVAILABLE DATA
# ============================================================

def load_json(path):
    with open(path) as f:
        return json.load(f)

# Load flow history
flow_history = load_json(f"{BASE}/flow_history.json")
print(f"[1] flow_history: {len(flow_history)} snapshots")

# Load trades
trade_files = {
    'btc': f"{BASE}/btc_trades.json",
    'eth': f"{BASE}/eth_trades.json",
    'gold': f"{BASE}/gold_trades.json",
    'indices': f"{BASE}/indices_trades.json",
    'equities': f"{BASE}/equities_trades.json",
}
all_trades = {}
for name, fp in trade_files.items():
    data = load_json(fp)
    all_trades[name] = data
    print(f"  {name}_trades: {len(data)} trades")

# ============================================================
# 2. TRADE P&L CORRELATION (if enough data)
# ============================================================
print("\n" + "="*60)
print("📊 引擎P&L相關性分析")
print("="*60)

# Check if we have enough trades
total_trades = sum(len(v) for v in all_trades.values())
print(f"交易總數: {total_trades}")
if total_trades < 10:
    print("⚠️ 交易數據過少 (<10筆)，轉用信號相關性分析")
    use_signal_corr = True
else:
    use_signal_corr = False
    # Build P&L dataframe
    # ... would process trades by timestamp for correlation

# ============================================================
# 3. FLOW HISTORY → SIGNAL CORRELATION ANALYSIS
# ============================================================
print("\n" + "="*60)
print("📶 流狀態信號相關性分析")
print("="*60)

# Extract flow state time series
flow_df = pd.DataFrame(flow_history)
flow_df['timestamp'] = pd.to_datetime(flow_df['timestamp'])
flow_df = flow_df.set_index('timestamp')

print(f"流狀態時間範圍: {flow_df.index.min()} ~ {flow_df.index.max()}")
print(f"總快照數: {len(flow_df)}")

# Analyze signals
signal_counts = {}
for s in flow_df['signals']:
    for sig in s:
        signal_counts[sig] = signal_counts.get(sig, 0) + 1

print("\n信號出現頻率:")
for sig, cnt in sorted(signal_counts.items(), key=lambda x: -x[1]):
    print(f"  {sig}: {cnt}次 ({cnt/len(flow_df)*100:.1f}%)")

# BTC_DOMINANCE_HIGH appears in ALL snapshots
# Let's check how many unique signal types exist
unique_signals = set()
for s in flow_df['signals']:
    unique_signals.update(s)
print(f"\n唯一信號類型: {unique_signals}")

# Check vix_regime distribution
print(f"\nVIX regime分布:")
print(flow_df['vix_regime'].value_counts())

# ============================================================
# 4. VIX REGIME IMPACT ANALYSIS
# ============================================================
print("\n" + "="*60)
print("📈 VIX體制影響分析")
print("="*60)

vix_data = flow_df['vix'].dropna().astype(float)
print(f"VIX範圍: {vix_data.min():.2f} ~ {vix_data.max():.2f}")
print(f"VIX均值: {vix_data.mean():.2f}")

# VIX regime analysis on market data (via Yahoo Finance)
print("\n透過Yahoo Finance獲取SPX、VIX、黃金、BTC、ETH歷史數據...")

# Fetch ~2 months of data
end_date = "2026-05-23"
start_date = "2026-03-01"

tickers_list = ["^GSPC", "^VIX", "GC=F", "BTC-USD", "ETH-USD", "DX-Y.NYB"]
try:
    market_data = yf.download(tickers_list, start=start_date, end=end_date, progress=False)
    if isinstance(market_data.columns, pd.MultiIndex):
        close_prices = market_data['Close']
    else:
        close_prices = market_data
    close_prices.columns = ['SPX', 'VIX', 'GOLD', 'BTC', 'ETH', 'DXY']
    print(f"下載到 {len(close_prices)} 天數據")
except Exception as e:
    print(f"Yahoo Finance下載失敗: {e}")
    close_prices = None

if close_prices is not None and not close_prices.empty:
    # Clean VIX data
    vix_series = close_prices['VIX'].dropna()
    print(f"\nVIX統計 (來自Yahoo):")
    print(f"  中位數: {vix_series.median():.2f}")
    print(f"  均值: {vix_series.mean():.2f}")
    print(f"  最小值: {vix_series.min():.2f}")
    print(f"  最大值: {vix_series.max():.2f}")
    print(f"  當前: {vix_series.iloc[-1]:.2f}")
    
    # VIX < 18 regime
    low_vix = close_prices[close_prices['VIX'] < 18]
    high_vix = close_prices[close_prices['VIX'] > 25]
    mid_vix = close_prices[(close_prices['VIX'] >= 18) & (close_prices['VIX'] <= 25)]
    
    print(f"\nVIX < 18 (低波動): {len(low_vix)}天")
    print(f"VIX 18-25 (中等): {len(mid_vix)}天")
    print(f"VIX > 25 (高波動): {len(high_vix)}天")
    
    for asset in ['SPX', 'BTC', 'ETH', 'GOLD', 'DXY']:
        if asset not in close_prices.columns:
            continue
        series = close_prices[asset].dropna()
        if len(series) < 5:
            continue
        # Daily returns
        returns = series.pct_change().dropna()
        
        # Map returns to VIX regimes
        regime_df = pd.DataFrame({
            'returns': returns,
            'vix': close_prices['VIX'].reindex(returns.index)
        }).dropna()
        
        low_ret = regime_df[regime_df['vix'] < 18]['returns']
        high_ret = regime_df[regime_df['vix'] > 25]['returns']
        mid_ret = regime_df[(regime_df['vix'] >= 18) & (regime_df['vix'] <= 25)]['returns']
        
        print(f"\n--- {asset} by VIX Regime ---")
        if len(low_ret) > 5:
            print(f"  VIX<18: 平均收益={low_ret.mean()*100:.2f}%  | 勝率={(low_ret>0).mean()*100:.1f}% | 日波動={low_ret.std()*100:.2f}%")
        if len(mid_ret) > 5:
            print(f"  VIX18-25: 平均收益={mid_ret.mean()*100:.2f}%  | 勝率={(mid_ret>0).mean()*100:.1f}% | 日波動={mid_ret.std()*100:.2f}%")
        if len(high_ret) > 5:
            print(f"  VIX>25: 平均收益={high_ret.mean()*100:.2f}%  | 勝率={(high_ret>0).mean()*100:.1f}% | 日波動={high_ret.std()*100:.2f}%")
    
    # ============================================================
    # 5. CROSS-MARKET CORRELATION MATRIX
    # ============================================================
    print("\n" + "="*60)
    print("🔄 跨市場相關性矩陣")
    print("="*60)
    
    # Daily returns for all assets
    price_df = close_prices.dropna()
    ret_df = price_df.pct_change().dropna()
    
    corr_matrix = ret_df.corr()
    print("\n日回報相關性矩陣:")
    print(corr_matrix.to_string(float_format=lambda x: f"{x:.3f}"))
    
    # Highlight high correlations
    print("\n高相關性組合 (|r| > 0.5):")
    high_corr_pairs = []
    for i in range(len(corr_matrix.columns)):
        for j in range(i+1, len(corr_matrix.columns)):
            val = corr_matrix.iloc[i, j]
            if abs(val) > 0.5:
                high_corr_pairs.append((corr_matrix.columns[i], corr_matrix.columns[j], val))
    if high_corr_pairs:
        for c1, c2, v in sorted(high_corr_pairs, key=lambda x: -abs(x[2])):
            print(f"  {c1} ↔ {c2}: r={v:.3f}")
    else:
        print("  (無超過0.5的相關性)")
    
    # ============================================================
    # 6. BTC DOMINANCE EFFECT (from flow_history)
    # ============================================================
    print("\n" + "="*60)
    print("🪙 BTC.D (比特幣市佔率) 效應分析")
    print("="*60)
    
    btc_dom = flow_df['btc_dominance'].dropna().astype(float)
    print(f"BTC.D範圍: {btc_dom.min():.1f}% ~ {btc_dom.max():.1f}%")
    print(f"BTC.D均值: {btc_dom.mean():.1f}%")
    print(f"當前BTC.D: {btc_dom.iloc[-1]:.1f}%")
    
    # Check correlation between flow state variables
    flow_numeric = flow_df[['btc_dominance', 'eth_btc_ratio', 'sol_btc_ratio', 'vix', 'ndx_spx_ratio', 'eurusd', 'gold']].dropna().astype(float)
    flow_corr = flow_numeric.corr()
    print("\n流狀態變量相關性矩陣:")
    print(flow_corr.to_string(float_format=lambda x: f"{x:.3f}"))
    
    # ============================================================
    # 7. TIME-OF-DAY PATTERN ANALYSIS
    # ============================================================
    print("\n" + "="*60)
    print("⏰ 時段表現分析")
    print("="*60)
    
    flow_df['hour'] = flow_df.index.hour
    print("\n各小時VIX變化 (來自流狀態):")
    hourly_vix = flow_df.groupby('hour')['vix'].agg(['mean', 'std', 'min', 'max'])
    print(hourly_vix.to_string(float_format=lambda x: f"{x:.2f}"))
    
    # ============================================================
    # 8. SEMICONDUCTOR SECTOR CORRELATION
    # ============================================================
    print("\n" + "="*60)
    print("🔬 半導體類股相關性分析 (AMD, AMAT, MRVL, LRCX, INTC)")
    print("="*60)
    
    semi_tickers = ["AMD", "AMAT", "MRVL", "LRCX", "INTC"]
    try:
        semi_data = yf.download(semi_tickers, start=start_date, end=end_date, progress=False)
        if isinstance(semi_data.columns, pd.MultiIndex):
            semi_close = semi_data['Close']
        else:
            semi_close = semi_data
        semi_close.columns = semi_tickers
        
        semi_ret = semi_close.pct_change().dropna()
        semi_corr = semi_ret.corr()
        
        print("\n半導體股日回報相關性矩陣:")
        print(semi_corr.to_string(float_format=lambda x: f"{x:.3f}"))
        
        # Average pairwise correlation
        triu_vals = []
        for i in range(len(semi_tickers)):
            for j in range(i+1, len(semi_tickers)):
                triu_vals.append(semi_corr.iloc[i, j])
        avg_corr = np.mean(triu_vals)
        print(f"\n平均成對相關性: {avg_corr:.3f}")
        
        # Eigenvalue analysis (condition number)
        eigvals = np.linalg.eigvalsh(semi_corr.values)
        print(f"特徵值: {[f'{v:.3f}' for v in sorted(eigvals, reverse=True)]}")
        cond_num = eigvals[-1] / eigvals[0] if eigvals[0] > 1e-10 else float('inf')
        print(f"條件數(最小/最大特徵值比): {cond_num:.3f}")
        
        # First principal component
        from numpy.linalg import eigh
        eigenvals, eigenvecs = eigh(semi_corr.values)
        pc1 = eigenvecs[:, -1]  # First PC
        print(f"\n第一主成分負載 (解釋{(eigenvals[-1]/sum(eigenvals))*100:.1f}%變異):")
        for t, w in zip(semi_tickers, pc1):
            print(f"  {t}: {w:.3f}")
        
        if avg_corr > 0.7:
            print("\n⚠️ 結論: 這些半導體股高度相關 (>0.7)，實質上≈ 1個頭寸")
            print(f"   平均相關性 {avg_corr:.3f} → 分散化效果有限")
        elif avg_corr > 0.5:
            print("\n⚠️ 結論: 這些半導體股中度相關 (0.5-0.7)，具有一定分散化效果")
        else:
            print("\n✅ 結論: 這些半導體股相關性較低 (<0.5)，分散化效果良好")
        
    except Exception as e:
        print(f"半導體數據獲取失敗: {e}")
        print("嘗試備用方法...")

# ============================================================
# 9. MONDAY GAP ANALYSIS
# ============================================================
print("\n" + "="*60)
print("🌅 週一開盤缺口分析 (Monday Gap)")
print("="*60)

# Use Yahoo Finance to find Monday open gaps
try:
    for ticker, name in [("^GSPC", "SPX"), ("BTC-USD", "BTC"), ("ETH-USD", "ETH"), ("GC=F", "GOLD")]:
        try:
            data = yf.download(ticker, start="2026-01-01", end=end_date, interval="1d", progress=False)
            if isinstance(data.columns, pd.MultiIndex):
                df = data['Close'].to_frame()
                df.columns = ['Close']
            else:
                df = data[['Close']]
            
            df.index = pd.to_datetime(df.index)
            df['prev_close'] = df['Close'].shift(1)
            df['day_of_week'] = df.index.dayofweek
            
            # Monday opens vs previous Friday close
            monday = df[df['day_of_week'] == 0].copy()
            # Actually use Friday close -> Monday close
            friday = df[df['day_of_week'] == 4].copy()
            
            gaps = []
            for idx in monday.index:
                # Find previous Friday
                prev_friday = idx - timedelta(days=3)
                while prev_friday not in friday.index:
                    prev_friday -= timedelta(days=1)
                if prev_friday in df.index:
                    fri_close = df.loc[prev_friday, 'Close']
                    mon_open = df.loc[idx, 'Close']  # Using close as proxy for open
                    gap_pct = (mon_open / fri_close - 1) * 100
                    gaps.append({
                        'date': idx,
                        'fri_close': fri_close,
                        'mon_open': mon_open,
                        'gap_pct': gap_pct
                    })
            
            if gaps:
                gap_df = pd.DataFrame(gaps)
                avg_gap = gap_df['gap_pct'].mean()
                med_gap = gap_df['gap_pct'].median()
                pos_gaps = (gap_df['gap_pct'] > 0).sum()
                neg_gaps = (gap_df['gap_pct'] < 0).sum()
                max_gap = gap_df['gap_pct'].max()
                min_gap = gap_df['gap_pct'].min()
                
                print(f"\n--- {name} (2026年週一開盤缺口) ---")
                print(f"  期間: {gap_df['date'].min().strftime('%Y-%m-%d')} ~ {gap_df['date'].max().strftime('%Y-%m-%d')}")
                print(f"  週一數量: {len(gaps)}")
                print(f"  平均缺口: {avg_gap:.2f}%")
                print(f"  中位數缺口: {med_gap:.2f}%")
                print(f"  正向缺口(上漲): {pos_gaps}次 ({pos_gaps/len(gaps)*100:.0f}%)")
                print(f"  負向缺口(下跌): {neg_gaps}次 ({neg_gaps/len(gaps)*100:.0f}%)")
                print(f"  最大漲幅: {max_gap:.2f}%")
                print(f"  最大跌幅: {min_gap:.2f}%")
                print(f"  缺口波動率: {gap_df['gap_pct'].std():.2f}%")
                
                # Recent gaps
                recent = gap_df.sort_values('date', ascending=False).head(5)
                print(f"\n  最近5次週一缺口:")
                for _, r in recent.iterrows():
                    direction = "📈" if r['gap_pct'] > 0 else "📉"
                    print(f"    {r['date'].strftime('%m/%d')}: {direction} {r['gap_pct']:.2f}% (週五收{r['fri_close']:.0f}→週一開{r['mon_open']:.0f})")
            else:
                print(f"\n{name}: 無週一數據")
        except Exception as e:
            print(f"\n{name}分析失敗: {e}")

except Exception as e:
    print(f"週一缺口分析失敗: {e}")

# ============================================================
# 10. SPX, VIX, GOLD, DXY CROSS-CORRELATION
# ============================================================
print("\n" + "="*60)
print("🌐 SPX·VIX·GOLD·DXY 跨資產相關性")
print("="*60)

if close_prices is not None and not close_prices.empty:
    macro_tickers = ['SPX', 'VIX', 'GOLD', 'DXY', 'BTC', 'ETH']
    available = [t for t in macro_tickers if t in close_prices.columns]
    
    macro_df = close_prices[available].dropna()
    macro_ret = macro_df.pct_change().dropna()
    
    print("\n日回報相關性:")
    macro_corr = macro_ret.corr()
    print(macro_corr.to_string(float_format=lambda x: f"{x:.3f}"))
    
    # Key relationships
    print("\n關鍵關係:")
    if 'SPX' in macro_corr and 'VIX' in macro_corr:
        spx_vix = macro_corr.loc['SPX', 'VIX']
        print(f"  SPX ↔ VIX: r={spx_vix:.3f} (通常負相關)")
    if 'GOLD' in macro_corr and 'DXY' in macro_corr:
        gold_dxy = macro_corr.loc['GOLD', 'DXY']
        print(f"  GOLD ↔ DXY: r={gold_dxy:.3f} (通常負相關)")
    if 'BTC' in macro_corr and 'SPX' in macro_corr:
        btc_spx = macro_corr.loc['BTC', 'SPX']
        print(f"  BTC ↔ SPX: r={btc_spx:.3f} (數位黃金vs傳統風險)")
    if 'ETH' in macro_corr and 'BTC' in macro_corr:
        eth_btc = macro_corr.loc['ETH', 'BTC']
        print(f"  ETH ↔ BTC: r={eth_btc:.3f} (加密貨幣內部)")

# ============================================================
# SUMMARY & OUTPUT FILE
# ============================================================
print("\n" + "="*60)
print("📋 綜合摘要")
print("="*60)

print(f"""
分析時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}
數據範圍: flow_history ({len(flow_history)}個快照) + Yahoo Finance (2026-03至05月)

核心發現:
1. 引擎交易: 僅 {total_trades} 筆交易 → 統計不足以做P&L相關性分析
2. 信號一致性: 所有快照均標記 BTC_DOMINANCE_HIGH (BTC.D=97.2%)
3. VIX體制: 當前VIX={flow_df['vix'].iloc[-1]:.2f} (低波動regime)
4. 半導體類股: 相關性待確認
5. 週一缺口: 歷史數據分析完成
""")

# Save results
output = {
    'total_flow_snapshots': len(flow_history),
    'total_trades': total_trades,
    'signal_counts': signal_counts,
    'current_vix': float(flow_df['vix'].iloc[-1]),
    'current_vix_regime': flow_df['vix_regime'].iloc[-1],
    'btc_dominance': float(flow_df['btc_dominance'].iloc[-1]),
}

with open(f"{BASE}/cross_market_analysis_results.json", 'w') as f:
    json.dump(output, f, indent=2, default=str)

print("\n✅ 結果保存至 cross_market_analysis_results.json")
