import sys, os
sys.path.append(r"/home/yiucheng620/workspace/shadow_lab")
from shadow_base import ShadowEngine

import numpy as np
import pandas as pd
from collections import deque

class ShadowEngine_BTC_1779973520(ShadowEngine):
    def __init__(self, market_name: str, strategy_id: str):
        super().__init__(market_name, strategy_id)
        # Volatility tracking
        self.vol_window = 24  # hours of data to estimate volatility
        self.min_vol_multiplier = 0.5  # min vol threshold as fraction of recent median
        self.max_vol_multiplier = 1.5  # max vol threshold
        self.vol_buffer = deque(maxlen=self.vol_window * 60)  # store minute-level returns
        
        # Weekend detection (BTC markets are 24/7; we define weekend as Sat 00:00 UTC to Sun 23:59 UTC)
        self.weekend_hours = set(range(0, 24))  # all hours on Sat/Sun
        
        # Chop detection
        self.chop_window = 12  # hours
        self.chop_buffer = deque(maxlen=self.chop_window * 60)  # store close prices
        
        # Cooldown to avoid re-entering after a failed trade
        self.cooldown_period = 4  # hours
        self.last_signal_time = None
        
    def is_weekend(self, timestamp):
        """Check if timestamp (pd.Timestamp or datetime) falls on weekend."""
        if isinstance(timestamp, (int, float)):
            timestamp = pd.Timestamp(timestamp, unit='s')
        return timestamp.weekday() >= 5  # 5=Saturday, 6=Sunday
    
    def compute_volatility(self, returns):
        """Compute rolling volatility (standard deviation) from returns list."""
        if len(returns) < 10:
            return None
        return np.std(list(returns))
    
    def detect_chop(self, prices):
        """Detect choppy market using ATR-like range vs directional movement."""
        if len(prices) < self.chop_window * 60:
            return True  # default to cautious
        prices_arr = np.array(list(prices))
        # Calculate directional movement index (DMI) simplified: ratio of absolute returns to range
        high = np.max(prices_arr)
        low = np.min(prices_arr)
        range_val = high - low
        if range_val == 0:
            return True
        # Mean absolute return
        returns = np.diff(prices_arr)
        mean_abs_ret = np.mean(np.abs(returns))
        # Chop index: if mean absolute return is small relative to range, market is choppy
        chop_index = mean_abs_ret / range_val
        # Threshold: if chop_index < 0.1 (very low directional movement relative to range), it's choppy
        return chop_index < 0.1
    
    def generate_signal(self, data):
        """
        data: dict with keys: 'timestamp', 'close', 'volume' (and optionally 'high', 'low')
        Returns: dict with 'action' (BUY/SELL/NONE) or None
        """
        timestamp = data.get('timestamp')
        close = data.get('close')
        volume = data.get('volume')
        
        if None in [timestamp, close, volume]:
            return None
        
        # Convert timestamp if needed
        if isinstance(timestamp, (int, float)):
            ts = pd.Timestamp(timestamp, unit='s')
        else:
            ts = pd.Timestamp(timestamp)
        
        # --- Cooldown check ---
        if self.last_signal_time is not None:
            hours_since_last = (ts - self.last_signal_time).total_seconds() / 3600
            if hours_since_last < self.cooldown_period:
                return None
        
        # --- Weekend avoidance ---
        if self.is_weekend(ts):
            return None
        
        # --- Update volatility buffer ---
        if len(self.vol_buffer) > 0:
            prev_close = self.vol_buffer[-1]
            ret = (close - prev_close) / prev_close
            self.vol_buffer.append(ret)
        else:
            self.vol_buffer.append(0.0)  # first entry placeholder
        
        # --- Update chop buffer ---
        self.chop_buffer.append(close)
        
        # --- Combine volatility and chop checks ---
        current_vol = self.compute_volatility(self.vol_buffer)
        if current_vol is None:
            return None
        
        # Get rolling median volatility (use last 12 hours)
        vol_list = list(self.vol_buffer)
        if len(vol_list) < 720:  # at least 12 hours of data
            return None
        recent_vol = vol_list[-720:]
        median_vol = np.median(recent_vol)
        
        # Check if current vol is too high or too low relative to median
        if median_vol == 0:
            return None
        vol_ratio = current_vol / median_vol
        
        if vol_ratio > self.max_vol_multiplier:
            # High volatility period - avoid
            return None
        
        # --- Chop check ---
        if self.detect_chop(self.chop_buffer):
            return None
        
        # --- Signal generation based on simple trend following (avoiding chop) ---
        # Use short-term MA vs long-term MA
        prices = list(self.chop_buffer)
        if len(prices) < 120:  # need at least 2 hours
            return None
        
        short_ma = np.mean(prices[-20:])  # last 20 minutes
        long_ma = np.mean(prices[-120:])  # last 120 minutes (2 hours)
        
        # Only trade if trend is clear and volatility is moderate
        if short_ma > long_ma * 1.001 and vol_ratio < 1.0:
            # Uptrend with low relative vol
            self.last_signal_time = ts
            return {'action': 'BUY'}
        elif short_ma < long_ma * 0.999 and vol_ratio < 1.0:
            # Downtrend with low relative vol
            self.last_signal_time = ts
            return {'action': 'SELL'}
        
        return None