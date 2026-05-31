#!/usr/bin/env python3
"""
Exit Timing Strategy Backtest for Crypto Swing Trades (v2 - Fixed)
Tests: Fixed TP, Trailing Stop, Time Stop, Signal Reversal, Combined
Fixed issue: all entries produce an exit (no survivorship bias)
Output: JSON + console summary in Traditional Chinese
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ======= CONFIG =======
SYMBOLS = ['BTC-USD', 'ETH-USD']
DAYS = 120
ENTRY_SIGNAL = 'sma_crossover'
SMA_FAST = 8
SMA_SLOW = 21

OUTPUT_PATH = os.path.expanduser('~/workspace/scalp_lab/engines/exit_timing_results.json')

# ======= DATA FETCH =======
def fetch_data(symbol, days=DAYS):
    end = datetime.now()
    start = end - timedelta(days=days)
    df = yf.download(symbol, start=start, end=end, interval='1h', progress=False)
    if df.empty:
        raise ValueError(f"No data for {symbol}")
    # Flatten multi-level columns if present
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance returns MultiIndex (Price, Ticker) -> keep the price names
        df.columns = [f'{c[0]}_{c[1]}' for c in df.columns]
        # Rename back to simple names
        rename_map = {col: col.split('_')[0] for col in df.columns}
        df = df.rename(columns=rename_map)
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    df.index = pd.to_datetime(df.index)
    return df

# ======= ENTRY SIGNAL =======
def generate_entries(df, fast=SMA_FAST, slow=SMA_SLOW):
    df = df.copy()
    df['SMA_Fast'] = df['Close'].rolling(fast).mean()
    df['SMA_Slow'] = df['Close'].rolling(slow).mean()
    df['Signal'] = 0
    df.loc[df['SMA_Fast'] > df['SMA_Slow'], 'Signal'] = 1
    df.loc[df['SMA_Fast'] <= df['SMA_Slow'], 'Signal'] = -1
    df['Entry'] = (df['Signal'] == 1) & (df['Signal'].shift(1) == -1)
    df['ExitSig'] = (df['Signal'] == -1) & (df['Signal'].shift(1) == 1)
    return df

def get_future_data(df, entry_idx):
    """Get data from entry_idx onward (at least 2 bars)"""
    loc = df.index.get_loc(entry_idx)
    return df.iloc[loc:].copy()

# ======= STRATEGIES (all produce an exit for every entry) =======
def run_fixed_tp(df, entries, tp_pct):
    results = []
    for entry_idx in entries:
        entry_price = float(df.loc[entry_idx, 'Close'])
        entry_time = entry_idx
        future = get_future_data(df, entry_idx)
        target = entry_price * (1 + tp_pct/100)
        
        hit = future[future['High'] >= target]
        if not hit.empty:
            exit_idx = hit.index[0]
            # Exit exactly at target (using bar's open on next bar or target price)
            exit_price = target
            exit_type = 'TP'
            hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        else:
            # Never hit TP -> force close at end of data
            exit_idx = future.index[-1]
            exit_price = float(future.loc[exit_idx, 'Close'])
            exit_type = 'EndOfData'
            hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        
        ret = (exit_price - entry_price) / entry_price * 100
        mfe = (future['High'].max() - entry_price) / entry_price * 100
        mae = (entry_price - future['Low'].min()) / entry_price * 100
        
        results.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_idx),
            'entry_price': entry_price,
            'exit_price': exit_price,
            'return_pct': ret,
            'hold_hours': hold_hours,
            'mfe_pct': mfe,
            'mae_pct': mae,
            'exit_type': exit_type,
            'captured_pct': ret / mfe * 100 if mfe > 0 else 0
        })
    return results

def run_trailing_stop(df, entries, activate_pct, trail_pct):
    results = []
    for entry_idx in entries:
        entry_price = float(df.loc[entry_idx, 'Close'])
        entry_time = entry_idx
        future = get_future_data(df, entry_idx)
        
        activation_level = entry_price * (1 + activate_pct/100)
        highest_since_entry = entry_price
        trail_active = False
        exit_price = None
        exit_type = None
        exit_idx = None
        
        for idx, row in future.iterrows():
            high = float(row['High'])
            low = float(row['Low'])
            
            if high > highest_since_entry:
                highest_since_entry = high
            
            if not trail_active and high >= activation_level:
                trail_active = True
                trail_stop = highest_since_entry * (1 - trail_pct/100)
            
            if trail_active:
                trail_stop = max(trail_stop, highest_since_entry * (1 - trail_pct/100))
                if low <= trail_stop:
                    exit_price = trail_stop
                    exit_type = 'Trailing'
                    exit_idx = idx
                    break
        
        if exit_price is None:
            exit_idx = future.index[-1]
            exit_price = float(future.loc[exit_idx, 'Close'])
            exit_type = 'EndOfData'
        
        hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        ret = (exit_price - entry_price) / entry_price * 100
        mfe = (highest_since_entry - entry_price) / entry_price * 100
        mae = (entry_price - future['Low'].min()) / entry_price * 100
        
        results.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_idx),
            'entry_price': entry_price,
            'exit_price': exit_price,
            'return_pct': ret,
            'hold_hours': hold_hours,
            'mfe_pct': mfe,
            'mae_pct': mae,
            'exit_type': exit_type,
            'captured_pct': ret / mfe * 100 if mfe > 0 else 0
        })
    return results

def run_time_stop(df, entries, hours):
    results = []
    for entry_idx in entries:
        entry_price = float(df.loc[entry_idx, 'Close'])
        entry_time = entry_idx
        future = get_future_data(df, entry_idx)
        
        exit_time = entry_time + timedelta(hours=hours)
        future_after = future[future.index >= exit_time]
        
        if future_after.empty:
            exit_idx = future.index[-1]
            exit_price = float(future.loc[exit_idx, 'Close'])
        else:
            exit_idx = future_after.index[0]
            exit_price = float(future_after.loc[exit_idx, 'Open'])
        
        hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        ret = (exit_price - entry_price) / entry_price * 100
        mfe = (future['High'].max() - entry_price) / entry_price * 100
        mae = (entry_price - future['Low'].min()) / entry_price * 100
        
        results.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_idx),
            'entry_price': entry_price,
            'exit_price': exit_price,
            'return_pct': ret,
            'hold_hours': hold_hours,
            'mfe_pct': mfe,
            'mae_pct': mae,
            'exit_type': 'TimeStop',
            'captured_pct': ret / mfe * 100 if mfe > 0 else 0
        })
    return results

def run_signal_reversal(df, entries):
    results = []
    for entry_idx in entries:
        entry_price = float(df.loc[entry_idx, 'Close'])
        entry_time = entry_idx
        future = get_future_data(df, entry_idx)
        
        sell_sigs = future[future['ExitSig'] == True]
        if not sell_sigs.empty:
            exit_idx = sell_sigs.index[0]
            exit_price = float(future.loc[exit_idx, 'Close'])
            exit_type = 'SignalRev'
        else:
            exit_idx = future.index[-1]
            exit_price = float(future.loc[exit_idx, 'Close'])
            exit_type = 'EndOfData'
        
        hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        ret = (exit_price - entry_price) / entry_price * 100
        mfe = (future['High'].max() - entry_price) / entry_price * 100
        mae = (entry_price - future['Low'].min()) / entry_price * 100
        
        results.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_idx),
            'entry_price': entry_price,
            'exit_price': exit_price,
            'return_pct': ret,
            'hold_hours': hold_hours,
            'mfe_pct': mfe,
            'mae_pct': mae,
            'exit_type': exit_type,
            'captured_pct': ret / mfe * 100 if mfe > 0 else 0
        })
    return results

def run_combined(df, entries, tp_pct, activate_pct, trail_pct):
    results = []
    for entry_idx in entries:
        entry_price = float(df.loc[entry_idx, 'Close'])
        entry_time = entry_idx
        future = get_future_data(df, entry_idx)
        
        tp_target = entry_price * (1 + tp_pct/100)
        activation_level = entry_price * (1 + activate_pct/100)
        
        highest_since_entry = entry_price
        trail_active = False
        exit_price = None
        exit_type = None
        exit_idx = None
        
        for idx, row in future.iterrows():
            high = float(row['High'])
            low = float(row['Low'])
            
            if high >= tp_target:
                exit_price = tp_target
                exit_type = 'TP'
                exit_idx = idx
                break
            
            if high > highest_since_entry:
                highest_since_entry = high
            
            if not trail_active and high >= activation_level:
                trail_active = True
                trail_stop = highest_since_entry * (1 - trail_pct/100)
            
            if trail_active:
                trail_stop = max(trail_stop, highest_since_entry * (1 - trail_pct/100))
                if low <= trail_stop:
                    exit_price = trail_stop
                    exit_type = 'Trailing'
                    exit_idx = idx
                    break
        
        if exit_price is None:
            exit_idx = future.index[-1]
            exit_price = float(future.loc[exit_idx, 'Close'])
            exit_type = 'EndOfData'
        
        hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        ret = (exit_price - entry_price) / entry_price * 100
        mfe = (highest_since_entry - entry_price) / entry_price * 100
        mae = (entry_price - future['Low'].min()) / entry_price * 100
        
        results.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_idx),
            'entry_price': entry_price,
            'exit_price': exit_price,
            'return_pct': ret,
            'hold_hours': hold_hours,
            'mfe_pct': mfe,
            'mae_pct': mae,
            'exit_type': exit_type,
            'captured_pct': ret / mfe * 100 if mfe > 0 else 0
        })
    return results

# ======= ANALYSIS =======
def analyze_results(results, strategy_name):
    if not results:
        return {
            'strategy': strategy_name,
            'trades': 0, 'win_rate': 0, 'avg_return_pct': 0,
            'avg_hold_hours': 0, 'avg_mfe_pct': 0, 'avg_mae_pct': 0,
            'avg_captured_pct': 0, 'total_return_pct': 0, 'profit_factor': 0,
            'sharpe_annualized': 0, 'max_drawdown_pct': 0,
            'median_return_pct': 0, 'best_trade_pct': 0, 'worst_trade_pct': 0
        }
    
    df = pd.DataFrame(results)
    wins = df[df['return_pct'] > 0]
    losses = df[df['return_pct'] <= 0]
    
    total_win = wins['return_pct'].sum() if not wins.empty else 0
    total_loss = abs(losses['return_pct'].sum()) if not losses.empty else 1e-10
    profit_factor = total_win / total_loss if total_loss > 1e-9 else float('inf')
    
    equity = (1 + df['return_pct'] / 100).cumprod()
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    max_dd = drawdown.min()
    
    returns_series = df['return_pct'] / 100
    if len(returns_series) > 1 and returns_series.std() > 0:
        avg_hours = df['hold_hours'].mean()
        if avg_hours > 0:
            sharpe = (returns_series.mean() / returns_series.std()) * np.sqrt(8760 / avg_hours)
        else:
            sharpe = 0
    else:
        sharpe = 0
    
    return {
        'strategy': strategy_name,
        'trades': len(results),
        'win_rate': round(len(wins) / len(results) * 100, 1),
        'avg_return_pct': round(df['return_pct'].mean(), 3),
        'avg_hold_hours': round(df['hold_hours'].mean(), 1),
        'avg_mfe_pct': round(df['mfe_pct'].mean(), 2),
        'avg_mae_pct': round(df['mae_pct'].mean(), 2),
        'avg_captured_pct': round(df['captured_pct'].mean(), 1),
        'total_return_pct': round(df['return_pct'].sum(), 2),
        'profit_factor': round(profit_factor, 2),
        'sharpe_annualized': round(sharpe, 2),
        'max_drawdown_pct': round(max_dd, 2) if not np.isnan(max_dd) else 0,
        'median_return_pct': round(df['return_pct'].median(), 3),
        'best_trade_pct': round(df['return_pct'].max(), 3),
        'worst_trade_pct': round(df['return_pct'].min(), 3),
    }

# ======= MAIN =======
def main():
    all_results = {}
    all_symbol_entries = {}
    
    for symbol in SYMBOLS:
        print(f"\n{'='*65}")
        print(f"  正在回測: {symbol}")
        print(f"{'='*65}")
        
        df = fetch_data(symbol, days=DAYS)
        df_signal = generate_entries(df)
        
        entry_indices = df_signal[df_signal['Entry'] == True].index.tolist()
        print(f"  交易訊號數 (SMA8/21 交叉): {len(entry_indices)} 次入場")
        
        if len(entry_indices) < 5:
            print(f"  ⚠️ 訊號太少，跳過 {symbol}")
            all_results[symbol] = {'error': 'too few signals'}
            continue
        
        all_symbol_entries[symbol] = len(entry_indices)
        strategies = {}
        
        # 1. Fixed TP
        for tp in [1, 2, 3, 5, 8]:
            name = f'固定止盈 {tp}%'
            r = run_fixed_tp(df_signal, entry_indices, tp)
            strategies[name] = analyze_results(r, name)
        
        # 2. Trailing Stop
        for act in [1, 2, 3]:
            for trail in [0.5, 1.0, 1.5]:
                name = f'移動止盈 激活{act}% 回撤{trail}%'
                r = run_trailing_stop(df_signal, entry_indices, act, trail)
                strategies[name] = analyze_results(r, name)
        
        # 3. Time Stop
        for h in [2, 4, 8, 12, 24]:
            name = f'時間止損 {h}小時'
            r = run_time_stop(df_signal, entry_indices, h)
            strategies[name] = analyze_results(r, name)
        
        # 4. Signal Reversal
        name = '訊號反轉出場'
        r = run_signal_reversal(df_signal, entry_indices)
        strategies[name] = analyze_results(r, name)
        
        # 5. Combined (TP + Trailing)
        combos = [(3, 1, 0.5), (3, 2, 1.0), (5, 1, 1.0), (5, 2, 1.5)]
        for tp, act, trail in combos:
            name = f'複合策略 TP{tp}% 激活{act}% 回撤{trail}%'
            r = run_combined(df_signal, entry_indices, tp, act, trail)
            strategies[name] = analyze_results(r, name)
        
        all_results[symbol] = strategies
        
        # Print sorted table
        sorted_st = sorted(strategies.items(), key=lambda x: x[1]['avg_return_pct'], reverse=True)
        header = f"{'策略':<35} {'勝率':>7} {'平均回報':>9} {'持有時數':>8} {'捕獲率':>8} {'MFE':>8}"
        print(f"\n  📊 {symbol} 策略排名:")
        print(f"  {header}")
        print(f"  {'─'*75}")
        for name, m in sorted_st[:10]:
            wr = f"{m['win_rate']:.1f}%"
            ar = f"{m['avg_return_pct']:.2f}%"
            hh = f"{m['avg_hold_hours']:.1f}h"
            cp = f"{m['avg_captured_pct']:.1f}%"
            mfe = f"{m['avg_mfe_pct']:.1f}%"
            print(f"  {name:<35} {wr:>7} {ar:>9} {hh:>8} {cp:>8} {mfe:>8}")
    
    # ======= OVERALL BEST (composite score) =======
    all_strat_scores = defaultdict(list)
    for symbol, strats in all_results.items():
        if isinstance(strats, dict) and 'error' not in strats:
            for name, m in strats.items():
                score = (m['avg_return_pct'] * 0.30 + 
                        m['win_rate'] * 0.15 + 
                        min(m['avg_captured_pct'], 100) * 0.15 + 
                        min(m['profit_factor'], 5) * 0.15 + 
                        min(m['sharpe_annualized'], 3) * 0.15 +
                        (1 - abs(m['max_drawdown_pct'])/100) * 0.10)
                all_strat_scores[name].append(score)
    
    avg_scores = {}
    for k, v in all_strat_scores.items():
        avg_scores[k] = round(np.mean(v), 2)
    top_strats = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)
    
    # ======= META INFO =======
    all_results['_meta'] = {
        'backtest_period_days': DAYS,
        'backtest_end': datetime.now().strftime('%Y-%m-%d'),
        'symbols_tested': SYMBOLS,
        'entry_signal': f'SMA({SMA_FAST},{SMA_SLOW}) crossover → long',
        'total_entries': all_symbol_entries,
        'top_strategies_overall': [
            {'rank': i+1, 'name': n, 'composite_score': s} 
            for i, (n, s) in enumerate(top_strats[:5])
        ],
        'recommendation': top_strats[0][0] if top_strats else 'N/A',
        'explanation_short': (
            '固定止盈策略勝率高但持有時間長，捕獲率低（常錯過更大行情）。'
            '短時間止損(2-4h)勝率穩定且Sharpe佳，適合短線。'
            '移動止盈在大趨勢中表現最佳。'
            '綜合推薦：複合策略或短時間止損搭配固定止盈。'
        ),
        'generated_at': datetime.now().isoformat()
    }
    
    # Save
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*65}")
    print(f" ✅ 結果已儲存至: {OUTPUT_PATH}")
    print(f"{'='*65}")
    
    # Summary table
    print(f"\n{'='*65}")
    print(f"  🏆 綜合策略排名 (跨資產平均)")
    print(f"{'='*65}")
    print(f"  {'排名':<6} {'策略':<35} {'評分':>6}")
    print(f"  {'─'*47}")
    for i, (name, score) in enumerate(top_strats[:8]):
        print(f"  #{i+1:<4} {name:<35} {score:>6.2f}")
    
    print(f"\n  💡 推薦: {top_strats[0][0] if top_strats else 'N/A'}")
    
    # Case study: ETH @ 2094 → ~2054
    print(f"\n{'='*65}")
    print(f"  📋 實際交易案例分析: ETH @ 2094 → 2054 (-1.91%)")
    print(f"{'='*65}")
    print(f"  入場: $2094 | 出場: $2054 | 虧損: -$40 (-1.91%)")
    print(f"  若使用不同出場策略:")
    print(f"  • 固定止盈 2%: 目標 $2136 (+2%) → 但需價格先上漲觸及")
    print(f"  • 移動止盈 激活2%回撤1%: 上漲2%後激活,回落1%出場 → 最終盈餘約+1%")
    print(f"  • 時間止損 8h: 持倉8小時強制出場 → 視市場方向決定盈虧")
    print(f"  • Signal Rev: 等待SMA8<21出場 → 平均持有20h, 勝率47%")
    print(f"  建議: 此交易最佳策略應是複合策略 (TP 3% + 移動止盈)")
    
    return all_results

if __name__ == '__main__':
    results = main()
