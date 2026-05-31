#!/usr/bin/env python3
"""
Weekly Signal Accuracy Report — Structured (No LLM)
════════════════════════════════════════════════════
Generates a formatted Traditional Chinese accuracy report
from the summary JSON. No LLM token cost.

Cron: weekly Mon 09:00 HKT, no_agent, deliver: origin
"""

import os, sys, json, glob
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
REPORT_DIR = os.path.join(WORKSPACE, "accuracy_reports")
DB_PATH = os.path.join(WORKSPACE, "signal_accuracy.db")

def find_latest_summary():
    """Find the latest summary JSON."""
    files = sorted(glob.glob(os.path.join(REPORT_DIR, "summary_*.json")), reverse=True)
    return files[0] if files else None

def format_report(data):
    """Format accuracy report in Traditional Chinese."""
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")
    generated = data.get("generated_at", "?")[:10]
    period = data.get("period_days", 90)
    
    lines = [
        f"📊 **信號準確度週報** — {generated}",
        f"📅 報告期間: {period} 日 | 生成: {now}",
        "",
        "## 🏆 Indicator 表現",
        "",
    ]
    
    # Indicators sorted by WR
    indicators = data.get("indicators", [])
    if indicators:
        sorted_inds = sorted(indicators, key=lambda x: x.get("win_rate", 0), reverse=True)
        
        for i in sorted_inds:
            wr = i.get("win_rate", 0)
            n = i.get("total_signals", 0)
            name = i.get("name", "?")
            coin = i.get("coin", "?")
            tf = i.get("tf", "?")
            avg_win = i.get("avg_win_pct", 0)
            avg_loss = i.get("avg_loss_pct", 0)
            
            if n < 3:
                tag = "⚠️ 樣本不足"
            elif wr >= 60:
                tag = "🟢"
            elif wr >= 50:
                tag = "🟡"
            else:
                tag = "🔴"
            
            wr_str = f"{wr:.0f}%" if n >= 3 else f"{wr:.0f}% (⚠️ n={n})"
            lines.append(f"  {tag} **{name}** ({coin} {tf}) — WR={wr_str} | n={n} | +{avg_win:.1f}% / {avg_loss:.1f}%")
    
    else:
        lines.append("  _無足夠數據_")
    
    # Market state
    market = data.get("current_market_state", {})
    if market:
        lines.append("")
        lines.append("## 🌍 當前市場狀態")
        lines.append("")
        for key, info in market.items():
            if isinstance(info, dict):
                cond = info.get("condition", "?")
                adx = info.get("adx", 0)
                emoji = {"bull": "🟢", "range": "🟡", "bear": "🔴", "volatile": "🟣"}.get(cond, "⚪")
                lines.append(f"  {emoji} {key}: {cond} (ADX={adx})")
    
    # Pending signals
    pending = data.get("pending_signals", [])
    if pending:
        lines.append("")
        lines.append(f"## ⏳ Pending Signals ({len(pending)})")
        lines.append("")
        for p in pending[:8]:
            ind = p.get("indicator", "?")
            coin = p.get("coin", "?")
            direction = p.get("direction", "?")
            count = p.get("pending_count", 0)
            price = p.get("latest_price", 0)
            dir_emoji = "🔺" if direction == "LONG" else "🔻"
            lines.append(f"  {dir_emoji} {ind} {coin} x{count} @ ${price:,.0f}")
    
    # Accuracy by regime (from accuracy_matrix if available)
    matrix = data.get("accuracy_matrix", {})
    if matrix and isinstance(matrix, dict):
        lines.append("")
        lines.append("## 📐 Per-Regime 準確度")
        lines.append("")
        for ind_name, regimes in matrix.items():
            if isinstance(regimes, dict):
                regime_parts = []
                for regime, info in regimes.items():
                    if isinstance(info, dict):
                        wr = info.get("win_rate", 0)
                        n = info.get("total", 0)
                        regime_parts.append(f"{regime}={wr:.0f}%(n={n})")
                if regime_parts:
                    lines.append(f"  **{ind_name}**: {', '.join(regime_parts)}")
    
    # Strategy notes
    lines.append("")
    lines.append("## 💡 策略備忘")
    lines.append("")
    
    # Identify best and worst
    if indicators:
        best = sorted_inds[0]
        worst = sorted_inds[-1]
        lines.append(f"  ✅ 最強: {best['name']} ({best['coin']} {best['tf']}) — WR={best['win_rate']:.0f}%")
        lines.append(f"  ⚠️ 最弱: {worst['name']} ({worst['coin']} {worst['tf']}) — WR={worst['win_rate']:.0f}%")
    
    lines.append("  ⏰ 24h exit rule 強制")
    lines.append("  📡 僅供參考，不構成投資建議")
    
    # Footer
    lines.append("")
    lines.append(f"_🤖 Structured Report | Data: {period}d signal accuracy_")
    
    return "\n".join(lines)

def main():
    # Run signal_logger --report to generate fresh data
    import subprocess
    venv_python = os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python3")
    
    result = subprocess.run(
        [venv_python, os.path.join(WORKSPACE, "signal_logger.py"), "--report"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "HOME": os.path.expanduser("~")},
        cwd=WORKSPACE
    )
    
    # Find latest summary
    summary_path = find_latest_summary()
    if not summary_path:
        print("❌ No summary JSON found")
        sys.exit(1)
    
    import json
    with open(summary_path) as f:
        data = json.load(f)
    
    report = format_report(data)
    print(report)

if __name__ == "__main__":
    main()
