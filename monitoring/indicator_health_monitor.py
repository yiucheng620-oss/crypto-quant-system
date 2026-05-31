#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
Indicator Health Monitor — 三盞燈自動健康檢查引擎
═══════════════════════════════════════════════════════════════
三燈系統：
  🔴 LIGHT_1 — Regime Mismatch: 當前市場 ≠ indicator WFA 驗證 regime
  🔴 LIGHT_2 — Live Accuracy Drop: 30日 rolling WR < 40%（需 ≥5 settled signals）
  🔴 LIGHT_3 — Signal Quality Decline: avg_win_pct 連續 2 窗口下降

Alert rule: 三盞紅燈齊著 OR 2/3 紅燈 + severity=HIGH → Telegram alert
單一紅燈 = silent（避免 noise）

Data sources:
  - signal_accuracy.db (SQLite) — live signals + accuracy checks
  - market_state table — 當前市場狀態
  - WFA benchmarks (built-in) — 每個 indicator 嘅 per-regime 歷史 WR

Output: JSON health report → consumed by Kanban pipeline
═══════════════════════════════════════════════════════════════
"""

import os, sys, json, sqlite3, time
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import numpy as np

# ──── CONFIG ────
HKT = timezone(timedelta(hours=8))
SIGNAL_DB = os.path.expanduser("~/workspace/databases/signal_accuracy.db")
HEALTH_DB = os.path.expanduser("~/workspace/databases/indicator_health.db")
REPORT_DIR = os.path.expanduser("~/workspace/data/health_reports")

# ──── WFA BENCHMARK REGISTRY ────
# Per-indicator, per-market-condition, per-horizon win rates from Phase 6/8 WFA
# Format: {indicator_name: {regime: {"24h": WR%, "48h": WR%, "validated": bool}}}
# "primary_regime": the regime(s) where this indicator is validated to work
WFA_BENCHMARKS = {
    "VP_BTC_4h_L": {
        "benchmarks": {
            "bull":  {"24h": 77.8, "48h": 66.7, "validated": True},
            "range": {"24h": 100.0, "48h": 83.3, "validated": True},
            "bear":  {"24h": 100.0, "48h": 75.0, "validated": True},
        },
        "primary_regimes": ["bull", "range", "bear"],  # 全能型
        "min_signals_for_check": 3,
        "severity": "HIGH",  # 80% WR indicator
    },
    "OBV_BTC_1h_L": {
        "benchmarks": {
            "bull":  {"24h": 66.7, "48h": 55.6, "validated": True},
            "range": {"24h": 75.0, "48h": 62.5, "validated": True},
            "bear":  {"24h": 50.0, "48h": 40.0, "validated": False},  # 熊市不可靠
        },
        "primary_regimes": ["bull", "range"],
        "min_signals_for_check": 3,
        "severity": "MED",
    },
    "OBV_SOL_1h_L": {
        "benchmarks": {
            "bull":  {"24h": 70.0, "48h": 60.0, "validated": True},
            "range": {"24h": 80.0, "48h": 66.7, "validated": True},
            "bear":  {"24h": 45.0, "48h": 30.0, "validated": False},  # 熊市不可靠
        },
        "primary_regimes": ["bull", "range"],
        "min_signals_for_check": 3,
        "severity": "MED",
    },
    "VP_ETH_4h_L": {
        "benchmarks": {
            "bull":  {"24h": 75.0, "48h": 66.7, "validated": True},
            "range": {"24h": 66.7, "48h": 50.0, "validated": True},
            "bear":  {"24h": 50.0, "48h": 33.3, "validated": False},
        },
        "primary_regimes": ["bull", "range"],
        "min_signals_for_check": 3,
        "severity": "LOW",  # 樣本不足
    },
    "RSI_ETH_1h_S": {
        "benchmarks": {
            "bull":  {"24h": 83.6, "48h": 72.7, "validated": True},
            "bear":  {"24h": 100.0, "48h": 85.7, "validated": True},
            "range": {"24h": 80.8, "48h": 69.2, "validated": True},
        },
        "primary_regimes": ["bull", "bear", "range"],  # 全能型 SHORT
        "min_signals_for_check": 3,
        "severity": "HIGH",  # 83.6% WR
    },
    "VP_BTC_1h_S": {
        "benchmarks": {
            "bull":  {"24h": 78.9, "48h": 89.5, "validated": True},
            "bear":  {"24h": 71.4, "48h": 57.1, "validated": True},
            "range": {"24h": 69.0, "48h": 55.0, "validated": True},
        },
        "primary_regimes": ["bull", "bear", "range"],
        "min_signals_for_check": 3,
        "severity": "HIGH",  # 78.9% WR
    },
    "VP_SOL_1h_S": {
        "benchmarks": {
            "bull":  {"24h": 78.6, "48h": 64.3, "validated": True},
            "bear":  {"24h": 72.7, "48h": 54.5, "validated": True},
            "range": {"24h": 66.7, "48h": 50.0, "validated": True},
        },
        "primary_regimes": ["bull", "bear", "range"],
        "min_signals_for_check": 3,
        "severity": "MED",
    },
    # ══════ V2 EXPANSION (2026-05-18) ══════
    # Experimental variants — conservative benchmarks, pending WFA
    "VP_BTC_1h_L": {
        "benchmarks": {
            "bull":  {"24h": 65.0, "48h": 55.0, "validated": False},
            "range": {"24h": 60.0, "48h": 50.0, "validated": False},
            "bear":  {"24h": 50.0, "48h": 40.0, "validated": False},
        },
        "primary_regimes": ["bull", "range"],
        "min_signals_for_check": 5,
        "severity": "LOW",
    },
    "OBV_BTC_1h_L_fast": {
        "benchmarks": {
            "bull":  {"24h": 60.0, "48h": 50.0, "validated": False},
            "range": {"24h": 65.0, "48h": 55.0, "validated": False},
            "bear":  {"24h": 40.0, "48h": 30.0, "validated": False},
        },
        "primary_regimes": ["bull", "range"],
        "min_signals_for_check": 5,
        "severity": "LOW",
    },
    "OBV_SOL_1h_L_fast": {
        "benchmarks": {
            "bull":  {"24h": 60.0, "48h": 50.0, "validated": False},
            "range": {"24h": 65.0, "48h": 55.0, "validated": False},
            "bear":  {"24h": 40.0, "48h": 30.0, "validated": False},
        },
        "primary_regimes": ["bull", "range"],
        "min_signals_for_check": 5,
        "severity": "LOW",
    },
    "OBV_BTC_1h_S": {
        "benchmarks": {
            "bull":  {"24h": 55.0, "48h": 45.0, "validated": False},
            "bear":  {"24h": 65.0, "48h": 55.0, "validated": False},
            "range": {"24h": 60.0, "48h": 50.0, "validated": False},
        },
        "primary_regimes": ["bear", "range"],
        "min_signals_for_check": 5,
        "severity": "LOW",
    },
    "OBV_SOL_1h_S": {
        "benchmarks": {
            "bull":  {"24h": 55.0, "48h": 45.0, "validated": False},
            "bear":  {"24h": 65.0, "48h": 55.0, "validated": False},
            "range": {"24h": 60.0, "48h": 50.0, "validated": False},
        },
        "primary_regimes": ["bear", "range"],
        "min_signals_for_check": 5,
        "severity": "LOW",
    },
    "VP_ETH_1h_S": {
        "benchmarks": {
            "bull":  {"24h": 60.0, "48h": 50.0, "validated": False},
            "bear":  {"24h": 65.0, "48h": 55.0, "validated": False},
            "range": {"24h": 55.0, "48h": 45.0, "validated": False},
        },
        "primary_regimes": ["bear", "range"],
        "min_signals_for_check": 5,
        "severity": "LOW",
    },
}

# ──── THRESHOLDS ────
ROLLING_DAYS = 30          # Rolling window for live accuracy
MIN_SETTLED = 5            # Minimum settled signals for live WR to be meaningful
LIVE_WR_THRESHOLD = 40.0   # Live WR below this → LIGHT_2 red
QUALITY_WINDOWS = 2        # Consecutive windows for LIGHT_3
WR_DECAY_THRESHOLD = 15.0  # WR drop % from WFA benchmark → concern
WIN_PCT_DECLINE = 0.3      # avg_win_pct drop ratio (current/previous) → concern

# ──── DATABASE ────

def init_health_db():
    """Create health tracking tables."""
    conn = sqlite3.connect(HEALTH_DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS health_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at INTEGER NOT NULL,
            indicator TEXT NOT NULL,
            current_regime TEXT NOT NULL,
            primary_regimes TEXT NOT NULL,              -- JSON array
            light_1_regime_mismatch INTEGER DEFAULT 0,  -- 0=green, 1=red
            light_2_accuracy_drop INTEGER DEFAULT 0,
            light_3_quality_decline INTEGER DEFAULT 0,
            live_wr_30d REAL,
            wfa_benchmark_wr REAL,
            settled_30d INTEGER DEFAULT 0,
            avg_win_pct_30d REAL,
            avg_win_pct_prev REAL,
            alert_triggered INTEGER DEFAULT 0,
            alert_severity TEXT,                        -- HIGH/MED/LOW
            details TEXT                                -- JSON extra info
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS health_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            indicator TEXT NOT NULL,
            severity TEXT NOT NULL,
            red_lights TEXT NOT NULL,     -- e.g. "LIGHT_1,LIGHT_2"
            message TEXT NOT NULL,
            acknowledged INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS regime_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            coin TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            condition TEXT NOT NULL,
            adx REAL,
            volatility_pct REAL,
            trend_strength REAL
        )
    """)
    conn.commit()
    conn.close()


def get_live_accuracy(signal_db_path: str, indicator: str, days: int = 30) -> dict:
    """Calculate rolling live accuracy for an indicator."""
    conn = sqlite3.connect(signal_db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    cutoff = int(time.time()) - days * 86400

    # Live signals settled in rolling window
    c.execute("""
        SELECT a.result, a.pnl_pct, a.market_condition
        FROM accuracy a
        JOIN signals s ON a.signal_id = s.id
        WHERE s.indicator = ? AND a.checked_at > ? AND a.horizon = '24h'
    """, (indicator, cutoff))

    rows = c.fetchall()
    conn.close()

    if not rows:
        return {"total": 0, "wr": None, "avg_win": 0, "avg_loss": 0, "sufficient": False}

    wins = [r for r in rows if r["result"] == "WIN"]
    losses = [r for r in rows if r["result"] == "LOSS"]
    total = len(rows)
    wr = (len(wins) / total * 100) if total > 0 else 0
    avg_win = np.mean([r["pnl_pct"] for r in wins]) if wins else 0
    avg_loss = np.mean([r["pnl_pct"] for r in losses]) if losses else 0

    return {
        "total": total,
        "wr": round(wr, 1),
        "avg_win": round(float(avg_win), 3),
        "avg_loss": round(float(avg_loss), 3),
        "sufficient": total >= MIN_SETTLED,
        "condition_breakdown": _condition_breakdown(rows),
    }


def _condition_breakdown(rows) -> dict:
    """Break down results by market condition."""
    groups = defaultdict(lambda: {"wins": 0, "losses": 0})
    for r in rows:
        mc = r["market_condition"] or "unknown"
        if r["result"] == "WIN":
            groups[mc]["wins"] += 1
        else:
            groups[mc]["losses"] += 1
    result = {}
    for mc, counts in groups.items():
        total = counts["wins"] + counts["losses"]
        result[mc] = {
            "total": total,
            "wr": round(counts["wins"] / total * 100, 1) if total > 0 else 0,
        }
    return result


def get_current_regime(signal_db_path: str, coin: str, tf: str) -> dict:
    """Get most recent market state for coin/tf from signal DB."""
    conn = sqlite3.connect(signal_db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT condition, adx, volatility_pct, trend_strength
        FROM market_state
        WHERE coin = ? AND timeframe = ?
        ORDER BY timestamp DESC LIMIT 1
    """, (coin, tf))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "condition": row["condition"],
            "adx": row["adx"],
            "volatility_pct": row["volatility_pct"],
            "trend_strength": row["trend_strength"],
        }
    return {"condition": "unknown", "adx": 0, "volatility_pct": 0, "trend_strength": 0}


def get_prev_health(health_db_path: str, indicator: str) -> dict:
    """Get previous health check for trend comparison."""
    conn = sqlite3.connect(health_db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT live_wr_30d, avg_win_pct_30d, light_1_regime_mismatch,
               light_2_accuracy_drop, light_3_quality_decline
        FROM health_checks
        WHERE indicator = ?
        ORDER BY checked_at DESC LIMIT 1
    """, (indicator,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "live_wr_30d": row["live_wr_30d"],
            "avg_win_pct_30d": row["avg_win_pct_30d"],
            "light_1": bool(row["light_1_regime_mismatch"]),
            "light_2": bool(row["light_2_accuracy_drop"]),
            "light_3": bool(row["light_3_quality_decline"]),
        }
    return {}


def check_indicator_health(indicator: str) -> dict:
    """
    Core 3-light health check for a single indicator.
    Returns health status dict.
    """
    benchmark = WFA_BENCHMARKS.get(indicator)
    if not benchmark:
        return {"indicator": indicator, "error": "No WFA benchmark defined"}

    # Get indicator config from signal_logger's INDICATORS
    indicator_config = _get_indicator_config(indicator)
    if not indicator_config:
        return {"indicator": indicator, "error": "Not in active INDICATORS roster"}

    coin = indicator_config["coin"]
    tf = indicator_config["tf"]
    direction = indicator_config.get("direction", "LONG")

    # Get live accuracy
    live_acc = get_live_accuracy(SIGNAL_DB, indicator, ROLLING_DAYS)

    # Get current regime
    current_regime = get_current_regime(SIGNAL_DB, coin, tf)
    regime = current_regime.get("condition", "unknown")

    # Get previous health for trend
    prev = get_prev_health(HEALTH_DB, indicator)

    # ──── LIGHT 1: Regime Mismatch ────
    primary_regimes = benchmark["primary_regimes"]
    wfa_benchmarks = benchmark["benchmarks"]
    regime_mismatch = regime not in primary_regimes

    # Is this regime validated at all?
    regime_data = wfa_benchmarks.get(regime, {})
    regime_validated = regime_data.get("validated", False)
    wfa_wr = regime_data.get("24h", None)  # WFA benchmark WR for this regime

    # LIGHT_1 is RED if: regime not in primary_regimes AND not validated
    light_1 = regime_mismatch and not regime_validated

    # ──── LIGHT 2: Live Accuracy Drop ────
    live_wr = live_acc["wr"]
    sufficient = live_acc["sufficient"]

    light_2 = False
    accuracy_drop_detail = ""
    if sufficient and live_wr is not None:
        if live_wr < LIVE_WR_THRESHOLD:
            light_2 = True
            accuracy_drop_detail = f"Live WR {live_wr}% < {LIVE_WR_THRESHOLD}% threshold"
    elif not sufficient:
        accuracy_drop_detail = f"樣本不足（{live_acc['total']}/{MIN_SETTLED} settled）— LIGHT_2 suppressed"

    # ──── LIGHT 3: Signal Quality Decline ────
    light_3 = False
    quality_decline_detail = ""
    if prev and prev.get("avg_win_pct_30d") is not None and live_acc["avg_win"] > 0:
        prev_avg_win = prev["avg_win_pct_30d"]
        curr_avg_win = live_acc["avg_win"]
        if prev_avg_win > 0 and (curr_avg_win / prev_avg_win) < (1 - WIN_PCT_DECLINE):
            # Check if this is 2nd consecutive decline
            # (previous check had LIGHT_3 on)
            if prev.get("light_3"):
                light_3 = True
                quality_decline_detail = (
                    f"avg_win: {prev_avg_win:.3f}% → {curr_avg_win:.3f}% "
                    f"(連續 2 窗口下降)"
                )
            else:
                quality_decline_detail = (
                    f"avg_win: {prev_avg_win:.3f}% → {curr_avg_win:.3f}% "
                    f"(首次下降，需下個窗口確認)"
                )
    elif live_acc["avg_win"] <= 0 and live_acc["total"] >= MIN_SETTLED:
        # No winning trades at all in 30 days
        light_3 = True
        quality_decline_detail = "30日內零獲勝交易"

    # ──── ALERT DECISION ────
    red_count = sum([light_1, light_2, light_3])
    severity = benchmark["severity"]

    # Alert rule: 3/3 → ALERT; 2/3 + HIGH severity → ALERT
    alert_triggered = (red_count >= 3) or (red_count >= 2 and severity == "HIGH")

    # Build red lights list
    red_lights = []
    if light_1: red_lights.append("LIGHT_1(Regime Mismatch)")
    if light_2: red_lights.append("LIGHT_2(Accuracy Drop)")
    if light_3: red_lights.append("LIGHT_3(Quality Decline)")

    return {
        "indicator": indicator,
        "coin": coin,
        "timeframe": tf,
        "direction": direction,
        "severity": severity,
        "current_regime": regime,
        "regime_adx": current_regime.get("adx", 0),
        "regime_trend": current_regime.get("trend_strength", 0),
        "regime_volatility": current_regime.get("volatility_pct", 0),
        "primary_regimes": primary_regimes,
        "wfa_benchmark_wr": wfa_wr,
        # Live accuracy
        "live_wr_30d": live_wr,
        "settled_30d": live_acc["total"],
        "sufficient_sample": sufficient,
        "avg_win_pct_30d": live_acc["avg_win"],
        "avg_loss_pct_30d": live_acc["avg_loss"],
        "condition_breakdown": live_acc.get("condition_breakdown", {}),
        # 3 lights
        "light_1_regime_mismatch": light_1,
        "light_2_accuracy_drop": light_2,
        "light_3_quality_decline": light_3,
        "red_count": red_count,
        "red_lights": red_lights,
        # Details
        "regime_detail": f"當前={regime}, primary={primary_regimes}, validated={regime_validated}",
        "accuracy_drop_detail": accuracy_drop_detail,
        "quality_decline_detail": quality_decline_detail,
        # Alert
        "alert_triggered": alert_triggered,
        "alert_message": _build_alert_message(indicator, coin, tf, direction,
                                                red_lights, regime, live_wr,
                                                live_acc, severity, red_count)
                        if alert_triggered else None,
        # Previous
        "prev_wr_30d": prev.get("live_wr_30d"),
        "prev_avg_win": prev.get("avg_win_pct_30d"),
        "prev_red_count": sum([prev.get("light_1", False),
                               prev.get("light_2", False),
                               prev.get("light_3", False)]) if prev else None,
    }


def _build_alert_message(indicator, coin, tf, direction, red_lights,
                         regime, live_wr, live_acc, severity, red_count) -> str:
    """Build human-readable alert message in Traditional Chinese."""
    direction_cn = "做多" if direction == "LONG" else "做空"
    lights_str = " + ".join(red_lights)
    regime_cn = {"bull": "牛市", "bear": "熊市", "range": "區間", "volatile": "高波動"}.get(regime, regime)

    msg = (
        f"🚨 Indicator Health Alert\n"
        f"{'🔴' * red_count} {indicator}（{coin} {tf} {direction_cn}）\n"
        f"Severity: {severity} | Red lights: {lights_str}\n"
        f"當前市場: {regime_cn} | Live WR(30d): {live_wr}% ({live_acc['total']} settled)\n"
    )

    if red_count >= 3:
        msg += "\n⚠️ 三盞紅燈全著 — 建議暫停此 indicator 交易，等待重新 WFA 驗證。"
    else:
        msg += "\n⚠️ 兩盞紅燈 + HIGH severity — 密切觀察，考慮降低倉位。"

    return msg


def _get_indicator_config(indicator: str) -> dict:
    """Import indicator config from signal_logger.py INDICATORS dict."""
    sys.path.insert(0, os.path.expanduser("~/workspace"))
    try:
        from signal_logger import INDICATORS
        return INDICATORS.get(indicator, {})
    except ImportError:
        return {}


# ──── MAIN ORCHESTRATOR ────

def run_full_health_check() -> dict:
    """Run health check on ALL active indicators. Returns full report."""
    init_health_db()

    # Get all active indicators from signal_logger
    active_indicators = list(WFA_BENCHMARKS.keys())

    results = []
    alerts = []
    now = int(time.time())

    for ind in active_indicators:
        result = check_indicator_health(ind)
        if "error" in result:
            continue

        results.append(result)

        # Log to health DB
        _log_health_check(ind, result, now)

        if result["alert_triggered"]:
            alerts.append(result)
            _log_alert(ind, result, now)

    # Build regime summary
    regime_summary = _build_regime_summary()

    report = {
        "generated_at": datetime.now(HKT).isoformat(),
        "total_indicators": len(results),
        "healthy_count": sum(1 for r in results if r["red_count"] == 0),
        "warning_count": sum(1 for r in results if 0 < r["red_count"] < 3),
        "critical_count": sum(1 for r in results if r["red_count"] >= 3),
        "alerts": [a["alert_message"] for a in alerts],
        "alert_count": len(alerts),
        "regime_summary": regime_summary,
        "indicators": results,
    }

    # Save report
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(
        REPORT_DIR,
        f"health_{datetime.now(HKT).strftime('%Y%m%d_%H%M')}.json"
    )
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    # Also save latest symlink
    latest_path = os.path.join(REPORT_DIR, "health_latest.json")
    with open(latest_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    print(f"[{datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}] Health check complete")
    print(f"  Healthy: {report['healthy_count']} | Warning: {report['warning_count']} | Critical: {report['critical_count']}")
    print(f"  Alerts: {report['alert_count']}")
    print(f"  Report: {report_path}")

    return report


def _log_health_check(indicator: str, result: dict, now: int):
    """Log health check result to DB."""
    conn = sqlite3.connect(HEALTH_DB)
    c = conn.cursor()
    c.execute("""
        INSERT INTO health_checks (
            checked_at, indicator, current_regime, primary_regimes,
            light_1_regime_mismatch, light_2_accuracy_drop, light_3_quality_decline,
            live_wr_30d, wfa_benchmark_wr, settled_30d,
            avg_win_pct_30d, avg_win_pct_prev,
            alert_triggered, alert_severity, details
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now, indicator,
        result["current_regime"],
        json.dumps(result["primary_regimes"]),
        int(result["light_1_regime_mismatch"]),
        int(result["light_2_accuracy_drop"]),
        int(result["light_3_quality_decline"]),
        result["live_wr_30d"],
        result["wfa_benchmark_wr"],
        result["settled_30d"],
        result["avg_win_pct_30d"],
        result.get("prev_avg_win"),
        int(result["alert_triggered"]),
        result["severity"],
        json.dumps({
            "regime_detail": result.get("regime_detail", ""),
            "accuracy_drop_detail": result.get("accuracy_drop_detail", ""),
            "quality_decline_detail": result.get("quality_decline_detail", ""),
        }, ensure_ascii=False),
    ))
    conn.commit()
    conn.close()


def _log_alert(indicator: str, result: dict, now: int):
    """Log alert to DB."""
    conn = sqlite3.connect(HEALTH_DB)
    c = conn.cursor()
    c.execute("""
        INSERT INTO health_alerts (created_at, indicator, severity, red_lights, message)
        VALUES (?, ?, ?, ?, ?)
    """, (
        now, indicator, result["severity"],
        ",".join(result["red_lights"]),
        result["alert_message"],
    ))
    conn.commit()
    conn.close()


def _build_regime_summary() -> dict:
    """Build summary of current market regimes across all coins/TFs."""
    coins = ["BTC", "ETH", "SOL"]
    tfs = ["1h", "4h"]
    summary = {}
    for coin in coins:
        for tf in tfs:
            regime = get_current_regime(SIGNAL_DB, coin, tf)
            summary[f"{coin}_{tf}"] = regime
    return summary


# ──── CLI ────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Indicator Health Monitor")
    parser.add_argument("--check", type=str, default=None,
                        help="Check single indicator (e.g., VP_BTC_4h_L)")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON only")
    parser.add_argument("--alerts-only", action="store_true",
                        help="Only show alerts, silent if none")
    args = parser.parse_args()

    if args.check:
        result = check_indicator_health(args.check)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        else:
            print(f"\n{'='*60}")
            print(f"  Health Check: {args.check}")
            print(f"{'='*60}")
            print(f"  Regime: {result.get('current_regime', 'N/A')}")
            print(f"  LIGHT_1 (Regime Mismatch): {'🔴' if result.get('light_1_regime_mismatch') else '🟢'}")
            print(f"  LIGHT_2 (Accuracy Drop):  {'🔴' if result.get('light_2_accuracy_drop') else '🟢'}")
            print(f"  LIGHT_3 (Quality Decline): {'🔴' if result.get('light_3_quality_decline') else '🟢'}")
            print(f"  Live WR(30d): {result.get('live_wr_30d')}% ({result.get('settled_30d')} settled)")
            print(f"  Alert: {'⚠️ YES' if result.get('alert_triggered') else '✅ No'}")
            if result.get("alert_message"):
                print(f"\n  {result['alert_message']}")
    elif args.alerts_only:
        report = run_full_health_check()
        if report["alert_count"] == 0:
            print("[SILENT]")
        else:
            for alert_msg in report["alerts"]:
                print(alert_msg)
                print("---")
    else:
        report = run_full_health_check()
        if not args.json:
            print(f"\n{'='*60}")
            print(f"  Indicator Health Monitor — Full Report")
            print(f"{'='*60}")
            for ind in report["indicators"]:
                lights = ""
                lights += "🔴" if ind["light_1_regime_mismatch"] else "🟢"
                lights += "🔴" if ind["light_2_accuracy_drop"] else "🟢"
                lights += "🔴" if ind["light_3_quality_decline"] else "🟢"
                wr_str = f"{ind['live_wr_30d']}%" if ind['live_wr_30d'] is not None else "N/A"
                print(f"  {lights} {ind['indicator']:20s} | {ind['current_regime']:7s} "
                      f"| WR: {wr_str:>6s} ({ind['settled_30d']} settled) "
                      f"| {'⚠️ ALERT' if ind['alert_triggered'] else '✅ OK'}")
