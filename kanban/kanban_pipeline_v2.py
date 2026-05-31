#!/usr/bin/env python3
"""
🎯 Kanban Stock Pipeline V2 — 真正 Kanban 4-stage 選股流程
══════════════════════════════════════════════════════════
Replaces old kanban_stocks.py (inline Gemini calls).
Uses actual kanban profiles for parallel analysis.

Pipeline:
  T1 (macro-analyst)    ─┐
  T2 (orderflow-analyst) ─┤  parallel
  T3 (onchain-analyst)   ─┘
  T4 (synthesizer) ← depends on T1+T2+T3

Input:  equities_setups.json (from equities_engine.py, auto-refreshed)
Output: kanban_trade_plans.json + TG report

Usage:
  python3 kanban_pipeline_v2.py          # Full pipeline
  python3 kanban_pipeline_v2.py --dry    # Print task bodies only, don't create
"""

import subprocess
import sys
import os
import json
import time
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
HERMES_BIN = os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes")
WORKSPACE = os.path.expanduser("~/workspace")
SETUPS_FILE = os.path.join(WORKSPACE, "equities_data", "equities_setups.json")
PLANS_FILE = os.path.join(WORKSPACE, "scalp_lab", "engines", "kanban_trade_plans.json")


def load_setups():
    """Load equities_setups.json, return summary for task bodies."""
    if not os.path.exists(SETUPS_FILE):
        print(f"❌ {SETUPS_FILE} not found. Run equities_engine first.")
        return None

    with open(SETUPS_FILE) as f:
        data = json.load(f)
    
    age_min = (time.time() - os.path.getmtime(SETUPS_FILE)) / 60
    if age_min > 120:
        print(f"⚠️ Data is {age_min:.0f} min old (stale). Consider re-running screener.")
    
    # Build compact summary for task bodies
    macro = data.get("macro", {})
    setups = data.get("setups", [])
    sectors = data.get("sectors", [])
    earnings = data.get("earnings_risk", [])
    
    # Limit setups to top 10 by RS for the analysts
    setups_sorted = sorted(setups, key=lambda s: s.get("rs_vs_spy", 0), reverse=True)[:10]
    
    summary = {
        "timestamp": data.get("timestamp", "unknown"),
        "macro": {
            "spy": macro.get("spy_price", "?"),
            "qqq": macro.get("qqq_price", "?"),
            "spy_trend": macro.get("spy_trend", "?"),
            "qqq_trend": macro.get("qqq_trend", "?"),
            "vix": macro.get("vix", "?"),
            "vix_regime": macro.get("vix_regime", "?"),
            "macro_bias": macro.get("macro_bias", "?"),
        },
        "sectors": [{"name": s["name"], "rs_vs_spy": s["rs_vs_spy"], "trend": s["trend"]} for s in sectors],
        "setups": [{
            "ticker": s["ticker"],
            "signal": s["signal"],
            "entry": s["entry"],
            "stop": s["stop"],
            "target": s["target"],
            "rr": s.get("rr", 0),
            "rs_vs_spy": s.get("rs_vs_spy", 0),
            "confidence": s.get("confidence", "?"),
        } for s in setups_sorted],
        "earnings_risk": earnings[:10] if earnings else [],
    }
    
    print(f"📊 Loaded {len(setups)} setups, top {len(setups_sorted)} by RS")
    print(f"   VIX={macro.get('vix','?')}, SPY={macro.get('spy_price','?')}, bias={macro.get('macro_bias','?')}")
    return summary


def create_task(title, assignee, body, parents=None):
    """Create a Kanban task and return task_id."""
    cmd = [HERMES_BIN, "kanban", "create", title, "--assignee", assignee, "--body", body]
    if parents:
        for p in (parents if isinstance(parents, list) else [parents]):
            cmd.extend(["--parent", p])
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    output = result.stdout.strip()
    
    # Parse task ID from output: "Created t_xxxxx  (ready, ...)"
    for line in output.split("\n"):
        if line.startswith("Created t_"):
            task_id = line.split()[1]
            print(f"  ✅ {task_id} ({assignee})")
            return task_id
    
    print(f"  ❌ Failed to create task: {output[:200]}")
    return None


def wait_for_task(task_id, timeout_min=20):
    """Poll until task is done or timeout."""
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        result = subprocess.run(
            [HERMES_BIN, "kanban", "show", task_id],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout
        if "status:" in output.lower() and "done" in output.lower().split("status:")[-1][:20]:
            print(f"  ✅ {task_id} done")
            return True
        if "status:" in output.lower() and "blocked" in output.lower().split("status:")[-1][:20]:
            print(f"  ⚠️ {task_id} blocked — needs manual unblock")
            return False
        print(f"  ... waiting (polling every 30s)")
        time.sleep(30)
    
    print(f"  ⏰ {task_id} timeout after {timeout_min}min")
    return False


def get_task_handoff(task_id):
    """Read a task's summary from kanban show (NOT log — log is agent session, no summary)."""
    result = subprocess.run(
        [HERMES_BIN, "kanban", "show", task_id],
        capture_output=True, text=True, timeout=15
    )
    output = result.stdout
    # Extract only the "Latest summary:" portion + everything after
    summary_idx = output.find("Latest summary:")
    if summary_idx >= 0:
        return output[summary_idx:]
    # Fallback: last 3000 chars
    return output[-3000:] if len(output) > 3000 else output


# ═══════════════════════════════════════════════════════════
#  TASK BODIES
# ═══════════════════════════════════════════════════════════

def body_macro(data_json: str) -> str:
    return f"""你係宏觀分析師（macro-analyst）。分析以下美股 screener 結果嘅 macro context。

## 數據
```json
{data_json}
```

## 任務
1. 判斷目前 macro regime：VIX 水平、SPY/QQQ 趨勢、macro bias 係咪 favorable for stock picking
2. 邊個 sector 有資金流入？邊個流出？
3. 有冇 earnings risk 需要注意？
4. 最終俾一個 macro score（0-10）同建議：積極選股 / 保守 / 避險

## 輸出格式
用 kanban_complete() 完成任務。summary 用繁體中文，格式如下：

```
## 宏觀評估
VIX: X.X (regime) | SPY: $XXX (trend) | QQQ: $XXX (trend)
Macro Score: X/10
建議: [積極選股 / 保守 / 避險]

### Sector Rotation
• sector_name: RS=X%, trend
...

### 風險提示
• ...
```

請真係執行分析，唔好敷衍。用桌面電腦可以 access file，但你只需要分析上面 JSON 嘅數據。"""


def body_technical(data_json: str) -> str:
    return f"""你係技術分析師。驗證以下 screener 揀出嚟嘅美股 setups。

## 數據
```json
{data_json}
```

## 任務
對每個 setup：
1. 確認 signal type (pullback/squeeze_breakout) 係咪合理
2. 驗證 entry/stop/target 價位：stop 係咪太窄（<1% ATR）？R:R > 1:2？
3. 如果同 sector 有多過一隻，揀最好嗰隻
4. 俾每隻一個技術評分（0-10）

## 規則
- 最多推薦 5 隻
- 同 sector 只推 1 隻（揀最高 RS + 最好 setup 嘅）
- R:R < 1:2 嘅直接 skip
- RS_vs_SPY < 20 嘅降級

## 輸出格式
用 kanban_complete()。summary 用繁體中文：

```
## 技術驗證
### ✅ 推薦
⭐X $TICKER — signal: X | entry=$X | stop=$X | target=$X | R:R=X | RS=X
評分: X/10 | 原因: ...

### ⚠️ 觀察
...（條件差少少但值得留意）

### ❌ 排除
...（R:R 太低 / RS 太弱）
```"""


def body_risk(data_json: str) -> str:
    return f"""你係風險分析師。針對以下美股 setups 做風險評估。

## 交易者條件
- 總資金: $10,000 USD (Capital.com demo equities account)
- 槓桿: 1:1（現貨）
- 最多持倉: 2 隻
- 每 sector 最多 1 隻
- 每注 max risk: $200（2% of capital）

## 數據
```json
{data_json}
```

## 任務
1. 計每隻建議倉位（股數），用 entry-stop distance 計 risk
2. Check correlation：同 sector 嘅股票揀一隻（避免集中風險）
3. 每注 max loss 唔可以超過 $200
4. Portfolio level：如果揀 2 隻，total risk 幾多？

## 輸出格式
用 kanban_complete()。summary 用繁體中文：

```
## 風險評估
### 建議倉位
$TICKER: entry=$X | stop=$X | risk/股=$X | 建議股數=X | max loss=$X
...

### Portfolio Risk
總倉位: $X,XXX (XX% of capital)
總 max loss: $XXX (X.X%)
剩餘 capital: $X,XXX

### ⚠️ 風險警告
• ...
```"""


def body_synthesizer(macro_json: str, tech_json: str, risk_json: str, data_json: str) -> str:
    return f"""你係首席策略師（synthesizer）。整合以下 3 位分析師嘅報告，出最終選股建議。

## 分析報告
### 宏觀分析
```json
{macro_json}
```

### 技術驗證
```json
{tech_json}
```

### 風險評估
```json
{risk_json}
```

## 原始數據
```json
{data_json}
```

## 任務
整合 3 份報告，輸出最終交易計劃：
1. 🎯 Top 3 推薦（排名分先後）
2. 每隻有完整 trade plan（入場/止損/止贏/倉位/R:R/原因）
3. 必須 cross-reference：技術推薦 + 風險可接受 + 宏觀 favorable
4. ⚠️ 風險提醒
5. 📋 Watchlist（值得觀察但未到入場點）

## 規則
- 最多推薦 3 隻
- 同 sector 只推 1 隻
- R:R < 1:2 唔推
- 冇信心嘅寧願唔推
- 每隻必須有 3 點推薦原因（技術面/基本面/風險面）

## 輸出（極重要！必須跟格式）
用 kanban_complete() 完成。

**summary 必須分兩部分，用 ===JSON=== 分隔：**

第一部分：繁體中文分析（Telegram format）
第二部分：===JSON=== 之後緊接 valid JSON（唔要 markdown code block）

格式範例：
```
🎯 Top 3 推薦

⭐5 $INTC — $119.84
入場: $119.84 | 止損: $116.24 | 止贏: $149.02 | R:R: 8.12 | 倉位: 41股 (~$4,913)
🔥 推薦原因:
• 技術面: squeeze breakout, RS 171.7全市場最強
• 基本面: 半導體板塊強勢，foundry turnaround catalyst
• 風險面: max loss $147.60 (1.48% of capital)

⚠️ 風險提醒
• ...

📋 Watchlist
• ...

===JSON===
{"timestamp":"2026-05-26T14:00:00+08:00","trades":[{"symbol":"INTC","direction":"LONG","entry":119.84,"stop":116.24,"target":149.02,"size_shares":41,"confidence":"5/5","reason":"squeeze breakout RS=171.7"}],"macro_bias":"積極做多","risk_warnings":"..."}
```

**⚠️ JSON 必須係 single line 或者 multi-line 但一定要 valid。唔可以用 markdown code block 包住。**"""


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main(dry_run=False):
    now = datetime.now(HKT)
    print(f"🎯 Kanban Stock Pipeline V2 — {now.strftime('%Y-%m-%d %H:%M HKT')}\n")
    
    # Load data
    data = load_setups()
    if not data:
        print("❌ No data. Abort.")
        return
    
    # Compact JSON for task bodies (keep it under token limits)
    macro_part = json.dumps({"macro": data["macro"], "sectors": data["sectors"], "earnings_risk": data["earnings_risk"]}, ensure_ascii=False, indent=2)
    setups_part = json.dumps({"setups": data["setups"], "sectors": data["sectors"]}, ensure_ascii=False, indent=2)
    full_data = json.dumps(data, ensure_ascii=False, indent=2)
    
    # Task bodies
    t1_body = body_macro(macro_part)
    t2_body = body_technical(setups_part)
    t3_body = body_risk(setups_part)
    
    if dry_run:
        print("=" * 60)
        print("DRY RUN — task bodies preview")
        print("=" * 60)
        print(f"\n--- T1 (macro-analyst) ---\n{t1_body[:500]}...\n")
        print(f"\n--- T2 (orderflow-analyst) ---\n{t2_body[:500]}...\n")
        print(f"\n--- T3 (onchain-analyst) ---\n{t3_body[:500]}...\n")
        return
    
    # ═══ Create tasks ═══
    print("📋 Creating Kanban tasks...\n")
    
    t1_id = create_task("🩺 [Macro] 市場環境分析", "macro-analyst", t1_body)
    t2_id = create_task("📈 [Technical] 技術形態驗證", "orderflow-analyst", t2_body)
    t3_id = create_task("⚖️ [Risk] 風險與倉位評估", "onchain-analyst", t3_body)
    
    if not all([t1_id, t2_id, t3_id]):
        print("❌ Failed to create tasks. Abort.")
        return
    
    # Synthesizer depends on all 3
    t4_body = body_synthesizer(
        json.dumps(data["macro"], ensure_ascii=False),
        json.dumps(data["setups"][:5], ensure_ascii=False),
        "pending — see parent tasks",
        full_data
    )
    t4_id = create_task("🧠 [Synthesizer] 最終選股推薦", "synthesizer", t4_body, parents=[t1_id, t2_id, t3_id])
    
    if not t4_id:
        print("❌ Failed to create synthesizer task. Abort.")
        return
    
    print(f"\n⏳ Pipeline created: T1={t1_id}, T2={t2_id}, T3={t3_id}, T4={t4_id}")
    print(f"   Dispatcher will process T1-T3 in parallel, then T4")
    
    # ═══ Wait for pipeline ═══
    print(f"\n⏰ Waiting for pipeline (max 25 min)...")
    print(f"   Check: hermes kanban list")
    
    ok = wait_for_task(t4_id, timeout_min=25)
    
    if ok:
        print(f"\n✅ Pipeline complete!")
        # Read synthesizer output
        log = get_task_handoff(t4_id)
        print(f"\n{'='*60}")
        print("📋 SYNTHESIZER OUTPUT")
        print(f"{'='*60}")
        # Print last 3000 chars (summary portion)
        tail = log[-3000:] if len(log) > 3000 else log
        print(tail)
        
        # Extract JSON from summary — search ONLY after "Latest summary:" to avoid
        # matching the example template in the task body
        import re
        json_match = re.search(r'---JSON---\s*\n?(.*?)(?:\n\nEvents|\n\s*\nEvents|\n\nRuns|\Z)', tail, re.DOTALL)
        if json_match:
            try:
                plan = json.loads(json_match.group(1).strip())
                os.makedirs(os.path.dirname(PLANS_FILE), exist_ok=True)
                tmp = PLANS_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(plan, f, indent=2, ensure_ascii=False)
                os.replace(tmp, PLANS_FILE)
                print(f"\n💾 Trade plans saved: {PLANS_FILE}")
                print(f"   {len(plan.get('trades', []))} trades extracted")
            except json.JSONDecodeError as e:
                print(f"\n⚠️ Failed to parse trade plan JSON: {e}")
        else:
            print(f"\n⚠️ No ---JSON--- block found in synthesizer output")
    else:
        print(f"\n⚠️ Pipeline incomplete — check kanban board")
        print(f"   T4={t4_id} status unknown")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    main(dry_run=dry)
