"""
Auto-Tune Optimizer — Grid Search + Background Execution
=========================================================
Generates parameter grids for Z-Score, Delta Ratio, OFI, and SL_ATR,
runs each combination through the Replay Engine, and ranks results
by the chosen objective function.

Runs in background via asyncio task with progress tracking.
"""

import asyncio
import itertools
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from services.replay_engine import run_replay, merge_config

logger = logging.getLogger(__name__)


# ── In-memory state for active optimization ──
_active_optimization: Optional[Dict] = None
_optimization_lock = asyncio.Lock()


def generate_grid(grid_config: Dict) -> List[Dict]:
    """Generate all parameter combinations from ranges.

    grid_config example:
    {
        "zscore_min": {"min": 0.5, "max": 1.5, "steps": 5},
        "delta_ratio_min": {"min": 0.10, "max": 0.30, "steps": 5},
        "ofi_threshold": {"min": 0.1, "max": 0.4, "steps": 4},
        "sl_atr_mult": {"min": 0.5, "max": 1.5, "steps": 4},
        "regime": "TRANSICAO",
    }
    """
    regime = grid_config.get("regime", "TRANSICAO")

    def linspace(cfg):
        mn, mx, steps = cfg["min"], cfg["max"], cfg["steps"]
        if steps <= 1:
            return [mn]
        step_size = (mx - mn) / (steps - 1)
        return [round(mn + i * step_size, 4) for i in range(steps)]

    z_values = linspace(grid_config.get("zscore_min", {"min": 0.5, "max": 1.5, "steps": 5}))
    dr_values = linspace(grid_config.get("delta_ratio_min", {"min": 0.10, "max": 0.30, "steps": 5}))
    ofi_values = linspace(grid_config.get("ofi_threshold", {"min": 0.1, "max": 0.4, "steps": 4}))
    sl_values = linspace(grid_config.get("sl_atr_mult", {"min": 0.5, "max": 1.5, "steps": 4}))

    combos = list(itertools.product(z_values, dr_values, ofi_values, sl_values))
    return [
        {"zscore": z, "delta_ratio": dr, "ofi": ofi, "sl_atr": sl, "regime": regime}
        for z, dr, ofi, sl in combos
    ]


def _build_replay_config(combo: Dict, base_config: Dict) -> Dict:
    """Convert a grid combo into a full replay config."""
    regime = combo["regime"]
    config = {**base_config}
    config["signal_mode"] = "DYNAMIC"

    # Apply per-regime thresholds (keep other regimes at base values)
    for key, combo_key in [
        ("zscore_min", "zscore"),
        ("delta_ratio_min", "delta_ratio"),
        ("ofi_threshold", "ofi"),
    ]:
        current = config.get(key, {})
        if isinstance(current, dict):
            config[key] = {**current, regime: combo[combo_key]}
        else:
            config[key] = {regime: combo[combo_key]}

    # SL ATR mult — apply to the archetype mapped from regime
    arch_map = {
        "COMPLACENCIA": "TREND", "BULL": "TREND", "BEAR": "TREND",
        "TRANSICAO": "RANGE", "CAPITULACAO": "FADE",
    }
    archetype = arch_map.get(regime, "RANGE")
    sl_current = config.get("sl_atr_mult", {})
    if isinstance(sl_current, dict):
        config["sl_atr_mult"] = {**sl_current, archetype: combo["sl_atr"]}

    return config


def _extract_objective(metrics: Dict, objective: str) -> float:
    mapping = {
        "sharpe": "sharpe_ratio",
        "sortino": "sortino_ratio",
        "profit_factor": "profit_factor",
        "net_pnl": "total_pnl",
        "min_drawdown": "max_drawdown",
        "expectancy": "expectancy",
    }
    key = mapping.get(objective, "sharpe_ratio")
    return metrics.get(key, 0)


async def run_optimization(database, optimization_id: str, grid: List[Dict],
                           base_config: Dict, objective: str) -> None:
    """Run all grid combinations in background. Updates _active_optimization state."""
    global _active_optimization

    total = len(grid)
    results = []
    best_obj = float('-inf') if objective != "min_drawdown" else float('inf')
    best_params = None

    for i, combo in enumerate(grid):
        # Check cancellation
        if _active_optimization and _active_optimization.get("cancelled"):
            _active_optimization["status"] = "cancelled"
            _active_optimization["finished_at"] = datetime.now(timezone.utc).isoformat()
            return

        # Update progress
        if _active_optimization:
            _active_optimization["progress"] = {
                "current": i + 1,
                "total": total,
                "pct": round((i + 1) / total * 100, 1),
                "best_objective": round(best_obj, 4) if best_obj != float('-inf') and best_obj != float('inf') else 0,
                "best_params": best_params,
            }

        config = _build_replay_config(combo, base_config)
        result = await run_replay(database, config)

        obj_value = _extract_objective(result["metrics"], objective)
        is_better = (
            (objective != "min_drawdown" and obj_value > best_obj) or
            (objective == "min_drawdown" and obj_value < best_obj)
        )
        if is_better and result["metrics"]["total_trades"] > 0:
            best_obj = obj_value
            best_params = combo

        results.append({
            "combo": combo,
            "objective_value": round(obj_value, 4),
            "metrics": {
                "total_trades": result["metrics"]["total_trades"],
                "win_rate": result["metrics"]["win_rate"],
                "total_pnl": result["metrics"]["total_pnl"],
                "sharpe_ratio": result["metrics"]["sharpe_ratio"],
                "sortino_ratio": result["metrics"]["sortino_ratio"],
                "profit_factor": result["metrics"]["profit_factor"],
                "max_drawdown": result["metrics"]["max_drawdown"],
                "max_drawdown_pct": result["metrics"]["max_drawdown_pct"],
                "expectancy": result["metrics"]["expectancy"],
                "return_pct": result["metrics"]["return_pct"],
            },
        })

        # Yield control periodically
        if (i + 1) % 10 == 0:
            await asyncio.sleep(0)

    # Sort results
    reverse = objective != "min_drawdown"
    results.sort(key=lambda x: x["objective_value"], reverse=reverse)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    # Build heatmap data — group by all 6 axis pairs
    heatmap = _build_heatmap_data(results, objective)

    finished_at = datetime.now(timezone.utc)
    final = {
        "optimization_id": optimization_id,
        "status": "completed",
        "objective": objective,
        "total_combinations": total,
        "started_at": _active_optimization["started_at"] if _active_optimization else None,
        "finished_at": finished_at.isoformat(),
        "duration_s": round((finished_at - datetime.fromisoformat(_active_optimization["started_at"])).total_seconds(), 1) if _active_optimization else 0,
        "best": results[0] if results else None,
        "results": results[:50],
        "heatmap": heatmap,
        "grid_config": _active_optimization.get("grid_config") if _active_optimization else None,
        "base_config": base_config,
    }

    # Persist to MongoDB
    doc = {**final, "stored_at": finished_at}
    await database.replay_optimizations.insert_one(doc)
    doc.pop("_id", None)

    if _active_optimization:
        _active_optimization["status"] = "completed"
        _active_optimization["result"] = final
        _active_optimization["finished_at"] = finished_at.isoformat()


def _build_heatmap_data(results: List[Dict], objective: str) -> Dict:
    """Build heatmap matrices for all 6 axis pair combinations."""
    params = ["zscore", "delta_ratio", "ofi", "sl_atr"]
    pairs = list(itertools.combinations(params, 2))
    heatmaps = {}

    for p1, p2 in pairs:
        key = f"{p1}_x_{p2}"
        matrix = {}
        counts = {}

        for r in results:
            c = r["combo"]
            x_val = str(c[p1])
            y_val = str(c[p2])
            cell_key = f"{x_val}|{y_val}"

            if cell_key not in matrix:
                matrix[cell_key] = 0.0
                counts[cell_key] = 0
            matrix[cell_key] += r["objective_value"]
            counts[cell_key] += 1

        # Average objective per cell
        cells = []
        for cell_key, total_obj in matrix.items():
            x_str, y_str = cell_key.split("|")
            avg = total_obj / counts[cell_key] if counts[cell_key] > 0 else 0
            cells.append({
                "x": float(x_str),
                "y": float(y_str),
                "value": round(avg, 4),
                "count": counts[cell_key],
            })

        # Unique axis values
        x_vals = sorted(set(c["x"] for c in cells))
        y_vals = sorted(set(c["y"] for c in cells))

        heatmaps[key] = {
            "x_param": p1,
            "y_param": p2,
            "x_values": x_vals,
            "y_values": y_vals,
            "cells": cells,
        }

    return heatmaps


def get_optimization_status() -> Optional[Dict]:
    """Get current optimization status."""
    if not _active_optimization:
        return None
    return {
        "optimization_id": _active_optimization["optimization_id"],
        "status": _active_optimization["status"],
        "started_at": _active_optimization["started_at"],
        "finished_at": _active_optimization.get("finished_at"),
        "progress": _active_optimization.get("progress", {}),
        "grid_config": _active_optimization.get("grid_config"),
        "objective": _active_optimization.get("objective"),
    }


def get_optimization_result() -> Optional[Dict]:
    """Get completed optimization result."""
    if not _active_optimization:
        return None
    if _active_optimization["status"] != "completed":
        return None
    return _active_optimization.get("result")


async def start_optimization(database, grid_config: Dict, base_config: Dict,
                             objective: str = "sharpe",
                             n_tradeable_min: int = 30) -> Dict:
    """Start a new optimization in background. Returns immediately with optimization_id.

    Args:
        n_tradeable_min: Minimum actionable snapshots required. Refuses to optimize
            if fewer than this many non-WAIT snapshots exist (avoids meaningless calibration).
    """
    global _active_optimization

    # ── Guard: minimum actionable snapshot count ──
    # Optimizing on all-WAIT snapshots produces statistically empty calibration.
    symbol = base_config.get('symbol', 'MES')
    try:
        pipeline = [
            {'$match': {'symbol': symbol, 'v3_status': {'$ne': 'WAIT'}}},
            {'$count': 'n'},
        ]
        cursor = database.v3_snapshots.aggregate(pipeline)
        docs = await cursor.to_list(length=1)
        n_tradeable = docs[0]['n'] if docs else 0
    except Exception:
        n_tradeable = 0

    if n_tradeable < n_tradeable_min:
        raise RuntimeError(
            f"Insufficient actionable snapshots: {n_tradeable} non-WAIT found, "
            f"need at least {n_tradeable_min}. Collect more live data before optimizing."
        )

    async with _optimization_lock:
        if _active_optimization and _active_optimization["status"] == "running":
            raise RuntimeError("Optimization already running")

        optimization_id = str(uuid.uuid4())[:12]
        grid = generate_grid(grid_config)

        _active_optimization = {
            "optimization_id": optimization_id,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "cancelled": False,
            "progress": {"current": 0, "total": len(grid), "pct": 0, "best_objective": 0, "best_params": None},
            "grid_config": grid_config,
            "objective": objective,
            "result": None,
        }

    # Fire and forget
    asyncio.create_task(run_optimization(database, optimization_id, grid, base_config, objective))

    return {
        "optimization_id": optimization_id,
        "total_combinations": len(grid),
        "status": "running",
    }


async def cancel_optimization() -> bool:
    """Cancel the active optimization."""
    global _active_optimization
    if _active_optimization and _active_optimization["status"] == "running":
        _active_optimization["cancelled"] = True
        return True
    return False
