#!/usr/bin/env python3
"""
Gemini Weekly Analyzer — 每週 Accuracy 矩陣分析
═══════════════════════════════════════════════════════
Call Vertex AI Gemini 2.5 Pro via vertex-proxy 分析:
  1. 邊個 indicator 喺邊個市況最可靠？
  2. 生成 Accuracy Matrix (你之前講嘅)
  3. 判斷「今日個市點？應該用邊個 indicator？」
  4. Output: Telegram-friendly Markdown report

Run: python3 weekly_analyzer.py [--days 90]
"""

import os, sys, json, time, requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))
PROXY_URL = os.environ.get("VERTEX_PROXY_URL", "http://localhost:8899/v1")
REPORT_DIR = os.path.expanduser("~/workspace/accuracy_reports")

# ──── PROMPT BUILDER ────

def build_analysis_prompt(data: dict) -> str:
    """Build a structured prompt for Gemini analysis."""

    indicators = data.get("indicators", [])
    matrix = data.get("accuracy_matrix", {})
    market_state = data.get("current_market_state", {})

    # Format indicator stats
    ind_lines = []
    for ind in indicators:
        ind_lines.append(
            f"- {ind['name']} ({ind['coin']} {ind['tf']}): "
            f"{ind['total_signals']} signals, {ind['win_rate']}% WR, "
            f"avg_win={ind['avg_win_pct']}%, avg_loss={ind['avg_loss_pct']}%"
        )

    # Format matrix
    matrix_lines = []
    for ind_name, conditions in sorted(matrix.items()):
        for cond, stats in sorted(conditions.items()):
            matrix_lines.append(
                f"| {ind_name} | {cond} | {stats['total']} | {stats['win_rate']}% |"
            )

    # Format current state
    state_lines = []
    for key, state in sorted(market_state.items()):
        state_lines.append(
            f"- {key}: {state['condition']} (ADX={state['adx']}, Vol={state['volatility_pct']}%)"
        )

    prompt = f"""你係一個專業加密貨幣交易分析師。請用繁體中文分析以下 Signal Accuracy 數據，生成一份簡潔嘅報告。

## 📊 數據摘要
### 各 Indicator 總體表現（{data.get('period_days', '?')} 日）
{chr(10).join(ind_lines)}

### Accuracy Matrix (24h | indicator × market_condition)
| Indicator | Market | Signals | Win Rate |
|-----------|--------|---------|----------|
{chr(10).join(matrix_lines)}

### 當前市場狀態
{chr(10).join(state_lines)}

## 🎯 請回答以下問題：

1. **Accuracy Matrix 總結**：邊個 indicator 喺邊個市況最可靠？邊個完全唔得？用一個簡單嘅勝率矩陣表示。

2. **今日策略建議**（基於當前市場狀態）：
   - 今日應該用邊個 indicator？邊個要 ignore？
   - 估計綜合準確度有幾高？
   - 建議 trade / 唔 trade？點解？

3. **最重要發現**：呢堆數據入面，最重要嘅 insight 係咩？（限 3 句）

4. **Signal Quality Score**：俾每個 indicator 一個 1-10 分嘅評分（10=最可靠），解釋評分原因。

格式要求：
- 用繁體中文
- 簡潔直接，唔要廢話
- 適合 Telegram 閱讀（唔好用 table，用 bullet）
- 報告長度限 3000 字以內
"""

    return prompt


# ──── GEMINI CALL ────

def call_gemini(prompt: str, model: str = "gemini-2.5-pro") -> str:
    """Call Gemini via vertex-proxy."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你係加密貨幣量化分析師。用繁體中文回答，簡潔精準。冇廢話。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 8192,
        "temperature": 0.7,  # Creative analysis
    }

    try:
        r = requests.post(
            f"{PROXY_URL}/chat/completions",
            json=payload,
            timeout=120,
            headers={"Content-Type": "application/json"}
        )
        r.raise_for_status()
        result = r.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content
    except Exception as e:
        print(f"[ERROR] Gemini call failed: {e}")
        return f"❌ Gemini 分析失敗：{str(e)}"


# ──── MAIN ────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="Analysis period in days")
    parser.add_argument("--model", default="gemini-2.5-pro", help="Model to use")
    parser.add_argument("--output", help="Output path for report")
    args = parser.parse_args()

    # Find latest summary JSON
    if not os.path.exists(REPORT_DIR):
        print("No report directory found. Run signal_logger.py --report first.")
        return

    json_files = sorted([f for f in os.listdir(REPORT_DIR) if f.startswith("summary_") and f.endswith(".json")])
    if not json_files:
        print("No summary JSON found. Run signal_logger.py --report first.")
        return

    latest_json = os.path.join(REPORT_DIR, json_files[-1])
    print(f"[{datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}] Loading: {latest_json}")

    with open(latest_json) as f:
        data = json.load(f)

    if not data.get("indicators"):
        print("No indicator data yet. Need at least some signals to analyze.")
        return

    # Build prompt & call Gemini
    print(f"[{datetime.now(HKT).strftime('%H:%M:%S')}] Building analysis prompt...")
    prompt = build_analysis_prompt(data)
    print(f"[{datetime.now(HKT).strftime('%H:%M:%S')}] Prompt length: {len(prompt)} chars")

    print(f"\n{'='*60}")
    print(f"📡 Calling Gemini 2.5 Pro via vertex-proxy...")
    print(f"{'='*60}\n")

    start = time.time()
    report = call_gemini(prompt, args.model)
    elapsed = time.time() - start

    # Print report
    print(f"\n{'='*60}")
    print(f"📊 Gemini Analysis Report ({elapsed:.1f}s)")
    print(f"{'='*60}\n")
    print(report)
    print(f"\n{'='*60}")

    # Save report
    if args.output:
        out_path = args.output
    else:
        out_path = os.path.join(REPORT_DIR, f"gemini_analysis_{datetime.now(HKT).strftime('%Y%m%d')}.md")

    with open(out_path, "w") as f:
        f.write(f"# Signal Accuracy Analysis\n")
        f.write(f"Generated: {datetime.now(HKT).isoformat()}\n\n")
        f.write(report)

    print(f"\n📁 Report saved: {out_path}")


if __name__ == "__main__":
    main()
