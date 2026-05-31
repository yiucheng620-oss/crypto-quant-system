#!/usr/bin/env python3
"""
🧬 GA Strategy Optimizer — Genetic Algorithm for Strategy Parameter Tuning
==========================================================================

Reads strategy_config.json, evolves parameters using GA, outputs optimized config.

Usage:
  python3 ga_optimizer.py --engine btc --generations 20 --population 30
  python3 ga_optimizer.py --engine all --generations 10 --population 20 --dry-run

Architecture:
  1. Load strategy_config.json → extract GA search space + default values
  2. For each generation:
     a. Generate population (random samples within parameter ranges)
     b. Score fitness (call fitness function — pluggable: backtest / paper trade / mock)
     c. Select top performers (tournament selection)
     d. Crossover + mutate → next generation
  3. Output best config → strategy_config.json (--apply flag) or stdout
"""

import json
import os
import sys
import copy
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Any, Optional

HKT = timezone(timedelta(hours=8))
WORKSPACE = os.path.expanduser("~/workspace")
CONFIG_PATH = os.path.join(WORKSPACE, "scalp_lab", "engines", "strategy_config.json")


# ==================== CONFIG LOADING ====================

def load_search_space(engine_name: str = None) -> Tuple[Dict, Dict]:
    """Load strategy config + extract GA search space.
    
    Returns: (config, search_params) where search_params = {param_name: {type, min, max, step}}
    """
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)
    
    search_space = cfg.get("_ga_search_space", {})
    shared = search_space.get("shared", {})
    
    # Merge shared + engine-specific
    params = dict(shared)
    if engine_name and engine_name != "all":
        engine_specific = search_space.get(f"{engine_name}_specific", {})
        params.update(engine_specific)
    
    # Filter to ga_searchable only
    searchable = {k: v for k, v in params.items() if v.get("ga_searchable", False)}
    
    return cfg, searchable


def get_default_params(engine_name: str) -> Dict:
    """Extract default parameter values from config for a given engine."""
    cfg, _ = load_search_space(engine_name)
    engines = cfg.get("engines", {})
    if engine_name == "all":
        defaults = {}
        for eng_name in engines:
            defaults[eng_name] = engines[eng_name]
        return defaults
    return engines.get(engine_name, {})


# ==================== POPULATION ====================

def random_individual(search_space: Dict) -> Dict:
    """Generate one random individual within parameter ranges."""
    ind = {}
    for name, spec in search_space.items():
        ptype = spec["type"]
        pmin = spec["min"]
        pmax = spec["max"]
        step = spec.get("step", 1)
        
        if ptype == "int":
            n_steps = int((pmax - pmin) / step) + 1
            ind[name] = pmin + random.randint(0, n_steps - 1) * step
        elif ptype == "float":
            n_steps = int((pmax - pmin) / step)
            ind[name] = round(pmin + random.randint(0, n_steps) * step, 6)
        elif ptype == "bool":
            ind[name] = random.choice([True, False])
    
    return ind


def create_population(search_space: Dict, size: int) -> List[Dict]:
    """Create initial random population."""
    return [random_individual(search_space) for _ in range(size)]


# ==================== GENETIC OPERATORS ====================

def tournament_select(population: List[Dict], fitnesses: List[float], 
                      tournament_size: int = 3) -> Dict:
    """Tournament selection: pick K random, return best."""
    selected_idx = [random.randint(0, len(population) - 1) for _ in range(tournament_size)]
    best_idx = max(selected_idx, key=lambda i: fitnesses[i])
    return copy.deepcopy(population[best_idx])


def uniform_crossover(parent_a: Dict, parent_b: Dict, 
                      crossover_rate: float = 0.5) -> Tuple[Dict, Dict]:
    """Uniform crossover: each gene randomly from A or B."""
    child_a, child_b = {}, {}
    for key in parent_a:
        if random.random() < crossover_rate:
            child_a[key] = parent_a[key]
            child_b[key] = parent_b[key]
        else:
            child_a[key] = parent_b[key]
            child_b[key] = parent_a[key]
    return child_a, child_b


def gaussian_mutate(individual: Dict, search_space: Dict, 
                    mutation_rate: float = 0.1, mutation_strength: float = 0.1) -> Dict:
    """Gaussian mutation: add noise within parameter range, then snap to step grid."""
    mutated = copy.deepcopy(individual)
    
    for name, spec in search_space.items():
        if random.random() >= mutation_rate:
            continue
        
        ptype = spec["type"]
        pmin = spec["min"]
        pmax = spec["max"]
        step = spec.get("step", 1)
        
        if ptype == "bool":
            mutated[name] = not mutated[name]
            continue
        
        # Gaussian noise proportional to range
        noise = random.gauss(0, (pmax - pmin) * mutation_strength)
        new_val = mutated[name] + noise
        
        # Clamp to range
        new_val = max(pmin, min(pmax, new_val))
        
        # Snap to step grid
        if ptype == "float":
            new_val = round(round(new_val / step) * step, 6)
        else:
            new_val = int(round(new_val / step) * step)
        
        mutated[name] = new_val
    
    return mutated


# ==================== FITNESS ====================

def mock_fitness_fn(individual: Dict, engine_name: str) -> float:
    """Mock fitness: simulate a 30-day backtest with random-like metrics.
    
    In production, replace with actual backtest call.
    Formula: penalize extreme values, reward moderate settings.
    """
    score = 0.0
    
    # Simulate: moderate SL/TP ratios are better
    if "stop_loss_pct" in individual and "take_profit_pct" in individual:
        rrr = individual["take_profit_pct"] / max(individual["stop_loss_pct"], 0.001)
        if 1.5 <= rrr <= 4.0:
            score += 0.3
        else:
            score -= 0.1
    
    # Simulate: moderate trailing activation is better
    if "trailing_activation_pct" in individual:
        ta = individual["trailing_activation_pct"]
        if 0.01 <= ta <= 0.03:
            score += 0.2
        else:
            score -= 0.1
    
    # Simulate: moderate risk is better
    if "risk_per_trade_pct" in individual:
        r = individual["risk_per_trade_pct"]
        if 0.01 <= r <= 0.03:
            score += 0.15
        elif r < 0.01:
            score -= 0.05
        else:
            score -= 0.1
    
    # Simulate: reasonable time stop
    if "time_stop_minutes" in individual:
        t = individual["time_stop_minutes"]
        if engine_name in ("btc", "eth") and 120 <= t <= 360:
            score += 0.1
        elif engine_name == "gold" and 60 <= t <= 240:
            score += 0.1
        elif engine_name == "equities" and t >= 480:
            score += 0.1
    
    # Add noise to simulate real-world variation
    score += random.gauss(0, 0.15)
    
    return max(0.0, score)


def paper_trade_fitness_fn(individual: Dict, engine_name: str) -> float:
    """
    REAL fitness: read engine's paper trade history and calculate performance.
    
    Reads: scalp_lab/engines/{engine}_trades.json (or {engine_name}_trades.json)
    Calculates: composite score from Sharpe-like ratio + win rate + profit factor.
    
    Returns 0.0 if < MIN_CLOSED_TRADES (not enough data to judge).
    """
    import math
    
    MIN_CLOSED_TRADES = 5  # Need at least 5 closed trades for meaningful score
    LOG_DIR = os.path.join(WORKSPACE, "scalp_lab", "engines")
    
    # Map engine_name to trade file name
    name_map = {
        "btc": "btc_swing", "eth": "eth_swing", 
        "gold": "GOLD", "indices": "indices", "equities": "equities"
    }
    file_name = name_map.get(engine_name, engine_name)
    
    # Try multiple filename patterns
    trade_files = [
        os.path.join(LOG_DIR, f"{file_name}_trades.json"),
        os.path.join(LOG_DIR, f"{engine_name}_trades.json"),
    ]
    
    trades = []
    for tf in trade_files:
        if os.path.exists(tf):
            try:
                with open(tf) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    trades = data
                    break
            except (json.JSONDecodeError, Exception):
                continue
    
    # Filter closed trades only
    closed = [t for t in trades if t.get("status") == "closed"]
    
    if len(closed) < MIN_CLOSED_TRADES:
        return 0.0  # Not enough data
    
    # Extract P&L values (try multiple field names)
    pnls = []
    for t in closed:
        pnl = t.get("pnl") or t.get("raw_pnl") or t.get("pnl_amt") or 0
        try:
            pnls.append(float(pnl))
        except (ValueError, TypeError):
            pnls.append(0.0)
    
    if not pnls:
        return 0.0
    
    # Metrics
    total_pnl = sum(pnls)
    avg_pnl = total_pnl / len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls) if pnls else 0
    total_wins = sum(wins) if wins else 0
    total_losses = abs(sum(losses)) if losses else 0.01  # avoid div by zero
    profit_factor = total_wins / total_losses if total_losses > 0 else 10.0
    
    # Sharpe-like: mean / std (annualized approximation)
    if len(pnls) >= 3:
        mean_pnl = sum(pnls) / len(pnls)
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std_pnl = math.sqrt(variance) if variance > 0 else 0.01
        sharpe_like = mean_pnl / std_pnl
    else:
        sharpe_like = 0
    
    # Composite fitness score
    # Weight: 40% Sharpe + 30% profit factor + 20% win rate + 10% total P&L
    sharpe_score = min(max(sharpe_like, -2), 5) * 0.15  # scale to ~0-0.75
    pf_score = min(profit_factor, 5) * 0.12  # scale to ~0-0.6
    wr_score = win_rate * 0.3  # 0-0.3
    pnl_score = min(max(total_pnl / 100, -0.5), 1.5) * 0.1  # scale to ~0-0.15
    
    fitness = sharpe_score + pf_score + wr_score + pnl_score
    
    return round(max(0.0, fitness), 4)


def backtest_fitness_fn(individual: Dict, engine_name: str) -> float:
    """
    PRODUCTION fitness: try paper trade data first, fall back to mock.
    
    Priority:
      1. paper_trade_fitness_fn — real P&L from live paper trading
      2. mock_fitness_fn — heuristic fallback when no data
    """
    real_score = paper_trade_fitness_fn(individual, engine_name)
    if real_score > 0:
        return real_score
    return mock_fitness_fn(individual, engine_name)


def evaluate_population(population: List[Dict], engine_name: str,
                       fitness_fn: callable = None) -> List[float]:
    """Score entire population. Uses mock fitness by default."""
    fn = fitness_fn or mock_fitness_fn
    return [fn(ind, engine_name) for ind in population]


# ==================== GA MAIN LOOP ====================

def run_ga(engine_name: str,
           generations: int = 20,
           population_size: int = 30,
           elite_count: int = 3,
           mutation_rate: float = 0.15,
           mutation_strength: float = 0.1,
           crossover_rate: float = 0.5,
           fitness_fn: callable = None,
           verbose: bool = True) -> Dict:
    """Run full GA optimization for one engine.
    
    Returns: {
        "best_individual": dict,
        "best_fitness": float,
        "evolution_log": [...],
        "runtime_seconds": float,
    }
    """
    cfg, search_space = load_search_space(engine_name)
    
    if not search_space:
        return {"error": f"No searchable parameters for engine '{engine_name}'"}
    
    start_time = time.time()
    population = create_population(search_space, population_size)
    best_overall = None
    best_fitness_overall = -float("inf")
    evolution_log = []
    
    for gen in range(generations):
        # Evaluate
        fitnesses = evaluate_population(population, engine_name, fitness_fn)
        
        # Track best
        best_idx = max(range(len(fitnesses)), key=lambda i: fitnesses[i])
        gen_best_fitness = fitnesses[best_idx]
        gen_best = population[best_idx]
        gen_avg = sum(fitnesses) / len(fitnesses)
        gen_worst = min(fitnesses)
        
        if gen_best_fitness > best_fitness_overall:
            best_fitness_overall = gen_best_fitness
            best_overall = copy.deepcopy(gen_best)
        
        gen_log = {
            "generation": gen + 1,
            "best_fitness": round(gen_best_fitness, 4),
            "avg_fitness": round(gen_avg, 4),
            "worst_fitness": round(gen_worst, 4),
        }
        evolution_log.append(gen_log)
        
        if verbose:
            print(f"  Gen {gen+1:3d}/{generations} | "
                  f"Best: {gen_best_fitness:.4f} | Avg: {gen_avg:.4f}")
        
        # Elitism: keep best N
        elite = [copy.deepcopy(population[i]) for i in sorted(
            range(len(fitnesses)), key=lambda i: fitnesses[i], reverse=True
        )[:elite_count]]
        
        # Create next generation
        next_pop = list(elite)
        
        while len(next_pop) < population_size:
            parent_a = tournament_select(population, fitnesses, 3)
            parent_b = tournament_select(population, fitnesses, 3)
            child_a, child_b = uniform_crossover(parent_a, parent_b, crossover_rate)
            child_a = gaussian_mutate(child_a, search_space, mutation_rate, mutation_strength)
            child_b = gaussian_mutate(child_b, search_space, mutation_rate, mutation_strength)
            next_pop.append(child_a)
            if len(next_pop) < population_size:
                next_pop.append(child_b)
        
        population = next_pop[:population_size]
    
    runtime = round(time.time() - start_time, 1)
    
    return {
        "engine": engine_name,
        "best_individual": best_overall,
        "best_fitness": round(best_fitness_overall, 4),
        "generations": generations,
        "population_size": population_size,
        "evolution_log": evolution_log,
        "runtime_seconds": runtime,
    }


def run_all_engines(**kwargs) -> Dict[str, Dict]:
    """Run GA for all 5 engines."""
    results = {}
    for eng in ["btc", "eth", "gold", "indices", "equities"]:
        print(f"\n🧬 {eng.upper()} Engine")
        results[eng] = run_ga(eng, **kwargs)
    return results


def apply_best_to_config(results: Dict[str, Dict], dry_run: bool = True) -> bool:
    """Write best GA results into strategy_config.json.
    
    Args:
        results: output from run_ga() or run_all_engines()
        dry_run: if True, print diff only, don't write
    
    Returns: True if config was updated
    """
    if "engine" in results:
        results = {results["engine"]: results}
    
    if dry_run:
        print("\n📋 DRY RUN — would apply these changes:")
    
    cfg, _ = load_search_space()
    updated = False
    
    for eng_name, result in results.items():
        if "error" in result:
            print(f"  ⚠️  {eng_name}: {result['error']} — skipping")
            continue
        
        best = result.get("best_individual", {})
        if not best:
            continue
        
        engine_cfg = cfg["engines"].get(eng_name, {})
        changes = []
        for param, value in best.items():
            old_val = engine_cfg.get(param)
            if old_val is not None and old_val != value:
                changes.append(f"{param}: {old_val} → {value}")
                engine_cfg[param] = value
        
        if changes:
            updated = True
            if dry_run:
                print(f"  📂 {eng_name}:")
                for c in changes:
                    print(f"    {c}")
            else:
                cfg["engines"][eng_name] = engine_cfg
    
    if updated and not dry_run:
        # Atomic write
        cfg["_meta"]["updated"] = datetime.now(HKT).isoformat()
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
        print(f"✅ Config updated: {CONFIG_PATH}")
    
    return updated


# ==================== CLI ====================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="🧬 GA Strategy Optimizer")
    parser.add_argument("--engine", default="btc", 
                       choices=["btc", "eth", "gold", "indices", "equities", "all"],
                       help="Which engine to optimize")
    parser.add_argument("--generations", type=int, default=20, 
                       help="Number of generations")
    parser.add_argument("--population", type=int, default=30,
                       help="Population size")
    parser.add_argument("--mutation-rate", type=float, default=0.15,
                       help="Probability of mutation per gene")
    parser.add_argument("--mutation-strength", type=float, default=0.1,
                       help="Mutation noise relative to parameter range")
    parser.add_argument("--elite", type=int, default=3,
                       help="Number of elite individuals to preserve")
    parser.add_argument("--apply", action="store_true",
                       help="Apply best config to strategy_config.json")
    parser.add_argument("--dry-run", action="store_true", default=True,
                       help="Dry run (no file write) — default")
    
    args = parser.parse_args()
    
    print(f"🧬 GA Strategy Optimizer")
    print(f"   Engine: {args.engine}")
    print(f"   Generations: {args.generations} | Population: {args.population}")
    print(f"   Mutation rate: {args.mutation_rate} | Elite: {args.elite}")
    print()
    
    kwargs = {
        "generations": args.generations,
        "population_size": args.population,
        "elite_count": args.elite,
        "mutation_rate": args.mutation_rate,
        "mutation_strength": args.mutation_strength,
    }
    
    if args.engine == "all":
        results = run_all_engines(**kwargs)
    else:
        result = run_ga(args.engine, **kwargs)
        results = {args.engine: result}
    
    # Print best
    print("\n" + "=" * 60)
    print("🏆 BEST INDIVIDUALS")
    print("=" * 60)
    for eng, res in results.items():
        if "error" in res:
            print(f"  ❌ {eng}: {res['error']}")
            continue
        print(f"\n  📂 {eng} (fitness={res['best_fitness']}, {res['runtime_seconds']}s)")
        for param, val in sorted(res["best_individual"].items()):
            print(f"    {param:35s} = {val}")
    
    # Apply if requested
    if args.apply:
        apply_best_to_config(results, dry_run=False)
    elif args.dry_run:
        apply_best_to_config(results, dry_run=True)
