#!/usr/bin/env python3
"""
📊 MONDAY REGIME DETECTOR + WEEKLY CONFIG LOCK
===============================================
Runs every Monday 09:00 HKT. Classifies market regime for the week
and locks per-engine strategy config (edge weights + risk params).

Pipeline:
  1. Fetch fresh macro data (VIX, DXY, BTC Dominance, ETH/BTC, funding)
  2. ML classification (Gemini Pro + rule-based) → regime type
  3. Map regime → per-engine edge weights + risk overrides
  4. Write regime_config.json (engine_coordinator reads this)
  5. Append to regime_history.json for backtesting

Regime types:
  trending_up    — bullish trend, strong momentum
  trending_down  — bearish trend, strong momentum
  choppy         — sideways, mean reversion preferred
  risk_on        — low VIX, strong risk appetite
  risk_off       — high VIX, capital preservation
  alt_rotation   — ETH/SOL outperforming BTC

Edge mapping per regime:
  trending_up/down → trend following (OI delta, ATR breakout) ↑ weight
  choppy           → mean reversion, lower weight, wider SL
  risk_on          → aggressive sizing
  risk_off         → defensive, tight SL, mean reversion only
  alt_rotation     → ETH heavy, BTC light

Cron: 0 9 * * 1  (every Monday 09:00 HKT)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

# ── Paths ──────────────────────────────────────────
HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENGINES_DIR = os.path.join(WORKSPACE, "scalp_engines")
LOG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
CONFIG_FILE = os.path.join(LOG_DIR, "regime_config.json")
HISTORY_FILE = os.path.join(LOG_DIR, "regime_history.json")
FLOW_FILE = os.path.join(LOG_DIR, "flow_state.json")
os.makedirs(LOG_DIR, exist_ok=True)

# ── Path setup for module imports ──────────────────────
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, ENGINES_DIR)

# ── Atomic I/O (inlined to avoid engine_base → capitalcom dependency) ──
def _atomic_save_json(path: str, data: Any):
    """Atomic write: write to temp, then os.replace. Prevents 0-byte corruption."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def _load_json(path: str, default: Any = None) -> Any:
    """Safe JSON load with fallback."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}

# ── Gemini (for ML classification) ──────────────────
try:
    from gemini_free import call_pro
except ImportError:
    call_pro = None

# ── yfinance (fallback for VIX / DXY) ──────────────
try:
    import yfinance as yf
except ImportError:
    yf = None


# ============================================================
#  DATA FETCH
# ============================================================

def _get_vix() -> float:
    """Fetch VIX from yfinance. Returns 0 on failure."""
    if yf is None:
        return 0
    try:
        t = yf.Ticker("^VIX")
        hist = t.history(period="2d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except Exception:
        pass
    return 0


def _get_dxy() -> float:
    """Fetch DXY (DX-Y.NYB) from yfinance. Returns 0 on failure."""
    if yf is None:
        return 0
    try:
        t = yf.Ticker("DX-Y.NYB")
        hist = t.history(period="2d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except Exception:
        pass
    return 0


def _get_week_change(current: float, key: str, flow_history: List[dict]) -> float:
    """
    Compute % change vs. ~7 days ago by looking back in flow_history.
    Returns 0.0 if insufficient data.
    """
    if not flow_history or len(flow_history) < 2:
        return 0.0
    # Look for an entry roughly 7 days (±1 day) older
    target_ts = time.time() - (7 * 86400)
    best = None
    best_diff = float("inf")
    for entry in flow_history:
        ts_str = entry.get("timestamp", "")
        if not ts_str:
            continue
        try:
            entry_ts = datetime.fromisoformat(ts_str).timestamp()
        except Exception:
            continue
        diff = abs(entry_ts - target_ts)
        if diff < best_diff:
            best_diff = diff
            best = entry
    if best is None or best_diff > 86400 * 2:  # within 2 days tolerance
        return 0.0
    old_val = best.get(key, 0)
    if old_val == 0:
        return 0.0
    return round((current - old_val) / old_val * 100, 2)


def collect_regime_data() -> dict:
    """
    Collect all data needed for regime classification.
    Returns a flat dict of features.
    """
    now = datetime.now(HKT)
    # Read latest flow monitor data
    flow = _load_json(FLOW_FILE, {})
    flow_history = _load_json(HISTORY_FILE, [])

    # Crypto data from flow monitor
    btc_dominance = flow.get("btc_dominance", 0)
    eth_btc_ratio = flow.get("eth_btc_ratio", 0)
    sol_btc_ratio = flow.get("sol_btc_ratio", 0)
    vix = flow.get("vix", 0)
    vix_regime = flow.get("vix_regime", "unknown")
    ndx_spx_ratio = flow.get("ndx_spx_ratio", 0)
    eurusd = flow.get("eurusd", 0)
    gold = flow.get("gold", 0)
    signals = flow.get("signals", [])

    # Fetch fresh VIX (may be stale in flow_state)
    fresh_vix = _get_vix()
    if fresh_vix and fresh_vix > 0:
        vix = fresh_vix

    # Fetch DXY
    dxy = _get_dxy()

    # Funding rates from flow monitor
    btc_funding = flow.get("btc_funding", 0)
    eth_funding = flow.get("eth_funding", 0)

    # Weekly changes
    btc_dom_change = _get_week_change(btc_dominance, "btc_dominance", flow_history)
    eth_btc_change = _get_week_change(eth_btc_ratio, "eth_btc_ratio", flow_history)
    vix_change = _get_week_change(vix, "vix", flow_history)

    # Also try to get BTC/ETH prices from flow for context
    hl_data = flow.get("hyperliquid", {})
    btc_price = hl_data.get("BTC", {}).get("mark", 0) if isinstance(hl_data, dict) else 0
    eth_price = hl_data.get("ETH", {}).get("mark", 0) if isinstance(hl_data, dict) else 0

    return {
        "timestamp": now.isoformat(),
        "weekday": now.strftime("%A"),
        "hour_hkt": now.hour,

        # Macro
        "vix": vix,
        "vix_regime": vix_regime,
        "vix_week_change_pct": vix_change,
        "dxy": dxy,
        "eurusd": eurusd,
        "gold": gold,

        # Crypto structure
        "btc_dominance": btc_dominance,
        "btc_dom_week_change_pct": btc_dom_change,
        "eth_btc_ratio": eth_btc_ratio,
        "eth_btc_week_change_pct": eth_btc_change,
        "sol_btc_ratio": sol_btc_ratio,
        "btc_price": btc_price,
        "eth_price": eth_price,

        # Sentiment
        "btc_funding": btc_funding,
        "eth_funding": eth_funding,
        "ndx_spx_ratio": ndx_spx_ratio,
        "flow_signals": signals,
    }


# ============================================================
#  REGIME CLASSIFICATION
# ============================================================

def classify_regime(data: dict) -> dict:
    """
    Two-stage classification:
      1. Rule-based pre-classification using hard thresholds
      2. LLM refinement using Gemini Pro (if available)

    Returns: {
        "regime": "trending_up|trending_down|choppy|risk_on|risk_off|alt_rotation",
        "confidence": 0.0-1.0,
        "reasoning": "...",
        "features": {...}
    }
    """
    vix = data.get("vix", 0)
    btc_dom = data.get("btc_dominance", 0)
    eth_btc = data.get("eth_btc_ratio", 0)
    dxy = data.get("dxy", 0)
    btc_funding = data.get("btc_funding", 0)
    eth_funding = data.get("eth_funding", 0)
    signals = data.get("flow_signals", [])
    btc_dom_change = data.get("btc_dom_week_change_pct", 0)
    eth_btc_change = data.get("eth_btc_week_change_pct", 0)
    vix_change = data.get("vix_week_change_pct", 0)

    # ── Rule-based scoring ──────────────────────────
    scores = {
        "trending_up": 0.0,
        "trending_down": 0.0,
        "choppy": 0.0,
        "risk_on": 0.0,
        "risk_off": 0.0,
        "alt_rotation": 0.0,
    }

    # VIX-based signals
    if vix > 0:
        if vix < 15:
            scores["risk_on"] += 0.3
            scores["trending_up"] += 0.2
        elif vix < 20:
            scores["risk_on"] += 0.1
        elif vix > 28:
            scores["risk_off"] += 0.3
            scores["trending_down"] += 0.2
        elif vix > 22:
            scores["risk_off"] += 0.1
            scores["choppy"] += 0.1

    # VIX week change
    if abs(vix_change) < 5:
        scores["choppy"] += 0.1

    # BTC Dominance signals
    if btc_dom > 55:
        scores["trending_up"] += 0.2
        scores["risk_on"] += 0.1
    elif btc_dom > 50:
        scores["trending_up"] += 0.1
    elif btc_dom < 42:
        scores["alt_rotation"] += 0.3
        scores["choppy"] += 0.1

    # BTC Dom week change
    if btc_dom_change > 3:
        scores["trending_up"] += 0.2
    elif btc_dom_change < -3:
        scores["alt_rotation"] += 0.2

    # ETH/BTC ratio → alt rotation signal
    if eth_btc > 0:
        eth_btc_change_abs = abs(eth_btc_change)
        if eth_btc > 0.04 and eth_btc_change > 2:
            scores["alt_rotation"] += 0.25
            scores["choppy"] -= 0.1
        elif eth_btc > 0.035:
            scores["alt_rotation"] += 0.1
        elif eth_btc < 0.025 and eth_btc_change < -2:
            scores["trending_down"] += 0.15
            scores["risk_off"] += 0.1

    # Funding rates
    if btc_funding > 0.015:
        scores["risk_on"] += 0.15
        scores["trending_up"] += 0.1
    elif btc_funding < -0.01:
        scores["risk_off"] += 0.15
        scores["trending_down"] += 0.1

    if eth_funding < -0.015:
        scores["trending_down"] += 0.1
    elif eth_funding > 0.02:
        scores["alt_rotation"] += 0.15

    # Flow signals
    if "VIX_FEAR" in signals:
        scores["risk_off"] += 0.2
    if "RISK_OFF_FLOW_TO_GOLD" in signals:
        scores["risk_off"] += 0.15
    if "ALT_SEASON_SIGNAL" in signals:
        scores["alt_rotation"] += 0.2
    if "BTC_DOMINANCE_HIGH" in signals:
        scores["trending_up"] += 0.15

    # DXY (strong USD → risk off for crypto)
    if dxy > 104:
        scores["risk_off"] += 0.1
        scores["trending_down"] += 0.1
    elif dxy < 100:
        scores["risk_on"] += 0.1

    # ── Determine primary regime ──────────────────────
    # Add small noise floor to avoid zero-score edge case
    sorted_regimes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary_regime, primary_score = sorted_regimes[0]
    secondary_regime, secondary_score = sorted_regimes[1]

    # Confidence: margin between first and second, normalized
    total = sum(scores.values()) or 1
    confidence = min((primary_score - secondary_score) / total + 0.3, 0.95)

    # ── LLM refinement (Gemini Pro) ──────────────────
    llm_reasoning = ""
    if call_pro is not None:
        llm_result = _llm_classify(data, scores)
        if llm_result:
            llm_regime = llm_result.get("regime", "")
            llm_confidence = llm_result.get("confidence", 0)
            llm_reasoning = llm_result.get("reasoning", "")
            # Blend: use LLM if confidence > 0.6, else stick with rule-based
            if llm_confidence >= 0.6:
                primary_regime = llm_regime
                confidence = (confidence + llm_confidence) / 2

    reasoning_parts = [f"Rule-based: {primary_regime} (score={primary_score:.2f})"]
    if secondary_score > 0.1:
        reasoning_parts.append(f"Secondary: {secondary_regime} ({secondary_score:.2f})")
    if llm_reasoning:
        reasoning_parts.append(f"LLM: {llm_reasoning}")

    return {
        "regime": primary_regime,
        "confidence": round(confidence, 3),
        "reasoning": " | ".join(reasoning_parts),
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "features": {k: data[k] for k in [
            "vix", "btc_dominance", "eth_btc_ratio", "dxy",
            "btc_funding", "eth_funding", "ndx_spx_ratio",
        ] if k in data},
    }


def _llm_classify(data: dict, rule_scores: dict) -> Optional[dict]:
    """Use Gemini Pro to refine regime classification."""
    now = datetime.now(HKT)

    prompt = f"""You are a crypto market regime classifier. Analyze the following data and determine the most likely market regime for the coming week.

Current time: {now.strftime('%A %H:%M HKT, %Y-%m-%d')}

MARKET DATA:
- VIX: {data.get('vix', 'N/A')} (regime: {data.get('vix_regime', 'unknown')})
- DXY (USD Index): {data.get('dxy', 'N/A')}
- BTC Dominance: {data.get('btc_dominance', 'N/A')}%
- ETH/BTC Ratio: {data.get('eth_btc_ratio', 'N/A')}
- BTC Funding Rate: {data.get('btc_funding', 'N/A')}%
- ETH Funding Rate: {data.get('eth_funding', 'N/A')}%
- NDX/SPX Ratio: {data.get('ndx_spx_ratio', 'N/A')}
- Gold: ${data.get('gold', 'N/A')}

RULE-BASED SCORES (already computed):
{json.dumps(rule_scores, indent=2)}

Regime definitions:
1. trending_up — BTC leading, strong upward momentum, low-mid VIX, positive funding
2. trending_down — Broad selloff, high VIX, negative funding
3. choppy — Sideways, VIX 15-22, no clear direction, low signal conviction
4. risk_on — Low VIX (<15), strong USD weakness, positive funding, alt activity
5. risk_off — High VIX (>28), strong USD (DXY>104), gold flowing, capital preservation
6. alt_rotation — ETH/BTC rising, BTC dominance falling (<45%), SOL outperforming

Respond in this exact JSON format (no markdown, no code fences):
{{"regime": "trending_up|trending_down|choppy|risk_on|risk_off|alt_rotation", "confidence": 0.0-1.0, "reasoning": "brief one-line explanation"}}"""

    try:
        result = call_pro(prompt, max_tokens=8192, temperature=0.7)
        # Parse JSON from response
        # Strip any markdown fences
        text = result.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        parsed = json.loads(text)
        if parsed.get("regime") in ("trending_up", "trending_down", "choppy", "risk_on", "risk_off", "alt_rotation"):
            return parsed
    except Exception as e:
        print(f"  ⚠️ LLM classification failed: {e}")

    return None


# ============================================================
#  REGIME → CONFIG MAPPING
# ============================================================

def build_regime_config(regime_result: dict) -> dict:
    """
    Map regime type → per-engine edge weights, risk params, and sizing.
    Returns the full regime_config.json structure.
    """
    regime = regime_result["regime"]
    confidence = regime_result["confidence"]
    features = regime_result.get("features", {})

    # ── Base config template ─────────────────────────
    config = {
        "_meta": {
            "version": "1.0.0",
            "description": f"Weekly Regime Config — locked Monday {datetime.now(HKT).strftime('%Y-%m-%d')}",
            "regime": regime,
            "confidence": confidence,
            "reasoning": regime_result.get("reasoning", ""),
            "generated_at": datetime.now(HKT).isoformat(),
            "locked_until": (datetime.now(HKT) + timedelta(days=6, hours=23)).isoformat(),
        },
        "global_overrides": {},
        "engines": {},
    }

    # ── Global overrides by regime ────────────────────
    global_overrides = {
        "trending_up": {
            "global_risk_multiplier": 1.2,
            "max_exposure_pct": 0.75,
            "edge_bias": "trend_following",
            "description": "Trend following preferred. Aggressive sizing. OI delta + ATR breakout edges active.",
        },
        "trending_down": {
            "global_risk_multiplier": 0.8,
            "max_exposure_pct": 0.50,
            "edge_bias": "trend_following",
            "description": "Trend following preferred (short side). Tighter risk. OI delta + ATR breakout edges active.",
        },
        "choppy": {
            "global_risk_multiplier": 0.6,
            "max_exposure_pct": 0.50,
            "edge_bias": "mean_reversion",
            "description": "Mean reversion only. Lower weight, wider SL. No trend edges.",
        },
        "risk_on": {
            "global_risk_multiplier": 1.5,
            "max_exposure_pct": 0.85,
            "edge_bias": "aggressive",
            "description": "Aggressive sizing. All edges active. Max positions increased.",
        },
        "risk_off": {
            "global_risk_multiplier": 0.4,
            "max_exposure_pct": 0.30,
            "edge_bias": "defensive",
            "description": "Defensive. Tight SL. Mean reversion only. Capital preservation priority.",
        },
        "alt_rotation": {
            "global_risk_multiplier": 1.1,
            "max_exposure_pct": 0.70,
            "edge_bias": "alt_rotation",
            "description": "ETH/SOL heavy. BTC light. Alt trend edges active.",
        },
    }
    config["global_overrides"] = global_overrides.get(regime, global_overrides["choppy"])

    # ── Per-engine configs ────────────────────────────
    # Each engine gets: edge_weights, risk_overrides, enabled_strategies

    # Base engine params (read from current strategy_config.json if available)
    current_cfg = _load_json(os.path.join(LOG_DIR, "strategy_config.json"), {}).get("engines", {})

    engines = ["btc", "eth", "gold", "indices", "equities"]

    for engine in engines:
        engine_cfg = current_cfg.get(engine, {})
        engine_cfg.pop("_enabled", None)

        if regime in ("trending_up", "trending_down"):
            config["engines"][engine] = _build_trend_config(engine, engine_cfg, regime, confidence, features)
        elif regime == "choppy":
            config["engines"][engine] = _build_choppy_config(engine, engine_cfg, confidence, features)
        elif regime == "risk_on":
            config["engines"][engine] = _build_risk_on_config(engine, engine_cfg, confidence, features)
        elif regime == "risk_off":
            config["engines"][engine] = _build_risk_off_config(engine, engine_cfg, confidence, features)
        elif regime == "alt_rotation":
            config["engines"][engine] = _build_alt_rotation_config(engine, engine_cfg, confidence, features)
        else:
            config["engines"][engine] = _build_choppy_config(engine, engine_cfg, 0.5, features)

    return config


def _apply_conf(confidence: float) -> float:
    """Scale a parameter by confidence: closer to 1 = stronger conviction."""
    return 0.7 + (confidence * 0.3)  # range: 0.7 to 1.0


def _build_trend_config(engine: str, base: dict, regime: str, confidence: float, features: dict) -> dict:
    """Trend-following config: OI delta + ATR breakout edges, higher weight."""
    mult = _apply_conf(confidence)
    is_short = regime == "trending_down"
    risk_mult = 0.8 if is_short else 1.2

    cfg = {
        "_enabled": True,
        "edge_weights": {
            "oi_delta": round(0.40 * mult, 2),
            "atr_breakout": round(0.35 * mult, 2),
            "mean_reversion": round(0.05, 2),
            "flow_bias": round(0.10, 2),
            "momentum": round(0.10 * mult, 2),
        },
        "risk_overrides": {
            "risk_per_trade_pct": round(base.get("risk_per_trade_pct", 0.02) * risk_mult * mult, 4),
            "stop_loss_pct": round(base.get("stop_loss_pct", 0.02) * (0.8 if is_short else 1.0), 4),
            "take_profit_pct": round(base.get("take_profit_pct", 0.05) * (1.2 if not is_short else 1.0), 4),
            "max_exposure_pct": round(base.get("max_exposure_pct", 0.70) * risk_mult * mult, 3),
        },
        "enabled_strategies": ["oi_delta", "atr_breakout", "flow_bias", "momentum"],
        "bias": "SHORT" if is_short else "LONG",
    }

    # BTC-specific OI delta thresholds
    if engine == "btc":
        cfg["oi_thresholds"] = {
            "long": round(0.25 * mult, 2),
            "short": round(-0.35 * mult, 2),
        }
        cfg["atr_period"] = base.get("atr_period", 14)

    # ETH gets boosted in alt rotation
    if engine == "eth" and not is_short:
        cfg["edge_weights"]["oi_delta"] = round(0.45 * mult, 2)
        cfg["edge_weights"]["momentum"] = round(0.15 * mult, 2)

    return cfg


def _build_choppy_config(engine: str, base: dict, confidence: float, features: dict) -> dict:
    """Mean reversion config: lower weight, wider SL, no trend edges."""
    mult = _apply_conf(confidence)

    cfg = {
        "_enabled": True,
        "edge_weights": {
            "oi_delta": round(0.10, 2),
            "atr_breakout": round(0.05, 2),
            "mean_reversion": round(0.55 * mult, 2),
            "flow_bias": round(0.25, 2),
            "momentum": round(0.05, 2),
        },
        "risk_overrides": {
            "risk_per_trade_pct": round(base.get("risk_per_trade_pct", 0.02) * 0.6 * mult, 4),
            "stop_loss_pct": round(base.get("stop_loss_pct", 0.02) * 1.5, 4),       # wider SL
            "take_profit_pct": round(base.get("take_profit_pct", 0.05) * 0.8, 4),    # smaller TP
            "max_exposure_pct": round(base.get("max_exposure_pct", 0.70) * 0.6 * mult, 3),
        },
        "enabled_strategies": ["mean_reversion", "flow_bias"],
        "bias": "NEUTRAL",
    }

    return cfg


def _build_risk_on_config(engine: str, base: dict, confidence: float, features: dict) -> dict:
    """Aggressive config: bigger size, all edges active."""
    mult = _apply_conf(confidence)

    cfg = {
        "_enabled": True,
        "edge_weights": {
            "oi_delta": round(0.30 * mult, 2),
            "atr_breakout": round(0.25 * mult, 2),
            "mean_reversion": round(0.15, 2),
            "flow_bias": round(0.15, 2),
            "momentum": round(0.15 * mult, 2),
        },
        "risk_overrides": {
            "risk_per_trade_pct": round(base.get("risk_per_trade_pct", 0.02) * 1.5 * mult, 4),
            "stop_loss_pct": round(base.get("stop_loss_pct", 0.02) * 0.9, 4),        # tighter SL
            "take_profit_pct": round(base.get("take_profit_pct", 0.05) * 1.3, 4),    # stretch TP
            "max_exposure_pct": round(base.get("max_exposure_pct", 0.70) * 1.5 * mult, 3),
        },
        "enabled_strategies": ["oi_delta", "atr_breakout", "mean_reversion", "flow_bias", "momentum"],
        "bias": "LONG",
    }

    # Gold benefits from risk-on (USD weakness)
    if engine == "gold":
        cfg["bias"] = "LONG"
        cfg["risk_overrides"]["risk_per_trade_pct"] = round(
            base.get("risk_per_trade_pct", 0.02) * 1.3 * mult, 4
        )

    return cfg


def _build_risk_off_config(engine: str, base: dict, confidence: float, features: dict) -> dict:
    """Defensive config: tiny size, tight SL, mean reversion only."""
    mult = _apply_conf(confidence)

    cfg = {
        "_enabled": True,
        "edge_weights": {
            "oi_delta": round(0.05, 2),
            "atr_breakout": round(0.02, 2),
            "mean_reversion": round(0.55 * mult, 2),
            "flow_bias": round(0.33, 2),
            "momentum": round(0.05, 2),
        },
        "risk_overrides": {
            "risk_per_trade_pct": round(base.get("risk_per_trade_pct", 0.02) * 0.35 * mult, 4),
            "stop_loss_pct": round(base.get("stop_loss_pct", 0.02) * 0.6, 4),        # tight SL
            "take_profit_pct": round(base.get("take_profit_pct", 0.05) * 0.7, 4),    # smaller TP
            "max_exposure_pct": round(base.get("max_exposure_pct", 0.70) * 0.35 * mult, 3),
        },
        "enabled_strategies": ["mean_reversion", "flow_bias"],
        "bias": "NEUTRAL",
    }

    # Gold gets protective bias in risk_off
    if engine == "gold":
        cfg["bias"] = "LONG"
        cfg["risk_overrides"]["risk_per_trade_pct"] = round(
            base.get("risk_per_trade_pct", 0.02) * 0.5 * mult, 4
        )

    return cfg


def _build_alt_rotation_config(engine: str, base: dict, confidence: float, features: dict) -> dict:
    """Alt rotation: ETH heavy, BTC light. SOL edges enabled."""
    mult = _apply_conf(confidence)

    if engine == "btc":
        # BTC: reduced exposure, no alt edges
        cfg = {
            "_enabled": True,
            "edge_weights": {
                "oi_delta": round(0.15 * mult, 2),
                "atr_breakout": round(0.15 * mult, 2),
                "mean_reversion": round(0.25, 2),
                "flow_bias": round(0.10, 2),
                "momentum": round(0.15, 2),
            },
            "risk_overrides": {
                "risk_per_trade_pct": round(base.get("risk_per_trade_pct", 0.02) * 0.5 * mult, 4),
                "stop_loss_pct": round(base.get("stop_loss_pct", 0.02) * 1.0, 4),
                "take_profit_pct": round(base.get("take_profit_pct", 0.05) * 1.0, 4),
                "max_exposure_pct": round(base.get("max_exposure_pct", 0.70) * 0.5 * mult, 3),
            },
            "enabled_strategies": ["mean_reversion", "flow_bias"],
            "bias": "NEUTRAL",
        }
    elif engine == "eth":
        # ETH: boosted. This is our primary vehicle for alt rotation.
        cfg = {
            "_enabled": True,
            "edge_weights": {
                "oi_delta": round(0.40 * mult, 2),
                "atr_breakout": round(0.30 * mult, 2),
                "mean_reversion": round(0.10, 2),
                "flow_bias": round(0.10, 2),
                "momentum": round(0.10 * mult, 2),
            },
            "risk_overrides": {
                "risk_per_trade_pct": round(base.get("risk_per_trade_pct", 0.02) * 1.5 * mult, 4),
                "stop_loss_pct": round(base.get("stop_loss_pct", 0.02) * 0.9, 4),
                "take_profit_pct": round(base.get("take_profit_pct", 0.05) * 1.3, 4),
                "max_exposure_pct": round(base.get("max_exposure_pct", 0.70) * 1.3 * mult, 3),
            },
            "enabled_strategies": ["oi_delta", "atr_breakout", "momentum", "flow_bias"],
            "bias": "LONG",
        }
    else:
        # Gold/Indices/Equities: standard choppy config, slight negative bias (capital may flow to crypto)
        cfg = {
            "_enabled": True,
            "edge_weights": {
                "oi_delta": round(0.15, 2),
                "atr_breakout": round(0.15, 2),
                "mean_reversion": round(0.40 * mult, 2),
                "flow_bias": round(0.20, 2),
                "momentum": round(0.10, 2),
            },
            "risk_overrides": {
                "risk_per_trade_pct": round(base.get("risk_per_trade_pct", 0.02) * 0.7 * mult, 4),
                "stop_loss_pct": round(base.get("stop_loss_pct", 0.02) * 1.2, 4),
                "take_profit_pct": round(base.get("take_profit_pct", 0.05) * 0.9, 4),
                "max_exposure_pct": round(base.get("max_exposure_pct", 0.70) * 0.7 * mult, 3),
            },
            "enabled_strategies": ["mean_reversion", "flow_bias"],
            "bias": "NEUTRAL",
        }

    return cfg


# ============================================================
#  HISTORY MANAGEMENT
# ============================================================

def load_history() -> list:
    """Load regime history from disk."""
    return _load_json(HISTORY_FILE, [])


def append_history(regime_result: dict, regime_config: dict):
    """Append to regime history file (maintain last 500 entries)."""
    history = load_history()

    entry = {
        "timestamp": datetime.now(HKT).isoformat(),
        "regime": regime_result["regime"],
        "confidence": regime_result["confidence"],
        "reasoning": regime_result.get("reasoning", ""),
        "scores": regime_result.get("scores", {}),
        "features": regime_result.get("features", {}),
        "config_summary": {
            "global_risk_multiplier": regime_config.get("global_overrides", {}).get("global_risk_multiplier"),
            "edge_bias": regime_config.get("global_overrides", {}).get("edge_bias"),
            "engine_count": len(regime_config.get("engines", {})),
        },
    }

    history.append(entry)
    if len(history) > 500:
        history = history[-500:]

    _atomic_save_json(HISTORY_FILE, history)


# ============================================================
#  MAIN ENTRY POINT
# ============================================================

def run_regime_detector(dry_run: bool = False) -> dict:
    """
    Full pipeline: collect data → classify → build config → persist.
    
    Args:
        dry_run: If True, print results but don't write to disk.
    
    Returns:
        dict with full results for TG notification / logging.
    """
    now = datetime.now(HKT)
    print(f"[{now.strftime('%Y-%m-%d %H:%M HKT')}] 📊 Regime Detector — Monday weekly lock")
    print("=" * 55)

    # ── Step 1: Collect data ────────────────────────
    print("\n1️⃣  Collecting market data...")
    data = collect_regime_data()
    print(f"   VIX: {data['vix']} ({data['vix_regime']})")
    print(f"   DXY: {data['dxy']}")
    print(f"   BTC Dominance: {data['btc_dominance']}%")
    print(f"   ETH/BTC: {data['eth_btc_ratio']}")
    print(f"   BTC Funding: {data['btc_funding']}%  |  ETH Funding: {data['eth_funding']}%")
    if data.get("flow_signals"):
        print(f"   ⚠️ Flow signals: {', '.join(data['flow_signals'])}")

    # ── Step 2: Classify regime ─────────────────────
    print("\n2️⃣  Classifying regime...")
    regime_result = classify_regime(data)
    print(f"   Regime: {regime_result['regime']}  (confidence: {regime_result['confidence']:.1%})")
    print(f"   Reasoning: {regime_result['reasoning']}")
    print(f"   Scores: {json.dumps(regime_result['scores'])}")

    # ── Step 3: Build config ────────────────────────
    print("\n3️⃣  Building weekly regime config...")
    regime_config = build_regime_config(regime_result)
    go = regime_config["global_overrides"]
    print(f"   Edge bias: {go['edge_bias']}")
    print(f"   Risk multiplier: {go['global_risk_multiplier']}x")
    print(f"   Max exposure: {go['max_exposure_pct']*100:.0f}%")
    print(f"   Description: {go['description']}")

    for engine_name, engine_cfg in regime_config["engines"].items():
        ew = engine_cfg.get("edge_weights", {})
        ro = engine_cfg.get("risk_overrides", {})
        bias = engine_cfg.get("bias", "N/A")
        strategies = ", ".join(engine_cfg.get("enabled_strategies", []))
        print(f"   [{engine_name:8s}] bias={bias} | "
              f"risk={ro.get('risk_per_trade_pct', 0)*100:.1f}% | "
              f"SL={ro.get('stop_loss_pct', 0)*100:.1f}% | "
              f"TP={ro.get('take_profit_pct', 0)*100:.1f}% | "
              f"edges: {strategies}")

    # ── Step 4: Persist ─────────────────────────────
    if dry_run:
        print("\n⚠️  DRY RUN — no files written")
    else:
        print("\n4️⃣  Writing regime_config.json + regime_history.json...")
        _atomic_save_json(CONFIG_FILE, regime_config)
        append_history(regime_result, regime_config)
        print(f"   ✅ {CONFIG_FILE}")
        print(f"   ✅ {HISTORY_FILE}")

    print(f"\n[{datetime.now(HKT).strftime('%H:%M HKT')}] ✅ Regime detection complete.")
    print(f"   Regime locked for week: {regime_result['regime']}")

    # Return full result for TG bridge / cron_entry
    return {
        "status": "success",
        "timestamp": now.isoformat(),
        "regime": regime_result["regime"],
        "confidence": regime_result["confidence"],
        "reasoning": regime_result["reasoning"],
        "global_overrides": go,
        "engine_count": len(regime_config["engines"]),
        "engines": {
            name: {
                "enabled_strategies": cfg.get("enabled_strategies", []),
                "bias": cfg.get("bias", "NEUTRAL"),
                "risk_per_trade_pct": cfg.get("risk_overrides", {}).get("risk_per_trade_pct", 0),
                "stop_loss_pct": cfg.get("risk_overrides", {}).get("stop_loss_pct", 0),
                "take_profit_pct": cfg.get("risk_overrides", {}).get("take_profit_pct", 0),
            }
            for name, cfg in regime_config["engines"].items()
        },
        "features": data,
    }


# ============================================================
#  CLI
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Monday Regime Detector — weekly config lock")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing")
    parser.add_argument("--history", action="store_true", help="Show regime history")
    parser.add_argument("--force", action="store_true", help="Run even if not Monday (for testing)")
    args = parser.parse_args()

    if args.history:
        history = load_history()
        if not history:
            print("No regime history yet.")
        else:
            print(f"📊 Regime History (last {len(history)} entries)\n")
            for i, entry in enumerate(history[-20:]):  # show last 20
                ts = entry.get("timestamp", "?")[:16]
                regime = entry.get("regime", "?")
                conf = entry.get("confidence", 0)
                reason = entry.get("reasoning", "")[:80]
                print(f"  {i+1:2d}. [{ts}] {regime:15s} (conf={conf:.1%})  {reason}")
        sys.exit(0)

    # Check: only run on Monday (unless --force)
    today = datetime.now(HKT)
    if today.weekday() != 0 and not args.force:
        print(f"⏭️  Today is {today.strftime('%A')}. Regime detector only runs on Monday.")
        print(f"   Use --force to run anyway (for testing).")
        sys.exit(0)

    # Check: only run at 09:00±2h unless --force
    hour = today.hour
    if not (7 <= hour <= 11) and not args.force:
        print(f"⏭️  Current time: {today.strftime('%H:%M HKT')}. Expected ~09:00 HKT.")
        print(f"   Use --force to run anyway (for testing).")
        sys.exit(0)

    result = run_regime_detector(dry_run=args.dry_run)

    # Print JSON summary for cron to pipe
    if not args.dry_run:
        print(f"\n📤 JSON summary:")
        print(json.dumps({
            "regime": result["regime"],
            "confidence": result["confidence"],
            "global_risk_multiplier": result["global_overrides"].get("global_risk_multiplier"),
            "edge_bias": result["global_overrides"].get("edge_bias"),
        }, indent=2))
