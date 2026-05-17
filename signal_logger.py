#!/usr/bin/env python3
"""
Signal Accuracy Tracker — 核心引擎
═══════════════════════════════════════════════════════
功能：
  1. 從 Binance fetch 最新 OHLCV → append to 本地 NPZ
  2. Run 5 個 indicator → generate signals
  3. Log signals to SQLite (去重）
  4. Back-check 24h/48h ago signals → 計 accuracy
  5. Classify market condition (Bull/Range/Volatility)
  6. 每週產出 CSV 俾 Gemini 分析

Run: python3 signal_logger.py [--force] [--backtest]
Cron: 0 */4 * * *
"""

import os, sys, json, time, sqlite3, argparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import numpy as np
import requests

# ──── CONFIG ────
DATA_DIR = os.path.expanduser("~/market_data/phase1")
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
REPORT_DIR = os.path.expanduser("~/workspace/accuracy_reports")
HKT = timezone(timedelta(hours=8))

# Indicator configs — survivors (7 total: 4 Long + 3 Short, WFA-validated)
INDICATORS = {
    # ── LONG ──
    # VP_BTC_4h_L — WFA: 77.8% Bull, 100% Range, 100% Bear (24h)
    "VP_BTC_4h_L": {
        "coin": "BTC", "tf": "4h", "fn": "vp_signal", "direction": "LONG",
        "params": {"vp_lookback": 20, "dist_pct": 1.0, "vol_thresh": 2.0, "vol_lookback": 30},
    },
    "VP_ETH_4h_L": {
        "coin": "ETH", "tf": "4h", "fn": "vp_signal", "direction": "LONG",
        "params": {"vp_lookback": 20, "dist_pct": 1.0, "vol_thresh": 2.0, "vol_lookback": 30},
    },
    "OBV_BTC_1h_L": {
        "coin": "BTC", "tf": "1h", "fn": "obv_signal", "direction": "LONG",
        "params": {"smooth": 8, "diverge_lookback": 10, "sig_mult": 1.3, "cooldown": 12},
    },
    "OBV_SOL_1h_L": {
        "coin": "SOL", "tf": "1h", "fn": "obv_signal", "direction": "LONG",
        "params": {"smooth": 8, "diverge_lookback": 10, "sig_mult": 1.3, "cooldown": 12},
    },
    # ── SHORT ──
    # RSI_ETH_1h_S — WFA: 83.6% Bull, 100% Bear, 80.8% Range (24h)
    "RSI_ETH_1h_S": {
        "coin": "ETH", "tf": "1h", "fn": "rsi_short_signal", "direction": "SHORT",
        "params": {"overbought": 70},
    },
    # VP_BTC_1h_S — WFA: 89.5% Bull 48h, 78.9% Bull 24h, 71.4% Bear 24h
    "VP_BTC_1h_S": {
        "coin": "BTC", "tf": "1h", "fn": "vp_short_signal", "direction": "SHORT",
        "params": {"vp_lookback": 20, "dist_pct": 0.5, "vol_thresh": 1.75, "vol_lookback": 30},
    },
    # VP_SOL_1h_S — WFA: 72.7% Bear 24h, 78.6% Bull 48h
    "VP_SOL_1h_S": {
        "coin": "SOL", "tf": "1h", "fn": "vp_short_signal", "direction": "SHORT",
        "params": {"vp_lookback": 20, "dist_pct": 0.5, "vol_thresh": 1.75, "vol_lookback": 30},
    },
}

# TFs we care about for data fetching
TFS_NEEDED = {"BTC": ["1h", "4h"], "ETH": ["1h", "4h"], "SOL": ["1h", "4h"]}
BINANCE_INTERVALS = {"1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}

# ──── DATABASE SETUP ────

def init_db():
    """Create tables if not exists."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,          -- Unix timestamp of signal bar
            coin TEXT NOT NULL,                   -- BTC/ETH/SOL
            timeframe TEXT NOT NULL,              -- 1h/4h/1d
            indicator TEXT NOT NULL,              -- VP/BB/OBV
            price REAL NOT NULL,                  -- close price at signal
            direction TEXT DEFAULT 'LONG',        -- LONG only for now
            created_at INTEGER NOT NULL,          -- When we logged it
            UNIQUE(timestamp, coin, timeframe, indicator)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS accuracy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            checked_at INTEGER NOT NULL,          -- When we checked
            horizon TEXT NOT NULL,                -- 24h / 48h
            result TEXT NOT NULL,                 -- WIN / LOSS / PENDING
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            pnl_pct REAL,
            market_condition TEXT,                -- bull/range/volatile
            FOREIGN KEY (signal_id) REFERENCES signals(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS market_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            coin TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            condition TEXT NOT NULL,              -- bull/range/volatile
            adx REAL, volatility_pct REAL,
            trend_strength REAL
        )
    """)
    conn.commit()
    conn.close()


# ──── DATA FETCHING ────

def fetch_binance_klines(symbol: str, interval: str, limit: int = 100) -> list:
    """Fetch klines from Binance public API. Returns list of [open_time, open, high, low, close, volume]."""
    url = f"https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        result = []
        for k in data:
            result.append({
                "ts": k[0],  # Binance returns milliseconds
                "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]),
                "volume": float(k[5]),
            })
        return result
    except Exception as e:
        print(f"[ERROR] Binance fetch {symbol} {interval}: {e}")
        return []


def update_npz(coin: str, tf: str, new_data: list) -> np.ndarray:
    """Append new bars to existing NPZ, return full arrays."""
    path = os.path.join(DATA_DIR, f"{coin}_{tf}.npz")
    if os.path.exists(path):
        old = np.load(path)
        old_close = old["close"]
        old_ts = old["timestamp"]
    else:
        old_close = np.array([])
        old_ts = np.array([])

    if not new_data:
        return {"close": old_close, "timestamp": old_ts}

    new_ts = np.array([d["ts"] for d in new_data])
    new_close = np.array([d["close"] for d in new_data])
    new_open = np.array([d["open"] for d in new_data])
    new_high = np.array([d["high"] for d in new_data])
    new_low = np.array([d["low"] for d in new_data])
    new_vol = np.array([d["volume"] for d in new_data])

    if len(old_ts) > 0:
        # Only append bars newer than last known
        mask = new_ts > old_ts[-1]
        if not mask.any():
            print(f"  [skip] {coin} {tf}: no new bars (last: {old_ts[-1]})")
            return {"close": old_close, "timestamp": old_ts,
                    "open": old.get("open", old_close), "high": old.get("high", old_close),
                    "low": old.get("low", old_close), "volume": old.get("volume", np.zeros_like(old_close))}

        new_ts = new_ts[mask]
        new_close = new_close[mask]
        new_open = new_open[mask]
        new_high = new_high[mask]
        new_low = new_low[mask]
        new_vol = new_vol[mask]
        print(f"  [update] {coin} {tf}: +{len(new_ts)} bars (last: {new_ts[-1]})")

        all_close = np.concatenate([old_close, new_close])
        all_ts = np.concatenate([old_ts, new_ts])
        all_open = np.concatenate([old.get("open", old_close), new_open])
        all_high = np.concatenate([old.get("high", old_close), new_high])
        all_low = np.concatenate([old.get("low", old_close), new_low])
        all_vol = np.concatenate([old.get("volume", np.zeros(len(old_close))), new_vol])
    else:
        all_close = new_close
        all_ts = new_ts
        all_open = new_open
        all_high = new_high
        all_low = new_low
        all_vol = new_vol

    # Save updated NPZ
    np.savez_compressed(path,
                        open=all_open, high=all_high, low=all_low,
                        close=all_close, volume=all_vol, timestamp=all_ts)
    return {"close": all_close, "timestamp": all_ts,
            "open": all_open, "high": all_high, "low": all_low, "volume": all_vol}


# ──── MARKET CONDITION CLASSIFIER ────

def classify_market(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                    lookback: int = 50) -> dict:
    """
    Classify market state: bull / range / volatile
    Returns {"condition": str, "adx": float, "volatility_pct": float, "trend_strength": float}
    """
    if len(close) < lookback + 5:
        return {"condition": "unknown", "adx": 0, "volatility_pct": 0, "trend_strength": 0}

    recent = close[-lookback:]
    recent_h = high[-lookback:]
    recent_l = low[-lookback:]

    # Volatility: ATR(14) / close %
    tr = np.maximum(recent_h - recent_l,
                    np.maximum(np.abs(recent_h - np.roll(recent, 1)),
                               np.abs(recent_l - np.roll(recent, 1))))
    tr[0] = recent_h[0] - recent_l[0]
    atr14 = np.mean(tr[-14:])
    vol_pct = (atr14 / recent[-1]) * 100

    # Trend: linear regression slope over lookback
    x = np.arange(lookback)
    slope, _ = np.polyfit(x, recent, 1)
    slope_pct = (slope / recent[-1]) * 100 * lookback  # Normalized trend strength

    # Simple ADX approximation
    up_move = np.diff(recent_h)
    down_move = -np.diff(recent_l)
    up_move = np.where(up_move > 0, up_move, 0)
    down_move = np.where(down_move > 0, down_move, 0)
    tr_arr = np.maximum(recent_h[1:] - recent_l[1:],
                         np.maximum(np.abs(recent_h[1:] - recent[:-1]),
                                    np.abs(recent_l[1:] - recent[:-1])))

    def rma(arr, period=14):
        result = np.zeros_like(arr)
        result[period-1] = np.mean(arr[:period])
        for i in range(period, len(arr)):
            result[i] = (result[i-1] * (period-1) + arr[i]) / period
        return result

    tr_rma = rma(tr_arr)
    up_rma = rma(up_move)
    down_rma = rma(down_move)

    with np.errstate(divide='ignore', invalid='ignore'):
        plus_di = np.where(tr_rma > 0, 100 * up_rma / tr_rma, 0)
        minus_di = np.where(tr_rma > 0, 100 * down_rma / tr_rma, 0)
        dx = np.where(plus_di + minus_di > 0,
                      100 * np.abs(plus_di - minus_di) / (plus_di + minus_di), 0)
    adx = rma(dx)[-1] if len(dx) > 14 else np.mean(dx[-14:])

    # Classification
    if vol_pct > 3.0:
        condition = "volatile"
    elif abs(slope_pct) > 1.5 and adx > 25:
        condition = "bull" if slope_pct > 0 else "bear"
    else:
        condition = "range"

    return {
        "condition": condition,
        "adx": round(adx, 1),
        "volatility_pct": round(vol_pct, 2),
        "trend_strength": round(slope_pct, 2),
    }


# ──── SIGNAL GENERATION ────

def get_indicator_fn(fn_name: str):
    """Import indicator function from final_indicators.py."""
    sys.path.insert(0, os.path.expanduser("~/workspace"))
    from final_indicators import vp_signal, bb_signal, obv_signal
    from final_indicators import vp_short_signal, bb_short_signal, obv_short_signal, rsi_short_signal
    return {
        "vp_signal": vp_signal, "bb_signal": bb_signal, "obv_signal": obv_signal,
        "vp_short_signal": vp_short_signal, "bb_short_signal": bb_short_signal,
        "obv_short_signal": obv_short_signal, "rsi_short_signal": rsi_short_signal,
    }[fn_name]


def run_indicator(data: dict, indicator_name: str, config: dict) -> list:
    """
    Run indicator and return list of signal dicts:
    [{timestamp, price, direction}]
    """
    fn = get_indicator_fn(config["fn"])
    close = data["close"]
    volume = data["volume"]
    params = config["params"]
    direction = config.get("direction", "LONG")

    sig_array = fn(close, volume, **params)

    signals = []
    ts_arr = data["timestamp"]
    for i in range(len(sig_array)):
        if sig_array[i] == 1:
            signals.append({
                "timestamp": int(ts_arr[i]),
                "price": float(close[i]),
                "direction": direction,
            })
    return signals


# ──── SIGNAL LOGGING ────

def log_signals(indicator_name: str, config: dict, signals: list):
    """Log new signals to SQLite, skip duplicates."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = int(time.time())
    coin = config["coin"]
    tf = config["tf"]
    new_count = 0

    for sig in signals:
        try:
            c.execute("""
                INSERT OR IGNORE INTO signals (timestamp, coin, timeframe, indicator, price, direction, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (sig["timestamp"], coin, tf, indicator_name, sig["price"], sig["direction"], now))
            if c.rowcount > 0:
                new_count += 1
        except Exception as e:
            print(f"  [ERROR] logging signal: {e}")

    conn.commit()
    conn.close()
    if new_count > 0:
        print(f"  [NEW] {indicator_name}: {new_count} signals logged")


# ──── ACCURACY BACK-CHECK ────

def check_accuracy(horizon_hours: int = 24):
    """
    Check signals that were created (horizon_hours +/- 2h ago) against current price.
    Marks WIN if price moved in signal direction, LOSS otherwise.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = int(time.time())
    window_start = now - (horizon_hours + 2) * 3600
    window_end = now - (horizon_hours - 2) * 3600

    # Find signals to check
    c.execute("""
        SELECT s.id, s.timestamp, s.coin, s.timeframe, s.indicator, s.price, s.direction
        FROM signals s
        LEFT JOIN accuracy a ON s.id = a.signal_id AND a.horizon = ?
        WHERE s.created_at BETWEEN ? AND ?
        AND a.id IS NULL
    """, (f"{horizon_hours}h", window_start, window_end))

    pending = c.fetchall()
    checked = 0

    for sig_id, sig_ts, coin, tf, indicator, entry_price, direction in pending:
        # Fetch current data for this coin/tf (use latest from NPZ)
        data = load_data(coin, tf)
        if data is None:
            continue

        # Find the bar closest to sig_ts + horizon_hours
        # sig_ts is in milliseconds (Binance), horizon_hours in hours
        target_ts = sig_ts + horizon_hours * 3600 * 1000
        ts_arr = data["timestamp"]
        close_arr = data["close"]

        # Find closest bar to target_ts (must be within 2 bars of horizon)
        idx = np.searchsorted(ts_arr, target_ts)
        if idx >= len(ts_arr):
            idx = len(ts_arr) - 1

        exit_price = float(close_arr[idx])
        actual_hours = (ts_arr[idx] - sig_ts) / 3600

        if actual_hours < horizon_hours * 0.7:  # Too early, skip
            continue

        # Check result
        pnl = (exit_price - entry_price) / entry_price
        if direction == "LONG":
            result = "WIN" if pnl > 0 else "LOSS"
        else:  # SHORT
            result = "WIN" if pnl < 0 else "LOSS"  # WIN when price goes DOWN
            pnl = -pnl  # Store positive PnL for winning short trades

        # Market condition at SIGNAL TIME (entry bar), not exit time
        sig_idx = np.searchsorted(ts_arr, sig_ts)
        mc = classify_market(close_arr[:sig_idx+1], data["high"][:sig_idx+1], data["low"][:sig_idx+1])

        c.execute("""
            INSERT INTO accuracy (signal_id, checked_at, horizon, result,
                                  entry_price, exit_price, pnl_pct, market_condition)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (sig_id, now, f"{horizon_hours}h", result,
              entry_price, exit_price, round(pnl * 100, 3),
              mc["condition"]))
        checked += 1

    conn.commit()
    conn.close()
    if checked > 0:
        print(f"  [CHECK] {horizon_hours}h: {checked} signals checked")


def historical_backtest():
    """
    Backtest mode: generate signals from first 70% of data,
    check accuracy against last 30% of data.
    """
    all_accuracy = []

    for ind_name, config in INDICATORS.items():
        coin, tf = config["coin"], config["tf"]
        data = load_data(coin, tf)
        if data is None:
            continue

        n = len(data["close"])
        split = int(n * 0.7)

        # Generate signals from training data
        train_data = {k: v[:split] for k, v in data.items()}
        signals = run_indicator(train_data, ind_name, config)

        print(f"\n  [{ind_name}] {len(signals)} signals in training set")

    # Check each signal against forward data
        for sig in signals:
            sig_ts = sig["timestamp"]
            entry_price = sig["price"]
            direction = sig.get("direction", "LONG")  # Get direction from config

            # Find this bar index in full data
            idx = np.searchsorted(data["timestamp"], sig_ts)
            if idx >= n - 5:
                continue

            # Check 24h and 48h ahead
            for horizon in [24, 48]:
                # Map hours to bars for this TF
                tf_hours = {"1h": 1, "4h": 4, "1d": 24}.get(tf, 1)
                bars = horizon // tf_hours

                target_idx = idx + bars
                if target_idx >= n:
                    continue

                exit_price = float(data["close"][target_idx])
                pnl = (exit_price - entry_price) / entry_price
                if direction == "LONG":
                    result = "WIN" if pnl > 0 else "LOSS"
                else:  # SHORT: WIN when price goes DOWN
                    result = "WIN" if pnl < 0 else "LOSS"
                    pnl = -pnl  # Store as positive for winning shorts
                # Market condition at signal time
                mc = classify_market(
                    data["close"][:idx+1], data["high"][:idx+1], data["low"][:idx+1]
                )

                # Log directly
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                now = int(time.time())
                # Insert signal if not exists
                c.execute("""
                    INSERT OR IGNORE INTO signals (timestamp, coin, timeframe, indicator, price, direction, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (sig_ts, coin, tf, ind_name, entry_price, direction, now))
                sig_id = c.lastrowid
                if sig_id == 0:
                    c.execute("SELECT id FROM signals WHERE timestamp=? AND coin=? AND timeframe=? AND indicator=?",
                              (sig_ts, coin, tf, ind_name))
                    sig_id = c.fetchone()[0]

                c.execute("""
                    INSERT OR REPLACE INTO accuracy (signal_id, checked_at, horizon, result,
                                    entry_price, exit_price, pnl_pct, market_condition)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (sig_id, now, f"{horizon}h", result,
                      entry_price, exit_price, round(pnl * 100, 3),
                      mc["condition"]))
                conn.commit()
                conn.close()

                all_accuracy.append({
                    "indicator": ind_name, "horizon": f"{horizon}h",
                    "result": result, "pnl_pct": round(pnl * 100, 3),
                    "market_condition": mc["condition"]
                })

    # Print summary
    print(f"\n{'='*60}")
    print(f"Historical Backtest Summary ({len(all_accuracy)} checks)")
    print(f"{'='*60}")

    # Group by (indicator, horizon, market_condition)
    groups = defaultdict(list)
    for a in all_accuracy:
        key = (a["indicator"], a["horizon"], a["market_condition"])
        groups[key].append(a)

    for (ind, hor, mc), items in sorted(groups.items()):
        wins = sum(1 for it in items if it["result"] == "WIN")
        losses = sum(1 for it in items if it["result"] == "LOSS")
        total = wins + losses
        wr = round(wins / total * 100, 1) if total > 0 else 0
        pnls = [it["pnl_pct"] for it in items]
        avg_pnl = round(sum(pnls) / len(pnls), 3) if pnls else 0
        print(f"  {ind} | {hor} | {mc:8s} | {total:3d} signals | {wr:5.1f}% WR | avg PnL: {avg_pnl:+.3f}%")

    print(f"\n  → Run --report to generate CSV for Gemini analysis")
    return all_accuracy


def load_data(coin: str, tf: str):
    """Load NPZ data for coin/timeframe."""
    path = os.path.join(DATA_DIR, f"{coin}_{tf}.npz")
    if not os.path.exists(path):
        return None
    d = np.load(path)
    return {"close": d["close"], "timestamp": d["timestamp"],
            "open": d["open"], "high": d["high"],
            "low": d["low"], "volume": d["volume"]}


# ──── WEEKLY REPORT GENERATION ────

def generate_accuracy_csv(days: int = 90):
    """Generate CSV of accuracy stats for Gemini analysis."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Per-indicator accuracy
    c.execute("""
        SELECT s.indicator, s.coin, s.timeframe, a.horizon, a.market_condition,
               COUNT(*) as total,
               SUM(CASE WHEN a.result='WIN' THEN 1 ELSE 0 END) as wins,
               AVG(CASE WHEN a.result='WIN' THEN a.pnl_pct ELSE NULL END) as avg_win,
               AVG(CASE WHEN a.result='LOSS' THEN a.pnl_pct ELSE NULL END) as avg_loss
        FROM accuracy a
        JOIN signals s ON a.signal_id = s.id
        WHERE a.checked_at > ?
        GROUP BY s.indicator, s.coin, s.timeframe, a.horizon, a.market_condition
        HAVING total >= 3
        ORDER BY s.indicator, a.horizon, a.market_condition
    """, (int(time.time()) - days * 86400,))

    rows = c.fetchall()
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"accuracy_{datetime.now(HKT).strftime('%Y%m%d')}.csv")

    with open(path, "w") as f:
        f.write("indicator,coin,timeframe,horizon,market_condition,total,wins,win_rate,avg_win_pct,avg_loss_pct\n")
        for r in rows:
            wr = round(r["wins"] / r["total"] * 100, 1) if r["total"] > 0 else 0
            f.write(f"{r['indicator']},{r['coin']},{r['timeframe']},{r['horizon']},{r['market_condition']},"
                    f"{r['total']},{r['wins']},{wr},{round(r['avg_win'] or 0, 3)},{round(r['avg_loss'] or 0, 3)}\n")

    conn.close()

    # Also generate summary JSON for Gemini
    summary = _build_summary_json(days)
    json_path = os.path.join(REPORT_DIR, f"summary_{datetime.now(HKT).strftime('%Y%m%d')}.json")
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"[REPORT] CSV: {path}")
    print(f"[REPORT] JSON: {json_path}")
    return path, json_path


def _build_summary_json(days: int = 90) -> dict:
    """Build structured summary for Gemini analysis."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    cutoff = int(time.time()) - days * 86400

    # Overall stats
    c.execute("""
        SELECT s.indicator, s.coin, s.timeframe, COUNT(*) as total,
               SUM(CASE WHEN a.result='WIN' THEN 1 ELSE 0 END) as wins,
               AVG(CASE WHEN a.result='WIN' THEN a.pnl_pct ELSE NULL END) as avg_win,
               AVG(CASE WHEN a.result='LOSS' THEN a.pnl_pct ELSE NULL END) as avg_loss
        FROM accuracy a
        JOIN signals s ON a.signal_id = s.id
        WHERE a.checked_at > ?
        GROUP BY s.indicator, s.coin, s.timeframe
    """, (cutoff,))

    indicators = []
    for r in c.fetchall():
        wr = round(r["wins"] / r["total"] * 100, 1) if r["total"] > 0 else 0
        indicators.append({
            "name": r["indicator"],
            "coin": r["coin"],
            "tf": r["timeframe"],
            "total_signals": r["total"],
            "win_rate": wr,
            "avg_win_pct": round(r["avg_win"] or 0, 3),
            "avg_loss_pct": round(r["avg_loss"] or 0, 3),
        })

    # Per-market-condition breakdown (matrix)
    c.execute("""
        SELECT s.indicator, a.market_condition, COUNT(*) as total,
               SUM(CASE WHEN a.result='WIN' THEN 1 ELSE 0 END) as wins
        FROM accuracy a
        JOIN signals s ON a.signal_id = s.id
        WHERE a.checked_at > ? AND a.horizon='24h'
        GROUP BY s.indicator, a.market_condition
        HAVING total >= 3
    """, (cutoff,))

    matrix = defaultdict(dict)
    for r in c.fetchall():
        wr = round(r["wins"] / r["total"] * 100, 1) if r["total"] > 0 else 0
        matrix[r["indicator"]][r["market_condition"]] = {"total": r["total"], "win_rate": wr}
    matrix_dict = {k: dict(v) for k, v in matrix.items()}

    # Current market conditions
    current_state = {}
    for coin in ["ETH", "SOL"]:
        for tf in ["1h", "4h"]:
            data = load_data(coin, tf)
            if data:
                mc = classify_market(data["close"], data["high"], data["low"])
                current_state[f"{coin}_{tf}"] = mc

    conn.close()
    return {
        "generated_at": datetime.now(HKT).isoformat(),
        "period_days": days,
        "indicators": indicators,
        "accuracy_matrix": matrix_dict,
        "current_market_state": current_state,
    }


# ──── MAIN ORCHESTRATOR ────

def main():
    parser = argparse.ArgumentParser(description="Signal Accuracy Tracker")
    parser.add_argument("--force", action="store_true", help="Force re-fetch data")
    parser.add_argument("--backtest", action="store_true",
                        help="Run backtest mode: generate all signals from history")
    parser.add_argument("--report", action="store_true", help="Generate weekly report CSV+JSON")
    parser.add_argument("--check", type=int, default=24, help="Hours horizon for accuracy check")
    args = parser.parse_args()

    init_db()
    print(f"[{datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}] Signal Accuracy Tracker")

    if args.report:
        print("\n=== Generating Weekly Report ===")
        csv_path, json_path = generate_accuracy_csv()
        print(f"\nReport ready. Next step: feed {json_path} to Gemini for analysis.")
        return

    # 1. Fetch latest data
    print("\n--- Data Fetch ---")
    for coin, tfs in TFS_NEEDED.items():
        for tf in tfs:
            symbol = f"{coin}USDT"
            interval = BINANCE_INTERVALS[tf]
            klines = fetch_binance_klines(symbol, interval, limit=100)
            update_npz(coin, tf, klines)

    # 2. Run indicators
    print("\n--- Signal Generation ---")

    # Check if backtest mode
    if args.backtest:
        print("\n=== Historical Backtest (70/30 split) ===")
        # Clear existing data for clean backtest
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM accuracy")
        c.execute("DELETE FROM signals")
        conn.commit()
        conn.close()
        historical_backtest()
        print("\nBacktest complete. Generating report...")
        csv_path, json_path = generate_accuracy_csv()
        print(f"\n📊 CSV: {csv_path}")
        print(f"📊 JSON: {json_path}")
        print(f"\nNext: python3 weekly_analyzer.py to get Gemini analysis")

        # CLEANUP: Remove historical signals to prevent polluting live alerts
        # Only keep signals from last 30 days (live window)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        recent_cutoff = int(time.time()) - 30 * 86400  # 30 days ago in seconds
        c.execute("DELETE FROM signals WHERE timestamp < ?", (recent_cutoff * 1000,))
        cleaned = c.rowcount
        c.execute("DELETE FROM accuracy WHERE signal_id NOT IN (SELECT id FROM signals)")
        orphaned = c.rowcount
        conn.commit()
        conn.close()
        if cleaned > 0:
            print(f"\n🧹 Cleaned {cleaned} historical signals + {orphaned} orphaned accuracy (live alert protection)")
        return

    for ind_name, config in INDICATORS.items():
        coin, tf = config["coin"], config["tf"]
        data = load_data(coin, tf)
        if data is None:
            print(f"  [SKIP] {ind_name}: no data")
            continue

        # Only generate signals for the last 200 bars to save time
        data_slice = {k: v[-200:] for k, v in data.items()}
        signals = run_indicator(data_slice, ind_name, config)
        log_signals(ind_name, config, signals)

    # 3. Check accuracy for past signals
    print("\n--- Accuracy Back-Check ---")
    for horizon in [24, 48]:
        check_accuracy(horizon)

    # 4. Current market state
    print("\n--- Market State ---")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = int(time.time())
    for coin in ["BTC", "ETH", "SOL"]:
        for tf in ["1h", "4h"]:
            data = load_data(coin, tf)
            if data:
                mc = classify_market(data["close"], data["high"], data["low"])
                c.execute("""
                    INSERT INTO market_state (timestamp, coin, timeframe, condition, adx, volatility_pct, trend_strength)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (now, coin, tf, mc["condition"], mc["adx"], mc["volatility_pct"], mc["trend_strength"]))
                print(f"  {coin} {tf}: {mc['condition']} (ADX={mc['adx']}, Vol={mc['volatility_pct']}%)")
    conn.commit()
    conn.close()

    # 5. Total signal count
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM signals")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM accuracy")
    total_acc = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT indicator) FROM signals")
    ind_count = c.fetchone()[0]
    conn.close()

    print(f"\n=== Summary ===")
    print(f"  Total signals: {total} ({ind_count} indicators)")
    print(f"  Accuracy checks: {total_acc}")
    print(f"  Next: run --report to generate weekly CSV")


if __name__ == "__main__":
    main()
