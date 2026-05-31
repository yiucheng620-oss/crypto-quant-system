#!/usr/bin/env python3
"""
BTC Micro-Domain Scanner — Simplified Domain Score + Fee-Aware Signal
=====================================================================
基於 BTC microstructure 研究（Woonggyu Lee, 2026）嘅核心發現：
  - Domain Score ≥ 4 → Spearman -0.183，16/16 WFA folds 全正
  - 但扣 taker fee（~8bp round-trip）後未盈利
  - 改良方向：Rolling SP filter + Regime filter + Min move threshold

簡化版用 Binance 免費 API 數據（唔需要 tick-level data）：
  1. Order book depth → Spread expansion
  2. Recent trades → Order flow persistence + Trade speed
  3. K-lines → Volatility regime
  4. Combined → Domain Score (0-6)

Output: 每分鐘 scan，Domain Score ≥ 3 + fee-aware check → TG alert
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
BINANCE_REST = "https://api.binance.com/api/v3"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
CACHE_FILES = {
    "BTCUSDT": os.path.expanduser("~/workspace/scalp_lab/engines/btc_micro_state.json"),
    "ETHUSDT": os.path.expanduser("~/workspace/scalp_lab/engines/eth_micro_state.json"),
}

# Fee hurdle: 0.04% taker × 2 (round-trip) = 8bp + 2bp buffer = 10bp
FEE_HURDLE_BP = 10.0
MIN_EXPECTED_MOVE_BP = 12.0  # Signal must predict ≥ 12bp to clear fees + profit

# ==================== Data Fetching ====================

def fetch_json(endpoint: str, params: dict = None, timeout: int = 5) -> dict:
    url = f"{BINANCE_REST}/{endpoint}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"_error": str(e)}


def get_order_book(symbol: str = "BTCUSDT", limit: int = 20) -> dict:
    """Get order book depth."""
    return fetch_json("depth", {"symbol": symbol, "limit": str(limit)})


def get_recent_trades(symbol: str = "BTCUSDT", limit: int = 500) -> list:
    """Get recent trades."""
    return fetch_json("trades", {"symbol": symbol, "limit": str(limit)})


def get_klines(symbol: str = "BTCUSDT", interval: str = "1m", limit: int = 100) -> list:
    """Get klines/candles."""
    return fetch_json("klines", {"symbol": symbol, "interval": interval, "limit": str(limit)})


def get_24hr_ticker(symbol: str = "BTCUSDT") -> dict:
    """Get 24hr ticker stats."""
    return fetch_json("ticker/24hr", {"symbol": symbol})


# ==================== Metric Computation ====================

def compute_spread_expansion(book: dict) -> float:
    """Compute bid-ask spread as percentage. Higher = more adverse selection."""
    if "_error" in book:
        return 0.0
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    if not bids or not asks:
        return 0.0
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid = (best_bid + best_ask) / 2
    if mid == 0:
        return 0.0
    spread_bp = (best_ask - best_bid) / mid * 10000  # in basis points
    # Normalized: typical BTC spread ~0.5-2bp. >5bp = widening
    return min(spread_bp / 5.0, 1.0)  # capped at 1.0


def compute_trade_speed(trades: list) -> float:
    """How fast are trades executing? Faster = more information flow."""
    if "_error" in trades or len(trades) < 10:
        return 0.0
    if not isinstance(trades, list):
        return 0.0
    # Count trades in the batch. 500 trades in < 1 min = fast
    # Estimate: if 500 trades returned, they cover ~30-60 sec typically
    # Normalize: 500 trades → 1.0, proportional below
    n = len(trades)
    return min(n / 500.0, 1.0)


def compute_order_flow_persistence(trades: list, window: int = 8) -> float:
    """How many consecutive same-direction fills? Higher = sustained pressure."""
    if "_error" in trades or len(trades) < window:
        return 0.0
    if not isinstance(trades, list):
        return 0.0
    
    # Determine direction of each trade: buyer-initiated if price >= best_bid approximation
    # Simplified: compare each trade price to previous
    directions = []
    for i in range(1, len(trades)):
        try:
            curr_price = float(trades[i]["price"])
            prev_price = float(trades[i-1]["price"])
            if curr_price > prev_price:
                directions.append(1)   # buy pressure
            elif curr_price < prev_price:
                directions.append(-1)  # sell pressure
            else:
                directions.append(0)
        except (KeyError, ValueError, IndexError):
            continue
    
    if len(directions) < window:
        return 0.0
    
    # Max consecutive same-direction in recent window
    max_consecutive = 0
    current_streak = 0
    current_dir = 0
    for d in directions[-window:]:
        if d != 0 and d == current_dir:
            current_streak += 1
        elif d != 0:
            current_dir = d
            current_streak = 1
        max_consecutive = max(max_consecutive, current_streak)
    
    return min(max_consecutive / window, 1.0)


def compute_flow_shock(trades: list) -> float:
    """Sudden volume spike + directional imbalance. Simplified VPIN proxy."""
    if "_error" in trades or len(trades) < 50:
        return 0.0
    if not isinstance(trades, list):
        return 0.0
    
    # Split into 2 halves, compare buy/sell ratio
    mid = len(trades) // 2
    first_half = trades[:mid]
    second_half = trades[mid:]
    
    def buy_ratio(tr):
        buys = sum(1 for t in tr if not t.get("isBuyerMaker", False))
        total = len(tr)
        return buys / total if total > 0 else 0.5
    
    r1 = buy_ratio(first_half)
    r2 = buy_ratio(second_half)
    
    # Shock = sudden change in buy/sell ratio
    shock = abs(r2 - r1)
    # Also factor in volume
    vol_change = len(second_half) / max(len(first_half), 1)
    vol_shock = abs(vol_change - 1.0)
    
    combined = (shock * 0.7 + vol_shock * 0.3)
    return min(combined * 5.0, 1.0)  # amplified for visibility


def compute_volatility_regime(klines: list) -> dict:
    """Compute current volatility. Lower vol → stronger mean reversion."""
    if "_error" in klines or len(klines) < 20:
        return {"vol_pct": 0.0, "regime": "unknown", "trend_strength": 0.0}
    if not isinstance(klines, list):
        return {"vol_pct": 0.0, "regime": "unknown", "trend_strength": 0.0}
    
    closes = []
    highs = []
    lows = []
    for k in klines[-60:]:  # last 60 minutes
        try:
            closes.append(float(k[4]))
            highs.append(float(k[2]))
            lows.append(float(k[3]))
        except (IndexError, ValueError):
            continue
    
    if len(closes) < 10:
        return {"vol_pct": 0.0, "regime": "unknown", "trend_strength": 0.0}
    
    # Volatility: ATR / price
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    avg_tr = sum(trs[-20:]) / min(len(trs), 20)
    current_price = closes[-1]
    vol_pct = (avg_tr / current_price) * 10000  # in bp
    
    # Trend strength: linear regression slope
    n = len(closes)
    x_mean = (n - 1) / 2
    y_mean = sum(closes) / n
    numerator = sum((i - x_mean) * (closes[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    slope = (numerator / denominator) if denominator > 0 else 0
    trend_strength = abs(slope / current_price * 10000)  # bp per bar
    
    # Regime classification
    if trend_strength > vol_pct * 0.5:
        regime = "trending"
    elif vol_pct < 3:  # <3bp ATR = very quiet
        regime = "quiet"
    else:
        regime = "ranging"
    
    return {"vol_pct": vol_pct, "regime": regime, "trend_strength": trend_strength}


def compute_book_trade_divergence(book: dict, trades: list) -> float:
    """Order book direction vs trade direction mismatch."""
    if "_error" in book or "_error" in trades:
        return 0.0
    if not isinstance(trades, list) or len(trades) < 20:
        return 0.0
    
    # Book direction: bid/ask imbalance at top levels
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    if not bids or not asks:
        return 0.0
    
    bid_vol = sum(float(b[1]) for b in bids[:5])
    ask_vol = sum(float(a[1]) for a in asks[:5])
    book_imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
    # Positive = more bids (bullish book), Negative = more asks (bearish book)
    
    # Trade direction: buy ratio in recent trades
    buys = sum(1 for t in trades[-50:] if not t.get("isBuyerMaker", False))
    total = len(trades[-50:])
    trade_buy_ratio = buys / total if total > 0 else 0.5
    trade_imbalance = (trade_buy_ratio - 0.5) * 2  # -1 to +1
    
    # Divergence: book says one thing, trades say another
    divergence = abs(book_imbalance - trade_imbalance)
    return min(divergence, 1.0)


# ==================== Domain Score ====================

def compute_domain_score(symbol: str = "BTCUSDT") -> dict:
    """Compute simplified Domain Score from live Binance data."""
    now = datetime.now(HKT)
    
    # Fetch all data
    book = get_order_book(symbol)
    trades = get_recent_trades(symbol)
    klines = get_klines(symbol)
    
    # Check for API errors
    if any("_error" in x for x in [book, trades, klines]):
        return {"error": "API fetch failed", "score": -1}
    
    # Compute 6 domain elements
    spread = compute_spread_expansion(book)
    speed = compute_trade_speed(trades)
    flow_persist = compute_order_flow_persistence(trades)
    flow_shock = compute_flow_shock(trades)
    vol_info = compute_volatility_regime(klines)
    divergence = compute_book_trade_divergence(book, trades)
    
    # Volatility score: LOW vol = HIGH score (mean reversion works better in quiet markets)
    vol_score = max(0.0, 1.0 - vol_info.get("vol_pct", 0) / 10.0)  # 10bp ATR → score 0
    
    domain_scores = {
        "spread_expansion": round(spread, 3),
        "trade_speed": round(speed, 3),
        "flow_persistence": round(flow_persist, 3),
        "flow_shock": round(flow_shock, 3),
        "book_trade_divergence": round(divergence, 3),
        "low_volatility": round(vol_score, 3),
    }
    
    # Total score (0-6)
    total = sum(1 for v in domain_scores.values() if v >= 0.4)
    
    # Direction: mean reversion bias
    # If flow is mostly buys → expect mean reversion DOWN (short)
    # If flow is mostly sells → expect mean reversion UP (long)
    trade_buys = sum(1 for t in (trades if isinstance(trades, list) else [])[-50:] 
                     if not t.get("isBuyerMaker", False))
    trade_total = len(trades[-50:]) if isinstance(trades, list) and len(trades) > 0 else 1
    buy_ratio = trade_buys / trade_total if trade_total > 0 else 0.5
    
    # Mean reversion: go opposite of recent flow
    if buy_ratio > 0.55:
        direction = "SHORT"  # Too many buys → expect revert down
    elif buy_ratio < 0.45:
        direction = "LONG"   # Too many sells → expect revert up
    else:
        direction = "NEUTRAL"
    
    # Fee-aware check
    # Expected move ≈ spread score * volatility → in bp
    # Higher domain score + higher vol = bigger expected move
    expected_move_bp = total * vol_info.get("vol_pct", 2) * 0.5
    clears_fees = expected_move_bp >= FEE_HURDLE_BP
    
    result = {
        "timestamp": now.isoformat(),
        "domain_score": total,
        "scores": domain_scores,
        "direction": direction,
        "buy_ratio": round(buy_ratio, 3),
        "regime": vol_info.get("regime", "unknown"),
        "vol_bp": round(vol_info.get("vol_pct", 0), 1),
        "trend_bp_per_bar": round(vol_info.get("trend_strength", 0), 2),
        "expected_move_bp": round(expected_move_bp, 1),
        "clears_fees": clears_fees,
        "actionable": total >= 3 and direction != "NEUTRAL" and clears_fees,
    }
    
    # Fetch current price
    ticker = get_24hr_ticker(symbol)
    if "_error" not in ticker:
        result["price"] = float(ticker.get("lastPrice", 0))
        result["price_change_24h_pct"] = float(ticker.get("priceChangePercent", 0))
    
    return result


# ==================== Output ====================

def format_tg_message(result: dict) -> str:
    """Format Domain Score result for Telegram."""
    if "error" in result:
        return f"❌ BTC Micro Scan Error: {result['error']}"
    
    ds = result["domain_score"]
    
    # Emoji by score
    if ds >= 5:
        emoji = "🔥🔥🔥"
    elif ds >= 4:
        emoji = "🔥🔥"
    elif ds >= 3:
        emoji = "🔥"
    else:
        emoji = "📊"
    
    actionable = "⚡ ACTIONABLE" if result["actionable"] else "💤 Monitoring"
    price = result.get("price", 0)
    
    lines = [
        f"{emoji} **BTC Domain Score: {ds}/6** — {actionable}",
        f"💰 ${price:,.0f} | Regime: {result['regime']} | Vol: {result['vol_bp']}bp",
        f"",
        f"📈 Components:",
        f"  Spread: {result['scores']['spread_expansion']:.2f} | Speed: {result['scores']['trade_speed']:.2f}",
        f"  Flow: {result['scores']['flow_persistence']:.2f} | Shock: {result['scores']['flow_shock']:.2f}",
        f"  BookDiv: {result['scores']['book_trade_divergence']:.2f} | LowVol: {result['scores']['low_volatility']:.2f}",
    ]
    
    if result["direction"] != "NEUTRAL":
        dir_emoji = "🔴" if result["direction"] == "SHORT" else "🟢"
        lines.append(f"")
        lines.append(f"{dir_emoji} **Signal: {result['direction']}** (buy ratio: {result['buy_ratio']:.2f})")
        lines.append(f"  Expected move: {result['expected_move_bp']}bp | Fee hurdle: {FEE_HURDLE_BP}bp")
        
        if result["clears_fees"]:
            lines.append(f"  ✅ Clears fees + buffer")
        else:
            lines.append(f"  ❌ Below fee hurdle — no trade")
    
    if result["actionable"]:
        lines.append(f"")
        lines.append(f"🎯 **TRADE SETUP DETECTED** — check chart")
    
    return "\n".join(lines)


def main():
    print(f"🔬 Multi-Coin Micro-Domain Scanner — {datetime.now(HKT).strftime('%H:%M HKT')}")
    print(f"   Fee hurdle: {FEE_HURDLE_BP}bp | Min move: {MIN_EXPECTED_MOVE_BP}bp")
    print("=" * 60)
    
    for symbol in SYMBOLS:
        coin = symbol.replace("USDT", "")
        cache_file = CACHE_FILES[symbol]
        print(f"\n{'─'*40}")
        
        result = compute_domain_score(symbol)
        
        if "error" in result:
            print(f"  {coin}: ❌ {result['error']}")
            continue
        
        # Save state
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(result, f, indent=2, default=str)
        
        # Print
        print(f"  💰 {coin} ${result.get('price', 0):,.0f} ({result.get('price_change_24h_pct', 0):+.2f}% 24h)")
        print(f"  🎯 Domain Score: {result['domain_score']}/6 | Direction: {result['direction']}")
        print(f"  📊 Regime: {result['regime']} | Vol: {result['vol_bp']}bp | Move: {result['expected_move_bp']}bp")
        print(f"  ⚡ Actionable: {result['actionable']}")
        
        # Components
        for k, v in result['scores'].items():
            bar = "█" * int(v * 10) + "░" * (10 - int(v * 10))
            mark = "✓" if v >= 0.4 else " "
            print(f"    [{mark}] {k:25s} [{bar}] {v:.3f}")
        
        print(f"  📁 State: {os.path.basename(cache_file)}")
        
        # TG alert only if actionable
        if result["actionable"]:
            tg_msg = format_tg_message(result)
            print(f"\n{tg_msg}")
    
    print(f"\n{'='*60}")
    print(f"✅ Scan complete: {len(SYMBOLS)} coins")


if __name__ == "__main__":
    main()
