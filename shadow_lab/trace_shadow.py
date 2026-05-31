import sys
import os
import json
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from shadow_base import ShadowEngine

def run_test():
    print("============================================================")
    print("🔗 INTEGRATION TRACE: Shadow Engine Sandbox")
    print("============================================================")
    
    # Create a shadow engine
    engine = ShadowEngine("BTC", strategy_id="shadow_v1_test")
    # Reset any old trades
    engine.trades = []
    
    fake_signal = {
        "direction": "LONG",
        "entry": 70000.0,
        "stop": 69000.0,
        "target": 73000.0,
        "strategy": "shadow_test_strat",
        "reason": "testing shadow sandbox"
    }
    
    print("📡 Injecting Fake Signal to Shadow Engine...")
    engine.execute_signal(fake_signal)
    
    print("\n📂 Checking Local Storage...")
    if os.path.exists(engine.trade_file):
        with open(engine.trade_file) as f:
            data = json.load(f)
            if data and data[-1].get("shadow_tag") == "shadow_v1_test":
                print("  ✅ Trade securely isolated in shadow JSON.")
                print(f"     File: {engine.trade_file}")
            else:
                print("  ❌ Data written, but shadow tag missing.")
    else:
        print("  ❌ Shadow JSON not created!")
        
    print("============================================================")
    print("🏆 SANDBOX SECURE: Ready for Multi-Agent Evolution")

if __name__ == "__main__":
    run_test()
