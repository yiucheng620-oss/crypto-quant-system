#!/usr/bin/env python3
"""
三市直擊 — Daily Multi-Asset Strategy Report
Data collection + rule-based signals + structured report
No LLM needed — all logic is rule-based.
Runs daily after US market close.
"""

import urllib.request
import json
from datetime import datetime, timezone

# ========== CONFIG ==========
SYMBOLS = {
    'US':       ['SPY', 'QQQ', 'IWM'],
    'SECTORS':  ['XLK', 'XLF', 'XLE'],
    'CRYPTO':   ['BTC-USD', 'ETH-USD', 'SOL-USD'],
    'GOLD':     ['GLD', 'GDX'],
    'MACRO':    ['^VIX', 'UUP', '^TNX'],
}

# ========== DATA FETCH ==========
def fetch(symbol, range_days='1mo'):
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/{}?range={}&interval=1d'.format(symbol, range_days)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        r = data['chart']['result'][0]
        return {
            'c': r['indicators']['quote'][0]['close'],
            'v': r['indicators']['quote'][0]['volume'],
            'h': r['indicators']['quote'][0]['high'],
            'l': r['indicators']['quote'][0]['low'],
        }
    except:
        return None

def ma(data, period):
    if not data:
        return 0
    # Filter out None values (Yahoo Finance quirk)
    clean = [x for x in data if x is not None]
    if not clean:
        return 0
    if len(clean) < period:
        return sum(clean)/len(clean)
    return sum(clean[-period:])/period

def sr_levels(h, l, c, n=20):
    cur = c[-1]
    rh = sorted(set(round(x,2) for x in h[-n:] if x > cur), reverse=True)
    rl = sorted(set(round(x,2) for x in l[-n:] if x < cur))
    return rl[-3:], rh[:3]

def vratio(v):
    clean = [x for x in v if x is not None]
    if len(clean) < 22:
        return 1.0
    avg = sum(clean[-22:-1])/21
    return clean[-1]/avg if avg > 0 else 1.0

# ========== SIGNAL LOGIC ==========
def signal_equity(name, price, chg_1d, ma20_dev, ma50_dev, vol_r, supports, resistances, nr, rt):
    """Rule-based equity signal"""
    lines = []
    
    # Trend assessment
    if ma20_dev > 5:
        lines.append('MA20 +{:.1f}% 超買，回調風險偏高'.format(ma20_dev))
    elif ma20_dev > 2:
        lines.append('MA20 +{:.1f}% 偏強但未過熱'.format(ma20_dev))
    elif ma20_dev < -3:
        lines.append('MA20 {:.1f}% 超賣，可能反彈'.format(ma20_dev))
    else:
        lines.append('MA20 {:.1f}% 中性區間'.format(ma20_dev))
    
    # Volume analysis
    if vol_r and vol_r > 1.5:
        lines.append('成交量 {:.1f}x 異常放量'.format(vol_r))
    elif vol_r and vol_r < 0.6:
        lines.append('成交量 {:.1f}x 極度萎縮，等待方向'.format(vol_r))
    
    # Signal
    signal = '🟡 持有'
    reason = ''
    
    if ma20_dev < -3 and chg_1d < 0 and vol_r and vol_r < 0.8:
        signal = '🟢 可觀察'
        reason = '超賣 + 縮量下跌，可能見底'
    elif ma20_dev < -3 and chg_1d > 0:
        signal = '🟢 可小注'
        reason = '超賣反彈中'
    elif ma20_dev > 7 and chg_1d > 0 and vol_r and vol_r < 0.8:
        signal = '🔴 減倉'
        reason = '超買 + 量背離'
    elif ma20_dev > 5 and chg_1d < 0:
        signal = '🔴 唔好撈'
        reason = '超買回落中，未到底'
    elif ma20_dev > 2 and ma20_dev <= 5:
        signal = '🟡 持有'
        reason = '趨勢向好，等回調加倉'
    elif ma20_dev >= -3 and ma20_dev <= 2:
        signal = '🟡 持有'
        reason = '區間整固，等方向'
    
    return signal, reason, lines

def signal_crypto(name, price, chg_1d, ma20_dev, vol_r, supports, resistances, nr, rt):
    """Rule-based crypto signal — more volatile, wider bands"""
    lines = []
    
    if ma20_dev > 10:
        lines.append('MA20 +{:.1f}% 極度超買'.format(ma20_dev))
    elif ma20_dev > 5:
        lines.append('MA20 +{:.1f}% 超買'.format(ma20_dev))
    elif ma20_dev < -8:
        lines.append('MA20 {:.1f}% 極度超賣'.format(ma20_dev))
    elif ma20_dev < -3:
        lines.append('MA20 {:.1f}% 超賣'.format(ma20_dev))
    else:
        lines.append('MA20 {:.1f}% 正常區間'.format(ma20_dev))
    
    signal = '🟡 持有'
    reason = ''
    
    if ma20_dev < -5 and chg_1d < 0:
        signal = '🟢 可觀察'
        reason = '超賣，可能見底'
    elif ma20_dev < -5 and chg_1d > 0:
        signal = '🟢 可小注'
        reason = '超賣反彈訊號'
    elif ma20_dev > 10:
        signal = '🔴 唔好追'
        reason = '極度超買，回調概率 >80%'
    elif ma20_dev > 5:
        signal = '🔴 謹慎'
        reason = '超買，等回調'
    elif ma20_dev >= -3 and ma20_dev <= 3:
        signal = '🟡 持有'
        reason = '整固中，等突破'
    
    return signal, reason, lines

def signal_gold(name, price, chg_1d, ma20_dev, vol_r):
    """Rule-based gold signal"""
    signal = '🟡 持有'
    reason = '避險對沖角色'
    if ma20_dev < -3:
        signal = '🟢 可加注'
        reason = '超賣，避險需求可能回歸'
    elif ma20_dev > 5:
        signal = '🟡 持有'
        reason = '強勢但不宜追'
    return signal, reason

# ========== REPORT GENERATOR ==========
def generate_report(all_data):
    now = datetime.now(timezone.utc)
    hkt = now.strftime('%H:%M HKT')
    
    lines = []
    lines.append('📊 三市直擊 ｜ {}'.format(now.strftime('%Y-%m-%d')))
    lines.append('🕐 {} ｜ 美股收盤數據'.format(hkt))
    lines.append('')
    
    # ━━━ US EQUITIES ━━━
    lines.append('━━━ 🇺🇸 美股 ━━━')
    
    for sym in ['QQQ', 'SPY', 'IWM']:
        d = all_data.get(sym, {})
        if 'error' in d:
            lines.append('  {} ❌ 數據錯誤'.format(sym))
            continue
        
        p, c1, c5 = d['price'], d['chg_1d'], d['chg_5d']
        arrow = '↑' if c1 > 0 else ('↓' if c1 < 0 else '→')
        sig, reason, details = signal_equity(
            sym, p, c1, d['ma20_dev'], d['ma50_dev'], d['vol_ratio'],
            d['supports'], d['resistances'], d.get('nearest_resistance'), d.get('resistance_tests', 0)
        )
        
        lines.append('  {} {} ${:,.2f} {} {:+.1f}%'.format(sig, sym, p, arrow, c1))
        lines.append('    MA20 {:+.1f}% ｜ 支持 {} ｜ 阻力 {}'.format(
            d['ma20_dev'], 
            '${:,.0f}'.format(d['supports'][0]) if d['supports'] else '?',
            '${:,.0f}'.format(d['resistances'][0]) if d['resistances'] else '?'))
        if d['vol_ratio']:
            lines.append('    量: {:.1f}x平均'.format(d['vol_ratio']))
        lines.append('    → {}: {}'.format(sig, reason))
        lines.append('')
    
    # Sector rotation
    xlk_d = all_data.get('XLK', {})
    xlf_d = all_data.get('XLF', {})
    xle_d = all_data.get('XLE', {})
    if all(k not in d for d in [xlk_d, xlf_d, xle_d] for k in ['error']):
        xlk_c = xlk_d.get('chg_1d', 0)
        xlf_c = xlf_d.get('chg_1d', 0)
        xle_c = xle_d.get('chg_1d', 0)
        lines.append('  ⚡ 板塊輪動: XLK {:+.1f}% | XLF {:+.1f}% | XLE {:+.1f}%'.format(xlk_c, xlf_c, xle_c))
        if xlk_c < -1 and (xlf_c > 0 or xle_c > 0):
            lines.append('     → 資金由科技轉向價值/能源，輪動中')
        lines.append('')
    
    # ━━━ CRYPTO ━━━
    lines.append('━━━ ₿ 加密 ━━━')
    
    for sym in ['BTC-USD', 'ETH-USD', 'SOL-USD']:
        d = all_data.get(sym, {})
        if 'error' in d:
            lines.append('  {} ❌ 數據錯誤'.format(sym))
            continue
        
        name = sym.replace('-USD', '')
        p, c1 = d['price'], d['chg_1d']
        arrow = '↑' if c1 > 0 else ('↓' if c1 < 0 else '→')
        sig, reason, details = signal_crypto(
            name, p, c1, d['ma20_dev'], d['vol_ratio'],
            d['supports'], d['resistances'], d.get('nearest_resistance'), d.get('resistance_tests', 0)
        )
        
        lines.append('  {} {} ${:,.0f} {} {:+.1f}%'.format(sig, name, p, arrow, c1))
        lines.append('    MA20 {:+.1f}% ｜ 支持 ${:,.0f} ｜ 阻力 ${:,.0f}'.format(
            d['ma20_dev'],
            d['supports'][0] if d['supports'] else 0,
            d['resistances'][0] if d['resistances'] else 0))
        lines.append('    → {}: {}'.format(sig, reason))
        lines.append('')
    
    # ━━━ GOLD ━━━
    lines.append('━━━ 🥇 黃金 ━━━')
    
    for sym in ['GLD', 'GDX']:
        d = all_data.get(sym, {})
        if 'error' in d:
            lines.append('  {} ❌ 數據錯誤'.format(sym))
            continue
        
        p, c1 = d['price'], d['chg_1d']
        arrow = '↑' if c1 > 0 else ('↓' if c1 < 0 else '→')
        sig, reason = signal_gold(sym, p, c1, d['ma20_dev'], d['vol_ratio'])
        
        lines.append('  {} {} ${:,.2f} {} {:+.1f}%'.format(sig, sym, p, arrow, c1))
        lines.append('    MA20 {:+.1f}% → {}: {}'.format(d['ma20_dev'], sig, reason))
        lines.append('')
    
    # ━━━ MACRO ━━━
    lines.append('━━━ 📊 宏觀 ━━━')
    
    vix_d = all_data.get('^VIX', {})
    uup_d = all_data.get('UUP', {})
    tnx_d = all_data.get('^TNX', {})
    
    if 'error' not in vix_d:
        lines.append('  VIX: {:.2f} ({:+.1f}%)'.format(vix_d['price'], vix_d['chg_1d']))
    if 'error' not in uup_d:
        lines.append('  美元 UUP: ${:.2f} ({:+.1f}%)'.format(uup_d['price'], uup_d['chg_1d']))
    if 'error' not in tnx_d:
        lines.append('  10Y 利率: {:.2f}% ({:+.1f}%)'.format(tnx_d['price'], tnx_d['chg_1d']))
    
    # Macro interpretation
    if 'error' not in tnx_d and 'error' not in vix_d:
        tnx_c = tnx_d.get('chg_1d', 0)
        vix_val = vix_d.get('price', 20)
        if tnx_c > 1 and vix_val < 20:
            lines.append('  → 10Y 升 + VIX 平 = 資金成本升但未恐慌')
        if vix_val > 22:
            lines.append('  ⚠️ VIX >22，市場進入恐懼模式')
    
    lines.append('')
    
    # ━━━ SUMMARY TABLE ━━━
    lines.append('━━━ ⚡ 今日策略總表 ━━━')
    lines.append('')
    
    summary_items = []
    for sym in ['QQQ', 'SPY', 'BTC-USD', 'ETH-USD', 'SOL-USD', 'GLD']:
        d = all_data.get(sym, {})
        if 'error' in d:
            continue
        name = sym.replace('-USD', '')
        if sym in ['QQQ', 'SPY']:
            sig, reason, _ = signal_equity(name, d['price'], d['chg_1d'], d['ma20_dev'], d['ma50_dev'],
                                            d['vol_ratio'], d['supports'], d['resistances'],
                                            d.get('nearest_resistance'), d.get('resistance_tests', 0))
        elif sym in ['BTC-USD', 'ETH-USD', 'SOL-USD']:
            sig, reason, _ = signal_crypto(name, d['price'], d['chg_1d'], d['ma20_dev'],
                                            d['vol_ratio'], d['supports'], d['resistances'],
                                            d.get('nearest_resistance'), d.get('resistance_tests', 0))
        else:
            sig, reason = signal_gold(name, d['price'], d['chg_1d'], d['ma20_dev'], d['vol_ratio'])
        summary_items.append('  {} {:<6s} {}'.format(sig, name, reason))
    
    lines.extend(summary_items)
    lines.append('')
    lines.append('━━━━━━━━━━━━━━━━━━━━')
    lines.append('⚡ 信號僅供參考，不構成投資建議。')
    
    return '\n'.join(lines)

# ========== MAIN ==========
def main():
    # Fetch all data
    all_data = {}
    for category, symbols in SYMBOLS.items():
        for sym in symbols:
            raw = fetch(sym)
            if raw is None:
                all_data[sym] = {'error': 'fetch failed'}
                continue
            
            c, v, h, l = raw['c'], raw['v'], raw['h'], raw['l']
            # Clean None values (Yahoo Finance returns null for non-trading days)
            c = [x for x in c if x is not None]
            v = [x for x in v if x is not None]
            h = [x for x in h if x is not None]
            l = [x for x in l if x is not None]
            if len(c) < 2:
                all_data[sym] = {'error': 'insufficient data'}
                continue
            cur = c[-1]
            prev = c[-2] if len(c)>1 else cur
            chg1 = ((cur-prev)/prev*100) if prev else 0
            chg5 = ((c[-1]-c[-6])/c[-6]*100) if len(c)>=6 else 0
            
            m20 = ma(c, 20)
            m50 = ma(c, 50)
            m20d = ((cur-m20)/m20*100) if m20 else 0
            m50d = ((cur-m50)/m50*100) if m50 else 0
            
            sup, res = sr_levels(h, l, c)
            vr = vratio(v)
            
            all_data[sym] = {
                'price': cur, 'chg_1d': chg1, 'chg_5d': chg5,
                'ma20_dev': m20d, 'ma50_dev': m50d,
                'supports': sup, 'resistances': res,
                'vol_ratio': vr,
                'nearest_resistance': res[0] if res else None,
                'resistance_tests': 0,
            }
    
    # Generate and print report
    report = generate_report(all_data)
    print(report)

if __name__ == '__main__':
    main()
