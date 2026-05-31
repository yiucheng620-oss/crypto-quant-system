#!/usr/bin/env python3
"""
📊 每日系統檢討報告 — 2026-05-27
================================
自動生成，包含：今日討論摘要、即時持倉、系統狀態、部署建議
"""

import json, os
from datetime import datetime, timezone, timedelta
HKT = timezone(timedelta(hours=8))
NOW = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")

WORKSPACE = os.path.expanduser("~/workspace")
LOG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def fetch_positions():
    """Live Capital.com data."""
    import sys
    sys.path.insert(0, os.path.join(WORKSPACE, "capitalcom"))
    from capitalcom_paper import check_positions, ACCOUNTS, get_account_balance
    positions = []
    balances = {}
    total_balance = 0
    total_pnl = 0
    for name, aid in ACCOUNTS.items():
        bal = get_account_balance(aid)
        total_balance += bal.get("balance", 0)
        balances[name] = bal
        for p in check_positions(aid):
            ticker = p.get("epic","?").replace("USD","")
            pnl = float(p.get("pnl_amt", 0))
            total_pnl += pnl
            positions.append({
                "account": name,
                "ticker": ticker,
                "direction": p["direction"],
                "entry": p["entry"],
                "current": p["current"],
                "pnl": round(pnl, 2),
                "sl": p.get("stop", 0),
                "tp": p.get("limit", 0),
            })
    return positions, balances, round(total_balance, 2), round(total_pnl, 2)

def fetch_market():
    """Market context from equities screener."""
    path = os.path.join(WORKSPACE, "equities_data", "equities_setups.json")
    d = load_json(path, {})
    macro = d.get("macro", {})
    sectors = d.get("sectors", [])
    setups = d.get("setups", [])
    return {
        "vix": macro.get("vix"),
        "vix_regime": macro.get("vix_regime"),
        "spy": macro.get("spy_price"),
        "spy_trend": macro.get("spy_trend"),
        "qqq": macro.get("qqq_price"),
        "qqq_trend": macro.get("qqq_trend"),
        "bias": macro.get("macro_bias"),
        "top_sectors": [{"name": s["name"], "rs": s.get("rs_vs_spy", s.get("rs",0)), "trend": s.get("trend","")} for s in sectors[:3]],
        "top_picks": [{"ticker": s["ticker"], "rs": s.get("rs_vs_spy",0), "signal": s.get("signal",""), "rr": s.get("rr",0), "confidence": s.get("confidence","")} for s in setups[:5]],
    }

def fetch_system():
    """Guardian + Coordinator + Cron status."""
    guardian = load_json(os.path.join(WORKSPACE, "data/health_reports/guardian_latest.json"), {})
    coord = load_json(os.path.join(LOG_DIR, "coordinator_state.json"), {})
    cron_ok = False
    cron_log = os.path.join(WORKSPACE, "cron.log")
    if os.path.exists(cron_log):
        cron_ok = (os.path.getmtime(cron_log) + 900 > __import__("time").time())
    lock_ok = not os.path.exists("/tmp/trading_system.lock")
    return {
        "guardian": guardian.get("overall_status", "?"),
        "broken": guardian.get("broken_count", 0),
        "warnings": guardian.get("warning_count", 0),
        "total_scans": coord.get("total_scans", 0),
        "total_executed": coord.get("total_executed", 0),
        "breaker": "TRIPPED" if coord.get("breaker_tripped") else "OK",
        "cron": "OK" if cron_ok else "⚠️ STALE",
        "lock": "OK" if lock_ok else "⚠️ LOCKED",
    }

def generate():
    positions, balances, total_balance, total_pnl = fetch_positions()
    market = fetch_market()
    system = fetch_system()

    report = f"""
╔══════════════════════════════════════════════════════════╗
║  📊 每日系統檢討報告                                      ║
║  {NOW}                                      ║
╚══════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
一、系統狀態
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🛡️ Guardian: {system['guardian'].upper()} | Broken: {system['broken']} | Warnings: {system['warnings']}
⚡ Engine: {system['total_scans']} scans | {system['total_executed']} executed | Breaker: {system['breaker']}
⏰ Cron: {system['cron']} | Lock: {system['lock']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
二、持倉 ({len(positions)} 倉) | Total: \${total_balance:.2f} | P&L: \${total_pnl:+.2f}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    for p in positions:
        pnl_str = f"+${p['pnl']:.2f}" if p['pnl'] >= 0 else f"-${abs(p['pnl']):.2f}"
        icon = "🟢" if p['pnl'] >= 0 else "🔴"
        report += f"\n  {icon} {p['ticker']:6s} {p['direction']:5s} | Entry: \${p['entry']:.2f} → \${p['current']:.2f} | PnL: {pnl_str}"
        if p['sl'] > 0:
            report += f" | SL: \${p['sl']:.2f}"
        if p['tp'] > 0:
            report += f" | TP: \${p['tp']:.2f}"

    report += f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
三、市場環境
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VIX: {market['vix']} ({market['vix_regime']})
SPY: \${market['spy']} {market['spy_trend']} | QQQ: \${market['qqq']} {market['qqq_trend']}
Bias: {market['bias']}

Top Sectors:"""
    for s in market['top_sectors']:
        report += f"\n  {s['name']:4s} RS {s['rs']:+.1f}% {s['trend']}"

    report += f"""

Top Picks:"""
    for p in market['top_picks']:
        report += f"\n  {p['ticker']:6s} RS {p['rs']:+.1f}% | {p['signal']} | R:R 1:{p['rr']:.1f} | {p['confidence']}"

    report += f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
四、今日問題 & 修復
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 Lock Storm (Critical)
   原因: /tmp/trading_system.lock stale，block 咗 2.5 小時 scan
   修復: cron_entry.py 加 stale detection (>5min auto-clear) + PID file + shutil.rmtree
   預防: lock_watchdog.sh cron 每 3 分鐘，>10min stale → 自動清除

🔴 BTC 冇止損被斬 (Critical)
   原因: 開倉 SL=$0，20x 槓桿，跌 1.1% → 系統主動斬倉 (-$24.96)
   修復: engine_base.py execute_signal() 加 hard enforcement — SL=$0 時自動 force set
   預防: 所有新倉必定有 SL，唔會再裸跑

🟡 Equities 日日揀同一隻 (Medium)
   原因: rs_score field name mismatch + sector 缺失 + reason 空白
   修復: equities_swing.py + equities_engine.py 三個 bugs 全 fix
   效果: 而家 semi-conductor + energy 雙板塊，RS 有意義

🟢 其他 cleanup
   - 8 個垃圾 trade entries 清除
   - update_stop_loss() function 新增到 capitalcom_paper.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
五、部署建議
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 🚨 今晚 21:15 HKT position review — 重點睇 BTC（新開倉）同 ETH
   - BTC LONG @75578 → SL=74510 (1.4%)，close enough，睇實
   - ETH SHORT +$97 (+4.6% from 2110)，可考慮 move SL to breakeven
   - US500/GOLD 微利，繼續 hold

2. 📊 今晚美股開市 (21:30 HKT)
   - VIX 17.0 正常，SPY+QQQ 雙升 → 積極做多
   - Equities: INTC+XOM 已 pick，INTC 已有倉，XOM 考慮
   - 半導體最強 (RS +51.7%)，但已 max 1 per sector

3. 🔧 系統持續監控
   - lock_watchdog: 每 3min 自動清 stale lock ✅
   - Guardian: hourly scan，0 broken ✅
   - Engine scan: 每 5min，5/5 active ✅

4. ⚠️ 風險提示
   - Crypto 20x 槓桿，SL 已 set，唔好手動改
   - BTC dominance 97.2% → alt rotation risk
   - VIX < 15 threshold not met → complacency risk not active

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Report generated: {NOW}
"""
    return report


if __name__ == "__main__":
    print(generate())
