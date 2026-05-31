#!/usr/bin/env python3
"""
⚡ GOLD SWING ENGINE — 黃金專屬宏觀短線引擎 (Swing 版)
====================================================
GOLD 受美元指數 (DXY) 及避險情緒 (VIX) 影響極大。
本引擎用 DXY 反向關係 + VIX regime 做 edge。
繼承 SwingEngine base，用 flow bias hook 做跨市場偏見。

數據源 (get_market_data):
  1. Capital.com GOLD bid/offer/mid price（用 get_market_info）
  2. EURUSD mid price（DXY proxy — EURUSD 升 = DXY 跌 = GOLD 升）
  3. VIX 水平（yfinance ^VIX — 高 VIX → GOLD 避險需求↑）
  4. Spread width（bid/offer 差距做 volatility proxy）

Signal 邏輯 (generate_signal):
  三層 filter：
    1. DXY 反向: EURUSD 過去 trend（與 state 嘅 previous EURUSD 比較）
    2. VIX regime: VIX > 25 → GOLD LONG bias（避險）; VIX < 15 → GOLD SHORT bias（complacency）
    3. Price momentum: GOLD mid vs 之前儲落嘅 previous GOLD price

Usage:
  from scalp_engines.gold_scalp import GoldScalpEngine
  engine = GoldScalpEngine()
  result = engine.scan()
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any

import yfinance as yf

HKT = timezone(timedelta(hours=8))

# 匯入 base class
sys.path.insert(0, os.path.expanduser("~/workspace"))
from scalp_engines.engine_base import SwingEngine, _atomic_save_json
from capitalcom_paper import get_market_info


# ==================== GOLD Swing Engine ====================

class GoldScalpEngine(SwingEngine):

    # === Gold-specific config ===
    VIX_HIGH_THRESHOLD: float = 25.0
    VIX_LOW_THRESHOLD: float = 15.0

    def _get_engine_param_map(self) -> dict:
        return {
            "vix_high_threshold": "VIX_HIGH_THRESHOLD",
            "vix_low_threshold": "VIX_LOW_THRESHOLD",
        }

    """GOLD (XAUUSD) 專屬 swing engine — DXY inverse + VIX regime 做 edge，flow bias hook 整合。"""

    MARKET = "XAUUSD"
    EPIC = "GOLD"
    ACCOUNT_KEY = "gold"

    # Risk: GOLD 波幅適中，用 swing 設定（比 scalp 更闊）
    MAX_POSITIONS = 1
    MAX_EXPOSURE_PCT = 0.70
    STOP_LOSS_PCT = 0.04         # 2.0% — Swing 闊 SL，比 scalp 0.8% 更寬容
    TAKE_PROFIT_PCT = 0.03       # 5.0% — 1:2.5 RR 目標
    RISK_PER_TRADE_PCT = 0.02    # 每次冒 2% 資金
    MAX_CONSEC_LOSSES = 5       # 連輸 5 次暫停

    # VIX threshold — 高於此值視為避險模式，低於此值視為 complacency
    VIX_HIGH_THRESHOLD = 25.0
    VIX_LOW_THRESHOLD = 15.0    # VIX < 15 → SHORT bias（資金流向 risk assets）

    def __init__(self, name: str = None):
        super().__init__(name or "gold")
        # 確保 state 有 GOLD 引擎專屬嘅 persistent field
        if "previous_eurusd" not in self.state:
            self.state["previous_eurusd"] = None
        if "previous_gold" not in self.state:
            self.state["previous_gold"] = None
        print(f"🏆 GOLD Swing Engine initialized — max_consec_losses={self.MAX_CONSEC_LOSSES}")

    # ==================== FLOW BIAS HOOK (override) ====================

    def interpret_flow_bias(self, flow_data: Dict) -> Dict:
        """
        GOLD 專屬 flow bias 解讀。

        GOLD 喺市場恐慌時係避險資產：
          - VIX fear/high（>25）→ LONG bias +1（資金湧入 gold）
          - VIX 低且 complacency → SHORT bias（資金流向 risk assets）

        美元反向關係：
          - EURUSD 升（DXY 跌）→ LONG bias +1（美元弱 = 金價升）
          - EURUSD 跌（DXY 升）→ SHORT bias +1（美元強 = 金價跌）

        Returns:
            {"LONG": int, "SHORT": int} — bias adjustment counts
        """
        bias = {"LONG": 0, "SHORT": 0}
        raw = flow_data.get("raw_data", {})

        # --- VIX fear → GOLD LONG（避險需求↑）---
        vix = raw.get("vix", 0)
        if vix > self.VIX_HIGH_THRESHOLD:
            bias["LONG"] += 1
            print(f"  [FLOW][GOLD] VIX={vix:.1f} > {self.VIX_HIGH_THRESHOLD} → 避險 LONG +1")
        elif vix < self.VIX_LOW_THRESHOLD:
            bias["SHORT"] += 1
            print(f"  [FLOW][GOLD] VIX={vix:.1f} < {self.VIX_LOW_THRESHOLD} → complacency SHORT +1")

        # --- EURUSD proxy for DXY ---
        # flow_data 嘅 raw_data 有無 eurusd？嘗試由 flow_state.json 嘅 signals 拎
        eurusd_flow = raw.get("eurusd", 0)
        eurusd_change = raw.get("eurusd_change_pct", 0)
        if eurusd_change > 0.0005:
            bias["LONG"] += 1
            print(f"  [FLOW][GOLD] EURUSD 升 {eurusd_change:.4%} → DXY 跌 → LONG +1")
        elif eurusd_change < -0.0005:
            bias["SHORT"] += 1
            print(f"  [FLOW][GOLD] EURUSD 跌 {eurusd_change:.4%} → DXY 升 → SHORT +1")

        print(f"  [FLOW][GOLD] Final bias: LONG={bias['LONG']} SHORT={bias['SHORT']}")
        return bias

    # ==================== DATA SOURCES ====================

    def get_market_data(self) -> Dict:
        """
        收集 GOLD 專屬宏觀市場數據。

        Returns dict with:
          - gold_price: float (Capital.com mid)
          - gold_bid: float
          - gold_offer: float
          - spread: float (offer - bid)
          - eurusd_price: float (Capital.com mid, for DXY proxy)
          - eurusd_bid: float
          - eurusd_offer: float
          - vix: float (^VIX via yfinance, or None)
          - source: str (data provenance)
        """
        data = {
            "gold_price": None,
            "gold_bid": None,
            "gold_offer": None,
            "spread": 0.0,
            "eurusd_price": None,
            "eurusd_bid": None,
            "eurusd_offer": None,
            "vix": None,
            "source": "none",
        }

        # --- Step 1: Capital.com GOLD bid/offer/mid ---
        try:
            gold_info = get_market_info(self.EPIC, self.account_id)
            if gold_info and "bid" in gold_info and gold_info["bid"] is not None:
                bid = float(gold_info["bid"])
                offer = float(gold_info.get("offer", bid))
                data["gold_bid"] = bid
                data["gold_offer"] = offer
                data["gold_price"] = (bid + offer) / 2.0
                data["spread"] = offer - bid
                data["source"] = "capitalcom"
            else:
                print(f"[GOLD] Capital.com market info incomplete: {gold_info}")
        except Exception as e:
            print(f"[GOLD] Capital.com GOLD fetch failed: {e}")

        # --- Step 2: EURUSD (DXY proxy) ---
        try:
            eurusd_info = get_market_info("EURUSD", self.account_id)
            if eurusd_info and "bid" in eurusd_info and eurusd_info["bid"] is not None:
                eurusd_bid = float(eurusd_info["bid"])
                eurusd_offer = float(eurusd_info.get("offer", eurusd_bid))
                data["eurusd_bid"] = eurusd_bid
                data["eurusd_offer"] = eurusd_offer
                data["eurusd_price"] = (eurusd_bid + eurusd_offer) / 2.0
            else:
                print(f"[GOLD] EURUSD market info incomplete: {eurusd_info}")
        except Exception as e:
            print(f"[GOLD] EURUSD fetch failed: {e}")

        # --- Step 3: VIX via yfinance ---
        try:
            vix_ticker = yf.Ticker("^VIX")
            vix_data = vix_ticker.history(period="1d", interval="1m")
            if vix_data is not None and not vix_data.empty:
                data["vix"] = float(vix_data["Close"].iloc[-1])
            else:
                # Fallback: try daily close
                vix_data = vix_ticker.history(period="1d")
                if vix_data is not None and not vix_data.empty:
                    data["vix"] = float(vix_data["Close"].iloc[-1])
        except Exception as e:
            print(f"[GOLD] VIX fetch failed (non-fatal): {e}")

        # --- Step 4: 檢查 GOLD 價格 ---
        if data["gold_price"] is None:
            print("[GOLD] ⚠️ No GOLD price from any data source!")

        return data

    # ==================== SIGNAL GENERATION ====================

    def generate_signal(self, data: Dict) -> Optional[Dict]:
        """
        根據宏觀市場數據產生 GOLD 交易訊號。

        三層 filter：
          1. DXY 反向: EURUSD trend（與 previous 比較）→ 決定 bias
          2. VIX regime: VIX > 25 → LONG bias（避險資金流入）; VIX < 15 → SHORT bias（complacency）
          3. GOLD price momentum: 現價 vs previous gold price

        三層共識決定最終方向；如果冇共識就 None。
        """
        gold_price = data.get("gold_price")
        if gold_price is None:
            print("[GOLD] No price — cannot generate signal")
            return None

        # 讀取 previous state
        prev_eurusd = self.state.get("previous_eurusd")
        prev_gold = self.state.get("previous_gold")

        # 更新 state（記低今次價格，下次用）
        eurusd_price = data.get("eurusd_price")
        if eurusd_price is not None:
            self.state["previous_eurusd"] = eurusd_price
        if gold_price is not None:
            self.state["previous_gold"] = gold_price
        _atomic_save_json(self.state_file, self.state)

        # === Layer 1: DXY Inverse (EURUSD proxy) ===
        # EURUSD ↑ → DXY ↓ → GOLD ↑ (LONG bias)
        # EURUSD ↓ → DXY ↑ → GOLD ↓ (SHORT bias)
        dxy_bias = None  # None = neutral
        if eurusd_price is not None and prev_eurusd is not None and prev_eurusd > 0:
            eurusd_change_pct = (eurusd_price - prev_eurusd) / prev_eurusd
            # 用 0.05% 做 threshold，過濾 noise
            if eurusd_change_pct > 0.0005:
                dxy_bias = "LONG"   # EURUSD 升 → DXY 跌 → GOLD 升
            elif eurusd_change_pct < -0.0005:
                dxy_bias = "SHORT"  # EURUSD 跌 → DXY 升 → GOLD 跌
            print(f"[GOLD] DXY proxy: EURUSD change={eurusd_change_pct:.5%} → bias={dxy_bias}")
        elif prev_eurusd is None:
            print("[GOLD] DXY proxy: first run, no previous EURUSD — skip")

        # === Layer 2: VIX Regime ===
        # VIX > 25 → 市場恐慌 → GOLD 避險需求 ↑ → LONG bias
        # VIX < 15 → 市場 complacency → GOLD 弱（資金流向 risk assets）→ SHORT bias
        vix_bias = None
        vix = data.get("vix")
        vix_edge_weight = self.edge_weights.get("vix_regime", 1.0)
        if vix_edge_weight >= 0.1 and vix is not None:
            if vix > self.VIX_HIGH_THRESHOLD:
                vix_bias = "LONG"
                print(f"[GOLD] VIX regime: VIX={vix:.1f} > {self.VIX_HIGH_THRESHOLD} → 避險 LONG bias")
            elif vix < self.VIX_LOW_THRESHOLD:
                vix_bias = "SHORT"
                print(f"[GOLD] VIX regime: VIX={vix:.1f} < {self.VIX_LOW_THRESHOLD} → complacency SHORT bias")
            else:
                print(f"[GOLD] VIX regime: VIX={vix:.1f} ({self.VIX_LOW_THRESHOLD}-{self.VIX_HIGH_THRESHOLD}) → neutral")

        # === Layer 3: GOLD Price Momentum ===
        momentum_bias = None
        if prev_gold is not None and prev_gold > 0:
            gold_change_pct = (gold_price - prev_gold) / prev_gold
            # 用 0.1% 做 threshold
            if gold_change_pct > 0.001:
                momentum_bias = "LONG"
            elif gold_change_pct < -0.001:
                momentum_bias = "SHORT"
            print(f"[GOLD] Price momentum: change={gold_change_pct:.4%} → bias={momentum_bias}")
        else:
            print("[GOLD] Price momentum: first run, no previous price — skip")

        # === 綜合判斷 ===
        # 計分: LONG +1, SHORT -1, neutral 0
        score = 0
        score += 1 if dxy_bias == "LONG" else (-1 if dxy_bias == "SHORT" else 0)
        score += 1 if vix_bias == "LONG" else (-1 if vix_bias == "SHORT" else 0)  # VIX 雙向: >25 LONG, <15 SHORT
        score += 1 if momentum_bias == "LONG" else (-1 if momentum_bias == "SHORT" else 0)

        direction = None
        strategy = None
        reasons = []

        if score >= 1:  # 1-way consensus 就夠 (was 2) — 更積極捕捉 GOLD 機會
            direction = "LONG"
            strategy = "gold_dxy_vix_momentum_long"
            if dxy_bias == "LONG":
                reasons.append("DXY 跌 (EURUSD 升)")
            if vix_bias == "LONG":
                reasons.append(f"VIX={vix:.1f} 避險")
            if momentum_bias == "LONG":
                reasons.append("price momentum 向上")
        elif score <= -1:
            direction = "SHORT"
            strategy = "gold_dxy_short"
            if dxy_bias == "SHORT":
                reasons.append("DXY 升 (EURUSD 跌)")
            if momentum_bias == "SHORT":
                reasons.append("price momentum 向下")
            if vix_bias == "SHORT":
                reasons.append(f"VIX={vix:.1f} complacency")
            if vix_bias == "LONG" and dxy_bias == "SHORT" and momentum_bias == "SHORT":
                # VIX 高但 DXY 強 + 金價跌 → VIX 可信度低，跟 DXY+momentum
                reasons.append("VIX 避險被 DXY 壓制")
        else:
            print(f"[GOLD] No consensus: score={score} (dxy={dxy_bias}, vix={vix_bias}, momentum={momentum_bias})")
            return None

        if not direction:
            # 清空 entry VIX/EURUSD 記錄（冇信號）
            self._entry_vix = None
            self._entry_eurusd = None
            return None

        # 記錄入場時 VIX 同 EURUSD（比 exit management 用）
        self._entry_vix = data.get("vix")
        self._entry_eurusd = data.get("eurusd_price")

        # 計算 entry/stop/target
        entry = round(gold_price, 2)
        if direction == "LONG":
            stop = round(entry * (1 - self.STOP_LOSS_PCT), 2)
            target = round(entry * (1 + self.TAKE_PROFIT_PCT), 2)
        else:
            stop = round(entry * (1 + self.STOP_LOSS_PCT), 2)
            target = round(entry * (1 - self.TAKE_PROFIT_PCT), 2)

        reason_str = " + ".join(reasons) if reasons else f"score={score}"
        full_reason = f"{direction} signal: {reason_str}"

        return {
            "strategy": strategy,
            "epic": self.EPIC,
            "label": "XAUUSD",
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            "reason": full_reason,
        }

    # ==================== OVERRIDE: ACTIVE EXITS (Gold-specific) ====================

    def check_active_exits(self):
        """Gold-specific active exit management.

        Calls base class exits (regime flip, stale position, dynamic TP, smart money)
        first, then adds Gold-specific exit logic:

        1. **VIX Regime Change Exit** — Gold嘅最核心 exit。
           Gold通常喺 VIX fear (>25) 嗰陣入LONG。
           如果 VIX 由 fear 跌返落 low (<20)，fear premium 消失 → 即走。

        2. **DXY Spike Exit** — EURUSD 跌 >1.5% = DXY 急升 → gold 受壓 → exit。

        3. **Time-Based Exit** — Gold 唔係 momentum market，唔應該等固定 TP。
           - 持倉 >4hr 浮贏 >0.5% 未到 TP → market close
           - 持倉 >2hr 浮輸 >0.3% → cut loss
        """
        # Step 1: 先執行 base class 嘅 exit logic（regime flip / stale / dynamic TP / smart money）
        super().check_active_exits()

        # Step 2: Gold-specific exits
        positions = self.get_positions()
        if not positions:
            return

        # 攞最新 market data（含 VIX 同 EURUSD）
        try:
            market_data = self.get_market_data()
        except Exception as e:
            print(f"[GOLD] check_active_exits: get_market_data failed: {e}")
            market_data = {}

        current_vix = market_data.get("vix")
        current_eurusd = market_data.get("eurusd_price")
        entry_vix = getattr(self, "_entry_vix", None)
        entry_eurusd = getattr(self, "_entry_eurusd", None)

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

            age_minutes = (time.time() - t["timestamp_ts"]) / 60
            now = datetime.now(HKT)

            should_exit = False
            exit_reason = ""

            # === 1. VIX REGIME CHANGE EXIT ===
            # 入場時 VIX > 25 (fear)，而家 VIX < 20 (low) → fear 消除 → 走
            if not should_exit and entry_vix is not None and current_vix is not None:
                if direction == "LONG" and entry_vix > 25.0 and current_vix < 20.0:
                    should_exit = True
                    exit_reason = (
                        f"vix_regime_change: entry_vix={entry_vix:.1f}→current={current_vix:.1f} "
                        f"fear resolved → cut LONG"
                    )
                    print(f"  [GOLD] {exit_reason}")

            # === 2. DXY SPIKE EXIT (EURUSD proxy) ===
            # EURUSD 跌 >1.5% from entry → DXY 急升 → gold 受壓
            if not should_exit and entry_eurusd is not None and current_eurusd is not None and entry_eurusd > 0:
                eurusd_change_pct = (current_eurusd - entry_eurusd) / entry_eurusd
                if eurusd_change_pct < -0.015:  # EURUSD 跌 >1.5% = DXY 升 >1.5%
                    should_exit = True
                    exit_reason = (
                        f"dxy_spike: EURUSD {eurusd_change_pct:.2%} from entry "
                        f"(entry_eurusd={entry_eurusd:.5f}→{current_eurusd:.5f}) "
                        f"→ DXY surging, exit"
                    )
                    print(f"  [GOLD] {exit_reason}")

            # === 3. TIME-BASED EXIT ===
            # Gold 唔係 momentum market，唔等固定 TP
            # a) 持倉 >4hr，浮贏 >0.5%，未到 TP → 市價平倉食糊
            if not should_exit and age_minutes > 240:  # 4 hours
                if pnl_pct > 0.005:  # 浮贏 >0.5%
                    should_exit = True
                    exit_reason = (
                        f"time_profit_exit: {age_minutes:.0f}min, pnl={pnl_pct:.2%} "
                        f"→ market close (gold no momentum)"
                    )
                    print(f"  [GOLD] {exit_reason}")

            # b) 持倉 >2hr，浮輸 >0.3% → cut loss
            if not should_exit and age_minutes > 120:  # 2 hours
                if pnl_pct < -0.003:  # 浮輸 >0.3%
                    should_exit = True
                    exit_reason = (
                        f"time_loss_exit: {age_minutes:.0f}min, pnl={pnl_pct:.2%} "
                        f"→ cut loss (gold not recovering)"
                    )
                    print(f"  [GOLD] {exit_reason}")

            # === Execute exit ===
            if should_exit and deal_id:
                print(f"  🛑 [GOLD active_exit] {direction}: {exit_reason}")
                result = close_position(deal_id, self.account_id)
                if "error" not in result:
                    t["status"] = "closed"
                    t["closed_at"] = now.isoformat()
                    t["closed_at_ts"] = time.time()
                    t["close_reason"] = exit_reason
                    # ── TG通知 ──
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
                    print(f"  ❌ [GOLD] Active exit failed: {result['error']}")

        _atomic_save_json(self.trade_file, self.trades)

    # ==================== OVERRIDE: STATUS ====================

    def status(self) -> Dict:
        base = super().status()
        base["max_consec_losses"] = self.MAX_CONSEC_LOSSES
        base["stop_loss_pct"] = self.STOP_LOSS_PCT
        base["take_profit_pct"] = self.TAKE_PROFIT_PCT
        base["vix_high_threshold"] = self.VIX_HIGH_THRESHOLD
        base["state_prev_eurusd"] = self.state.get("previous_eurusd")
        base["state_prev_gold"] = self.state.get("previous_gold")
        return base


# ==================== MAIN (standalone test) ====================

if __name__ == "__main__":
    print("=" * 60)
    print("GOLD Swing Engine — 獨立測試")
    print("=" * 60)

    engine = GoldScalpEngine()

    # Test 1: fetch data
    print("\n📡 Fetching market data...")
    data = engine.get_market_data()
    print(f"   GOLD price:  {data.get('gold_price')}")
    print(f"   GOLD bid:    {data.get('gold_bid')}")
    print(f"   GOLD offer:  {data.get('gold_offer')}")
    print(f"   Spread:      {data.get('spread'):.4f}")
    print(f"   EURUSD:      {data.get('eurusd_price')}")
    print(f"   VIX:         {data.get('vix')}")
    print(f"   Source:      {data.get('source')}")

    # Test 2: generate signal
    print("\n📊 Generating signal (run 1 — first pass, no prev state)...")
    signal = engine.generate_signal(data)
    if signal:
        print(f"   ✅ Signal found:")
        print(f"      Strategy:  {signal['strategy']}")
        print(f"      Direction: {signal['direction']}")
        print(f"      Entry:     {signal['entry']}")
        print(f"      Stop:      {signal['stop']}")
        print(f"      Target:    {signal['target']}")
        print(f"      Reason:    {signal['reason']}")
    else:
        print(f"   ❌ No signal (first run — need previous price for momentum)")

    # Test 3: run again with same data to simulate second pass
    print("\n📊 Generating signal (run 2 — with prev state)...")
    signal2 = engine.generate_signal(data)
    if signal2:
        print(f"   ✅ Signal found:")
        print(f"      Strategy:  {signal2['strategy']}")
        print(f"      Direction: {signal2['direction']}")
        print(f"      Entry:     {signal2['entry']}")
        print(f"      Stop:      {signal2['stop']}")
        print(f"      Target:    {signal2['target']}")
        print(f"      Reason:    {signal2['reason']}")
    else:
        print(f"   ❌ No signal")

    # Test 4: engine status
    print("\n📋 Engine status:")
    st = engine.status()
    print(f"   Market:          {st['market']}")
    print(f"   Account:         {st['account']}")
    print(f"   Balance:         {st['balance']}")
    print(f"   Positions:       {st['positions']}")
    print(f"   Paused:          {st['paused']}")
    print(f"   Prev EURUSD:     {st.get('state_prev_eurusd')}")
    print(f"   Prev GOLD:       {st.get('state_prev_gold')}")

    print("\n✅ GOLD Scalp Engine test complete")
