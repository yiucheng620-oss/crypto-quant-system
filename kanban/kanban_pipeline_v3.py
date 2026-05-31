#!/usr/bin/env python3
"""
🎯 Kanban Stock Pipeline V3 — Direct Parallel Gemini（唔經 hermes kanban tasks）
══════════════════════════════════════════════════════════════
V2 → V3 升級：
  - 唔再用 hermes kanban create/wait → 直接用 call_pro() 並行 3 個 analyst
  - 10 秒完成（vs V2 4-25 分鐘）
  - 零 external dependency（唔使 kanban worker）
  - 同一套 analyst profiles + synthesizer prompt

Pipeline:
  T1 (macro-analyst)    ─┐
  T2 (orderflow-analyst) ─┤  ThreadPoolExecutor parallel → ~3s
  T3 (onchain-analyst)   ─┘
  T4 (synthesizer) ← sequential → ~5s
  Total: ~8-10 秒

Usage:
  python3 kanban_pipeline_v3.py          # Full pipeline
  python3 kanban_pipeline_v3.py --dry    # Print prompts only
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENGINES_DIR = os.path.join(WORKSPACE, "scalp_engines")
LOG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
SETUPS_FILE = os.path.join(WORKSPACE, "equities_data", "equities_setups.json")
PLANS_FILE = os.path.join(LOG_DIR, "kanban_trade_plans.json")
os.makedirs(LOG_DIR, exist_ok=True)

sys.path.insert(0, ENGINES_DIR)
# Pipeline analysts use 3.5 Flash (cheap, 10K RPD) — only Synthesizer uses 3.1 Pro
from gemini_free import call_pro, call_flash


# ═══════════════════════════════════════════════════════════════
# 1. DATA LOADER
# ═══════════════════════════════════════════════════════════════

def load_setups():
    """Load equities setups from engine output."""
    if not os.path.exists(SETUPS_FILE):
        print(f"❌ {SETUPS_FILE} not found")
        return None
    try:
        with open(SETUPS_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"❌ Failed to load setups: {e}")
        return None

    macro = raw.get("macro", {})
    sectors = raw.get("sectors", [])
    setups = raw.get("setups", [])
    earnings = raw.get("earnings", [])

    if not setups:
        print("📋 No setups to analyze")
        return None

    # Sort by RS, top 15
    setups_sorted = sorted(setups, key=lambda s: s.get("rs_vs_spy", 0), reverse=True)[:15]

    summary = {
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


# ═══════════════════════════════════════════════════════════════
# 2. ANALYST PROMPTS（同 V2 一樣，只改 output instruction）
# ═══════════════════════════════════════════════════════════════

PROMPT_MACRO = """你係宏觀分析師。分析以下美股 screener 結果嘅 macro context。

## 數據
```json
{data}
```

## 任務
1. 判斷目前 macro regime：VIX 水平、SPY/QQQ 趨勢、macro bias 係咪 favorable for stock picking
2. 邊個 sector 有資金流入？邊個流出？
3. 有冇 earnings risk 需要注意？
4. 最終俾一個 macro score（0-10）同建議：積極選股 / 保守 / 避險

## 輸出格式
**只輸出以下 JSON，唔要任何其他文字：**
```json
{{
  "macro_score": <0-10>,
  "recommendation": "積極選股|保守|避險",
  "vix_assessment": "<一句話>",
  "sector_rotation": "<資金流入流出分析>",
  "risk_notes": ["<風險1>", "<風險2>", ...]
}}
```"""

PROMPT_TECHNICAL = """你係技術分析師。驗證以下 screener 揀出嚟嘅美股 setups。

## 數據
```json
{data}
```

## 任務
對每個 setup：
1. 確認 signal type (pullback/squeeze_breakout) 係咪合理
2. 驗證 entry/stop/target 價位：stop 係咪太窄？R:R > 1:2？
3. 如果同 sector 有多過一隻，揀最好嗰隻
4. 俾每隻一個技術評分（0-10）

## 規則
- 最多推薦 5 隻
- 同 sector 只推 1 隻（揀最高 RS + 最好 setup 嘅）
- R:R < 1:2 嘅直接 skip
- RS_vs_SPY < 20 嘅降級

## 輸出格式
**只輸出以下 JSON，唔要任何其他文字：**
```json
{{
  "approved": [
    {{"ticker": "XXX", "score": 8, "reason": "一句話原因"}},
    ...
  ],
  "watchlist": [
    {{"ticker": "XXX", "score": 5, "reason": "條件差少少"}},
    ...
  ],
  "excluded": [
    {{"ticker": "XXX", "reason": "R:R太低/RS太弱"}},
    ...
  ]
}}
```"""

PROMPT_RISK = """你係風險分析師。針對以下美股 setups 做風險評估。

## 交易者條件
- 總資金: $10,000 USD (Capital.com demo equities account)
- 槓桿: 1:1（現貨）
- 最多持倉: 2 隻
- 每 sector 最多 1 隻
- 每注 max risk: $200（2% of capital）

## 數據
```json
{data}
```

## 任務
1. 計每隻建議倉位（股數），用 entry-stop distance 計 risk
2. Check correlation：同 sector 嘅股票揀一隻（避免集中風險）
3. 每注 max loss 唔可以超過 $200
4. Portfolio level：如果揀 2 隻，total risk 幾多？

## 輸出格式
**只輸出以下 JSON，唔要任何其他文字：**
```json
{{
  "positions": [
    {{"ticker": "XXX", "size_shares": 41, "risk_amount": 150, "max_loss_pct": 1.5}},
    ...
  ],
  "portfolio_risk": {{
    "total_value": 4930,
    "total_pct": 49.3,
    "total_max_loss": 297,
    "remaining_capital": 5070
  }},
  "warnings": ["集中風險警告", "..."]
}}
```"""

PROMPT_SYNTHESIZER = """你係首席策略師。整合以下 3 位分析師嘅報告，出最終選股建議。

## 分析報告

### 宏觀分析
```json
{macro_report}
```

### 技術驗證
```json
{tech_report}
```

### 風險評估
```json
{risk_report}
```

## 原始數據
```json
{raw_data}
```

## 任務
整合 3 份報告，輸出最終交易計劃：
1. 🎯 Top 2-3 推薦（排名分先後）
2. 每隻有完整 trade plan（入場/止損/止贏/倉位/R:R/原因）
3. 必須 cross-reference：技術推薦 + 風險可接受 + 宏觀 favorable
4. ⚠️ 風險提醒

## 規則
- 最多推薦 3 隻
- 同 sector 只推 1 隻
- R:R < 1:2 唔推
- 冇信心嘅寧願唔推

## 輸出格式
**只輸出以下 JSON（single line or multi-line，但一定要 valid），唔要 markdown，唔要解釋：**
```json
{{
  "timestamp": "{timestamp}",
  "macro_bias": "一句話 macro bias 總結",
  "trades": [
    {{
      "symbol": "INTC",
      "direction": "LONG",
      "entry": 119.84,
      "stop": 116.24,
      "target": 149.02,
      "size_shares": 41,
      "confidence": "5/5",
      "reason": "一句話原因"
    }}
  ],
  "risk_warnings": "組合風險提醒"
}}
```"""


# ═══════════════════════════════════════════════════════════════
# 3. PARALLEL GEMINI CALLS
# ═══════════════════════════════════════════════════════════════

def call_analyst(name: str, prompt: str) -> str:
    """Call Gemini Pro for one analyst. Returns raw text."""
    print(f"  🧠 {name} analyzing...")
    t0 = time.time()
    try:
        result = call_pro(prompt, max_tokens=8192, temperature=0.5)
        elapsed = time.time() - t0
        if result:
            print(f"  ✅ {name} done ({elapsed:.1f}s, {len(result)} chars)")
        else:
            print(f"  ❌ {name} returned empty")
        return result or ""
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ❌ {name} error ({elapsed:.1f}s): {e}")
        return ""


def extract_json(text: str) -> dict:
    """Extract JSON object from Gemini response."""
    if not text:
        return {}

    # Strategy 1: Direct parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract from code block
    match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find outermost braces
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Fallback: return raw text as error
    return {"_raw": text[:500], "_error": "JSON parse failed"}


# ═══════════════════════════════════════════════════════════════
# 4. MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════

def main(dry_run=False):
    now = datetime.now(HKT)
    ts = now.strftime("%Y-%m-%d %H:%M HKT")
    print(f"🎯 Kanban Stock Pipeline V3 — {ts}")
    print(f"   Direct parallel Gemini (no kanban worker dependency)\n")

    # Load data
    data = load_setups()
    if not data:
        print("❌ No data. Abort.")
        return

    # Prepare prompt data
    macro_part = json.dumps({"macro": data["macro"], "sectors": data["sectors"], "earnings_risk": data["earnings_risk"]}, ensure_ascii=False, indent=2)
    setups_part = json.dumps({"setups": data["setups"], "sectors": data["sectors"]}, ensure_ascii=False, indent=2)
    full_data = json.dumps(data, ensure_ascii=False)

    # Build prompts
    prompt_macro = PROMPT_MACRO.format(data=macro_part)
    prompt_tech = PROMPT_TECHNICAL.format(data=setups_part)
    prompt_risk = PROMPT_RISK.format(data=full_data)

    if dry_run:
        print("=" * 60)
        print("MACRO PROMPT:")
        print(prompt_macro[:500])
        print("\n" + "=" * 60)
        print("TECHNICAL PROMPT:")
        print(prompt_tech[:500])
        print("\n" + "=" * 60)
        print("RISK PROMPT:")
        print(prompt_risk[:500])
        return

    # ── Phase 1: Parallel analysis (ThreadPoolExecutor, no 429 concern at 3 req) ──
    print("Phase 1/2: Parallel analyst calls (3 concurrent via ThreadPoolExecutor)")
    t_start = time.time()

    results = {}
    futures = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures["macro"] = executor.submit(call_analyst, "Macro", prompt_macro)
        futures["technical"] = executor.submit(call_analyst, "Technical", prompt_tech)
        futures["risk"] = executor.submit(call_analyst, "Risk", prompt_risk)
        for name, future in futures.items():
            results[name] = future.result(timeout=90)

    # Parse outputs
    macro_json = extract_json(results.get("macro", ""))
    tech_json = extract_json(results.get("technical", ""))
    risk_json = extract_json(results.get("risk", ""))

    t1 = time.time() - t_start
    print(f"\nPhase 1 done: {t1:.1f}s\n")

    # ── Phase 2: Synthesizer ──
    print("Phase 2/2: Synthesizer")
    t2_start = time.time()

    # Use parsed JSON for synthesizer (more compact than raw text)
    synth_prompt = PROMPT_SYNTHESIZER.format(
        macro_report=json.dumps(macro_json, ensure_ascii=False, indent=2),
        tech_report=json.dumps(tech_json, ensure_ascii=False, indent=2),
        risk_report=json.dumps(risk_json, ensure_ascii=False, indent=2),
        raw_data=json.dumps(data["setups"][:5], ensure_ascii=False),
        timestamp=now.isoformat(),
    )

    synth_result = call_analyst("Synthesizer", synth_prompt)
    plan = extract_json(synth_result)

    t2 = time.time() - t2_start
    total = time.time() - t_start
    print(f"\nPhase 2 done: {t2:.1f}s | Total: {total:.1f}s\n")

    # ── Save ──
    if plan and "trades" in plan:
        # Always override timestamp (Gemini may return stale/wrong timestamp)
        plan["timestamp"] = now.isoformat()
        if "macro_bias" not in plan:
            plan["macro_bias"] = macro_json.get("recommendation", data["macro"].get("macro_bias", "neutral"))

        os.makedirs(os.path.dirname(PLANS_FILE), exist_ok=True)
        tmp = PLANS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
        os.replace(tmp, PLANS_FILE)

        print(f"{'='*60}")
        print("📋 TRADE PLANS")
        print(f"{'='*60}")
        print(f"Macro: {plan.get('macro_bias', '?')[:80]}")
        for t in plan.get("trades", []):
            print(f"  {'⭐' + t.get('confidence', '?')[0] if t.get('confidence') else '⭐'} ${t['symbol']} {t.get('direction','LONG')} | Entry=${t['entry']} | Stop=${t['stop']} | Target=${t['target']} | R:R={((t['target']-t['entry'])/(t['entry']-t['stop'])):.1f}")
        if plan.get("risk_warnings"):
            print(f"\n⚠️ {plan['risk_warnings'][:200]}")
        print(f"\n✅ Plans saved: {PLANS_FILE}")
    else:
        print("❌ Failed to extract valid trade plan from synthesizer output")
        print(f"   Raw output preview: {synth_result[:500]}")

    print(f"\n⏱️ Total pipeline time: {total:.1f}s")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    main(dry_run=dry)
