#!/usr/bin/env python3
"""
📊 每日持倉主動管理 — 09:30 + 14:30 HKT
=========================================
唔好 set 完 SL/TP 就算 — 每日因應大市主動管理每個倉位。

對每個持倉做 Gemini 分析：
  - 📈 繼續持有（原 thesis 有效）
  - ❌ 平倉（大市轉向 / thesis 失效）
  - 🔧 調整止損（窄咗定闊咗）
  - 🎯 調整止贏（更進取定更保守）
  - ⚠️ 加紙止（paper_only）

支援：
  - Capital.com 真實倉位 → read API P&L + execute SL/TP adjustment
  - Paper-only 倉位 → 手動建議（不執行）

Cron:
  30 1 * * 1-5 → 09:30 HKT（美股開市後）
  30 6 * * 1-5 → 14:30 HKT（美股收市前）
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENG_DIR = os.path.join(WORKSPACE, "scalp_engines")
LOG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
REVIEW_DIR = os.path.join(WORKSPACE, "reviews")
POSITION_LOG = os.path.join(REVIEW_DIR, "position_review.json")
os.makedirs(REVIEW_DIR, exist_ok=True)

sys.path.insert(0, ENG_DIR)
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "capitalcom"))

# ═══════════════════════════════════════════════════════════════
# 1. GATHER ALL POSITIONS
# ═══════════════════════════════════════════════════════════════

NORMALIZE_MAP = {
    "CRYPTO": "BTCUSD", "INDICES": "US500", "GOLD": "GOLD",
    "BTC": "BTCUSD", "ETH": "ETHUSD",
}


def gather_positions() -> list:
    """Read ALL open positions from JSON files + Capital.com API.
    Deduplicate by deal_id. Enrich with live API data."""
    positions = {}

    # ── 1a. Capital.com API: get real positions with live P&L ──
    try:
        from capitalcom_paper import check_positions, ACCOUNTS
        for acct_name, acct_id in ACCOUNTS.items():
            try:
                pos_list = check_positions(acct_id)
                for p in pos_list:
                    deal_id = p.get("deal_id", "")
                    if not deal_id or deal_id == "?":
                        continue
                    epic = p.get("epic", "")
                    positions[deal_id] = {
                        "source": "capital_com",
                        "account": acct_name,
                        "epic": epic,
                        "ticker": p.get("ticker", epic),
                        "direction": p.get("direction", "LONG"),
                        "size": float(p.get("size", 0)),
                        "entry": float(p.get("entry", 0)),
                        "current_price": float(p.get("current", 0)),
                        "pnl": float(p.get("pnl_amt", 0)),
                        "pnl_pct": float(p.get("pnl_pct", 0)),
                        "stop": float(p.get("stop", 0)) or None,
                        "limit": float(p.get("limit", 0)) or None,
                        "deal_id": deal_id,
                        "paper_only": False,
                    }
            except Exception as e:
                print(f"  ⚠️ Capital.com {acct_name} failed: {e}")
    except ImportError:
        print("  ⚠️ capitalcom_paper not available")

    # ── 1b. JSON trade files: enrich Capital.com positions + paper_only ──
    json_positions = {}
    trade_files = [f for f in os.listdir(LOG_DIR) if f.endswith("_trades.json")]
    
    # First pass: enrich Capital.com positions with SL/TP from JSON (API may not return them)
    for fname in trade_files:
        path = os.path.join(LOG_DIR, fname)
        try:
            with open(path) as f:
                trades = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for t in trades:
            deal_id = t.get("deal_id", "")
            if deal_id and deal_id != "paper_only" and deal_id in positions:
                # Enrich: fill missing SL/TP from JSON record
                pos = positions[deal_id]
                if not pos.get("stop") and t.get("stop"):
                    pos["stop"] = float(t.get("stop", 0))
                if not pos.get("limit") and t.get("target"):
                    pos["limit"] = float(t.get("target", 0))
                if not pos.get("entry") or pos.get("entry") == 0:
                    pos["entry"] = float(t.get("entry", 0))
    for fname in trade_files:
        path = os.path.join(LOG_DIR, fname)
        try:
            with open(path) as f:
                trades = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for t in trades:
            if t.get("status") != "open":
                continue
            deal_id = t.get("deal_id", "")
            is_paper = t.get("paper_only", False)

            if is_paper and deal_id == "paper_only":
                # Paper-only trade: use deal_ref as unique key
                key = t.get("deal_ref", f"paper_{t.get('epic', '?')}")
                if key in json_positions:
                    continue
                json_positions[key] = {
                    "source": "paper_only",
                    "account": "paper",
                    "epic": t.get("epic", ""),
                    "ticker": t.get("epic", ""),
                    "direction": t.get("direction", "LONG"),
                    "size": float(t.get("size", 0)),
                    "entry": float(t.get("entry", 0)),
                    "current_price": None,  # 無 real-time price
                    "pnl": 0.0,
                    "pnl_pct": 0.0,
                    "stop": float(t.get("stop", 0)) or None,
                    "target": float(t.get("target", 0)) or None,
                    "limit": float(t.get("target", 0)) or None,
                    "deal_id": "paper_only",
                    "deal_ref": key,
                    "paper_only": True,
                    "reason": t.get("reason", ""),
                    "engine": t.get("engine", ""),
                }

    # Merge: Capital.com positions take priority
    for k, v in json_positions.items():
        if v.get("paper_only"):
            positions[k] = v
        elif v.get("deal_id", "") not in positions:
            positions[v.get("deal_id", "")] = v

    return list(positions.values())


# ═══════════════════════════════════════════════════════════════
# 2. GATHER MARKET CONTEXT
# ═══════════════════════════════════════════════════════════════

def get_market_context() -> dict:
    """Get current market context for position review."""
    ctx = {
        "vix": None,
        "macro_bias": "neutral",
        "time_hkt": datetime.now(HKT).strftime("%H:%M"),
        "day": datetime.now(HKT).strftime("%A"),
        "spx_direction": "flat",
        "sectors": {},
        "notes": [],
    }

    # ── Macro Pulse ──
    pulse_file = os.path.join(LOG_DIR, "macro_pulse.json")
    if os.path.exists(pulse_file):
        try:
            with open(pulse_file) as f:
                pulse = json.load(f)
            ctx["macro_bias"] = pulse.get("regime", pulse.get("bias", "neutral"))
            ctx["vix"] = pulse.get("vix", None)
            ctx["notes"].append(f"Macro: {pulse.get('summary', 'N/A')[:200]}")
        except Exception:
            pass

    # ── Try to get VIX from file if not in pulse ──
    if ctx["vix"] is None:
        vix_file = os.path.join(LOG_DIR, "flow_state.json")
        if os.path.exists(vix_file):
            try:
                with open(vix_file) as f:
                    flow = json.load(f)
                vix_data = flow.get("vix", {})
                ctx["vix"] = vix_data.get("current") or vix_data.get("value")
            except Exception:
                pass

    # ── Check regime detector output ──
    regime_file = os.path.join(REVIEW_DIR, "regime_latest.json")
    if os.path.exists(regime_file):
        try:
            with open(regime_file) as f:
                regime = json.load(f)
            ctx["regime"] = regime.get("regime", "unknown")
            ctx["notes"].append(f"Regime: {regime.get('regime', '?')}")
        except Exception:
            pass
    else:
        # Fallback: try the ML regime detector output at canonical path
        alt_regime = os.path.join(WORKSPACE, "data", "ml", "ml_regime_latest.json")
        if os.path.exists(alt_regime):
            try:
                with open(alt_regime) as f:
                    regime = json.load(f)
                # ml_regime_latest.json has per-asset regimes; extract consensus
                regimes = set()
                for asset, data in regime.items():
                    if isinstance(data, dict) and "regime" in data:
                        regimes.add(data["regime"])
                if regimes:
                    ctx["regime"] = ", ".join(sorted(regimes))
                    ctx["notes"].append(f"Regime (ML): {ctx['regime']}")
                else:
                    ctx["regime"] = "unknown"
            except Exception:
                ctx["regime"] = "unknown"
        else:
            # Proper fallback: create minimal default regime context
            ctx["regime"] = "unknown"
            ctx["notes"].append("Regime: unknown (no regime_latest.json)")
            # Create default file so future reads don't silently miss it
            default_regime = {
                "regime": "unknown",
                "confidence": 0.0,
                "created_at": datetime.now(HKT).isoformat(),
                "note": "Auto-created fallback — no regime detector output available",
            }
            try:
                os.makedirs(REVIEW_DIR, exist_ok=True)
                tmp = regime_file + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(default_regime, f, indent=2)
                os.replace(tmp, regime_file)
                ctx["notes"].append("Created default regime_latest.json fallback")
            except Exception:
                pass

    return ctx


# ═══════════════════════════════════════════════════════════════
# 3. GEMINI POSITION ANALYSIS
# ═══════════════════════════════════════════════════════════════

# Capital.com 槓桿對照（CFD margin → effective leverage）
LEVERAGE_MAP = {
    "crypto":   "~2x (CFD 50% margin)",
    "gold":     "~20x (CFD 5% margin)",
    "indices":  "~20x (CFD 5% margin)",
    "equities": "1:1 (無槓桿)",
}


def _get_leverage_label(pos: dict) -> str:
    """Return leverage label for a position."""
    acct = pos.get("account", "equities").lower()
    return LEVERAGE_MAP.get(acct, "1:1")


def build_analysis_prompt(positions: list, market_ctx: dict) -> str:
    """Build Gemini prompt with all positions and market context."""
    pos_lines = []
    for i, p in enumerate(positions):
        epic = p.get("ticker") or p.get("epic", "?")
        direction = p.get("direction", "?")
        entry = p.get("entry", 0)
        size = p.get("size", 0)
        pnl = p.get("pnl", 0)
        pnl_pct = p.get("pnl_pct", 0)
        stop = p.get("stop")
        limit = p.get("limit") or p.get("target")
        is_paper = p.get("paper_only", False)
        source = "📝 paper-only" if is_paper else f"🏦 Capital.com ({p.get('account', '?')})"
        current = p.get("current_price")
        
        pos_lines.append(f"""
### Position {i+1}: {epic} {direction}
- Source: {source}
- Account: {p.get('account', '?').upper()} | Leverage: {_get_leverage_label(p)}
- Entry: ${entry:.2f} | {'Current: $' + f'{current:.2f}' if current else 'Current: N/A'}
- Size: {size:.4f} | P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)
- Current SL: {'$' + f'{stop:.2f}' if stop else 'None'} | Current TP: {'$' + f'{limit:.2f}' if limit else 'None'}
- Position Value: ${entry * size:.2f}""")

    vix_str = f"VIX: {market_ctx.get('vix', 'N/A')}" if market_ctx.get('vix') else "VIX: N/A"
    macro_str = market_ctx.get('macro_bias', 'neutral')
    notes = "; ".join(market_ctx.get('notes', []))
    
    return f"""You are a senior trading desk manager reviewing open positions at {market_ctx.get('time_hkt', '?')} HKT on {market_ctx.get('day', '?')}.

## Market Context
- {vix_str}
- Macro Bias: {macro_str}
- Notes: {notes}
- Markets: US equities session {'active' if _is_us_session() else 'inactive'}

## Open Positions
{chr(10).join(pos_lines)}

## Task
For EACH position above, decide ONE action:

- **HOLD**: Thesis still valid, keep position unchanged
- **CLOSE**: Thesis invalidated OR risk/reward unfavorable. Close NOW.
- **ADJUST_SL**: Move stop loss (give new SL price and reason)
- **ADJUST_TP**: Move take profit (give new TP price and reason)
- **TRAIL**: Activate trailing stop (give activation threshold in %)

### Rules:
1. If VIX > 25: tighten SL to breakeven faster, reduce TP targets
2. If VIX < 15: allow wider SL, let profits run
3. If macro_bias contradicts position direction → strongly consider CLOSE
4. Paper-only trades without current price → use entry price as reference
5. Be AGGRESSIVE on winning positions (trail stops, not just hold)
6. Be DECISIVE on losing positions (cut losses, don't wait for SL hit)
7. If P&L > +5% and no TP adjustment yet → suggest trailing stop at +3%

### ⚡ LEVERAGED POSITIONS (CRYPTO / GOLD / INDICES — NOT equities):
8. Leverage amplifies both gains AND losses → SL must be ACTIVE, not passive
9. **NEVER set SL to exact breakeven (entry price) on leveraged positions** — 
   that guarantees getting stopped out on noise. Instead:
   - If P&L < +3%: keep SL at original level (don't move yet)
   - If P&L +3~8%: trail SL at +1.5% behind current price (lock some profit, leave room)
   - If P&L > +8%: trail SL at +3% behind current price (let winner run)
10. For leveraged positions, TRAIL is preferred over ADJUST_SL — 
    automated trailing lets profits run while protecting gains
11. If a leveraged position is losing > 5%: CLOSE it (leverage means small moves = big losses)
12. Gold (XAUUSD) especially volatile at ~20x leverage — wider stops needed, 
    minimum 0.5% stop distance from current price

### UNLEVERAGED (equities 1:1):
13. Equities can use tighter stops, breakeven SL acceptable after +2% profit
14. Equities SL adjustments should be based on support/resistance, not just P&L %

Return ONLY valid JSON array (no markdown, no explanation):
```json
[
  {{
    "position": <number matching Position #>,
    "epic": "<ticker>",
    "action": "HOLD|CLOSE|ADJUST_SL|ADJUST_TP|TRAIL",
    "new_sl": <float or null>,
    "new_tp": <float or null>,
    "trail_activation_pct": <float or null>,
    "reason": "<1-2 sentence reason in Chinese>"
  }}
]
```"""


def _is_us_session() -> bool:
    """Check if US market is currently in session."""
    now = datetime.now(HKT)
    h = now.hour + now.minute / 60
    # US regular session: 21:30-04:00 HKT (summer) / 22:30-05:00 (winter)
    return (21.5 <= h or h < 4.0) and now.weekday() < 5


def call_gemini_position_review(prompt: str) -> list:
    """Call Gemini 3.1 Pro for position review. Return parsed JSON list."""
    try:
        from gemini_free import call_pro
    except ImportError:
        print("  ❌ gemini_free not available")
        return []

    response = call_pro(prompt, max_tokens=8192, temperature=0.3)
    if not response:
        print("  ❌ Gemini returned empty response")
        return []

    # Extract JSON from response — try multiple strategies
    import re

    # Strategy 1: Direct JSON parse
    try:
        parsed = json.loads(response.strip())
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract from markdown code block
    match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', response)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find outermost array brackets (greedy, handles nested objects)
    # Find the first '[' and last ']'
    start = response.find('[')
    end = response.rfind(']')
    if start >= 0 and end > start:
        json_str = response[start:end+1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            # Try to fix common issues: trailing commas, truncated
            print(f"  ⚠️ JSON parse error at pos {e.pos}: {json_str[max(0,e.pos-50):e.pos+50]}")

    print(f"  ⚠️ Could not parse Gemini response (first 300 chars): {response[:300]}")
    return []


# ═══════════════════════════════════════════════════════════════
# 4. EXECUTE ADJUSTMENTS
# ═══════════════════════════════════════════════════════════════

def execute_adjustments(positions: list, decisions: list) -> list:
    """Execute SL/TP adjustments on Capital.com. Log results."""
    results = []
    
    # Build lookup
    pos_by_epic = {}
    for i, p in enumerate(positions):
        epic = p.get("ticker") or p.get("epic", "")
        key = f"{epic}_{p.get('direction', '')}"
        pos_by_epic[key] = p
        pos_by_epic[str(i + 1)] = p

    for d in decisions:
        pos_ref = d.get("position")
        epic = d.get("epic", "")
        action = d.get("action", "HOLD")
        
        # Find matching position
        pos = pos_by_epic.get(str(pos_ref)) or pos_by_epic.get(f"{epic}_LONG") or pos_by_epic.get(f"{epic}_SHORT")
        
        if not pos:
            results.append({"epic": epic, "action": action, "executed": False, "reason": "Position not found"})
            continue

        result = {
            "epic": epic,
            "action": action,
            "executed": False,
            "reason": d.get("reason", ""),
            "new_sl": d.get("new_sl"),
            "new_tp": d.get("new_tp"),
            "trail_pct": d.get("trail_activation_pct"),
        }

        if action == "HOLD":
            result["executed"] = True
            result["reason"] = d.get("reason", "Thesis still valid, holding")
        
        elif action == "CLOSE":
            if pos.get("paper_only"):
                result["executed"] = True
                result["reason"] = f"Paper-only closed: {d.get('reason', '')}"
            elif pos.get("deal_id") and pos.get("deal_id") != "paper_only":
                try:
                    from capitalcom_paper import close_position, get_account_for
                    # 🔧 Fix: use correct account_id via get_account_for()
                    account = pos.get("account", "equities")
                    account_id = get_account_for(account)
                    close_result = close_position(pos["deal_id"], account_id)
                    if "error" not in close_result:
                        result["executed"] = True
                        result["reason"] = f"Closed: {d.get('reason', '')}"
                    else:
                        result["executed"] = False
                        result["reason"] = f"Close failed: {close_result.get('error', '?')}"
                except Exception as e:
                    result["executed"] = False
                    result["reason"] = f"Close error: {e}"
        
        elif action in ("ADJUST_SL", "ADJUST_TP", "TRAIL"):
            # 🔧 TRAIL = Capital.com 冇原生 support → 計出 trailing SL price，當 ADJUST_SL 執行
            actual_action = action
            new_sl = d.get("new_sl")
            
            if action == "TRAIL" and not new_sl:
                # Calculate trailing SL from current price + trail_pct
                trail_pct = d.get("trail_activation_pct")
                current = pos.get("current_price") or pos.get("entry", 0)
                if current > 0 and trail_pct:
                    if pos.get("direction") == "LONG":
                        new_sl = round(current * (1 - trail_pct / 100), 2)
                    else:
                        new_sl = round(current * (1 + trail_pct / 100), 2)
                    actual_action = "ADJUST_SL"
                    print(f"     📐 TRAIL → ADJUST_SL: {trail_pct}% from ${current:.2f} = ${new_sl}")
            
            if pos.get("paper_only"):
                result["executed"] = True
                result["reason"] = f"Paper-only {actual_action}: {d.get('reason', '')}"
            elif pos.get("deal_id") and pos.get("deal_id") != "paper_only":
                try:
                    from capitalcom_paper import get_account_for, DEMO_URL, _headers
                    import requests as _requests
                    account_id = pos.get("account_id", "") or get_account_for(pos.get("account", "equities"))
                    h = _headers(account_id)
                    
                    update = {}
                    if actual_action in ("ADJUST_SL", "TRAIL") and new_sl:
                        update["stopLevel"] = round(float(new_sl), 2)
                    if actual_action == "ADJUST_TP" and d.get("new_tp"):
                        update["profitLevel"] = round(float(d["new_tp"]), 2)
                    
                    if update:
                        r = _requests.put(
                            f'{DEMO_URL}/api/v1/positions/{pos["deal_id"]}',
                            json=update, headers=h, timeout=10
                        )
                        if r.status_code == 200:
                            result["executed"] = True
                            result["action_display"] = actual_action
                        else:
                            result["executed"] = False
                            result["reason"] = f"API error: {r.status_code}"
                    else:
                        result["executed"] = True
                        result["reason"] = "No parameter change needed"
                except Exception as e:
                    result["executed"] = False
                    result["reason"] = f"Adjustment error: {e}"

        results.append(result)

    return results


# ═══════════════════════════════════════════════════════════════
# 5. MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    now = datetime.now(HKT)
    ts = now.strftime("%Y-%m-%d %H:%M HKT")
    print(f"{'='*60}")
    print(f"📊 每日持倉主動管理")
    print(f"   {ts}")
    print(f"{'='*60}\n")

    # 1. Gather
    print("📋 1/4 讀取持倉...")
    positions = gather_positions()
    if not positions:
        print("   ✅ 無需管理嘅持倉")
        return
    
    real_count = sum(1 for p in positions if not p.get("paper_only"))
    paper_count = sum(1 for p in positions if p.get("paper_only"))
    print(f"   Capital.com: {real_count} | Paper-only: {paper_count} | Total: {len(positions)}")
    
    for p in positions:
        epic = p.get("ticker") or p.get("epic", "?")
        is_paper = "📝" if p.get("paper_only") else "🏦"
        pnl = p.get("pnl", 0)
        print(f"   {is_paper} {epic} {p.get('direction','?')} | Entry=${p.get('entry',0):.2f} | P&L=${pnl:+.2f} ({p.get('pnl_pct',0):+.1f}%)")

    # 2. Market context
    print("\n🌍 2/4 市場背景...")
    ctx = get_market_context()
    print(f"   VIX: {ctx.get('vix', 'N/A')} | Macro: {ctx.get('macro_bias', '?')} | Session: {'🟢 active' if _is_us_session() else '🔴 closed'}")

    # 3. Gemini analysis
    print("\n🧠 3/4 Gemini 分析中...")
    prompt = build_analysis_prompt(positions, ctx)
    decisions = call_gemini_position_review(prompt)
    
    if not decisions:
        print("   ⚠️ Gemini 無返回決策，所有倉位維持不變")
        decisions = [{"position": i+1, "epic": p.get("ticker", p.get("epic", "?")), "action": "HOLD", 
                       "reason": "Gemini unavailable, auto-hold"} for i, p in enumerate(positions)]

    # 4. Execute
    print("\n⚡ 4/4 執行調整...")
    results = execute_adjustments(positions, decisions)
    
    # Display results
    action_emojis = {"HOLD": "✋", "CLOSE": "❌", "ADJUST_SL": "🔧", "ADJUST_TP": "🎯", "TRAIL": "📈"}
    for r in results:
        emoji = action_emojis.get(r["action"], "❓")
        status = "✅" if r["executed"] else "⚠️"
        detail = ""
        if r.get("new_sl"):
            detail += f" SL→${r['new_sl']:.2f}"
        if r.get("new_tp"):
            detail += f" TP→${r['new_tp']:.2f}"
        if r.get("trail_pct"):
            detail += f" Trail@{r['trail_pct']}%"
        print(f"   {emoji} {status} {r['epic']}: {r['action']}{detail}")
        print(f"      {r.get('reason', '')[:100]}")

    # 5. Save log
    log = {
        "timestamp": now.isoformat(),
        "positions_count": len(positions),
        "market_context": ctx,
        "decisions": decisions,
        "execution_results": [{"action": r["action"], "epic": r["epic"], "executed": r["executed"], "reason": r.get("reason", "")} for r in results],
    }

    # Append to log
    history = []
    if os.path.exists(POSITION_LOG):
        try:
            with open(POSITION_LOG) as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append(log)
    # Keep last 60 entries
    history = history[-60:]
    
    tmp = POSITION_LOG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, POSITION_LOG)

    # Summary
    actions = defaultdict(int)
    for r in results:
        if r["executed"]:
            actions[r["action"]] += 1
    
    print(f"\n📊 總結: ", end="")
    summary_parts = [f"{emoji} {count}" for act, count in actions.items() 
                     if (emoji := action_emojis.get(act, "❓"))]
    print(" | ".join(summary_parts) if summary_parts else "No changes")


if __name__ == "__main__":
    main()
