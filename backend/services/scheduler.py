"""
Walk-Forward Scheduler
======================
Periodic automatic optimization that runs in background.
Compares results against current parameters and optionally auto-applies
when improvement exceeds threshold.

Runs as an asyncio background task started from server.py on startup.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from services.optimizer import generate_grid, run_optimization, _build_replay_config
from services.replay_engine import DEFAULT_CONFIG, merge_config

logger = logging.getLogger(__name__)

# In-memory scheduler state
_scheduler_task: Optional[asyncio.Task] = None
_scheduler_running = False


def _compute_next_run(schedule: Dict) -> datetime:
    """Compute next run time based on schedule config."""
    now = datetime.now(timezone.utc)
    freq = schedule.get("frequency", "weekly")
    hour = schedule.get("hour_utc", 6)

    if freq == "daily":
        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run

    if freq == "custom_hours":
        custom_h = schedule.get("custom_hours", 24)
        last_run = schedule.get("last_run_at")
        if last_run and isinstance(last_run, datetime):
            return last_run + timedelta(hours=custom_h)
        return now + timedelta(hours=custom_h)

    # weekly (default)
    dow = schedule.get("day_of_week", 6)  # 0=Mon, 6=Sun
    next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    days_ahead = dow - now.weekday()
    if days_ahead < 0 or (days_ahead == 0 and next_run <= now):
        days_ahead += 7
    next_run += timedelta(days=days_ahead)
    return next_run


async def update_schedule(database, schedule_data: Dict) -> Dict:
    """Create or update the single active schedule."""
    global _scheduler_task

    now = datetime.now(timezone.utc)
    next_run = _compute_next_run(schedule_data)

    doc = {
        "active": schedule_data.get("enabled", True),
        "frequency": schedule_data.get("frequency", "weekly"),
        "custom_hours": schedule_data.get("custom_hours"),
        "day_of_week": schedule_data.get("day_of_week", 6),
        "hour_utc": schedule_data.get("hour_utc", 6),
        "grid_config": schedule_data.get("grid_config", {}),
        "objective": schedule_data.get("objective", "sharpe"),
        "auto_apply": schedule_data.get("auto_apply", False),
        "improvement_threshold_pct": schedule_data.get("improvement_threshold_pct", 10.0),
        "created_at": now,
        "updated_at": now,
        "next_run_at": next_run,
        "last_run_at": None,
    }

    # Upsert: only one active schedule
    await database.replay_schedules.delete_many({})
    await database.replay_schedules.insert_one(doc)
    doc.pop("_id", None)

    # Convert datetimes
    for key in ("created_at", "updated_at", "next_run_at"):
        if key in doc and isinstance(doc[key], datetime):
            doc[key] = doc[key].isoformat()

    # Restart scheduler loop if running
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    if doc["active"]:
        _scheduler_task = asyncio.create_task(_scheduler_loop(database))
        logger.info("Scheduler started/restarted. Next run: %s", next_run.isoformat())

    return doc


async def disable_schedule(database):
    """Disable the active schedule."""
    global _scheduler_task
    await database.replay_schedules.update_many({}, {"$set": {"active": False}})
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None
    logger.info("Scheduler disabled")


async def start_scheduler(database):
    """Called on server startup to resume any active schedule."""
    global _scheduler_task

    schedule = await database.replay_schedules.find_one({"active": True})
    if not schedule:
        logger.info("No active schedule found")
        return

    _scheduler_task = asyncio.create_task(_scheduler_loop(database))
    logger.info("Scheduler resumed on startup")


async def _scheduler_loop(database):
    """Main scheduler loop — checks every 60s if it's time to run."""
    global _scheduler_running

    try:
        while True:
            schedule = await database.replay_schedules.find_one({"active": True})
            if not schedule:
                logger.info("Schedule deactivated, stopping loop")
                break

            now = datetime.now(timezone.utc)
            next_run = schedule.get("next_run_at")

            # Motor devolve datetimes naive — normalizar para UTC-aware antes de comparar.
            if isinstance(next_run, datetime) and next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=timezone.utc)

            if next_run and isinstance(next_run, datetime) and now >= next_run:
                if not _scheduler_running:
                    _scheduler_running = True
                    try:
                        await _execute_scheduled_run(database, schedule)
                    except Exception as e:
                        logger.error("Scheduled run failed: %s", e)
                    finally:
                        _scheduler_running = False

            # Sleep 60s between checks
            await asyncio.sleep(60)

    except asyncio.CancelledError:
        logger.info("Scheduler loop cancelled")


async def _execute_scheduled_run(database, schedule: Dict):
    """Execute a single scheduled optimization run."""
    import uuid

    logger.info("Starting scheduled optimization run")
    run_id = str(uuid.uuid4())[:12]
    started_at = datetime.now(timezone.utc)

    grid_config = schedule.get("grid_config", {})
    objective = schedule.get("objective", "sharpe")
    auto_apply = schedule.get("auto_apply", False)
    threshold_pct = schedule.get("improvement_threshold_pct", 10.0)

    # Generate grid and run all combos
    grid = generate_grid(grid_config)
    base_config = merge_config({})  # Use current defaults as base

    # Run optimization inline (not via background task, since we're already in background)
    results = []
    best_obj = float('-inf') if objective != "min_drawdown" else float('inf')
    best_result = None

    from services.replay_engine import run_replay

    for combo in grid:
        config = _build_replay_config(combo, base_config)
        result = await run_replay(database, config)

        obj_val = _extract_obj(result["metrics"], objective)
        is_better = (
            (objective != "min_drawdown" and obj_val > best_obj) or
            (objective == "min_drawdown" and obj_val < best_obj)
        )
        if is_better and result["metrics"]["total_trades"] > 0:
            best_obj = obj_val
            best_result = {"combo": combo, "metrics": result["metrics"], "objective_value": round(obj_val, 4)}

        results.append({
            "combo": combo,
            "objective_value": round(obj_val, 4),
            "total_trades": result["metrics"]["total_trades"],
        })

    completed_at = datetime.now(timezone.utc)

    # Compare with current defaults
    current_baseline_result = await run_replay(database, base_config)
    baseline_obj = _extract_obj(current_baseline_result["metrics"], objective)

    improvement_pct = 0.0
    if baseline_obj != 0:
        if objective == "min_drawdown":
            improvement_pct = ((baseline_obj - best_obj) / abs(baseline_obj)) * 100
        else:
            improvement_pct = ((best_obj - baseline_obj) / abs(baseline_obj)) * 100

    applied = False
    if auto_apply and improvement_pct >= threshold_pct and best_result:
        # Auto-apply: save best params as "active" preset
        await _apply_best_params(database, best_result["combo"], grid_config.get("regime", "TRANSICAO"))
        applied = True
        logger.info("Auto-applied params: improvement=%.1f%% (threshold=%.1f%%)", improvement_pct, threshold_pct)
    elif best_result:
        logger.info("Proposed params (not auto-applied): improvement=%.1f%% (threshold=%.1f%%)", improvement_pct, threshold_pct)

    # Record history
    history_doc = {
        "run_id": run_id,
        "executed_at": started_at,
        "completed_at": completed_at,
        "duration_s": round((completed_at - started_at).total_seconds(), 1),
        "objective": objective,
        "total_combinations": len(grid),
        "baseline_objective": round(baseline_obj, 4),
        "best_objective": round(best_obj, 4) if best_result else None,
        "improvement_pct": round(improvement_pct, 2),
        "auto_applied": applied,
        "best_params": best_result["combo"] if best_result else None,
        "best_metrics": best_result["metrics"] if best_result else None,
        "status": "applied" if applied else "proposed" if best_result else "no_improvement",
    }
    await database.replay_schedule_history.insert_one(history_doc)
    history_doc.pop("_id", None)

    # Update next_run
    schedule_data = {**schedule}
    schedule_data["last_run_at"] = completed_at
    next_run = _compute_next_run(schedule_data)
    await database.replay_schedules.update_one(
        {"active": True},
        {"$set": {"last_run_at": completed_at, "next_run_at": next_run, "updated_at": completed_at}}
    )

    logger.info("Scheduled run complete: %s (improvement: %.1f%%, applied: %s)",
                run_id, improvement_pct, applied)


async def _apply_best_params(database, combo: Dict, regime: str):
    """Save best params as the auto-tuned preset."""
    from datetime import datetime, timezone

    arch_map = {
        "COMPLACENCIA": "TREND", "BULL": "TREND", "BEAR": "TREND",
        "TRANSICAO": "RANGE", "CAPITULACAO": "FADE",
    }
    archetype = arch_map.get(regime, "RANGE")

    config_update = {
        "zscore_min": {regime: combo["zscore"]},
        "delta_ratio_min": {regime: combo["delta_ratio"]},
        "ofi_threshold": {regime: combo["ofi"]},
        "sl_atr_mult": {archetype: combo["sl_atr"]},
    }

    now = datetime.now(timezone.utc)
    await database.replay_presets.update_one(
        {"name": "[Auto-Tuned]"},
        {"$set": {
            "name": "[Auto-Tuned]",
            "config": config_update,
            "description": f"Auto-applied by scheduler on {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "preset_id": "auto-tuned",
            "updated_at": now,
        }, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )


def _extract_obj(metrics: Dict, objective: str) -> float:
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
