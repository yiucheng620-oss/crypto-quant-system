#!/usr/bin/env python3
"""Feed research data to Gemini 3.1 Pro for strategic analysis."""
import sys
sys.path.insert(0, "/home/yiucheng620/workspace/scalp_engines")

from gemini_free import call_pro
import json

PROMPT = """你係一個頂尖嘅量化交易系統戰略分析師。請基於以下全部研究數據，進行深度戰略分析。

================================================================================
【研究數據 — Post-Mortem Findings】
================================================================================
- 8個持倉打開：2個Crypto短倉（ETH @ 2094, BTC @ 76516，全部賺緊）、5個半導體長倉（AMD, AMAT, MRVL, LRCX, INTC，全部蝕緊）、1個SPX長倉（微蝕）
- 全部喺星期五23:16-23:48 HKT開倉
- 關鍵發現：5個半導體長倉實際上係1個集中賭注，唔係分散投資
- Crypto止損太窄（0.5-0.9%），唔夠cover週末gap風險

================================================================================
【研究數據 — Backtest Findings】
================================================================================
- 系統得2日歷史，總共7筆交易，全部輸錢或打和
- 數據太薄，冇統計意義
- Equities引擎完全冇交易紀錄

================================================================================
【研究數據 — Paper Trading Audit Findings】
================================================================================
- 4個賬戶，總存款$12,990，而家結餘$12,812（-$177，-1.37%）
- Crypto賬戶+11.75%，Equities賬戶-2.88%
- 嚴重BUG：P&L追蹤壞咗——所有已平倉交易顯示$0 P&L
- Gold賬戶從未執行交易
- 總共30筆交易，7筆已平倉，23筆顯示「開倉」（好多係過期未清理）

================================================================================
【3條關鍵問題 — 請逐一詳細回答】
================================================================================

問題1：基於以上數據，呢個系統最大嘅結構性問題係咩？（唔好講廢話，直接指出最嚴重嗰1-2個）

問題2：星期一開市前，最應該做嘅3件事係咩？（按緊急程度排序，講清楚點做）

問題3：如果只可以改一個嘢去提升下星期表現，改咩？（具體講改咩、點樣改、改完預期效果）

請用香港口語廣東話回答，直接、具體、可執行。唔好畀generic建議。"""

print("=" * 60)
print("🧠 Calling Gemini 3.1 Pro for strategic analysis...")
print("=" * 60)

result = call_pro(PROMPT, max_tokens=8192, temperature=0.7)

print("\n" + "=" * 60)
print("📊 GEMINI RESPONSE:")
print("=" * 60)
print(result)

# Save to file
output_path = "/home/yiucheng620/workspace/reviews/gemini_strategic_20260523.txt"
with open(output_path, "w", encoding="utf-8") as f:
    f.write(result)
print(f"\n✅ Saved to {output_path}")
