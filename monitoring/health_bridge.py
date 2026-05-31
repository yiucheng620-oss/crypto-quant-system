#!/usr/bin/env python3
"""
Health Bridge — 將 Indicator Health Monitor 整合到 Gemini Daily Analyst
═══════════════════════════════════════════════════════════
用法：被 gemini_daily_analyst.py import，提供 get_indicator_health()
輸出：簡潔健康摘要字串，直接 inject 入 Gemini prompt
═══════════════════════════════════════════════════════════
"""

import os, json

HEALTH_REPORT = os.path.expanduser("~/workspace/health_reports/health_latest.json")


def get_indicator_health() -> str:
    """Return concise health summary for Gemini prompt injection."""
    if not os.path.exists(HEALTH_REPORT):
        return "（無健康報告數據）"

    try:
        with open(HEALTH_REPORT) as f:
            report = json.load(f)
    except (json.JSONDecodeError, IOError):
        return "（健康報告讀取失敗）"

    lines = ["📊 指標健康狀態:"]

    # Sort by red_count descending
    indicators = sorted(report.get("indicators", []),
                       key=lambda x: x.get("red_count", 0), reverse=True)

    for ind in indicators:
        name = ind["indicator"]
        coin = ind["coin"]
        tf = ind["timeframe"]
        direction = "多" if ind["direction"] == "LONG" else "空"
        red_count = ind.get("red_count", 0)

        if red_count == 0:
            lines.append(f"  ✅ {name}（{coin} {tf} {direction}）— 健康")
        else:
            red_lights = ind.get("red_lights", [])
            lights_str = ", ".join(red_lights) if red_lights else "無燈號"
            lines.append(f"  🔴 {name}（{coin} {tf} {direction}）— {red_count}盞紅燈: {lights_str}")

            # Add regime detail for LIGHT_1
            if ind.get("light_1_regime_mismatch"):
                primary = ind.get("primary_regimes", [])
                current = ind.get("current_regime", "?")
                lines.append(f"     → Regime mismatch: 當前={current}, 驗證範圍={primary}")

            # Add accuracy detail for LIGHT_2
            if ind.get("light_2_accuracy_drop"):
                wr = ind.get("live_wr_30d", "?")
                lines.append(f"     → Live WR(30d)={wr}%")

        # Add severity if warning+
        severity = ind.get("severity", "")
        if red_count >= 2 and severity == "HIGH":
            lines.append(f"     ⚠️ HIGH severity — 建議暫停交易")

    # Alert summary
    alerts = report.get("alerts", [])
    if alerts:
        lines.append(f"\n🚨 活躍警報: {len(alerts)} 個")

    # Regime overview
    regime = report.get("regime_summary", {})
    bear_count = sum(1 for v in regime.values() if v.get("condition") == "bear")
    total = len(regime)
    lines.append(f"\n🌍 市場狀態: {bear_count}/{total} 幣種/時間框架處於熊市")

    return "\n".join(lines)


if __name__ == "__main__":
    print(get_indicator_health())
