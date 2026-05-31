#!/usr/bin/env python3
"""
Lightweight News Sentiment Analyzer (VADER)
══════════════════════════════════════════
Since FinBERT was too heavy for this VM (timeout on model download),
we use VADER (Valence Aware Dictionary and sEntiment Reasoner) — 
a rule-based sentiment tool that's fast, lightweight, and works without GPU.

Reads macro_alerts.json headlines, scores each, outputs aggregated sentiment.

Usage:
  python3 sentiment_analyzer.py
Output:
  ~/workspace/sentiment_data/sentiment_latest.json
"""

import json, os, sys
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
HOME = os.path.expanduser("~")
WORKSPACE = os.path.join(HOME, "workspace")
MACRO_JSON = os.path.join(WORKSPACE, "macro_data", "macro_alerts.json")
OUTPUT_DIR = os.path.join(WORKSPACE, "sentiment_data")
OUTPUT_JSON = os.path.join(OUTPUT_DIR, "sentiment_latest.json")

# Try to import VADER (install if not present)
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
except ImportError:
    print("[sentiment] Installing vaderSentiment...", file=sys.stderr)
    import subprocess
    subprocess.run([
        os.path.join(HOME, ".hermes/hermes-agent/venv/bin/python3"),
        "-m", "pip", "install", "vaderSentiment", "-q"
    ], timeout=60)
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

def analyze():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Load headlines
    if not os.path.exists(MACRO_JSON):
        output = {"timestamp": datetime.now(HKT).isoformat(), "error": "No macro data",
                   "overall_score": 0, "headline_count": 0}
        with open(OUTPUT_JSON, 'w') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print("[sentiment] No macro_alerts.json found — empty output")
        return
    
    with open(MACRO_JSON) as f:
        macro = json.load(f)
    
    headlines = macro.get("headlines", macro.get("alerts", []))
    if not headlines:
        output = {"timestamp": datetime.now(HKT).isoformat(), "error": "No headlines",
                   "overall_score": 0, "headline_count": 0}
        with open(OUTPUT_JSON, 'w') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print("[sentiment] No headlines in macro data")
        return
    
    # Analyze each headline
    analyzer = SentimentIntensityAnalyzer()
    scored = []
    
    for h in headlines[:50]:  # Cap at 50 headlines
        title = h.get("title", h.get("headline", ""))
        if not title:
            continue
        
        scores = analyzer.polarity_scores(title)
        compound = scores["compound"]
        
        # Classify
        if compound >= 0.3:
            sentiment = "bullish"
        elif compound <= -0.3:
            sentiment = "bearish"
        else:
            sentiment = "neutral"
        
        scored.append({
            "title": title[:120],
            "compound": round(compound, 4),
            "sentiment": sentiment,
            "priority": h.get("priority", 50),
        })
    
    if not scored:
        output = {"timestamp": datetime.now(HKT).isoformat(), "error": "No scorable headlines",
                   "overall_score": 0, "headline_count": 0}
    else:
        # Aggregate
        avg_score = sum(s["compound"] for s in scored) / len(scored)
        bullish = [s for s in scored if s["sentiment"] == "bullish"]
        bearish = [s for s in scored if s["sentiment"] == "bearish"]
        
        output = {
            "timestamp": datetime.now(HKT).isoformat(),
            "headline_count": len(scored),
            "overall_score": round(avg_score, 4),
            "overall_sentiment": "bullish" if avg_score > 0.1 else ("bearish" if avg_score < -0.1 else "neutral"),
            "bullish_count": len(bullish),
            "bearish_count": len(bearish),
            "neutral_count": len(scored) - len(bullish) - len(bearish),
            "top_bullish": sorted(bullish, key=lambda s: -s["compound"])[:3],
            "top_bearish": sorted(bearish, key=lambda s: s["compound"])[:3],
            "model": "VADER (rule-based, lightweight)",
        }
    
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"[sentiment] Analyzed {len(scored)} headlines")
    print(f"[sentiment] Overall: {output.get('overall_sentiment', 'N/A')} (score: {output.get('overall_score', 0):.3f})")
    print(f"[sentiment] Saved to {OUTPUT_JSON}")

if __name__ == "__main__":
    analyze()
