# 🔄 Crypto 自我進化交易系統 V2

## ⏰ Cron 排程

| 時間 (HKT) | UTC | 系統 | 功能 |
|---|---|---|---|
| 每5分鐘 | */5 | `cron_entry.py` | 5引擎全掃描 → signal → execute |
| 每小時 | 0 * | `system_guardian.py` | 健康檢查 + dead param scan + auto-fix |
| 06:00 | 22:00 | `morning_briefing.sh` | 每日晨報 |
| 06:10 | 22:10 | `morning_tg_bridge.py` | TG 推送 |
| 07:30 | 23:30 | `daily_news.py` | 新聞選股 + Gemini 分析 |
| **08:00** | **00:00** | **`daily_review_v2.py`** | **每日自動優化 + Gemini 策略執行 + auto-fix** |
| **週一 09:00** | **週一 01:00** | **`regime_detector.py`** | **制度識別 + 週度策略鎖定** |
| **週六 08:00** | **週六 00:00** | **`weekly_review_v2.py`** | **Edge P&L 拆解 + Gemini 假設生成** |
| **週日 08:00** | **週日 00:00** | **`hypothesis_backtester.py`** | **WFA 回測 + Paper Pool 晉升** |

## 🔄 兩層閉環

### 每週循環
```
Mon: Regime Detection → 鎖定本週策略
Tue-Fri: 執行交易 (跟 regime_config.json)
Sat: Weekly Review → Edge P&L 分析 → Gemini 生成新假設
Sun: Hypothesis Backtest → WFA 驗證 → Paper Pool → Auto-promote
```

### 每日循環
```
每5min: Engine scan → signal → execute
08:00:  分析昨日 trades → auto-optimize params → Gemini strategic actions → auto-fix bugs
每小時: System Guardian → health check + dead param scan
```

## 🧠 自我進化機制

| 機制 | 做咩 | 自動化程度 |
|---|---|---|
| **Param Optimization** | 根據 whipsaw/missed TP/time stop 自動調 SL/TP/Trailing | ✅ 全自動 |
| **Strategy Weight** | Edge P&L rolling → ±20% weight adjust daily | ✅ 全自動 |
| **Gemini Actions** | Structured JSON → auto-parse → auto-execute | ✅ 全自動 |
| **Bug Detection** | Dead param scan + import verify + trade sync | ✅ 全自動 |
| **Regime Detection** | 週一分類制度 → 鎖定整週 edge weights | ✅ 全自動 |
| **Hypothesis Testing** | 週六生成 → 週日 WFA → Paper Pool → Live | ✅ 全自動 |
| **TG Notification** | 只喺 actionable / broken 時通知 | ✅ 靜音優先 |

## 📂 系統檔案

```
scalp_engines/
├── cron_entry.py              # 每5分鐘全掃描入口
├── engine_coordinator.py      # 引擎協調器
├── engine_base.py             # SwingEngine 基類
├── btc_swing.py               # BTC 波段 (OI Delta + ATR SL)
├── eth_swing.py               # ETH 波段 (ETH/BTC ratio)
├── gold_scalp.py              # Gold (VIX/DXY regime)
├── indices_scalp.py           # Indices (SPX/NDX VIX direction)
├── equities_swing.py          # Equities (Kanban picks)
├── flow_monitor.py            # 資金流監控
├── macro_pulse.py             # 宏觀脈衝
├── daily_review_v2.py         # 🆕 每日自動優化 V2
├── weekly_review_v2.py        # 🆕 每週深度檢討 V2
├── regime_detector.py         # 🆕 週一制度識別
├── hypothesis_backtester.py   # 🆕 週日假設回測
├── system_guardian.py         # 🆕 每小時系統監護
├── daily_news.py              # 新聞選股
├── morning_tg_bridge.py       # TG 推送
├── gemini_free.py             # Gemini API
└── tg_notify.py               # TG 通知
```
