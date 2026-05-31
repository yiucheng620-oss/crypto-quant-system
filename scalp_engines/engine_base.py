#!/usr/bin/env python3
"""
⚡ SCALP ENGINE BASE V2 — Intraday Swing 基底引擎
===================================================
Pivot from Scalping → Intraday Swing (5-15min timeframe).
Incorporates fixes from Gemini 3.5 Flash + 3.1 Pro audit.

Key changes from V1:
  - Atomic writes (os.replace) for all JSON state files
  - interpret_flow_bias() hook — per-engine, not global一刀切
  - Time-based auto-pause: 48hr rolling window drawdown, not consecutive losses
  - One-time slippage: only on close transition, not repeated every scan
  - P&L Attribution: recorded at ENTRY, not close (因果正確)
  - Trailing stop infrastructure: track high/low, update Hard SL via API
  - ATR-based dynamic position sizing ready
  - TTL check on flow data (expire after 1hr)
  - Edge weights: dynamic signal filtering from regime_config
"""

import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any

# ── TG Notification ────────────────────────────────────────
try:
    from tg_notify import tg_trade_open, tg_trade_close, tg_circuit_breaker
except ImportError:
    tg_trade_open = tg_trade_close = tg_circuit_breaker = lambda *a, **kw: None

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENGINES_DIR = os.path.join(WORKSPACE, "scalp_engines")
LOG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
os.makedirs(LOG_DIR, exist_ok=True)

sys.path.insert(0, WORKSPACE)
from capitalcom_paper import (
    open_position, check_positions, close_position, get_account_balance,
    ACCOUNTS, get_account_for, get_market_info,
    MARKET_ACCOUNT, EPIC_MAP, _auth, _headers,
)

FLOW_FILE = os.path.join(LOG_DIR, "flow_state.json")


# ==================== ATOMIC I/O ====================

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


# ==================== LOCKED I/O (for shared flow_state.json) ====================

def _locked_save_json(path: str, data: Any):
    """
    Atomic write with file locking via fcntl.flock.
    Prevents concurrent write races between processes.
    Creates/acquires path+.lock with exclusive lock (LOCK_EX).
    """
    lock_path = path + ".lock"
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def _locked_load_json(path: str, default: Any = None) -> Any:
    """
    Read JSON with shared file locking via fcntl.flock (LOCK_SH).
    Creates lock file if it doesn't exist yet ('a' mode).
    Falls back to regular load on error.
    """
    lock_path = path + ".lock"
    try:
        with open(lock_path, "a") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_SH)
            try:
                return _load_json(path, default)
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    except Exception:
        return _load_json(path, default)


# ==================== SWING ENGINE BASE ====================

class SwingEngine:
    """Base class for all intraday swing engines."""

    # === Subclass MUST override ===
    MARKET: str = ""
    EPIC: str = ""
    INITIAL_BALANCE: float = 1000.0
    ACCOUNT_KEY: str = ""

    # === Swing risk params (wider than scalp) ===
    MAX_POSITIONS: int = 1
    MAX_EXPOSURE_PCT: float = 0.70
    RISK_PER_TRADE_PCT: float = 0.02       # 2% of balance per trade
    STOP_LOSS_PCT: float = 0.03             # Wider: 3% (was 2%) — 減少被 noise whipsaw
    TAKE_PROFIT_PCT: float = 0.05           # 1:1.67 RR target
    TRAILING_ACTIVATION_PCT: float = 0.02   # Start trailing after 2% profit
    TRAILING_DISTANCE_PCT: float = 0.015    # Trail 1.5% behind price
    TIME_STOP_MINUTES: int = 240            # 4 hours max hold (crypto)
    MAX_48H_LOSS_PCT: float = 0.15          # 15% drawdown in 48h → pause

    # === Slippage by account type ===
    SLIPPAGE_PCT: float = 0.0005  # default 0.05%, overridden per account
    SLIPPAGE_MAP = {"crypto": 0.0005, "gold": 0.0003, "indices": 0.0003, "equities": 0.0002}

    def __init__(self, name: str = None):
        self.name = name or self.MARKET
        self.account_id = get_account_for(self.MARKET)
        if not self.ACCOUNT_KEY:
            self.ACCOUNT_KEY = next(
                (k for k, v in ACCOUNTS.items() if v == self.account_id), "crypto"
            )
        self.slippage_pct = self.SLIPPAGE_MAP.get(self.ACCOUNT_KEY, 0.0005)

        # State files
        self.state_file = os.path.join(LOG_DIR, f"{self.name}_state.json")
        self.trade_file = os.path.join(LOG_DIR, f"{self.name}_trades.json")
        self.signal_file = os.path.join(LOG_DIR, f"{self.name}_signals.json")

        # Load state
        self.state = _load_json(self.state_file, {
            "total_trades": 0,
            "total_pnl_raw": 0.0,
            "total_pnl_adjusted": 0.0,
            "paused": False,
            "pause_reason": "",
            "best_strategy": None,
            "strategy_stats": {},
            "trade_history": [],  # lightweight summary for 48hr window
        })
        self.trades = _load_json(self.trade_file, [])

        # === Strategy Config: load from unified JSON (overrides class defaults) ===
        self._apply_strategy_config()

        # === Edge weights: dynamic signal filtering from regime_config ===
        self.edge_weights = {}  # default empty = all edges at 1.0

    def _apply_strategy_config(self):
        """Load engine parameters from strategy_config.json.
        
        Class-level defaults act as fallback. Config JSON values override them.
        This is the single source of truth for all tunable parameters.
        """
        config_path = os.path.join(LOG_DIR, "strategy_config.json")
        if not os.path.exists(config_path):
            return  # No config file → use class defaults

        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, Exception):
            return

        engine_cfg = cfg.get("engines", {}).get(self.name, {})
        if not engine_cfg:
            return

        param_map = {
            "risk_per_trade_pct": "RISK_PER_TRADE_PCT",
            "stop_loss_pct": "STOP_LOSS_PCT",
            "take_profit_pct": "TAKE_PROFIT_PCT",
            "trailing_activation_pct": "TRAILING_ACTIVATION_PCT",
            "trailing_distance_pct": "TRAILING_DISTANCE_PCT",
            "time_stop_minutes": "TIME_STOP_MINUTES",
            "max_exposure_pct": "MAX_EXPOSURE_PCT",
            "max_positions": "MAX_POSITIONS",
            "max_consec_losses": "MAX_CONSEC_LOSSES",
            # Engine-specific params (also in base so BTC/ETH get them without subclass map)
            "stop_loss_atr_mult": "STOP_LOSS_ATR_MULT",
            "oi_delta_long_threshold": "OI_DELTA_LONG_THRESHOLD",
            "oi_delta_short_threshold": "OI_DELTA_SHORT_THRESHOLD",
        }
        engine_map = self._get_engine_param_map()
        param_map.update(engine_map)

        for json_key, class_attr in param_map.items():
            if json_key in engine_cfg:
                setattr(self, class_attr, engine_cfg[json_key])

    def _get_engine_param_map(self) -> dict:
        """Override: return {json_key: class_attr} for engine-specific params."""
        return {}

    # ==================== EDGE WEIGHTS (dynamic signal filtering) ====================

    def apply_edge_weights(self, weights: dict):
        """Set edge weights for dynamic signal filtering.

        Args:
            weights: dict of {edge_name: float} where 0.0 = skip, 1.0 = full weight.
                     Default empty = all edges at 1.0 (normal operation).
        """
        self.edge_weights = weights if isinstance(weights, dict) else {}
        print(f"  ⚖️ [{self.name}] Edge weights applied: {self.edge_weights}")

    # ==================== REGIME OVERRIDES (from regime_config.json) ====================

    def apply_regime_overrides(self, engine_cfg: dict):
        """Apply per-engine regime config overrides.

        Handles:
          - edge_weights: dict of {edge_name: weight}
          - risk_overrides: dict with risk_per_trade_pct, max_exposure_pct, etc.
          - bias: string direction override ("LONG", "SHORT", or None)

        Args:
            engine_cfg: dict from regime_config.json for this engine
        """
        if not isinstance(engine_cfg, dict):
            return

        # Edge weights
        if "edge_weights" in engine_cfg and isinstance(engine_cfg["edge_weights"], dict):
            self.apply_edge_weights(engine_cfg["edge_weights"])

        # Risk overrides
        risk = engine_cfg.get("risk_overrides", {})
        if isinstance(risk, dict):
            risk_map = {
                "risk_per_trade_pct": "RISK_PER_TRADE_PCT",
                "max_exposure_pct": "MAX_EXPOSURE_PCT",
                "stop_loss_pct": "STOP_LOSS_PCT",
                "take_profit_pct": "TAKE_PROFIT_PCT",
                "max_positions": "MAX_POSITIONS",
            }
            for json_key, class_attr in risk_map.items():
                if json_key in risk:
                    setattr(self, class_attr, risk[json_key])

        # Bias override (stored for generate_signal to use if needed)
        bias = engine_cfg.get("bias")
        if bias in ("LONG", "SHORT"):
            self._regime_bias = bias
            print(f"  🧭 [{self.name}] Regime bias set: {bias}")
        else:
            self._regime_bias = None

    # ==================== DATA (override) ====================

    def get_market_data(self) -> Dict:
        """Override: fetch market-specific data."""
        raise NotImplementedError

    def generate_signal(self, data: Dict) -> Optional[Dict]:
        """Override: generate trade signal. Access flow context via data['_flow']."""
        raise NotImplementedError

    # ==================== FLOW CONTEXT ====================

    def get_flow_context(self) -> Dict:
        """
        Read cross-market flow data. TTL check: expire after 1 hour.
        Calls interpret_flow_bias() hook — overridden per engine.
        Also pulls Smart Money whale positioning data for risk management.
        """
        default = {
            "raw_data": {}, "bias_adjustment": {"LONG": 0, "SHORT": 0},
            "vix": 0, "vix_regime": "unknown",
        }
        
        # ── Integration Trace: Layer 1 (Live VIX/DXY Data Source) ──
        # Fix stale data gap by fetching VIX & DXY directly if file is stale or missing.
        try:
            import yfinance as yf
            import requests as req
            # Fallback direct fetch logic
            vix_live = 0
            vix_tick = yf.Ticker("^VIX")
            vix_hist = vix_tick.history(period="1d")
            if not vix_hist.empty:
                vix_live = float(vix_hist["Close"].iloc[-1])
        except Exception as e:
            vix_live = 0
            
        try:
            if not os.path.exists(FLOW_FILE):
                default["vix"] = vix_live
                return default
            file_age = time.time() - os.path.getmtime(FLOW_FILE)
            if file_age > 3600:
                print(f"  ⚠️ Flow data expired ({file_age/3600:.1f}h), using live VIX fallback")
                default["vix"] = vix_live
                return default
            raw = _locked_load_json(FLOW_FILE, {})
            bias = self.interpret_flow_bias(raw)
            ctx = {
                "raw_data": raw,
                "bias_adjustment": bias,
                "vix": raw.get("vix", vix_live),
                "vix_regime": raw.get("vix_regime", "unknown"),
                "btc_dominance": raw.get("btc_dominance", 0),
                "flow_signals": raw.get("signals", []),
            }
            # ── Smart Money whale positioning ──
            try:
                from collectors.smart_money_collector import get_context as get_sm
                sm = get_sm()
                ctx["smart_money"] = sm
            except Exception:
                pass  # Collector unavailable — engine works without it
            return ctx
        except Exception as e:
            print(f"  ⚠️ Flow context error: {e}, using neutral")
            return default

    def interpret_flow_bias(self, flow_data: Dict) -> Dict:
        """
        HOOK: Per-engine flow interpretation.
        Base = NEUTRAL. Subclasses override for their market logic.
        Returns {"LONG": int, "SHORT": int}.
        """
        return {"LONG": 0, "SHORT": 0}

    # ==================== POSITIONS ====================

    def get_balance(self) -> Dict:
        return get_account_balance(self.account_id)

    def get_positions(self) -> List[Dict]:
        all_positions = check_positions(self.account_id)
        epics = self._get_epics()
        return [p for p in all_positions if p.get("epic") in epics]

    def _get_epics(self) -> set:
        return {self.EPIC}

    def count_open(self) -> int:
        return len(self.get_positions())

    def is_paused(self) -> bool:
        paused = self.state.get("paused", False)
        if not paused:
            return False
        # ── Auto-resume: clear weekend pause when it's no longer the weekend ──
        reason = self.state.get("pause_reason", "")
        if "weekend" in reason.lower() or "sunday" in reason.lower() or "saturday" in reason.lower():
            now = datetime.now(HKT)
            if now.weekday() < 5:  # Monday-Friday
                self.state["paused"] = False
                self.state["pause_reason"] = ""
                self.state["paused_at"] = None
                _atomic_save_json(self.state_file, self.state)
                return False
        return True

    # ==================== CIRCUIT BREAKER (48hr rolling) ====================

    def check_48hr_breaker(self) -> bool:
        """
        Intraday swing breaker: check if 48hr rolling P&L exceeds limit.
        Natural time-decay: losses older than 48hr fall out of window.
        """
        now = time.time()
        cutoff = now - (48 * 3600)
        recent_pnl = 0.0

        for t in self.trades:
            if t.get("status") != "closed":
                continue
            closed_ts = t.get("closed_at_ts", 0)
            if closed_ts >= cutoff:
                recent_pnl += t.get("adjusted_pnl", t.get("pnl", 0))

        max_loss = self.INITIAL_BALANCE * self.MAX_48H_LOSS_PCT * -1
        if recent_pnl <= max_loss:
            self.state["paused"] = True
            self.state["pause_reason"] = (
                f"48hr rolling P&L ${recent_pnl:.2f} ≤ -${abs(max_loss):.2f}"
            )
            _atomic_save_json(self.state_file, self.state)
            print(f"  🚨 BREAKER: {self.state['pause_reason']}")
            # ── TG通知：Circuit Breaker ──
            try:
                tg_circuit_breaker(self.MARKET, self.state["pause_reason"])
            except Exception:
                pass
            return True

        # Auto-release if P&L recovers
        if self.state.get("paused") and recent_pnl > max_loss * 0.5:
            self.state["paused"] = False
            self.state["pause_reason"] = ""
            _atomic_save_json(self.state_file, self.state)
            print(f"  ✅ BREAKER released — 48hr P&L ${recent_pnl:.2f}")

        return self.state.get("paused", False)

    # ==================== EXECUTION ====================

    def execute_signal(self, signal: Dict) -> Dict:
        """Execute trade on Capital.com demo. One-time entry attribution."""
        if not signal:
            return {"executed": False, "reason": "no_signal"}

        if self.check_48hr_breaker():
            return {"executed": False, "reason": "circuit_breaker"}

        existing = self.get_positions()
        if len(existing) >= self.MAX_POSITIONS:
            return {"executed": False, "reason": f"max_positions ({len(existing)}/{self.MAX_POSITIONS})"}
        if any(p.get("epic") == signal.get("epic", self.EPIC) for p in existing):
            return {"executed": False, "reason": f"{self.MARKET} already open"}

        bal = self.get_balance()
        if not bal or "balance" not in bal:
            return {"executed": False, "reason": "balance_api_failed_empty"}
        balance = bal.get("balance", self.INITIAL_BALANCE)

        # Validate: balance not zero (circuit breaker guard)
        if balance <= 0:
            return {"executed": False, "reason": "zero_balance_api_error"}

        total_exposure = sum(
            p.get("size", 0) * p.get("current", p.get("entry", 0))
            for p in existing
        )
        signal_exposure = balance * self.RISK_PER_TRADE_PCT * 10
        if total_exposure + signal_exposure > balance * self.MAX_EXPOSURE_PCT:
            return {"executed": False, "reason": "exposure_limit"}

        risk_mult = signal.get("risk_mult", 1.0)
        
        # ── 纏論 Chanlun Confidence Multiplier ──
        try:
            tracker_file = os.path.expanduser("~/workspace/chanlun_system/data/signals/tracker.json")
            if os.path.exists(tracker_file):
                import json
                with open(tracker_file) as f:
                    cl_data = json.load(f)
                active_cl = cl_data.get("active_signals", [])
                for cl in active_cl:
                    if cl.get("symbol") == self.MARKET and signal.get("direction") == "LONG":
                        print(f"  📈 [{self.MARKET}] Chanlun 1买 signal active! Applying 1.2x confidence multiplier.")
                        risk_mult *= 1.2
                        break
        except Exception:
            pass

        risk_amount = balance * self.RISK_PER_TRADE_PCT * risk_mult
        entry = signal["entry"]
        stop = signal["stop"]
        # 🔒 HARD ENFORCEMENT: never allow SL=$0 or SL=entry
        if stop <= 0 or stop == entry:
            forced_sl_pct = getattr(self, 'STOP_LOSS_PCT', 0.02)
            if signal["direction"] == "LONG":
                stop = round(entry * (1 - forced_sl_pct), 2)
            else:
                stop = round(entry * (1 + forced_sl_pct), 2)
            print(f"  ⚠️ [{self.MARKET}] SL was \$0 — forced to \${stop:.2f} ({forced_sl_pct*100:.1f}%)")
            signal["stop"] = stop
        risk_per_unit = abs(entry - stop) if signal["direction"] == "LONG" else abs(stop - entry)
        if risk_per_unit <= 0:
            risk_per_unit = entry * 0.005
        if risk_per_unit <= 0:
            return {"executed": False, "reason": "zero_risk_per_unit"}
        size = max(round(risk_amount / risk_per_unit, 4), 0.01)

        result = open_position(
            ticker=signal.get("label", self.MARKET),
            direction=signal["direction"],
            entry=entry, stop=stop,
            target=signal["target"],
            market=self.ACCOUNT_KEY,
            size=size,
        )
        if "error" in result:
            return {"executed": False, "reason": result["error"]}

        now = datetime.now(HKT)
        now_ts = time.time()

        # 🔧 用 Capital.com 真實 fill price，唔係 signal 建議價
        actual_entry = result.get("entry", entry)
        actual_deal_id = result.get("deal_id", "?")
        is_paper_only = result.get("paper_only", False)

        trade = {
            "timestamp": now.isoformat(),
            "timestamp_ts": now_ts,
            "market": self.MARKET, "engine": self.name,
            "strategy": signal.get("strategy", "swing"),
            "epic": signal.get("epic", self.EPIC),
            "direction": signal["direction"],
            "entry": actual_entry, "stop": stop, "target": signal["target"],
            "size": size,
            "deal_ref": result.get("deal_ref", "?"),
            "deal_id": actual_deal_id,          # "paper_only" for virtual trades
            "status": "open", "pnl": 0.0, "adjusted_pnl": 0.0,
            "raw_pnl": 0.0, "slippage_cost": 0.0,
            "slippage_applied": False,
            "exit_price": 0.0, "closed_at": "", "closed_at_ts": 0,
            "account": self.ACCOUNT_KEY,
            "reason": signal.get("reason", ""),
            "highest_price": actual_entry,   # for trailing stop (LONG)
            "lowest_price": actual_entry,     # for trailing stop (SHORT)
            "trailing_active": False,
            "time_stop_minutes": self.TIME_STOP_MINUTES,
            "paper_only": is_paper_only,       # ⚠️ Capital.com 無此資產 → 純虛擬 tracking
            "paper_reason": result.get("paper_reason", "") if is_paper_only else "",
            "reconciled": is_paper_only,       # paper_only trades auto-reconciled (唔使 match API)
            # === Attribution recorded at ENTRY (因果正確) ===
            "entry_attribution": {
                "vix_at_entry": signal.get("_entry_vix", 0),
                "vix_regime": signal.get("_entry_vix_regime", "unknown"),
                "btc_dominance": signal.get("_entry_btc_dom", 0),
                "entry_hour_hkt": now.hour,
                "entry_day": now.strftime("%A"),
            },
        }
        self.trades.append(trade)
        _atomic_save_json(self.trade_file, self.trades)

        # ── TG通知：開倉 ──
        try:
            tg_trade_open(
                self.MARKET, signal["direction"], entry,
                stop, signal["target"],
                signal.get("reason", ""), signal.get("strategy", "")
            )
        except Exception:
            pass

        return {"executed": True, "trade": trade}

    # ==================== TRAILING STOP ====================

    def update_trailing_stops(self):
        """Check open positions and move SL if trailing condition met."""
        positions = self.get_positions()
        if not positions:
            return

        try:
            import sys
            sys.path.append(os.path.join(os.path.expanduser("~"), "workspace", "capitalcom"))
            from capitalcom_paper import update_stop_loss
        except ImportError:
            update_stop_loss = None

        for t in self.trades:
            if t["status"] != "open":
                continue
            if t.get("reconciled"):
                continue  # ⛔ Never touch manually reconciled trades

            # Find matching position
            pos = next(
                (p for p in positions if p.get("deal_id") == t.get("deal_id")),
                None
            )
            if not pos:
                continue

            current_price = float(pos.get("current", pos.get("entry", 0)))
            if current_price <= 0:
                continue
            entry_price = t["entry"]
            direction = t["direction"]
            target = t.get("target", 0)

            # Update high/low water mark
            if direction == "LONG":
                if current_price > t["highest_price"]:
                    t["highest_price"] = current_price
            else:
                if current_price < t["lowest_price"]:
                    t["lowest_price"] = current_price

            new_stop = t["stop"]  # default: no change
            old_stop = t["stop"]
            
            # --- 1. Break-Even Trailing (Free Ride) ---
            is_break_even = False
            if target > 0:
                target_dist = abs(target - entry_price)
                current_dist = current_price - entry_price if direction == "LONG" else entry_price - current_price
                if target_dist > 0 and (current_dist / target_dist) >= 0.5:
                    is_break_even = True
                    buffer = entry_price * 0.0005
                    new_stop = entry_price + buffer if direction == "LONG" else entry_price - buffer
            
            # --- 2. Standard Trailing ---
            if not is_break_even:
                if direction == "LONG":
                    pnl_pct = (current_price - entry_price) / entry_price
                    if pnl_pct >= self.TRAILING_ACTIVATION_PCT:
                        t["trailing_active"] = True
                        new_stop = t["highest_price"] * (1 - self.TRAILING_DISTANCE_PCT)
                else:
                    pnl_pct = (entry_price - current_price) / entry_price
                    if pnl_pct >= self.TRAILING_ACTIVATION_PCT:
                        t["trailing_active"] = True
                        new_stop = t["lowest_price"] * (1 + self.TRAILING_DISTANCE_PCT)

            if not is_break_even and not t.get("trailing_active"):
                continue

            # Move stop only if favorable
            stop_updated = False
            if direction == "LONG" and new_stop > old_stop:
                t["stop"] = round(new_stop, 4)
                stop_updated = True
                print(f"  📈 Trailing SL ↑: {self.MARKET} LONG ${old_stop:.2f} → ${t['stop']:.2f}")
            elif direction == "SHORT" and new_stop < old_stop:
                t["stop"] = round(new_stop, 4)
                stop_updated = True
                print(f"  📉 Trailing SL ↓: {self.MARKET} SHORT ${old_stop:.2f} → ${t['stop']:.2f}")
            elif is_break_even and old_stop != new_stop:
                # Force break even if it differs from current stop and hasn't been set yet
                # Ensure it's in the favorable direction compared to old stop
                if (direction == "LONG" and new_stop > old_stop) or (direction == "SHORT" and new_stop < old_stop):
                    t["stop"] = round(new_stop, 4)
                    stop_updated = True
                    print(f"  🛡️ Break-Even SL: {self.MARKET} {direction} ${old_stop:.2f} → ${t['stop']:.2f}")

            # ACTUALLY send to Capital.com API
            if stop_updated and update_stop_loss and t.get("deal_id"):
                try:
                    res = update_stop_loss(t["deal_id"], t["stop"], t.get("target"), self.account_id)
                    if "error" in res:
                        print(f"  ⚠️ API SL Update Failed: {res['error']}")
                    else:
                        print(f"  ✅ API SL Updated successfully for {t['deal_id']}")
                except Exception as e:
                    print(f"  ⚠️ API SL Update Error: {e}")

        _atomic_save_json(self.trade_file, self.trades)

    # ==================== TIME-BASED STOP ====================

    def check_time_stops(self):
        """Close positions that exceed max holding time."""
        now_ts = time.time()
        positions = self.get_positions()

        for t in self.trades:
            if t["status"] != "open":
                continue
            if t.get("reconciled"):
                continue  # ⛔ Never touch manually reconciled trades
            age_minutes = (now_ts - t["timestamp_ts"]) / 60
            time_limit = t.get("time_stop_minutes", self.TIME_STOP_MINUTES)

            if age_minutes >= time_limit:
                deal_id = t.get("deal_id", "")
                if deal_id:
                    print(f"  ⏰ Time stop: {self.MARKET} {t['direction']} held {age_minutes:.0f}min ≥ {time_limit}min")
                    result = close_position(deal_id, self.account_id)
                    if "error" not in result:
                        t["status"] = "closed"
                        t["closed_at"] = datetime.now(HKT).isoformat()
                        t["closed_at_ts"] = now_ts
                        t["close_reason"] = "time_stop"
                    else:
                        print(f"  ❌ Time stop close failed: {result['error']}")
                else:
                    t["status"] = "closed"
                    t["closed_at"] = datetime.now(HKT).isoformat()
                    t["closed_at_ts"] = now_ts
                    t["close_reason"] = "time_stop_no_deal_id"

        _atomic_save_json(self.trade_file, self.trades)

    # ==================== STATE MANAGEMENT ====================

    def update_closed(self):
        """Detect closed positions with re-verification against Capital.com API.

        TWO-WAY sync (v3 fix):
          1. API → JSON: if Capital.com shows a position that matches a JSON trade
             (by deal_id, or by epic+direction+entry price), mark it OPEN.
          2. JSON → API: if JSON trade is marked OPEN but Capital.com no longer
             shows it, AND paper_trades.json doesn't have it as open → mark CLOSED.

        Safety net: cross-checks paper_trades.json (master truth) before closing.
        This prevents the "engine thinks closed, Capital.com still open" desync.
        """
        positions = self.get_positions()
        open_ids = {str(p.get("deal_id", "")) for p in positions if p.get("deal_id")}

        # Build fallback match map: (epic, direction, rounded_entry) → position
        pos_by_key = {}
        for p in positions:
            epic = str(p.get("epic", ""))
            direction = str(p.get("direction", ""))
            entry = round(float(p.get("entry", 0)))
            if epic and direction and entry > 0:
                pos_by_key[(epic, direction, entry)] = p

        # ── Load paper_trades.json (master truth) for cross-check ──
        paper_deal_ids = set()
        paper_path = os.path.join(LOG_DIR, "paper_trades.json")
        try:
            if os.path.exists(paper_path):
                with open(paper_path) as f:
                    paper_trades = json.load(f)
                paper_deal_ids = {
                    str(t.get("deal_id", ""))
                    for t in paper_trades
                    if t.get("status") == "open" and t.get("deal_id") and t["deal_id"] != "?"
                }
        except (json.JSONDecodeError, OSError, Exception):
            pass  # paper_trades.json not available → continue without cross-check

        for t in self.trades:
            deal_id = str(t.get("deal_id", ""))
            direction = t.get("direction", "")
            entry_price = round(t.get("entry", 0))
            epic = t.get("epic", self.EPIC)  # Use trade's epic, fallback to engine EPIC
            
            # ⛔ Never touch reconciled trades EXCEPT to detect legitimate closes
            if t.get("reconciled"):
                # Reconciled but check if position still exists on API
                if deal_id and deal_id != "?" and deal_id not in open_ids:
                    # Check paper_trades.json too
                    if deal_id not in paper_deal_ids:
                        t["status"] = "closed"
                        t["close_reason"] = "closed_on_api"
                        t["closed_at"] = datetime.now(HKT).isoformat()
                        t["closed_at_ts"] = time.time()
                        print(f"  📝 [sync] {self.MARKET} reconciled {epic} closed on API → marked closed")
                continue

            # === 1. API → JSON: re-verify "closed" trades against live positions ===
            if t["status"] != "open":
                matched = False
                # Strategy A: exact deal_id match
                if deal_id and deal_id != "?" and deal_id in open_ids:
                    matched = True
                # Strategy B: fallback match by epic + direction + entry price
                if not matched:
                    key = (epic, direction, entry_price)
                    if key in pos_by_key:
                        matched = True

                if matched:
                    t["status"] = "open"
                    # Update deal_id if we found it via fallback
                    if deal_id == "?" or not deal_id:
                        fallback_pos = pos_by_key.get((epic, direction, entry_price))
                        if fallback_pos and fallback_pos.get("deal_id"):
                            t["deal_id"] = str(fallback_pos["deal_id"])
                    print(f"  🔄 [sync] {self.MARKET} {direction} re-opened (was incorrectly closed)")
                continue  # skip close detection for already-closed trades

            # === 2. JSON → API: detect truly closed positions ===
            is_still_open = False
            # Strategy A: exact deal_id
            if deal_id and deal_id != "?" and deal_id in open_ids:
                is_still_open = True
            # Strategy B: fallback match
            if not is_still_open:
                key = (epic, direction, entry_price)
                if key in pos_by_key:
                    is_still_open = True
                    # Update deal_id from live position
                    live_pos = pos_by_key[key]
                    if live_pos.get("deal_id"):
                        t["deal_id"] = str(live_pos["deal_id"])

            # 🔒 Safety net: check paper_trades.json before closing
            if not is_still_open and deal_id and deal_id in paper_deal_ids:
                is_still_open = True  # paper_trades.json says it's still open → don't close

            if not is_still_open:
                t["status"] = "closed"
                t["closed_at"] = datetime.now(HKT).isoformat()
                t["closed_at_ts"] = time.time()
                t["close_reason"] = t.get("close_reason", "tp_sl_hit")
                
                # Save exit price from engine's price history
                if not t.get("exit_price"):
                    price_hist = getattr(self, "price_history", [])
                    t["exit_price"] = price_hist[0] if price_hist else t.get("entry", 0)

                # One-time slippage calculation
                if not t.get("slippage_applied"):
                    raw_pnl = t.get("pnl", 0)
                    slippage_cost = t["entry"] * self.slippage_pct * t["size"]
                    t["raw_pnl"] = round(raw_pnl, 2)
                    t["slippage_cost"] = round(slippage_cost, 2)
                    t["adjusted_pnl"] = round(raw_pnl - slippage_cost, 2)
                    t["slippage_applied"] = True

                # Attribution snapshot at close time
                try:
                    flow = _locked_load_json(FLOW_FILE, {})
                    t["close_attribution"] = {
                        "vix_at_close": flow.get("vix", 0),
                        "vix_regime": flow.get("vix_regime", "unknown"),
                        "close_hour_hkt": datetime.now(HKT).hour,
                    }
                except Exception:
                    pass

                # ── TG通知：平倉（只發一次）──
                if not t.get("notified_close"):
                    try:
                        hold_min = (t["closed_at_ts"] - t["timestamp_ts"]) / 60
                        exit_price = t.get("exit_price", 0) or t.get("entry", 0)
                        tg_trade_close(
                            self.MARKET, t["direction"], t["entry"],
                            exit_price,
                            t.get("adjusted_pnl", t.get("pnl", 0)),
                            t.get("close_reason", "tp_sl_hit"),
                            hold_min
                        )
                        t["notified_close"] = True
                    except Exception:
                        pass

        _atomic_save_json(self.trade_file, self.trades)

    def update_state(self):
        """Update engine state with closed trade stats."""
        closed = [t for t in self.trades if t["status"] == "closed"]

        raw_total = sum(t.get("raw_pnl", t.get("pnl", 0)) for t in closed)
        adj_total = sum(t.get("adjusted_pnl", 0) for t in closed)

        self.state["total_trades"] = len(closed)
        self.state["total_pnl_raw"] = round(raw_total, 2)
        self.state["total_pnl_adjusted"] = round(adj_total, 2)

        # Best strategy: min 10 trades, Sharpe-like metric
        strats = {}
        for t in closed:
            s = t.get("strategy", "swing")
            if s not in strats:
                strats[s] = {"trades": 0, "wins": 0, "pnls": []}
            strats[s]["trades"] += 1
            strats[s]["pnls"].append(t.get("adjusted_pnl", 0))
            if t.get("adjusted_pnl", 0) > 0:
                strats[s]["wins"] += 1

        best = None
        best_score = -999
        for s, stats in strats.items():
            if stats["trades"] < 10:
                continue
            pnls = stats["pnls"]
            mean_pnl = sum(pnls) / len(pnls) if pnls else 0
            variance = sum((x - mean_pnl)**2 for x in pnls) / len(pnls) if pnls else 1
            std = variance ** 0.5 if variance > 0 else 1
            score = mean_pnl / std if std > 0 else mean_pnl
            stats["win_rate"] = round(stats["wins"] / stats["trades"] * 100, 1)
            stats["score"] = round(score, 3)
            if score > best_score:
                best_score = score
                best = s
            self.state["strategy_stats"][s] = stats

        if best:
            self.state["best_strategy"] = best

        _atomic_save_json(self.state_file, self.state)

    # ==================== ACTIVE EXITS ====================

    def check_active_exits(self):
        """Active position management — exit if conditions flip.

        Three exit triggers beyond fixed SL/TP:
          1. REGIME FLIP: flow bias turns against position (e.g. VIX fear → cut LONG)
          2. STALE POSITION: underwater > 30min with no improvement → cut loss
          3. DYNAMIC TP: if winning > 3% for > 60min → widen TP (let runner run)
        """
        positions = self.get_positions()
        if not positions:
            return

        try:
            flow = self.get_flow_context()
        except Exception:
            flow = {}
        flow_bias = self.interpret_flow_bias(flow.get("raw_data", {}))

        for t in self.trades:
            if t["status"] != "open":
                continue
            if t.get("reconciled"):
                continue  # ⛔ Never touch manually reconciled trades

            deal_id = t.get("deal_id", "")
            pos = next((p for p in positions if str(p.get("deal_id", "")) == str(deal_id)), None)
            if not pos:
                continue

            direction = t["direction"]
            current_price = float(pos.get("current", 0))
            entry_price = t["entry"]
            if current_price <= 0:
                continue

            pnl_pct = (current_price - entry_price) / entry_price
            if direction == "SHORT":
                pnl_pct = -pnl_pct

            age_minutes = (time.time() - t["timestamp_ts"]) / 60
            now = datetime.now(HKT)

            # === 1. REGIME FLIP EXIT ===
            # If flow bias strongly contradicts the position, exit
            long_bias = flow_bias.get("LONG", 0)
            short_bias = flow_bias.get("SHORT", 0)
            should_exit_regime = False
            exit_reason = ""

            if direction == "LONG" and short_bias >= 2 and long_bias == 0:
                should_exit_regime = True
                exit_reason = f"flow_regime_flip: SHORT bias {short_bias}"
            elif direction == "SHORT" and long_bias >= 2 and short_bias == 0:
                should_exit_regime = True
                exit_reason = f"flow_regime_flip: LONG bias {long_bias}"

            # === 1.5 THESIS INVALIDATION (Opposite Signal) ===
            if not should_exit_regime and age_minutes > 15:
                try:
                    if not hasattr(self, "_active_exit_market_data"):
                        self._active_exit_market_data = self.get_market_data()
                    current_signal = self.generate_signal(self._active_exit_market_data)
                    if current_signal and current_signal.get("direction"):
                        sig_dir = current_signal["direction"]
                        if (direction == "LONG" and sig_dir == "SHORT") or                            (direction == "SHORT" and sig_dir == "LONG"):
                            should_exit_regime = True
                            exit_reason = f"thesis_invalidation: Engine now signals {sig_dir}"
                except Exception as e:
                    pass

            # === 2. STALE POSITION EXIT ===
            # Underwater for > 30min and hasn't improved in last 10 min
            if not should_exit_regime and pnl_pct < -0.005 and age_minutes > 30:
                # Check if improving vs 10 minutes ago
                highest = t.get("highest_price", entry_price) if direction == "LONG" else t.get("lowest_price", entry_price)
                if direction == "LONG":
                    improving = current_price > highest * 0.995  # within 0.5% of best
                else:
                    improving = current_price < highest * 1.005
                if not improving:
                    should_exit_regime = True
                    exit_reason = f"stale_underwater: {age_minutes:.0f}min, no improvement"

            # === 3. DYNAMIC TP (let winners run) ===
            # If winning > 3% and position > 60min, widen TP by 50%
            if pnl_pct > 0.03 and age_minutes > 60 and not t.get("tp_widened"):
                old_target = t.get("target", 0)
                new_target = old_target * 1.5 if old_target > 0 else current_price * 1.05
                t["target"] = round(new_target, 2)
                t["tp_widened"] = True
                print(f"  🚀 [dynamic] {self.MARKET} TP widened: +50% → {new_target:.2f}")

            # === 4. SMART MONEY POSITIONING EXIT ===
            # Whale positioning-based exit triggers (only for BTC/ETH/SOL)
            sm_ctx = flow.get("smart_money", {})
            sm_whales = sm_ctx.get("whales", {}).get(self.MARKET, {})
            if not should_exit_regime and sm_whales:
                sm_ls = sm_whales.get("ls_ratio", 0.5)
                sm_long_profit = sm_whales.get("long_profit_pct", 50)
                sm_short_profit = sm_whales.get("short_profit_pct", 50)

                if direction == "SHORT":
                    # 4a. SQUEEZE DETECTION: extreme short positioning + shorts too profitable
                    if sm_ls < 0.20 and sm_short_profit > 75:
                        should_exit_regime = True
                        exit_reason = (
                            f"whale_squeeze: L/S={sm_ls:.2f} shorts={sm_short_profit:.0f}%_win "
                            f"→ whales may squeeze"
                        )
                    # 4b. L/S recovering while holding SHORT (whales starting to buy)
                    elif sm_ls > 0.60 and pnl_pct < 0:
                        should_exit_regime = True
                        exit_reason = (
                            f"whale_reversal: L/S={sm_ls:.2f} recovering → cut SHORT"
                        )

                elif direction == "LONG":
                    # 4c. WHALE CAPITULATION: longs trapped + extreme short bias
                    if sm_ls < 0.25 and sm_long_profit < 30:
                        should_exit_regime = True
                        exit_reason = (
                            f"whale_capitulation: L/S={sm_ls:.2f} "
                            f"longs={sm_long_profit:.0f}%_win → whales abandoning"
                        )
                    # 4d. Extreme long overheated → take profit
                    elif sm_ls > 2.5 and sm_long_profit > 80 and pnl_pct > 0.02:
                        should_exit_regime = True
                        exit_reason = (
                            f"whale_overheated: L/S={sm_ls:.2f} "
                            f"longs={sm_long_profit:.0f}%_win → distribution risk"
                        )

            # === Execute exit ===
            if should_exit_regime and deal_id:
                print(f"  🛑 [active_exit] {self.MARKET} {direction}: {exit_reason}")
                result = close_position(deal_id, self.account_id)
                if "error" not in result:
                    t["status"] = "closed"
                    t["closed_at"] = now.isoformat()
                    t["closed_at_ts"] = time.time()
                    t["close_reason"] = exit_reason
                    # ── TG通知：Active Exit ──
                    try:
                        hold_min = (time.time() - t["timestamp_ts"]) / 60
                        tg_trade_close(
                            self.MARKET, direction, t["entry"],
                            current_price,
                            t.get("adjusted_pnl", t.get("pnl", 0)),
                            exit_reason,
                            hold_min
                        )
                        t["notified_close"] = True
                    except Exception:
                        pass
                else:
                    print(f"  ❌ Active exit failed: {result['error']}")

        _atomic_save_json(self.trade_file, self.trades)

    # ==================== SCAN CYCLE ====================

    def scan(self) -> Dict:
        """Single scan cycle — intraday swing version."""
        now = datetime.now(HKT)
        
        # ── 週末保護：六日唔自動交易（crypto 24/7 除外，子 class 可 override）──
        if getattr(self, 'WEEKEND_TRADE_ENABLED', False) is False:
            if now.weekday() >= 5:  # 5=Saturday, 6=Sunday
                result = {
                    "timestamp": now.isoformat(),
                    "market": self.MARKET,
                    "paused": True,
                    "reason": f"weekend_skip: {now.strftime('%A')} — no auto-trade",
                    "signals_found": 0, "executed": 0, "signals": [],
                }
                _atomic_save_json(self.signal_file, result)
                return result
        
        self.update_closed()
        self.update_trailing_stops()
        self.check_time_stops()
        self.check_active_exits()   # 主動管理：regime flip / stale exit / dynamic TP
        self.check_48hr_breaker()
        self.update_state()

        result = {
            "timestamp": now.isoformat(),
            "market": self.MARKET,
            "paused": self.is_paused(),
            "open": self.count_open(),
            "signals_found": 0, "executed": 0, "signals": [],
        }

        if self.is_paused():
            result["reason"] = f"paused: {self.state.get('pause_reason', '')}"
            _atomic_save_json(self.signal_file, result)
            return result

        try:
            data = self.get_market_data()
        except Exception as e:
            result["error"] = f"data_fetch: {e}"
            _atomic_save_json(self.signal_file, result)
            return result

        # Inject flow context
        try:
            flow_ctx = self.get_flow_context()
            data["_flow"] = flow_ctx
        except Exception:
            data["_flow"] = {}

        try:
            signal = self.generate_signal(data)
        except Exception as e:
            result["error"] = f"signal_gen: {e}"
            _atomic_save_json(self.signal_file, result)
            return result

        if signal:
            # Attach entry attribution before execution
            signal["_entry_vix"] = data["_flow"].get("vix", 0)
            signal["_entry_vix_regime"] = data["_flow"].get("vix_regime", "unknown")
            signal["_entry_btc_dom"] = data["_flow"].get("btc_dominance", 0)

            result["signals_found"] = 1
            result["signals"].append({
                "direction": signal.get("direction"),
                "strategy": signal.get("strategy"),
                "entry": signal.get("entry"),
                "reason": signal.get("reason"),
            })
            exec_result = self.execute_signal(signal)
            if exec_result.get("executed"):
                result["executed"] = 1

        _atomic_save_json(self.signal_file, result)
        return result

    def status(self) -> Dict:
        """Current engine status."""
        bal = self.get_balance()
        positions = self.get_positions()
        return {
            "market": self.MARKET,
            "account": self.ACCOUNT_KEY,
            "balance": bal.get("balance", 0),
            "pnl_raw": bal.get("pnl", 0),
            "pnl_adjusted": self.state.get("total_pnl_adjusted", 0),
            "positions": len(positions),
            "paused": self.is_paused(),
            "pause_reason": self.state.get("pause_reason", ""),
            "total_trades": self.state.get("total_trades", 0),
            "total_pnl_raw": self.state.get("total_pnl_raw", 0),
            "total_pnl_adjusted": self.state.get("total_pnl_adjusted", 0),
            "best_strategy": self.state.get("best_strategy"),
            "edge_type": getattr(self, "EDGE_TYPE", "directional"),
            "edge_kpi": getattr(self, "EDGE_KPI", "direction_hit_rate + favorable_excursion"),
            "positions_detail": [
                {
                    "ticker": p.get("ticker"),
                    "direction": p.get("direction"),
                    "entry": p.get("entry"),
                    "current": p.get("current"),
                    "pnl_amt": p.get("pnl_amt", 0),
                }
                for p in positions
            ],
        }

    def reset(self):
        """Reset engine state — clean slate for pivot."""
        self.state = {
            "total_trades": 0, "total_pnl_raw": 0.0,
            "total_pnl_adjusted": 0.0,
            "paused": False, "pause_reason": "",
            "best_strategy": None, "strategy_stats": {},
        }
        _atomic_save_json(self.state_file, self.state)
        self.trades = []
        _atomic_save_json(self.trade_file, [])
        print(f"✅ {self.name} engine reset — clean slate")
