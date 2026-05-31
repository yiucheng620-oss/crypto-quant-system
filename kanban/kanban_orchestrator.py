#!/usr/bin/env python3
"""
Kanban Morning Briefing Orchestrator
══════════════════════════════════════
每日 05:30 HKT 啟動，建立 4 個 parallel analyst Kanban tasks，
等佢哋完成後自動 trigger synthesizer 整合報告。

架構：
  Data Collectors (script, 0 token)
    → 4 Parallel Analyst Workers (LLM, burns DeepSeek tokens)
      → 1 Synthesizer (LLM, depends on all 4)
        → 最終報告推送 Telegram

Usage:
  python3 kanban_orchestrator.py           # Full pipeline
  python3 kanban_orchestrator.py --dry-run # Print tasks without creating
"""

import os, sys, json, subprocess, time
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
HOME = os.path.expanduser("~")
WORKSPACE = os.path.join(HOME, "workspace")
VENV_PYTHON = os.path.join(HOME, ".hermes/hermes-agent/venv/bin/python3")
HERMES = os.path.join(HOME, ".hermes/hermes-agent/venv/bin/hermes")

# ═══════════════════════════════════════════
# Step 0: Run Data Collectors
# ═══════════════════════════════════════════

COLLECTORS = [
    ("hl_orderbook.py", "orderbook_data"),
    ("hl_funding.py", "funding_data"),
    ("etf_flow.py", "etf_data"),
    ("onchain_data.py", "onchain_data"),
]

def run_collectors():
    """Run all data collector scripts. All zero-token, pure Python."""
    print("📡 [0/5] Running data collectors...")
    for script, _ in COLLECTORS:
        path = os.path.join(WORKSPACE, script)
        if os.path.exists(path):
            result = subprocess.run(
                [VENV_PYTHON, path],
                capture_output=True, text=True, timeout=60,
                cwd=WORKSPACE,
                env={**os.environ, "HOME": HOME}
            )
            status = "✅" if result.returncode == 0 else "❌"
            print(f"  {status} {script}")
        else:
            print(f"  ⚠️  {script} not found")
    print()

# ═══════════════════════════════════════════
# Step 1: Create Kanban Analyst Tasks
# ═══════════════════════════════════════════

ANALYST_TASKS = [
    {
        "title": "On-Chain Analysis",
        "assignee": "onchain-analyst",
        "body": """你是 On-Chain 分析師。請閱讀以下數據並用繁體中文撰寫分析。

數據檔案：
- ~/workspace/onchain_data/onchain_latest.json

請分析：
1. BTC 鏈上活動趨勢（算力、難度、交易量 vs 歷史）
2. 網絡健康度評估
3. 任何異常信號（算力急跌、交易量異常等）
4. 對 BTC 價格的潛在影響

輸出格式：繁體中文，200-300 字，emoji，Telegram-friendly（無 markdown 表格）。
如果數據不足，誠實說明。""",
    },
    {
        "title": "Order Flow & Market Microstructure",
        "assignee": "orderflow-analyst",
        "body": """你是 Order Flow 分析師。請閱讀以下數據並用繁體中文撰寫分析。

數據檔案：
- ~/workspace/orderbook_data/orderbook_latest.json
- ~/workspace/funding_data/funding_latest.json
- ~/workspace/options_data/options_latest.json（如存在）

請分析：
1. BTC/ETH/SOL 訂單簿買賣深度比 → 市場微觀情緒
2. 資金費率 → 市場是否過熱/過冷
3. 選擇權 Max Pain + Put/Call Ratio → 關鍵價位
4. 綜合 micro-structure 判斷

輸出格式：繁體中文，200-300 字，emoji，Telegram-friendly（無 markdown 表格）。""",
    },
    {
        "title": "Macro & Flow Analysis",
        "assignee": "macro-analyst",
        "body": """你是宏觀分析師。請閱讀以下數據並用繁體中文撰寫分析。

數據檔案：
- ~/workspace/macro_data/macro_alerts.json
- ~/workspace/etf_data/etf_flow_latest.json
- ~/market_data/daily/（最新日期 folder）

請分析：
1. ETF 資金流向（BTC/ETH 淨流入/流出）→ 機構情緒
2. Top 3 最重要宏觀事件 + 對 crypto 的影響
3. 美股關鍵數據（SPY/QQQ/VIX）→ 風險偏好
4. 綜合 macro + flow 判斷

輸出格式：繁體中文，200-300 字，emoji，Telegram-friendly（無 markdown 表格）。""",
    },
    {
        "title": "Strategy & Backtest Ranking",
        "assignee": "backtest-analyst",
        "body": """你是策略分析師。請閱讀以下數據並用繁體中文撰寫分析。

數據檔案：
- ~/workspace/vbt_results/latest_ranking.json（如存在）
- ~/workspace/signal_accuracy.db（SQLite）

若 vbt_results 存在，請分析：
1. Top 3 策略（Sharpe/Return/DD）→ 推薦
2. Worst 3 策略 → 應避免
3. 市場狀態 vs 策略匹配度（當前 bear market → 哪些策略適合？）

若數據不足，請用 signal_accuracy.db 做替代分析：
- 各 indicator 近期 WR 排名
- 建議關注的 indicator

輸出格式：繁體中文，200-300 字，emoji，Telegram-friendly（無 markdown 表格）。""",
    },
]

def create_kanban_task(title, assignee, body, parents=None):
    """Create a Kanban task via hermes CLI."""
    cmd = [
        HERMES, "kanban", "create", title,
        "--assignee", assignee,
        "--body", body,
    ]
    if parents:
        for p in parents:
            cmd.extend(["--parent", p])
    
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=30,
        env={**os.environ, "HOME": HOME}
    )
    
    # Parse task ID from output
    output = result.stdout + result.stderr
    for line in output.split("\n"):
        if "task_id" in line.lower() or line.startswith("t_"):
            # Try to extract task ID
            import re
            match = re.search(r'(t_[a-f0-9]+)', line)
            if match:
                return match.group(1)
    
    print(f"  ⚠️  Could not parse task ID from: {output[:200]}")
    return None

def run_orchestrator(dry_run=False):
    """Main orchestrator."""
    now = datetime.now(HKT)
    print(f"🏗️  Kanban Morning Briefing Orchestrator")
    print(f"🕐 {now.strftime('%Y-%m-%d %H:%M HKT')}")
    print()
    
    # Step 0: Data collectors
    run_collectors()
    
    if dry_run:
        print("🔍 DRY RUN — would create these tasks:")
        for t in ANALYST_TASKS:
            print(f"  📋 [{t['assignee']}] {t['title']}")
        print(f"  📋 [synthesizer] Morning Briefing Synthesis (depends on all 4)")
        return
    
    # Step 1: Create 4 parallel analyst tasks
    print("📋 [1/5] Creating analyst tasks...")
    analyst_ids = []
    for t in ANALYST_TASKS:
        tid = create_kanban_task(t["title"], t["assignee"], t["body"])
        if tid:
            analyst_ids.append(tid)
            print(f"  ✅ {tid}: {t['title']} → {t['assignee']}")
        else:
            print(f"  ❌ Failed: {t['title']}")
    
    if len(analyst_ids) < 2:
        print("❌ Not enough analyst tasks created. Aborting.")
        return
    
    # Step 2: Create synthesizer task (depends on all analysts)
    print(f"\n📋 [2/5] Creating synthesizer task (depends on {len(analyst_ids)} analysts)...")
    synth_body = f"""你是綜合報告合成師。請閱讀以下 4 位分析師的輸出，整合成一份完整的早晨簡報。

分析師任務 ID（請用 hermes kanban show <id> 閱讀每位分析師的輸出）：
{chr(10).join(f'  - {tid}' for tid in analyst_ids)}

請整合成以下格式的早晨簡報：

📊 加密行情（BTC/ETH/SOL 價格 + 24h 變幅）
📈 市場微觀（訂單簿 + 資金費率 + 選擇權）
⛓️ 鏈上數據（網絡健康度）
💰 資金流向（ETF flow + 機構情緒）
🌍 宏觀（Top 3 事件 + 美股快照）
🎯 策略建議（Today's focus）
🧠 綜合判斷 + 今日關注

輸出格式：繁體中文，500-800 字，emoji，Telegram-friendly（無 markdown 表格）。
要簡潔有力，直接講重點，唔好廢話。"""

    synth_id = create_kanban_task(
        "Morning Briefing Synthesis",
        "synthesizer",
        synth_body,
        parents=analyst_ids
    )
    
    if synth_id:
        print(f"  ✅ {synth_id}: Morning Briefing Synthesis → synthesizer")
        print(f"  📎 Depends on: {', '.join(analyst_ids)}")
    else:
        print("  ❌ Failed to create synthesizer task")
    
    print(f"\n⏳ Pipeline launched. Dispatcher will process tasks.")
    print(f"   預計完成時間：~10-15 分鐘")
    print(f"   監控：hermes kanban list")
    print(f"   Synthesizer: hermes kanban show {synth_id}" if synth_id else "")

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run_orchestrator(dry_run=dry_run)
