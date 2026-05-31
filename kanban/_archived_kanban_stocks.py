#!/usr/bin/env python3
"""
Kanban 美股深度分析引擎
========================
Reads stock_shortlist.json from Screener → dispatches to 4 analysts → synthesizer → TG

Analyst roles:
  1. 技術分析師 — 4H/1H/30m chart pattern, support/resistance, entry/exit levels
  2. 基本面分析師 — Sector trend, earnings, catalyst, relative strength vs peers
  3. 風險分析師 — Position sizing, stop loss, risk/reward ratio, correlation
  4. 市場環境分析師 — Macro context, sector rotation, fund flows, VIX regime

Synthesizer: compares 4 analyst reports, picks top 3 with trade plan.
"""
import sys
import os
import json
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
SHORTLIST_FILE = os.path.expanduser("~/workspace/scalp_lab/engines/stock_shortlist.json")

# Add gemini_free
sys.path.insert(0, os.path.expanduser("~/workspace/scalp_engines"))
from gemini_free import call_flash, call_pro


def load_shortlist():
    if not os.path.exists(SHORTLIST_FILE):
        print(f"❌ Shortlist not found: {SHORTLIST_FILE}")
        print("   Run stock_screener.py first")
        return None
    
    with open(SHORTLIST_FILE) as f:
        return json.load(f)


def analyst_1_technical(picks):
    """技術分析師: 4H/1H chart patterns, entry/exit levels."""
    stocks_text = "\n".join([
        f"${p['symbol']} @ ${p['price']} | Patterns: {', '.join(pt['type'] for pt in p['patterns'])}"
        for p in picks[:10]
    ])
    
    prompt = f"""你係技術分析師。以下係美股篩選結果，每隻已標註技術形態。

{stocks_text}

請對每隻股票：
1. 確認技術形態是否有效（pullback/breakout/momentum）
2. 提供精確入場價位（support 位或 breakout level）
3. 提供止損價位（跌破關鍵 support 或前低）
4. 提供止贏目標（下一阻力位，用 -200 pips buffer 原則）
5. 給予 1-5 星評級（只俾 4-5 星嘅先值得 trade）

用繁體中文。格式：
⭐X星 $TICKER ($XX.XX)
入場: $XX.XX | 止損: $XX.XX | 止贏: $XX.XX
原因: (一句話)"""

    return call_flash(prompt, max_tokens=8192, temperature=0.7)


def analyst_2_fundamental(picks):
    """基本面分析師: sector, catalyst, relative strength."""
    stocks_text = ", ".join([f"${p['symbol']}" for p in picks[:10]])
    
    prompt = f"""你係基本面分析師。請快速分析以下美股嘅 sector trend 同催化劑：

股票清單: {stocks_text}

對每隻有 catalyst 嘅股票：
1. 所屬 sector 目前係 lead 定 lag？
2. 最近有冇 earnings/catalyst/新聞？
3. 相對同 sector 其他股票，呢隻係強定弱？
4. 短期 catalyst risk（例如 upcoming earnings, FDA, etc）

只列出有明確 catalyst 嘅股票，冇嘅跳過。用繁體中文，每隻 2-3 行。"""

    return call_flash(prompt, max_tokens=8192, temperature=0.7)


def analyst_3_risk(picks):
    """風險分析師: position sizing, R:R, correlation."""
    stocks_text = "\n".join([
        f"${p['symbol']} @ ${p['price']} | 5d change: {p.get('change_5d', '?')}%"
        for p in picks[:10]
    ])
    
    prompt = f"""你係風險分析師。分析以下美股嘅風險參數：

{stocks_text}

交易者條件：
- 總資金 $10,000 USD
- 槓桿 1:1（現貨）
- 最多持倉 2 隻
- 每 sector 最多 1 隻
- 每注 risk 唔超過總資金 2%（即 $200 max loss）

對每隻有潛力嘅股票：
1. 建議倉位大小（股數 + 金額）
2. 建議止損價位同 max loss
3. Risk/Reward ratio
4. 如果同其他 pick 同 sector，建議揀邊隻

用繁體中文。"""

    return call_flash(prompt, max_tokens=8192, temperature=0.7)


def analyst_4_macro(picks):
    """市場環境分析師: macro, VIX, sector rotation."""
    prompt = f"""你係宏觀分析師。美股有以下潛在標的：

{', '.join([f'${p["symbol"]}' for p in picks[:5]])}

請快速評估：
1. 目前 macro regime（risk-on / risk-off / neutral）— 用 VIX、TNX、DXY 判斷
2. 邊啲 sector 資金流入緊？邊啲流出緊？
3. 以上 pick 嘅 sector 係咪喺 favorable regime？
4. 今個星期有冇 macro event risk（FOMC, CPI, etc）？

用繁體中文，5-8 行總結。"""

    return call_flash(prompt, max_tokens=8192, temperature=0.7)


def synthesizer(tech, fund, risk, macro, picks):
    """Synthesizer: 整合 4 份報告，出最終推薦."""
    
    prompt = f"""你係首席交易策略師。以下係 4 位分析師對美股短list嘅分析：

=== 技術分析 ===
{tech[:1500]}

=== 基本面 ===
{fund[:1000]}

=== 風險評估 ===
{risk[:1500]}

=== 宏觀環境 ===
{macro[:1000]}

=== 股票資料 ===
{json.dumps([{ 'symbol': p['symbol'], 'price': p['price'], 'patterns': [pt['type'] for pt in p['patterns']] } for p in picks[:10]], indent=2)}

請整合分析，輸出：

1. 🎯 Top 3 推薦（排名）
   每隻必須包含以下欄位：
   ⭐星級 | 股票代號 | 現價 | 入場價 | 止損價 | 止贏價 | R:R | 建議倉位
   
   🔥 推薦原因（必須最少寫 3 點）：
   • 技術面：（形態 + 關鍵位）
   • 基本面：（catalyst / sector trend）
   • 風險面：（max loss / 信心度）
   
   範例格式：
   ⭐⭐⭐⭐ $AAPL — $308.50
   入場: $305 | 止損: $298 | 止贏: $320 | R:R: 2.1 | 倉位: 30股 (~$9,150)
   🔥 推薦原因:
   • 技術面: pullback to 4H support at $300, holding above 20 SMA
   • 基本面: AI iPhone cycle + service revenue growth accelerating
   • 風險面: max loss $210 (2.1% of account), 4/5 confidence

2. ⚠️ 風險提醒（如有 macro event risk 或 sector rotation risk）

3. 📋 本週 watchlist（值得觀察但未到入場點嘅）

規則：
- 最多推薦 3 隻
- 同 sector 只推 1 隻
- Risk/Reward < 1:2 嘅唔推
- 冇信心嘅寧願唔推
- 每隻必須有 3 點推薦原因

用繁體中文，Telegram format（唔好用 table）。"""

    return call_pro(prompt, max_tokens=8192, temperature=0.7)


def main():
    now = datetime.now(HKT)
    print(f"🏗️ Kanban Stock Analysis Pipeline — {now.strftime('%Y-%m-%d %H:%M HKT')}\n")
    
    # Load shortlist
    data = load_shortlist()
    if not data:
        return
    
    picks = data.get("top_picks", []) or data.get("full_shortlist", [])[:10]
    
    if not picks:
        print("❌ No stocks in shortlist")
        return
    
    print(f"📋 {len(picks)} stocks in shortlist, dispatching to 4 analysts...\n")
    
    # Run 4 analysts (can't parallel in cron, do sequentially)
    print("👨‍💻 Analyst 1: 技術分析...")
    tech = analyst_1_technical(picks)
    if not tech or tech.startswith("[ALL FAILED"):
        print(f"   ❌ Failed: {tech[:100] if tech else 'no response'}")
        return
    print(f"   ✅ {len(tech)} chars")
    
    print("👨‍💼 Analyst 2: 基本面...")
    fund = analyst_2_fundamental(picks)
    if not fund or fund.startswith("[ALL FAILED"):
        print(f"   ⚠️ Failed, continuing without fundamentals")
        fund = "基本面分析暫時無法取得"
    else:
        print(f"   ✅ {len(fund)} chars")
    
    print("👨‍🔬 Analyst 3: 風險評估...")
    risk = analyst_3_risk(picks)
    if not risk or risk.startswith("[ALL FAILED"):
        print(f"   ⚠️ Failed, continuing without risk analysis")
        risk = "風險評估暫時無法取得"
    else:
        print(f"   ✅ {len(risk)} chars")
    
    print("👨‍🚀 Analyst 4: 宏觀環境...")
    macro = analyst_4_macro(picks)
    if not macro or macro.startswith("[ALL FAILED"):
        print(f"   ⚠️ Failed, continuing without macro")
        macro = "宏觀分析暫時無法取得"
    else:
        print(f"   ✅ {len(macro)} chars")
    
    print("\n🧠 Synthesizer: 整合分析...")
    final = synthesizer(tech, fund, risk, macro, picks)
    if not final or final.startswith("[ALL FAILED"):
        print(f"   ❌ Synthesizer failed: {final[:100] if final else 'no response'}")
        return
    print(f"   ✅ {len(final)} chars")
    
    # ── Extract structured trade plans for executor ──
    print("\n📋 Extracting trade plans for execution...")
    plans_json = extract_trade_plans(final, picks)
    if plans_json:
        plans_file = os.path.expanduser("~/workspace/scalp_lab/engines/kanban_trade_plans.json")
        tmp = plans_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(plans_json, f, indent=2, ensure_ascii=False)
        os.replace(tmp, plans_file)  # Atomic!
        print(f"   ✅ {len(plans_json.get('trades', []))} trade plans saved to kanban_trade_plans.json")
    else:
        print("   ⚠️ No executable trade plans extracted")
    
    # Output for cron delivery
    print("\n" + "="*60)
    print(final)
    print("="*60)


def extract_trade_plans(synthesizer_text, picks):
    """Use Gemini to parse synthesizer output into structured trade plans."""
    prompt = f"""Extract trade plans from this analysis into JSON:

{synthesizer_text[:2500]}

Stock data for reference:
{json.dumps([{'symbol': p['symbol'], 'price': p['price']} for p in picks[:10]], indent=2)}

Return ONLY a JSON object with this exact structure:
{{
  "trades": [
    {{
      "symbol": "AAPL",
      "direction": "LONG",
      "entry": 150.00,
      "stop": 145.00,
      "target": 160.00,
      "confidence": "4/5",
      "reason": "pullback to support"
    }}
  ],
  "notes": "macro risk warning or watchlist notes"
}}

Rules:
- Only include trades with explicit entry/stop/target
- Direction MUST be LONG or SHORT
- confidence: star rating as "N/5" format
- Max 3 trades
- If no actionable trades, return empty trades array
- Return ONLY valid JSON, no markdown"""

    try:
        response = call_flash(prompt, max_tokens=8192, temperature=0.7)
        # Clean up response - find JSON
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        print(f"   ⚠️ Failed to parse trade plans: {e}")
    
    return None


if __name__ == "__main__":
    main()
