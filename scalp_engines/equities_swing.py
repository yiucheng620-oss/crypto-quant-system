#!/usr/bin/env python3
"""
⚡ EQUITIES SWING ENGINE — 美股揀股引擎
========================================
繼承 SwingEngine base，每 5 分鐘 run screener。
Edge = stock selection alpha (揀中嘅股跑贏 SPY/peer)。
唔係方向交易，係揀股能力驗證。

KPI: picked stocks vs SPY relative return over 5-day window.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
ENGINES_DIR = os.path.join(WORKSPACE, "scalp_engines")
LOG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")

sys.path.insert(0, WORKSPACE)
sys.path.insert(0, ENGINES_DIR)

from engine_base import SwingEngine, _atomic_save_json, _load_json


class EquitiesSwingEngine(SwingEngine):

    # === Equities-specific config ===
    MAX_POSITIONS_PER_SECTOR: int = 1

    def _get_engine_param_map(self) -> dict:
        return {
            "max_positions_per_sector": "MAX_POSITIONS_PER_SECTOR",
        }

    """美股揀股引擎 — stock selection alpha 驗證。

    每 scan 執行 dynamic screener，選出 top picks。
    唔自動落單（揀股需要人手判斷），淨係 track picks performance。
    """

    MARKET = "EQUITIES"
    EPIC = "SPY"
    ACCOUNT_KEY = "equities"
    INITIAL_BALANCE = 10000.0

    MAX_POSITIONS = 2           # 最多同時 2 個倉（之前係 5，太分散 = 假分散）
    MAX_POSITIONS_PER_SECTOR = 1  # 🆕 每板塊最多 1 倉（防半導體式自殺）
    MAX_EXPOSURE_PCT = 0.80
    STOP_LOSS_PCT = 0.03
    TAKE_PROFIT_PCT = 0.050
    SCREENER_TTL = 21600  # 6 hours (was 3600=1hr — hourly refresh waste 216K tokens/day)

    def __init__(self, name: str = None):
        super().__init__(name or "equities")
        self.screener_output = os.path.join(
            WORKSPACE, "equities_data", "equities_setups.json"
        )
        self.picks_file = os.path.join(LOG_DIR, "equities_picks.json")
        self.picks = _load_json(self.picks_file, [])
        self._cached_picks = None
        self._cached_picks_ts = 0
        print(f"📊 Equities Engine initialised — account: equities ({self.account_id})")

    def _get_cached_picks(self) -> dict | None:
        """Return cached screener output if within TTL."""
        if os.path.exists(self.screener_output):
            age = time.time() - os.path.getmtime(self.screener_output)
            if age < self.SCREENER_TTL:
                try:
                    with open(self.screener_output, "r") as f:
                        return json.load(f)
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    # fallback: cache file missing or corrupt, run screener fresh
                    pass
        return None

    def _run_screener(self) -> dict:
        """Run the equities screener (heavy — yfinance 100+ stocks). Only called when cache expired."""
        try:
            sys.path.insert(0, os.path.join(WORKSPACE, "kanban"))
            import equities_engine
            return equities_engine.run()
        except Exception as e:
            print(f"  ⚠️ Equities screener error: {e}")
        return {}

    def get_market_data(self) -> dict:
        """Get picks: from cache if fresh, else re-run screener."""
        picks = self._get_cached_picks()
        if picks is None:
            print(f"  📊 [EQUITIES] Cache expired — re-running screener...")
            picks = self._run_screener()

        return {
            "picks": picks or {},
            "cached": time.time() - os.path.getmtime(self.screener_output) if os.path.exists(self.screener_output) else 9999,
            "_flow": self.get_flow_context(),
        }

    def generate_signal(self, data: dict) -> dict | None:
        """Equities engine: return picks as signal for tracking.

        Edge tracking: record which stocks were picked + their prices,
        then compare against SPY in 5 days.
        """
        picks = data.get("picks", {})
        setups = picks.get("setups", [])
        if not setups:
            return None

        # Record picks for performance tracking (max 2 picks, 1 per sector)
        now = datetime.now(HKT)
        new_picks = []
        seen_sectors = set()
        for s in setups:
            if len(new_picks) >= self.MAX_POSITIONS:
                break
            sector = s.get("sector", "Unknown")
            if sector in seen_sectors:
                continue  # 🆕 板塊上限：每 sector 最多 1 倉
            seen_sectors.add(sector)
            p = {
                "ticker": s.get("ticker"),
                "price": s.get("price"),
                "confidence": s.get("confidence", "3/5"),
                "reason": s.get("reason") or f"{s.get('signal','setup')} | RS:{s.get('rs_vs_spy',0):+.1f}% | R:R 1:{s.get('rr',0):.1f}",
                "sector": s.get("sector") or self._guess_sector(s.get("ticker", "")),
                "rs_score": s.get("rs_vs_spy", 0),  # FIXED: was rs_score, actual key is rs_vs_spy
                "picked_at": now.isoformat(),
                "picked_at_ts": time.time(),
                "spy_at_pick": picks.get("benchmarks", {}).get("SPY", {}).get("price", picks.get("macro", {}).get("spy_price", 0)),
            }
            new_picks.append(p)

        if new_picks:
            # Dedup: skip recording if same tickers were recorded < 30 min ago
            ticker_key = tuple(sorted(p['ticker'] for p in new_picks))
            should_record = True
            if self.picks:
                last = self.picks[-1]
                last_tickers = tuple(sorted(p['ticker'] for p in last.get('picks', [])))
                age_sec = time.time() - last.get('ts_epoch', 0)
                if last_tickers == ticker_key and age_sec < 1800:  # 30 min
                    should_record = False
            
            if should_record:
                self.picks.append({
                    "ts": now.isoformat(),
                    "ts_epoch": time.time(),
                    "picks": new_picks,
                    "market_regime": {
                        "vix": data["_flow"].get("vix", 0),
                        "spy_price": picks.get("macro", {}).get("spy_price", 0),
                    },
                })
                # Cap history at 2000 entries (about 3 months at 4x/day)
                if len(self.picks) > 2000:
                    self.picks = self.picks[-2000:]
                _atomic_save_json(self.picks_file, self.picks)
                print(f"📊 [EQUITIES] {len(new_picks)} new picks tracked: "
                      f"{', '.join(p['ticker'] for p in new_picks)}")
            else:
                print(f"📊 [EQUITIES] Same picks as {age_sec:.0f}s ago — skip duplicate")

        return {
            "direction": "LONG",
            "strategy": "stock_selection",
            "entry": 0,  # no execution
            "stop": 0,
            "target": 0,
            "reason": f"Track {len(new_picks)} picks for 5-day alpha measurement",
            "label": "EQUITIES",
            "epic": "SPY",
            "picks": new_picks,
        }

    def interpret_flow_bias(self, flow_data: dict) -> dict:
        """VIX regime bias for equities.

        VIX < 15 → favorable for stock picking (low vol)
        VIX > 25 → reduce exposure (high vol)
        """
        bias = {"LONG": 0, "SHORT": 0}
        vix = flow_data.get("vix", 0)
        if vix > 25:
            bias["SHORT"] += 1  # signal to reduce
        elif vix < 15:
            bias["LONG"] += 1
        return bias

    # ── Sector mapping (fallback when screener doesn't provide sector) ──
    _SECTOR_MAP = {
        "AAPL": "科技", "MSFT": "科技", "NVDA": "半導體", "GOOGL": "通訊", "AMZN": "消費",
        "META": "通訊", "TSLA": "消費", "AMD": "半導體", "AVGO": "半導體", "INTC": "半導體",
        "QCOM": "半導體", "TXN": "半導體", "MU": "半導體", "AMAT": "半導體", "LRCX": "半導體",
        "ADI": "半導體", "MRVL": "半導體", "ARM": "半導體", "DELL": "科技", "SMCI": "科技",
        "CRM": "科技", "ADBE": "科技", "NOW": "科技", "SNOW": "科技", "ORCL": "科技",
        "INTU": "科技", "PLTR": "科技", "NET": "科技", "DDOG": "科技", "CRWD": "科技",
        "ZS": "科技", "NFLX": "通訊", "SPOT": "通訊", "SNAP": "通訊", "PINS": "通訊",
        "RBLX": "通訊", "UBER": "科技", "ABNB": "科技", "DASH": "科技",
        "JPM": "金融", "BAC": "金融", "WFC": "金融", "GS": "金融", "MS": "金融",
        "V": "金融", "MA": "金融", "AXP": "金融", "BLK": "金融", "SCHW": "金融",
        "LLY": "醫療", "UNH": "醫療", "JNJ": "醫療", "PFE": "醫療", "ABBV": "醫療",
        "MRK": "醫療", "TMO": "醫療", "ISRG": "醫療", "VRTX": "醫療",
        "COST": "消費", "HD": "消費", "WMT": "消費", "NKE": "消費", "SBUX": "消費",
        "LULU": "消費", "CMG": "消費", "DIS": "通訊", "PYPL": "金融", "COIN": "金融",
        "HOOD": "金融", "BA": "工業", "CAT": "工業", "GE": "工業", "RTX": "工業",
        "LMT": "工業", "XOM": "能源", "CVX": "能源", "COP": "能源",
        "RIVN": "消費", "LCID": "消費",
    }

    def _guess_sector(self, ticker: str) -> str:
        return self._SECTOR_MAP.get(ticker.upper(), "Other")

    def scan(self) -> dict:
        """Override: equities engine tracks picks only, no auto-execution.
        
        Stock selection is done manually by the trader - engine just generates
        the pick list and tracks performance vs SPY benchmark.
        """
        # Run base scan cycle (update_closed, trailing, etc.) but skip signal execution
        now = datetime.now(HKT)
        self.update_closed()
        self.update_trailing_stops()
        self.check_time_stops()
        self.check_active_exits()
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
            signal = self.generate_signal(data)
            if signal:
                result["signals_found"] = 1
                result["signals"].append({
                    "direction": signal.get("direction"),
                    "strategy": signal.get("strategy"),
                    "reason": signal.get("reason"),
                    "picks": signal.get("picks", []),
                })
                # NO auto-execution for equities — trader picks manually
        except Exception as e:
            result["error"] = f"equities_scan: {e}"

        _atomic_save_json(self.signal_file, result)
        return result

    def status(self) -> dict:
        """Override: include picks tracking info."""
        base = super().status()
        base["picks_tracked"] = len(self.picks)
        base["edge_type"] = "stock_selection_alpha"
        base["edge_kpi"] = "5-day relative return vs SPY"
        return base
