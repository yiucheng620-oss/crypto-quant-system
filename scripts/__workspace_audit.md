# Workspace Audit — Complete File Categorization

## Executive Summary

**Workspace**: `~/workspace` — Trading system with ~70 Python files, 18+ data directories, 2 nested git repos.

**Two System Eras Detected:**
1. **V1 System (git-tracked, legacy)**: `signal_logger.py`-based 4h indicator loop. Cron shell scripts deleted. Files still in repo but no longer actively cronned.
2. **V2 System (untracked, active)**: `scalp_engines/cron_entry.py`-driven 5-min intraday swing/scalp. Currently live in crontab. Everything under `scalp_engines/`, `scalp_lab/`, most root .py files.

**Cron Jobs Active (May 2026):**
- `*/5 * * * *` → `scalp_engines/cron_entry.py` (core 5-engine sweep)
- `0 0 * * *` → `scalp_engines/daily_review.py`
- `0 0 * * 6` → `scalp_engines/weekly_review.py`
- `0 22 * * *` → `morning_briefing.sh` (external, not in workspace)
- `30 23 * * *` → `scalp_engines/daily_news.py`
- `10 22 * * *` → `scalp_engines/morning_tg_bridge.py`

**Git Status**: Branch `main`, up to date with `origin/main`.
- 12 cron `.sh` files **deleted** from tracking (removed from repo)
- 6 `.py` files **modified** (correlation_matrix, cross_market_sentinel, gemini_daily_analyst, macro_monitor, signal_logger, stock_pullback_scanner, tri_market_daily)
- ~40 files **untracked** (the V2 system + data collectors)

---

## FULL FILE MAPPING

### GROUP 1: V2 SCALP ENGINES (Active — Cron-run) → `scalp_engines/`

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `scalp_engines/cron_entry.py` | **Active** - Cron entry point (5-min) | Cron-run | `scalp_engines/cron_entry.py` |
| `scalp_engines/btc_scalp.py` | **Active** - BTC scalp engine | Cron-run | `scalp_engines/engines/btc_scalp.py` |
| `scalp_engines/btc_swing.py` | **Active** - BTC swing engine | Cron-run | `scalp_engines/engines/btc_swing.py` |
| `scalp_engines/eth_scalp.py` | **Active** - ETH scalp engine | Cron-run | `scalp_engines/engines/eth_scalp.py` |
| `scalp_engines/eth_swing.py` | **Active** - ETH swing engine | Cron-run | `scalp_engines/engines/eth_swing.py` |
| `scalp_engines/gold_scalp.py` | **Active** - Gold scalp engine | Cron-run | `scalp_engines/engines/gold_scalp.py` |
| `scalp_engines/indices_scalp.py` | **Active** - Indices scalp engine | Cron-run | `scalp_engines/engines/indices_scalp.py` |
| `scalp_engines/sol_scalp.py` | **Active** - SOL scalp engine | Cron-run | `scalp_engines/engines/sol_scalp.py` |
| `scalp_engines/equities_swing.py` | **Active** - Equities swing engine | Cron-run | `scalp_engines/engines/equities_swing.py` |
| `scalp_engines/engine_base.py` | **Active** - Base class for all engines | Cron-run | `scalp_engines/engine_base.py` |
| `scalp_engines/engine_coordinator.py` | **Active** - Coordinator/multiplexer | Cron-run | `scalp_engines/engine_coordinator.py` |
| `scalp_engines/flow_monitor.py` | **Active** - Flow monitoring engine | Cron-run | `scalp_engines/flow_monitor.py` |
| `scalp_engines/system_collector.py` | **Active** - System state collector | Cron-run | `scalp_engines/system_collector.py` |
| `scalp_engines/macro_pulse.py` | **Active** - Macro pulse checker | Cron-run | `scalp_engines/macro_pulse.py` |
| `scalp_engines/tg_notify.py` | **Active** - Telegram notification lib | Cron-run | `scalp_engines/tg_notify.py` |
| `scalp_engines/gemini_free.py` | **Active** - Gemini API wrapper | Cron-run | `scalp_engines/gemini_free.py` |
| `scalp_engines/daily_review.py` | **Active** - Daily post-review (cron 00:00) | Cron-run | `scalp_engines/reviews/daily_review.py` |
| `scalp_engines/weekly_review.py` | **Active** - Weekly review (cron Sat 00:00) | Cron-run | `scalp_engines/reviews/weekly_review.py` |
| `scalp_engines/daily_news.py` | **Active** - News stock picks (cron 23:30) | Cron-run | `scalp_engines/daily_news.py` |
| `scalp_engines/morning_tg_bridge.py` | **Active** - Morning TG push (cron 22:10) | Cron-run | `scalp_engines/morning_tg_bridge.py` |
| `scalp_engines/run_gemini_strategic.py` | **Active** - Strategic Gemini analysis | Used on demand | `scalp_engines/run_gemini_strategic.py` |

### GROUP 2: V1 LEGACY SIGNAL SYSTEM (Not cronned, git-tracked, old) → `legacy/v1_signal_system/`

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `signal_logger.py` | **Legacy** - Core v1 engine (4h loop, superseded by V2) | Modified, NOT in cron | `legacy/v1_signal_system/signal_logger.py` |
| `final_indicators.py` | **Legacy** - Old indicator library | Tracked, NOT in cron | `legacy/v1_signal_system/final_indicators.py` |
| `btc_vp_alert.py` | **Legacy** - VP_BTC_4h alert | Tracked, NOT in cron | `legacy/v1_signal_system/btc_vp_alert.py` |
| `cross_vp_alert.py` | **Legacy** - Cross-market VP alert | Tracked, NOT in cron | `legacy/v1_signal_system/cross_vp_alert.py` |
| `btc_short_alert.py` | **Legacy** - VP_BTC_1h Short alert | Tracked, NOT in cron | `legacy/v1_signal_system/btc_short_alert.py` |
| `rsi_eth_short_alert.py` | **Legacy** - RSI_ETH_1h Short alert | Tracked, NOT in cron | `legacy/v1_signal_system/rsi_eth_short_alert.py` |
| `wfa_analysis.py` | **Legacy** - WFA long-only framework | Tracked, Research completed | `legacy/v1_signal_system/wfa_analysis.py` |
| `wfa_short.py` | **Legacy** - WFA short-only pipeline | Tracked, Research completed | `legacy/v1_signal_system/wfa_short.py` |
| `weekly_analyzer.py` | **Legacy** - Gemini weekly analyzer | Tracked, NOT in cron | `legacy/v1_signal_system/weekly_analyzer.py` |
| `unified_alert_checker.py` | **Legacy** - Replaced 4 alert scripts (v1→v2 bridge) | Untracked, unused | `legacy/v1_signal_system/unified_alert_checker.py` |
| `data_pipeline.sh` | **Legacy** - Old data pipeline shell script | Tracked, deleted from cron | `legacy/v1_signal_system/data_pipeline.sh` |

### GROUP 3: DATA COLLECTORS (Active, used by V2 engines) → `collectors/`

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `capitalcom_paper.py` | **Active** - Capital.com demo API module (core dependency) | Untracked, used by all engines | `collectors/capitalcom_paper.py` |
| `btc_liq_tracker.py` | **Active** - Liquidation zone tracker (5-min OI scan) | Untracked, cron-run indirectly | `collectors/btc_liq_tracker.py` |
| `btc_micro_scanner.py` | **Active** - Micro-domain score scanner | Untracked, cron-run | `collectors/btc_micro_scanner.py` |
| `positioning_collector.py` | **Active** - OI/funding/LS ratio collector | Untracked, used by V2 | `collectors/positioning_collector.py` |
| `multi_exchange_ob.py` | **Active** - Multi-exchange OB collector | Untracked, morning_briefing dependency | `collectors/multi_exchange_ob.py` |
| `hl_funding.py` | **Active** - Hyperliquid funding rate | Untracked, used by V2 | `collectors/hl_funding.py` |
| `hl_orderbook.py` | **Active** - Hyperliquid orderbook | Untracked, used by V2 | `collectors/hl_orderbook.py` |
| `etf_flow.py` | **Active** - BTC/ETH ETF flow tracker | Untracked, used by morning_briefing | `collectors/etf_flow.py` |
| `onchain_data.py` | **Active** - On-chain metrics collector | Untracked, used by V2 | `collectors/onchain_data.py` |
| `deribit_options.py` | **Active** - Deribit options data | Untracked, used occasionally | `collectors/deribit_options.py` |
| `volume_profile.py` | **Active** - BTC Volume Profile calculator | Untracked, used by V2 | `collectors/volume_profile.py` |
| `liquidation_heatmap.py` | **Active** - Liquidation heatmap builder | Untracked | `collectors/liquidation_heatmap.py` |
| `market_data_collector.py` | **Active** - Yahoo Finance daily data collector | Tracked, modified | `collectors/market_data_collector.py` |
| `correlation_matrix.py` | **Active** - Cross-market correlation | Tracked, modified | `collectors/correlation_matrix.py` |
| `cross_market_sentinel.py` | **Active** - Cross-market decoupling monitor | Tracked, modified | `collectors/cross_market_sentinel.py` |

### GROUP 4: ANALYTICS & RESEARCH (Completed/Standalone) → `analytics/`

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `macro_monitor.py` | **Active** - Macro catalyst scanner (used by briefing) | Modified, active | `analytics/macro_monitor.py` |
| `sentiment_analyzer.py` | **Active** - VADER sentiment | Untracked | `analytics/sentiment_analyzer.py` |
| `gemini_daily_analyst.py` | **Active** - Gemini analyst (used by V2) | Modified | `analytics/gemini_daily_analyst.py` |
| `tri_market_daily.py` | **Active** - Tri-market daily report (rule-based) | Modified | `analytics/tri_market_daily.py` |
| `stock_pullback_scanner.py` | **Active** - Stock pullback scanner | Modified | `analytics/stock_pullback_scanner.py` |
| `stock_screener.py` | **Active** - US stock screener (→kanban) | Untracked | `analytics/stock_screener.py` |
| `equities_engine.py` | **Active** - Equities engine V2 | Untracked | `analytics/equities_engine.py` |
| `ai_bubble_monitor.py` | **Active** - AI bubble monitor (SOX/NVDA) | Tracked | `analytics/ai_bubble_monitor.py` |
| `ai_bubble_weekly.py` | **Active** - Weekly AI bubble deep report | Tracked | `analytics/ai_bubble_weekly.py` |
| `vbt_backtest.py` | **Research** - VectorBT backtest engine | Untracked, completed | `research/vbt_backtest.py` |
| `vbt_ranking.py` | **Research** - VectorBT ranking report | Untracked, completed | `research/vbt_ranking.py` |
| `funding_squeeze_backtest.py` | **Research** - Funding squeeze hypothesis test | Untracked, completed | `research/funding_squeeze_backtest.py` |
| `crypto_scalper.py` | **Research** - Early crypto scalp lab (superseded by V2) | Untracked, completed | `research/crypto_scalper.py` |
| `scalp_runner.py` | **Research** - Early scalp runner (superseded by V2 engines) | Untracked, completed | `research/scalp_runner.py` |

### GROUP 5: KANBAN PIPELINE (Active, trade management) → `analytics/kanban/`

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `kanban_orchestrator.py` | **Active** - Kanban morning orchestrator | Untracked | `analytics/kanban/kanban_orchestrator.py` |
| `kanban_closed_loop.py` | **Active** - Kanban trade executor/tracker | Untracked | `analytics/kanban/kanban_closed_loop.py` |
| `kanban_stocks.py` | **Active** - Kanban stock deep analysis | Untracked | `analytics/kanban/kanban_stocks.py` |
| `kanban_trigger.py` | **Active** - Kanban pipeline trigger | Untracked | `analytics/kanban/kanban_trigger.py` |

### GROUP 6: REGIME/ML (Research, completed) → `research/regime/`

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `regime_backtest.py` | **Research** - Regime-adaptive backtest engine | Untracked, completed | `research/regime/regime_backtest.py` |
| `regime_selector.py` | **Research** - Regime-adaptive indicator selector | Untracked, completed | `research/regime/regime_selector.py` |
| `ml_regime.py` | **Research** - ML RandomForest regime classifier | Untracked, completed | `research/regime/ml_regime.py` |

### GROUP 7: PAPER TRADING (Active) → `trading/`

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `paper_trader.py` | **Active** - Paper trading tracker | Untracked | `trading/paper_trader.py` |
| `audit_capital.py` | **Active** - Capital.com audit v1 | Untracked | `trading/audit_capital.py` |
| `audit_capital_v2.py` | **Active** - Capital.com audit v2 (better) | Untracked | `trading/audit_capital_v2.py` |
| `morning_briefing.py` | **Active** - Commander's Briefing (cron 22:00) | Untracked, cron-run | `trading/morning_briefing.py` |

### GROUP 8: HEALTH MONITORING (Active) → `monitoring/`

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `indicator_health_monitor.py` | **Active** - 3-light health check engine | Untracked | `monitoring/indicator_health_monitor.py` |
| `health_report.py` | **Active** - Health report generator | Untracked | `monitoring/health_report.py` |
| `health_bridge.py` | **Active** - Health bridge for Gemini | Untracked | `monitoring/health_bridge.py` |
| `weekly_accuracy_report.py` | **Active** - Weekly accuracy report | Untracked | `monitoring/weekly_accuracy_report.py` |
| `test_health_system.sh` | **Active** - Health system integration test | Untracked | `monitoring/test_health_system.sh` |

### GROUP 9: SCALP LAB (Research/Sandbox) → `research/scalp_lab/`

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `scalp_lab/compile_report.py` | **Research** - Report compilation | Untracked | `research/scalp_lab/compile_report.py` |
| `scalp_lab/cross_market_analysis.py` | **Research** - Cross-market analysis v1 | Untracked | `research/scalp_lab/cross_market_analysis.py` |
| `scalp_lab/cross_market_analysis_v2.py` | **Research** - Cross-market analysis v2 | Untracked | `research/scalp_lab/cross_market_analysis_v2.py` |
| `scalp_lab/ga_optimizer.py` | **Research** - GA optimizer | Untracked | `research/scalp_lab/ga_optimizer.py` |
| `scalp_lab/monday_gap_analysis.py` | **Research** - Monday gap analysis | Untracked | `research/scalp_lab/monday_gap_analysis.py` |
| `scalp_lab/engines/exit_timing_backtest.py` | **Research** - Exit timing backtest | Untracked | `research/scalp_lab/engines/exit_timing_backtest.py` |
| `scalp_lab/engines/exit_timing_backtest_v2.py` | **Research** - Exit timing backtest v2 | Untracked | `research/scalp_lab/engines/exit_timing_backtest_v2.py` |
| `scalp_lab/engines/oi_delta_backtest.py` | **Research** - OI delta backtest | Untracked | `research/scalp_lab/engines/oi_delta_backtest.py` |

### GROUP 10: BTC PERP RESEARCH (Completed External Project) → `research/btc-perp-microstructure/`

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `btc-perp-microstructure/ob_poc_v4.py` | **Research** - Order book PoC | External git repo | Keep as-is |
| `btc-perp-microstructure/vortexbar_lab.py` | **Research** - Vortex bar lab | External git repo | Keep as-is |
| `btc-perp-microstructure/BTC_Perp_Research_Brief_v2.pdf` | **Data** - Research PDF | Static asset | Keep as-is |
| `btc-perp-microstructure/assets/*.png` | **Data** - Research charts | Static assets | Keep as-is |

### GROUP 11: CONFIG & META

| File | Category | Status | Proposed Location |
|------|----------|--------|-------------------|
| `README.md` | **Meta** - Project documentation | Tracked | `README.md` |
| `.gitignore` | **Meta** - Git ignore rules | Tracked | `.gitignore` |
| `cleanup.log` | **Log** - Cleanup log output | Data | `logs/cleanup.log` |

### GROUP 12: DATA DIRECTORIES (All active data outputs)

| Directory | Files | Content Type | Proposed Location |
|-----------|-------|--------------|-------------------|
| `accuracy_reports/` | ~13 files | Signal accuracy CSVs + summaries | `data/accuracy_reports/` |
| `health_reports/` | ~40+ files | Health check JSON snapshots | `data/health_reports/` |
| `reviews/` | ~30+ files | Daily/weekly reviews + news picks | `data/reviews/` |
| `equities_data/` | 2 files | Equities setups + news picks | `data/equities/` |
| `etf_data/` | 1 file | ETF flow latest | `data/etf/` |
| `funding_data/` | 2 files | Funding rate history + latest | `data/funding/` |
| `macro_data/` | 1 file | Macro alerts | `data/macro/` |
| `ml_data/` | 1 file | ML regime output | `data/ml/` |
| `onchain_data/` | 1 file | On-chain metrics | `data/onchain/` |
| `options_data/` | 1 file | Options data | `data/options/` |
| `orderbook_data/` | 2 files | Order book snapshots | `data/orderbook/` |
| `paper_trades/` | 1 file | Paper trading positions | `data/paper_trades/` |
| `sentiment_data/` | 1 file | Sentiment latest | `data/sentiment/` |
| `stock_scanner/` | 1 file | Stock scanner prev results | `data/stock_scanner/` |
| `vbt_results/` | 1 file | VectorBT rankings | `data/vbt_results/` |
| `market_data/positioning/` | 1 file | Positioning history CSV | `data/positioning/` |
| `regime_backtest/` | 3 files | Regime backtest results | `data/regime/` |
| `cron/` | 2 files | Cron log files | `logs/cron/` |
| `logs/` | 1 file | Briefing stderr log | `logs/` |
| `scalp_lab/engines/` | ~100+ JSON files | Runtime state, trades, scans, signals | Keep in `scalp_engines/runtime/` |

### GROUP 13: DATABASES

| File | Size | Category | Proposed Location |
|------|------|----------|-------------------|
| `signal_accuracy.db` | 90KB | **Active** - Signal accuracy SQLite | `data/signal_accuracy.db` |
| `indicator_health.db` | 242KB | **Active** - Indicator health SQLite | `data/indicator_health.db` |

---

## PROPOSED DIRECTORY STRUCTURE

```
~/workspace/
├── README.md
├── .gitignore
│
├── scalp_engines/                    ← V2 ACTIVE ENGINES (keep flat for imports)
│   ├── cron_entry.py                 # Cron entry point
│   ├── engine_base.py                # Base class
│   ├── engine_coordinator.py         # Coordinator
│   ├── flow_monitor.py               # Flow monitor
│   ├── system_collector.py           # System state
│   ├── macro_pulse.py                # Macro pulse
│   ├── tg_notify.py                  # TG notify lib
│   ├── gemini_free.py                # Gemini API
│   ├── morning_tg_bridge.py          # Morning TG push
│   ├── daily_news.py                 # Daily news picks
│   ├── run_gemini_strategic.py       # Strategic analysis
│   ├── engines/                      # Per-asset engines
│   │   ├── btc_scalp.py
│   │   ├── btc_swing.py
│   │   ├── eth_scalp.py
│   │   ├── eth_swing.py
│   │   ├── sol_scalp.py
│   │   ├── gold_scalp.py
│   │   ├── indices_scalp.py
│   │   └── equities_swing.py
│   ├── reviews/                      # Review scripts
│   │   ├── daily_review.py
│   │   └── weekly_review.py
│   └── runtime/                      # Runtime state (was scalp_lab/engines/*.json)
│       ├── btc_state.json
│       ├── eth_state.json
│       ├── btc_trades.json
│       ├── ... (all runtime JSON)
│       └── scan_full_scan_*.json     # 5-min scan snapshots
│
├── collectors/                       ← DATA COLLECTORS
│   ├── capitalcom_paper.py           # Capital.com demo API
│   ├── btc_liq_tracker.py
│   ├── btc_micro_scanner.py
│   ├── positioning_collector.py
│   ├── multi_exchange_ob.py
│   ├── hl_funding.py
│   ├── hl_orderbook.py
│   ├── etf_flow.py
│   ├── onchain_data.py
│   ├── deribit_options.py
│   ├── volume_profile.py
│   ├── liquidation_heatmap.py
│   ├── market_data_collector.py
│   ├── correlation_matrix.py
│   └── cross_market_sentinel.py
│
├── analytics/                        ← ANALYSIS SCRIPTS
│   ├── macro_monitor.py
│   ├── sentiment_analyzer.py
│   ├── gemini_daily_analyst.py
│   ├── tri_market_daily.py
│   ├── stock_pullback_scanner.py
│   ├── stock_screener.py
│   ├── equities_engine.py
│   ├── ai_bubble_monitor.py
│   ├── ai_bubble_weekly.py
│   └── kanban/                       ← Kanban pipeline
│       ├── kanban_orchestrator.py
│       ├── kanban_closed_loop.py
│       ├── kanban_stocks.py
│       └── kanban_trigger.py
│
├── trading/                          ← TRADING EXECUTION
│   ├── morning_briefing.py           # Commander's Briefing
│   ├── paper_trader.py
│   ├── audit_capital.py
│   └── audit_capital_v2.py
│
├── monitoring/                       ← HEALTH MONITORING
│   ├── indicator_health_monitor.py
│   ├── health_report.py
│   ├── health_bridge.py
│   ├── weekly_accuracy_report.py
│   └── test_health_system.sh
│
├── research/                         ← COMPLETED RESEARCH
│   ├── vbt_backtest.py
│   ├── vbt_ranking.py
│   ├── funding_squeeze_backtest.py
│   ├── crypto_scalper.py
│   ├── scalp_runner.py
│   ├── regime/                       ← Regime classification research
│   │   ├── regime_backtest.py
│   │   ├── regime_selector.py
│   │   └── ml_regime.py
│   ├── scalp_lab/                    ← Sandbox experiments
│   │   ├── compile_report.py
│   │   ├── cross_market_analysis.py
│   │   ├── cross_market_analysis_v2.py
│   │   ├── ga_optimizer.py
│   │   ├── monday_gap_analysis.py
│   │   └── engines/
│   │       ├── exit_timing_backtest.py
│   │       ├── exit_timing_backtest_v2.py
│   │       └── oi_delta_backtest.py
│   └── btc-perp-microstructure/      ← External research (keep as-is)
│
├── legacy/                           ← V1 OBSOLETE SYSTEM
│   └── v1_signal_system/
│       ├── signal_logger.py
│       ├── final_indicators.py
│       ├── btc_vp_alert.py
│       ├── cross_vp_alert.py
│       ├── btc_short_alert.py
│       ├── rsi_eth_short_alert.py
│       ├── wfa_analysis.py
│       ├── wfa_short.py
│       ├── weekly_analyzer.py
│       ├── unified_alert_checker.py
│       └── data_pipeline.sh
│
├── data/                             ← DATA OUTPUTS
│   ├── accuracy_reports/             # (was ~/accuracy_reports/)
│   ├── health_reports/               # (was ~/health_reports/)
│   ├── reviews/                      # (was ~/reviews/)
│   ├── equities/
│   ├── etf/
│   ├── funding/
│   ├── macro/
│   ├── ml/
│   ├── onchain/
│   ├── options/
│   ├── orderbook/
│   ├── paper_trades/
│   ├── sentiment/
│   ├── stock_scanner/
│   ├── vbt_results/
│   ├── positioning/
│   ├── regime/
│   ├── signal_accuracy.db
│   └── indicator_health.db
│
└── logs/                             ← LOG FILES
    ├── cron/
    │   ├── morning_briefing.log
    │   └── morning_tg.log
    ├── cleanup.log
    └── briefing_stderr.log
```

---

## SUMMARY COUNTS

| Group | Active | Legacy | Research | Data | Total |
|-------|--------|--------|----------|------|-------|
| V2 Engines | 21 | 0 | 0 | 0 | 21 |
| V1 Legacy | 0 | 10 | 0 | 0 | 10 |
| Data Collectors | 15 | 0 | 0 | 0 | 15 |
| Analytics | 9 | 0 | 0 | 0 | 9 |
| Kanban | 4 | 0 | 0 | 0 | 4 |
| Research | 0 | 0 | 8 | 0 | 8 |
| Regime/ML | 0 | 0 | 3 | 0 | 3 |
| Trading | 4 | 0 | 0 | 0 | 4 |
| Monitoring | 5 | 0 | 0 | 0 | 5 |
| Config/Meta | 0 | 0 | 0 | 3 | 3 |
| Data Dirs | 0 | 0 | 0 | 20 | 20 |
| **Total** | **58** | **10** | **11** | **23** | **102** |

**Note**: `scalp_lab/engines/*.json` (100+ runtime state files) excluded from count — these are volatile runtime data that get recreated every 5 minutes.

---

## KEY FINDINGS

1. **V1 is officially dead**: The original `signal_logger.py` 4h-based system (git-tracked) is no longer in crontab. All 12 cron shell scripts were deleted from git. The V2 `cron_entry.py` replaced everything.

2. **V2 is actively running**: 6 cron jobs active, 5 engines (BTC, ETH, gold, indices, equities) scanning every 5 minutes. This generates ~300 JSON state snapshots per day in `scalp_lab/engines/`.

3. **Data directories are fragmented**: 18+ data directories scattered across workspace root. Proposed consolidation under `data/`.

4. **10 files are true legacy** (V1 signal system) - safe to archive or delete after confirmation.

5. **BTW**: `daily_brief.py` and `cross_asset_matrix.py` referenced in README no longer exist on disk. They were probably deleted during V2 migration.
