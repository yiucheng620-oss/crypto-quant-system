#!/usr/bin/env python3
"""
⚡ INDICES SCALP ENGINE — SPX + NDX 宏觀指數交易引擎
================================================
繼承 ScalpEngine base，專為 SPX 及 NDX 設計：
- 數據源: Capital.com bid/offer/mid + yfinance VIX + SPX/NDX Ratio
- 策略: VIX regime detection + ratio trend + spread liquidity 三層過濾
- 特性: 共享 macro account，獨立 position limit (各 1 倉)

用法：
  from indices_scalp import IndicesScalpEngine
  engine = IndicesScalpEngine()
  result = engine.scan()
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any

import requests

from engine_base import SwingEngine, _atomic_save_json
from capitalcom_paper import close_position

# ==================== 常數 ====================

HKT = timezone(timedelta(hours=8))

# Per-market config
MARKET_CONFIGS = {
    "SPX": {
        "epic": "US500",
        "label": "SPX",
        "max_positions": 1,
        "stop_pct": 0.008,
        "tp_pct": 0.016,
    },
    "NDX": {
        "epic": "US100",
        "label": "NDX",
        "max_positions": 1,
        "stop_pct": 0.012,
        "tp_pct": 0.024,
    },
}

# VIX regime thresholds
VIX_RISK_ON = 18       # VIX < 18 → risk-on, LONG bias
VIX_RISK_OFF = 25      # VIX > 25 → risk-off, SHORT bias

# SPX/NDX ratio regime
RATIO_UP_THRESHOLD = 0.002   # ratio change > +0.2% → NDX outperforming
RATIO_DOWN_THRESHOLD = -0.002  # ratio change < -0.2% → SPX outperforming

# Spread quality (in pips / price points)
SPREAD_TIGHT = 0.5     # spread ≤ 0.5 → high liquidity
SPREAD_WIDE = 2.0      # spread ≥ 2.0 → low liquidity, skip


# ==================== 輔助函數 ====================


def _fetch_vix() -> Optional[float]:
    """
    從 yfinance 抓 VIX 水平。
    Try/except 包住，任何 failure return None 唔 crash engine。

    Returns:
        float: VIX 價格, or None
    """
    try:
        import yfinance as yf
        t = yf.Ticker("^VIX")
        hist = t.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        print(f"⚠️ _fetch_vix error: {e}")
    return None

def _fetch_market_price(epic: str, account_id: str) -> Optional[Dict]:
    """
    從 Capital.com 抓單一市場嘅價格、bid/offer 及 spread 資料。

    Args:
        epic: Capital.com EPIC code (e.g. "US500", "US100")
        account_id: Capital.com account ID

    Returns:
        dict with bid/offer/mid/spread, or None
    """
    try:
        from capitalcom_paper import get_market_info
        info = get_market_info(epic, account_id)
        if not info or not isinstance(info, dict):
            return None
        if "error" in info:
            print(f"⚠️ Capital.com error for {epic}: {info.get('error')}")
            return None

        bid = info.get("bid")
        offer = info.get("offer")

        if bid is None or offer is None:
            return None

        bid = float(bid)
        offer = float(offer)
        mid = (bid + offer) / 2.0
        spread = round(offer - bid, 4)

        return {
            "bid": bid,
            "offer": offer,
            "mid": mid,
            "spread": spread,
            "status": info.get("status", "?"),
        }
    except ImportError:
        print("⚠️ capitalcom_paper not available")
    except Exception as e:
        print(f"⚠️ Capital.com fetch error for {epic}: {e}")
    return None


def _compute_ndx_spx_ratio(ndx_data: Dict, spx_data: Dict) -> Optional[Dict]:
    """
    計算 NDX/SPX 價格比例及其變化方向。
    用 mid price 計算 ratio, 比例上升 = NDX 跑贏 (risk-on tech/growth)。

    Returns:
        dict with ratio, direction ("up"/"down"/"flat"), or None
    """
    if not ndx_data or not spx_data:
        return None

    ndx_mid = ndx_data.get("mid")
    spx_mid = spx_data.get("mid")
    if not ndx_mid or not spx_mid or spx_mid <= 0:
        return None

    try:
        ratio = round(ndx_mid / spx_mid, 6)
        # 用簡單 threshold 決定方向 (冇歷史比較, 只靠即時水平)
        # 呢度係 baseline: 第一次 call 時 direction = "flat"
        return {
            "ratio": ratio,
            "direction": "flat",
            "ndx_mid": ndx_mid,
            "spx_mid": spx_mid,
        }
    except (TypeError, ValueError, ZeroDivisionError) as e:
        print(f"⚠️ Ratio compute error: {e}")
    return None


def _get_vix_regime(vix: Optional[float]) -> str:
    """
    將 VIX 水平轉換為 regime 標籤。

    Args:
        vix: VIX 價格 (可為 None)

    Returns:
        "risk_on", "neutral", "risk_off", or "unknown" (無 VIX 數據)
    """
    if vix is None:
        return "unknown"
    if vix < VIX_RISK_ON:
        return "risk_on"
    if vix > VIX_RISK_OFF:
        return "risk_off"
    return "neutral"


# ==================== Indices Engine ====================


class IndicesScalpEngine(SwingEngine):
    """
    SPX + NDX 宏觀指數 Scalp Engine。

    數據源:
      1. Capital.com SPX (US500) bid/offer/mid price
      2. Capital.com NDX (US100) bid/offer/mid price
      3. VIX 水平 (yfinance ^VIX)
      4. NDX/SPX ratio (risk-on/risk-off indicator)
      5. Spread width (流動性 proxy)

    信號邏輯 (三層 filter):
      1. VIX Regime: < 18 → risk-on LONG bias; > 25 → risk-off SHORT bias
      2. NDX/SPX Ratio Trend: ratio 升 → NDX LONG; ratio 跌 → SPX 相對強
      3. Spread Quality: spread tight → 高可信度執行

    兩個市場獨立掃描，各自獨立 signal。
    """

    MARKET = "INDICES"
    EPIC = "US500"       # Primary default (SPX)
    ACCOUNT_KEY = "indices"

    # 共享風險參數
    MAX_POSITIONS = 2       # SPX + NDX 各一, 總共最多 2
    MAX_EXPOSURE_PCT = 0.80
    RISK_PER_TRADE_PCT = 0.02
    MAX_CONSEC_LOSSES = 5

    def __init__(self, name: str = None):
        super().__init__(name or "indices")
        # Account routing: 指數 independent account
        from capitalcom_paper import ACCOUNTS
        idx_id = ACCOUNTS.get("indices", self.account_id)
        self.account_id = idx_id
        self.account_key = "indices"
        # Ratio 追蹤狀態 (in-memory, 跨 scan 用)
        self._prev_ratio = None
        self._ratio_direction = "flat"
        print(f"🤖 Indices Scalp Engine initialised — account: indices ({self.account_id})")
        print(f"   Markets: SPX (US500) | NDX (US100)")

    def _get_epics(self) -> set:
        """Override: Indices engine trades both US500 and US100."""
        return {"US500", "US100"}

    # ==================== VIX 1H History Helper ====================

    def _fetch_vix_1h_history(self) -> Optional[List[float]]:
        """
        抓 VIX 過去 1 小時嘅價格序列，用嚟計算 VIX ROC（Rate of Change）。
        由 yfinance ^VIX 拎 1 小時級別數據。

        Returns:
            List[float] — VIX 價格序列（至少 2 個數據點），或 None
        """
        try:
            import yfinance as yf
            vix_ticker = yf.Ticker("^VIX")
            vix_data = vix_ticker.history(period="2d", interval="1h")
            if vix_data is not None and not vix_data.empty and len(vix_data) >= 2:
                closes = vix_data["Close"].tolist()
                # 取最近 2 個數據點（now 同 1h ago）
                return closes[-2:]
            # Fallback: 改用 1d daily close
            vix_data = vix_ticker.history(period="5d", interval="1d")
            if vix_data is not None and not vix_data.empty and len(vix_data) >= 2:
                closes = vix_data["Close"].tolist()
                return closes[-2:]
        except ImportError:
            print("⚠️ yfinance not available for VIX ROC — skip")
        except Exception as e:
            print(f"⚠️ VIX 1h history fetch error: {e}")
        return None

    # ==================== FLOW BIAS HOOK (override) ====================

    def interpret_flow_bias(self, flow_data: Dict) -> Dict:
        """
        Indices 專屬 flow bias 解讀。

        對於 SPX/NDX 指數：
          - VIX fear（高 VIX > 25）→ SHORT bias +1（市場恐慌壓低指數）
          - VIX complacency（低 VIX < 18）→ LONG bias +1（資金流入風險資產）

        Returns:
            {"LONG": int, "SHORT": int} — bias adjustment counts
        """
        bias = {"LONG": 0, "SHORT": 0}
        raw = flow_data.get("raw_data", {})

        vix = raw.get("vix", 0)
        vix_regime = raw.get("vix_regime", "unknown")

        # --- VIX fear → SHORT（恐慌時指數跌）---
        if vix > VIX_RISK_OFF:
            bias["SHORT"] += 1
            print(f"  [FLOW][INDICES] VIX={vix:.1f} > {VIX_RISK_OFF} → 恐慌 SHORT +1")
        # --- VIX complacency → LONG（risk-on 時指數升）---
        elif vix < VIX_RISK_ON:
            bias["LONG"] += 1
            print(f"  [FLOW][INDICES] VIX={vix:.1f} < {VIX_RISK_ON} → complacency LONG +1")
        else:
            print(f"  [FLOW][INDICES] VIX={vix:.1f} ({vix_regime}) → neutral")

        print(f"  [FLOW][INDICES] Final bias: LONG={bias['LONG']} SHORT={bias['SHORT']}")
        return bias

    # ==================== 數據獲取 ====================

    def get_market_data(self) -> Dict:
        """
        收集 SPX + NDX 市場數據及 VIX。

        Returns:
            dict with keys:
            - spx_data: SPX price info dict (or None)
            - ndx_data: NDX price info dict (or None)
            - vix: VIX 價格 float (or None)
            - vix_regime: regime 標籤 str
            - ratio_data: NDX/SPX ratio dict (or None)
            - timestamp: ISO timestamp
            - sources_alive: 可用數據源列表
            - sources_dead: 不可用數據源列表
        """
        result = {
            "timestamp": datetime.now(HKT).isoformat(),
            "spx_data": None,
            "ndx_data": None,
            "vix": None,
            "vix_regime": "unknown",
            "ratio_data": None,
            "sources_alive": [],
            "sources_dead": [],
        }

        # === Cash Session Time Stop Check ===
        # SPX/NDX cash session close: 16:00 ET
        # Summer (EDT, UTC-4): 20:00 UTC = HKT 04:00 (next day)
        # Winter (EST, UTC-5): 21:00 UTC = HKT 05:00 (next day)
        # 收市前 15 分鐘停止新 signal + 強制平倉
        now_utc = datetime.now(timezone.utc)
        now_hkt = datetime.now(HKT)
        
        # Auto-detect DST: US DST ~ Mar-Oct (2nd Sun Mar to 1st Sun Nov)
        is_dst = 3 <= now_utc.month <= 10
        if is_dst:
            market_close_utc_hour = 20  # EDT → UTC 20:00
        else:
            market_close_utc_hour = 21  # EST → UTC 21:00
        market_close_utc_min = 0
        cash_warn_minutes = 15  # 收市前 15 分鐘

        # 計算距離收市嘅時間（分鐘）
        close_today_utc = now_utc.replace(
            hour=market_close_utc_hour, minute=market_close_utc_min, second=0, microsecond=0
        )
        mins_to_close = (close_today_utc - now_utc).total_seconds() / 60.0

        # 喺收市前 15 分鐘內 → 標記 cash_session_closing，skip signal
        if 0 <= mins_to_close <= cash_warn_minutes:
            result["cash_session_closing"] = True
            result["cash_session_note"] = (
                f"⚠️ SPX cash session closing in {mins_to_close:.0f}min"
                f" (HKT {now_hkt.hour:02d}:{now_hkt.minute:02d}) — skip signals"
            )
            print(f"  ⏰ {result['cash_session_note']}")

            # 強制 close 現有 open position
            existing_positions = self.get_positions()
            for pos in existing_positions:
                deal_id = pos.get("deal_id", "")
                if deal_id:
                    print(f"  🔴 Cash session close: forcing close deal {deal_id}")
                    close_result = close_position(deal_id, self.account_id)
                    if "error" in close_result:
                        print(f"  ❌ Force close failed: {close_result['error']}")
        else:
            result["cash_session_closing"] = False
            result["cash_session_note"] = (
                f"OK ({mins_to_close:.0f}min to cash close)"
            )

        # 1. Capital.com SPX price
        try:
            spx = _fetch_market_price("US500", self.account_id)
            if spx:
                result["spx_data"] = spx
                result["sources_alive"].append("spx_price")
            else:
                result["sources_dead"].append("spx_price")
        except Exception as e:
            print(f"⚠️ get_market_data: SPX exception: {e}")
            result["sources_dead"].append("spx_price")

        # 2. Capital.com NDX price
        try:
            ndx = _fetch_market_price("US100", self.account_id)
            if ndx:
                result["ndx_data"] = ndx
                result["sources_alive"].append("ndx_price")
            else:
                result["sources_dead"].append("ndx_price")
        except Exception as e:
            print(f"⚠️ get_market_data: NDX exception: {e}")
            result["sources_dead"].append("ndx_price")

        # 3. VIX 水平
        try:
            vix = _fetch_vix()
            if vix is not None:
                result["vix"] = vix
                result["vix_regime"] = _get_vix_regime(vix)
                result["sources_alive"].append("vix")
            else:
                result["sources_dead"].append("vix")
        except Exception as e:
            print(f"⚠️ get_market_data: VIX exception: {e}")
            result["sources_dead"].append("vix")

        # 4. NDX/SPX Ratio
        try:
            ratio_data = _compute_ndx_spx_ratio(
                result.get("ndx_data"), result.get("spx_data")
            )
            if ratio_data:
                # 同上次 ratio 比較，判斷方向
                current_ratio = ratio_data["ratio"]
                if self._prev_ratio is not None:
                    ratio_change = (current_ratio - self._prev_ratio) / self._prev_ratio
                    if ratio_change > RATIO_UP_THRESHOLD:
                        self._ratio_direction = "up"
                    elif ratio_change < RATIO_DOWN_THRESHOLD:
                        self._ratio_direction = "down"
                    else:
                        self._ratio_direction = "flat"
                    ratio_data["direction"] = self._ratio_direction
                    ratio_data["change_pct"] = round(ratio_change * 100, 3)
                else:
                    ratio_data["direction"] = "flat"
                    ratio_data["change_pct"] = 0.0

                self._prev_ratio = current_ratio
                result["ratio_data"] = ratio_data
                result["sources_alive"].append("spx_ndx_ratio")
            else:
                result["sources_dead"].append("spx_ndx_ratio")
        except Exception as e:
            print(f"⚠️ get_market_data: Ratio exception: {e}")
            result["sources_dead"].append("spx_ndx_ratio")

        # Debug summary
        alive = len(result["sources_alive"])
        dead = len(result["sources_dead"])
        spx_mid = result.get("spx_data", {}).get("mid")
        ndx_mid = result.get("ndx_data", {}).get("mid")
        print(f"📡 Indices market data — {alive} alive, {dead} dead")
        print(f"   SPX={spx_mid} | NDX={ndx_mid} | VIX={result.get('vix')} ({result.get('vix_regime')})")
        if result.get("ratio_data"):
            print(f"   NDX/SPX ratio={result['ratio_data']['ratio']} ({result['ratio_data']['direction']}, Δ={result['ratio_data']['change_pct']}%)")

        return result

    # ==================== 單一市場信號 ====================

    def _signal_for_market(self, market_key: str, data: Dict) -> Optional[Dict]:
        """
        為一個市場生成信號 (SPX 或 NDX)。

        Args:
            market_key: "SPX" 或 "NDX"
            data: get_market_data() 嘅回傳值

        Returns:
            signal dict 或 None
        """
        cfg = MARKET_CONFIGS[market_key]
        epic = cfg["epic"]
        market_data_key = "spx_data" if market_key == "SPX" else "ndx_data"
        market_data = data.get(market_data_key)

        if not market_data:
            print(f"❌ [{market_key}] No price data — cannot generate signal")
            return None

        price = market_data.get("mid")
        bid = market_data.get("bid")
        offer = market_data.get("offer")
        spread = market_data.get("spread")

        if not price or price <= 0:
            print(f"❌ [{market_key}] Invalid price: {price}")
            return None

        if not bid or not offer:
            print(f"❌ [{market_key}] Missing bid/offer")
            return None

        # === 取得 macro context ===
        vix = data.get("vix")
        vix_regime = data.get("vix_regime", "unknown")
        ratio_data = data.get("ratio_data")

        # === Filter 1: Spread Quality (流動性過濾) ===
        # Spread 太闊 = 低流動性, 唔適合入市
        if spread is not None and spread > SPREAD_WIDE:
            print(f"⏸️ [{market_key}] Filter1 (Spread): spread={spread} > {SPREAD_WIDE} — 流動性不足")
            return None

        spread_note = f"spread={spread}"
        if spread is not None and spread <= SPREAD_TIGHT:
            spread_note += " (tight)"
        else:
            spread_note += " (moderate)"

        # === Filter 2: VIX Regime (VIX 制度檢測) ===
        # 決定整體市場 bias
        bias_direction = None
        vix_note = f"vix={vix}" if vix is not None else "vix=na"
        vix_edge_weight = self.edge_weights.get("vix_direction", 1.0)

        if vix_edge_weight >= 0.1 and vix_regime == "risk_on":
            # VIX < 18 → 風險偏好, LONG bias
            bias_direction = "LONG"
            vix_note += " (risk-on → LONG bias)"
        elif vix_edge_weight >= 0.1 and vix_regime == "risk_off":
            # VIX > 25 → 風險規避, SHORT bias
            bias_direction = "SHORT"
            vix_note += " (risk-off → SHORT bias)"
        elif vix_edge_weight >= 0.1 and vix_regime == "neutral":
            bias_direction = None
            vix_note += " (neutral — 等價格方向)"
        else:
            # Edge suppressed or no VIX data
            bias_direction = None
            vix_note += f" (edge_weight={vix_edge_weight})" if vix_edge_weight < 0.1 else " (unknown — 用價格方向)"

        # === Filter 3: VIX ROC (Rate of Change) Filter ===
        # 掉咗舊嘅 bid_dist/ask_dist（永遠相等嘅 bug）
        # 改用: VIX 過去 1 小時跌 > 5% 且 VIX < 20 → LONG SPX
        vix_roc_bias = None
        vix_roc_note = "vix_roc=na"
        try:
            vix_data_raw = self._fetch_vix_1h_history()  # 自定義 helper 抓 1h VIX data
            if vix_data_raw and len(vix_data_raw) >= 2:
                vix_now = vix_data_raw[-1]
                vix_1h_ago = vix_data_raw[0]
                if vix_1h_ago > 0:
                    vix_change_pct = (vix_now - vix_1h_ago) / vix_1h_ago
                    vix_roc_note = f"vix_roc={vix_change_pct:.2%} (now={vix_now:.1f}, 1h_ago={vix_1h_ago:.1f})"
                    if vix_change_pct < -0.05 and vix_now < 20:
                        # VIX 快速回落 + 低位 → 市場穩定 → LONG SPX
                        vix_roc_bias = "LONG"
                        vix_roc_note += " → 恐慌消退 LONG bias"
                    elif vix_change_pct > 0.05:
                        # VIX 急升 → 風險急增 → SHORT bias
                        vix_roc_bias = "SHORT"
                        vix_roc_note += " → 恐慌急升 SHORT bias"
        except Exception as e:
            print(f"⚠️ VIX ROC filter error: {e}")

        # === Filter 4: NDX/SPX Ratio Trend (相對強勢) ===
        ratio_note = "ratio=na"
        ratio_signal_direction = None

        if ratio_data:
            ratio_dir = ratio_data.get("direction", "flat")
            ratio_note = f"ratio={ratio_data['ratio']} ({ratio_dir}, Δ={ratio_data.get('change_pct', 0)}%)"

            if ratio_dir == "up":
                # NDX 跑贏 → risk-on tech, NDX LONG bias
                if market_key == "NDX":
                    ratio_signal_direction = "LONG"
                elif market_key == "SPX":
                    ratio_signal_direction = "SHORT"  # SPX 相對弱
            elif ratio_dir == "down":
                # NDX 跑輸 → SPX 相對強
                if market_key == "SPX":
                    ratio_signal_direction = "LONG"
                elif market_key == "NDX":
                    ratio_signal_direction = "SHORT"  # NDX 相對弱

        # === 綜合決定方向 ===
        # 用 VIX ROC filter 替代舊嘅 bid_dist/ask_dist（math bug 已修）
        direction = None
        reasons = []

        # 情況 A: VIX regime 明確 + Ratio trend 一致
        if bias_direction and ratio_signal_direction:
            if bias_direction == ratio_signal_direction:
                direction = bias_direction
                reasons = [f"VIX+Ratio 一致{direction}", vix_note, ratio_note, spread_note]
            else:
                # VIX 同 ratio 方向唔同, 用 VIX ROC 做 tiebreaker
                if vix_roc_bias:
                    direction = vix_roc_bias
                    reasons = [f"VIX({bias_direction})≠Ratio({ratio_signal_direction})→VIX_ROC: {vix_roc_note}",
                               vix_note, ratio_note, spread_note]
                else:
                    # 冇 VIX ROC bias, 用 VIX regime (大趨勢優先)
                    direction = bias_direction
                    reasons = [f"VIX({bias_direction})優先", vix_note, ratio_note, spread_note]

        # 情況 B: 只有 VIX regime
        elif bias_direction:
            if vix_roc_bias == bias_direction:
                direction = bias_direction
                reasons = [f"VIX+VIX_ROC一致{direction}", vix_note, vix_roc_note, spread_note]
            elif vix_roc_bias:
                direction = vix_roc_bias
                reasons = [f"VIX_ROC優先於VIX", vix_roc_note, vix_note, spread_note]
            else:
                direction = bias_direction
                reasons = [f"純VIX方向", vix_note, spread_note]

        # 情況 C: 只有 Ratio trend
        elif ratio_signal_direction:
            if vix_roc_bias == ratio_signal_direction:
                direction = ratio_signal_direction
                reasons = [f"Ratio+VIX_ROC一致{direction}", ratio_note, vix_roc_note, spread_note]
            elif vix_roc_bias:
                direction = vix_roc_bias
                reasons = [f"VIX_ROC優先於Ratio", vix_roc_note, ratio_note, spread_note]
            else:
                direction = ratio_signal_direction
                reasons = [f"純Ratio方向", ratio_note, spread_note]

        # 情況 D: 只有 VIX ROC (冇 VIX regime, 冇 Ratio change)
        elif vix_roc_bias:
            direction = vix_roc_bias
            reasons = [f"純VIX_ROC方向", vix_roc_note, spread_note]

        # 情況 E: 完全冇方向
        if direction is None:
            print(f"⏸️ [{market_key}] 無足夠 edge 產生信號 — {vix_note} | {ratio_note} | {vix_roc_note}")
            return None

        # === 執行信號 ===
        stop_pct = cfg["stop_pct"]
        tp_pct = cfg["tp_pct"]

        if direction == "LONG":
            stop = round(price * (1 - stop_pct), 4)
            target = round(price * (1 + tp_pct), 4)
        else:
            stop = round(price * (1 + stop_pct), 4)
            target = round(price * (1 - tp_pct), 4)

        reason = " | ".join(p for p in reasons if p)
        strategy_name = f"idx_{vix_regime}_{self._ratio_direction}"

        signal = {
            "strategy": strategy_name,
            "epic": epic,
            "label": market_key,
            "direction": direction,
            "entry": price,
            "stop": stop,
            "target": target,
            "reason": reason,
            "entry_vix": vix,         # 記錄入場時 VIX（比 exit management 用）
        }

        print(f"🚦 [{market_key}] SIGNAL: {direction} @ {price} | SL={stop} TP={target} | {reason}")
        return signal

    # ==================== 覆蓋 generate_signal (單市場 fallback) ====================

    def generate_signal(self, data: Dict) -> Optional[Dict]:
        """
        Base class compatibility — 預設掃 SPX。
        實際使用請呼叫 scan() 而非 generate_signal()。
        """
        return self._signal_for_market("SPX", data)

    # ==================== 覆蓋 scan cycle (雙市場) ====================

    def scan(self) -> Dict:
        """
        完整 scan cycle: SPX + NDX 各自獨立掃描。

        Returns:
            dict with scan results for both markets
        """
        now = datetime.now(HKT)
        
        # ── 週末保護 ──
        if getattr(self, 'WEEKEND_TRADE_ENABLED', False) is False:
            if now.weekday() >= 5:
                result = {
                    "timestamp": now.isoformat(),
                    "market": self.MARKET,
                    "paused": True,
                    "reason": f"weekend_skip: {now.strftime('%A')}",
                    "signals_found": 0, "executed": 0, "signals": [],
                }
                _atomic_save_json(self.signal_file, result)
                return result
        
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
            "signals_found": 0,
            "executed": 0,
            "signals": [],
            "markets": {},
        }

        if self.is_paused():
            result["reason"] = "paused"
            _atomic_save_json(self.signal_file, result)
            return result

        # Get data once for both markets
        try:
            data = self.get_market_data()
        except Exception as e:
            result["error"] = f"data_fetch: {e}"
            _atomic_save_json(self.signal_file, result)
            return result

        # Scan each market independently
        for market_key in ["SPX", "NDX"]:
            market_result = self._scan_single_market(market_key, data)
            result["markets"][market_key] = market_result

            if market_result.get("signal_found"):
                result["signals_found"] += 1
                result["signals"].append(market_result.get("signal_summary", {}))
                if market_result.get("executed"):
                    result["executed"] += 1

        _atomic_save_json(self.signal_file, result)
        return result

    def _scan_single_market(self, market_key: str, data: Dict) -> Dict:
        """
        掃一個市場 (SPX 或 NDX) 並執行信號。

        Args:
            market_key: "SPX" 或 "NDX"
            data: get_market_data() 嘅結果

        Returns:
            dict with scan results for this market
        """
        cfg = MARKET_CONFIGS[market_key]
        epic = cfg["epic"]
        result = {
            "market": market_key,
            "epic": epic,
            "signal_found": False,
            "executed": False,
            "signal_summary": None,
        }

        # Generate signal for this market
        try:
            signal = self._signal_for_market(market_key, data)
        except Exception as e:
            result["error"] = f"signal_gen: {e}"
            print(f"⚠️ [{market_key}] signal generation exception: {e}")
            return result

        if not signal:
            return result

        result["signal_found"] = True
        result["signal_summary"] = {
            "direction": signal.get("direction"),
            "strategy": signal.get("strategy"),
            "entry": signal.get("entry"),
            "reason": signal.get("reason"),
        }

        # Execute signal (使用 base class execute_signal)
        try:
            # 暫時 override MAX_POSITIONS 邏輯:
            # 檢查當前已開倉嘅 positions, 只睇同 epic
            existing = self.get_positions()
            same_epic_open = any(
                p.get("epic") == epic for p in existing
            )
            total_open = len(existing)

            if same_epic_open:
                print(f"⏸️ [{market_key}] {epic} already open — skip")
                result["reason"] = "already_open"
                return result

            if total_open >= self.MAX_POSITIONS:
                print(f"⏸️ [{market_key}] max positions ({total_open}/{self.MAX_POSITIONS}) — skip")
                result["reason"] = "max_positions"
                return result

            exec_result = self.execute_signal(signal)
            if exec_result.get("executed"):
                result["executed"] = True
                print(f"✅ [{market_key}] Trade executed: {signal['direction']} {epic}")
            else:
                result["reason"] = exec_result.get("reason", "execution_failed")
                print(f"❌ [{market_key}] Execution failed: {exec_result.get('reason')}")
        except Exception as e:
            result["error"] = f"execution: {e}"
            print(f"⚠️ [{market_key}] execution exception: {e}")

        return result

    # ==================== 狀態覆蓋 ====================

    def status(self) -> Dict:
        """返回目前 engine 狀態，包含兩個市場資訊。"""
        bal = self.get_balance()
        positions = self.get_positions()
        spx_positions = [p for p in positions if p.get("epic") == "US500"]
        ndx_positions = [p for p in positions if p.get("epic") == "US100"]

        return {
            "market": self.MARKET,
            "account": self.account_key,
            "balance": bal.get("balance", 0),
            "pnl_raw": bal.get("pnl", 0),
            "pnl_adjusted": self.state.get("total_pnl_adjusted", 0),
            "positions": len(positions),
            "paused": self.is_paused(),
            "pause_reason": self.state.get("pause_reason", ""),
            "consec_losses": self.state.get("consecutive_losses", 0),
            "total_trades": self.state.get("total_trades", 0),
            "total_pnl_raw": self.state.get("total_pnl_raw", 0),
            "total_pnl_adjusted": self.state.get("total_pnl_adjusted", 0),
            "best_strategy": self.state.get("best_strategy"),
            "spx_open": len(spx_positions),
            "ndx_open": len(ndx_positions),
            "spx_positions": [
                {
                    "ticker": p.get("ticker"),
                    "direction": p.get("direction"),
                    "entry": p.get("entry"),
                    "current": p.get("current"),
                    "pnl_amt": p.get("pnl_amt", 0),
                }
                for p in spx_positions
            ],
            "ndx_positions": [
                {
                    "ticker": p.get("ticker"),
                    "direction": p.get("direction"),
                    "entry": p.get("entry"),
                    "current": p.get("current"),
                    "pnl_amt": p.get("pnl_amt", 0),
                }
                for p in ndx_positions
            ],
        }


# ==================== INDICES-SPECIFIC ACTIVE EXITS ====================

    def _fetch_spy_sma20(self) -> Optional[float]:
        """
        用 yfinance 下載 SPY 日線數據，計算 SMA20。

        Returns:
            float: SMA20 值, or None if unavailable
        """
        try:
            import yfinance as yf
            spy = yf.Ticker("SPY")
            hist = spy.history(period="2mo", interval="1d")
            if hist is not None and not hist.empty and len(hist) >= 20:
                sma20 = hist["Close"].rolling(window=20).mean().iloc[-1]
                return float(round(sma20, 2))
            print("  ⚠️ [INDICES] SPY history insufficient for SMA20")
        except ImportError:
            print("  ⚠️ [INDICES] yfinance not available for SMA20")
        except Exception as e:
            print(f"  ⚠️ [INDICES] SMA20 fetch error: {e}")
        return None

    # ==================== OVERRIDE: ACTIVE EXITS (Indices-specific) ====================

    def check_active_exits(self):
        """Indices-specific active exit management.

        Calls base class exits (regime flip, stale position, dynamic TP, smart money)
        first, then adds Indices-specific exit logic:

        1. **VIX Spike Exit** — 最核心嘅 Indices exit！
           VIX 單日升 >20% (e.g. 15→18+) → risk-off，所有 indices LONG 即走。
           讀取 entry_vix（開倉時記錄）同 current VIX 比較。

        2. **Bond Yield Spike Exit** —
           10-year yield 急升 >10bps intraday → 股市受壓 → exit。
           TODO: 接入 FRED API US10Y data。

        3. **Trailing SMA Exit**（取代固定 TP）—
           Indices 係 slow grind market，用 SMA20 做 trailing exit。
           LONG: current_price < SMA20 (跌穿) → exit
           SHORT: current_price > SMA20 (升穿) → exit
           Fallback: 冇 SMA20 → -1% hard cut
        """
        # Step 1: 先執行 base class 嘅 exit logic（regime flip / stale / dynamic TP / smart money）
        super().check_active_exits()

        # Step 2: Indices-specific exits
        positions = self.get_positions()
        if not positions:
            return

        # Refresh market data for current VIX
        try:
            market_data = self.get_market_data()
        except Exception as e:
            print(f"  ⚠️ [INDICES] check_active_exits: get_market_data failed: {e}")
            market_data = {}

        current_vix = market_data.get("vix")

        # Fetch SMA20 for trailing exit
        sma20 = self._fetch_spy_sma20()

        for t in self.trades:
            if t["status"] != "open":
                continue
            if t.get("reconciled"):
                continue  # ⛔ 唔 touch 手動 reconcile 嘅 trade

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

            should_exit = False
            exit_reason = ""

            entry_vix = t.get("entry_vix")

            # === 1. VIX SPIKE EXIT ===
            # VIX 單日升 >20% from entry → risk-off → 走
            if not should_exit and entry_vix is not None and current_vix is not None and entry_vix > 0:
                vix_change_pct = (current_vix - entry_vix) / entry_vix
                if vix_change_pct > 0.20:  # VIX >20% up spike
                    should_exit = True
                    exit_reason = (
                        f"vix_spike: VIX {entry_vix:.1f}→{current_vix:.1f} "
                        f"(+{vix_change_pct:.1%}), risk-off → exit"
                    )
                    print(f"  [INDICES] 🔴 {exit_reason}")
                    # TG notify
                    try:
                        from tg_notify import send_alert
                        send_alert(f"🔴 INDICES VIX SPIKE EXIT\n{exit_reason}")
                    except ImportError:
                        pass

            # === 2. BOND YIELD SPIKE EXIT ===
            # 10-year yield 急升 >10bps intraday → 股市受壓
            if not should_exit:
                try:
                    import requests
                    # Try FRED API for US10Y
                    fred_key = os.environ.get("FRED_API_KEY", "")
                    if fred_key:
                        us10y_url = (
                            f"https://api.stlouisfed.org/fred/series/observations"
                            f"?series_id=DGS10&sort_order=desc&limit=2"
                            f"&api_key={fred_key}&file_type=json"
                        )
                        resp = requests.get(us10y_url, timeout=10)
                        if resp.status_code == 200:
                            obs = resp.json().get("observations", [])
                            if len(obs) >= 2:
                                y_curr = float(obs[0]["value"])
                                y_prev = float(obs[1]["value"])
                                yield_bps_change = (y_curr - y_prev) * 100
                                if yield_bps_change > 10:  # >10bps intraday spike
                                    should_exit = True
                                    exit_reason = (
                                        f"yield_spike: US10Y {y_prev:.2f}→{y_curr:.2f} "
                                        f"(+{yield_bps_change:.1f}bps), pressure → exit"
                                    )
                                    print(f"  [INDICES] 🔴 {exit_reason}")
                    else:
                        # Fallback: skip with TODO note
                        print(f"  📝 [INDICES] TODO: FRED_API_KEY not set — skip bond yield check")
                except Exception as e:
                    print(f"  ⚠️ [INDICES] Bond yield check error: {e}")

            # === 3. TRAILING SMA EXIT ===
            # Indices 係 slow grind market — 用 SMA20 做 trailing exit 取代固定 TP
            if not should_exit and sma20 is not None and sma20 > 0:
                if direction == "LONG" and current_price < sma20:
                    should_exit = True
                    exit_reason = (
                        f"sma20_trailing_exit: price={current_price:.2f} < SMA20={sma20:.2f}"
                    )
                    print(f"  [INDICES] 🔴 {exit_reason}")
                elif direction == "SHORT" and current_price > sma20:
                    should_exit = True
                    exit_reason = (
                        f"sma20_trailing_exit: price={current_price:.2f} > SMA20={sma20:.2f}"
                    )
                    print(f"  [INDICES] 🔴 {exit_reason}")

            # Fallback: no SMA20 available → -1% hard cut
            if not should_exit and sma20 is None:
                if pnl_pct < -0.01:  # -1% loss
                    should_exit = True
                    exit_reason = (
                        f"hard_cut: pnl={pnl_pct:.2%} (no SMA20 available)"
                    )
                    print(f"  [INDICES] 🔴 {exit_reason}")

            if should_exit:
                print(f"  🔴 [INDICES] Closing deal {deal_id}: {exit_reason}")
                close_result = close_position(deal_id, self.account_id)
                if "error" in close_result:
                    print(f"  ❌ [INDICES] Close failed: {close_result['error']}")
                else:
                    print(f"  ✅ [INDICES] Close sent: {close_result}")
                # Mark trade as closing
                t["status"] = "closing"
                t["active_exit_reason"] = exit_reason


# ==================== 快速測試 ====================

if __name__ == "__main__":
    print("=" * 60)
    print("🔬 Indices Scalp Engine — Quick Test")
    print("=" * 60)

    engine = IndicesScalpEngine()

    # Test 1: get_market_data
    print("\n📡 Testing get_market_data()...")
    data = engine.get_market_data()
    spx = data.get("spx_data", {})
    ndx = data.get("ndx_data", {})
    print(f"   SPX bid={spx.get('bid')} offer={spx.get('offer')} mid={spx.get('mid')}")
    print(f"   NDX bid={ndx.get('bid')} offer={ndx.get('offer')} mid={ndx.get('mid')}")
    print(f"   VIX: {data.get('vix')} ({data.get('vix_regime')})")
    print(f"   Ratio: {data.get('ratio_data')}")
    print(f"   Sources alive: {data.get('sources_alive')}")
    print(f"   Sources dead: {data.get('sources_dead')}")

    # Test 2: signal for SPX
    print("\n🚦 Testing SPX signal...")
    spx_signal = engine._signal_for_market("SPX", data)
    if spx_signal:
        print(f"   ✅ SPX Signal: {spx_signal['direction']} @ {spx_signal['entry']}")
        print(f"      SL: {spx_signal['stop']} | TP: {spx_signal['target']}")
        print(f"      Reason: {spx_signal['reason']}")
    else:
        print("   ❌ No SPX signal")

    # Test 3: signal for NDX
    print("\n🚦 Testing NDX signal...")
    ndx_signal = engine._signal_for_market("NDX", data)
    if ndx_signal:
        print(f"   ✅ NDX Signal: {ndx_signal['direction']} @ {ndx_signal['entry']}")
        print(f"      SL: {ndx_signal['stop']} | TP: {ndx_signal['target']}")
        print(f"      Reason: {ndx_signal['reason']}")
    else:
        print("   ❌ No NDX signal")

    # Test 4: engine status
    print("\n📊 Testing status()...")
    status = engine.status()
    print(f"   Balance: {status.get('balance')}")
    print(f"   Positions: {status.get('positions')} (SPX: {status.get('spx_open')}, NDX: {status.get('ndx_open')})")
    print(f"   Paused: {status.get('paused')}")

    # Test 5: scan cycle
    print("\n🔄 Testing scan() cycle...")
    scan_result = engine.scan()
    print(f"   Signals found: {scan_result.get('signals_found')}")
    print(f"   Executed: {scan_result.get('executed')}")
    if "error" in scan_result:
        print(f"   Error: {scan_result['error']}")

    print("\n✅ Indices Scalp Engine test complete")
