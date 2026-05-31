import sys
import os
import time
from datetime import datetime
import json
from typing import Dict, Any, Optional

import types
dummy_api = types.ModuleType("capitalcom_paper")

def dummy_open(*args, **kwargs):
    deal_id = f"shadow_{int(time.time()*1000)}"
    print(f"  [SHADOW-API] 🟢 Fake Open (Deal: {deal_id})")
    return {"dealReference": deal_id, "dealId": deal_id, "status": "accepted"}

def dummy_close(*args, **kwargs):
    print(f"  [SHADOW-API] 🔴 Fake Close")
    return {"dealReference": args[0] if args else "unknown", "status": "closed"}

def dummy_update(*args, **kwargs):
    print(f"  [SHADOW-API] 🛡️ Fake Update SL/TP")
    return {"status": "updated"}

dummy_api.open_position = dummy_open
dummy_api.close_position = dummy_close
dummy_api.update_stop_loss = dummy_update
dummy_api.check_positions = lambda *args, **kwargs: []
dummy_api.get_account_balance = lambda *args, **kwargs: {"balance": 10000.0}
dummy_api.ACCOUNTS = {"crypto": "123", "indices": "456"}
dummy_api.get_account_for = lambda *args: "crypto"
dummy_api.get_market_info = lambda *args: {"status": "ok"}
dummy_api.MARKET_ACCOUNT = {"BTC": "crypto", "SPX": "indices"}
dummy_api.EPIC_MAP = {"BTC": "BITCOIN", "SPX": "US500"}
dummy_api._auth = lambda: None
dummy_api._headers = lambda *args: {}

sys.modules['capitalcom_paper'] = dummy_api

sys.path.append(os.path.join(os.path.expanduser("~"), "workspace", "scalp_engines"))
from engine_base import SwingEngine

class ShadowEngine(SwingEngine):
    def __init__(self, market_name: str, strategy_id: str):
        self.strategy_id = strategy_id
        self.SHADOW_DIR = os.path.join(os.path.expanduser("~"), "workspace", "shadow_lab", "data")
        os.makedirs(self.SHADOW_DIR, exist_ok=True)
        # MUST CALL SUPER FIRST to init default paths from base class
        super().__init__(market_name)
        # THEN OVERWRITE them
        self.trade_file = os.path.join(self.SHADOW_DIR, f"{strategy_id}_trades.json")
        self.signal_file = os.path.join(self.SHADOW_DIR, f"{strategy_id}_latest_signal.json")
        
    def _get_engine_param_map(self) -> dict:
        return {
            "BTC": {"epic": "BITCOIN", "atr_multiplier": 2.0},
            "ETH": {"epic": "ETHUSD", "atr_multiplier": 2.0},
            "SPX": {"epic": "US500", "atr_multiplier": 2.0},
        }.get(self.MARKET, {"epic": "UNKNOWN", "atr_multiplier": 2.0})
        
    def execute_signal(self, signal: Dict) -> Dict:
        res = super().execute_signal(signal)
        if self.trades:
            self.trades[-1]["shadow_tag"] = self.strategy_id
            with open(self.trade_file, "w") as f:
                json.dump(self.trades, f, indent=2)
        return res
