#!/usr/bin/env python3
"""
Capital.com Demo Accounts — 全面審計腳本 (v2, 正確合併引擎數據)
"""
import sys, os, json
sys.path.insert(0, os.path.expanduser("~/workspace"))
os.chdir(os.path.expanduser("~/workspace"))

from capitalcom_paper import (ACCOUNTS, get_account_balance, check_positions, _headers, DEMO_URL)
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")

def load_json_any(path):
    if os.path.exists(path):
        with open(path) as f:
            c = f.read().strip()
            if c and c != "[]":
                return json.loads(c)
    return []

def fetch_balances():
    print("=" * 70)
    print("🏦 CAPITAL.COM DEMO — 帳戶餘額快照")
    print(f"   擷取時間: {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
    print("=" * 70)
    results = {}
    td, tb, tp = 0, 0, 0
    names = {"crypto":"加密貨幣(Crypto)","equities":"美股(Equities)","indices":"指數(Indices)","gold":"黃金(Gold)"}
    for key, aid in ACCOUNTS.items():
        try:
            b = get_account_balance(aid)
            if b:
                results[key]=b
                d=b.get("deposit",0); bl=b.get("balance",0); p=b.get("pnl",0); av=b.get("available",0)
                pct=(p/d*100) if d>0 else 0
                print(f"\n📂 {names[key]} (ID: {aid})")
                print(f"   初始入金: ${d:,.2f}  目前餘額: ${bl:,.2f}")
                print(f"   P&L: ${p:+,.2f} ({pct:+.2f}%)  可用: ${av:,.2f}")
                td+=d; tb+=bl; tp+=p
        except Exception as e:
            print(f"\n❌ {key}: {e}")
    print(f"\n💰 合計: 總入金=${td:,.2f}  總餘額=${tb:,.2f}  總P&L=${tp:+,.2f}  總回報率={(tp/td*100) if td>0 else 0:+.2f}%")
    return results, td, tb, tp

def fetch_positions():
    print(f"\n{'='*70}")
    print("📊 當前持倉 (API)")
    print("="*70)
    names = {"crypto":"加密貨幣","equities":"美股","indices":"指數","gold":"黃金"}
    top = 0
    for key, aid in ACCOUNTS.items():
        try:
            pos = check_positions(aid)
            if not pos:
                print(f"\n📂 {names[key]} — 無持倉"); continue
            print(f"\n📂 {names[key]} ({len(pos)}持倉):")
            ap = 0
            for p in pos:
                e = "🟢" if p["pnl_amt"]>0 else "🔴" if p["pnl_amt"]<0 else "⚪"
                print(f"   {e} {p['ticker']:<6s} {p['direction']:<5s} 入場${p['entry']:.2f}→現價${p['current']:.2f}  P&L${p['pnl_amt']:+,.2f}({p['pnl_pct']:+.2f}%)  止損${p['stop']:.2f}  目標${p['limit']:.2f}")
                ap+=p["pnl_amt"]
            print(f"   💰 小計: ${ap:+,.2f}")
            top+=ap
        except Exception as e:
            print(f"\n❌ {key}: {e}")
    print(f"\n💰 總浮動P&L: ${top:+,.2f}")

def merge_all_trades():
    print(f"\n{'='*70}")
    print("📈 完整交易歷史合併重建")
    print("="*70)
    
    # Load all sources (unified)
    paper_trades = load_json_any(os.path.join(WORKSPACE,"scalp_lab","engines","paper_trades.json"))
    
    engine_paths = {
        "btc": os.path.join(WORKSPACE,"scalp_lab","engines","btc_trades.json"),
        "eth": os.path.join(WORKSPACE,"scalp_lab","engines","eth_trades.json"),
        "gold": os.path.join(WORKSPACE,"scalp_lab","engines","gold_trades.json"),
        "indices": os.path.join(WORKSPACE,"scalp_lab","engines","indices_trades.json"),
        "equities": os.path.join(WORKSPACE,"scalp_lab","engines","equities_trades.json"),
    }
    engines = {}
    for ek, ep in engine_paths.items():
        engines[ek] = load_json_any(ep)
    
    print(f"📁 paper_trades.json: {len(paper_trades)}筆 (Paper Trades)")
    for ek, et in engines.items():
        if et:
            print(f"📁 engines/{ek}_trades.json ({ek}引擎): {len(et)}筆")
    
    # Build master dict by deal_ref
    master = {}  # deal_ref -> merged trade
    
    # Priority: engine data > paper trade data
    def add_source(t, source_name):
        ref = t.get("deal_ref","") or t.get("deal_id","")
        if not ref:
            return
        if ref not in master:
            master[ref] = {"deal_ref": ref, "sources": []}
        master[ref]["sources"].append(source_name)
        
        # Fill in fields (first seen wins unless engine overrides)
        fields = t.copy()
        fields.pop("deal_ref", None)
        if "deal_id" in fields and master[ref].get("deal_id","?")=="?" and fields["deal_id"]!="?":
            master[ref]["deal_id"] = fields["deal_id"]
        if source_name.startswith("engine_"):
            # Engine data is more authoritative for status/pnl
            for k,v in fields.items():
                if k in ("status","pnl","adjusted_pnl","raw_pnl","slippage_cost","closed_at","exit_price","close_reason","strategy"):
                    master[ref][k] = v
        # Always fill missing
        for k,v in fields.items():
            if k not in master[ref] or not master[ref][k]:
                master[ref][k] = v
    
    for t in paper_trades:
        add_source(t, "paper")
    for ek, et in engines.items():
        for t in et:
            add_source(t, f"engine_{ek}")
    
    # Now also cross-reference with API positions to get real deal_ids
    for key, aid in ACCOUNTS.items():
        try:
            pos = check_positions(aid)
            for p in pos:
                # Try to match by ticker+entry
                for ref, m in master.items():
                    if m.get("ticker","") == p["ticker"] and abs(m.get("entry",0) - p["entry"]) < 5:
                        if m.get("deal_id","?")=="?" and p.get("deal_id","?")!="?":
                            m["deal_id"] = p["deal_id"]
                        m["current_price"] = p["current"]
                        m["current_pnl"] = p["pnl_amt"]
        except:
            pass
    
    # Fill defaults
    for ref, m in master.items():
        if "status" not in m or not m["status"]:
            m["status"] = "open"
        if "strategy" not in m:
            m["strategy"] = m.get("strategy","manual")
        if "market" not in m:
            m["market"] = "unknown"
        if "ticker" not in m:
            m["ticker"] = m.get("label", m.get("epic","?"))
        # For engine trades with adjusted_pnl, that's the real P&L
        if m.get("source","").startswith("engine_") or m.get("source","") == "scalp":
            if m.get("pnl",0) == 0 and m.get("adjusted_pnl",0) != 0:
                m["pnl"] = m["adjusted_pnl"]
            if m.get("raw_pnl",0) != 0:
                m["pnl"] = m["raw_pnl"]
        # Capital orders tracker doesn't track closed positions properly
        # But engine files correctly mark them. Ensure consistency.
        if m.get("closed_at") and m["status"] == "open":
            m["status"] = "closed"
    
    trades = sorted(master.values(), key=lambda x: x.get("opened_at", x.get("timestamp","")))
    print(f"\n📊 合併去重後: {len(trades)}筆唯一交易")
    
    closed_count = sum(1 for t in trades if t.get("status")=="closed")
    open_count = sum(1 for t in trades if t.get("status")=="open")
    print(f"   已平倉: {closed_count}  持倉中: {open_count}")
    
    return trades

def analyze(trades):
    print(f"\n{'='*70}")
    print("📊 交易績效分析")
    print("="*70)
    
    closed = [t for t in trades if t.get("status")=="closed"]
    opened = [t for t in trades if t.get("status")=="open"]
    
    print(f"\n📊 總覽: 總交易={len(trades)}  已平倉={len(closed)}  持倉中={len(opened)}")
    
    wins=0; losses=0; tp=0; gp=0; gl=0
    for t in closed:
        p = t.get("pnl",0)
        tp+=p
        if p>0: wins+=1; gp+=p
        elif p<0: losses+=1; gl+=abs(p)
    wr = (wins/len(closed)*100) if closed else 0
    pf = (gp/gl) if gl>0 else float('inf')
    
    print(f"\n🏆 已平倉績效:")
    print(f"   勝率: {wr:.1f}% ({wins}勝/{losses}敗)")
    print(f"   已實現P&L: ${tp:+,.2f}  獲利因子: {pf:.2f}")
    
    # Per-market
    print(f"\n{'='*70}")
    print("📊 各市場表現")
    print("="*70)
    markets = defaultdict(lambda: {"total":0,"closed":0,"wins":0,"pnl":0,"trades":[]})
    for t in trades:
        m = t.get("market","unknown").lower()
        markets[m]["total"]+=1
        markets[m]["trades"].append(t)
        if t.get("status")=="closed":
            markets[m]["closed"]+=1
            p=t.get("pnl",0)
            markets[m]["pnl"]+=p
            if p>0: markets[m]["wins"]+=1
    for m, d in sorted(markets.items()):
        wr2=(d["wins"]/d["closed"]*100) if d["closed"] else 0
        print(f"\n   📂 {m.upper()} ({d['total']}筆, {d['closed']}筆平倉)")
        print(f"      勝率: {wr2:.1f}% ({d['wins']}/{d['closed']})  P&L: ${d['pnl']:+,.2f}")
        for t in d["trades"]:
            p=t.get("pnl",0)
            e="🟢" if p>0 else "🔴" if p<0 else "⚪"
            s=t.get("status","?")
            st=t.get("strategy","?")
            op=t.get("opened_at",t.get("timestamp",""))[:16]
            print(f"      {e} {t.get('ticker','?'):<6s} {t.get('direction','?'):<5s} @${t.get('entry',0):.2f}  P&L=${p:+,.2f}  {s}  [{st}]  {op}")
    
    # Per-strategy  
    print(f"\n{'='*70}")
    print("📊 各策略表現")
    print("="*70)
    strats = defaultdict(lambda: {"total":0,"closed":0,"wins":0,"pnl":0})
    for t in trades:
        s = t.get("strategy","manual")
        strats[s]["total"]+=1
        if t.get("status")=="closed":
            strats[s]["closed"]+=1
            p=t.get("pnl",0)
            strats[s]["pnl"]+=p
            if p>0: strats[s]["wins"]+=1
    for s, d in sorted(strats.items()):
        wr2=(d["wins"]/d["closed"]*100) if d["closed"] else 0
        print(f"\n   📊 {s}")
        print(f"      總:{d['total']}  平倉:{d['closed']}  勝率:{wr2:.1f}%({d['wins']}/{d['closed']})  P&L:${d['pnl']:+,.2f}")
    
    # Monthly
    print(f"\n{'='*70}")
    print("📅 每月P&L")
    print("="*70)
    monthly = defaultdict(lambda: {"trades":0,"wins":0,"pnl":0})
    for t in closed:
        ts = t.get("opened_at",t.get("timestamp",""))
        if ts:
            mk = ts[:7]
            monthly[mk]["trades"]+=1
            monthly[mk]["pnl"]+=t.get("pnl",0)
            if t.get("pnl",0)>0: monthly[mk]["wins"]+=1
    for mk in sorted(monthly):
        d=monthly[mk]
        wr2=(d["wins"]/d["trades"]*100) if d["trades"] else 0
        print(f"   {mk}: {d['trades']}筆 {d['wins']}勝 P&L${d['pnl']:+,.2f} 勝率{wr2:.1f}%")
    
    # Best/worst
    print(f"\n{'='*70}")
    print("🏆 最佳/最差交易")
    print("="*70)
    if closed:
        sc = sorted(closed, key=lambda t: t.get("pnl",0))
        print("\n   💀 最差交易:")
        for t in sc[:3]:
            print(f"      🔴 {t.get('ticker','?')} {t.get('direction','?')} @${t.get('entry',0):.2f} P&L=${t.get('pnl',0):+,.2f} [{t.get('strategy','?')}]")
        print("\n   🏆 最佳交易:")
        for t in reversed(sc[-3:]):
            print(f"      🟢 {t.get('ticker','?')} {t.get('direction','?')} @${t.get('entry',0):.2f} P&L=${t.get('pnl',0):+,.2f} [{t.get('strategy','?')}]")
    
    # Anomalies
    print(f"\n{'='*70}")
    print("⚠️ 異常檢測")
    print("="*70)
    zero_pnl = [t for t in closed if t.get("pnl",0)==0]
    if zero_pnl:
        print(f"\n   ⚠️ P&L=0的平倉交易: {len(zero_pnl)}筆")
        for t in zero_pnl:
            print(f"      {t.get('ticker','?')} {t.get('direction','?')} ref={t.get('deal_ref','')} source={t.get('sources',['?'])}")
    else:
        print(f"\n   ✅ 無P&L=0的已平倉交易")
    
    no_deal_id = [t for t in trades if t.get("deal_id","")=="?" or not t.get("deal_id")]
    if no_deal_id:
        print(f"\n   ⚠️ Deal_ID='?'的交易: {len(no_deal_id)}筆 (可能是API未返回真實ID)")
    
    # Check straddles/conflicting directions on same ticker
    ticker_dirs = defaultdict(set)
    for t in opened:
        ticker_dirs[t.get("ticker","?")].add(t.get("direction","?"))
    for tk, dirs in ticker_dirs.items():
        if len(dirs)>1:
            print(f"\n   ⚠️ {tk}: 同時持有多空方向 {dirs} — 可能是對沖")
    
    return {"closed":len(closed),"open":len(opened),"wins":wins,"losses":losses,"win_rate":wr,"pnl":tp,"profit_factor":pf}

if __name__ == "__main__":
    print("\n" + "🔥"*35)
    print("🔥   CAPITAL.COM PAPER TRADING 全面審計報告   🔥")
    print("🔥"*35 + "\n")
    
    bal, td, tb, tp = fetch_balances()
    fetch_positions()
    all_trades = merge_all_trades()
    metrics = analyze(all_trades)
    
    # Combine API P&L (realized + unrealized) with trade history
    print(f"\n{'='*70}")
    print("📋 最終審計匯總")
    print("="*70)
    
    # Get actual API P&L (realized + floating)
    total_api_pnl = tp  # from fetch_balances
    trade_pnl = metrics["pnl"]  # from closed trades in engine files
    
    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📊 CAPITAL.COM DEMO PAPER TRADING 全面審計報告
  生成: {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【帳戶概況 (API)】
  帳戶數: 4 (加密貨幣/美股/指數/黃金)
  總入金:     ${td:,.2f}
  總餘額:     ${tb:,.2f}
  總P&L(已實現+浮動): ${tp:+,.2f}
  總回報率:   {(tp/td*100) if td>0 else 0:+.2f}%

【交易統計 (引擎日誌)】
  總開倉交易: {metrics['closed']+metrics['open']}
  已平倉:     {metrics['closed']}
  持倉中:     {metrics['open']}
  勝率:       {metrics['win_rate']:.1f}% ({metrics['wins']}勝/{metrics['losses']}敗)
  已實現P&L:  ${trade_pnl:+,.2f}
  獲利因子:   {metrics['profit_factor']:.2f}

【帳戶明細】
  加密貨幣: 入金$1,000.90 → 餘額${bal.get('crypto',{}).get('balance',0):.2f} → P&L${bal.get('crypto',{}).get('pnl',0):+,.2f}
  美股:     入金$9,993.02 → 餘額${bal.get('equities',{}).get('balance',0):.2f} → P&L${bal.get('equities',{}).get('pnl',0):+,.2f}
  指數:     入金$1,000.33 → 餘額${bal.get('indices',{}).get('balance',0):.2f} → P&L${bal.get('indices',{}).get('pnl',0):+,.2f}
  黃金:     入金$996.60 → 餘額${bal.get('gold',{}).get('balance',0):.2f} → P&L${bal.get('gold',{}).get('pnl',0):+,.2f}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
注意:
• 引擎日誌中大部分平倉交易的P&L=0 (因closed_at是由scalerunner記錄,
  但實際P&L由Capital.com API計算, 未回寫到日誌中)
• API的P&L包含所有已實現+浮動損益, 為最終準確數字
• 3筆BTC scalp及1筆XAUUSD在engine檔案中被標記為closed但P&L=0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")
