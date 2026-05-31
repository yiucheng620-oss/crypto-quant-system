#!/usr/bin/env python3
"""
vectorbt 策略回測引擎 v2
讀取 OHLCV 歷史數據，執行 RSI / BB / VP 策略參數掃描，
輸出 Sharpe Ratio、Max Drawdown、Win Rate 排名至 JSON。

支援資產: BTC_USD, ETH_USD, SOL_USD（從 history CSV 讀取）
手續費: 0.04%, 滑價: 0.05%
"""
import json
import os
from datetime import datetime, timezone
from itertools import product

import numpy as np
import vectorbt as vbt

# ── 設定 ──────────────────────────────────────────────
DATA_DIR = os.path.expanduser("~/market_data")
OUTPUT_DIR = os.path.expanduser("~/workspace/vbt_results")
os.makedirs(OUTPUT_DIR, exist_ok=True)

ASSETS = ["BTC_USD", "ETH_USD", "SOL_USD"]
COMMISSION_PCT = 0.0004   # 0.04%
SLIPPAGE = 0.0005         # 0.05%
INITIAL_CASH = 10_000.0
MIN_TRADES = 2            # 最少交易筆數, 低於此數不納入排名


# ── 載入數據 ──────────────────────────────────────────
def load_data(asset):
    """從 history CSV 載入 OHLCV"""
    path = os.path.join(DATA_DIR, f"{asset}_history.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到數據: {path}")
    df = vbt.YFData.df_to_ohlcv(path) if hasattr(vbt.YFData, 'df_to_ohlcv') else None

    # 手動載入
    import pandas as pd
    df = pd.read_csv(path, parse_dates=["Date"])
    df.set_index("Date", inplace=True)
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        if c not in df.columns:
            raise ValueError(f"{asset} CSV 缺少欄位: {c}")
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    df.dropna(inplace=True)
    print(f"  ✓ 載入 {asset}: {len(df)} 根 K 棒 ({df.index[0].date()} ~ {df.index[-1].date()})")
    return df


# ── 策略函式 ──────────────────────────────────────────
def run_rsi_strategy(df, window, oversold, overbought):
    """RSI 策略: RSI < oversold 做多, RSI > overbought 出場"""
    close = df["Close"]
    rsi = vbt.RSI.run(close, window=window)
    entries = rsi.rsi_crossed_below(oversold)
    exits = rsi.rsi_crossed_above(overbought)
    pf = vbt.Portfolio.from_signals(
        close, entries=entries, exits=exits,
        init_cash=INITIAL_CASH, fees=COMMISSION_PCT,
        slippage=SLIPPAGE, freq="1D",
    )
    return pf


def run_bb_strategy(df, window, alpha):
    """Bollinger Bands 策略: 觸及下軌做多, 觸及上軌出場"""
    close = df["Close"]
    bb = vbt.BBANDS.run(close, window=window, alpha=alpha)
    entries = close <= bb.lower
    exits = close >= bb.upper
    pf = vbt.Portfolio.from_signals(
        close, entries=entries, exits=exits,
        init_cash=INITIAL_CASH, fees=COMMISSION_PCT,
        slippage=SLIPPAGE, freq="1D",
    )
    return pf


def run_vp_strategy(df, vol_lookback, vol_mult):
    """
    改良 Volume Profile 策略（適用於日線）
    - 計算 VWAP 作為動態價值中樞
    - HVN = 成交量 > vol_ma * vol_mult（高量節點）
    - 趨勢跟蹤版本: HVN + 價格 > VWAP → 做多（確認上漲動能）
    - 出場: HVN + 價格 < VWAP 或持有超過 10 天
    """
    close = df["Close"]
    volume = df["Volume"]
    high = df["High"]
    low = df["Low"]

    # VWAP 計算
    tp = (high + low + close) / 3.0
    vwap = (tp * volume).rolling(vol_lookback).sum() / volume.rolling(vol_lookback).sum()

    # 成交量均線與 HVN
    vol_ma = volume.rolling(vol_lookback).mean()
    hvn = volume > (vol_ma * vol_mult)

    # 趨勢跟蹤: HVN 出現且價格在 VWAP 之上 = 動能確認做多
    entries = hvn & (close > vwap)
    # VP 出場: HVN 出現且價格跌破 VWAP = 動能衰竭
    exits = hvn & (close < vwap)

    pf = vbt.Portfolio.from_signals(
        close, entries=entries, exits=exits,
        init_cash=INITIAL_CASH, fees=COMMISSION_PCT,
        slippage=SLIPPAGE, freq="1D",
    )
    return pf


# ── 績效指標提取 ──────────────────────────────────────
def safe_float(v, default=None):
    if v is None:
        return default
    try:
        fv = float(v)
        return fv if np.isfinite(fv) else default
    except (ValueError, TypeError):
        return default


def extract_metrics(pf, label):
    stats = pf.stats()
    sharpe = stats.get("Sharpe Ratio", None)
    max_dd = stats.get("Max Drawdown [%]", stats.get("Max Drawdown (%)", None))
    wr = stats.get("Win Rate [%]", stats.get("Win Rate (%)", None))
    total_return = stats.get("Total Return [%]", stats.get("Total Return (%)", None))
    trades = stats.get("Total Closed Trades", stats.get("Total Trades", 0))

    sharpe_val = safe_float(sharpe)
    max_dd_val = safe_float(max_dd)
    wr_val = safe_float(wr, 0.0)
    total_return_val = safe_float(total_return)
    trades_val = int(trades) if trades is not None else 0

    return {
        "strategy": label,
        "sharpe": sharpe_val,
        "max_dd_pct": round(max_dd_val, 2) if max_dd_val is not None else None,
        "wr_pct": round(wr_val, 2) if wr_val is not None else 0.0,
        "trades": trades_val,
        "total_return_pct": round(total_return_val, 2) if total_return_val is not None else None,
    }


# ── 主回測流程 ──────────────────────────────────────
def backtest_all():
    rankings = []

    for asset in ASSETS:
        print(f"\n{'='*50}")
        print(f"  回測 {asset}")
        print(f"{'='*50}")
        try:
            df = load_data(asset)
        except FileNotFoundError as e:
            print(f"  ✗ {e}")
            continue

        # RSI 參數網格
        rsi_params = list(product(
            [14, 21],           # window
            [25, 30, 35],       # oversold
            [65, 70, 75],       # overbought
        ))
        # BB 參數網格
        bb_params = list(product(
            [20, 30],           # window
            [1.5, 2.0, 2.5],   # alpha
        ))
        # VP 參數網格
        vp_params = list(product(
            [15, 20, 30],       # vol_lookback
            [1.5, 2.0],         # vol_mult
        ))

        print(f"  RSI 參數組合: {len(rsi_params)} 組")
        print(f"  BB  參數組合: {len(bb_params)} 組")
        print(f"  VP  參數組合: {len(vp_params)} 組")

        # RSI
        for w, os, ob in rsi_params:
            label = f"RSI_{asset}_{w}_{os}_{ob}"
            try:
                pf = run_rsi_strategy(df, w, os, ob)
                rankings.append(extract_metrics(pf, label))
            except Exception as e:
                print(f"  ✗ {label}: {e}")

        # BB
        for w, a in bb_params:
            label = f"BB_{asset}_{w}_{a}"
            try:
                pf = run_bb_strategy(df, w, a)
                rankings.append(extract_metrics(pf, label))
            except Exception as e:
                print(f"  ✗ {label}: {e}")

        # VP
        for vl, vm in vp_params:
            label = f"VP_{asset}_{vl}_{vm}"
            try:
                pf = run_vp_strategy(df, vl, vm)
                rankings.append(extract_metrics(pf, label))
            except Exception as e:
                print(f"  ✗ {label}: {e}")

    return rankings


def save_results(rankings):
    # 過濾低交易量 & 無 Sharpe
    valid = [r for r in rankings if r["sharpe"] is not None and r["trades"] >= MIN_TRADES]
    no_data = [r for r in rankings if r["sharpe"] is None or r["trades"] < MIN_TRADES]

    ranked = sorted(valid, key=lambda x: x["sharpe"], reverse=True)
    # 按資產分類
    by_asset = {}
    for r in ranked:
        asset = r["strategy"].split("_")[1] + "_" + r["strategy"].split("_")[2]
        by_asset.setdefault(asset, []).append(r)

    output = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_strategies": len(rankings),
        "valid_strategies": len(valid),
        "top3_sharpe": ranked[:3] if len(ranked) >= 3 else ranked,
        "worst3_sharpe": list(reversed(ranked[-3:])) if len(ranked) >= 3 else list(reversed(ranked)),
        "best_per_asset": {k: v[0] for k, v in by_asset.items() if v},
        "rankings": ranked + no_data,
        "by_asset_summary": {
            k: {
                "best": v[0],
                "count": len(v),
                "avg_sharpe": round(np.mean([x["sharpe"] for x in v]), 4)
            }
            for k, v in by_asset.items()
        },
    }

    path = os.path.join(OUTPUT_DIR, "latest_ranking.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 結果已儲存至 {path}")
    print(f"   共 {len(rankings)} 個策略組合，{len(valid)} 個有效（≥{MIN_TRADES} 筆交易）")
    if ranked:
        print(f"   最佳 Sharpe: {ranked[0]['strategy']} = {ranked[0]['sharpe']:.4f}")
        print(f"   最差 Sharpe: {ranked[-1]['strategy']} = {ranked[-1]['sharpe']:.4f}")


if __name__ == "__main__":
    print(f"🚀 vectorbt 回測引擎 v2 ({datetime.now()})")
    print(f"   資產: {', '.join(ASSETS)}")
    print(f"   手續費: {COMMISSION_PCT*100:.2f}% | 滑價: {SLIPPAGE*100:.2f}%")
    print(f"   初始資金: ${INITIAL_CASH:,.0f} | 最少交易門檻: {MIN_TRADES}")
    rankings = backtest_all()
    save_results(rankings)
