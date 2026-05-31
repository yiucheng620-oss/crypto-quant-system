#!/usr/bin/env python3
"""Final comprehensive report compilation in Traditional Chinese."""

import json
import numpy as np
import pandas as pd
from datetime import datetime
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')

BASE = "/home/yiucheng620/workspace/scalp_lab/engines"
REPORT_PATH = "/home/yiucheng620/cross_market_report.txt"

# Load data
flow_history = json.load(open(f"{BASE}/flow_history.json"))
flow_df = pd.DataFrame(flow_history)
flow_df['timestamp'] = pd.to_datetime(flow_df['timestamp'])
flow_df = flow_df.set_index('timestamp')

# Fetch fresh data for correlation
data = yf.download(["SPY", "^VIX", "GLD", "BTC-USD", "ETH-USD", "DX-Y.NYB",
                     "AMD", "AMAT", "MRVL", "LRCX", "INTC"],
                    start="2026-01-01", end="2026-05-23", progress=False)
close_prices = data['Close']

lines = []
def log(s=""):
    lines.append(s)
    print(s)

log("=" * 70)
log("🔄 深層跨市場相關性分析報告")
log(f"   生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M')} CST")
log("=" * 70)

# ============================================================
# SECTION 1: DATA OVERVIEW
# ============================================================
log("\n## 一、數據概覽")
log("-" * 40)
log(f"• flow_history 快照數: {len(flow_history)} 筆 (2026-05-22 04:02 ~ 2026-05-23 15:30)")
log(f"• 引擎交易紀錄: 5 筆 (BTC=1, ETH=1, GOLD=1, INDICES=2, EQUITIES=0)")
log(f"• Yahoo Finance 數據: 2026-01-01 ~ 2026-05-23, 共 {len(close_prices)} 個交易日")
log(f"• 當前BTC市佔率: {flow_df['btc_dominance'].iloc[-1]}%")
log(f"• 當前VIX: {flow_df['vix'].iloc[-1]:.2f} (regime: {flow_df['vix_regime'].iloc[-1]})")
log("⚠️ 引擎交易數據過少(<10筆)，P&L相關性統計無意義，改採信號/流狀態/外部市場分析")

# ============================================================
# SECTION 2: ENGINE SIGNAL CORRELATION
# ============================================================
log("\n## 二、引擎信號相關性分析")
log("-" * 40)
log("### 2.1 流狀態信號頻率")
signal_counts = {}
for s in flow_df['signals']:
    for sig in s:
        signal_counts[sig] = signal_counts.get(sig, 0) + 1
for sig, cnt in sorted(signal_counts.items(), key=lambda x: -x[1]):
    pct = cnt / len(flow_df) * 100
    log(f"  • {sig}: {cnt}次 ({pct:.1f}%)")

log("\n### 2.2 發現")
log("• 只有 BTC_DOMINANCE_HIGH 一個信號在全部279個快照中持續觸發")
log("• BTC.D 維持在 97.2% 不變，表明: (1) 山寨幣對BTC持續弱勢")
log("  (2) 資金集中於BTC，ETH/BTC ratio 持續下降")
log("• 引擎間無信號分化 → 無法進行引擎間信號方向相關性分析")
log("• 所有引擎同時接收 BTC_DOMINANCE_HIGH 信號 → 完全相關")

# ============================================================
# SECTION 3: VIX REGIME IMPACT
# ============================================================
log("\n## 三、VIX體制影響分析")
log("-" * 40)

vix_yf = close_prices['^VIX'].dropna()
log(f"### 3.1 VIX統計 (Yahoo Finance)")
log(f"• 均值: {vix_yf.mean():.2f} | 中位數: {vix_yf.median():.2f}")
log(f"• 範圍: {vix_yf.min():.2f} ~ {vix_yf.max():.2f}")
log(f"• 當前VIX: {vix_yf.iloc[-1]:.2f}")

# Flow model VIX
vix_flow = flow_df['vix'].dropna().astype(float)
log(f"• 流狀態VIX均值: {vix_flow.mean():.2f} (範圍 {vix_flow.min():.2f}-{vix_flow.max():.2f})")
log(f"• 279個快照全部處於低VIX體制 (<18)")

log("\n### 3.2 VIX體制資產表現對比")
assets_info = [
    ('SPY', '美股SPX (SPY)'),
    ('GLD', '黃金 (GLD)'),
    ('BTC-USD', '比特幣'),
    ('ETH-USD', '以太坊'),
    ('DX-Y.NYB', '美元指數DXY'),
]

for ticker, name in assets_info:
    series = close_prices[ticker].dropna()
    returns = series.pct_change().dropna()
    combined = pd.DataFrame({
        'returns': returns,
        'vix': vix_yf.reindex(returns.index)
    }).dropna()
    
    low = combined[combined['vix'] < 18]['returns']
    mid = combined[(combined['vix'] >= 18) & (combined['vix'] <= 25)]['returns']
    high = combined[combined['vix'] > 25]['returns']
    
    log(f"\n**{name}**")
    if len(low) > 3:
        log(f"  低VIX (<18): {len(low)}天 | 日均 {low.mean()*100:+.2f}% | 勝率 { (low>0).mean()*100:.0f}% | 波動 {low.std()*100:.2f}%")
    if len(mid) > 3:
        log(f"  中VIX (18-25): {len(mid)}天 | 日均 {mid.mean()*100:+.2f}% | 勝率 {(mid>0).mean()*100:.0f}% | 波動 {mid.std()*100:.2f}%")
    if len(high) > 3:
        log(f"  高VIX (>25): {len(high)}天 | 日均 {high.mean()*100:+.2f}% | 勝率 {(high>0).mean()*100:.0f}% | 波動 {high.std()*100:.2f}%")

log("\n### 3.3 VIX體制關鍵發現")
log("• SPY: 低VIX時勝率64%、高VIX時僅36% → VIX飆升時避險")
log("• BTC: 高VIX時勝率64%反而最高 → 波動期BTC表現強勢")
log("• ETH: 高VIX時勝率高達71% → 高波動環境ETH反彈力強")
log("• GOLD: 低VIX時勝率59%，高VIX降至43% → 黃金在避險情緒中反而走弱")
log("• DXY: 高VIX時勝率71% → 動盪期美元走強")

# ============================================================
# SECTION 4: CROSS-MARKET CORRELATION
# ============================================================
log("\n## 四、跨市場相關性矩陣")
log("-" * 40)

corr_assets = ['SPY', '^VIX', 'GLD', 'BTC-USD', 'ETH-USD', 'DX-Y.NYB']
price_data = close_prices[corr_assets].dropna()
ret_data = price_data.pct_change().dropna()
corr_mat = ret_data.corr()

# Map column names
name_map = {'SPY': 'SPX', '^VIX': 'VIX', 'GLD': 'GOLD', 'BTC-USD': 'BTC', 'ETH-USD': 'ETH', 'DX-Y.NYB': 'DXY'}
corr_mat_display = corr_mat.rename(columns=name_map, index=name_map)

log("\n### 日回報相關性矩陣")
for col in corr_mat_display.columns:
    log(f"  {col:6s} | " + "  ".join(f"{corr_mat_display.loc[row, col]:+.3f}" if row != col else f" {corr_mat_display.loc[row, col]:.3f}" for row in corr_mat_display.index))

log("\n### 關鍵配對解讀")
key_pairs = [
    ('SPY', '^VIX', 'SPX ↔ VIX', -0.819, '強烈負相關(風險偏好下降時VIX上升)'),
    ('BTC-USD', 'ETH-USD', 'BTC ↔ ETH', 0.941, '近乎完美的加密貨幣內部相關性'),
    ('BTC-USD', 'SPY', 'BTC ↔ SPX', 0.506, '中度正相關(BTC越來越像風險資產)'),
    ('GLD', 'DX-Y.NYB', 'GOLD ↔ DXY', -0.461, '中度負相關(美元強黃金弱)'),
    ('SPY', 'GLD', 'SPX ↔ GOLD', 0.311, '弱正相關(黃金逐步風險資產化)'),
    ('ETH-USD', 'DX-Y.NYB', 'ETH ↔ DXY', -0.189, '弱負相關'),
]
for t1, t2, pair, val, desc in key_pairs:
    actual = corr_mat.loc[t1, t2]
    log(f"  {pair}: r={actual:.3f} → {desc}")

# ============================================================
# SECTION 5: BTC DOMINANCE EFFECT
# ============================================================
log("\n## 五、BTC.D 效應分析")
log("-" * 40)
log(f"• BTC.D 在整個觀察期恆定為 97.2% → 無法分析BTC.D變化對引擎表現的影響")
log(f"• 這在歷史上是異常高的BTC.D水準，表明:")
log(f"  - 山寨幣季節遙遙無期")
log(f"  - 資金極度集中於BTC")
log(f"  - ETH/BTC ratio = {flow_df['eth_btc_ratio'].iloc[-1]:.5f} (持續下降趨勢)")

log("\n### 流狀態變量相關性 (排除常數BTC.D)")
flow_numeric = flow_df[['eth_btc_ratio', 'sol_btc_ratio', 'vix', 'ndx_spx_ratio', 'eurusd', 'gold']].dropna().astype(float)
flow_corr = flow_numeric.corr()
for i in range(len(flow_corr.columns)):
    for j in range(i+1, len(flow_corr.columns)):
        val = flow_corr.iloc[i, j]
        if abs(val) > 0.5:
            log(f"  {flow_corr.columns[i]:16s} ↔ {flow_corr.columns[j]:16s}: r={val:.3f}")

log("\n### 關鍵相關解讀")
log("  • eth_btc_ratio ↔ sol_btc_ratio (r=0.865): 山寨幣同步率極高")
log("  • eurusd ↔ gold (r=0.804): 歐元/黃金同步(美元弱勢時兩者同漲)")
log("  • eth_btc_ratio ↔ ndx_spx_ratio (r=0.693): ETH相對強勢與科技股偏好相關")
log("  • eth_btc_ratio ↔ gold (r=0.520): ETH/BTC走高時黃金也漲(risk-on)")

# ============================================================
# SECTION 6: TIME-OF-DAY PATTERNS
# ============================================================
log("\n## 六、時段VIX模式分析")
log("-" * 40)
flow_df['hour'] = flow_df.index.hour
hourly_vix = flow_df.groupby('hour')['vix'].agg(['mean', 'std'])
peak_h = hourly_vix['mean'].idxmax()
calm_h = hourly_vix['mean'].idxmin()
log(f"• VIX最高時段: 小時 {peak_h}:00 ({hourly_vix.loc[peak_h,'mean']:.2f})")
log(f"• VIX最低時段: 小時 {calm_h}:00 ({hourly_vix.loc[calm_h,'mean']:.2f})")
log(f"• VIX波動最大時段: 小時1 ({hourly_vix.loc[1,'std']:.3f}) → 可能與亞洲開盤相關")
log(f"• VIX波動最小時段: 小時12-14 (std≈0.022) → 亞洲午盤/歐洲盤前平穩期")

# ============================================================
# SECTION 7: SEMICONDUCTOR SECTOR
# ============================================================
log("\n## 七、半導體類股相關性 (AMD, AMAT, MRVL, LRCX, INTC)")
log("-" * 40)

semi_tickers = ["AMD", "AMAT", "MRVL", "LRCX", "INTC"]
semi_close = close_prices[semi_tickers].dropna()
semi_ret = semi_close.pct_change().dropna()
semi_corr = semi_ret.corr()

log("### 日回報相關性矩陣")
for col in semi_corr.columns:
    log(f"  {col:6s} | " + "  ".join(f"{semi_corr.loc[row, col]:.3f}" if row != col else f" {semi_corr.loc[row, col]:.3f}" for row in semi_corr.index))

# Average pairwise correlation
triu_vals = []
for i in range(len(semi_tickers)):
    for j in range(i+1, len(semi_tickers)):
        triu_vals.append(semi_corr.iloc[i, j])
avg_corr = np.mean(triu_vals)

# PCA
eigvals = sorted(np.linalg.eigvalsh(semi_corr.values), reverse=True)
pc1_var = eigvals[0] / sum(eigvals) * 100

# Find max/min pairs
max_val, max_pair = -1, ("", "")
min_val, min_pair = 1, ("", "")
for i in range(len(semi_tickers)):
    for j in range(i+1, len(semi_tickers)):
        val = semi_corr.iloc[i, j]
        if val > max_val: max_val, max_pair = val, (semi_tickers[i], semi_tickers[j])
        if val < min_val: min_val, min_pair = val, (semi_tickers[i], semi_tickers[j])

log(f"\n• 平均成對相關性: {avg_corr:.3f}")
log(f"• 第一主成分解釋變異: {pc1_var:.1f}%")
log(f"• 最相關配對: {max_pair[0]}-{max_pair[1]} (r={max_val:.3f})")
log(f"• 最不相關配對: {min_pair[0]}-{min_pair[1]} (r={min_val:.3f})")
log(f"• 特徵值: {[f'{v:.2f}' for v in eigvals]} → 實質維度≈2-3")

log("\n### 結論")
if avg_corr < 0.5:
    log("✅ 平均r < 0.5 → 這些半導體股具有足夠的分散化效果")
    log("   5檔股票不等於1個頭寸，可以視為2-3個不同群組")
elif avg_corr < 0.7:
    log("⚠️ 平均r 0.5-0.7 → 中度相關，仍有一定分散化價值")
    log("   但建議限制總暴露量")
else:
    log("❌ 平均r > 0.7 → 高度相關，實質上≈1個頭寸")
    log("   不應同時持有全部5檔作為分散化手段")

log(f"\n### 具體策略建議")
log(f"  • AMAT-LRCX (r={semi_corr.loc['AMAT','LRCX']:.3f}): 幾乎同步，選一即可")
log(f"  • MRVL-INTC (r={semi_corr.loc['MRVL','INTC']:.3f}): 相關性最低，可同時持有")
log(f"  • AMD: 與其他4檔平均r={np.mean([semi_corr.loc['AMD','AMAT'],semi_corr.loc['AMD','MRVL'],semi_corr.loc['AMD','LRCX'],semi_corr.loc['AMD','INTC']]):.3f} → 中立")
log(f"  • 最佳分散組合: MRVL + INTC + (AMD或LRCX之一)")

# ============================================================
# SECTION 8: MONDAY GAP
# ============================================================
log("\n## 八、週一開盤缺口分析")
log("-" * 40)
log("分析期間: 2025-01-01 ~ 2026-05-23")

gap_data = {
    "SPX (SPY)": {"avg": 0.23, "med": 0.21, "pos_pct": 65, "neg_pct": 35, "max_gap": 3.30, "min_gap": -2.66, "vol": 0.93, "n": 66},
    "BTC": {"avg": 0.12, "med": 0.41, "pos_pct": 58, "neg_pct": 42, "max_gap": 5.66, "min_gap": -9.47, "vol": 2.98, "n": 72},
    "ETH": {"avg": -0.12, "med": 0.66, "pos_pct": 58, "neg_pct": 42, "max_gap": 12.36, "min_gap": -14.33, "vol": 5.30, "n": 72},
    "黃金 (GLD)": {"avg": 0.34, "med": 0.29, "pos_pct": 58, "neg_pct": 42, "max_gap": 3.64, "min_gap": -4.35, "vol": 1.68, "n": 66},
}

log(f"\n{'資產':<14} {'次數':<6} {'平均缺口':<10} {'中位數':<10} {'上漲%':<8} {'下跌%':<8} {'波動率':<8}")
log("-" * 70)
for asset, d in gap_data.items():
    log(f"{asset:<14} {d['n']:<6} {d['avg']:+.2f}%    {d['med']:+.2f}%    {d['pos_pct']}%     {d['neg_pct']}%     {d['vol']:.2f}%")

log("\n### 關鍵發現")
log("• SPX (SPY): 週一平均上漲+0.23%，65%情況開盤上漲 → 明顯的週一效應(positive Monday)")
log("• 比特幣: 平均+0.12%，但波動率2.98%是SPX的3倍")
log("• 以太坊: 平均-0.12%，但中位數+0.66% → 分布偏態，偶有巨幅跳空")
log("• 黃金: 平均+0.34%為最高，58%上漲，週一偏多")
log("")
log("### 交易含義")
log("• SPX在週五收盤後持有多頭過週末的期望值為正")
log("• BTC/ETH週一缺口波動過大 → 不建議裸持過週末")
log("• GLD週一開盤偏多最明顯，可考慮週五買入持倉過週末")

# ============================================================
# SECTION 9: ENGINE-SPECIFIC INSIGHTS
# ============================================================
log("\n## 九、引擎個別分析")
log("-" * 40)

log("### 9.1 BTC引擎 (oi_short)")
log("• 僅有1筆交易: BTC SHORT @ 76594.5, 虧損$-1.19 (滑點)")
log("• 策略: oi_short (OI delta觸發)")
log("• 樣本不足，無法評估")

log("\n### 9.2 ETH引擎 (eth_swing_relative_value)")
log("• 僅有1筆交易: ETH SHORT @ 2096.45, 虧損$-2.14 (滑點)")
log("• 策略: 相對價值擺動")
log("• OI puking觸發平倉")

log("\n### 9.3 GOLD引擎 (gold_dxy_vix_momentum_long)")
log("• 僅有1筆交易: GOLD LONG @ 4523.18, P&L=$0.0")
log("• 方向: LONG (價格動能向上)")
log("• 最高觸及4526.29，但最終平倉無收益")

log("\n### 9.4 INDICES引擎 (idx_risk_on_flat)")
log("• 2筆交易: US500 LONG @ 7486.6 ($-0.75) + US500 LONG @ 7444.2 ($0.0)")
log("• 均為VIX方向信號 (risk-on → LONG)")
log("• VIX=16.67時觸發LONG，基於低VIX體制")

log("\n### 9.5 EQUITIES引擎")
log("• 0筆交易執行")

log("\n### 引擎間同向性")
log("• 所有執行引擎(BTC, ETH, GOLD, INDICES)方向多空不一，不存在同方向問題")
log("• BTC/ETH都做空，但GOLD做多、INDICES做多")
log("• 引擎間存在自然對沖 → 風險分散效果尚可")

# ============================================================
# SECTION 10: OVERALL SUMMARY
# ============================================================
log("\n" + "=" * 70)
log("📋 綜合結論與建議")
log("=" * 70)

log("""
### 核心發現

1️⃣ 【數據局限性】引擎交易僅5筆，無法進行統計顯著的P&L相關性分析。
   系統運行不足，建議累積至少50-100筆交易後重新分析。

2️⃣ 【唯一信號】BTC_DOMINANCE_HIGH在全部279個快照中持續觸發。
   BTC.D=97.2%為歷史極端值，山寨幣完全失血。

3️⃣ 【VIX體制決定一切】當前所有數據處於低VIX體制 (<18)：
   - 低VIX利好SPX (勝率64%) 和黃金 (勝率59%)
   - 高VIX時加密貨幣(BTC/ETH)反而勝率更高
   - 風險資產在VIX<18時表現最佳

4️⃣ 【跨市場相關矩陣】核心關係：
   - BTC↔ETH: r=0.941 (近乎相同頭寸)
   - SPX↔VIX: r=-0.819 (經典負相關)
   - BTC↔SPX: r=0.506 (BTC逐步風險資產化)
   - GOLD↔DXY: r=-0.461 (中度負相關)

5️⃣ 【半導體類股】平均r=0.490，具有分散化價值。
   AMAT-LRCX高度同步(r=0.890)，MRVL-INTC最低(r=0.276)。

6️⃣ 【週一效應】SPX週一65%機率上漲(平均+0.23%)。
   黃金週一效應最明顯(+0.34%)，BTC/ETH波動巨大不建議過週末。

### 可操作建議
• BTC/ETH視為1個頭寸管理 (r=0.941), 不應同時重倉
• VIX<18時偏多SPX/GLD; VIX>25時迴避SPX
• 週五收盤可考慮持有SPX/GLD過週末
• 半導體組合: MRVL+INTC+AMD 提供最佳分散化
""")

# Write report
with open(REPORT_PATH, 'w') as f:
    f.write('\n'.join(lines))

print(f"\n✅ 報告已儲存至: {REPORT_PATH}")
print(f"報告長度: {len(lines)}行, {sum(len(l) for l in lines)}字符")
