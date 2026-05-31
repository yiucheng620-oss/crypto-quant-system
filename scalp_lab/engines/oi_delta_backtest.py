#!/usr/bin/env python3
"""
OI Delta Edge Backtest for BTC & ETH
Tests OI delta thresholds as swing trading signals.
Uses Binance futures OI data via ccxt + yfinance for price.
Outputs results in Traditional Chinese.
"""

import json
import numpy as np
import pandas as pd
import yfinance as yf
import ccxt
import warnings
from datetime import datetime, timezone
warnings.filterwarnings('ignore')

BASE = "/home/yiucheng620/workspace/scalp_lab/engines"
OUTPUT_PATH = f"{BASE}/oi_delta_backtest_results.json"

# ============================================================
# Data fetching
# ============================================================

def fetch_oi_4h(symbol_ccxt, limit=500):
    """Fetch OI history from Binance via ccxt (max ~30 days of 4h data)."""
    exchange = ccxt.binanceusdm()
    oi_raw = exchange.fetch_open_interest_history(symbol_ccxt, '4h', limit=limit)
    records = []
    for item in oi_raw:
        records.append({
            "timestamp": item['timestamp'] / 1000,  # ms to s
            "open_interest_btc": item['openInterestAmount'],
            "oi_value_usdt": item['openInterestValue']
        })
    df = pd.DataFrame(records)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
    df = df.sort_values('timestamp').reset_index(drop=True)
    return df

def fetch_price_4h(symbol_yf, start, end):
    """Fetch 4h OHLCV from yfinance."""
    df = yf.download(symbol_yf, start=start, end=end, interval="4h", progress=False)
    if df.empty:
        return None
    # Flatten MultiIndex columns
    df.columns = [col[0].lower() for col in df.columns]
    df = df.reset_index()
    df = df.rename(columns={'Datetime': 'timestamp'})
    return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].copy()

# ============================================================
# Metrics calculation
# ============================================================

def calc_metrics(returns):
    """Calculate performance metrics from a series of trade returns."""
    if len(returns) == 0:
        return {"count": 0, "win_rate": 0, "avg_return": 0, "total_return": 0,
                "sharpe": 0, "max_dd": 0, "profit_factor": 0}

    returns = np.array(returns)
    wins = returns[returns > 0]
    losses = returns[returns < 0]

    win_rate = len(wins) / len(returns) if len(returns) > 0 else 0
    avg_return = float(np.mean(returns))
    total_return = float(np.sum(returns))

    # Sharpe (annualized, using 4h returns -> 2190 periods per year)
    std = np.std(returns, ddof=1)
    if std > 1e-10:
        sharpe = float((np.mean(returns) / std) * np.sqrt(2190))
    else:
        sharpe = 0.0

    # Max drawdown of cumulative returns
    cum_returns = np.cumsum(returns)
    if len(cum_returns) > 0:
        peak = np.maximum.accumulate(cum_returns)
        dd = cum_returns - peak
        max_dd = float(abs(np.min(dd)))
    else:
        max_dd = 0.0

    # Profit factor
    gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = float(abs(np.sum(losses))) if len(losses) > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 1e-10 else (999.0 if gross_profit > 0 else 0.0)

    return {
        "count": len(returns),
        "win_rate": round(win_rate, 4),
        "avg_return": round(avg_return, 6),
        "total_return": round(total_return, 4),
        "sharpe": round(sharpe, 4),
        "max_dd": round(max_dd, 4),
        "profit_factor": round(profit_factor, 4)
    }

# ============================================================
# Backtest engine
# ============================================================

def run_backtest(merged_df, thresholds, hold_periods):
    """
    Backtest OI delta edge.

    For each threshold, when OI delta < -threshold%, enter SHORT.
    For LONG, when OI delta > +threshold%, enter LONG.
    Exit after N periods (hold periods).
    Trades are non-overlapping — after a signal, skip ahead by hold_period.
    """
    prices = merged_df['close'].values
    deltas = merged_df['oi_delta_pct'].values
    n = len(prices)

    results = {}

    for direction, label in [("SHORT", "做空"), ("LONG", "做多")]:
        direction_results = {}
        for threshold in thresholds:
            for hold in hold_periods:
                trades = []
                i = 1  # start from index 1 (need previous OI for delta)
                while i < n - 1:
                    trigger = False
                    if direction == "SHORT" and deltas[i] < -threshold:
                        trigger = True
                    elif direction == "LONG" and deltas[i] > threshold:
                        trigger = True

                    if trigger:
                        entry_price = prices[i]
                        exit_idx = min(i + hold, n - 1)
                        exit_price = prices[exit_idx]

                        if direction == "SHORT":
                            ret = (entry_price - exit_price) / entry_price
                        else:
                            ret = (exit_price - entry_price) / entry_price

                        trades.append(ret)
                        i = exit_idx  # non-overlapping
                    else:
                        i += 1

                metrics = calc_metrics(trades)
                direction_results[f"threshold_{threshold}%_hold_{hold}"] = metrics

        results[direction] = direction_results

    return results

# ============================================================
# Main
# ============================================================

def main():
    print("=" * 60)
    print("OI Delta 邊際效應回測 — BTC & ETH")
    print("=" * 60)

    thresholds = [0.5, 1.0, 2.0, 3.0, 5.0]
    hold_periods = [1, 2, 4, 8]  # 4h candles

    end_date = "2026-05-23"
    start_date = "2026-01-01"

    all_results = {}

    for coin_info in [
        ("BTC", "BTC-USD", "BTC/USDT:USDT"),
        ("ETH", "ETH-USD", "ETH/USDT:USDT"),
    ]:
        name, yf_symbol, ccxt_symbol = coin_info
        print(f"\n{'=' * 40}")
        print(f"  {name}")
        print(f"{'=' * 40}")

        # --- 1. Price ---
        print(f"  [{name}] 下載價格 (yfinance 4H)...")
        price_df = fetch_price_4h(yf_symbol, start_date, end_date)
        if price_df is None or len(price_df) < 50:
            print(f"  [{name}] 價格數據不足，跳過")
            continue
        print(f"  [{name}] {len(price_df)} 根4H蠟燭")

        # --- 2. OI ---
        print(f"  [{name}] 下載持倉量 (Binance 4H)...")
        oi_df = fetch_oi_4h(ccxt_symbol, limit=500)
        if oi_df is None or len(oi_df) < 20:
            print(f"  [{name}] OI 數據不足，跳過")
            continue
        print(f"  [{name}] {len(oi_df)} 根4H蠟燭")
        print(f"      時間範圍: {oi_df['timestamp'].min()} ~ {oi_df['timestamp'].max()}")

        # --- 3. Merge ---
        # Make timestamps tz-naive for merging
        price_aligned = price_df.set_index('timestamp')
        if price_aligned.index.tz is not None:
            price_aligned.index = price_aligned.index.tz_localize(None)
        oi_aligned = oi_df.set_index('timestamp')
        if oi_aligned.index.tz is not None:
            oi_aligned.index = oi_aligned.index.tz_localize(None)
        merged = price_aligned[['close']].join(oi_aligned[['oi_value_usdt']], how='inner')
        # Also compute OI delta on the merged data
        merged['oi_delta_pct'] = merged['oi_value_usdt'].pct_change() * 100.0
        merged = merged.dropna().copy()
        print(f"  [{name}] 合併後數據點: {len(merged)}")

        if len(merged) < 30:
            print(f"  [{name}] 合併後數據不足，跳過")
            continue

        # --- 4. OI delta stats ---
        deltas = merged['oi_delta_pct'].values
        print(f"\n  [{name}] OI Delta 統計:")
        print(f"      平均值: {np.mean(deltas):+.4f}%")
        print(f"      標準差: {np.std(deltas):.4f}%")
        print(f"      最大值: {np.max(deltas):+.4f}%")
        print(f"      最小值: {np.min(deltas):+.4f}%")
        p95 = float(np.percentile(np.abs(deltas), 95))
        p99 = float(np.percentile(np.abs(deltas), 99))
        print(f"      |OI Delta| P95: {p95:.4f}%, P99: {p99:.4f}%")

        # Signal counts
        print(f"\n  [{name}] 閾值觸發次數:")
        for direction, label in [("SHORT", "做空"), ("LONG", "做多")]:
            for thresh in thresholds:
                if direction == "SHORT":
                    cnt = int(np.sum(deltas < -thresh))
                else:
                    cnt = int(np.sum(deltas > thresh))
                print(f"      {label} OI Δ{'<-' if direction == 'SHORT' else '>'} {thresh:+.1f}%: {cnt} 次 ({cnt/len(deltas)*100:.1f}%)")

        # --- 5. Run backtest ---
        print(f"\n  [{name}] 執行回測...")
        results = run_backtest(merged, thresholds, hold_periods)

        # --- 6. Print tables ---
        all_best = {"sharpe": -999, "info": None}

        for direction, label in [("SHORT", "做空"), ("LONG", "做多")]:
            print(f"\n  [{name}] {label} 信號回測結果:")
            header = f"  {'閾值%':>7} | {'Hold':>4} | {'交易次數':>6} | {'勝率':>7} | {'平均回報':>8} | {'Sharpe':>7} | {'MaxDD':>7} | {'ProfitF':>7}"
            sep = "  " + "-" * 7 + "-+-" + "-" * 4 + "-+-" + "-" * 6 + "-+-" + "-" * 7 + "-+-" + "-" * 8 + "-+-" + "-" * 7 + "-+-" + "-" * 7 + "-+-" + "-" * 7
            print(header)
            print(sep)

            for thresh in thresholds:
                for hold in hold_periods:
                    key = f"threshold_{thresh}%_hold_{hold}"
                    r = results[direction][key]
                    sharpe = r['sharpe']
                    if sharpe > all_best["sharpe"] and r['count'] >= 3:
                        all_best["sharpe"] = sharpe
                        all_best["info"] = (name, direction, thresh, hold, r)

                    print(f"  {thresh:>7.1f} | {hold:>2d}   | {r['count']:>5d}   | {r['win_rate']*100:>5.1f}%  | {r['avg_return']*100:>+6.2f}% | {r['sharpe']:>6.2f}  | {r['max_dd']*100:>5.2f}% | {r['profit_factor']:>6.2f}")

        # Store results
        all_results[name] = {
            "price_data_points": len(price_df),
            "oi_data_points": len(oi_df),
            "merged_points": len(merged),
            "oi_delta_stats": {
                "mean": round(float(np.mean(deltas)), 4),
                "std": round(float(np.std(deltas)), 4),
                "min": round(float(np.min(deltas)), 4),
                "max": round(float(np.max(deltas)), 4),
                "abs_p95": round(p95, 4),
                "abs_p99": round(p99, 4),
            },
            "results": results
        }

        if all_best["info"]:
            nm, dr, th, hd, r = all_best["info"]
            print(f"\n  ⭐ [{nm}] 最佳組合: {dr} | 閾值={th}% | Hold={hd}期")
            print(f"      Sharpe={r['sharpe']:.2f}, 勝率={r['win_rate']*100:.1f}%, 交易={r['count']}次")
            print(f"      平均回報={r['avg_return']*100:.2f}%, MaxDD={r['max_dd']*100:.2f}%")

    # --- Save ---
    with open(OUTPUT_PATH, 'w') as f:
        # Convert numpy types for JSON
        class NpEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, (np.integer,)):
                    return int(obj)
                if isinstance(obj, (np.floating,)):
                    return float(obj)
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
                return super().default(obj)
        json.dump(all_results, f, indent=2, cls=NpEncoder, ensure_ascii=False)
    print(f"\n✅ 結果已儲存到: {OUTPUT_PATH}")

    # ============================================================
    # Final summary
    # ============================================================
    print("\n" + "=" * 60)
    print("📊 總結與交易建議")
    print("=" * 60)

    print("\n📌 OI Delta 邊際效應分析:")
    print("  OI Delta（持倉量變化率）衡量市場上未平倉合約的增減幅。")
    print("  當 OI 大幅增長：新資金入場，趨勢可能延續。")
    print("  當 OI 大幅縮減：資金離場，趨勢可能反轉。")

    print("\n📌 各幣種最佳策略:")
    for coin_name in ["BTC", "ETH"]:
        if coin_name not in all_results:
            continue
        coin_best = {"sharpe": -999, "info": None}
        for direction in ["SHORT", "LONG"]:
            for key, r in all_results[coin_name]["results"][direction].items():
                if r['sharpe'] > coin_best['sharpe'] and r['count'] >= 3:
                    coin_best['sharpe'] = r['sharpe']
                    coin_best['info'] = (direction, key, r)
        if coin_best['info']:
            d, k, r = coin_best['info']
            print(f"\n  {coin_name}:")
            print(f"    策略: {d} | 參數: {k}")
            print(f"    Sharpe: {r['sharpe']:.2f} | 勝率: {r['win_rate']*100:.1f}%")
            print(f"    交易次數: {r['count']} | 平均回報: {r['avg_return']*100:.2f}%")
            print(f"    最大回撤: {r['max_dd']*100:.2f}% | Profit Factor: {r['profit_factor']:.2f}")

    print("\n📌 結論:")
    print("  OI Delta 可作為輔助信號使用，但單獨作為進場依據效果有限。")
    print("  建議與價格動能、訂單簿失衡等其他信號結合使用。")
    print("  回測數據範圍約30天（Binance API限制），結果僅供參考。")

if __name__ == "__main__":
    main()
