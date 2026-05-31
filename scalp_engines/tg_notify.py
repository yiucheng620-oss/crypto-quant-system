#!/usr/bin/env python3
"""
📬 TG_NOTIFY — 交易系統 Telegram 通知模組
===========================================
極簡 Telegram Bot API wrapper，俾 engine 同 cron job 用。

用法：
  from tg_notify import tg_send
  tg_send("🚀 BTC LONG @ 76500 | SL=75800 TP=78000 | OI Delta +1.2%")

特性：
  - 直接讀 ~/.hermes/.env 拎 bot token（唔 hardcode）
  - 429 rate limit auto retry（最多 3 次）
  - 靜音模式：所有 message silent（唔會 bomb user）
  - chat_id: 7201013497 (Home channel)
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

# ── Config ──────────────────────────────────────────────────
CHAT_ID = "7201013497"
ENV_FILE = Path.home() / ".hermes" / ".env"

def _load_token() -> str:
    """從 .env 讀 TELEGRAM_BOT_TOKEN"""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().split("\n"):
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""

BOT_TOKEN = _load_token()
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# ── Public API ─────────────────────────────────────────────

def tg_send(text: str, silent: bool = True, retries: int = 3) -> bool:
    """
    發送 Telegram 訊息到 Home channel。

    Args:
        text: 訊息內容（支援 Telegram markdown）
        silent: True = 唔響（disable_notification）
        retries: 429 rate limit 時重試次數

    Returns:
        True if sent successfully, False otherwise
    """
    if not BOT_TOKEN:
        print("[TG] ⚠️ No bot token found, skipping notification")
        return False

    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": silent,
    }).encode()

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                if data.get("ok"):
                    return True
                else:
                    print(f"[TG] ❌ API error: {data.get('description', 'unknown')}")
                    return False
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Rate limited — wait and retry
                retry_after = int(e.headers.get("Retry-After", 5))
                print(f"[TG] ⚠️ 429 rate limited, retrying in {retry_after}s...")
                time.sleep(retry_after)
                continue
            print(f"[TG] ❌ HTTP {e.code}: {e.reason}")
            return False
        except Exception as e:
            print(f"[TG] ❌ Send failed: {e}")
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return False

    return False


def tg_trade_open(market: str, direction: str, entry: float, stop: float,
                  target: float, reason: str, strategy: str = "") -> bool:
    """格式化 trade open 通知並發送。"""
    emoji = "🟢" if direction == "LONG" else "🔴"
    # Escape HTML entities in user-provided text
    safe_reason = reason.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_strat = strategy.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") if strategy else ""
    strat_str = f" | <i>{safe_strat}</i>" if safe_strat else ""
    msg = (
        f"{emoji} <b>{direction} {market}</b> @ ${entry:,.2f}{strat_str}\n"
        f"SL: ${stop:,.2f} | TP: ${target:,.2f}\n"
        f"<i>{safe_reason}</i>"
    )
    return tg_send(msg)


def tg_trade_close(market: str, direction: str, entry: float, exit_price: float,
                   pnl: float, close_reason: str, hold_min: float = 0) -> bool:
    """格式化 trade close 通知並發送。"""
    pnl_emoji = "💰" if pnl > 0 else "💸"
    hold_str = f" | {hold_min:.0f}min" if hold_min > 0 else ""
    safe_reason = close_reason.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    msg = (
        f"{pnl_emoji} <b>{direction} {market} 平倉</b> — P&L: ${pnl:+.2f}{hold_str}\n"
        f"Entry: ${entry:,.2f} → Exit: ${exit_price:,.2f}\n"
        f"<i>{safe_reason}</i>"
    )
    return tg_send(msg)


def tg_circuit_breaker(engine: str, reason: str) -> bool:
    """Circuit breaker trigger 通知。"""
    safe_reason = reason.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    msg = f"🚨 <b>CIRCUIT BREAKER</b> — {engine}\n<i>{safe_reason}</i>"
    return tg_send(msg, silent=False)  # 呢個要響


def tg_daily_summary(report_path: str) -> bool:
    """發送每日 summary（從 report file 讀）。"""
    try:
        with open(report_path) as f:
            content = f.read()
    except (FileNotFoundError, OSError):
        # fallback: notify user if report file cannot be read
        return tg_send("📊 每日優化報告已生成，但無法讀取。")
    
    # Extract summary section (first ~1500 chars, Telegram limit is 4096)
    lines = content.split("\n")
    # Find the key sections
    summary_parts = []
    in_stats = False
    in_opt = False
    for line in lines:
        if "交易統計" in line:
            in_stats = True
            summary_parts.append(line)
            continue
        if "今日自動優化" in line:
            in_opt = True
            summary_parts.append(line)
            continue
        if "Gemini" in line and "戰略分析" in line:
            # Stop before Gemini analysis (too long)
            break
        if in_stats or in_opt:
            summary_parts.append(line)
    
    msg = "\n".join(summary_parts[:25])  # Cap at 25 lines
    if not msg.strip():
        msg = content[:1500]
    
    return tg_send(msg, silent=False)  # 每日報告要響


# ── Quick test ──────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("[TG] Sending test message...")
        ok = tg_send("✅ *TG Notify Test*\n交易通知系統運作正常。", silent=True)
        print(f"[TG] {'✅ OK' if ok else '❌ FAIL'}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--trade-open":
        ok = tg_trade_open("BTC", "LONG", 76500.0, 75800.0, 78000.0,
                          "OI Delta +1.2% (> 0.3%) | new money inflow", "oi_long")
        print(f"[TG] Trade open: {'✅' if ok else '❌'}")
    else:
        print("Usage: python3 tg_notify.py [--test|--trade-open]")
