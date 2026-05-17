# Crypto Quant System

Multi-indicator crypto trading system with **Walk-Forward Analysis** validation.

## 7 WFA-Validated Indicators

### LONG
| Indicator | Coin | TF | Best Market | 24h WR |
|:--|:--|:--|:--|:--|
| VP_BTC_4h_L | BTC | 4h | Range | 66.7% |
| VP_ETH_4h_L | ETH | 4h | Range | 75.0% |
| OBV_BTC_1h_L | BTC | 1h | Range | 64.3% |
| OBV_SOL_1h_L | SOL | 1h | Range | 62.5% |

### SHORT
| Indicator | Coin | TF | Best Market | 24h WR |
|:--|:--|:--|:--|:--|
| RSI_ETH_1h_S | ETH | 1h | Bull | 83.6% |
| VP_BTC_1h_S | BTC | 1h | Range | 69.2% |
| VP_SOL_1h_S | SOL | 1h | Range | 87.5% |

## System Components

```
signal_logger.py    — Core engine: fetch OHLCV → run indicators → log signals → back-check accuracy
final_indicators.py — Indicator library (VP, OBV, BB, RSI — Long + Short variants)
btc_vp_alert.py     — VP_BTC_4h Long alert with action instructions
cross_vp_alert.py   — Dual-coin VP_4h confirmation alert
btc_short_alert.py  — VP_BTC_1h Short alert
rsi_eth_short_alert.py — RSI_ETH_1h Short alert (strongest Short indicator)
daily_brief.py      — 09:00 HKT daily report (market state + signals + Fear & Greed)
macro_monitor.py    — Macro catalyst scanner (IPO, Fed, SEC, CPI)
wfa_analysis.py     — Walk-Forward Analysis framework (5 rolling windows)
wfa_short.py        — Short-only WFA pipeline
weekly_analyzer.py  — Weekly accuracy report generator
```

## Cron Schedule

```
signal-tracker-4h    → Every 4h (fetch + run indicators + accuracy check)
btc-vp4h-alert       → +5min (VP_BTC_4h Long alert)
btc-vp1h-short-alert → +7min (VP_BTC_1h Short alert)
rsi-eth-short-alert  → +8min (RSI_ETH_1h Short alert)
cross-vp4h-alert     → +10min (Cross-market VP confirmation)
daily-brief-9am      → 09:00 HKT (Daily report)
macro-monitor        → 07:00 + 19:00 HKT (Macro event alerts)
weekly-report        → Mon 08:00 HKT (AI bubble weekly)
```

## Setup

1. Clone + install deps: `pip install numpy pandas requests`
2. Set up Vertex AI proxy (optional, for Gemini analysis)
3. Configure cron jobs via Hermes Agent
4. Data: Binance OHLCV → NPZ files in `~/market_data/phase1/`

## License

MIT — use at your own risk. Not financial advice.
