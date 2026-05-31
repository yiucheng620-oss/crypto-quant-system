#!/usr/bin/env python3
"""
Regime-Adaptive Indicator Selector — L2: Pipeline Integration
═══════════════════════════════════════════════════════════════
Selects the best indicators for current market regime using 
regime_backtest data. Plugs into signal_logger.py and 
gemini_daily_analyst.py for auto-filtering.

Modes:
  strict:  Only return indicators passing Wilson significance (default)
  pilot:   Return all with confidence labels, flag low-confidence
  hybrid:  Strict for HIGH alerts, pilot for context

Usage:
  from regime_selector import get_regime_indicators, RegimeSelector
  
  sel = RegimeSelector(mode='pilot')
  indicators = sel.select()  
  # → [{'indicator': 'OBV_SOL_1h_L', 'regime': 'range', 'wr': 75, ...}, ...]
  
  filter_signal(signal, indicators)  # → True if signal should fire
"""

import os, json, sqlite3
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HKT = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/workspace/signal_accuracy.db")
BACKTEST_DIR = os.path.expanduser("~/workspace/regime_backtest")
PERF_FILE = os.path.join(BACKTEST_DIR, "regime_performance.json")

# ═══════════════════════════════════════════
# Regime Selector class
# ═══════════════════════════════════════════

class RegimeSelector:
    """Auto-selects indicators for current market regime."""
    
    def __init__(self, mode='pilot', min_wilson_strict=40, min_wilson_pilot=25):
        self.mode = mode
        self.min_wilson_strict = min_wilson_strict
        self.min_wilson_pilot = min_wilson_pilot
        self.regime_data = None
        self.current_regime = None
        self.refresh()
    
    def refresh(self):
        """Reload data from files and DB."""
        self._load_performance()
        self._load_current_regime()
    
    def _load_performance(self):
        """Load regime performance JSON."""
        if os.path.exists(PERF_FILE):
            with open(PERF_FILE) as f:
                data = json.load(f)
                self.regime_data = data.get('regime_performance', {})
        else:
            self.regime_data = {}
    
    def _load_current_regime(self):
        """Load current market regime for each coin/timeframe."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT coin, timeframe, condition, adx
            FROM market_state ORDER BY timestamp DESC
        """)
        rows = c.fetchall()
        conn.close()
        
        self.current_regime = {}
        for r in rows:
            key = f"{r['coin']}_{r['timeframe']}"
            if key not in self.current_regime:
                self.current_regime[key] = {
                    'condition': r['condition'],
                    'adx': r['adx'],
                    'coin': r['coin'],
                    'timeframe': r['timeframe'],
                }
    
    def select(self, coin=None, timeframe=None, direction=None):
        """Select indicators for current regime.
        
        Args:
            coin: Filter to specific coin (BTC/ETH/SOL) or None for all
            timeframe: Filter to specific tf (1h/4h) or None for all
            direction: Filter to LONG/SHORT or None for all
        
        Returns:
            List of selected indicators with metadata
        """
        results = []
        
        for key, regime_info in self.current_regime.items():
            c = regime_info['coin']
            tf = regime_info['timeframe']
            condition = regime_info['condition']
            
            # Apply filters
            if coin and c != coin:
                continue
            if timeframe and tf != timeframe:
                continue
            
            # Get indicators for this regime
            regime_indicators = self.regime_data.get(condition, {})
            
            for ind_name, perf in regime_indicators.items():
                # Coin match check
                coins = perf.get('coins', [])
                if c not in coins:
                    continue
                
                # Direction match
                dirs = perf.get('directions', [])
                if direction and direction not in dirs:
                    continue
                
                entry = self._evaluate(ind_name, perf, condition)
                if entry:
                    entry['coin'] = c
                    entry['timeframe'] = tf
                    entry['current_regime'] = condition
                    entry['adx'] = regime_info['adx']
                    results.append(entry)
        
        # Sort: strict mode by wilson, pilot mode by wr
        if self.mode == 'strict':
            results.sort(key=lambda x: x.get('wilson_lower', 0), reverse=True)
        else:
            results.sort(key=lambda x: x['win_rate'], reverse=True)
        
        return results
    
    def _evaluate(self, ind_name, perf, regime):
        """Evaluate indicator for selection based on mode."""
        wins = perf['wins']
        total = perf['total']
        wr = perf['win_rate']
        wilson = perf.get('wilson_lower', 0)
        stars = perf.get('stars', '∅')
        
        entry = {
            'indicator': ind_name,
            'regime': regime,
            'win_rate': wr,
            'wilson_lower': wilson,
            'stars': stars,
            'total': total,
            'expectancy': perf.get('expectancy', 0),
            'avg_win_pct': perf.get('avg_win_pct', 0),
            'avg_loss_pct': perf.get('avg_loss_pct', 0),
            'directions': perf.get('directions', []),
        }
        
        if self.mode == 'strict':
            threshold = self.min_wilson_strict
        else:
            threshold = self.min_wilson_pilot
        
        if total < 3:
            if self.mode == 'strict':
                return None  # Skip entirely
            else:
                entry['confidence'] = 'LOW'
                entry['warning'] = f'n={total} (insufficient)'
                entry['selected'] = False
        elif wilson < threshold:
            if self.mode == 'strict':
                return None
            else:
                entry['confidence'] = 'LOW'
                entry['warning'] = f'Wilson={wilson}% < {threshold}%'
                entry['selected'] = False
        else:
            if stars == '⭐⭐⭐':
                entry['confidence'] = 'HIGH'
            elif stars == '⭐⭐':
                entry['confidence'] = 'MEDIUM'
            else:
                entry['confidence'] = 'PILOT'
            entry['selected'] = True
        
        return entry
    
    def filter_signals(self, signals, min_confidence='PILOT'):
        """Filter signal list to only include regime-appropriate indicators.
        
        Args:
            signals: List of signal dicts with indicator/coin/regime fields
            min_confidence: Minimum confidence to allow ('PILOT', 'MEDIUM', 'HIGH')
        
        Returns:
            (passed, suppressed) tuple of signal lists
        """
        confidence_rank = {'LOW': 0, 'PILOT': 1, 'MEDIUM': 2, 'HIGH': 3}
        min_rank = confidence_rank.get(min_confidence, 1)
        
        # Build lookup: coin_regime → {indicator_name → entry}
        selected = self.get_selection_map()
        
        passed = []
        suppressed = []
        
        for sig in signals:
            coin = sig.get('coin', '')
            indicator = sig.get('indicator', '')
            # Find the regime for this coin
            key_match = f"{coin}_"  # partial match
            sig_regime = None
            for key, info in self.current_regime.items():
                if key.startswith(key_match):
                    sig_regime = info['condition']
                    break
            
            entry = selected.get(f"{coin}_{sig_regime}", {}).get(indicator, None)
            
            if entry and entry.get('confidence_rank', 0) >= min_rank:
                sig['regime_context'] = entry
                passed.append(sig)
            else:
                if entry:
                    sig['suppress_reason'] = f"confidence={entry.get('confidence')} < {min_confidence}"
                else:
                    sig['suppress_reason'] = f"no regime data for {indicator} in {sig_regime}"
                suppressed.append(sig)
        
        return passed, suppressed
    
    def get_selection_map(self):
        """Return {coin_regime: {indicator: entry}} for quick lookup."""
        selected = self.select()
        result = defaultdict(dict)
        for entry in selected:
            key = f"{entry.get('coin','')}_{entry.get('current_regime','')}"
            result[key][entry['indicator']] = {
                **entry,
                'confidence_rank': {'LOW': 0, 'PILOT': 1, 'MEDIUM': 2, 'HIGH': 3}.get(entry.get('confidence', 'LOW'), 0),
            }
        return dict(result)
    
    def summary(self):
        """Generate a concise summary for Gemini analyst context injection."""
        selected = self.select()
        if not selected:
            return "⚠️ Regime-Adaptive Selection: NO indicators qualify (insufficient data for current regime). Using legacy unfiltered signal list."
        
        lines = [f"🔄 **Regime-Adaptive Indicators** (mode: {self.mode})"]
        
        by_coin = defaultdict(list)
        for entry in selected:
            coin = entry.get('coin', '?')
            by_coin[coin].append(entry)
        
        for coin, entries in sorted(by_coin.items()):
            active = [e for e in entries if e.get('selected')]
            lines.append(f"\n**{coin}** ({len(active)} active / {len(entries)} total):")
            for e in entries:
                status = "✅" if e.get('selected') else "❌"
                conf = e.get('confidence', '?')
                lines.append(
                    f"  {status} {e['indicator']} — "
                    f"WR={e['win_rate']}% Wilson≥{e['wilson_lower']}% "
                    f"| n={e['total']} | {conf}"
                )
        
        lines.append(f"\n💡 _Pilot mode: low-confidence selections are labeled — treat with caution._")
        return "\n".join(lines)

# ═══════════════════════════════════════════
# Convenience functions for pipeline integration
# ═══════════════════════════════════════════

_selector_instance = None

def get_selector(mode='pilot'):
    """Get or create the singleton RegimeSelector."""
    global _selector_instance
    if _selector_instance is None or _selector_instance.mode != mode:
        _selector_instance = RegimeSelector(mode=mode)
    return _selector_instance

def get_regime_context():
    """Get regime context for Gemini analyst prompt injection.
    
    Call this from gemini_daily_analyst.py to get the regime-adaptive 
    indicator context to inject into the prompt.
    """
    sel = get_selector()
    return sel.summary()

def filter_signals_for_regime(signals, mode='pilot'):
    """Filter signals through regime selector.
    
    Call this from signal_logger.py before writing signals to DB.
    
    Args:
        signals: List of signal dicts
        mode: 'strict' or 'pilot'
    
    Returns:
        (passed_signals, suppressed_signals)
    """
    sel = get_selector(mode)
    return sel.filter_signals(signals)

# ═══════════════════════════════════════════
# CLI / Testing
# ═══════════════════════════════════════════

def main():
    import sys
    
    mode = 'pilot'
    if '--strict' in sys.argv:
        mode = 'strict'
    
    sel = RegimeSelector(mode=mode)
    results = sel.select()
    
    print(f"🔄 Regime-Adaptive Indicator Selection (mode: {mode})")
    print(f"═══ Current Regime ═══")
    for key, info in sel.current_regime.items():
        print(f"  {info['coin']} {info['timeframe']}: {info['condition']} (ADX={info['adx']})")
    print()
    
    selected = [r for r in results if r.get('selected')]
    unselected = [r for r in results if not r.get('selected')]
    
    if selected:
        print(f"✅ SELECTED ({len(selected)}):")
        for r in selected:
            print(f"  {r['confidence']:6s} {r['indicator']:25s} {r['coin']} {r['timeframe']} | {r['current_regime']} | WR={r['win_rate']}% Wilson≥{r['wilson_lower']}% | n={r['total']}")
    else:
        print(f"⚠️ NO INDICATORS SELECTED — all below threshold or insufficient data")
    
    if unselected:
        print(f"\n❌ BELOW THRESHOLD ({len(unselected)}):")
        for r in unselected:
            warning = r.get('warning', '?')
            print(f"  {r['indicator']:25s} {r['coin']} {r['timeframe']} | {r['current_regime']} | WR={r['win_rate']}% | {warning}")
    
    print()
    print(sel.summary())

if __name__ == '__main__':
    main()
