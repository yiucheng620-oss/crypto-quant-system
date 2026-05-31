#!/usr/bin/env python3
"""
纏論 System — Signal Tracker (一步到位專業版)
=============================================
獨立於 trading engine 嘅信號追蹤系統。
功能：
  1. 讀取最新 scan 結果 (包含 6 大買賣點型態)
  2. 對比之前信號（new / active / expired / completed）
  3. 記錄信號生命週期
  4. 計算各信號類型的歷史準確率
"""
import json, os, sys
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
SYS_DIR = os.path.expanduser("~/workspace/chanlun_system")
SIGNAL_FILE = os.path.join(SYS_DIR, "data/signals", "latest.json")
TRACKER_FILE = os.path.join(SYS_DIR, "data/signals", "tracker.json")
HISTORY_DIR = os.path.join(SYS_DIR, "data/history")

os.makedirs(HISTORY_DIR, exist_ok=True)

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)

def track():
    """Main tracking logic"""
    now = datetime.now(HKT)
    
    # Load latest scan
    latest = load_json(SIGNAL_FILE)
    if not latest or not latest.get("signals"):
        return {"status": "no_signals", "time": now.isoformat()}
    
    # Load tracker state
    tracker = load_json(TRACKER_FILE, {"active_signals": [], "history": [], "stats": {}})
    
    current_signals = latest["signals"]
    active = tracker.get("active_signals", [])
    
    # Build unique keys for current signals (include signal type)
    current_keys = set()
    for s in current_signals:
        key = f"{s['symbol']}_{s['tf']}_{s['type']}_{s['entry_price']}_{s['idx']}"
        current_keys.add(key)
    
    # Process previously active signals
    still_active = []
    expired = []
    
    for old in active:
        key = old.get("key", "")
        
        # Check if this signal is still in current scan
        if key in current_keys:
            # Update current price
            for s in current_signals:
                sk = f"{s['symbol']}_{s['tf']}_{s['type']}_{s['entry_price']}_{s['idx']}"
                if sk == key:
                    old["current_price"] = s.get("current_price", old.get("current_price"))
                    # Calculate percentage return based on signal direction
                    entry = old["entry_price"]
                    curr = old["current_price"]
                    direction = "SHORT" if "sell" in old.get("type", "buy") else "LONG"
                    if direction == "LONG":
                        old["pct_from_entry"] = round((curr / entry - 1) * 100, 2)
                    else:
                        old["pct_from_entry"] = round((entry / curr - 1) * 100, 2)
                    old["last_seen"] = now.isoformat()
                    break
            still_active.append(old)
        else:
            # Signal no longer active — check if it was profitable
            old["status"] = "expired"
            old["expired_at"] = now.isoformat()
            expired.append(old)
    
    # Add new signals
    for s in current_signals:
        key = f"{s['symbol']}_{s['tf']}_{s['type']}_{s['entry_price']}_{s['idx']}"
        if key not in {a.get("key","") for a in still_active}:
            entry_price = s["entry_price"]
            curr_price = s.get("current_price", entry_price)
            direction = "SHORT" if "sell" in s["type"] else "LONG"
            
            if direction == "LONG":
                pct_return = round((curr_price / entry_price - 1) * 100, 2)
            else:
                pct_return = round((entry_price / curr_price - 1) * 100, 2)
                
            new_entry = {
                "key": key,
                "symbol": s["symbol"],
                "tf": s["tf"],
                "type": s["type"],
                "direction": direction,
                "entry_price": entry_price,
                "entry_idx": s["idx"],
                "pivot_lower": s["pivot_lower"],
                "pivot_upper": s["pivot_upper"],
                "lowest_price": s.get("lowest_price"),
                "highest_price": s.get("highest_price"),
                "atr": s.get("atr"),
                "current_price": curr_price,
                "pct_from_entry": pct_return,
                "first_seen": now.isoformat(),
                "last_seen": now.isoformat(),
                "status": "active",
                "bars_ago": s.get("bars_ago", 0),
            }
            still_active.append(new_entry)
    
    # Move expired to history
    for ex in expired:
        tracker["history"].append(ex)
    
    # Keep only last 500 history entries
    if len(tracker["history"]) > 500:
        tracker["history"] = tracker["history"][-500:]
    
    # Update stats by type
    all_completed = [h for h in tracker["history"] if h.get("status") == "expired"]
    if all_completed:
        wins = sum(1 for h in all_completed if h.get("pct_from_entry", 0) > 0)
        total = len(all_completed)
        
        # Calculate stats per type
        by_type = {}
        for h in all_completed:
            t = h.get("type", "unknown")
            if t not in by_type:
                by_type[t] = {"total": 0, "wins": 0, "returns": []}
            by_type[t]["total"] += 1
            if h.get("pct_from_entry", 0) > 0:
                by_type[t]["wins"] += 1
            by_type[t]["returns"].append(h.get("pct_from_entry", 0))
            
        type_stats = {}
        for t, stat in by_type.items():
            type_stats[t] = {
                "total": stat["total"],
                "wins": stat["wins"],
                "win_rate": round(stat["wins"] / stat["total"] * 100, 1),
                "avg_return": round(sum(stat["returns"]) / stat["total"], 2)
            }
            
        tracker["stats"] = {
            "total_closed": total,
            "wins": wins,
            "win_rate": round(wins / total * 100, 1),
            "avg_return": round(sum(h.get("pct_from_entry", 0) for h in all_completed) / total, 2),
            "by_type": type_stats,
            "last_updated": now.isoformat(),
        }
    
    tracker["active_signals"] = still_active
    tracker["last_tracked"] = now.isoformat()
    
    save_json(TRACKER_FILE, tracker)
    
    # Daily snapshot to history
    date_str = now.strftime("%Y-%m-%d")
    daily_file = os.path.join(HISTORY_DIR, f"tracker_{date_str}.json")
    if not os.path.exists(daily_file):
        save_json(daily_file, tracker)
    
    # Generate summary
    summary = {
        "time": now.isoformat(),
        "active_count": len(still_active),
        "new_count": len(current_signals) - len([a for a in active if any(
            a.get("key","") == f"{s['symbol']}_{s['tf']}_{s['type']}_{s['entry_price']}_{s['idx']}"
            for s in current_signals
        )]),
        "expired_count": len(expired),
        "stats": tracker.get("stats", {}),
        "active": [
            f"{s['symbol']} {s['tf']} ({s['type'].upper()}): ${s['entry_price']:.1f} → ${s.get('current_price',0):.1f} ({s.get('pct_from_entry',0):+.1f}%)"
            for s in still_active
        ],
    }
    
    return summary

if __name__ == "__main__":
    result = track()
    
    if "--quiet" not in sys.argv:
        print("纏論 System Tracker")
        print("=" * 50)
        print(f"Active: {result['active_count']} | New: {result.get('new_count',0)} | Expired: {result.get('expired_count',0)}")
        
        stats = result.get("stats", {})
        if stats:
            print(f"History: {stats.get('total_closed',0)} closed, "
                  f"WR={stats.get('win_rate',0)}%, "
                  f"avg={stats.get('avg_return',0):+.2f}%")
            if "by_type" in stats:
                print("\nBreakdown by type:")
                for t, s in stats["by_type"].items():
                    print(f"  {t.upper()}: {s['wins']}/{s['total']} ({s['win_rate']}%) avg={s['avg_return']:+.2f}%")
        
        if result.get("active"):
            print("\nActive signals:")
            for a in result["active"]:
                print(f"  {a}")
    else:
        # Quiet mode: only output if there are changes
        if result.get("new_count", 0) > 0 or result.get("expired_count", 0) > 0:
            print(f"🔔 纏論: {result['new_count']} new, {result['expired_count']} expired")
            for a in result.get("active", []):
                print(f"  {a}")
