# 🔍 交易系統每日/每週報告流程深度審計報告
**審計日期：2026-05-29 | 審計者：Hermes Agent**

---

## 評分標準
- **🟢 REAL** — 產生可執行數據，改變系統行為
- **🟡 PARTIAL** — 部分有用但有效率/品質問題
- **🔴 DECORATION** — 填充文字、空殼、無實際影響
- **⛔ BROKEN** — 定義了但從未執行或持續報錯

---

## 1️⃣ weekly_review_v2.py — 每週邊緣績效檢討

| 項目 | 評估 |
|------|------|
| 排程 | 週六 08:00 HKT (系統 crontab) |
| 最後執行 | 2026-05-23 (log 5KB) |
| 分類 | **🟡 PARTIAL** |

**真實性分析：**
- ✅ Phase 1-3：實際載入 7 日 trades，做 edge 分類、regime 分析、交叉矩陣 — **REAL**
- ✅ Phase 4：Gemini hypothesis generation 流程完整
- ❌ **Hypotheses 輸出幾乎為空**：`hypothesis_results.json` 僅 178 bytes，`weekly_hypotheses.json` 不存在
- ❌ Phase 5：`update_strategy_config()` 功能定義但從未在 log 中看到實際觸發權重更改
- ❌ 文件頭說「週日 20:00 HKT」但 crontab 是「週六 08:00 HKT」 — 文檔衝突

**判斷：** 框架紮實但 hypothses 輸出空洞。Gemini hypothesis generation 可能失敗或結果沒被保存/使用。**每週 hypothesis 環節是空殼。**

---

## 2️⃣ daily_review_v2.py — 每日自動優化引擎 v2

| 項目 | 評估 |
|------|------|
| 排程 | 週六 08:30 HKT (系統 crontab) — 原本每日，現已改為每週 |
| 最後執行 | 2026-05-29 08:00 (16KB JSON + 3.9KB TXT) |
| 分類 | **🟢 REAL** |

**真實性分析：**
- ✅ Phase 1：統計分析紮實 — 載入 7 日數據，計算 WR/SL rate/TP rate/hold time/trailing rate
- ✅ Phase 2：**實際修改 engine source files** — 有 bak 文件證明 (btc_swing.py.bak_20260529_0800)
- ✅ Phase 3：策略權重調整 — 寫入 `strategy_config.json`，有完整的 `weight_history.json`
- ✅ Phase 4：Gemini 結構化輸出 — 輸出 JSON 中包含 actionable actions
- ✅ Phase 5：Integrity check — 輸出 `integrity_check.json` (44 lines)
- ✅ `param_changelog.json` 767 行 / 19KB — 完整的審計跟蹤

**頻率問題：** 現在只跑週六 (原本每日)，但排程名稱仍是「每日」。做為週報，它和 `weekly_review_v2.py` **僅差 30 分鐘** — 重度重疊。

**判斷：** 這是系統中最有價值的自動化流程。**唯一真正修改 engine 參數的流程。**

---

## 3️⃣ daily_position_review.py — 每日持倉主動管理

| 項目 | 評估 |
|------|------|
| 排程 | 週一至五 09:30 / 21:15 / 02:00 HKT (3x 每日) |
| 最後執行 | 2026-05-29 01:30 (position_review_0930.log 3.8KB) |
| 分類 | **🟢 REAL** |

**真實性分析：**
- ✅ 實際讀取 Capital.com API 獲取真實倉位
- ✅ 實際下單執行 SL/TP 調整 (`execute_adjustments()` 調用 `capitalcom_paper`)
- ✅ `position_review.json` 30KB — 包含真實位的 Gemini 決策 (BTC/INTC/XOM/QQQ/XAUUSD)
- ✅ 有中文原因的 Gemini 輸出 (如「比特幣多頭已有浮盈，上調止損至入場價」)

**判斷：** 高價值流程。3x 每日頻率合理（開市後/收市前/美股中段）。**無重疊。**

---

## 4️⃣ daily_news.py — 新聞驅動選股引擎

| 項目 | 評估 |
|------|------|
| 排程 | 每日 07:30 HKT |
| 最後執行 | 2026-05-28 23:30 (UTC) = 2026-05-29 07:30 HKT |
| 分類 | **🟢 REAL** |

**真實性分析：**
- ✅ 6 個 RSS 來源實際抓取 (CNBC/MarketWatch/Reuters/Bloomberg/SeekingAlpha/TechCrunch)
- ✅ Gemini Google Search Grounding 實際調用
- ✅ 每日輸出 JSON + TXT 文件，持續 May 23-29 (每個約 5-6KB)
- ✅ TG 推送啟動 (`tg_send`)
- ✅ Screener cross-check 有實際交叉對照

**判斷：** 穩定的真實流程。每日產出有 Catalyst 的選股清單。**頻率合理，無明顯重疊。**

---

## 5️⃣ daily_evolution.py — 達爾文影子進化工廠

| 項目 | 評估 |
|------|------|
| 排程 | 每日 08:15 HKT |
| 最後執行 | 2026-05-29 00:15 (UTC) = 08:15 HKT |
| 分類 | **🔴 DECORATION** |

**真實性分析：**
- ❌ **永遠 FAIL**：兩種策略 (BTC, INDICES) 都被 Judge 評 45/100 → Rejected
- ❌ 只有 1 個候選策略文件 (`shadowengine_btc_1779973520.py`)
- ❌ `shadow_v1_test_trades.json` 只有測試交易（70000 的 fake signal）
- ❌ `trace_shadow.py` 只是沙箱測試，非生產流程
- ❌ 每日跑但零產出 — DeepSeek V4 + Gemini 2.5 Pro + Gemini 2.5 Flash 三模型調用全浪費
- ✅ 框架設計概念有趣（Researcher → Risk Manager → Judge）
- ✅ `evolution.log` 顯示流程確實在跑

**判斷：** **非常昂貴的裝飾** — 每天消耗 DeepSeek + Gemini API 額度，產出零個可用的策略。7 天內零成功。建議**暫停排程**直到 Judge 標準放寬或改為手動觸發。

---

## 6️⃣ equities_engine.py — Alpha Hunter V2 (含 audit_historical_picks)

| 項目 | 評估 |
|------|------|
| 排程 | 每日 08:30 HKT (經 equities_swing.py 調用) |
| 最後執行 | 2026-05-29 (last equities_setups.json 更新) |
| 分類 | **🟢 REAL** (但 data quality ↓) |

**真實性分析：**
- ✅ `run()` 完整執行：benchmarks → sourcing → verification → council → sentinel → audit
- ✅ `audit_historical_picks()` 是真實函數 — 讀取 5 日前 picks、yfinance 獲取價格、計算 alpha vs SPY
- ✅ 輸出 `equities_setups.json` 被 equities_swing.py 實際使用
- ✅ TG 推送啟動
- ❌ **equities_picks.json 品質問題**：36,845 行 / 1MB，重複記錄同一隻 ADI 數百次
- ❌ `spy_at_pick: 0` — key benchmark 數據缺失，alpha 計算不可靠
- ❌ Defence fallback 經常用（council 失敗時），fallback 用靜態 sector 描述

**判斷：** **框架真實，但數據品質拖累。** audit_historical_picks 是系統中唯一追蹤選股 alpha 的機制，但因為 equities_picks.json 的資料問題，實際 alpha 計算不可靠。

---

## 7️⃣ equities_swing.py — Edge Tracking (equities_picks.json)

| 項目 | 評估 |
|------|------|
| 排程 | 每日 cache 刷新 (6hr TTL) |
| 分類 | **🔴 DATA QUALITY FAILURE** |

**真實性分析：**
- ✅ 繼承 `SwingEngine`，有完整的交易邏輯
- ✅ `picks_file = equities_picks.json` 確實在寫入
- ❌ **36,845 行文件**，99% 是重複數據 (相同 ADI pick 每 5 分鐘記錄一次)
- ❌ `spy_at_pick: 0` + `rs_score: 0` — 極重要的基準數據完全缺失
- ❌ `reason: ""` + `sector: ""` — 選股原因為空
- ❌ 同一 ticker (ADI) 從 May 23 到 29 被重複記錄數百次

**判斷：** **重複記錄嚴重**。記錄機制有 bug — 每次 scan 都 append 相同 pick 而不是跳過/更新。建議：
1. 只在有新 ticker 時才記錄
2. 補上 `spy_at_pick` 和 `rs_score`
3. 或者改用 upsert 模式

---

## 8️⃣ kanban_closed_loop.py — track/execute/review

| 項目 | 評估 |
|------|------|
| 排程 | US market hours 每 30 min (系統 crontab) |
| 最後執行 | 2026-05-28 (kanban_tracker.log 6.4KB) |
| 分類 | **🟡 PARTIAL** |

**真實性分析：**
- ✅ `track` 模式：檢查開倉位、記錄到 kanban_state.json (5.5KB)
- ✅ `execute` 模式：從 trade plans 實際下單到 Capital.com
- ✅ `kanban_trade_history.json` (3.3KB) 有交易歷史
- ❌ Hermes cron 的「Kanban V3 美股預判與執行」**持續報 error (timeout)**
- ❌ 「Kanban V3 每週業績檢討」**從未執行** (completed: 0)
- ⚠️ track 每 30 分鐘的頻率可能過高

**判斷：** **track/execute 是真實的**，但 Hermes cron 中的 kanban 任務有執行問題。review 模式是裝飾（從未執行）。

---

## 9️⃣ Hermes Cron 任務

| 任務名稱 | 排程 | 狀態 | 評估 |
|---------|------|------|------|
| 日誌清理 | 每日 00:00 | 17次完成 ✅ | 🟢 REAL |
| Kanban V3 美股預判與執行 | 週一至五 20:30 | **8次完成，最後 error** | ⛔ BROKEN |
| Kanban V3 美股收盤日報 | 週二至六 08:00 | 4次完成 ✅ | 🟢 REAL |
| Kanban V3 每週業績檢討 | 週六 10:00 | **0次完成 — 從未執行** | 🔴 DECORATION |
| lock-watchdog | 每 3 分鐘 | 1061次完成 ✅ | 🟢 REAL |
| detect-gaps | 每 30 分鐘 | 97次完成 ✅ | 🟢 REAL |
| Smart Money 歷史快照 | 每日 2次 | 2次完成 ✅ | 🟢 REAL |
| 纏論 1買 掃描 | 每小時 | 32次完成 ✅ | 🟢 REAL |

---

## 🔟 其他流程

| 流程 | 評估 |
|------|------|
| **system_guardian.py** (每小時) | 🟢 REAL — 60+ guardian JSON 報告，頻繁輸出 |
| **detect_gaps.py** (每 30 分鐘) | 🟢 REAL — 97次完成，實際掃描 pipeline gaps |
| **regime_detector.py** (週一 09:00) | 🟡 PARTIAL — 定義了但未深入審計其產出品質 |

---

## ⚡ 重疊與重複問題

### 1️⃣ daily_review_v2.py ↔ weekly_review_v2.py — **嚴重重疊**
- 兩者都在週六早上執行，僅差 30 分鐘
- 兩者都分析 7 日 trade 數據
- 兩者都使用 Gemini
- 兩者都調整 strategy_config.json 權重
- **建議合併**：如果 daily_review 已經做權重調整和參數優化，weekly_review 應專注於 hypotheses 生成而不再重複調整權重

### 2️⃣ equities_picks.json 36,845 行 — **數據污染**
- 每 5 分鐘重複記錄相同 pick (ADI)
- `spy_at_pick: 0` 破壞了 audit_historical_picks 的 alpha 計算
- **建議清理**並加入 dedup 機制

### 3️⃣ daily_evolution.py vs 實際交易 — **零連接**
- 每日產生策略但全部 rejected
- 沒有任何成功策略進入沙箱測試
- 和真實交易系統完全隔離

---

## 📋 綜合建議

### 立即停止（節省 API 成本）
1. **⛔ daily_evolution.py** — 暫停排程。7 天零成功，每天浪費 DeepSeek + 2x Gemini API。改為每週一次手動執行，或放寬 Judge 標準。

### 合併/重構
2. **🔗 daily_review_v2.py + weekly_review_v2.py** — 兩者 30 分鐘內執行太荒謬。保留 daily_review 做參數優化 + 權重調整，讓 weekly_review 專注 hypotheses + edge 矩陣分析。或直接在 daily_review 中加入 hypotheses 功能後刪除 weekly_review。

### 修復數據品質
3. **🛠️ equities_picks.json** — 加入 dedup 邏輯 (upsert 模式)。記錄 `spy_at_pick`。限制最多 100 條歷史記錄。
4. **🛠️ audit_historical_picks** — 因為 spy_at_pick=0 目前 alpha 計算不可靠。修復數據源後才能信賴。

### 修復執行問題
5. **🔧 Kanban V3 美股預判與執行** — timeout 120s 不夠。增加到 300s 或優化 pipeline 速度。
6. **🚀 Kanban V3 每週業績檢討** — 從未執行。確認 `kanban_review.sh` 是否存在且可用。

### 頻率調整
7. **kanban_closed_loop.py track** — 每 30 分鐘可能太頻繁（US market hours = ~6.5h = 13 次/天）
8. **detect_gaps** — 每 30 分鐘掃描 pipeline gap 合理。保留。

---

## 📊 總結表

| 流程 | 分類 | 最後新鮮度 | 產出大小 | 改變系統？ | 建議 |
|------|------|-----------|---------|-----------|------|
| daily_review_v2.py | 🟢 REAL | 今天 | 16KB JSON | ✅ 改 engine 參數 | 保留 |
| daily_position_review.py | 🟢 REAL | 今天 | 30KB JSON | ✅ 改真實 SL/TP | 保留 |
| daily_news.py | 🟢 REAL | 今天 | 6KB JSON+TXT | ✅ TG 推送 | 保留 |
| equities_engine.py | 🟢 REAL | 今天 | equities_setups | ✅ 供其他引擎使用 | 修復 data quality |
| system_guardian.py | 🟢 REAL | 每小時 | guardian JSON | ✅ 警報 | 保留 |
| detect_gaps.py | 🟢 REAL | 每 30 分 | 日誌 | ⚠️ 警報 | 保留 |
| kanban_closed_loop.py track | 🟡 PARTIAL | 昨天 | 6KB log | ✅ 下單 | 修復 timeout |
| weekly_review_v2.py | 🟡 PARTIAL | 5/23 | 5KB log | ❌ 假設沒寫入 | 合併入 daily_review |
| regime_detector.py | 🟡 PARTIAL | 未審計 | 未審計 | ❓ | 需額外審計 |
| daily_evolution.py | 🔴 DECORATION | 今天 | 687B log | ❌ 永遠 FAIL | **立即暫停** |
| equities_picks.json | 🔴 DATA FAIL | 今天 | 1MB/36K行 | ❌ 重複數據 | **清理 dedup** |
| Kanban 每週業績檢討 | 🔴 DECORATION | 從未執行 | N/A | ❌ 空殼 | 確認 script 存在 |
| Kanban 美股預判執行 | ⛔ BROKEN | 今天 error | timeout | ❌ 失敗 | 增加 timeout |
