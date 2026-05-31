#!/usr/bin/env python3
"""
Indicator Health Report Generator
══════════════════════════════════════
Runs health check + generates Telegram-ready report using Gemini flash.

Cron: every 4h (no_agent, deliver: origin)
Silent if 0 alerts. Delivers report only when actionable.

Usage:
  python3 health_report.py        # Full check + Gemini report if alerts
  python3 health_report.py --json # JSON output only (no Gemini)
"""

import os, sys, json, subprocess, requests
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
HEALTH_DIR = os.path.join(WORKSPACE, "data", "health_reports")
HEALTH_JSON = os.path.join(HEALTH_DIR, "health_latest.json")
VERTEX_URL = "http://localhost:8899/v1/chat/completions"

def run_health_check():
    """Run indicator_health_monitor.py and return the JSON path."""
    result = subprocess.run(
        [os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python3"),
         os.path.join(WORKSPACE, "monitoring", "indicator_health_monitor.py"), "--json"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "HOME": os.path.expanduser("~")},
        cwd=WORKSPACE
    )
    # Parse output for report path
    for line in result.stdout.split("\n"):
        if "Report:" in line:
            return line.split("Report:")[-1].strip()
    # Fallback
    candidates = sorted(
        [f for f in os.listdir(HEALTH_DIR) if f.startswith("health_") and f.endswith(".json")],
        reverse=True
    )
    return os.path.join(HEALTH_DIR, candidates[0]) if candidates else None

def load_health_data(path):
    """Load and parse health check JSON."""
    if not path or not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)

def analyze_health(data):
    """Quick rule-based analysis before Gemini call."""
    indicators = data.get("indicators", [])
    
    healthy = data.get("healthy_count", len(indicators))
    warning = data.get("warning_count", 0)
    critical = data.get("critical_count", 0)
    alert_count = data.get("alert_count", 0)
    alerts = data.get("alerts", [])
    
    # Find alerted indicators from the indicator list
    alert_indicators = [i for i in indicators if i.get("alert_triggered")]
    
    # Determine if reportable  
    should_report = critical > 0 or alert_count > 0
    
    return {
        "total": len(indicators),
        "healthy": healthy,
        "warning": warning,
        "critical": critical,
        "alert_count": len(alert_indicators),
        "alerts": alert_indicators,
        "should_report": should_report,
        "timestamp": data.get("timestamp", ""),
    }

def call_gemini(analysis, data):
    """Generate Telegram report via Gemini flash."""
    now = datetime.now(HKT).strftime("%m/%d %H:%M HKT")
    
    # Build concise prompt
    alert_summary = []
    for a in analysis["alerts"][:8]:
        ind = a.get("indicator", a.get("name", "?"))
        red_count = a.get("red_count", 0)
        red_lights = a.get("red_lights", [])
        light_str = ", ".join(red_lights) if red_lights else f"{red_count} red lights"
        alert_summary.append(f"  {ind}: {light_str}")
    
    prompt = f"""你係系統健康監察員。請用繁體中文寫一份簡潔 Telegram 報告（<500字）。

**時間**: {now}
**總指標**: {analysis['total']} | 🟢 Healthy: {analysis['healthy']} | 🟡 Warning: {analysis['warning']} | 🔴 Critical: {analysis['critical']}

**警報指標**:
{chr(10).join(alert_summary) if alert_summary else '（無警報）'}

**原始數據** (節錄):
{json.dumps(data.get('indicators', [])[:8], indent=2, ensure_ascii=False)[:2000]}

**規則**:
- 如有 critical → 明確寫「需要立即檢查」
- Warning 要按嚴重度分：regime mismatch（正常）/ accuracy drop（關注）
- 如果全部係 regime mismatch 類 warning → 結尾加 [FYI — 熊市正常]
- 唔好超過 500 字
- 用 bullet point
- 唔好講廢話"""

    try:
        payload = {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 8192,
            "temperature": 0.7,
        }
        r = requests.post(VERTEX_URL, json=payload, timeout=45)
        r.raise_for_status()
        content = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        return content if content else None
    except Exception as e:
        return f"⚠️ Gemini unavailable: {str(e)[:100]}\n\nRule-based summary:\n  Healthy: {analysis['healthy']} | Warning: {analysis['warning']} | Critical: {analysis['critical']}"

def main():
    json_path = run_health_check()
    data = load_health_data(json_path)
    
    if not data:
        print("❌ Health check failed — no data")
        sys.exit(1)
    
    analysis = analyze_health(data)
    
    # JSON mode
    if "--json" in sys.argv:
        print(json.dumps(analysis, indent=2, default=str))
        return
    
    # Silent if no alerts (empty stdout = silent delivery)
    if not analysis["should_report"]:
        return
    
    # Generate report
    report = call_gemini(analysis, data)
    if report:
        header = f"🩺 **指標健康報告** — {datetime.now(HKT).strftime('%m/%d %H:%M HKT')}\n"
        footer = f"\n_📊 {analysis['total']} indicators | 🤖 Gemini 2.5 Flash_"
        print(header + report + footer)

if __name__ == "__main__":
    main()
