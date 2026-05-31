#!/usr/bin/env python3
"""
纏論 System — Paper Trade Tracker (一步到位專業版)
===================================================
⚠️ 此系統不會、不能、不應接觸 Capital.com API。
只記錄 hypothetical paper trades 作研究用途。
支援多空雙向交易、纏論結構化動態風控與真實 SL/TP。

風控規則：
  - LONG (1buy/2buy): SL = 結構低點 (lowest_price) - 0.5 * ATR. TP = 1:2 盈虧比。
  - LONG (3buy): SL = 中樞上沿 - 0.5 * ATR. TP = 1:2 盈虧比。
  - SHORT (1sell/2sell): SL = 結構高點 (highest_price) + 0.5 * ATR. TP = 1:2 盈虧比。
  - SHORT (3sell): SL = 中樞下沿 + 0.5 * ATR. TP = 1:2 盈虧比。
  - 資金管理：每筆交易嚴格控制在帳戶資產的 2% 風險偏好。
"""
import json, os, sys
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
SYS_DIR = os.path.expanduser("~/workspace/chanlun_system")
PAPER_FILE = os.path.join(SYS_DIR, "data/paper", "trades.json")
TRACKER_FILE = os.path.join(SYS_DIR, "data/signals", "tracker.json")

# ⚠️ 安全檢查：確保不會跟實彈/Demo 系統的 paper_positions.json 衝突
MAIN_PAPER_FILE = os.path.expanduser("~/workspace/data/paper_trades/paper_positions.json")
assert PAPER_FILE != MAIN_PAPER_FILE, f"CRITICAL: Paper file collision! {PAPER_FILE}"

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default if default is not None else []

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)

PAPER_CONFIG = {
    "capital": 10000,            # 獨立 10,000 美金賬戶
    "risk_per_trade_pct": 0.02,  # 每筆交易最大損失 2% ($200)
    "min_sl_pct": 0.015,         # 最小止損 1.5% 防止過窄插針
    "max_sl_pct": 0.05,          # 最大止損 5.0% 防止止損過寬
}

# ═══════════════════════════════════════════════════════════════
# Trailing Stop 設定 (照搬 System 1 engine_base 邏輯)
# ═══════════════════════════════════════════════════════════════
TRAILING_ACTIVATION_PCT = 0.02   # 浮贏 2% 啟動 trailing
TRAILING_DISTANCE_PCT = 0.015    # Trail 1.5% behind price

def get_current_price(symbol):
    """從最新信號文件中讀取最新價格"""
    signal_file = os.path.join(SYS_DIR, "data/signals", "latest.json")
    try:
        with open(signal_file) as f:
            data = json.load(f)
        for s in data.get("signals", []):
            if s.get("symbol") == symbol:
                return s.get("current_price")
    except:
        pass
    return None

def calculate_structural_stops(signal):
    """根據信號類型和 ATR，計算出最科學的纏論結構化止損與止盈"""
    entry = signal["entry_price"]
    atr = signal.get("atr", entry * 0.02)  # fallback 2%
    stype = signal["type"]
    direction = "SHORT" if "sell" in stype else "LONG"
    
    # 預設 3% 止損作為安全墊
    sl_dist = entry * 0.03
    
    if direction == "LONG":
        if stype in ["1buy", "2buy"] and signal.get("lowest_price") is not None:
            # 結構低點下方 0.5 * ATR
            sl_price = signal["lowest_price"] - (0.5 * atr)
            sl_dist = entry - sl_price
        elif stype == "3buy":
            # 回踏不破中樞上沿，中樞上沿下方 0.5 * ATR
            sl_price = signal["pivot_upper"] - (0.5 * atr)
            sl_dist = entry - sl_price
        else:
            sl_price = entry - sl_dist
            
        # 限制止損百分比在 [1.5%, 5%] 區間內
        min_sl = entry * PAPER_CONFIG["min_sl_pct"]
        max_sl = entry * PAPER_CONFIG["max_sl_pct"]
        if sl_dist < min_sl:
            sl_price = entry - min_sl
            sl_dist = min_sl
        elif sl_dist > max_sl:
            sl_price = entry - max_sl
            sl_dist = max_sl
            
        # 1:2 盈虧比設定 take profit
        tp_price = entry + (2.0 * sl_dist)
        
    else:  # SHORT
        if stype in ["1sell", "2sell"] and signal.get("highest_price") is not None:
            # 結構高點上方 0.5 * ATR
            sl_price = signal["highest_price"] + (0.5 * atr)
            sl_dist = sl_price - entry
        elif stype == "3sell":
            # 反彈不破中樞下沿，中樞下沿上方 0.5 * ATR
            sl_price = signal["pivot_lower"] + (0.5 * atr)
            sl_dist = sl_price - entry
        else:
            sl_price = entry + sl_dist
            
        min_sl = entry * PAPER_CONFIG["min_sl_pct"]
        max_sl = entry * PAPER_CONFIG["max_sl_pct"]
        if sl_dist < min_sl:
            sl_price = entry + min_sl
            sl_dist = min_sl
        elif sl_dist > max_sl:
            sl_price = entry + max_sl
            sl_dist = max_sl
            
        tp_price = entry - (2.0 * sl_dist)
        
    return round(sl_price, 2), round(tp_price, 2)

def open_paper_trade(signal):
    """
    基於活躍信號開立虛擬持倉。
    支援雙向，動態計算倉位大小。
    
    ⛔ Dedup: 同一個 signal (symbol+tf+type+entry) 只要曾經交易過
    （無論 open/closed），都不會重複開倉。
    """
    trades = load_json(PAPER_FILE, [])
    
    direction = "SHORT" if "sell" in signal["type"] else "LONG"
    
    # ⛔ DEDUP: 檢查同一個 signal key 是否已經被交易過
    signal_key = f"{signal['symbol']}_{signal['tf']}_{signal['type']}_{signal['entry_price']}"
    for t in trades:
        # Handle older trades that may not have 'type' field
        t_type = t.get('type') or (t.get('strategy', '').replace('chanlun_', '') if 'strategy' in t else '')
        t_key = f"{t.get('symbol')}_{t.get('tf')}_{t_type}_{t.get('entry_price')}"
        if t_key == signal_key:
            # Already traded this exact signal, skip
            return None
    
    # 檢查是否已有相同品種同方向的活躍持倉（額外保護）
    for t in trades:
        if t.get("status") == "open" and t.get("symbol") == signal["symbol"] and t.get("direction") == direction:
            return None
            
    # 計算止損與止盈
    sl, tp = calculate_structural_stops(signal)
    entry = signal["entry_price"]
    
    # 風險與倉位大小計算 (Risk-based sizing)
    capital = PAPER_CONFIG["capital"]
    risk_amount = capital * PAPER_CONFIG["risk_per_trade_pct"]  # 2% Risk
    
    sl_dist = abs(entry - sl)
    position_size = risk_amount / sl_dist if sl_dist > 0 else 0
    if position_size <= 0:
        return None
        
    # 檢查總可用資金是否超限
    used_capital = sum(
        t.get("position_size", 0) * t.get("entry_price", 0)
        for t in trades if t.get("status") == "open"
    )
    new_position_value = position_size * entry
    if used_capital + new_position_value > capital:
        # 降低槓桿/倉位以符合可用資金
        remaining_cap = capital - used_capital
        if remaining_cap > entry * 0.1: # 至少能買0.1手
            position_size = remaining_cap / entry
        else:
            return None  # 可用資金不足
            
    trade = {
        "trade_id": f"CL_{datetime.now(HKT).strftime('%Y%m%d_%H%M%S')}",
        "symbol": signal["symbol"],
        "tf": signal["tf"],
        "type": signal["type"],
        "direction": direction,
        "entry_price": round(entry, 2),
        "stop_loss": round(sl, 2),
        "take_profit": round(tp, 2),
        "highest_price": round(entry, 2),   # for trailing stop (LONG)
        "lowest_price": round(entry, 2),     # for trailing stop (SHORT)
        "trailing_active": False,
        "position_size": round(position_size, 4),
        "entry_time": datetime.now(HKT).isoformat(),
        "status": "open",
        "strategy": f"chanlun_{signal['type']}",
        "pivot_lower": signal.get("pivot_lower"),
        "pivot_upper": signal.get("pivot_upper"),
        "notes": f"纏論 {signal['type'].upper()} 信號, bars_ago={signal.get('bars_ago')}",
    }
    
    trades.append(trade)
    save_json(PAPER_FILE, trades)
    
    return trade

def update_trailing_stops(trades):
    """
    照搬 System 1 engine_base.update_trailing_stops() 邏輯。
    
    1. Break-Even Trailing: 浮贏達到 TP 50% → SL 移到 entry + 0.05% buffer
    2. Standard Trailing: 浮贏 ≥ TRAILING_ACTIVATION_PCT → SL 跟蹤最高/最低價
    """
    updated = False
    
    for trade in trades:
        if trade.get("status") != "open":
            continue
        
        symbol = trade["symbol"]
        current_price = get_current_price(symbol)
        if current_price is None:
            continue
        
        direction = trade.get("direction", "LONG")
        entry = trade["entry_price"]
        old_sl = trade["stop_loss"]
        tp = trade["take_profit"]
        
        # Init water marks if missing (legacy trades)
        if "highest_price" not in trade:
            trade["highest_price"] = entry
        if "lowest_price" not in trade:
            trade["lowest_price"] = entry
        
        # Update high/low water mark
        if direction == "LONG":
            if current_price > trade["highest_price"]:
                trade["highest_price"] = current_price
        else:
            if current_price < trade["lowest_price"]:
                trade["lowest_price"] = current_price
        
        new_sl = old_sl
        be_sl = None  # break-even candidate
        
        # --- 1. Break-Even Trailing (Free Ride) ---
        tp_dist = abs(tp - entry)
        if tp_dist > 0:
            if direction == "LONG":
                current_dist = current_price - entry
            else:
                current_dist = entry - current_price
            
            if current_dist > 0 and (current_dist / tp_dist) >= 0.5:
                # 已達到 TP 50% → SL 移到 entry + 0.05% buffer
                buffer = entry * 0.0005
                be_sl = entry + buffer if direction == "LONG" else entry - buffer
                trade["trailing_active"] = True
        
        # --- 2. Standard Trailing ---
        ts_sl = None  # trailing stop candidate
        if direction == "LONG":
            pnl_pct = (current_price - entry) / entry
            if pnl_pct >= TRAILING_ACTIVATION_PCT:
                trade["trailing_active"] = True
                ts_sl = trade["highest_price"] * (1 - TRAILING_DISTANCE_PCT)
        else:
            pnl_pct = (entry - current_price) / entry
            if pnl_pct >= TRAILING_ACTIVATION_PCT:
                trade["trailing_active"] = True
                ts_sl = trade["lowest_price"] * (1 + TRAILING_DISTANCE_PCT)
        
        # Pick the BEST stop (most favorable for the position)
        # LONG: higher SL = better | SHORT: lower SL = better
        candidates = [old_sl]
        if be_sl is not None:
            candidates.append(be_sl)
        if ts_sl is not None:
            candidates.append(ts_sl)
        
        if direction == "LONG":
            new_sl = max(candidates)
        else:
            new_sl = min(candidates)
        
        # Move stop only if favorable (with 0.01 tolerance for floating point)
        if direction == "LONG" and new_sl > old_sl + 0.01:
            trade["stop_loss"] = round(new_sl, 2)
            updated = True
            print(f"  📈 [Chanlun Trailing] {symbol} LONG SL ${old_sl:.2f} → ${new_sl:.2f}")
        elif direction == "SHORT" and new_sl < old_sl - 0.01:
            trade["stop_loss"] = round(new_sl, 2)
            updated = True
            print(f"  📉 [Chanlun Trailing] {symbol} SHORT SL ${old_sl:.2f} → ${new_sl:.2f}")
    
    return updated


def check_paper_trades():
    """
    檢查所有活躍倉位。
    根據最新價格判斷是否觸發 SL / TP，或信號失效超時平倉。
    """
    trades = load_json(PAPER_FILE, [])
    tracker = load_json(TRACKER_FILE, {})
    active_signals = tracker.get("active_signals", [])
    active_keys = {s.get("key", "") for s in active_signals}
    
    # ── 0. Trailing Stop Update (先 update SL，再 check 係咪 hit) ──
    ts_updated = update_trailing_stops(trades)
    
    updated = ts_updated
    
    for trade in trades:
        if trade.get("status") != "open":
            continue
            
        symbol = trade["symbol"]
        current_price = get_current_price(symbol)
        if current_price is None:
            continue
            
        direction = trade.get("direction", "LONG")
        entry = trade["entry_price"]
        sl = trade["stop_loss"]
        tp = trade["take_profit"]
        
        is_closed = False
        close_reason = ""
        pnl = 0.0
        pnl_amount = 0.0
        
        # 1. 檢查止損
        if direction == "LONG" and current_price <= sl:
            is_closed = True
            close_reason = "stop_loss"
            pnl = round((sl / entry - 1) * 100, 2)
            pnl_amount = round((sl - entry) * trade["position_size"], 2)
        elif direction == "SHORT" and current_price >= sl:
            is_closed = True
            close_reason = "stop_loss"
            pnl = round((entry / sl - 1) * 100, 2)
            pnl_amount = round((entry - sl) * trade["position_size"], 2)
            
        # 2. 檢查止盈
        elif direction == "LONG" and current_price >= tp:
            is_closed = True
            close_reason = "take_profit"
            pnl = round((tp / entry - 1) * 100, 2)
            pnl_amount = round((tp - entry) * trade["position_size"], 2)
        elif direction == "SHORT" and current_price <= tp:
            is_closed = True
            close_reason = "take_profit"
            pnl = round((entry / tp - 1) * 100, 2)
            pnl_amount = round((entry - tp) * trade["position_size"], 2)
            
        # 3. 檢查信號失效超時 (持倉 >24h 且不活躍)
        else:
            ttype = trade.get("type", "1buy")
            trade_key = f"{trade['symbol']}_{trade['tf']}_{ttype}_{trade['entry_price']}"
            still_active = any(trade_key in k for k in active_keys)
            
            try:
                entry_time = datetime.fromisoformat(trade["entry_time"])
                hours_since_entry = (datetime.now(HKT) - entry_time).total_seconds() / 3600
            except:
                hours_since_entry = 0
                
            if not still_active and hours_since_entry > 24:
                is_closed = True
                close_reason = "signal_expired"
                if direction == "LONG":
                    pnl = round((current_price / entry - 1) * 100, 2)
                    pnl_amount = round((current_price - entry) * trade["position_size"], 2)
                else:
                    pnl = round((entry / current_price - 1) * 100, 2)
                    pnl_amount = round((entry - current_price) * trade["position_size"], 2)
                    
        if is_closed:
            trade["status"] = "closed"
            trade["close_price"] = round(current_price, 2)
            trade["close_time"] = datetime.now(HKT).isoformat()
            trade["close_reason"] = close_reason
            trade["pnl"] = pnl
            trade["pnl_amount"] = pnl_amount
            updated = True
            
            # 4. 回填環境特徵庫
            try:
                features_file = os.path.join(SYS_DIR, "data/exports", "signal_features.json")
                if os.path.exists(features_file):
                    with open(features_file) as f:
                        feats = json.load(f)
                    ttype = trade.get("type", "1buy")
                    fkey = f"{trade['symbol']}_{trade['tf']}_{ttype}_{trade['entry_price']}"
                    # 尋找匹配的 key (模糊匹配前半部)
                    for k in feats.keys():
                        if k.startswith(fkey):
                            feats[k]["pnl_pips"] = pnl_amount
                            feats[k]["win"] = pnl > 0
                            break
                    with open(features_file, "w") as f:
                        json.dump(feats, f, indent=2, default=str)
            except Exception as e:
                print(f"  ⚠️ 回填特徵庫失敗: {e}")
                
    if updated:
        save_json(PAPER_FILE, trades)
        
    return trades

def get_paper_summary():
    """計算虛擬賬戶總體業績"""
    trades = load_json(PAPER_FILE, [])
    open_trades = [t for t in trades if t.get("status") == "open"]
    closed_trades = [t for t in trades if t.get("status") == "closed"]
    
    total_pnl = sum(t.get("pnl_amount", 0) for t in closed_trades)
    wins = sum(1 for t in closed_trades if t.get("pnl_amount", 0) > 0)
    
    return {
        "total_trades": len(trades),
        "open": len(open_trades),
        "closed": len(closed_trades),
        "wins": wins,
        "win_rate": round(wins / len(closed_trades) * 100, 1) if closed_trades else 0,
        "total_pnl_amount": round(total_pnl, 2),
        "avg_pnl_pct": round(
            sum(t.get("pnl", 0) for t in closed_trades) / len(closed_trades), 2
        ) if closed_trades else 0,
        "open_positions": [
            {
                "symbol": t["symbol"],
                "type": t.get("type", "1buy"),
                "direction": t["direction"],
                "entry": t["entry_price"],
                "current": get_current_price(t["symbol"]) or t["entry_price"],
                "sl": t["stop_loss"],
                "tp": t["take_profit"],
            }
            for t in open_trades
        ],
    }

if __name__ == "__main__":
    assert PAPER_FILE != MAIN_PAPER_FILE, "SANITY FAILED: paper file collision!"
    
    summary_before = get_paper_summary()
    
    # 1. 讀取最新活躍信號並嘗試開倉
    tracker = load_json(TRACKER_FILE, {})
    active_signals = tracker.get("active_signals", [])
    
    new_trades = []
    for sig in active_signals:
        trade = open_paper_trade(sig)
        if trade:
            new_trades.append(trade)
            
    # 2. 檢查/平倉活躍持倉
    check_paper_trades()
    
    summary = get_paper_summary()
    
    # 3. 輸出詳細報告
    if "--quiet" not in sys.argv:
        print("📝 纏論 Paper Trade Tracker (動態風控)")
        print("=" * 50)
        print(f"賬戶初始資金: ${PAPER_CONFIG['capital']:,} | 風險控制: {PAPER_CONFIG['risk_per_trade_pct']*100:.0f}%/每單")
        print(f"交易統計: {summary['total_trades']} 總單 | {summary['open']} 持倉 | {summary['closed']} 已平倉")
        
        if summary['closed'] > 0:
            print(f"業績表現: {summary['wins']}/{summary['closed']} 勝場 "
                  f"({summary['win_rate']}%), 總盈虧=${summary['total_pnl_amount']:+.2f}, "
                  f"平均單筆盈虧={summary['avg_pnl_pct']:+.2f}%")
                  
        if new_trades:
            print(f"\n🆕 偵測到新信號並開倉 ({len(new_trades)}):")
            for t in new_trades:
                print(f"  {t['symbol']} {t['tf']} ({t['type'].upper()}): {t['direction']} @ ${t['entry_price']:.1f} "
                      f"[SL=${t['stop_loss']:.1f}, TP=${t['take_profit']:.1f}]")
                      
        if summary['open_positions']:
            print(f"\n📊 當前持倉:")
            for p in summary['open_positions']:
                cp = p['current']
                if p['direction'] == "LONG":
                    pnl = (cp / p['entry'] - 1) * 100
                else:
                    pnl = (p['entry'] / cp - 1) * 100
                print(f"  {p['symbol']} ({p['type'].upper()}): {p['direction']} ${p['entry']:.1f} → ${cp:.1f} ({pnl:+.1f}%) "
                      f"[SL=${p['sl']:.1f}, TP=${p['tp']:.1f}]")
    else:
        # Quiet mode
        changes = []
        if new_trades:
            changes.append(f"{len(new_trades)} new trade(s)")
        if summary['open'] != summary_before.get('open', 0):
            changes.append(f"{summary['open']} open positions")
        if changes:
            print(f"📝 纏論 Paper: {', '.join(changes)}")
