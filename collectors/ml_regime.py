#!/usr/bin/env python3
"""
ML Market Regime Classifier
讀取 OHLCV 數據，訓練 RandomForest，預測當前市場狀態 (bull/bear/range/volatile)
輸出 JSON 並與規則型 ADX 狀態比較
"""

import os
import sys
import json
import glob
import warnings
import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings('ignore')

# ============================================================
# CONFIG
# ============================================================
HOME = os.path.expanduser('~')
MARKET_DATA_DIR = os.path.join(HOME, 'market_data', 'daily')
OUTPUT_DIR = os.path.join(HOME, 'workspace', 'ml_data')
OUTPUT_FILE = os.path.join(OUTPUT_DIR, 'ml_regime_latest.json')

TICKERS = ['BTC_USD', 'ETH_USD', 'SOL_USD']
SYMBOLS_SHORT = {'BTC_USD': 'BTC', 'ETH_USD': 'ETH', 'SOL_USD': 'SOL'}

RF_N_ESTIMATORS = 100
RF_MAX_DEPTH = 5
MIN_TRAIN_SAMPLES = 30  # minimal samples for training
RECENT_YEARS = 2  # target years, but we use what we have


def find_latest_folder():
    """Find the most recent date folder in market_data/daily/"""
    folders = sorted(glob.glob(os.path.join(MARKET_DATA_DIR, '*-*-*')), reverse=True)
    if not folders:
        raise FileNotFoundError(f"No date folders found in {MARKET_DATA_DIR}")
    return folders[0]


def load_ohlcv(filepath):
    """Load OHLCV CSV with Date, Open, High, Low, Close, Volume"""
    df = pd.read_csv(filepath, parse_dates=['Date'])
    df.sort_values('Date', inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def compute_rsi(series, period=14):
    """Compute RSI using SMA method"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_adx(df, period=14):
    """Compute ADX (Average Directional Index) for rule-based comparison"""
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    # True Range
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    
    # Directional Movement
    up_move = high - high.shift()
    down_move = low.shift() - low
    
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=df.index)
    
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr.replace(0, np.nan))
    
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.rolling(window=period).mean()
    
    return adx, plus_di, minus_di


def adx_regime_rule(adx_val, plus_di_val, minus_di_val):
    """Classic ADX rule-based regime classification"""
    if pd.isna(adx_val) or pd.isna(plus_di_val) or pd.isna(minus_di_val):
        return 'unknown'
    
    if adx_val >= 25:
        if plus_di_val > minus_di_val:
            return 'bull'
        else:
            return 'bear'
    elif adx_val >= 20:
        # weak trend - directional
        if plus_di_val > minus_di_val:
            return 'bull'
        else:
            return 'bear'
    else:
        return 'range'


def engineer_features(df):
    """
    Compute features for regime classification.
    Features: returns (1d,5d,21d), volatility (5d,21d), RSI,
              volume change, MA deviations, ADX
    Automatically adapts to data length (skips features needing more data than available).
    """
    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume']
    n = len(df)
    
    features = pd.DataFrame(index=df.index)
    
    # Returns
    features['return_1d'] = close.pct_change()
    if n >= 6:
        features['return_5d'] = close.pct_change(periods=5)
    if n >= 22:
        features['return_21d'] = close.pct_change(periods=21)
    
    # Volatility (rolling std of daily returns)
    daily_ret = close.pct_change()
    features['vol_5d'] = daily_ret.rolling(window=5, min_periods=3).std()
    features['vol_21d'] = daily_ret.rolling(window=min(21, n//2), min_periods=5).std()
    
    # RSI
    features['rsi_14'] = compute_rsi(close, period=min(14, max(5, n//4)))
    
    # Volume change
    features['volume_change_1d'] = volume.pct_change()
    if n >= 6:
        features['volume_change_5d'] = volume.pct_change(periods=5)
    
    # MA deviations: (price - MA) / MA — only for periods we have enough data
    for ma_period in [20, 50, 200]:
        if n >= ma_period:
            ma = close.rolling(window=ma_period).mean()
            features[f'ma_dev_{ma_period}'] = (close - ma) / ma.replace(0, np.nan)
    
    # ADX
    adx_period = min(14, max(5, n//5))
    adx, plus_di, minus_di = compute_adx(df, period=adx_period)
    features['adx'] = adx
    features['plus_di'] = plus_di
    features['minus_di'] = minus_di
    
    return features


def label_regime(df, vol_percentile=90):
    """
    Label each day with regime:
    - volatile: vol_21d > 90th percentile of all vol_21d
    - bull: return_5d > +2%
    - bear: return_5d < -2%
    - range: everything else
    """
    daily_ret = df['Close'].pct_change()
    vol_21d = daily_ret.rolling(window=21).std()
    vol_threshold = vol_21d.quantile(vol_percentile / 100.0)
    
    ret_5d = df['Close'].pct_change(periods=5)
    
    labels = pd.Series(index=df.index, dtype='object')
    
    for i in df.index:
        if pd.isna(vol_21d.loc[i]) or pd.isna(ret_5d.loc[i]):
            labels.loc[i] = np.nan
            continue
        
        # Priority: volatile first
        if vol_21d.loc[i] >= vol_threshold:
            labels.loc[i] = 'volatile'
        elif ret_5d.loc[i] > 0.02:
            labels.loc[i] = 'bull'
        elif ret_5d.loc[i] < -0.02:
            labels.loc[i] = 'bear'
        else:
            labels.loc[i] = 'range'
    
    return labels


def prepare_training_data(df):
    """Prepare features and labels for training"""
    features = engineer_features(df)
    labels = label_regime(df)
    
    # Align and drop NaN
    combined = features.copy()
    combined['label'] = labels
    combined.dropna(inplace=True)
    
    if len(combined) < MIN_TRAIN_SAMPLES:
        return None, None, None
    
    X = combined.drop(columns=['label'])
    y = combined['label']
    
    return X, y, features


def train_model(X, y):
    """Train RandomForest classifier"""
    # Scale features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # Train
    model = RandomForestClassifier(
        n_estimators=RF_N_ESTIMATORS,
        max_depth=RF_MAX_DEPTH,
        random_state=42,
        class_weight='balanced'
    )
    model.fit(X_scaled, y)
    
    return model, scaler


def predict_regime(model, scaler, latest_features):
    """Predict regime for the latest data point"""
    X_latest = latest_features.iloc[-1:].copy()
    
    # Handle NaN in latest features
    if X_latest.isna().any(axis=None):
        return None, None
    
    X_scaled = scaler.transform(X_latest)
    
    probs = model.predict_proba(X_scaled)[0]
    prediction = model.predict(X_scaled)[0]
    confidence = float(np.max(probs))
    
    classes = list(model.classes_)
    prob_dict = {cls: float(probs[i]) for i, cls in enumerate(classes)}
    
    # Ensure all 4 classes are present
    for regime in ['bull', 'bear', 'range', 'volatile']:
        if regime not in prob_dict:
            prob_dict[regime] = 0.0
    
    return prediction, confidence, prob_dict


def process_ticker(filepath):
    """Full pipeline for one ticker"""
    df = load_ohlcv(filepath)
    
    if len(df) < 60:  # Need minimum data
        return None
    
    X, y, all_features = prepare_training_data(df)
    
    if X is None or len(X) < MIN_TRAIN_SAMPLES:
        return None
    
    # Train model
    model, scaler = train_model(X, y)
    
    # Predict latest regime
    latest_features = all_features.iloc[-1:]
    if latest_features.isna().any(axis=None):
        # Fall back to using only the last row with valid features
        # Look backwards for the last valid row
        valid_idx = all_features.dropna().index
        if len(valid_idx) == 0:
            return None
        latest_features = all_features.loc[[valid_idx[-1]]]
    
    prediction, confidence, prob_dict = predict_regime(model, scaler, latest_features)
    
    if prediction is None:
        return None
    
    # ADX rule-based regime for comparison
    adx_series, plus_di_series, minus_di_series = compute_adx(df)
    latest_adx = adx_series.iloc[-1]
    latest_plus_di = plus_di_series.iloc[-1]
    latest_minus_di = minus_di_series.iloc[-1]
    adx_regime = adx_regime_rule(latest_adx, latest_plus_di, latest_minus_di)
    
    # Label distribution
    label_counts = y.value_counts().to_dict()
    total_samples = len(y)
    
    # Confidence level
    n_features = X.shape[1]
    n_samples = len(X)
    
    confidence_level = 'normal'
    if n_samples < 60:
        confidence_level = 'low'
    elif n_samples < 120:
        confidence_level = 'moderate'
    
    result = {
        'regime': prediction,
        'confidence': round(confidence, 4),
        'confidence_level': confidence_level,
        'probabilities': prob_dict,
        'adx_comparison': {
            'adx_regime': adx_regime,
            'adx_value': round(float(latest_adx), 2) if not pd.isna(latest_adx) else None,
            'plus_di': round(float(latest_plus_di), 2) if not pd.isna(latest_plus_di) else None,
            'minus_di': round(float(latest_minus_di), 2) if not pd.isna(latest_minus_di) else None,
        },
        'training_info': {
            'n_samples': n_samples,
            'n_features': n_features,
            'label_distribution': {str(k): int(v) for k, v in label_counts.items()},
            'date_range': {
                'start': str(df['Date'].iloc[0].date()),
                'end': str(df['Date'].iloc[-1].date()),
            }
        }
    }
    
    return result


def main():
    parser = argparse.ArgumentParser(description='ML Market Regime Classifier')
    parser.add_argument('--output', '-o', default=OUTPUT_FILE, help='Output JSON path')
    parser.add_argument('--data-dir', '-d', default=None, help='Specific date folder (e.g. 2026-05-20)')
    args = parser.parse_args()
    
    # Find latest data folder
    if args.data_dir:
        data_folder = os.path.join(MARKET_DATA_DIR, args.data_dir)
        if not os.path.isdir(data_folder):
            print(f"ERROR: Specified data folder not found: {data_folder}")
            sys.exit(1)
    else:
        data_folder = find_latest_folder()
    
    print(f"📂 Data folder: {data_folder}")
    print(f"📊 Training RandomForest (n_estimators={RF_N_ESTIMATORS}, max_depth={RF_MAX_DEPTH})...")
    
    results = {}
    
    for ticker in TICKERS:
        filepath = os.path.join(data_folder, f"{ticker}.csv")
        if not os.path.exists(filepath):
            print(f"  ⚠️  {ticker}: file not found, skipping")
            continue
        
        print(f"  🔄 {ticker}...", end=' ')
        result = process_ticker(filepath)
        
        if result is None:
            print("❌ insufficient data")
            continue
        
        symbol = SYMBOLS_SHORT[ticker]
        results[symbol] = result
        
        pred = result['regime']
        conf = result['confidence']
        adx_r = result['adx_comparison']['adx_regime']
        cl = result['confidence_level']
        
        emoji = {'bull': '🐂', 'bear': '🐻', 'range': '➖', 'volatile': '🌪️'}.get(pred, '❓')
        
        match_icon = '✅' if pred == adx_r else '⚠️'
        print(f"{emoji} ML: {pred} ({conf:.1%}, {cl}) | ADX: {adx_r} {match_icon}")
    
    # Save output
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Output saved to: {args.output}")
    
    # Print summary
    print("\n📋 Regime Summary:")
    print("=" * 60)
    for symbol, data in results.items():
        pred = data['regime']
        conf = data['confidence']
        emoji = {'bull': '🐂', 'bear': '🐻', 'range': '➖', 'volatile': '🌪️'}.get(pred, '❓')
        adx_r = data['adx_comparison']['adx_regime']
        match = '✅' if pred == adx_r else '⚠️ 分歧'
        print(f"  {symbol}: {emoji} ML={pred.upper():>8} (信心:{conf:.1%}) | ADX={adx_r.upper():>8} {match}")
    
    return results


if __name__ == '__main__':
    results = main()
