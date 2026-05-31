#!/usr/bin/env python3
"""
Capital.com Demo Accounts — 全面審計腳本
"""
import sys
import os

# Add workspace to path
sys.path.insert(0, os.path.expanduser("~/workspace"))

# Patch: make sure module can be imported
os.chdir(os.path.expanduser("~/workspace"))

from capitalcom_paper import (
    ACCOUNTS, get_account_balance, check_positions, _headers,
    DEMO_URL, EPIC_MAP, TRACKER_FILE, HISTORY_FILE
)
import json
import requests
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")

def fetch_all_account_balances():
    """Fetch balances from all 4 accounts via API."""
    print("=" * 70)
    print("🏦 CAPITAL.COM DEMO — 帳戶餘額快照")
    print(f"   擷取時間: {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 70)
    
    results = {}
    total_deposit = 0
    total_balance = 0
    total_pnl = 0
    
    account_names = {
        "321895128687259934": "加密貨幣TEST (Crypto)",
        "321895132982227230": "美股TEST (Equities)",
        "321895137277194526": "指數TEST (Indices)",
        "321895124392292638": "黃金TEST (Gold)",
    }
    
    for key, acct_id in ACCOUNTS.items():
        name = account_names.get(acct_id, key)
        try:
            bal = get_account_balance(acct_id)
            if bal:
                results[key] = bal
                deposit = bal.get("deposit", 0)
                balance = bal.get("balance", 0)
                pnl = bal.get("pnl", 0)
                available = bal.get("available", 0)
                pnl_pct = (pnl / deposit * 100) if deposit > 0 else 0
                
                print(f"\n📂 {name} (ID: {acct_id})")
                print(f"   初始入金: ${deposit:,.2f}")
                print(f"   目前餘額: ${balance:,.2f}")
                print(f"   P&L:      ${pnl:+,.2f}  ({pnl_pct:+.2f}%)")
                print(f"   可用資金: ${available:,.2f}")
                
                total_deposit += deposit
                total_balance += balance
                total_pnl += pnl
            else:
                print(f"\n❌ {key}: 無法獲取帳戶資料")
                results[key] = {}
        except Exception as e:
            print(f"\n❌ {key}: 錯誤 — {e}")
            results[key] = {}
    
    print(f"\n{'=' * 70}")
    print(f"💰 合計匯總")
    print(f"   總入金:     ${total_deposit:,.2f}")
    print(f"   總餘額:     ${total_balance:,.2f}")
    print(f"   總 P&L:     ${total_pnl:+,.2f}")
    total_pnl_pct = (total_pnl / total_deposit * 100) if total_deposit > 0 else 0
    print(f"   總回報率:   {total_pnl_pct:+.2f}%")
    print(f"{'=' * 70}")
    
    return results, total_deposit, total_balance, total_pnl


def fetch_open_positions():
    """Fetch all open positions from all accounts."""
    print(f"\n{'=' * 70}")
    print("📊 CAPITAL.COM DEMO — 當前持倉")
    print(f"   擷取時間: {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 70)
    
    account_names = {
        "321895128687259934": "加密貨幣TEST (Crypto)",
        "321895132982227230": "美股TEST (Equities)",
        "321895137277194526": "指數TEST (Indices)",
        "321895124392292638": "黃金TEST (Gold)",
    }
    
    all_positions = {}
    total_open_pnl = 0
    
    for key, acct_id in ACCOUNTS.items():
        name = account_names.get(acct_id, key)
        try:
            positions = check_positions(acct_id)
            all_positions[key] = positions
            
            if not positions:
                print(f"\n📂 {name} — 📭 無持倉")
                continue
            
            print(f"\n📂 {name} ({len(positions)} 持倉):")
            acct_pnl = 0
            for p in positions:
                emoji = "🟢" if p["pnl_amt"] > 0 else "🔴" if p["pnl_amt"] < 0 else "⚪"
                print(f"   {emoji} {p['ticker']:<6s} {p['direction']:<5s} | 入場 ${p['entry']:.2f} → 現價 ${p['current']:.2f} | P&L ${p['pnl_amt']:+,.2f} ({p['pnl_pct']:+.2f}%) | 止損 ${p['stop']:.2f} | 目標 ${p['limit']:.2f}")
                acct_pnl += p["pnl_amt"]
            print(f"   💰 帳戶浮動 P&L: ${acct_pnl:+,.2f}")
            total_open_pnl += acct_pnl
        except Exception as e:
            print(f"\n❌ {key}: 錯誤 — {e}")
            all_positions[key] = []
    
    print(f"\n{'=' * 70}")
    print(f"💰 總浮動 P&L: ${total_open_pnl:+,.2f}")
    print("=" * 70)
    
    return all_positions


def fetch_transaction_history():
    """Try to get transaction history from API."""
    print(f"\n{'=' * 70}")
    print("📜 CAPITAL.COM DEMO — 交易歷史 (API)")
    print("=" * 70)
    
    for key, acct_id in ACCOUNTS.items():
        try:
            h = _headers(acct_id)
            # Try transactions endpoint
            for endpoint in ["/api/v1/history/transactions", "/api/v1/history/activity"]:
                url = f"{DEMO_URL}{endpoint}"
                r = requests.get(url, headers=h, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    transactions = data.get('transactions', data.get('activities', []))
                    print(f"\n📂 {key} ({acct_id}): {len(transactions)} 筆交易紀錄")
                    if transactions:
                        for t in transactions[:5]:  # Show first 5
                            print(f"   {json.dumps(t, ensure_ascii=False)[:200]}")
                    break
                else:
                    print(f"\n📂 {key}: {endpoint} → HTTP {r.status_code}")
        except Exception as e:
            print(f"\n📂 {key}: 錯誤 — {e}")


def reconstruct_trade_history():
    """Reconstruct full trade history from ALL JSON files."""
    print(f"\n{'=' * 70}")
    print("📈 完整交易歷史重建")
    print("=" * 70)
    
    all_trades = []
    
    # Source 1: paper_trades.json (unified paper trades)
    tracker_path = os.path.join(WORKSPACE, "scalp_lab", "engines", "paper_trades.json")
    if os.path.exists(tracker_path):
        with open(tracker_path) as f:
            tracker_trades = json.load(f)
        print(f"\n📁 paper_trades.json (Paper Trades): {len(tracker_trades)} 筆交易")
        for t in tracker_trades:
            all_trades.append({
                "source": "paper",
                "ticker": t.get("epic", "?"),  # engine format uses 'epic'
                "epic": t.get("epic", ""),
                "direction": t.get("direction", ""),
                "entry": t.get("entry", 0),
                "stop": t.get("stop", 0),
                "target": t.get("target", 0),
                "size": t.get("size", 0),
                "market": t.get("market", ""),
                "account_id": t.get("account", ""),
                "opened_at": t.get("timestamp", ""),
                "status": t.get("status", "open"),
                "pnl": t.get("pnl", 0),
                "deal_ref": t.get("deal_ref", ""),
                "strategy": t.get("strategy", "manual"),
            })
    
    # Source 2: Engine trades (per-asset + paper_trades.json)
    engine_files = {
        "btc": os.path.join(WORKSPACE, "scalp_lab", "engines", "btc_trades.json"),
        "eth": os.path.join(WORKSPACE, "scalp_lab", "engines", "eth_trades.json"),
        "gold": os.path.join(WORKSPACE, "scalp_lab", "engines", "gold_trades.json"),
        "indices": os.path.join(WORKSPACE, "scalp_lab", "engines", "indices_trades.json"),
        "equities": os.path.join(WORKSPACE, "scalp_lab", "engines", "equities_trades.json"),
    }
    
    for engine, path in engine_files.items():
        if os.path.exists(path):
            with open(path) as f:
                content = f.read().strip()
                if content and content != "[]":
                    engine_trades = json.loads(content)
                    print(f"📁 engines/{engine}_trades.json ({engine}引擎): {len(engine_trades)} 筆交易")
                    for t in engine_trades:
                        existing = [x for x in all_trades if x.get("deal_ref") == t.get("deal_ref")]
                        if not existing:
                            all_trades.append({
                                "source": f"engine_{engine}",
                                "ticker": t.get("market", t.get("epic", "?")),
                                "epic": t.get("epic", ""),
                                "direction": t.get("direction", ""),
                                "entry": t.get("entry", 0),
                                "stop": t.get("stop", 0),
                                "target": t.get("target", 0),
                                "size": t.get("size", 0),
                                "market": t.get("account", t.get("market", "")),
                                "opened_at": t.get("timestamp", ""),
                                "status": t.get("status", "open"),
                                "pnl": t.get("adjusted_pnl", t.get("pnl", 0)),
                                "raw_pnl": t.get("raw_pnl", 0),
                                "deal_ref": t.get("deal_ref", ""),
                                "deal_id": t.get("deal_id", ""),
                                "strategy": t.get("strategy", "unknown"),
                                "closed_at": t.get("closed_at", ""),
                                "exit_price": t.get("exit_price", 0),
                                "close_reason": t.get("close_reason", ""),
                                "slippage_cost": t.get("slippage_cost", 0),
                            })
                else:
                    print(f"📁 engines/{engine}_trades.json: 空檔案")
    
    print(f"\n{'=' * 70}")
    print(f"📊 合併去重後共 {len(all_trades)} 筆唯一交易")
    print("=" * 70)
    
    return all_trades


def analyze_trades(trades):
    """Comprehensive trade analysis."""
    print(f"\n{'=' * 70}")
    print("📊 交易績效分析")
    print("=" * 70)
    
    if not trades:
        print("❌ 沒有交易數據可分析")
        return
    
    closed_trades = [t for t in trades if t.get("status") == "closed"]
    open_trades = [t for t in trades if t.get("status") == "open"]
    
    print(f"\n📊 總覽:")
    print(f"   總交易數:   {len(trades)}")
    print(f"   已平倉:     {len(closed_trades)}")
    print(f"   持倉中:     {len(open_trades)}")
    
    # === Win Rate ===
    # Use adjusted_pnl for engine trades, otherwise pnl
    winning = 0
    losing = 0
    total_pnl = 0
    total_gross_profit = 0
    total_gross_loss = 0
    
    for t in closed_trades:
        pnl = t.get("pnl", 0)
        # Use adjusted_pnl if available (accounts for slippage)
        if "raw_pnl" in t and t["raw_pnl"] != 0:
            pnl = t["raw_pnl"]
        # For engine trades, adjusted_pnl is the real P&L
        if t.get("source", "").startswith("engine_") and t.get("pnl", 0) == 0 and t.get("raw_pnl", 0) == 0:
            # Use whatever is non-zero
            pnl = t.get("adjusted_pnl", t.get("pnl", 0))
        
        total_pnl += pnl
        if pnl > 0:
            winning += 1
            total_gross_profit += pnl
        elif pnl < 0:
            losing += 1
            total_gross_loss += abs(pnl)
    
    win_rate = (winning / len(closed_trades) * 100) if closed_trades else 0
    profit_factor = (total_gross_profit / total_gross_loss) if total_gross_loss > 0 else float('inf')
    
    print(f"\n🏆 績效指標:")
    print(f"   勝率:           {win_rate:.1f}% ({winning}勝/{losing}敗)")
    print(f"   已實現 P&L:     ${total_pnl:+,.2f}")
    print(f"   總獲利:         ${total_gross_profit:+,.2f}")
    print(f"   總虧損:         ${total_gross_loss:+,.2f}")
    print(f"   獲利因子:       {profit_factor:.2f}")
    
    # === Per-Market Breakdown ===
    print(f"\n{'=' * 70}")
    print("📊 各市場表現")
    print("=" * 70)
    
    markets = {}
    for t in trades:
        market = t.get("market", "unknown").lower()
        if market not in markets:
            markets[market] = {"trades": [], "closed": [], "pnl": 0}
        markets[market]["trades"].append(t)
        if t.get("status") == "closed":
            markets[market]["closed"].append(t)
    
    for market, data in sorted(markets.items()):
        closed = data["closed"]
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        losses = sum(1 for t in closed if t.get("pnl", 0) < 0)
        mkt_pnl = sum(t.get("pnl", 0) for t in closed)
        wr = (wins / len(closed) * 100) if closed else 0
        print(f"\n   📂 {market.upper()} ({len(data['trades'])}筆, {len(closed)}筆平倉)")
        print(f"      勝率: {wr:.1f}% ({wins}/{len(closed)})")
        print(f"      P&L: ${mkt_pnl:+,.2f}")
        
        # List all trades in this market
        for t in data["trades"]:
            pnl = t.get("pnl", 0)
            emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            status = t.get("status", "?")
            ticker = t.get("ticker", "?")
            direction = t.get("direction", "?")
            entry = t.get("entry", 0)
            strategy = t.get("strategy", "?")
            print(f"      {emoji} {ticker} {direction} @${entry:.2f} | P&L=${pnl:+,.2f} | {status} | {strategy}")
    
    # === Per-Strategy Breakdown ===
    print(f"\n{'=' * 70}")
    print("📊 各策略表現")
    print("=" * 70)
    
    strategies = {}
    for t in trades:
        strat = t.get("strategy", "manual")
        if strat not in strategies:
            strategies[strat] = {"trades": [], "closed": [], "pnl": 0}
        strategies[strat]["trades"].append(t)
        if t.get("status") == "closed":
            strategies[strat]["closed"].append(t)
    
    for strat, data in sorted(strategies.items()):
        closed = data["closed"]
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        losses = sum(1 for t in closed if t.get("pnl", 0) < 0)
        strat_pnl = sum(t.get("pnl", 0) for t in closed)
        wr = (wins / len(closed) * 100) if closed else 0
        print(f"\n   📊 {strat}")
        print(f"      總交易: {len(data['trades'])} | 平倉: {len(closed)}")
        print(f"      勝率: {wr:.1f}% ({wins}/{len(closed)})")
        print(f"      P&L: ${strat_pnl:+,.2f}")
    
    # === Monthly P&L ===
    print(f"\n{'=' * 70}")
    print("📅 每月 P&L 分析")
    print("=" * 70)
    
    monthly = {}
    for t in closed_trades:
        opened = t.get("opened_at", "")
        if opened:
            try:
                month_key = opened[:7]  # YYYY-MM
                if month_key not in monthly:
                    monthly[month_key] = {"trades": 0, "wins": 0, "pnl": 0}
                monthly[month_key]["trades"] += 1
                monthly[month_key]["pnl"] += t.get("pnl", 0)
                if t.get("pnl", 0) > 0:
                    monthly[month_key]["wins"] += 1
            except:
                pass
    
    for month in sorted(monthly.keys()):
        data = monthly[month]
        wr = (data["wins"] / data["trades"] * 100) if data["trades"] > 0 else 0
        print(f"   {month}: {data['trades']}筆交易 | {data['wins']}勝 | P&L ${data['pnl']:+,.2f} | 勝率 {wr:.1f}%")
    
    # === Best/Worst Trades ===
    print(f"\n{'=' * 70}")
    print("🏆 最佳/最差交易")
    print("=" * 70)
    
    if closed_trades:
        sorted_closed = sorted(closed_trades, key=lambda t: t.get("pnl", 0))
        worst = sorted_closed[:3]
        best = sorted_closed[-3:][::-1]
        
        print("\n   🏆 最佳交易 (Top 3):")
        for t in best:
            print(f"      🟢 {t.get('ticker','?')} {t.get('direction','?')} @${t.get('entry',0):.2f} | P&L=${t.get('pnl',0):+,.2f} | {t.get('strategy','?')}")
        
        print("\n   💀 最差交易 (Bottom 3):")
        for t in worst:
            print(f"      🔴 {t.get('ticker','?')} {t.get('direction','?')} @${t.get('entry',0):.2f} | P&L=${t.get('pnl',0):+,.2f} | {t.get('strategy','?')}")
    
    # === Anomaly Detection ===
    print(f"\n{'=' * 70}")
    print("⚠️ 異常檢測")
    print("=" * 70)
    
    # Check for duplicate deal_refs
    refs = [t.get("deal_ref") for t in trades if t.get("deal_ref")]
    from collections import Counter
    dup_refs = [ref for ref, count in Counter(refs).items() if count > 1]
    if dup_refs:
        print(f"\n   ❌ 重複 Deal Ref: {len(dup_refs)} 個")
        for ref in dup_refs:
            dups = [t for t in trades if t.get("deal_ref") == ref]
            items = []
            for x in dups:
                items.append("{} ({})".format(x.get("ticker","?"), x.get("status","?")))
            print(f"      {ref}: {len(dups)}次 — {', '.join(items)}")
    else:
        print("\n   ✅ 無重複 Deal Ref")
    
    # Check for trades with 0 P&L
    zero_pnl = [t for t in closed_trades if t.get("pnl", 0) == 0]
    if zero_pnl:
        print(f"\n   ⚠️ P&L=0 的平倉交易: {len(zero_pnl)} 筆")
        for t in zero_pnl:
            print(f"      {t.get('ticker','?')} {t.get('direction','?')} | {t.get('deal_ref','')} | P&L={t.get('pnl',0)} | source={t.get('source','')}")
    
    # Check for trades with no deal_id (marked "?")
    no_deal_id = [t for t in trades if t.get("deal_id", "") == "?"]
    if no_deal_id:
        print(f"\n   ⚠️ Deal ID='?' 的交易: {len(no_deal_id)} 筆")
    
    return {
        "total_trades": len(trades),
        "closed_trades": len(closed_trades),
        "open_trades": len(open_trades),
        "win_rate": win_rate,
        "wins": winning,
        "losses": losing,
        "total_pnl": total_pnl,
        "gross_profit": total_gross_profit,
        "gross_loss": total_gross_loss,
        "profit_factor": profit_factor,
    }


if __name__ == "__main__":
    print("\n" + "🔥" * 35)
    print("🔥   CAPITAL.COM DEMO PAPER TRADING 全面審計報告   🔥")
    print("🔥" * 35 + "\n")
    
    # Step 1: Account balances from API
    balances, total_dep, total_bal, total_pnl = fetch_all_account_balances()
    
    # Step 2: Open positions from API
    positions = fetch_open_positions()
    
    # Step 3: Transaction history (API)
    fetch_transaction_history()
    
    # Step 4: Reconstruct trade history from files
    trades = reconstruct_trade_history()
    
    # Step 5: Analyze trades
    metrics = analyze_trades(trades)
    
    # Step 6: Final summary
    print(f"\n{'=' * 70}")
    print("📋 最終審計匯總")
    print("=" * 70)
    
    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📊 CAPITAL.COM DEMO PAPER TRADING 審計報告
  生成時間: {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【帳戶概況】
  總入金:     ${total_dep:,.2f}
  總餘額:     ${total_bal:,.2f}
  總 P&L:     ${total_pnl:+,.2f}
  總回報率:   {((total_pnl/total_dep*100) if total_dep>0 else 0):+.2f}%

【交易統計】
  總交易數:   {metrics['total_trades']}
  已平倉:     {metrics['closed_trades']}
  持倉中:     {metrics['open_trades']}
  勝率:       {metrics['win_rate']:.1f}% ({metrics['wins']}勝/{metrics['losses']}敗)
  已實現 P&L: ${metrics['total_pnl']:+,.2f}
  獲利因子:   {metrics['profit_factor']:.2f}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")
