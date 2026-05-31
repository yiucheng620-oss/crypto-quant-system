#!/usr/bin/env python3
"""Trade Setup Alert — silent unless valid confluence setup exists."""
import os, sys, json
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WS = os.path.expanduser("~/workspace")
MKT = os.path.expanduser("~/market_data")

def load_json(p):
    try:
        with open(p) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # fallback: return empty dict if JSON file is missing or corrupt
        return {}

def get_sr(coin, days=5):
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    path = os.path.join(MKT, "daily", today, f"{coin}_USD.csv")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        lines = f.readlines()
    rows = [l.strip().split(",") for l in lines[-days:] if l.strip()]
    if not rows or len(rows[0]) < 5:
        return {}
    try:
        highs = [float(r[2]) for r in rows]
        lows = [float(r[3]) for r in rows]
        close = float(rows[-1][4])
    except (ValueError, IndexError):
        return {}
    return {"support": min(lows), "resistance": max(highs), "close": close}

def main():
    ob = load_json(os.path.join(WS, "orderbook_data", "orderbook_latest.json"))
    opt = load_json(os.path.join(WS, "options_data", "options_latest.json"))

    alerts = []
    # Track directions for correlation check (FIX 3)
    directions = {}  # coin -> "LONG" or "SHORT"

    for coin in ["BTC", "ETH", "SOL"]:
        cdata = ob.get("coins", {}).get(coin, {})
        sr = get_sr(coin)
        if not cdata or not sr:
            continue

        price = cdata.get("mid", 0)
        sup, res = sr.get("support", 0), sr.get("resistance", 0)
        if not price or not sup or not res:
            continue

        # === FIX 2: Entry Precision — narrowed from ±2% to ±1% ===
        near_sup = sup and price <= sup * 1.01
        near_res = res and price >= res * 0.99
        at_level = "support" if near_sup else ("resistance" if near_res else None)

        # === FIX 1: ETF flow contradiction filter ===
        etf_contradiction = False
        if coin in ["BTC", "ETH"]:
            ef = load_json(os.path.join(WS, "etf_data", "etf_flow_latest.json"))
            ek = "btc_etf" if coin == "BTC" else "eth_etf"
            px = ef.get(ek, {}).get("estimated_net_flow_proxy", {})
            ex = px.get("estimated_excess_volume")
            if ex is not None and ex < 0 and at_level == "support":
                # ETF outflow while signal is LONG → contradiction
                etf_contradiction = True
            elif ex is not None and ex > 0 and at_level == "resistance":
                # ETF inflow while signal is SHORT → contradiction
                etf_contradiction = True

        # 2) Order flow imbalance (>3:1)
        bid_v = cdata.get("bid_volume_2pct", 0) or 1
        ask_v = cdata.get("ask_volume_2pct", 0) or 1
        bid_ratio = bid_v / ask_v
        ask_ratio = ask_v / bid_v

        # 3) Options support
        od = opt.get("data", {}).get(coin, {})
        mp = od.get("max_pain", 0)
        pcr = od.get("put_call_ratio", 0.5)

        bull = (at_level == "support") + (bid_ratio > 3) + (mp > price and pcr < 0.7)
        bear = (at_level == "resistance") + (ask_ratio > 3) + (0 < mp < price and pcr > 0.7)

        if bull >= 2:
            stop = sup * 0.985
            target = res
            rr = round((target - price) / (price - stop), 1) if (price - stop) > 0 else 0
            if bull == 3:
                if rr > 2.0:
                    conf = "5/5 🔥🔥🔥"
                else:
                    conf = "4/5 🔥🔥"
            else:
                conf = "3/5 🔥" if rr > 1.5 else "2/5 ⚡"
            # FIX 1: ETF contradiction caps at 2/5
            if etf_contradiction:
                conf_num = 2
                conf_str = "2/5 ⚡"
                extra = "🔴 ETF流向矛盾 — 信號降級"
                alerts.append(f"🚨 交易設置警報\n{coin} / 📈 做多\n入場 ${price:,.0f} 止蝕 ${stop:,.0f}\n目標 ${target:,.0f} / R:R 1:{rr} / 信心 {conf_str}\n{extra}")
            else:
                alerts.append(f"🚨 交易設置警報\n{coin} / 📈 做多\n入場 ${price:,.0f} 止蝕 ${stop:,.0f}\n目標 ${target:,.0f} / R:R 1:{rr} / 信心 {conf}")
            directions[coin] = "LONG"
        elif bear >= 2:
            stop = res * 1.015
            target = sup
            rr = round((stop - price) / (price - target), 1) if (price - target) > 0 else 0
            if bear == 3:
                if rr > 2.0:
                    conf = "5/5 🔥🔥🔥"
                else:
                    conf = "4/5 🔥🔥"
            else:
                conf = "3/5 🔥" if rr > 1.5 else "2/5 ⚡"
            # FIX 1: ETF contradiction caps at 2/5
            if etf_contradiction:
                conf_str = "2/5 ⚡"
                extra = "🔴 ETF流向矛盾 — 信號降級"
                alerts.append(f"🚨 交易設置警報\n{coin} / 📉 做空\n入場 ${price:,.0f} 止蝕 ${stop:,.0f}\n目標 ${target:,.0f} / R:R 1:{rr} / 信心 {conf_str}\n{extra}")
            else:
                alerts.append(f"🚨 交易設置警報\n{coin} / 📉 做空\n入場 ${price:,.0f} 止蝕 ${stop:,.0f}\n目標 ${target:,.0f} / R:R 1:{rr} / 信心 {conf}")
            directions[coin] = "SHORT"

    # === FIX 3: Correlation Check — all 3 coins same direction ===
    crypto_dirs = {k: v for k, v in directions.items() if k in ["BTC", "ETH", "SOL"]}
    if len(crypto_dirs) == 3:
        dirs_set = set(crypto_dirs.values())
        if len(dirs_set) == 1:
            # All same direction — add correlation warning
            common_dir = list(dirs_set)[0]
            # Find which has best R:R
            best_rr = 0
            best_coin = ""
            for a in alerts:
                for c in ["BTC", "ETH", "SOL"]:
                    if a.startswith(f"🚨 交易設置警報\n{c}") and "R:R 1:" in a:
                        idx = a.index("R:R 1:")
                        rr_str = a[idx+6:].split()[0]
                        try:
                            rr_val = float(rr_str)
                            if rr_val > best_rr:
                                best_rr = rr_val
                                best_coin = c
                        except (ValueError, TypeError):
                            # fallback: skip unparseable R:R value
                            pass
            # Add warning to each alert and dock confidence
            new_alerts = []
            for a in alerts:
                for c in ["BTC", "ETH", "SOL"]:
                    if a.startswith(f"🚨 交易設置警報\n{c}"):
                        # Dock 1 confidence level
                        for level, downgrade in [
                            ("5/5 🔥🔥🔥", "4/5 🔥🔥"),
                            ("4/5 🔥🔥", "3/5 🔥"),
                            ("3/5 🔥", "2/5 ⚡"),
                            ("2/5 ⚡", "1/5 ⚠️"),
                        ]:
                            if level in a:
                                a = a.replace(level, downgrade)
                                break
                        a += f"\n⚠️ BTC/ETH/SOL 全部{common_dir} — 高相關性風險，建議只揀 {best_coin}"
                        break
                new_alerts.append(a)
            alerts = new_alerts

    if not alerts:
        sys.exit(0)
    print("\n\n".join(alerts))

if __name__ == "__main__":
    main()
