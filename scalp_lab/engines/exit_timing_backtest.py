#!/usr/bin/env python3
"""
Exit Timing Strategy Backtest for Crypto Swing Trades
Tests: Fixed TP, Trailing Stop, Time Stop, Signal Reversal, Combined
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
DAYS = 120  # >90 backtest window
ENTRY_SIGNAL = 'sma_crossover'  # SMA crossover entry signal
SMA_FAST = 8
SMA_SLOW = 21
RISK_PER_TRADE = 0.02  # 2% risk per trade
COMMISSION = 0.001  # 0.1% per trade (typical exchange fee)

OUTPUT_PATH = os.path.expanduser('~/workspace/scalp_lab/engines/exit_timing_results.json')

# ======= DATA FETCH =======
def fetch_data(symbol, days=DAYS):
    """Fetch hourly OHLC data"""
    end = datetime.now()
    start = end - timedelta(days=days)
    df = yf.download(symbol, start=start, end=end, interval='1h', progress=False)
    if df.empty:
        raise ValueError(f"No data for {symbol}")
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
    df.index = pd.to_datetime(df.index)
    return df

# ======= ENTRY SIGNAL: SMA Crossover =======
def generate_entries(df, fast=SMA_FAST, slow=SMA_SLOW):
    """Generate buy/sell signals using SMA crossover"""
    df = df.copy()
    df['SMA_Fast'] = df['Close'].rolling(fast).mean()
    df['SMA_Slow'] = df['Close'].rolling(slow).mean()
    df['Signal'] = 0
    df.loc[df['SMA_Fast'] > df['SMA_Slow'], 'Signal'] = 1
    df.loc[df['SMA_Fast'] <= df['SMA_Slow'], 'Signal'] = -1
    df['Entry'] = (df['Signal'] == 1) & (df['Signal'].shift(1) == -1)
    df['ExitSig'] = (df['Signal'] == -1) & (df['Signal'].shift(1) == 1)
    return df

# ======= EXIT STRATEGIES =======
def run_fixed_tp(df, entries, tp_pct):
    """Exit at fixed take-profit percentage"""
    results = []
    for entry_idx in entries:
        entry_price = df.loc[entry_idx, 'Close']
        entry_time = entry_idx
        target = entry_price * (1 + tp_pct/100)
        
        # Find when price hits target
        future = df.loc[entry_idx:]
        hit = future[future['High'] >= target]
        if not hit.empty:
            exit_idx = hit.index[0]
            exit_price = target
            exit_type = 'TP'
        else:
            continue  # No exit within data window
        
        hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        ret = (exit_price - entry_price) / entry_price * 100
        mfe = (future['High'].max() - entry_price) / entry_price * 100
        mae = (entry_price - future['Low'].min()) / entry_price * 100
        
        results.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_idx),
            'entry_price': float(entry_price),
            'exit_price': float(exit_price),
            'return_pct': float(ret),
            'hold_hours': float(hold_hours),
            'mfe_pct': float(mfe),
            'mae_pct': float(mae),
            'exit_type': exit_type,
            'captured_pct': float(ret / mfe * 100) if mfe > 0 else 0
        })
    return results

def run_trailing_stop(df, entries, activate_pct, trail_pct):
    """Trailing stop: activate at activate_pct gain, trail by trail_pct"""
    results = []
    for entry_idx in entries:
        entry_price = df.loc[entry_idx, 'Close']
        entry_time = entry_idx
        future = df.loc[entry_idx:].copy()
        
        activation_level = entry_price * (1 + activate_pct/100)
        highest_since_entry = entry_price
        trail_active = False
        exit_price = None
        exit_type = None
        exit_idx = None
        
        for idx, row in future.iterrows():
            high = row['High']
            low = row['Low']
            
            # Track highest
            if high > highest_since_entry:
                highest_since_entry = high
            
            # Check activation
            if not trail_active and high >= activation_level:
                trail_active = True
                trail_stop = highest_since_entry * (1 - trail_pct/100)
            
            # Update trailing stop
            if trail_active:
                trail_stop = max(trail_stop, highest_since_entry * (1 - trail_pct/100))
                if low <= trail_stop:
                    exit_price = trail_stop
                    exit_type = 'Trailing'
                    exit_idx = idx
                    break
        
        if exit_price is None:
            # No exit triggered; close at end
            exit_idx = future.index[-1]
            exit_price = future.loc[exit_idx, 'Close']
            exit_type = 'EndOfData'
        
        hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        ret = (exit_price - entry_price) / entry_price * 100
        mfe = (highest_since_entry - entry_price) / entry_price * 100
        mae = (entry_price - future['Low'].min()) / entry_price * 100
        
        results.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_idx),
            'entry_price': float(entry_price),
            'exit_price': float(exit_price),
            'return_pct': float(ret),
            'hold_hours': float(hold_hours),
            'mfe_pct': float(mfe),
            'mae_pct': float(mae),
            'exit_type': exit_type,
            'captured_pct': float(ret / mfe * 100) if mfe > 0 else 0
        })
    return results

def run_time_stop(df, entries, hours):
    """Exit after N hours regardless of price"""
    results = []
    for entry_idx in entries:
        entry_price = df.loc[entry_idx, 'Close']
        entry_time = entry_idx
        future = df.loc[entry_idx:].copy()
        
        exit_time = entry_time + timedelta(hours=hours)
        
        # Find nearest bar after time stop
        future = future[future.index >= exit_time]
        if future.empty:
            continue
        exit_idx = future.index[0]
        exit_price = future.loc[exit_idx, 'Open']  # Exit at open
        
        hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        ret = (exit_price - entry_price) / entry_price * 100
        future_full = df.loc[entry_idx:]
        mfe = (future_full['High'].max() - entry_price) / entry_price * 100
        mae = (entry_price - future_full['Low'].min()) / entry_price * 100
        
        results.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_idx),
            'entry_price': float(entry_price),
            'exit_price': float(exit_price),
            'return_pct': float(ret),
            'hold_hours': float(hold_hours),
            'mfe_pct': float(mfe),
            'mae_pct': float(mae),
            'exit_type': 'TimeStop',
            'captured_pct': float(ret / mfe * 100) if mfe > 0 else 0
        })
    return results

def run_signal_reversal(df, entries):
    """Exit when opposite signal appears (SMA crossover back)"""
    results = []
    for entry_idx in entries:
        entry_price = df.loc[entry_idx, 'Close']
        entry_time = entry_idx
        future = df.loc[entry_idx:].copy()
        
        # Find next sell signal
        sell_sigs = future[future['ExitSig'] == True]
        if not sell_sigs.empty:
            exit_idx = sell_sigs.index[0]
            exit_price = future.loc[exit_idx, 'Close']
            exit_type = 'SignalRev'
        else:
            exit_idx = future.index[-1]
            exit_price = future.loc[exit_idx, 'Close']
            exit_type = 'EndOfData'
        
        hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        ret = (exit_price - entry_price) / entry_price * 100
        mfe = (future['High'].max() - entry_price) / entry_price * 100
        mae = (entry_price - future['Low'].min()) / entry_price * 100
        
        results.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_idx),
            'entry_price': float(entry_price),
            'exit_price': float(exit_price),
            'return_pct': float(ret),
            'hold_hours': float(hold_hours),
            'mfe_pct': float(mfe),
            'mae_pct': float(mae),
            'exit_type': exit_type,
            'captured_pct': float(ret / mfe * 100) if mfe > 0 else 0
        })
    return results

def run_combined(df, entries, tp_pct, activate_pct, trail_pct):
    """TP first, then trailing stop if TP not hit"""
    results = []
    for entry_idx in entries:
        entry_price = df.loc[entry_idx, 'Close']
        entry_time = entry_idx
        future = df.loc[entry_idx:].copy()
        
        tp_target = entry_price * (1 + tp_pct/100)
        activation_level = entry_price * (1 + activate_pct/100)
        
        highest_since_entry = entry_price
        trail_active = False
        exit_price = None
        exit_type = None
        exit_idx = None
        
        for idx, row in future.iterrows():
            high = row['High']
            low = row['Low']
            
            # Check TP first
            if high >= tp_target:
                exit_price = tp_target
                exit_type = 'TP'
                exit_idx = idx
                break
            
            # Track highest
            if high > highest_since_entry:
                highest_since_entry = high
            
            # Check trailing activation
            if not trail_active and high >= activation_level:
                trail_active = True
                trail_stop = highest_since_entry * (1 - trail_pct/100)
            
            # Update trailing stop
            if trail_active:
                trail_stop = max(trail_stop, highest_since_entry * (1 - trail_pct/100))
                if low <= trail_stop:
                    exit_price = trail_stop
                    exit_type = 'Trailing'
                    exit_idx = idx
                    break
        
        if exit_price is None:
            exit_idx = future.index[-1]
            exit_price = future.loc[exit_idx, 'Close']
            exit_type = 'EndOfData'
        
        hold_hours = (exit_idx - entry_time).total_seconds() / 3600
        ret = (exit_price - entry_price) / entry_price * 100
        mfe = (highest_since_entry - entry_price) / entry_price * 100
        mae = (entry_price - future['Low'].min()) / entry_price * 100
        
        results.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_idx),
            'entry_price': float(entry_price),
            'exit_price': float(exit_price),
            'return_pct': float(ret),
            'hold_hours': float(hold_hours),
            'mfe_pct': float(mfe),
            'mae_pct': float(mae),
            'exit_type': exit_type,
            'captured_pct': float(ret / mfe * 100) if mfe > 0 else 0
        })
    return results

# ======= ANALYSIS =======
def analyze_results(results, strategy_name):
    """Calculate aggregate metrics"""
    if not results:
        return {
            'strategy': strategy_name,
            'trades': 0,
            'win_rate': 0,
            'avg_return_pct': 0,
            'avg_hold_hours': 0,
            'avg_mfe_pct': 0,
            'avg_mae_pct': 0,
            'avg_captured_pct': 0,
            'total_return_pct': 0,
            'profit_factor': 0,
            'sharpe_hourly': 0,
            'max_drawdown_pct': 0
        }
    
    df = pd.DataFrame(results)
    wins = df[df['return_pct'] > 0]
    losses = df[df['return_pct'] <= 0]
    
    total_win = wins['return_pct'].sum() if not wins.empty else 0
    total_loss = abs(losses['return_pct'].sum()) if not losses.empty else 1
    profit_factor = total_win / total_loss if total_loss > 0 else float('inf')
    
    # Calculate running equity curve for max drawdown
    equity = (1 + df['return_pct'] / 100).cumprod()
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    max_dd = drawdown.min()
    
    # Hourly Sharpe (annualized)
    returns_series = df['return_pct'] / 100
    if len(returns_series) > 1 and returns_series.std() > 0:
        avg_hours = df['hold_hours'].mean()
        sharpe_hourly = (returns_series.mean() / returns_series.std()) * np.sqrt(8760 / avg_hours) if avg_hours > 0 else 0
    else:
        sharpe_hourly = 0
    
    return {
        'strategy': strategy_name,
        'trades': len(results),
        'win_rate': float(len(wins) / len(results) * 100),
        'avg_return_pct': float(df['return_pct'].mean()),
        'avg_hold_hours': float(df['hold_hours'].mean()),
        'avg_mfe_pct': float(df['mfe_pct'].mean()),
        'avg_mae_pct': float(df['mae_pct'].mean()),
        'avg_captured_pct': float(df['captured_pct'].mean()),
        'total_return_pct': float(df['return_pct'].sum()),
        'profit_factor': float(profit_factor),
        'sharpe_annualized': float(sharpe_hourly),
        'max_drawdown_pct': float(max_dd) if not np.isnan(max_dd) else 0,
        'median_return_pct': float(df['return_pct'].median()),
        'best_trade_pct': float(df['return_pct'].max()),
        'worst_trade_pct': float(df['return_pct'].min()),
    }

# ======= MAIN =======
def main():
    all_results = {}
    
    for symbol in SYMBOLS:
        print(f"\n{'='*60}")
        print(f"  正在回測: {symbol}")
        print(f"{'='*60}")
        
        df = fetch_data(symbol, days=DAYS)
        df_signal = generate_entries(df)
        
        # Get entry indices
        entry_indices = df_signal[df_signal['Entry'] == True].index.tolist()
        print(f"  交易訊號數: {len(entry_indices)} 次入場")
        
        if len(entry_indices) < 5:
            print(f"  ⚠️ 訊號太少，跳過 {symbol}")
            all_results[symbol] = {'error': 'too few signals'}
            continue
        
        # 1. Fixed TP
        strategies = {}
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
        combos = [
            (3, 1, 0.5), (3, 2, 1.0), (5, 1, 1.0), (5, 2, 1.5)
        ]
        for tp, act, trail in combos:
            name = f'複合策略 TP{tp}% 激活{act}% 回撤{trail}%'
            r = run_combined(df_signal, entry_indices, tp, act, trail)
            strategies[name] = analyze_results(r, name)
        
        all_results[symbol] = strategies
        
        # Print top 5
        sorted_st = sorted(strategies.items(), key=lambda x: x[1]['avg_return_pct'], reverse=True)
        print(f"\n  📊 {symbol} 策略排名 (by 平均回報):")
        print(f"  {'策略':<30} {'勝率':>8} {'平均回報':>10} {'平均持有':>10} {'捕獲率':>8}")
        print(f"  {'-'*66}")
        for name, metrics in sorted_st[:8]:
            print(f"  {name:<30} {metrics['win_rate']:>7.1f}% {metrics['avg_return_pct']:>9.2f}% {metrics['avg_hold_hours']:>8.1f}h {metrics['avg_captured_pct']:>7.1f}%")
    
    # ======= OVERALL BEST =======
    # Find best strategy across all symbols
    all_strat_scores = defaultdict(list)
    for symbol, strats in all_results.items():
        if isinstance(strats, dict) and 'error' not in strats:
            for name, m in strats.items():
                # Composite score: higher is better
                score = m['avg_return_pct'] * 0.3 + m['win_rate'] * 0.2 + m['avg_captured_pct'] * 0.2 + m['profit_factor'] * 0.15 + min(m['sharpe_annualized'], 5) * 0.15
                all_strat_scores[name].append(score)
    
    avg_scores = {k: np.mean(v) for k, v in all_strat_scores.items()}
    top_strats = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)
    
    all_results['_meta'] = {
        'backtest_period_days': DAYS,
        'symbols_tested': SYMBOLS,
        'entry_signal': f'SMA({SMA_FAST},{SMA_SLOW}) crossover',
        'total_trades_per_symbol': {s: len(entry_indices) for s in SYMBOLS},
        'top_strategies_overall': [
            {'name': n, 'composite_score': round(s, 2)} for n, s in top_strats[:5]
        ],
        'recommendation': top_strats[0][0] if top_strats else 'N/A',
        'generated_at': datetime.now().isoformat()
    }
    
    # Save
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"\n{'='*60}")
    print(f" ✅ 結果已儲存至: {OUTPUT_PATH}")
    print(f"{'='*60}")
    
    # Print recommendation
    print(f"\n🏆 綜合推薦策略: {top_strats[0][0] if top_strats else 'N/A'}")
    print(f"   複合評分: {top_strats[0][1]:.2f}" if top_strats else "")
    
    return all_results

if __name__ == '__main__':
    results = main()
