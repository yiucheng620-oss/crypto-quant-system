#!/usr/bin/env python3
"""
📬 Morning Briefing → TG Bridge
================================
讀 morning_briefing.sh 嘅 stdout log → 截取重點 → TG 推送。

Cron: 緊接 morning_briefing.sh 之後 (06:05 HKT = 22:05 UTC)
"""

import os
import sys
import re
import time
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
sys.path.insert(0, os.path.join(WORKSPACE, "scalp_engines"))

try:
    from tg_notify import tg_send
except ImportError:
    print("[TG] tg_notify unavailable")
    sys.exit(1)

try:
    from gemini_free import call_pro
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    print("[TG Bridge] gemini_free not available, skipping second opinion")

LOG_PATH = os.path.join(WORKSPACE, "logs", "morning_briefing.log")

def extract_summary(log_content: str) -> str:
    """Extract key sections from Commander's Briefing output."""
    lines = log_content.split("\n")
    
    # Find the most recent run completion marker
    complete_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if "Commander's Briefing Complete:" in lines[i]:
            complete_idx = i
            break
    
    if complete_idx is None:
        return None  # Not complete yet
    
    # Find the corresponding start marker (search backward from complete_idx)
    start_idx = None
    for i in range(complete_idx, -1, -1):
        if "Commander's Briefing Start:" in lines[i]:
            start_idx = i
            break
    
    if start_idx is None:
        return None
    
    recent = lines[start_idx:complete_idx+1]
    
    # Extract sections by markers
    parts = []
    in_section = False
    section_lines = []
    section_name = ""
    top3_count = 0
    
    for line in recent:
        # Detect section headers
        if "PART 0:" in line or "執行摘要" in line:
            if section_lines:
                parts.append((section_name, section_lines))
            section_name = "📋 執行摘要"
            section_lines = []
            in_section = True
            continue
        elif "PART 1:" in line or "宏觀環境" in line:
            if section_lines:
                parts.append((section_name, section_lines))
            section_name = "🌍 宏觀"
            section_lines = []
            in_section = True
            continue
        elif "PART 2:" in line or "加密貨幣" in line:
            if section_lines:
                parts.append((section_name, section_lines))
            section_name = "🪙 Crypto"
            section_lines = []
            in_section = True
            continue
        elif "PART 3:" in line or "美股" in line:
            if section_lines:
                parts.append((section_name, section_lines))
            section_name = "📊 美股"
            section_lines = []
            in_section = True
            continue
        elif "Commander's Briefing Complete" in line or "Hermes Agent" in line or "📡 Crypto:" in line:
            in_section = False
            continue
        
        if in_section:
            clean = line.strip()
            if clean and len(clean) > 3:
                section_lines.append(clean)
    
    # Save last section
    if section_lines:
        parts.append((section_name, section_lines))
    
    if not parts:
        return None
    
    # Build output: Top 3 picks first, then macro, then crypto, then equities (trimmed)
    output = []
    
    for name, slines in parts:
        if name == "📋 執行摘要":
            # Only keep top 3 picks + regime
            output.append("📋 執行摘要")
            regime_found = False
            pick_count = 0
            for l in slines:
                if "市場體制" in l or "regime" in l.lower():
                    output.append(l)
                    regime_found = True
                elif any(c in l for c in ["Top 3", "1.", "2.", "3.", "📈", "📉"]):
                    output.append(l)
                    pick_count += 1
                    if pick_count >= 5:
                        break
            output.append("")
        elif name == "🌍 宏觀":
            output.append("🌍 宏觀")
            output.extend(slines[:4])
            output.append("")
        elif name == "🪙 Crypto":
            output.append("🪙 Crypto")
            # Keep price + S/R lines, skip order book details
            kept = 0
            for l in slines:
                if any(kw in l for kw in ["BTC", "ETH", "SOL", "關鍵價位", "$", "上升", "下降", "盤整", "ETF"]):
                    output.append(l)
                    kept += 1
                if kept >= 8:
                    break
            output.append("")
        elif name == "📊 美股":
            output.append("📊 美股")
            # Keep sector + top 5 setups
            kept = 0
            for l in slines:
                if any(kw in l for kw in ["偏向", "最強", "半導體", "能源", "科技", "金融", "Entry", "📈", "📉", "5/5", "🔥"]):
                    output.append(l)
                    kept += 1
                if kept >= 12:
                    break
            output.append("")
    
    if not output:
        # Fallback: raw lines
        meaningful = [l.strip() for l in recent if l.strip() and len(l.strip()) > 10]
        output = ["📋 晨報摘要:"] + meaningful[:20]
    
    return "\n".join(output)


def get_second_opinion(log_content: str) -> str | None:
    """Send briefing key points to Gemini 3.1 Pro for independent second opinion."""
    if not HAS_GEMINI:
        return None
    
    # Extract key sections for the prompt
    lines = log_content.split("\n")
    briefing_text = ""
    capture = False
    captured = 0
    
    for line in lines:
        if "Commander's Briefing Start:" in line:
            capture = True
            continue
        if "Commander's Briefing Complete:" in line:
            break
        if capture and line.strip() and len(line.strip()) > 5:
            briefing_text += line.strip() + "\n"
            captured += 1
            if captured >= 60:  # Enough context
                break
    
    if not briefing_text.strip():
        return None
    
    prompt = f"""You are an independent trading analyst giving a SECOND OPINION on this morning briefing.

Briefing content:
{briefing_text[:3000]}

Provide in 3-5 lines (Chinese):
1. Do you AGREE or DISAGREE with the regime assessment? Why?
2. Which of the top picks do you endorse, and which do you question?
3. One macro risk the briefing might have MISSED.

Be direct and concise. Use Traditional Chinese."""

    try:
        opinion = call_pro(prompt, max_tokens=8192, temperature=0.7)
        if opinion and not opinion.startswith("[ALL FAILED"):
            # Truncate if too long (TG message limit ~4096)
            if len(opinion) < 50:
                print(f"[TG Bridge] ⚠️ Second opinion too short ({len(opinion)} chars), skipping")
                return None
            opinion_text = opinion.strip()
            # TG limit ~4096, header ~40 + footer ~30 = ~70 overhead
            max_output = 3950
            if len(opinion_text) > max_output:
                opinion_text = opinion_text[:max_output] + "\n\n…(truncated)"
            return f"🧠 <b>Second Opinion</b> (Gemini 3.1 Pro)\n\n{opinion_text}\n\n— Gemini 3.1 Pro ✓"
    except Exception as e:
        print(f"[TG Bridge] Second opinion failed: {e}")
    
    return None


def main():
    now = datetime.now(HKT)
    
    # Read log
    if not os.path.exists(LOG_PATH):
        print(f"❌ Log not found: {LOG_PATH}")
        return
    
    # Try up to 3 times with 30s delay (briefing might still be running)
    for attempt in range(3):
        with open(LOG_PATH, "r") as f:
            content = f.read()
        
        if not content.strip():
            print(f"⚠️ Attempt {attempt+1}: Log empty, retrying in 30s...")
            time.sleep(30)
            continue
        
        summary = extract_summary(content)
        if summary:
            break
        
        print(f"⚠️ Attempt {attempt+1}: Briefing not complete yet, retrying in 30s...")
        time.sleep(30)
    else:
        print("❌ Briefing not complete after 3 attempts (90s)")
        # Fallback: send last 20 lines of log
        lines = content.strip().split("\n")
        summary = "⚠️ 晨報未完成，以下為最後輸出：\n\n" + "\n".join(lines[-15:])
    
    # Build TG message
    date_str = now.strftime("%m/%d")
    msg = f"🌅 <b>晨報</b> — {date_str}\n\n{summary}"
    
    # Truncate if too long
    if len(msg) > 3800:
        msg = msg[:3750] + "\n\n...(truncated)"
    
    # Send
    msg = msg.replace("&", "&amp;").replace("<b>", "⟨B⟩").replace("</b>", "⟨/B⟩")
    msg = msg.replace("⟨B⟩", "<b>").replace("⟨/B⟩", "</b>")
    
    sent = tg_send(msg, silent=True)
    print(f"{'✅ TG sent' if sent else '❌ TG failed'}")

    # ── Second Opinion (Gemini 3.1 Pro) ──
    if HAS_GEMINI:
        print("[TG Bridge] Getting second opinion from Gemini 3.1 Pro...")
        opinion = get_second_opinion(content)
        if opinion:
            time.sleep(1)  # Brief pause between messages
            opinion = opinion.replace("&", "&amp;").replace("<b>", "⟨B⟩").replace("</b>", "⟨/B⟩")
            opinion = opinion.replace("⟨B⟩", "<b>").replace("⟨/B⟩", "</b>")
            tg_send(opinion, silent=True)
            print("✅ Second opinion sent")
        else:
            print("⚠️ Second opinion skipped (no content)")


if __name__ == "__main__":
    main()
