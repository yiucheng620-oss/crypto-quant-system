#!/usr/bin/env python3
"""
AI Bubble Daily Monitor Script V3
- SOX MA20 偏離度放最頂（最關鍵數字）
- 方向箭頭：↑惡化 ↓改善 →持平
- 警報級別對應行動建議
- Extra context 濃縮到 5 項核心

Key thresholds:
  SOX:    < 11,500 = WARN, < 11,200 = DANGER
  ANET:   < $145   = WARN, < $130   = DANGER
  NVDA:   < $210   = WARN, < $200   = DANGER
  HYG:    < $79.80 = WARN, < $79.50 = DANGER (credit stress)
  VIX:    > 20     = WARN, > 22     = DANGER
  SKEW:   > 140    = WARN, > 145    = DANGER
"""

import urllib.request
import json
from datetime import datetime

# ========== CONFIG ==========
THRESHOLDS = {
    '^SOX':    {'warn': 11500, 'danger': 11200, 'label': 'SOX Index'},
    'ANET':    {'warn': 145,   'danger': 130,   'label': 'Arista Networks'},
    'NVDA':    {'warn': 210,   'danger': 200,   'label': 'NVIDIA'},
    'AMD':     {'warn': 430,   'danger': 400,   'label': 'AMD'},
    'HYG':     {'warn': 79.80, 'danger': 79.50, 'label': 'High Yield ETF'},
    'LQD':     {'warn': 108.0, 'danger': 107.0, 'label': 'Inv Grade ETF'},
}

INVERSE_THRESHOLDS = {
    '^VIX':    {'warn': 20,    'danger': 22,    'label': 'VIX'},
    '^VIX9D':  {'warn': 20,    'danger': 22,    'label': 'VIX 9-Day'},
    '^SKEW':   {'warn': 140,   'danger': 145,   'label': 'SKEW Tail Risk'},
}

# Reduced to 5 key context items
MONITOR_EXTRA = ['SMCI', 'DDOG', 'SPY', 'QQQ', 'TLT']

# ========== DATA FETCH ==========
def fetch_yahoo(symbols, range_days='5d'):
    results = {}
    for sym in symbols:
        url = 'https://query1.finance.yahoo.com/v8/finance/chart/{}?range={}&interval=1d'.format(sym, range_days)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=10).read())
            result = data['chart']['result'][0]
            closes = result['indicators']['quote'][0]['close']
            if closes:
                recent = closes[-6:] if len(closes) >= 6 else closes
                current = recent[-1]
                prev = recent[-2] if len(recent) >= 2 else current
                chg_1d = ((current - prev) / prev * 100) if prev else 0
                chg_5d = ((recent[-1] - recent[0]) / recent[0] * 100) if len(recent) >= 2 else 0
                results[sym] = {
                    'price': current,
                    'chg_1d': chg_1d,
                    'chg_5d': chg_5d,
                }
        except Exception as e:
            results[sym] = {'error': str(e)[:80]}
    return results

def fetch_sox_ma20():
    """Fetch 30 days of SOX data and calculate MA20 deviation"""
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX?range=1mo&interval=1d'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        if len(closes) >= 21:
            ma20_today = sum(closes[-20:]) / 20
            ma20_yesterday = sum(closes[-21:-1]) / 20
            current = closes[-1]
            dev_today = ((current - ma20_today) / ma20_today) * 100
            dev_yesterday = ((closes[-2] - ma20_yesterday) / ma20_yesterday) * 100 if len(closes) >= 2 else 0
            return {
                'current': current,
                'ma20': ma20_today,
                'deviation': dev_today,
                'dev_yesterday': dev_yesterday,
            }
    except Exception:
        pass
    return None

# ========== DIRECTION ARROWS ==========
def direction_arrow(current, previous, is_inverse=False):
    """Return direction arrow based on trend.
    For normal thresholds: higher price = good (↓ improving)
    For inverse thresholds (VIX/SKEW): lower = good (↓ improving)
    """
    if current is None or previous is None:
        return ''
    diff = current - previous
    if abs(diff) < 0.001:
        return '→'
    if is_inverse:
        return '↑惡化' if diff > 0 else '↓改善'
    else:
        return '↓惡化' if diff < 0 else '↑改善'

def price_direction(current, previous):
    """Simple price direction: up or down"""
    if current is None or previous is None:
        return ''
    diff = current - previous
    if abs(diff) < 0.01:
        return '→持平'
    return '↑' if diff > 0 else '↓'

# ========== ALERTS + ACTIONS ==========
def check_alerts_with_actions(data, sox_ma20):
    alerts = []
    actions = []
    
    for sym, cfg in THRESHOLDS.items():
        d = data.get(sym, {})
        price = d.get('price')
        if price is None:
            continue
        
        label = cfg['label']
        if price <= cfg['danger']:
            alerts.append(('🔴 DANGER', '{}: ${:.2f} (threshold: ${})'.format(label, price, cfg['danger'])))
            if sym == 'HYG':
                actions.append('🔴 信用市場出現裂縫！立即檢討所有風險倉位')
            elif sym == 'ANET':
                actions.append('🔴 ANET 跌穿 $130！AI 網絡支出全面收縮訊號，建議檢討所有 AI 硬件倉位')
            elif sym == '^SOX':
                actions.append('🔴 SOX 跌穿 11,200！轉防守模式，考慮減倉或買對沖')
        elif price <= cfg['warn']:
            alerts.append(('🟡 WARN', '{}: ${:.2f} (threshold: ${})'.format(label, price, cfg['warn'])))
            if sym == 'ANET':
                actions.append('🟡 ANET 低於 $145 警報線，觀察會否跌穿 $130')
    
    for sym, cfg in INVERSE_THRESHOLDS.items():
        d = data.get(sym, {})
        price = d.get('price')
        if price is None:
            continue
        
        label = cfg['label']
        if price >= cfg['danger']:
            alerts.append(('🔴 DANGER', '{}: {:.2f} (threshold: {:.1f})'.format(label, price, cfg['danger'])))
            if sym == '^VIX':
                actions.append('🔴 VIX > 22！市場進入恐懼模式，減倉或買對沖')
            elif sym == '^SKEW':
                actions.append('🔴 SKEW > 145！極端尾風險，聰明錢大量買 put，立即檢討')
        elif price >= cfg['warn']:
            alerts.append(('🟡 WARN', '{}: {:.2f} (threshold: {:.1f})'.format(label, price, cfg['warn'])))
            if sym == '^SKEW':
                actions.append('🟡 SKEW 高於 140，聰明錢在佈局對沖，保持警覺')
    
    # SOX MA20 specific action
    if sox_ma20:
        dev = sox_ma20['deviation']
        if dev > 15:
            actions.append('⚠️ SOX MA20 偏離 >15% — 歷史回調概率 >70%，不要現價追入')
        elif dev > 10:
            actions.append('⚠️ SOX MA20 偏離 >10% — 超買，等回調先入場')
    
    return alerts, actions

# ========== MAIN ==========
def main():
    # Fetch current data
    all_symbols = list(THRESHOLDS.keys()) + list(INVERSE_THRESHOLDS.keys()) + MONITOR_EXTRA
    data = fetch_yahoo(all_symbols)
    sox_ma20 = fetch_sox_ma20()
    
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    
    # === HEADER ===
    print('📡 AI Bubble Daily ｜ {}'.format(now))
    
    # === SOX MA20 — THE KEY METRIC ===
    if sox_ma20:
        dev = sox_ma20['deviation']
        dev_prev = sox_ma20['dev_yesterday']
        arrow = direction_arrow(dev, dev_prev, is_inverse=True)
        bar = '█' * min(int(abs(dev)), 20)
        print('SOX: ${:,.0f} ｜ MA20 偏離 {}{:+.1f}% {}'.format(
            sox_ma20['current'], bar[:3], dev, arrow))
        print('MA20: ${:,.0f}'.format(sox_ma20['ma20']))
    print()
    
    # === THRESHOLD SUMMARY — compact, with direction ===
    # Group into: 正常 / 警報
    ok_items = []
    warn_items = []
    danger_items = []
    
    for sym, cfg in THRESHOLDS.items():
        d = data.get(sym, {})
        price = d.get('price')
        if price is None:
            continue
        chg = price_direction(price, price - d.get('chg_1d', 0) * price / 100)
        line = '{} {} ${:.0f}'.format(cfg['label'], chg, price)
        if price <= cfg['danger']:
            danger_items.append('🔴 ' + line)
        elif price <= cfg['warn']:
            warn_items.append('🟡 ' + line)
        else:
            ok_items.append(line)
    
    for sym, cfg in INVERSE_THRESHOLDS.items():
        d = data.get(sym, {})
        price = d.get('price')
        if price is None:
            continue
        chg = price_direction(price, price - d.get('chg_1d', 0) * price / 100)
        line = '{} {} {:.2f}'.format(cfg['label'], chg, price)
        if price >= cfg['danger']:
            danger_items.append('🔴 ' + line)
        elif price >= cfg['warn']:
            warn_items.append('🟡 ' + line)
        else:
            ok_items.append(line)
    
    if danger_items:
        for item in danger_items:
            print(item)
    if warn_items:
        for item in warn_items:
            print(item)
    # Only show OK items if nothing is wrong, or just 1 line summary
    if not danger_items and not warn_items:
        print('🟢 全部指標正常')
    else:
        ok_count = len(ok_items)
        if ok_count > 0:
            print('🟢 其餘 {} 項正常'.format(ok_count))
    
    # === ALERTS + ACTIONS ===
    alerts, actions = check_alerts_with_actions(data, sox_ma20)
    
    if alerts:
        print()
        print('=== 🚨 警報 ===')
        for level, msg in alerts:
            print('  {} {}'.format(level, msg))
    
    if actions:
        print()
        print('=== 💡 建議行動 ===')
        for act in actions:
            print('  ' + act)
    
    # === EXTRA CONTEXT (5 key items) ===
    print()
    print('=== 📊 背景數據 ===')
    for sym in MONITOR_EXTRA:
        d = data.get(sym, {})
        price = d.get('price')
        if price is not None:
            chg_1d = d.get('chg_1d', 0)
            arrow = '↑' if chg_1d > 0 else ('↓' if chg_1d < 0 else '→')
            print('  {:8s} ${:>10.2f}  {}{:+.1f}%'.format(sym, price, arrow, chg_1d))
    
    # === VIX TERM STRUCTURE ===
    vix_spot = data.get('^VIX', {}).get('price')
    vix_9d = data.get('^VIX9D', {}).get('price')
    vix_3m = data.get('^VIX3M', {}).get('price')
    print()
    print('=== VIX 期限結構 ===')
    if vix_spot and vix_9d and vix_3m:
        struct = 'contango ↑' if vix_3m > vix_spot else 'backwardation ↓'
        print('  Spot:{:.1f} → 9D:{:.1f} → 3M:{:.1f}  ({})'.format(vix_spot, vix_9d, vix_3m, struct))

if __name__ == '__main__':
    main()
