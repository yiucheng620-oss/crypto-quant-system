# 大漠哥量化框架研究
## 外部量化交易者方法論拆解 & 可行性評估

> **來源：** X/Twitter @damobianyuan（大漠哥）
> **背景：** 缠論愛好者 → 幣安百強創作者 → 獨立研究員 → 量化策略實盤測試中
> **社群：** TG「大漠茶館」+ Discord
> **研究日期：** 2026-05-28
> **最後更新：** 2026-05-28  
> **狀態：** ✅ Phase 1-2 完成 | 🔬 Phase 3 背景進行中
> **獨立系統：** `~/workspace/chanlun_system/`（與 trading engine 完全分離）

---

## 一、背景概述

大漠哥係一個由手動缠論交易轉型量化嘅獨立交易者。佢嘅進化路徑同我地系統高度相似：

```
手動缠論分析 → 系統化信號 → 量化策略實盤測試
手動技術指標 → 5-Indicator系統 → 全自動化交易引擎
```

**關鍵差異：** 佢用缠論做結構分析（中樞/背馳/買賣點），我地用傳統技術指標。兩者可以互補。

---

## 二、核心框架拆解

### 2.1 多時間框架架構

```
大級別（定方向）：  4h / 8h / 12h  ─→ Trend Bias
中級別（定結構）：  1h / 30min      ─→ Pattern Formation  
小級別（定入場）：  15min / 5min    ─→ Entry Trigger
```

**現有系統對比：**
- 我地每個 indicator 獨立用一個 TF，冇跨 TF 確認
- VP: 1h/4h | BB: 1h/4h | OBV: 1h | MACD: killed | RSI: killed
- 缺少：「大級別 bias → 中級別結構 → 小級別入場」嘅層級確認

### 2.2 缠論核心概念

| 概念 | 解釋 | 量化難度 |
|------|------|----------|
| **中樞 (Pivot/Consolidation)** | 價格重疊區間，至少 3 段重疊 | 🟡 中等 |
| **背馳 (Divergence)** | 價格新低但 MACD 不跟隨 | 🟢 容易 |
| **1買 (First Buy)** | 底背馳後第一個反彈 | 🟢 容易 |
| **2買 (Second Buy)** | 回調不破前低 | 🟡 中等 |
| **3買 (Third Buy)** | 突破中樞後回踩確認 | 🟡 中等 |
| **級別轉換** | 小級別中樞 = 大級別一筆 | 🔴 困難 |

### 2.3 輔助工具

| 工具 | 用途 | 量化難度 |
|------|------|----------|
| **MACD 背離分級** | 分 1次/2次/連續背離 | 🟢 容易 |
| **Vegas 通道** | 動態支撐/壓力 | 🟢 容易 |
| **成交量確認** | 背離 + 量縮 = 更可靠 | 🟢 容易 |

---

## 三、信號系統分類

### Type A: MACD 背離信號（最常用）

他大量使用 MACD 背離，而且有分級：

| 級別 | 條件 | 可靠性（據他所述） |
|------|------|-------------------|
| 1次背離 | 價格新低 + MACD 不跟隨 | 中等 |
| 2次背離（背了又背）| 連續兩次背離 | 高 |
| 4h 級別背離 | 4h TF 上出現 | 「相對來說都很準」 |
| 12h 級別量柱背離 | MACD 量柱不收斂 | 高（rare signal） |

**實例：**
- 2025-06-20: BTC/ETH 12h 級別量柱背離 + 背了又背
- 2025-09-02: SUI 4h MACD 連續底背離 → 現貨埋伏
- 2025-09-19: BTC MACD 兩次背離 → 117900 壓制有效

### Type B: 中樞結構信號

| 型態 | 條件 | 方向 |
|------|------|------|
| 中樞上沿 + 支撐 | 回調踩中樞上沿 + 多次插針收回 | LONG |
| 中樞下沿 + 突破 | 突破中樞 + 回踩確認 | LONG |
| 中樞右下方 + 3賣 | 大級別中樞右下方出現 3賣 | SHORT |
| 中樞右上方 + 3買 | 小級別中樞右上方 3買 | LONG |

**實例：**
- 2025-07-29: ETH 回調踩中樞上沿 + MACD 底背離 → LONG @ $3910/$3950
- 2025-10-20: BTC 大級別中樞右下方 3賣 → 繼續向下

### Type C: 「錯殺機會」Pattern

暴跌後的特殊 pattern：

```
條件：
1. 暴跌後價格回到中樞區間
2. 中樞結構未被破壞
3. 15min 級別出現 1買 → 2買 → 3買序列
4. 情緒極度悲觀（「情緒幾乎砸到地板」）

入場：3買構建區間
止損：中樞下沿
```

**實例：**
- 2025-06-23: SOL 暴跌後，15min 級別 1買完成 + 中樞形成 → 等待 3買

### Type D: Vegas 通道信號

| 信號 | 條件 |
|------|------|
| 快軌壓制 | 價格被 Vegas 快軌壓住 → 壓力有效 |
| 幔軌壓制 | 價格被 Vegas 幔軌壓住 → 更強壓力 |
| 快軌突破 | 突破快軌 + 回踩確認 → LONG |

**實例：**
- 2025-11-08: UNI 4h Vegas 快軌壓制 + 1h 幔軌壓制 → 空

---

## 四、與現有系統的整合可行性

### 4.1 即時可整合（🟢 容易）

| 大漠哥方法 | 現有系統對應 | 整合方式 |
|-----------|-------------|---------|
| MACD 背離分級 | MACD（已被 kill） | 復活並加「連續背離次數」filter |
| 多 TF 確認 | 各 indicator 獨立 TF | 加 TF alignment layer |
| Vegas 通道 | 冇 | 新增 EMA 144/169 通道 |

### 4.2 需要研究（🟡 中等）

| 大漠哥方法 | 可行性 | 需研究 |
|-----------|--------|--------|
| 中樞 Detection | 可量化 | ZigZag + 重疊檢測 algorithm |
| 買賣點分類 | 可量化 | 基於中樞 + 價格行為嘅 rule-based 分類 |
| 錯殺 Pattern | 可量化 | 跌幅閾值 + 結構 intact check + TF recovery |

### 4.3 挑戰性高（🔴 困難）

| 大漠哥方法 | 困難點 |
|-----------|--------|
| 級別轉換 | 需要 recursive pattern recognition |
| 缠論完整實現 | 太多主觀判斷，難以完全自動化 |
| 背馳級別判斷 | 需要 determine「邊個級別嘅背馳」 |

---

## 五、逐項研究計劃

按優先級排序：

### Phase 1: 低掛果實（本週）✅ 已完成

- [x] **P1.1 MACD 背離復活** — 加背離次數分級 → [詳細結果](#p11-結果)
- [x] **P1.2 多 TF 確認層** — Cross-TF alignment → [詳細結果](#p12-結果)
- [x] **P1.3 Vegas 通道** — EMA 144/169 dynamic S/R → [詳細結果](#p13-結果)

---

## P1 研究結果

### P1.1 MACD 背離復活結果

**數據規模：** BTC/ETH/SOL × 1h/4h/1d = 199 個背離信號
**核心發現：**

| 發現 | 結論 |
|------|------|
| MACD 背離自身極強 | 1d horizon 100% WR（所有品種/方向），9 個 horizon 組合全部 >60% WR |
| 「背了又背」假設 | ❌ 不成立 — 2次+背離只在 9/30 cases 優於 1次背離 |
| BEARISH 例外 | ETH 4h BEARISH 2次+: WR 由 50%→90%（+40%），33%→80%（+47%） |
| BULLISH 反而差 | 2次+ BULLISH 通常比 1次更差（2/12 cases 改善） |

**建議：復活 MACD，但唔需要背離分級。直接用 MACD 背離作為 entry filter，配合其他 indicator 確認。**

### P1.2 多 TF 確認層結果

**核心發現：**

| 發現 | 結論 |
|------|------|
| 對齊信號反而差 | 18 個測試中，misaligned 在 12 個 case 優於 aligned |
| 唯一例外 | BTC 4h→1h @48 bars: aligned ΔWR +5.6% |
| 可能原因 | Counter-trend entries（misaligned）捕捉 mean reversion，crypto 中更有效 |
| 大漠哥方法 | 喺 crypto 可能唔適用 — 佢主要 trade 傳統市場？ |

**建議：❌ 唔加 multi-TF alignment layer。反而可以考慮「逆大級別 bias 嘅小級別 signal」作為 contrarian entry。**

### P1.3 Vegas 通道結果

**核心發現：**

| Setup | Best WR | Best Symbol/TF | 穩定性 |
|-------|---------|---------------|--------|
| **BOUNCE_LONG** | **64%** | ETH 4h @6 bars | ⭐ 最穩定（7/8 cases >54% WR）|
| REJECT_SHORT | 63% | BTC 4h @6 bars | ⭐ 穩定（4/6 cases >52% WR）|
| BREAKOUT_LONG | 58% | ETH 1h @48 bars | ➡️ 不穩定 |
| BREAKDOWN_SHORT | 56% | ETH 4h @12 bars | ➡️ 不穩定 |

**建議：✅ 整合 BOUNCE_LONG 同 REJECT_SHORT。Vegas 通道作為 mean reversion S/R 有效，但作為 breakout signal 無效。**

---

### Phase 2: 中樞結構（下週）

- [ ] **P2.1 中樞 Detection Algorithm**
  - ZigZag 轉折點 → 重疊區間識別 → 中樞標記
  - 目標：自動 detect 中樞位置同級別
  - 檔案：`research/pivot_detection.py`

- [ ] **P2.2 買賣點分類**
  - 基於中樞 + MACD 背離 → 1買/2買/3買 classification
  - 目標：量化每種買點嘅 historical win rate
  - 檔案：`research/buy_point_classifier.py`

- [ ] **P2.3 錯殺 Pattern Backtest**
  - 暴跌 → 結構 intact → recovery signal → entry
  - 目標：找出 optimal 跌幅閾值同 recovery 條件
  - 檔案：`research/misKill_pattern.py`

### Phase 3: 策略合成（兩週後）

- [ ] **P3.1 多信號融合框架**
  - MACD + 中樞 + Vegas + Smart Money → 綜合 score
  - 目標：一套 rule-based fusion system
  - 檔案：`research/damo_fusion_engine.py`

- [ ] **P3.2 Walk-Forward Validation**
  - 整套框架嘅 WFA 驗證
  - 目標：確認係 in-sample overfit 定真正 edge
  - 檔案：`research/damo_wfa.py`

---

## 六、關鍵問題（待研究回答）

1. MACD「背了又背」係咪真係比 1次背離更可靠？量化數字係幾多？
2. 多 TF 確認嘅 edge 有幾大？（大級別 bias + 小級別 signal vs 單 TF）
3. 中樞 detection 用 ZigZag 夠唔夠準？定要用更複雜嘅 algorithm？
4. 缠論「買點」同我地現有 indicator signal 有咩 correlation？
5. 錯殺 pattern 嘅 optimal 參數係咩？（跌幅%、恢復時間、結構條件）
6. 整個框架嘅 Sharpe/MaxDD 係幾多？同現有 5-indicator 系統對比如何？

---

## 七、大漠哥文章待讀清單

需要 X 登入先睇到全文，之後補讀：

| 日期 | Article ID | 估計主題 |
|------|-----------|---------|
| 2026-05-25 | 2057853939811549184 | 量化策略實盤測試（用戶分享呢篇） |
| 2026-05-10 | 2053326697047760896 | 未知（最近一篇 Article） |

---

## 八、參考資料

- X Profile: https://x.com/damobianyuan
- TG 社群: https://t.me/damoteahouse / https://t.me/zuiluclub
- Discord: https://discord.gg/adp89Hv84H
- Linktree: https://linktr.ee/damobianyuan
- 研究日期: 2026-05-28
