#!/usr/bin/env python3
"""
Kanban Pipeline Trigger — 觸發 Health Monitor 4-stage pipeline
═══════════════════════════════════════════════════════════
Creates 4 linked Kanban tasks with proper dependency chain:
  T1 (collector) → T2 (analyzer) → T3 (reviewer) → T4 (reporter)

Usage: python3 kanban_trigger.py health
       python3 kanban_trigger.py gemini
═══════════════════════════════════════════════════════════
"""
import subprocess, sys, os, json
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
HERMES_BIN = os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes")

def create_task(title, assignee, body, parents=None):
    """Create a Kanban task and return task_id."""
    cmd = [HERMES_BIN, "kanban", "create", title, "--assignee", assignee, "--body", body]
    if parents:
        if isinstance(parents, str):
            parents = [parents]
        for p in parents:
            cmd.extend(["--parent", p])
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    output = result.stdout.strip()
    
    # Parse: "Created t_xxxxx  (ready, assignee=...)" 
    if "Created " in output:
        task_id = output.split()[1]
        print(f"  ✅ {task_id}: {title} → {assignee}")
        return task_id
    else:
        print(f"  ❌ Failed: {output}")
        if result.stderr:
            print(f"     stderr: {result.stderr}")
        return None

def pipeline_health():
    """Create Health Monitor 4-stage pipeline."""
    now = datetime.now(HKT).strftime("%m/%d %H:%M HKT")
    print(f"\n🩺 Health Pipeline Trigger — {now}")
    
    # T1: Collector — runs indicator_health_monitor.py
    t1 = create_task(
        title=f"🩺 [Collector] Run health check {now}",
        assignee="health-collector",
        body="""export HOME=/home/yiucheng620
cd ~/workspace && ~/.hermes/hermes-agent/venv/bin/python3 indicator_health_monitor.py --json

⚠️ IMPORTANT: After running, write a COMMENT with the path to health_latest.json and summary stats (healthy/warning/critical counts)."""
    )
    if not t1: return
    
    # T2: Analyzer — deep analysis of health data (depends on T1)
    t2 = create_task(
        title=f"🩺 [Analyzer] Deep analyze health data",
        assignee="health-analyzer",
        parents=[t1],
        body=f"""Read ~/workspace/health_reports/health_latest.json

Using vertex-proxy with gemini-2.5-flash, analyze each indicator's health:

For indicators with 1+ red lights:
1. WHY is this light red?
2. Is it actionable? (sufficient sample? regime shift?)  
3. Risk level: 立即暫停 / 觀察中 / 可忽略

Key rules:
- LIGHT_1 in bear market for OBV type indicators is EXPECTED (they're validated for bull/range only). NOT a crisis.
- LIGHT_2 meaningful only when settled_30d >= 5.
- LIGHT_3 requires 2 consecutive windows of decline.

Write your analysis as a COMMENT on this task."""
    )
    if not t2: return
    
    # T3: Reviewer — quality gate (depends on T2)
    t3 = create_task(
        title=f"🩺 [Reviewer] Quality gate — review analysis",
        assignee="health-reviewer",
        parents=[t2],
        body=f"""Read the analyzer's COMMENT on task {t2}.

Then review against the RAW JSON at ~/workspace/health_reports/health_latest.json:

1. Are all numbers directly from the JSON? Flag hallucinations.
2. Is severity rating consistent with 3-light rule?
3. Any false positives? (e.g., OBV bear regime mismatch is NORMAL)

Verdict: APPROVED or NEEDS_REVISION

Write your verdict as a COMMENT. If APPROVED, include a final summary suitable for Telegram.
Use vertex-proxy with gemini-2.5-flash."""
    )
    if not t3: return
    
    # T4: Reporter — format and send (depends on T3)
    t4 = create_task(
        title=f"🩺 [Reporter] Send health report",
        assignee="health-reporter",
        parents=[t3],
        body=f"""Read the reviewer's COMMENT on task {t3}.

If verdict is APPROVED:
- Format the summary into a Telegram-friendly report (Traditional Chinese, under 1500 chars)
- If 0 alerts: output "[SILENT]"
- If only normal regime mismatches: mention as FYI, not alert
- DO NOT report suppressed lights as issues

If verdict is NEEDS_REVISION:
- Output "[BLOCKED] Reviewer found issues"

Your final output will be delivered to the user."""
    )
    
    print(f"\n✅ Pipeline created: {t1} → {t2} → {t3} → {t4}")
    print(f"   Dispatcher will process in order")

if __name__ == "__main__":
    pipeline = sys.argv[1] if len(sys.argv) > 1 else "health"
    if pipeline == "health":
        pipeline_health()
    else:
        print(f"Usage: {sys.argv[0]} health")
        sys.exit(1)
