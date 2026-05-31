#!/usr/bin/env python3
"""Fix Monday gap analysis - handle yfinance MultiIndex columns properly."""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

end_date = "2026-05-23"

gap_targets = [
    ("SPY", "SPX (SPY ETF)"),
    ("BTC-USD", "比特幣 (BTC)"),
    ("ETH-USD", "以太坊 (ETH)"),
    ("GLD", "黃金 (GLD ETF)"),
]

print("🌅 週一開盤缺口分析 (Monday Gap)")
print("="*60)

for ticker, name in gap_targets:
    try:
        df = yf.download(ticker, start="2025-01-01", end=end_date, progress=False)
        if df.empty or len(df) < 30:
            print(f"\n{name}: 數據不足 ({len(df)}行)")
            continue
        
        # yfinance returns MultiIndex columns even for single tickers
        # df['Close'] gives a Series
        close_col = df['Close']
        if isinstance(close_col, pd.DataFrame):
            close_series = close_col.iloc[:, 0]  # take first column
        else:
            close_series = close_col
        
        if not isinstance(close_series, pd.Series):
            print(f"\n{name}: 無法解析收盤價")
            continue
            
        close_series = close_series.dropna()
        
        # Build dataframe with day_of_week
        price_df = close_series.to_frame(name='Close')
        price_df['day_of_week'] = price_df.index.dayofweek
        
        # Find Monday vs previous Friday gaps
        gaps = []
        for i in range(1, len(price_df)):
            if price_df['day_of_week'].iloc[i] == 0:  # Monday
                mon_price = price_df['Close'].iloc[i]
                # Look back for the most recent preceding trading day
                for j in range(i-1, max(i-6, -1), -1):
                    prev_dow = price_df['day_of_week'].iloc[j]
                    if prev_dow == 4:  # Friday
                        fri_price = price_df['Close'].iloc[j]
                        gap_pct = (mon_price / fri_price - 1) * 100
                        gaps.append({
                            'date': price_df.index[i],
                            'fri_date': price_df.index[j],
                            'fri_close': float(fri_price),
                            'mon_close': float(mon_price),
                            'gap_pct': float(gap_pct)
                        })
                        break
                    elif prev_dow == 3:  # Thursday (holiday week fallback)
                        thu_price = price_df['Close'].iloc[j]
                        gap_pct = (mon_price / thu_price - 1) * 100
                        gaps.append({
                            'date': price_df.index[i],
                            'fri_date': price_df.index[j],
                            'fri_close': float(thu_price),
                            'mon_close': float(mon_price),
                            'gap_pct': float(gap_pct),
                            'note': '週四→週一(長週末)'
                        })
                        break
        
        if len(gaps) < 3:
            print(f"\n{name}: 僅{len(gaps)}個週一缺口數據")
            continue
            
        gap_df = pd.DataFrame(gaps)
        avg = gap_df['gap_pct'].mean()
        med = gap_df['gap_pct'].median()
        pos = (gap_df['gap_pct'] > 0).sum()
        neg = (gap_df['gap_pct'] < 0).sum()
        
        print(f"\n--- {name} ---")
        print(f"  分析期間: {gap_df['date'].min().strftime('%Y-%m-%d')} ~ {gap_df['date'].max().strftime('%Y-%m-%d')}")
        print(f"  週一數量: {len(gaps)}次")
        print(f"  平均缺口: {avg:+.2f}%")
        print(f"  中位數缺口: {med:+.2f}%")
        print(f"  📈 上漲開盤: {pos}次 ({pos/len(gaps)*100:.0f}%)")
        print(f"  📉 下跌開盤: {neg}次 ({neg/len(gaps)*100:.0f}%)")
        print(f"  最大漲幅開盤: {gap_df['gap_pct'].max():+.2f}%")
        print(f"  最大跌幅開盤: {gap_df['gap_pct'].min():+.2f}%")
        print(f"  缺口波動率(標準差): {gap_df['gap_pct'].std():.2f}%")
        
        # Recent 10 gaps
        recent = gap_df.sort_values('date', ascending=False).head(10)
        print(f"\n  最近10次週一開盤:")
        for _, r in recent.iterrows():
            arrow = "📈" if r['gap_pct'] > 0 else "📉"
            note = f" ({r.get('note','')})" if 'note' in r else ""
            print(f"    {r['date'].strftime('%m/%d')} {arrow} {r['gap_pct']:+.2f}%{note}")
            
    except Exception as e:
        import traceback
        print(f"\n{name} 分析失敗: {e}")
        traceback.print_exc()
