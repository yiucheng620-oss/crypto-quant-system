#!/usr/bin/env python3
"""
vbt_ranking.py - vectorbt 策略排名報告（繁體中文）
讀取 vbt_results/latest_ranking.json，輸出 Telegram 友善的簡潔報告
"""
import json
import os
import sys
from datetime import datetime

REPORT_PATH = os.path.expanduser("~/workspace/vbt_results/latest_ranking.json")

ASSET_NAMES = {
    "BTC_USD": "BTC",
    "ETH_USD": "ETH",
    "SOL_USD": "SOL",
}

def parse_label(label):
    """從標籤解析策略類型、資產、參數"""
    parts = label.split("_")
    if not parts:
        return label, "", ""
    strategy_type = parts[0]  # RSI, BB, VP
    # 找出資產部分
    asset_parts = []
    for p in parts[1:]:
        if p in ("BTC", "ETH", "SOL"):
            asset_parts.append(p)
        elif p == "USD":
            asset_parts.append(p)
            break
        else:
            break
    asset = "_".join(asset_parts) if asset_parts else ""
    # 參數部分是剩下的
    param_start = len(strategy_type) + len(asset.split("_"))
    params = "_".join(parts[param_start:]) if param_start < len(parts) else ""
    return strategy_type, asset, params


def strategy_emoji(stype):
    return {"RSI": "📊", "BB": "📉", "VP": "📈"}.get(stype, "📌")


def format_pct(val, is_dd=False):
    """格式化百分比"""
    if val is None:
        return "N/A"
    if is_dd:
        return f"-{abs(val):.2f}%"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.2f}%"


def generate_report():
    if not os.path.exists(REPORT_PATH):
        return "❌ 找不到排名數據，請先執行 vbt_backtest.py"

    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    lines = []
    ts = data.get("timestamp", "unknown")
    total = data.get("total_strategies", 0)
    valid = data.get("valid_strategies", 0)
    top3 = data.get("top3_sharpe", [])
    worst3 = data.get("worst3_sharpe", [])
    by_asset = data.get("by_asset_summary", {})

    # 標題
    dt_str = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%m/%d %H:%M") if ts != "unknown" else ts
    lines.append(f"🤖 *vectorbt 策略排名報告*")
    lines.append(f"🕐 {dt_str} | 共 {total} 組策略 ({valid} 組有效)\n")

    # Top 3 最佳
    lines.append(f"🏆 *Top 3 最佳策略*")
    for i, r in enumerate(top3, 1):
        stype, asset, _ = parse_label(r["strategy"])
        emoji = strategy_emoji(stype)
        asset_short = ASSET_NAMES.get(asset, asset)
        sharpe = f"{r['sharpe']:.2f}" if r['sharpe'] else "N/A"
        wr = f"{r['wr_pct']:.0f}%" if r['wr_pct'] else "N/A"
        dd = format_pct(r['max_dd_pct'])
        ret = format_pct(r['total_return_pct'])
        trades = r['trades']
        lines.append(
            f"{i}. {emoji} {stype}/{asset_short}  Sharpe={sharpe}"
        )
        lines.append(f"   WR={wr}  DD={dd}  Ret={ret}  Trades={trades}")

    lines.append("")

    # Worst 3 最差
    lines.append(f"⚠️ *Worst 3 最差策略*")
    for i, r in enumerate(worst3, 1):
        stype, asset, _ = parse_label(r["strategy"])
        emoji = strategy_emoji(stype)
        asset_short = ASSET_NAMES.get(asset, asset)
        sharpe = f"{r['sharpe']:.2f}" if r['sharpe'] else "N/A"
        wr = f"{r['wr_pct']:.0f}%" if r['wr_pct'] else "N/A"
        dd = format_pct(r['max_dd_pct'])
        ret = format_pct(r['total_return_pct'])
        trades = r['trades']
        lines.append(
            f"{i}. {emoji} {stype}/{asset_short}  Sharpe={sharpe}"
        )
        lines.append(f"   WR={wr}  DD={dd}  Ret={ret}  Trades={trades}")

    lines.append("")

    # 各資產最佳策略
    lines.append(f"📊 *各資產最佳策略*")
    for asset, summary in sorted(by_asset.items()):
        best = summary["best"]
        asset_short = ASSET_NAMES.get(asset, asset)
        stype, _, _ = parse_label(best["strategy"])
        emoji = strategy_emoji(stype)
        sharpe = f"{best['sharpe']:.2f}" if best['sharpe'] else "N/A"
        wr = f"{best['wr_pct']:.0f}%" if best['wr_pct'] else "N/A"
        ret = format_pct(best['total_return_pct'])
        avg_sharpe = f"{summary['avg_sharpe']:.2f}"
        count = summary["count"]
        lines.append(
            f"  {emoji} {asset_short}: {stype}  S={sharpe}  WR={wr}  Ret={ret}"
        )
        lines.append(f"     平均 Sharpe={avg_sharpe}（{count} 組有效）")

    lines.append("")

    # 策略類型勝率統計
    lines.append(f"📈 *策略類型表現*")
    type_stats = {}
    for r in data.get("rankings", []):
        stype, _, _ = parse_label(r["strategy"])
        if r["sharpe"] is not None:
            type_stats.setdefault(stype, []).append(r["sharpe"])
    for stype in ["RSI", "BB", "VP"]:
        vals = type_stats.get(stype, [])
        if vals:
            avg_s = sum(vals) / len(vals)
            best_s = max(vals)
            lines.append(f"  {strategy_emoji(stype)} {stype}: 平均 Sharpe={avg_s:.2f}  最佳={best_s:.2f}  組數={len(vals)}")

    lines.append("")
    lines.append("💡 *說明*")
    lines.append("  • RSI = 相對強弱指標  BB = 布林通道  VP = 成交量分布")
    lines.append("  • 僅納入 ≥2 筆完成交易的策略")
    lines.append(f"  • 手續費 0.04% | 滑價 0.05% | 日線數據 (183 根 K 棒)")

    return "\n".join(lines)


if __name__ == "__main__":
    report = generate_report()
    print(report)
