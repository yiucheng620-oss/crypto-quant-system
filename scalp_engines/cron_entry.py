#!/usr/bin/env python3
"""
⏰ CRON ENTRY POINT V2 — 唯一 cron 入口點
===========================================
全部 5 個 engine 獨立掃描，每 5 分鐘一次。
用 SingletonLock 避免 race condition。

Engine list:
  btc      — OI Delta + ATR SL
  eth      — ETH/BTC ratio + OI confirm
  gold     — VIX/DXY regime
  indices  — VIX momentum + SPX/NDX
  equities — stock selection (dynamic screener)

排程: 全部每 5 分鐘，零分別。
"""

import os
import sys
import time
import signal
import shutil
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))

# ── Singleton Lock ──────────────────────────────────────────────────────
LOCK_DIR = "/tmp/trading_system.lock"

class SingletonLock:
    """Atomic 目錄鎖，timeout=0 即 fail-fast。"""

    def __init__(self, timeout: int = 0):
        self.timeout = timeout
        self.lock_dir = LOCK_DIR

    def __enter__(self):
        try:
            os.mkdir(self.lock_dir)
        except FileExistsError:
            # Stale lock detection: if lock dir > 5 min old, assume dead process
            try:
                lock_age = time.time() - os.path.getmtime(self.lock_dir)
                if lock_age > 300:  # 5 minutes (scan timeout = 4 min)
                    print(f"⚠️ [{datetime.now(HKT).strftime('%H:%M:%S')}] "
                          f"Stale lock detected ({lock_age:.0f}s old) — auto-clearing")
                    os.rmdir(self.lock_dir)
                    os.mkdir(self.lock_dir)
                    return self
            except OSError:
                pass
            print(f"⚠️ [{datetime.now(HKT).strftime('%H:%M:%S')}] "
                  f"Previous cron still running. Exiting 0.")
            sys.exit(0)
        # Write PID to lock dir for cross-check
        try:
            with open(os.path.join(self.lock_dir, "pid"), "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            shutil.rmtree(self.lock_dir)
        except OSError:
            pass


# ── Main ────────────────────────────────────────────────────────────────

def main():
    with SingletonLock():
        workspace = os.path.expanduser("~/workspace")
        engines_dir = os.path.join(workspace, "scalp_engines")
        sys.path.insert(0, workspace)
        sys.path.insert(0, engines_dir)
        sys.path.insert(0, os.path.join(workspace, "capitalcom"))

        try:
            from capitalcom_paper import _auth
            _auth()
        except Exception as e:
            print(f"  ⚠️ Capital.com auth failed (non-fatal): {e}")

        from engine_coordinator import EngineCoordinator
        coord = EngineCoordinator()

        now = datetime.now(HKT)
        ts = now.strftime("%Y-%m-%d %H:%M HKT")
        
        # ⛔ Weekend gate: let engines decide individually
        # Crypto engines (BTC/ETH) have WEEKEND_TRADE_ENABLED=True for 24/7 trailing + exits
        # Traditional engines (Gold/Indices/Equities) skip via their own engine_base check
        weekday = now.weekday()  # 0=Mon, 5=Sat, 6=Sun
        is_weekend = weekday >= 5
        if is_weekend:
            print(f"📅 [{ts}] WEEKEND — crypto engines only (BTC/ETH run 24/7, others skip)")
        else:
            print(f"⚡ [{ts}] CRON: FULL SCAN — all 5 engines")

        # 4-min timeout to prevent lock storms
        class TimeoutError_(Exception):
            pass

        def _handler(signum, frame):
            raise TimeoutError_("Scan timed out after 4 minutes")

        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(240)  # 4 minutes
        try:
            coord.run_scan()
        except TimeoutError_:
            print(f"⛔ [{ts}] CRON: Scan timed out (>4min), forcing exit to release lock")
        except Exception as e:
            print(f"⛔ [{ts}] CRON: Scan failed: {e}")
            raise
        finally:
            signal.alarm(0)  # Cancel alarm


if __name__ == "__main__":
    main()
