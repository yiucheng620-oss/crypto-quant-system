#!/usr/bin/env python3
"""
Regime-Adaptive Backtest Engine — L1: Regime Split
═══════════════════════════════════════════════════════
For each indicator, split historical performance by market regime
(bull/bear/range/volatile) to reveal genuine regime-dependent edge.

驗證鏈: Real Trade → Regime Split → WFA per regime → Cross-market → MC

Output:
  1. regime_performance.json — per-indicator per-regime WR, sample, confidence
  2. Human-readable summary (stdout)
  3. regime_selection.json — auto-selected indicators for CURRENT regime

Integration:
  → regime_selector.py (L2)
  → signal_logger.py (pre-filter)
  → gemini_daily_analyst.py (context injection)

Usage:
  python3 regime_backtest.py           # Full backtest + selection
  python3 regime_backtest.py --json    # JSON output only
  python3 regime_backtest.py --report  # Human-readable report
  python3 regime_backtest.py --diff    # JSON output only WHEN selection changes
"""

import os, sys, json, sqlite3, math
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
OUTPUT_DIR = os.path.expanduser("~/workspace/regime_backtest")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ═══════════════════════════════════════════
# Confidence / significance helpers
# ═══════════════════════════════════════════

def wilson_confidence(wins, total, z=1.96):
    """Wilson score confidence interval lower bound.
    Gives conservative WR estimate for small samples.
    """
    if total == 0:
        return 0.0
    p = wins / total
    denominator = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denominator
    return max(0.0, centre - margin)

def significance_stars(wins, total, min_n=3):
    """Rate statistical reliability.
    ⭐⭐⭐ = n>=10 AND wilson_lower > 55%
    ⭐⭐  = n>=5 AND wilson_lower > 50%
    ⭐   = n>=3 (exists but noisy)
    ∅   = insufficient data
    """
    if total < min_n:
        return "∅"
    lower = wilson_confidence(wins, total)
    if total >= 10 and lower > 0.55:
        return "⭐⭐⭐"
    elif total >= 5 and lower > 0.50:
        return "⭐⭐"
    elif total >= 3:
        return "⭐"
    return "∅"

# ═══════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════

def load_data():
    """Load all signals, accuracy, and market_state data."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Signals with their accuracy outcomes
    c.execute("""
        SELECT s.id, s.indicator, s.coin, s.timeframe, s.direction,
               s.price, s.created_at,
               a.result, a.entry_price, a.exit_price, a.pnl_pct,
               a.market_condition as acc_regime, a.horizon
        FROM signals s
        LEFT JOIN accuracy a ON s.id = a.signal_id
        ORDER BY s.created_at
    """)
    signals = [dict(r) for r in c.fetchall()]

    # Market state (regime snapshots)
    c.execute("""
        SELECT coin, timeframe, condition, adx, volatility_pct, timestamp
        FROM market_state
        ORDER BY timestamp
    """)
    market_states = [dict(r) for r in c.fetchall()]

    conn.close()
    return signals, market_states

def find_regime_at_time(signal, market_states):
    """Find the market regime at the time a signal was created.
    
    Strategy: find the latest market_state with same coin+timeframe
    and timestamp <= signal.created_at (convert ms to seconds).
    """
    coin = signal['coin']
    tf = signal['timeframe']
    sig_ts = signal['created_at']
    
    # created_at is in milliseconds for some rows, seconds for others
    if sig_ts > 1e10:
        sig_ts = sig_ts / 1000  # ms → seconds
    
    best = None
    best_ts = 0
    
    for ms in market_states:
        if ms['coin'] == coin and ms['timeframe'] == tf:
            if ms['timestamp'] <= sig_ts and ms['timestamp'] > best_ts:
                best = ms
                best_ts = ms['timestamp']
    
    return best['condition'] if best else None

# ═══════════════════════════════════════════
# Backtest computation
# ═══════════════════════════════════════════

def compute_regime_performance(signals, market_states):
    """Compute per-indicator per-regime performance metrics."""
    
    # Structure: indicator → regime → {wins, total, pnl_list, ...}
    perf = defaultdict(lambda: defaultdict(lambda: {
        'wins': 0, 'total': 0,
        'pnl_list': [], 'win_pnl_list': [], 'loss_pnl_list': [],
        'coins': set(), 'directions': set(),
    }))
    
    # Also track: regime → indicator → performance (for selection)
    regime_perf = defaultdict(lambda: defaultdict(lambda: {
        'wins': 0, 'total': 0,
        'wilson_lower': 0, 'stars': '∅',
        'avg_win': 0, 'avg_loss': 0,
    }))
    
    settled_count = 0
    unsettled_count = 0
    no_regime_count = 0
    
    for sig in signals:
        # Find regime at signal time
        regime = sig.get('acc_regime')  # Prefer accuracy table's regime label
        if not regime:
            regime = find_regime_at_time(sig, market_states)
        
        if not regime:
            no_regime_count += 1
            continue
        
        ind = sig['indicator']
        direction = sig.get('direction', 'UNKNOWN')
        result = sig.get('result')
        pnl = sig.get('pnl_pct')
        
        # Track all signals (even unsettled) for regime distribution
        entry = perf[ind][regime]
        entry['total'] += 1
        entry['coins'].add(sig['coin'])
        entry['directions'].add(direction)
        
        # Settled signals
        if result is not None:
            settled_count += 1
            if result == 'WIN':
                entry['wins'] += 1
                if pnl is not None:
                    entry['win_pnl_list'].append(pnl)
            else:
                if pnl is not None:
                    entry['loss_pnl_list'].append(pnl)
            if pnl is not None:
                entry['pnl_list'].append(pnl)
        else:
            unsettled_count += 1
    
    # Compute derived metrics
    for ind, regimes in perf.items():
        for regime, data in regimes.items():
            wins = data['wins']
            total_settled = len(data['pnl_list'])
            
            if total_settled == 0:
                continue
            
            wr = (wins / total_settled) * 100
            wl = wilson_confidence(wins, total_settled)
            stars = significance_stars(wins, total_settled)
            
            avg_win = sum(data['win_pnl_list']) / len(data['win_pnl_list']) * 100 if data['win_pnl_list'] else 0
            avg_loss = sum(data['loss_pnl_list']) / len(data['loss_pnl_list']) * 100 if data['loss_pnl_list'] else 0
            
            regime_perf[regime][ind] = {
                'wins': wins,
                'total': total_settled,
                'total_signals': data['total'],  # including unsettled
                'win_rate': round(wr, 1),
                'wilson_lower': round(wl * 100, 1),
                'stars': stars,
                'avg_win_pct': round(avg_win, 2),
                'avg_loss_pct': round(avg_loss, 2),
                'expectancy': round((wr/100 * avg_win) + ((100-wr)/100 * avg_loss), 2) if avg_win or avg_loss else 0,
                'coins': list(data['coins']),
                'directions': list(data['directions']),
            }
    
    metadata = {
        'generated_at': datetime.now(HKT).isoformat(),
        'total_signals': len(signals),
        'settled_signals': settled_count,
        'unsettled_signals': unsettled_count,
        'no_regime_match': no_regime_count,
        'regimes_found': sorted(set(r['condition'] for r in market_states)),
        'indicators_analyzed': len(set(
            ind for regime_data in regime_perf.values() 
            for ind in regime_data
        )),
        'data_days': '3 days (market_state: May 16-19, 2026)',
    }
    
    return regime_perf, metadata

# ═══════════════════════════════════════════
# Current regime selection
# ═══════════════════════════════════════════

def get_current_regime():
    """Get current market regime for each coin/timeframe."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute("""
        SELECT coin, timeframe, condition, adx
        FROM market_state
        ORDER BY timestamp DESC
    """)
    rows = c.fetchall()
    conn.close()
    
    # Get latest regime per coin/timeframe
    current = {}
    for r in rows:
        key = f"{r['coin']}_{r['timeframe']}"
        if key not in current:
            current[key] = {
                'condition': r['condition'],
                'adx': r['adx'],
                'coin': r['coin'],
                'timeframe': r['timeframe'],
            }
    return current

def select_indicators_for_regime(regime_perf, current_regimes, min_wilson=40.0, top_n=None):
    """Auto-select best indicators for each current regime.
    
    Filters:
    - min_wilson: minimum wilson lower bound % (default 40%)
    - Only settled >= 3 signals
    - Sorted by wilson_lower descending
    
    Returns dict: coin_tf → {regime, selected_indicators, excluded_indicators}
    """
    selection = {}
    
    for key, regime_info in current_regimes.items():
        condition = regime_info['condition']
        coin = regime_info['coin']
        tf = regime_info['timeframe']
        
        available = regime_perf.get(condition, {})
        
        selected = []
        excluded = []
        
        for ind, perf in available.items():
            # Only select indicators matching this coin
            coins = perf.get('coins', [])
            if coin not in coins:
                continue
            
            if perf['total'] < 3:
                excluded.append({
                    'indicator': ind,
                    'reason': f"insufficient data (n={perf['total']})",
                    'wr': perf['win_rate'],
                    'stars': perf['stars'],
                })
            elif perf['wilson_lower'] < min_wilson:
                excluded.append({
                    'indicator': ind,
                    'reason': f"wilson_lower={perf['wilson_lower']}% < {min_wilson}%",
                    'wr': perf['win_rate'],
                    'wilson': perf['wilson_lower'],
                    'stars': perf['stars'],
                })
            else:
                selected.append({
                    'indicator': ind,
                    'win_rate': perf['win_rate'],
                    'wilson_lower': perf['wilson_lower'],
                    'stars': perf['stars'],
                    'total': perf['total'],
                    'expectancy': perf.get('expectancy', 0),
                    'avg_win_pct': perf['avg_win_pct'],
                    'avg_loss_pct': perf['avg_loss_pct'],
                    'directions': perf.get('directions', []),
                })
        
        # Sort by wilson_lower descending
        selected.sort(key=lambda x: x['wilson_lower'], reverse=True)
        excluded.sort(key=lambda x: x.get('wilson', 0), reverse=True)
        
        if top_n:
            selected = selected[:top_n]
        
        selection[key] = {
            'coin': coin,
            'timeframe': tf,
            'regime': condition,
            'adx': regime_info['adx'],
            'selected': selected,
            'excluded': excluded,
            'total_available': len(available),
        }
    
    return selection

# ═══════════════════════════════════════════
# Reporting
# ═══════════════════════════════════════════

def print_report(regime_perf, metadata, selection=None):
    """Human-readable regime backtest report."""
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")
    
    print(f"📊 Regime-Adaptive Backtest — {now}")
    print(f"═══════════════════════════════════════")
    print(f"  Signals: {metadata['total_signals']} total | {metadata['settled_signals']} settled | {metadata['unsettled_signals']} unsettled")
    print(f"  Data: {metadata['data_days']}")
    print(f"  Regimes found: {', '.join(metadata['regimes_found'])}")
    print()
    
    # Per-regime breakdown
    all_regimes = set(regime_perf.keys())
    
    for regime in sorted(all_regimes):
        indicators = regime_perf.get(regime, {})
        if not indicators:
            continue
        
        print(f"## 🏷️ {regime.upper()} REGIME")
        print()
        
        # Sort by wilson_lower descending
        sorted_inds = sorted(
            indicators.items(),
            key=lambda x: x[1].get('wilson_lower', 0),
            reverse=True
        )
        
        for ind, perf in sorted_inds:
            stars = perf.get('stars', '∅')
            wr = perf['win_rate']
            wl = perf.get('wilson_lower', 0)
            n = perf['total']
            exp = perf.get('expectancy', 0)
            dirs = ', '.join(perf.get('directions', []))
            
            # Status emoji
            if stars == '⭐⭐⭐':
                status = '🟢'
            elif stars == '⭐⭐':
                status = '🟡'
            elif stars == '⭐':
                status = '🟠'
            else:
                status = '🔴'
            
            print(f"  {status} {ind:25s} | WR={wr:.0f}% (Wilson≥{wl}%) | n={n} | E={exp:+.1f}% | {stars} | {dirs}")
        
        print()
    
    # Current regime selection
    if selection:
        print("## 🎯 CURRENT REGIME SELECTION")
        print()
        
        for key, data in selection.items():
            regime = data['regime']
            coin = data['coin']
            tf = data['timeframe']
            
            print(f"### {coin} {tf} → {regime} (ADX={data['adx']})")
            
            if data['selected']:
                print(f"  ✅ SELECTED ({len(data['selected'])} indicators):")
                for s in data['selected']:
                    print(f"     • {s['indicator']} — WR={s['win_rate']}% Wilson≥{s['wilson_lower']}% {s['stars']} | n={s['total']} | E={s['expectancy']:+.1f}%")
            else:
                print(f"  ⚠️ NO INDICATORS QUALIFY — all excluded or insufficient data")
            
            if data['excluded']:
                print(f"  ❌ EXCLUDED ({len(data['excluded'])}):")
                for e in data['excluded']:
                    print(f"     • {e['indicator']} — {e['reason']}")
            
            print()
    
    # Warning
    print("⚠️ DATA WARNING:")
    print(f"  Only {metadata['data_days']} of regime data available.")
    print(f"  n < 10 = statistically unreliable. Treat as directional, not actionable.")
    print(f"  System will auto-improve as more signals settle over time.")
    print()
    print(f"📁 Full data: {OUTPUT_DIR}/regime_performance.json")
    print(f"📁 Selection: {OUTPUT_DIR}/regime_selection.json")

# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════

def main():
    signals, market_states = load_data()
    regime_perf, metadata = compute_regime_performance(signals, market_states)
    
    # Save full performance data
    perf_output = {
        'metadata': metadata,
        'regime_performance': {
            regime: dict(indicators)
            for regime, indicators in regime_perf.items()
        }
    }
    
    with open(os.path.join(OUTPUT_DIR, 'regime_performance.json'), 'w') as f:
        json.dump(perf_output, f, indent=2, default=str)
    
    # Current regime selection
    current = get_current_regime()
    selection = select_indicators_for_regime(regime_perf, current)
    
    selection_output = {
        'generated_at': datetime.now(HKT).isoformat(),
        'current_regimes': current,
        'selection': selection,
    }
    
    with open(os.path.join(OUTPUT_DIR, 'regime_selection.json'), 'w') as f:
        json.dump(selection_output, f, indent=2, default=str)
    
    # Report
    if '--json' in sys.argv or '--diff' in sys.argv:
        output_json = json.dumps(selection_output, indent=2)
    else:
        print_report(regime_perf, metadata, selection)
        output_json = None
    
    # ── Diff mode: only output when selection changes ──
    if '--diff' in sys.argv:
        last_state_path = os.path.join(OUTPUT_DIR, 'last_state.json')
        
        # Extract current selection's indicator names per regime key
        def extract_selected_names(sel):
            return {
                key: [s['indicator'] for s in data['selected']]
                for key, data in sel.items()
            }
        
        current_selection = extract_selected_names(selection)
        
        changed = True
        if os.path.exists(last_state_path):
            try:
                with open(last_state_path) as f:
                    last_state = json.load(f)
                last_selection = last_state.get('selection_names', {})
                if last_selection == current_selection:
                    changed = False
            except (json.JSONDecodeError, KeyError):
                pass
        
        # Save current state for next comparison
        with open(last_state_path, 'w') as f:
            json.dump({
                'generated_at': datetime.now(HKT).isoformat(),
                'selection_names': current_selection,
            }, f, indent=2)
        
        if changed and output_json:
            print(output_json)
        # If unchanged: output nothing (already exited or will exit silently)
    elif output_json:
        print(output_json)

if __name__ == '__main__':
    main()
