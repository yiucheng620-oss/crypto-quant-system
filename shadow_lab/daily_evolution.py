import os
import sys
from evolution_factory import run_evolution_pipeline

if __name__ == "__main__":
    # We will just run it for BTC and INDICES
    run_evolution_pipeline("BTC")
    print("\n" + "="*50 + "\n")
    run_evolution_pipeline("INDICES")
