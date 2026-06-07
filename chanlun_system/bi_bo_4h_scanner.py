#!/usr/bin/env python3
"""
Bi-Bo 4h Scanner — 中長線部署監控
===================================
Output structured JSON with:
- Signals (if any) → trade parameters
- Market context (always) → trend, zhongshu, divergence ratios
- "How close" analysis → distance to next potential signal
"""
import json, os, sys, urllib.request, io, contextlib
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(WORKSPACE, "core"))

from chanlun_phase1 import KLine, run_pipeline
from chanlun_phase2 import find_zhongshu_from_bi, merge_overlapping_zhongshu, detect_buy_sell_points

LABEL_MAP = {"buy1":"一買","buy2":"二買","buy3":"三買","sell1":"一賣","sell2":"二賣","sell3":"三賣"}
CONF_ICON = {"high":"🔥","medium":"✅","low":"⚠️"}

def fetch_btc(limit=500):
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=4h&limit={limit}"
    resp = urllib.request.urlopen(url, timeout=30)
    data = json.loads(resp.read())
    return [KLine(ts=k[0], dt=datetime.utcfromtimestamp(k[0]/1000).isoformat(),
                  open=float(k[1]), high=float(k[2]),
                  low=float(k[3]), close=float(k[4]), vol=float(k[5])) for k in data]


def analyze_proximity(bi_list, zs, points):
    """
    Analyze how close we are to each potential signal type.
    Returns proximity info for the LLM to interpret.
    """
    proximity = {}
    
    if len(bi_list) < 4:
        return {"error": f"Bi太少 ({len(bi_list)}), 無法分析"}
    
    # Last Bi
    last_bi = bi_list[-1]
    current_price = last_bi.end_price
    
    # ── 一買 proximity ──
    down_bis = [(i, b) for i, b in enumerate(bi_list) if b.direction == "down"]
    buy1_info = {"status": "none"}
    
    if len(down_bis) >= 2:
        b_prev, b_curr = down_bis[-2][1], down_bis[-1][1]
        div_ratio = b_curr.amplitude_pct / max(b_prev.amplitude_pct, 0.1)
        
        buy1_info["divergence_ratio"] = round(div_ratio, 2)
        buy1_info["prev_down_pct"] = round(b_prev.amplitude_pct, 1)
        buy1_info["curr_down_pct"] = round(b_curr.amplitude_pct, 1)
        buy1_info["prev_price_range"] = f"${b_prev.start_price:,.0f}→${b_prev.end_price:,.0f}"
        buy1_info["curr_price_range"] = f"${b_curr.start_price:,.0f}→${b_curr.end_price:,.0f}"
        
        if div_ratio < 0.90:
            # 一買 already triggered — check if it's been detected
            existing = [p for p in points if p.type == "buy1"]
            if existing:
                buy1_info["status"] = "triggered"
                buy1_info["signal_price"] = existing[-1].price
                buy1_info["signal_dt"] = existing[-1].dt[:10]
                buy1_info["confidence"] = existing[-1].confidence
            else:
                buy1_info["status"] = "should_exist"
        elif div_ratio < 1.0:
            # Close! Current down bi need to extend a bit more for divergence
            buy1_info["status"] = "close"
            # Need div_ratio < 0.90. Currently at X, so need curr bi amp to grow to at least prev_amp * 0.90
            target_amp = b_prev.amplitude_pct * 0.90
            # If curr down is still in progress, how much more does price need to drop?
            if last_bi.direction == "down":
                # For divergence, we need curr_amp to be LESS than target_amp
                # Actually divergence means the current down bi has LESS amplitude than previous
                # So we need: current down to finish (not continue extending)
                # The closer div_ratio is to 0.90 (from above), the closer to signal
                pct_from_signal = (div_ratio - 0.90) / 0.90 * 100
                buy1_info["pct_from_signal"] = round(pct_from_signal, 1)
                buy1_info["note"] = f"等下跌力度再減弱 {pct_from_signal:.0f}% 或出現上升筆確認"
            else:
                # Last bi is up — wait for a new down bi + divergence
                buy1_info["status"] = "waiting_for_down_bi"
                buy1_info["note"] = "目前上升中，等新一輪下跌出現背馳"
        else:
            # Accelerating down — not close
            buy1_info["status"] = "accelerating"
            buy1_info["note"] = "下跌加速中，一買遙遠"
    
    # ── 一賣 proximity ──
    up_bis = [(i, b) for i, b in enumerate(bi_list) if b.direction == "up"]
    sell1_info = {"status": "none"}
    
    if len(up_bis) >= 2:
        b_prev, b_curr = up_bis[-2][1], up_bis[-1][1]
        div_ratio = b_curr.amplitude_pct / max(b_prev.amplitude_pct, 0.1)
        
        sell1_info["divergence_ratio"] = round(div_ratio, 2)
        sell1_info["prev_up_pct"] = round(b_prev.amplitude_pct, 1)
        sell1_info["curr_up_pct"] = round(b_curr.amplitude_pct, 1)
        
        if div_ratio < 0.90:
            existing = [p for p in points if p.type == "sell1"]
            if existing:
                sell1_info["status"] = "triggered"
                sell1_info["signal_price"] = existing[-1].price
                sell1_info["signal_dt"] = existing[-1].dt[:10]
                sell1_info["confidence"] = existing[-1].confidence
            else:
                sell1_info["status"] = "should_exist"
        elif div_ratio < 1.0:
            sell1_info["status"] = "close"
            pct_from = (div_ratio - 0.90) / 0.90 * 100
            sell1_info["pct_from_signal"] = round(pct_from, 1)
        elif last_bi.direction == "up":
            sell1_info["status"] = "waiting_for_down_bi"
            sell1_info["note"] = "上升中，等升勢減弱"
        else:
            sell1_info["status"] = "accelerating"
    
    proximity["buy1"] = buy1_info
    proximity["sell1"] = sell1_info
    
    # ── 中樞分析 ──
    if zs:
        last_zs = zs[-1]
        zs_data = {
            "ZG": round(last_zs.ZG, 0),
            "ZD": round(last_zs.ZD, 0),
            "ZZ": round(last_zs.ZZ, 0),
            "direction": "上升中樞" if last_zs.direction == "up" else "下跌中樞",
            "width_pct": round(last_zs.width_pct, 1),
            "dt_range": last_zs.dt_range,
        }
        
        # Price position relative to zhongshu
        if current_price > last_zs.ZG:
            zs_data["price_position"] = f"高於中樞上軌 (ZG=${last_zs.ZG:.0f}) +{((current_price-last_zs.ZG)/last_zs.ZG*100):.1f}%"
        elif current_price < last_zs.ZD:
            zs_data["price_position"] = f"低於中樞下軌 (ZD=${last_zs.ZD:.0f}) -{((last_zs.ZD-current_price)/last_zs.ZD*100):.1f}%"
        else:
            zs_data["price_position"] = f"中樞區間內 (ZG=${last_zs.ZG:.0f} ZD=${last_zs.ZD:.0f})"
    else:
        zs_data = None
    
    proximity["zhongshu"] = zs_data
    
    # ── 三買/三賣 potential ──
    if zs:
        last_zs = zs[-1]
        if last_zs.direction == "up":
            # 三買需要：突破ZG後回調不破ZG
            dist_to_zg = current_price - last_zs.ZG
            proximity["buy3"] = {
                "distance_to_ZG": round(dist_to_zg, 0),
                "distance_pct": round(dist_to_zg / last_zs.ZG * 100, 1),
                "status": "close" if dist_to_zg > 0 and dist_to_zg / last_zs.ZG < 0.05 else "far"
            }
        else:
            dist_to_zd = last_zs.ZD - current_price
            proximity["sell3"] = {
                "distance_to_ZD": round(dist_to_zd, 0),
                "distance_pct": round(dist_to_zd / last_zs.ZD * 100, 1),
                "status": "close" if dist_to_zd > 0 and dist_to_zd / last_zs.ZD < 0.05 else "far"
            }
    
    return proximity


def scan():
    """Main scan — outputs structured JSON to stdout"""
    now = datetime.now(HKT)
    
    # Fetch + run pipeline silently
    klines = fetch_btc(500)
    klines_f = [k for k in klines if k.dt >= "2026-03-01"]
    
    with contextlib.redirect_stdout(io.StringIO()):
        processed, fx, bi_list = run_pipeline(klines_f, min_kline=5, min_amp=1.3)
    
    zs_raw = find_zhongshu_from_bi(bi_list)
    zs = merge_overlapping_zhongshu(zs_raw)
    points = detect_buy_sell_points(bi_list, zs)
    
    current_price = klines[-1].close
    prev_close_4h = klines[-2].close if len(klines) >= 2 else current_price
    price_change_4h = (current_price - prev_close_4h) / prev_close_4h * 100
    
    # Current Bi status
    last_bi = bi_list[-1] if bi_list else None
    bi_direction = ""
    if last_bi:
        bi_direction = "⬆️上升" if last_bi.direction == "up" else "⬇️下跌"
    
    # All Bi summary (last 8)
    bi_summary = []
    for i, b in enumerate(bi_list[-8:]):
        bi_summary.append({
            "idx": i + len(bi_list) - min(8, len(bi_list)),
            "dir": "↑" if b.direction == "up" else "↓",
            "amp": round(b.amplitude_pct, 1),
            "range": f"${b.start_price:,.0f}→${b.end_price:,.0f}",
            "start": b.start_dt[:10],
            "end": b.end_dt[:10],
        })
    
    # Signals
    signal_list = []
    for p in points[-5:]:
        signal_list.append({
            "type": LABEL_MAP.get(p.type, p.type),
            "type_code": p.type,
            "price": round(p.price, 0),
            "dt": p.dt[:10],
            "confidence": p.confidence,
            "confidence_icon": CONF_ICON.get(p.confidence, "?"),
            "reason": p.reason,
        })
    
    # Divergence ratios — orthodox Chanlun: completed Bi vs completed Bi only
    divergence = {}
    down_bis = [(i, b) for i, b in enumerate(bi_list) if b.direction == "down"]
    up_bis = [(i, b) for i, b in enumerate(bi_list) if b.direction == "up"]
    
    if len(down_bis) >= 2:
        amp_curr = down_bis[-1][1].amplitude_pct
        amp_prev = down_bis[-2][1].amplitude_pct
        div = amp_curr / max(amp_prev, 0.1)
        divergence["down_ratio"] = round(div, 2)
        divergence["down_status"] = "背馳" if div < 0.9 else ("接近" if div < 1.0 else "加速")
        divergence["down_bi_amp"] = round(amp_curr, 1)
        divergence["down_prev_amp"] = round(amp_prev, 1)
    
    if len(up_bis) >= 2:
        amp_curr = up_bis[-1][1].amplitude_pct
        amp_prev = up_bis[-2][1].amplitude_pct
        div = amp_curr / max(amp_prev, 0.1)
        divergence["up_ratio"] = round(div, 2)
        divergence["up_status"] = "背馳" if div < 0.9 else ("接近" if div < 1.0 else "加速")
        divergence["up_bi_amp"] = round(amp_curr, 1)
        divergence["up_prev_amp"] = round(amp_prev, 1)
    
    # ── Ongoing movement tracker (separate from orthodox ratio) ──
    ongoing = {}
    if last_bi:
        if last_bi.direction == "down" and current_price < last_bi.end_price:
            pct = round(abs((last_bi.end_price - current_price) / last_bi.end_price * 100), 1)
            ongoing["direction"] = "down"
            ongoing["since_end"] = f"${last_bi.end_price:,.0f}→${current_price:,.0f}"
            ongoing["pct"] = pct
            ongoing["note"] = f"Bi{len(bi_list)} 確認後再跌 {pct}%，等緊新底分型"
        elif last_bi.direction == "up" and current_price > last_bi.end_price:
            pct = round(abs((current_price - last_bi.end_price) / last_bi.end_price * 100), 1)
            ongoing["direction"] = "up"
            ongoing["since_end"] = f"${last_bi.end_price:,.0f}→${current_price:,.0f}"
            ongoing["pct"] = pct
            ongoing["note"] = f"Bi{len(bi_list)} 確認後再升 {pct}%，等緊新頂分型"
        elif last_bi.direction == "down" and current_price > last_bi.end_price:
            pct = round(abs((current_price - last_bi.end_price) / last_bi.end_price * 100), 1)
            ongoing["direction"] = "rebound"
            ongoing["since_end"] = f"${last_bi.end_price:,.0f}→${current_price:,.0f}"
            ongoing["pct"] = pct
            ongoing["note"] = f"低位反彈 +{pct}%，觀察會否形成上升筆"
        elif last_bi.direction == "up" and current_price < last_bi.end_price:
            pct = round(abs((last_bi.end_price - current_price) / last_bi.end_price * 100), 1)
            ongoing["direction"] = "pullback"
            ongoing["since_end"] = f"${last_bi.end_price:,.0f}→${current_price:,.0f}"
            ongoing["pct"] = pct
            ongoing["note"] = f"高位回調 -{pct}%，觀察會否形成下跌筆"
    
    # Proximity analysis
    proximity = analyze_proximity(bi_list, zs, points)
    
    # Alt errors
    alt_errors = sum(1 for i in range(1, len(bi_list)) if bi_list[i].direction == bi_list[i-1].direction)
    
    output = {
        "scan_time": now.strftime("%Y-%m-%d %H:%M HKT"),
        "current_price": round(current_price, 0),
        "price_change_4h_pct": round(price_change_4h, 2),
        "data_period": f"{klines[0].dt[:10]} → {klines[-1].dt[:10]}",
        "bi_count": len(bi_list),
        "alt_errors": alt_errors,
        "zhongshu_count": len(zs),
        "signal_count": len(points),
        "current_bi": {
            "direction": bi_direction,
            "start_price": round(last_bi.start_price, 0) if last_bi else 0,
            "end_price": round(last_bi.end_price, 0) if last_bi else 0,
            "amplitude_pct": round(last_bi.amplitude_pct, 1) if last_bi else 0,
            "start_dt": last_bi.start_dt[:10] if last_bi else "",
            "end_dt": last_bi.end_dt[:10] if last_bi else "",
        } if last_bi else None,
        "bi_structure": bi_summary,
        "zhongshu": [
            {
                "dir": "↗上升" if z.direction == "up" else "↘下跌",
                "ZG": round(z.ZG, 0),
                "ZD": round(z.ZD, 0),
                "ZZ": round(z.ZZ, 0),
                "width_pct": round(z.width_pct, 1),
                "dt_range": z.dt_range,
            }
            for z in zs[-3:]
        ] if zs else [],
        "signals": signal_list,
        "new_signals": [s for s in signal_list if s["dt"] == bi_list[-1].end_dt[:10] if last_bi],
        "divergence": divergence,
        "ongoing": ongoing,
        "proximity": proximity,
    }
    
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return output


if __name__ == "__main__":
    scan()
