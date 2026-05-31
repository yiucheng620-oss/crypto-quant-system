#!/usr/bin/env python3
"""
🆓 Gemini Free Tier Router — 統一入口，自動 fallback
=====================================================
用法:
    from gemini_free import call_flash, call_pro, get_usage

    result = call_flash("Extract tickers from this text...")
    result = call_pro("Analyze market regime...")
    usage = get_usage()  # {'flash': {'count': 5, 'rpd': 10000}, ...}

特性:
  - Gemini 3.5 Flash (10K RPD) → 量大的 task
  - Gemini 3.1 Pro  (250 RPD) → 高價值的 task
  - 自動 fallback 到 Vertex Proxy (port 8899)
  - 每日用量追蹤
"""

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ═══════════════════════════════════════
# Config
# ═══════════════════════════════════════
HKT = timezone(timedelta(hours=8))

GEMINI_KEY = None
_env_path = os.path.expanduser("~/.hermes/.env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            if line.startswith("GEMINI_API_KEY="):
                GEMINI_KEY = line.split("=", 1)[1].strip()
                break

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Rate limits (from AI Studio Tier 1)
LIMITS = {
    "gemini-3.5-flash":      {"rpm": 1000, "tpm": 2_000_000, "rpd": 10_000},
    "gemini-3.1-pro-preview": {"rpm": 25,   "tpm": 2_000_000, "rpd": 250},
}

# Daily usage counters (reset at midnight Pacific = 07:00 UTC = 15:00 HKT)
_usage = {
    "gemini-3.5-flash":      {"count": 0, "reset_day": None},
    "gemini-3.1-pro-preview": {"count": 0, "reset_day": None},
}

def _reset_if_new_day():
    """Reset counters at midnight Pacific (07:00 UTC)."""
    now = datetime.now(timezone.utc)
    today = now.date()
    # After 07:00 UTC, it's the next Pacific day
    if now.hour >= 7:
        today = today
    else:
        today = today - timedelta(days=1)
    
    for model in _usage:
        if _usage[model]["reset_day"] != today:
            _usage[model]["count"] = 0
            _usage[model]["reset_day"] = today


# ═══════════════════════════════════════
# Core call functions
# ═══════════════════════════════════════

def _call_gemini(model: str, prompt: str, max_tokens: int = 8192, temperature: float = 0.7, tools: list = None) -> str:
    """Direct call to Gemini API (free tier). Optionally with tools (google_search, code_execution)."""
    _reset_if_new_day()
    
    if not GEMINI_KEY:
        return _fallback_vertex(prompt, max_tokens, model)
    
    # Check RPD limit
    usage_info = _usage.get(model, {"count": 0, "reset_day": None})
    limit = LIMITS.get(model, {}).get("rpd", 999_999)
    if usage_info["count"] >= limit:
        print(f"[gemini_free] ⚠️ {model} RPD limit reached ({limit}), falling back to Vertex")
        return _fallback_vertex(prompt, max_tokens, model)
    
    url = f"{GEMINI_BASE}/{model}:generateContent?key={GEMINI_KEY}"
    
    payload_dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    
    # Only 3.5 models support thinkingConfig; 3.1 Pro doesn't
    if "3.5" in model:
        payload_dict["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    
    if tools:
        payload_dict["tools"] = tools
    
    payload = json.dumps(payload_dict).encode()
    
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        
        usage_info["count"] += 1
        
        if "candidates" in data:
            # Safe access: content may be empty if thinking consumed all tokens
            content = data["candidates"][0].get("content", {})
            parts = content.get("parts", [])
            text = ""
            sources = []
            
            for part in parts:
                if "text" in part:
                    text += part["text"]
            
            # Extract grounding sources if available
            gm = data["candidates"][0].get("groundingMetadata", {})
            for chunk in gm.get("groundingChunks", []):
                web = chunk.get("web", {})
                if web.get("uri"):
                    sources.append({"title": web.get("title", ""), "uri": web["uri"]})
            
            tokens = data.get("usageMetadata", {}).get("totalTokenCount", 0)
            
            # Return text + sources in a structured way
            result = {"text": text, "sources": sources, "tokens": tokens}
            return result
        else:
            err = data.get("error", {})
            print(f"[gemini_free] ❌ {model}: {err.get('message', 'unknown')[:100]}")
            return _fallback_vertex(prompt, max_tokens, model)
            
    except urllib.error.HTTPError as e:
        print(f"[gemini_free] ❌ HTTP {e.code}: {e.reason}")
        return _fallback_vertex(prompt, max_tokens, model)
    except Exception as e:
        print(f"[gemini_free] ❌ {model} error: {e}")
        return _fallback_vertex(prompt, max_tokens, model)


def _fallback_vertex(prompt: str, max_tokens: int = 8192, model: str = "gemini-pro") -> dict:
    """Fallback to the local Vertex Proxy on port 8899 using ADC."""
    url = "http://127.0.0.1:8899/v1/chat/completions"
    
    # Map model name for Vertex Proxy (gemini-2.5-pro, gemini-2.5-flash)
    vertex_model = "gemini-2.5-pro"
    if "flash" in model.lower():
        vertex_model = "gemini-2.5-flash"
        
    payload_dict = {
        "model": vertex_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7
    }
    
    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    
    try:
        print(f"[gemini_free] 🔄 Falling back to local Vertex Proxy (127.0.0.1:8899) with model {vertex_model}...")
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            
        choices = data.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "")
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return {"text": text, "sources": [], "tokens": tokens}
        else:
            return {"text": "", "sources": [], "tokens": 0, "error": "Empty response from Vertex Proxy"}
            
    except Exception as e:
        print(f"[gemini_free] ❌ Vertex Proxy fallback failed: {e}")
        return {"text": "", "sources": [], "tokens": 0, "error": f"Vertex Proxy failed: {e}"}


# ═══════════════════════════════════════
# Public API
# ═══════════════════════════════════════

def call_flash(prompt: str, max_tokens: int = 8192, temperature: float = 0.7) -> str:
    """
    Call Gemini 3.5 Flash (free tier) — plain text, no tools.
    用於：新聞提取、快速分類、輕量分析。
    RPD: 10,000 — 基本上任 call。
    """
    result = _call_gemini("gemini-3.5-flash", prompt, max_tokens, temperature)
    if isinstance(result, dict):
        return result.get("text", "")
    return result


def call_pro(prompt: str, max_tokens: int = 8192, temperature: float = 0.7) -> str:
    """
    Call Gemini 3.1 Pro Preview (free tier) — plain text, no tools.
    用於：Regime 判定、深度分析、交易驗證。
    RPD: 250 — 每日限量，只俾高價值 task。
    """
    result = _call_gemini("gemini-3.1-pro-preview", prompt, max_tokens, temperature)
    if isinstance(result, dict):
        return result.get("text", "")
    return result


# ═══════════════════════════════════════
# Tool-enabled calls (Google Search Grounding)
# ═══════════════════════════════════════

SEARCH_TOOL = [{"google_search": {}}]

def call_flash_search(prompt: str, max_tokens: int = 8192, temperature: float = 0.7) -> dict:
    """
    Call Gemini 3.5 Flash WITH Google Search Grounding.
    Returns: {"text": "...", "sources": [{"title":..., "uri":...}], "tokens": N}
    用於：即時新聞搜尋、macro data、price check。
    """
    result = _call_gemini("gemini-3.5-flash", prompt, max_tokens, temperature, tools=SEARCH_TOOL)
    if isinstance(result, dict):
        return result
    return {"text": result, "sources": [], "tokens": 0}


def call_pro_search(prompt: str, max_tokens: int = 8192, temperature: float = 0.7) -> dict:
    """
    Call Gemini 3.1 Pro WITH Google Search Grounding (deep analysis).
    用於：深度研究、需要 sourcing 嘅 macro 分析。
    RPD: 250。
    """
    result = _call_gemini("gemini-3.1-pro-preview", prompt, max_tokens, temperature, tools=SEARCH_TOOL)
    if isinstance(result, dict):
        return result
    return {"text": result, "sources": [], "tokens": 0}


def get_usage() -> dict:
    """Get current daily usage stats."""
    _reset_if_new_day()
    result = {}
    for model, info in _usage.items():
        short_name = model.replace("gemini-", "").replace("-preview", "")
        limit = LIMITS.get(model, {}).get("rpd", "?")
        result[short_name] = {
            "used": info["count"],
            "limit": limit,
            "remaining": limit - info["count"] if isinstance(limit, int) else "?",
        }
    return result


# ═══════════════════════════════════════
# Self-test
# ═══════════════════════════════════════
if __name__ == "__main__":
    print("🧪 Gemini Free Tier Router — Self Test\n")
    
    print("Testing 3.5 Flash...")
    result = call_flash("Say hello in one word.")
    print(f"  3.5 Flash: {result.strip()[:80]}")
    
    print("\nTesting 3.1 Pro...")
    result = call_pro("What is 2+2? Answer in one word.")
    print(f"  3.1 Pro: {result.strip()[:80]}")
    
    print("\n📊 Usage:")
    for model, info in get_usage().items():
        print(f"  {model}: {info['used']}/{info['limit']} used ({info['remaining']} remaining)")
