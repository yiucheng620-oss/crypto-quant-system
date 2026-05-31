#!/usr/bin/env python3
"""Save daily Smart Money snapshot to history for future backtesting."""
import json, os, sys
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
HISTORY_DIR = os.path.expanduser("~/workspace/data/smart_money/history")
LATEST = os.path.expanduser("~/workspace/data/smart_money/smart_money_latest.json")

os.makedirs(HISTORY_DIR, exist_ok=True)

if not os.path.exists(LATEST):
    print("No latest smart money data")
    sys.exit(0)

with open(LATEST) as f:
    data = json.load(f)

date_str = datetime.now(HKT).strftime("%Y-%m-%d")
# Save with timestamp for intraday tracking
ts_str = datetime.now(HKT).strftime("%Y-%m-%d_%H%M")
out_file = os.path.join(HISTORY_DIR, f"sm_{ts_str}.json")

with open(out_file, "w") as f:
    json.dump(data, f, indent=2)

print(f"Saved: {out_file}")

# Keep only last 90 days
all_files = sorted([f for f in os.listdir(HISTORY_DIR) if f.startswith("sm_")])
if len(all_files) > 90:
    for old in all_files[:-90]:
        os.remove(os.path.join(HISTORY_DIR, old))
        print(f"  Purged: {old}")
