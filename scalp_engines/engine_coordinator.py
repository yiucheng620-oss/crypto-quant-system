#!/usr/bin/env python3
"""
🎯 ENGINE COORDINATOR — 多市場引擎協調器 (Intraday Swing Mode)
==============================================================
職責：
  1. 先跑 flow_monitor（跨市場資金流數據）
  2. 依序跑 engine（BTC/ETH/GOLD/Indices — SOL removed for swing）
  3. 整合所有 signal → cross-market 報告
  4. 出 JSON + 文字 summary

Circuit breaker:
  - 計 total_balance 前 skip 任何 balance=0 嘅 engine（API fail guard）
  - 如果 total_balance 比 initial_balance 跌超過 5% → breaker
  - 所有 JSON save 用 atomic write (_atomic_save_json)

用法:
  python3 engine_coordinator.py                # Full scan (all engines + flow)
  python3 engine_coordinator.py --status       # Status check
  python3 engine_coordinator.py --report       # Cross-market report
  python3 engine_coordinator.py --scan-crypto  # Crypto only (BTC/ETH)
  python3 engine_coordinator.py --scan-macro   # Macro only (GOLD/Indices)
"""

import json
import os
import sys
import glob
import time
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENGINES_DIR = os.path.join(WORKSPACE, "scalp_engines")
LOG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
os.makedirs(LOG_DIR, exist_ok=True)

COORDINATOR_STATE = os.path.join(LOG_DIR, "coordinator_state.json")

sys.path.insert(0, WORKSPACE)
sys.path.insert(0, ENGINES_DIR)
sys.path.insert(0, os.path.join(WORKSPACE, "capitalcom"))
sys.path.insert(0, os.path.join(WORKSPACE, "collectors"))

from capitalcom_paper import _auth

# 匯入基底引擎 (ScalpEngine → SwingEngine 改名)
from engine_base import SwingEngine, _atomic_save_json, _load_json

# 匯入各市場引擎 (SOL removed — too noisy for swing)
from btc_swing import BtcSwingEngine
from eth_swing import EthSwingEngine
from gold_scalp import GoldScalpEngine
from indices_scalp import IndicesScalpEngine
from equities_swing import EquitiesSwingEngine

# Flow monitor (run first, not as engine)
import flow_monitor


class EngineCoordinator:
    """Orchestrates all 5 engines + cross-market analysis."""

    def __init__(self):
        _auth()  # Authenticate once for all engines

        self.engines = {
            "btc": BtcSwingEngine("btc"),
            "eth": EthSwingEngine("eth"),
            "gold": GoldScalpEngine("gold"),
            "indices": IndicesScalpEngine("indices"),
            "equities": EquitiesSwingEngine("equities"),
        }

        self.coord_state = _load_json(COORDINATOR_STATE, {
            "total_scans": 0,
            "total_signals": 0,
            "total_executed": 0,
            "initial_total_balance": 0,   # 初始總資金，用嚟計 drawdown
            "breaker_tripped": False,     # 電路斷路器狀態
        })

    def _get_total_exposure_from_all_logs(self) -> dict:
        """Read all trade logs (engines/*_trades.json including paper_trades.json)
        to compute total open position exposure across all systems.
        Returns dict with total_exposure, open_count, and per-file breakdown.
        """
        total_exposure = 0.0
        open_count = 0
        breakdown = {"engines": {}}

        # All engine trades: scalp_lab/engines/*_trades.json (includes paper_trades.json)
        engine_pattern = os.path.join(LOG_DIR, "*_trades.json")
        for fpath in sorted(glob.glob(engine_pattern)):
            fname = os.path.basename(fpath)
            try:
                with open(fpath, "r") as f:
                    trades = json.load(f)
            except Exception:
                continue
            file_exposure = 0.0
            file_open = 0
            for t in trades:
                if t.get("status") != "open":
                    continue
                size = float(t.get("size", 0))
                price = float(t.get("entry", t.get("current", 0)))
                file_exposure += size * price
                file_open += 1
            total_exposure += file_exposure
            open_count += file_open
            if file_open > 0:
                breakdown["engines"][fname] = {
                    "open": file_open, "exposure": round(file_exposure, 2)
                }

        return {
            "total_exposure": round(total_exposure, 2),
            "open_count": open_count,
            "breakdown": breakdown,
        }

    def _check_breaker(self, flow: dict, total_balance: float, scope_key: str = "initial_total_balance") -> str | None:
        """
        檢查 global circuit breaker 條件。
        Trigger: VIX > 35 或 portfolio drawdown > 5%
        Release: VIX < 30 兼 drawdown < 3%
        返回 reason string 如果 breaker triggered，否則 None。

        scope_key: 按 scan scope（CRYPTO_SCAN / FULL_SCAN）各自計 drawdown baseline
        """
        vix = flow.get("vix", 0)
        initial = self.coord_state.get(scope_key, 0)

        # 未初始化或零 balance -> 唔 trigger
        if initial <= 0 or total_balance <= 0:
            return None

        # Drawdown as positive %: (initial - current) / initial
        drawdown_pct = max(0, (initial - total_balance) / initial)

        # === Release condition: breaker 之前 tripped 咗，check 可否 release ===
        if self.coord_state.get("breaker_tripped", False):
            if vix < 30 and drawdown_pct < 0.03:
                self.coord_state["breaker_tripped"] = False
                _atomic_save_json(COORDINATOR_STATE, self.coord_state)
                print(f"  🔓 Circuit breaker released — VIX={vix:.1f} "
                      f"drawdown={drawdown_pct*100:.1f}%")
                return None

        # === Trigger condition ===
        reasons = []
        if vix > 35:
            reasons.append(f"VIX={vix:.1f} (> 35)")
        if drawdown_pct > 0.05:
            reasons.append(f"drawdown={drawdown_pct*100:.1f}% (> 5%)")

        if reasons:
            self.coord_state["breaker_tripped"] = True
            _atomic_save_json(COORDINATOR_STATE, self.coord_state)
            return "; ".join(reasons)

        return None

    def _run_engines(self, names: list, label: str) -> tuple:
        """Run a subset of engines, return (results, total_signals, total_executed, flow)."""
        now = datetime.now(HKT)
        print("=" * 60)
        print(f"🎯 ENGINE COORDINATOR {label} — {now.strftime('%Y-%m-%d %H:%M HKT')}")
        print("=" * 60)

        print(f"\n⚡ Engine Scans [{label}]...")
        results = {}
        total_signals = 0
        total_executed = 0
        flow = {}

        # === Global Circuit Breaker Check ===
        # 收集所有 engine 嘅 balance 嚟計 total drawdown
        # API fail guard: skip 任何 balance=0 嘅 engine
        total_balance = 0
        skipped_balance = 0
        for name in names:
            if name in self.engines:
                try:
                    bal = self.engines[name].get_balance()
                    b = bal.get("balance", 0)
                    if b <= 0:
                        skipped_balance += 1
                        print(f"  ⚠️ [{name:8s}] balance={b} (API fail guard - skipped)")
                        continue
                    total_balance += b
                except Exception:
                    skipped_balance += 1
                    print(f"  ⚠️ [{name:8s}] balance fetch failed (API fail guard - skipped)")

        # 初始化初始總資金（按 scan scope 各自 set，唔同 scope 唔同 baseline）
        init_key = f"initial_{label.lower().replace(' ','_')}"
        if self.coord_state.get(init_key, 0) == 0 and total_balance > 0:
            self.coord_state[init_key] = total_balance
            _atomic_save_json(COORDINATOR_STATE, self.coord_state)

        # 檢查 global circuit breaker（用返同 scope 嘅 initial balance 比較）
        breaker_reason = self._check_breaker(flow, total_balance, scope_key=init_key)
        if breaker_reason:
            print(f"  🔒 GLOBAL CIRCUIT BREAKER TRIPPED: {breaker_reason}")
            print(f"  ⏭️  跳過全部 engine scan")
            # 全部 engine 標記為 circuit_breaker，唔 scan
            for name in names:
                if name in self.engines:
                    status = self.engines[name].status()
                    results[name] = {
                        "status": "circuit_breaker",
                        "reason": breaker_reason,
                        **status,
                    }
            return results, 0, 0, flow

        for name in names:
            if name not in self.engines:
                print(f"  ⚠️ Unknown engine: {name}")
                continue
            engine = self.engines[name]
            status = engine.status()
            print(f"  [{name:8s}] paused={status['paused']} open={status['positions']} "
                  f"bal=${status['balance']:.2f} pnl=${status.get('pnl_raw',0):+.2f}")

            if status["paused"]:
                results[name] = {"status": "paused", **status}
                continue

            try:
                scan_result = engine.scan()
                results[name] = {
                    "status": "scanned",
                    "signals": scan_result.get("signals_found", 0),
                    "executed": scan_result.get("executed", 0),
                    "signals_detail": scan_result.get("signals", []),
                    **engine.status(),
                }
                total_signals += scan_result.get("signals_found", 0)
                total_executed += scan_result.get("executed", 0)

                if scan_result.get("executed"):
                    sig = scan_result.get("signals", [{}])[0]
                    print(f"    ✅ NEW TRADE: {sig.get('direction')} @ ${sig.get('entry', 0):.2f} "
                          f"| {sig.get('strategy')} | {sig.get('reason')}")

            except Exception as e:
                results[name] = {"status": "error", "error": str(e), **engine.status()}
                print(f"    ❌ Engine error: {e}")

        return results, total_signals, total_executed, flow

    def _run_flow_monitor(self) -> dict:
        """Run flow monitor and return flow data."""
        print(f"\n🌊 Flow Monitor...")
        try:
            flow = flow_monitor.run_flow_monitor()
            return flow
        except Exception as e:
            print(f"  ⚠️ Flow monitor failed: {e}")
            return {}

    def _read_flow_context(self) -> dict:
        """Read existing flow context without running full monitor."""
        try:
            flow = flow_monitor.get_latest_flow()
            return flow if flow else {}
        except Exception:
            return {}

    def run_scan(self) -> dict:
        """Run ALL 5 engines + flow monitor — unified scan, every 5 minutes."""
        flow = self._run_flow_monitor()
        engine_names = ["btc", "eth", "gold", "indices", "equities"]
        label = "FULL SCAN"

        # ── GAP 1: Load regime_config.json and apply overrides ──
        regime_path = os.path.join(LOG_DIR, "regime_config.json")
        regime_cfg = {}
        if os.path.exists(regime_path):
            file_age_days = (time.time() - os.path.getmtime(regime_path)) / 86400
            if file_age_days <= 7:
                try:
                    with open(regime_path, "r") as f:
                        regime_cfg = json.load(f)
                    print(f"  📋 Regime config loaded ({file_age_days:.1f} days old)")
                except Exception as e:
                    print(f"  ⚠️ Regime config load error: {e}")
            else:
                print(f"  ⏸️ Regime config stale ({file_age_days:.1f} days > 7) — using defaults")
        else:
            print(f"  ⏸️ No regime_config.json — using defaults")

        # Apply global overrides
        global_overrides = regime_cfg.get("global_overrides", {}) if regime_cfg else {}
        global_risk_mult = global_overrides.get("global_risk_multiplier", 1.0)
        global_max_exp = global_overrides.get("global_max_exposure_pct")

        # ── Smart Money vs Regime Contradiction Detection ──
        sm_contradiction = None
        try:
            from collectors.smart_money_collector import get_context as get_sm
            sm = get_sm()
            sm_whales = sm.get("whales", {})
            # Use BTC as primary regime proxy since it leads the market
            btc_sm = sm_whales.get("BTC", {})
            if btc_sm:
                btc_ls = btc_sm.get("ls_ratio", 0.5)
                btc_long_profit = btc_sm.get("long_profit_pct", 50)

                # Regime bullish (mult > 1.0) but whales extreme short → contradiction
                if global_risk_mult > 1.0 and btc_ls < 0.30 and btc_long_profit < 40:
                    sm_contradiction = "bearish"
                    global_risk_mult = min(global_risk_mult, 0.7)
                    print(f"  🐋 Smart Money CONTRADICTION: regime bullish but whales extreme short "
                          f"(L/S={btc_ls:.2f}, L_win={btc_long_profit:.0f}%) → risk ×{global_risk_mult}")

                # Regime bearish (mult < 1.0) but whales accumulating long → contradiction
                elif global_risk_mult < 1.0 and btc_ls > 1.5 and btc_long_profit > 60:
                    sm_contradiction = "bullish"
                    global_risk_mult = max(global_risk_mult, 0.8)
                    print(f"  🐋 Smart Money CONTRADICTION: regime bearish but whales accumulating "
                          f"(L/S={btc_ls:.2f}, L_win={btc_long_profit:.0f}%) → risk floor ×{global_risk_mult}")

                # Also check ETH for confirmation
                eth_sm = sm_whales.get("ETH", {})
                if eth_sm and sm_contradiction:
                    eth_ls = eth_sm.get("ls_ratio", 0.5)
                    if (sm_contradiction == "bearish" and eth_ls > 0.40) or \
                       (sm_contradiction == "bullish" and eth_ls < 0.80):
                        # ETH doesn't confirm the contradiction → reduce adjustment
                        global_risk_mult = 1.0 if sm_contradiction == "bearish" else global_risk_mult
                        print(f"  🐋 ETH doesn't confirm → contradiction weakened")
        except Exception:
            pass  # Collector unavailable — skip contradiction check

        if global_risk_mult != 1.0:
            print(f"  🌍 Global risk multiplier: {global_risk_mult}x")
        if global_max_exp is not None:
            print(f"  🌍 Global max exposure: {global_max_exp*100:.0f}%")

        # Apply per-engine regime config
        per_engine = regime_cfg.get("engines", {}) if regime_cfg else {}
        for name in engine_names:
            if name in self.engines:
                engine = self.engines[name]

                # Global risk multiplier
                if global_risk_mult != 1.0:
                    engine.RISK_PER_TRADE_PCT = min(engine.RISK_PER_TRADE_PCT * global_risk_mult, 0.10)

                # Global max exposure
                if global_max_exp is not None:
                    engine.MAX_EXPOSURE_PCT = global_max_exp

                # Per-engine overrides
                engine_cfg = per_engine.get(name, {})
                if engine_cfg:
                    engine.apply_regime_overrides(engine_cfg)

        # ── Smart Money context (whale positioning) ──
        try:
            from collectors.smart_money_collector import get_context
            smart_money = get_context()
            sm_signals = smart_money.get("signals", [])
            if sm_signals:
                print(f"  🤑 Smart Money: {len(sm_signals)} signals")
                for s in sm_signals:
                    print(f"    [{s['severity']:7s}] {s['symbol']:4s} {s['type']:20s} {s['detail'][:60]}")
            flow["smart_money"] = smart_money
        except Exception as e:
            print(f"  ⚠️ Smart money collector unavailable: {e}")

        results, total_signals, total_executed, _ = self._run_engines(
            engine_names, label
        )
        summary = self._build_summary(flow, results, total_signals, total_executed)
        self._print_summary(summary)
        self._save_scan_report(results, total_signals, total_executed, flow, summary, "FULL_SCAN")
        return {"flow": flow, "engines": results, "summary": summary}

    def run_status(self) -> dict:
        """Quick status check across all engines."""
        flow = flow_monitor.get_latest_flow()

        print("=" * 50)
        print("🎯 ENGINE STATUS DASHBOARD")
        print("=" * 50)

        total_balance = 0
        total_pnl = 0
        total_pnl_adj = 0
        total_positions = 0
        paused_count = 0

        for name, engine in self.engines.items():
            s = engine.status()
            total_balance += s["balance"]
            total_pnl += s["pnl_raw"]
            total_pnl_adj += s.get("pnl_adjusted", s["pnl_raw"])
            total_positions += s["positions"]
            if s["paused"]:
                paused_count += 1

            status_icon = "⏸️" if s["paused"] else "▶️"
            print(f"  {status_icon} [{name:8s}] bal=${s['balance']:.2f} "
                  f"pnl=${s['pnl_raw']:+.2f} | {s['positions']} open | "
                  f"trades={s['total_trades']}")

            for p in s.get("positions_detail", []):
                print(f"     {p['ticker']} {p['direction']} "
                      f"@{p['entry']:.2f} → {p['current']:.2f} "
                      f"| ${p['pnl_amt']:+.2f}")

        total_engines = len(self.engines)
        print(f"\n  💰 TOTAL: ${total_balance:.2f} | Raw P&L: ${total_pnl:+.2f} | "
              f"Adj P&L: ${total_pnl_adj:+.2f} | "
              f"Open: {total_positions} | Paused: {paused_count}/{total_engines}")

        if flow:
            print(f"  🌊 VIX: {flow.get('vix', '?')} ({flow.get('vix_regime', '?')}) | "
                  f"BTC Dom: {flow.get('btc_dominance', '?')}%")
            if flow.get("signals"):
                print(f"  ⚠️ Flow: {', '.join(flow['signals'])}")

        return {"engines": {n: e.status() for n, e in self.engines.items()}, "flow": flow}

    def run_report(self) -> dict:
        """Generate cross-market report for daily briefing."""
        flow = flow_monitor.get_latest_flow()
        engine_statuses = {n: e.status() for n, e in self.engines.items()}

        summary = self._build_report_summary(flow, engine_statuses)
        self._print_report(summary)
        return summary

    def _save_scan_report(self, results, total_signals, total_executed, flow, summary, label):
        """Save scan report to disk and update coordinator state (atomic write)."""
        now = datetime.now(HKT)
        self.coord_state["total_scans"] += 1
        self.coord_state["total_signals"] += total_signals
        self.coord_state["total_executed"] += total_executed
        self.coord_state["last_scan"] = now.isoformat()
        _atomic_save_json(COORDINATOR_STATE, self.coord_state)

        scan_report = {
            "timestamp": now.isoformat(),
            "mode": label,
            "flow": flow,
            "engines": results,
            "summary": summary,
        }
        report_file = os.path.join(
            LOG_DIR,
            f"scan_{label.lower().replace(' ','_')}_{now.strftime('%Y%m%d_%H%M')}.json"
        )
        _atomic_save_json(report_file, scan_report)

    def _build_summary(self, flow, results, signals, executed):
        """Build cross-market summary."""
        engines_active = sum(
            1 for r in results.values()
            if r.get("status") != "paused"
        )

        return {
            "engines_active": engines_active,
            "engines_total": len(self.engines),
            "signals_found": signals,
            "signals_executed": executed,
            "vix_regime": flow.get("vix_regime", "unknown"),
            "btc_dominance": flow.get("btc_dominance", 0),
            "flow_signals": flow.get("signals", []),
            "risk_environment": self._assess_risk(flow),
        }

    def _assess_risk(self, flow) -> str:
        """Assess overall risk environment."""
        vix = flow.get("vix", 0)
        signals = flow.get("signals", [])

        if vix > 30 or "VIX_FEAR" in signals:
            return "HIGH_RISK — 全市場恐懼，建議減倉"
        if vix > 25 or len(signals) >= 3:
            return "ELEVATED — 風險升高，嚴格止損"
        if vix < 15:
            return "COMPLACENCY — 低波動，小心假突破"
        return "NORMAL — 正常風險環境"

    def _print_summary(self, summary):
        print(f"\n  🎯 Active engines: {summary['engines_active']}/{summary['engines_total']}")
        print(f"  📡 Signals: {summary['signals_found']} | Executed: {summary['signals_executed']}")
        print(f"  🌊 VIX: {summary['vix_regime']} | BTC Dom: {summary['btc_dominance']}%")
        print(f"  ⚠️ Risk: {summary['risk_environment']}")
        if summary['flow_signals']:
            print(f"  🚨 Flow alerts: {', '.join(summary['flow_signals'])}")

    def _build_report_summary(self, flow, engine_statuses):
        """Build detailed report for daily briefing (incl. slippage-adjusted P&L)."""
        total_pnl = sum(s["pnl_raw"] for s in engine_statuses.values())
        total_pnl_adjusted = sum(
            s.get("total_pnl_adjusted", s["pnl_raw"]) for s in engine_statuses.values()
        )
        total_balance = sum(s["balance"] for s in engine_statuses.values())

        markets = []
        for name, s in engine_statuses.items():
            markets.append({
                "name": name,
                "balance": s["balance"],
                "pnl": s["pnl_raw"],
                "pnl_adjusted": s.get("total_pnl_adjusted", s["pnl_raw"]),
                "positions": s["positions"],
                "paused": s["paused"],
                "total_trades": s["total_trades"],
                "consec_losses": s.get("consec_losses", 0),
                "best_strategy": s["best_strategy"],
            })

        return {
            "total_balance": total_balance,
            "total_pnl": total_pnl,
            "total_pnl_adjusted": total_pnl_adjusted,
            "markets": markets,
            "flow": {
                "vix": flow.get("vix"),
                "vix_regime": flow.get("vix_regime"),
                "btc_dominance": flow.get("btc_dominance"),
                "eth_btc": flow.get("eth_btc_ratio"),
                "signals": flow.get("signals", []),
            },
            "risk_assessment": self._assess_risk(flow),
        }

    def _print_report(self, report):
        print("=" * 60)
        print(f"📊 CROSS-MARKET REPORT — {datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}")
        print("=" * 60)
        print(f"\n💰 Total Balance: ${report['total_balance']:.2f} | "
              f"Raw P&L: ${report['total_pnl']:+.2f} | "
              f"Adj P&L: ${report.get('total_pnl_adjusted', report['total_pnl']):+.2f}")
        print(f"⚠️  Risk: {report['risk_assessment']}")
        print(f"\n📂 Per Market:")
        for m in report["markets"]:
            icon = "⏸️" if m["paused"] else "▶️"
            pnl_adj = m.get("pnl_adjusted", m["pnl_raw"])
            slippage_cost = m["pnl_raw"] - pnl_adj
            print(f"  {icon} {m['name']:8s} bal=${m['balance']:.2f} "
                  f"pnl=${m['pnl']:+.2f} (adj=${pnl_adj:+.2f}, "
                  f"slippage=${slippage_cost:.2f}) | {m['positions']} open | "
                  f"{m['total_trades']} trades")
        print(f"\n🌊 Flow Snapshot:")
        f = report["flow"]
        print(f"  VIX: {f.get('vix', '?')} ({f.get('vix_regime', '?')})")
        print(f"  BTC Dominance: {f.get('btc_dominance', '?')}%")
        print(f"  ETH/BTC: {f.get('eth_btc', '?')}")
        if f.get("signals"):
            print(f"  🚨 {', '.join(f['signals'])}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--scan"

    coord = EngineCoordinator()

    if mode == "--scan":
        coord.run_scan()
    elif mode == "--status":
        coord.run_status()
    elif mode == "--report":
        coord.run_report()
    else:
        print("Usage: engine_coordinator.py [--scan|--status|--report]")
