#!/usr/bin/env python3
"""
纏論 System — Attribution & Feature Analyst (一步到位專業版)
============================================================
分析信號特徵庫 (signal_features.json) 與 實際交易結果 (trades.json)，
自動計算因子相關性與歸因，並生成 Markdown 數據分析報告。
"""
import json, os, sys
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
SYS_DIR = os.path.expanduser("~/workspace/chanlun_system")
FEATURES_FILE = os.path.join(SYS_DIR, "data/exports", "signal_features.json")
PAPER_FILE = os.path.join(SYS_DIR, "data/paper", "trades.json")
REPORT_FILE = os.path.join(SYS_DIR, "data/exports", "attribution_report.md")

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def generate_report():
    features = load_json(FEATURES_FILE, {})
    trades = load_json(PAPER_FILE, [])
    
    closed_trades = [t for t in trades if t.get("status") == "closed"]
    open_trades = [t for t in trades if t.get("status") == "open"]
    
    # 建立特徵與結果匹配
    matched_data = []
    for fkey, feat in features.items():
        # 尋找是否有對應的 closed trade
        matched_t = None
        for t in closed_trades:
            # 模糊匹配
            ttype = t.get("type", "1buy")
            tkey = f"{t['symbol']}_{t['tf']}_{ttype}_{t['entry_price']}"
            if fkey.startswith(tkey):
                matched_t = t
                break
        
        if matched_t:
            feat["pnl_pips"] = matched_t.get("pnl_amount", 0.0)
            feat["pnl_pct"] = matched_t.get("pnl", 0.0)
            feat["win"] = matched_t.get("pnl", 0.0) > 0
            matched_data.append(feat)
            
    # 開始撰寫報告
    lines = []
    lines.append("# 📊 纏論 System 3 因子歸因與策略孵化報告")
    lines.append(f"> **報告生成時間**：{datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')} HKT")
    lines.append(f"> **數據資產覆蓋**：BTC, ETH, SOL (1h / 4h)")
    lines.append("")
    
    lines.append("## 📈 1. 賬戶與持倉總覽")
    total_trades = len(trades)
    total_closed = len(closed_trades)
    total_open = len(open_trades)
    wins = sum(1 for t in closed_trades if t.get("pnl_amount", 0) > 0)
    wr = round(wins / total_closed * 100, 1) if total_closed > 0 else 0.0
    total_pnl = sum(t.get("pnl_amount", 0) for t in closed_trades)
    
    lines.append(f"*   **總交易次數**：{total_trades} 次")
    lines.append(f"*   **當前持倉數**：{total_open} 單")
    lines.append(f"*   **已平倉期數**：{total_closed} 筆")
    lines.append(f"*   **歷史勝率**：**{wr}%** ({wins}/{total_closed} 勝)")
    lines.append(f"*   **累計實現盈虧**：**${total_pnl:+.2f} USD**")
    lines.append("")
    
    if open_trades:
        lines.append("### 📊 當前持倉詳情")
        for p in open_trades:
            ptype = p.get("type", "1buy").upper()
            lines.append(f"*   **{p['symbol']} ({ptype})**: {p['direction']} 入場價 `${p['entry_price']:.1f}` | 止損 `${p['stop_loss']:.1f}` | 止盈 `${p['take_profit']:.1f}` (開倉時間: {p['entry_time']})")
        lines.append("")

    lines.append("## 🔬 2. 環境因子歸因分析 (Attribute Attribution)")
    lines.append("我哋通過收集信號觸發時的實時微觀特徵（Funding Rate, OI, Smart Money），分析它們對最終勝率的影響：")
    lines.append("")
    
    if len(matched_data) == 0:
        lines.append("⚠️ **分析提示**：當前特徵數據庫中尚無已平倉的歷史交易記錄，下方展示理論因子篩選模型。當 System 3 完成首批平倉後，此處將自動輸出實時特徵相關性矩陣。")
        lines.append("")
        
    # ── 因子 1: Funding Rate 資金費率 ──
    lines.append("### 🔴 因子 A: 資金費率 (Funding Rate) 影響")
    if matched_data:
        neg_funding = [d for d in matched_data if d.get("funding_rate", 0) < 0]
        pos_funding = [d for d in matched_data if d.get("funding_rate", 0) >= 0]
        
        neg_wr = sum(1 for d in neg_funding if d["win"]) / len(neg_funding) * 100 if neg_funding else 0
        pos_wr = sum(1 for d in pos_funding if d["win"]) / len(pos_funding) * 100 if pos_funding else 0
        
        lines.append(f"*   **負費率環境 (Short Squeeze 潛在區)**: 觸發 {len(neg_funding)} 次 | 勝率 **{neg_wr:.1f}%**")
        lines.append(f"*   **正/平費率環境 (常態多頭區)**: 觸發 {len(pos_funding)} 次 | 勝率 **{pos_wr:.1f}%**")
    else:
        lines.append("*   **假說**：當觸發 **1买 (底背馳超跌)** 時，若 Funding Rate < 0%（市場空頭過度擁擠），往往容易引發空頭踩踏 (Short Squeeze)，大幅提升 1买 暴漲勝率。")
    lines.append("")
    
    # ── 因子 2: Smart Money 莊家大戶多空比 ──
    lines.append("### 🐋 因子 B: 幣安大戶多空比 (Whale Long/Short Ratio)")
    if matched_data:
        high_sm = [d for d in matched_data if d.get("whale_ls_ratio", 0) > 0.3]
        low_sm = [d for d in matched_data if d.get("whale_ls_ratio", 0) <= 0.3]
        
        high_wr = sum(1 for d in high_sm if d["win"]) / len(high_sm) * 100 if high_sm else 0
        low_wr = sum(1 for d in low_sm if d["win"]) / len(low_sm) * 100 if low_sm else 0
        
        lines.append(f"*   **大戶看多區 (LS Ratio > 0.3)**: 觸發 {len(high_sm)} 次 | 勝率 **{high_wr:.1f}%**")
        lines.append(f"*   **大戶看空區 (LS Ratio <= 0.3)**: 觸發 {len(low_sm)} 次 | 勝率 **{low_wr:.1f}%**")
    else:
        lines.append("*   **假說**：若纏論結構性買點觸發時，Whale Long/Short Ratio 同步高企 (大戶重倉多單)，多頭共振勝率將會顯著上升。")
    lines.append("")
    
    # ── 因子 3: RSI 14 超買超賣 ──
    lines.append("### ⚡ 因子 C: 強度指標 RSI(14) 狀態")
    if matched_data:
        oversold = [d for d in matched_data if d.get("rsi_14", 50) < 30]
        normal_rsi = [d for d in matched_data if d.get("rsi_14", 50) >= 30]
        
        os_wr = sum(1 for d in oversold if d["win"]) / len(oversold) * 100 if oversold else 0
        norm_wr = sum(1 for d in normal_rsi if d["win"]) / len(normal_rsi) * 100 if normal_rsi else 0
        
        lines.append(f"*   **極度超賣區 (RSI < 30)**: 觸發 {len(oversold)} 次 | 勝率 **{os_wr:.1f}%**")
        lines.append(f"*   **常態強弱區 (RSI >= 30)**: 觸發 {len(normal_rsi)} 次 | 勝率 **{norm_wr:.1f}%**")
    else:
        lines.append("*   **假說**：1买 作為左側抄底信號，如果 RSI 同步殺穿至 30 以下，極致的恐慌往往能提供最安全、最厚的止損安全邊界。")
    lines.append("")
    
    # ── 3. 信號類型歷史表現 ──
    lines.append("## 🏆 3. 纏論信號類型排行榜 (Type Leaderboard)")
    tracker_state = load_json(os.path.join(SYS_DIR, "data/signals/tracker.json"))
    stats = tracker_state.get("stats", {})
    by_type = stats.get("by_type", {})
    
    if by_type:
        sorted_types = sorted(by_type.items(), key=lambda x: x[1].get("win_rate", 0), reverse=True)
        lines.append("根據歷史已平倉數據，各纏論信號類型表現排名如下：")
        lines.append("")
        for rank, (t, stat) in enumerate(sorted_types, 1):
            lines.append(f"{rank}.  **{t.upper()}** (總次數: {stat['total']} | 勝率: **{stat['win_rate']}%** | 平均單筆回報: {stat['avg_return']:+.2f}%)")
    else:
        lines.append("⏱️ 尚無已平倉信號，排行榜載入中...")
    lines.append("")
    
    # ── 4. 戰略反哺 System 1 建議 ──
    lines.append("## 🚀 4. 戰略反哺與實盤進化建議")
    lines.append("System 3 作為一個獨立的策略試驗田，其產出的數據將直接反哺我哋嘅實盤 System 1：")
    lines.append("1.  **動態倉位控制**：若 System 3 驗證出『1买觸發 + Funding < 0』勝率大於 75%，我們將直接在 System 1 中寫入規則：一旦滿足該共振，實盤開倉自信度倍率 (Confidence Multiplier) 提升至 1.5x。")
    lines.append("2.  **雷區避險**：如果某個信號類型（例如 3买）在歷史 20 次交易中勝率低於 40%，我們將在 System 1 實盤中設置**一票否決制**，直接過濾掉實盤中對應的突破單，避免無謂磨損。")
    
    # 寫入檔案
    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(lines))
        
    print(f"🎉 歸因報告生成成功：{REPORT_FILE}")
    return REPORT_FILE

if __name__ == "__main__":
    generate_report()
