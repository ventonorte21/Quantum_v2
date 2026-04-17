"""
Replay Engine API Routes
========================
Endpoints for Walk-Forward backtesting with configurable parameters.
Includes Auto-Tune optimizer with background grid search.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import uuid
import io
import csv

from services.replay_engine import (
    run_replay, run_batch, DEFAULT_CONFIG, merge_config,
    ObjectiveFunction,
)
from services.optimizer import (
    start_optimization, cancel_optimization,
    get_optimization_status, get_optimization_result,
)

replay_router = APIRouter(prefix="/api/replay", tags=["replay"])

# Will be set by server.py on startup
_database = None


def set_replay_db(db):
    global _database
    _database = db


# ── Request Models ──

class ReplayRunRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)

class ReplayBatchRequest(BaseModel):
    configs: List[Dict[str, Any]]
    objective: str = "sharpe"

class ReplayCompareRequest(BaseModel):
    run_ids: List[str]

class GridRangeConfig(BaseModel):
    min: float
    max: float
    steps: int = Field(ge=2, le=10)

class OptimizeRequest(BaseModel):
    grid_config: Dict[str, Any] = Field(default_factory=lambda: {
        "zscore_min": {"min": 0.5, "max": 1.5, "steps": 5},
        "delta_ratio_min": {"min": 0.10, "max": 0.30, "steps": 5},
        "ofi_threshold": {"min": 0.1, "max": 0.4, "steps": 4},
        "sl_atr_mult": {"min": 0.5, "max": 1.5, "steps": 4},
        "regime": "TRANSICAO",
    })
    base_config: Dict[str, Any] = Field(default_factory=dict)
    objective: str = "sharpe"

class PresetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    config: Dict[str, Any]
    description: str = ""

class PresetUpdateRequest(BaseModel):
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    description: Optional[str] = None

class ScheduleRequest(BaseModel):
    enabled: bool = True
    frequency: str = "weekly"  # daily, weekly, custom_hours
    custom_hours: Optional[int] = None  # only if frequency == custom_hours
    day_of_week: int = 6  # 0=Mon, 6=Sun
    hour_utc: int = 6  # 06:00 UTC = ~02:00 ET
    grid_config: Dict[str, Any] = Field(default_factory=lambda: {
        "zscore_min": {"min": 0.5, "max": 1.5, "steps": 5},
        "delta_ratio_min": {"min": 0.10, "max": 0.30, "steps": 5},
        "ofi_threshold": {"min": 0.1, "max": 0.4, "steps": 4},
        "sl_atr_mult": {"min": 0.5, "max": 1.5, "steps": 4},
        "regime": "TRANSICAO",
    })
    objective: str = "sharpe"
    auto_apply: bool = False  # True = auto-update active params
    improvement_threshold_pct: float = 10.0  # min % improvement to auto-apply


# ── Endpoints ──

@replay_router.get("/defaults")
async def get_default_config():
    """Return the default configuration for the Replay Engine."""
    return {"config": DEFAULT_CONFIG}


@replay_router.get("/run/submit")
async def execute_replay_get(payload: str):
    """GET companion for /run — proxy-safe (no JSON body). payload = JSON-encoded ReplayRunRequest."""
    import json as _json
    try:
        data = _json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload JSON")
    req = ReplayRunRequest(**data)
    return await execute_replay(req)


@replay_router.post("/run")
async def execute_replay(request: ReplayRunRequest):
    """Execute a single Walk-Forward replay with the given configuration."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    result = await run_replay(_database, request.config)

    # Persist the run to MongoDB for future retrieval
    run_doc = {
        **result,
        "stored_at": datetime.now(timezone.utc),
    }
    await _database.replay_runs.insert_one(run_doc)

    # Remove _id before returning
    run_doc.pop("_id", None)
    return result


@replay_router.get("/runs")
async def list_runs(limit: int = 20):
    """List previous replay runs (most recent first)."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    cursor = _database.replay_runs.find(
        {}, {"_id": 0, "trades": 0, "metrics.equity_curve": 0}
    ).sort("stored_at", -1).limit(limit)

    runs = []
    async for doc in cursor:
        # Convert datetime fields
        for key in ("stored_at", "started_at", "finished_at"):
            if key in doc and isinstance(doc[key], datetime):
                doc[key] = doc[key].isoformat()
        runs.append(doc)

    return {"runs": runs, "total": len(runs)}


@replay_router.get("/run/{run_id}")
async def get_run(run_id: str):
    """Get full details of a specific replay run."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    doc = await _database.replay_runs.find_one(
        {"run_id": run_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Convert datetime fields
    for key in ("stored_at", "started_at", "finished_at"):
        if key in doc and isinstance(doc[key], datetime):
            doc[key] = doc[key].isoformat()

    # Convert trade times
    for t in doc.get("trades", []):
        for tk in ("entry_time", "exit_time"):
            if tk in t and isinstance(t[tk], datetime):
                t[tk] = t[tk].isoformat()

    return doc


@replay_router.get("/export/{run_id}")
async def export_run(run_id: str, format: str = "csv"):
    """Export trades of a replay run as CSV or XLSX."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    doc = await _database.replay_runs.find_one({"run_id": run_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    trades = doc.get("trades", [])
    metrics = doc.get("metrics", {})
    config = doc.get("config", {})

    # Flatten trade data
    columns = [
        "id", "symbol", "regime", "archetype", "side", "quantity",
        "entry_price", "exit_price", "hard_stop", "take_profit",
        "entry_time", "exit_time", "exit_reason", "entry_reason",
        "gross_pnl", "net_pnl", "commission", "duration_minutes",
        "scale_out_pnl", "scale_out_done", "trailing_active", "breakeven_active",
        "max_favorable", "max_adverse",
    ]

    rows = []
    for t in trades:
        row = {}
        for c in columns:
            val = t.get(c, "")
            if isinstance(val, datetime):
                val = val.isoformat()
            row[c] = val
        rows.append(row)

    filename = f"replay_{run_id}"

    if format == "xlsx":
        import pandas as pd
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            # Trades sheet
            if rows:
                df = pd.DataFrame(rows, columns=columns)
                df.to_excel(writer, sheet_name="Trades", index=False)
            # Metrics sheet
            m_rows = [[k, v] for k, v in metrics.items() if k not in ("equity_curve", "regime_breakdown", "archetype_breakdown", "exit_breakdown")]
            if m_rows:
                pd.DataFrame(m_rows, columns=["Metric", "Value"]).to_excel(writer, sheet_name="Metrics", index=False)
            # Config sheet
            c_rows = [[k, str(v)] for k, v in config.items()]
            if c_rows:
                pd.DataFrame(c_rows, columns=["Parameter", "Value"]).to_excel(writer, sheet_name="Config", index=False)

        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
        )
    else:
        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        content = output.getvalue()
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
        )


@replay_router.post("/compare")
async def compare_runs(request: ReplayCompareRequest):
    """Compare metrics of multiple runs side by side."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    comparisons = []
    for rid in request.run_ids:
        doc = await _database.replay_runs.find_one(
            {"run_id": rid},
            {"_id": 0, "trades": 0, "metrics.equity_curve": 0}
        )
        if doc:
            for key in ("stored_at", "started_at", "finished_at"):
                if key in doc and isinstance(doc[key], datetime):
                    doc[key] = doc[key].isoformat()
            comparisons.append(doc)

    return {"comparisons": comparisons, "count": len(comparisons)}


@replay_router.post("/batch")
async def execute_batch(request: ReplayBatchRequest):
    """Execute multiple replay configs and rank by objective function.

    ML-ready: accepts parameter grid, returns ranked results.
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    if len(request.configs) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 configs per batch")

    result = await run_batch(_database, request.configs, request.objective)

    # Persist batch result
    batch_doc = {
        **result,
        "stored_at": datetime.now(timezone.utc),
    }
    await _database.replay_batches.insert_one(batch_doc)
    batch_doc.pop("_id", None)

    return result


@replay_router.delete("/run/{run_id}")
async def delete_run(run_id: str):
    """Delete a specific replay run."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    result = await _database.replay_runs.delete_one({"run_id": run_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    return {"deleted": run_id}


@replay_router.get("/snapshot-stats")
async def get_snapshot_stats():
    """Get snapshot collection stats for the replay engine."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    total = await _database.v3_snapshots.count_documents({})
    actionable = await _database.v3_snapshots.count_documents(
        {"action": {"$in": ["BUY", "SELL"]}}
    )

    oldest = await _database.v3_snapshots.find_one(
        {}, {"recorded_at": 1, "_id": 0}, sort=[("recorded_at", 1)]
    )
    newest = await _database.v3_snapshots.find_one(
        {}, {"recorded_at": 1, "_id": 0}, sort=[("recorded_at", -1)]
    )

    pipeline = [
        {"$group": {"_id": "$symbol", "count": {"$sum": 1}}},
    ]
    by_symbol = {}
    async for doc in _database.v3_snapshots.aggregate(pipeline):
        by_symbol[doc["_id"]] = doc["count"]

    rpipe = [
        {"$group": {"_id": "$regime", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_regime = {}
    async for doc in _database.v3_snapshots.aggregate(rpipe):
        by_regime[doc["_id"]] = doc["count"]

    return {
        "total_snapshots": total,
        "actionable_signals": actionable,
        "oldest": oldest["recorded_at"].isoformat() if oldest and isinstance(oldest.get("recorded_at"), datetime) else None,
        "newest": newest["recorded_at"].isoformat() if newest and isinstance(newest.get("recorded_at"), datetime) else None,
        "by_symbol": by_symbol,
        "by_regime": by_regime,
    }


# ── Auto-Tune Optimizer Endpoints ──

@replay_router.get("/optimize/start")
async def start_optimize_get(payload: str):
    """GET companion for /optimize — proxy-safe (no JSON body)."""
    import json as _json
    try:
        data = _json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload JSON")
    req = OptimizeRequest(**data)
    return await start_optimize(req)


@replay_router.post("/optimize")
async def start_optimize(request: OptimizeRequest):
    """Start a background grid search optimization.

    Returns immediately with optimization_id. Poll /optimize/status for progress.
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    # Validate grid won't exceed 1000 combos
    gc = request.grid_config
    total = 1
    for key in ["zscore_min", "delta_ratio_min", "ofi_threshold", "sl_atr_mult"]:
        if key in gc and isinstance(gc[key], dict):
            total *= gc[key].get("steps", 1)
    if total > 1000:
        raise HTTPException(status_code=400, detail=f"Grid too large: {total} combinations (max 1000)")

    try:
        result = await start_optimization(_database, request.grid_config, request.base_config, request.objective)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@replay_router.get("/optimize/status")
async def optimize_status():
    """Get current optimization progress."""
    status = get_optimization_status()
    if not status:
        return {"status": "idle", "message": "No optimization running"}
    return status


@replay_router.get("/optimize/result")
async def optimize_result():
    """Get completed optimization result."""
    result = get_optimization_result()
    if not result:
        status = get_optimization_status()
        if status and status["status"] == "running":
            raise HTTPException(status_code=202, detail="Optimization still running")
        raise HTTPException(status_code=404, detail="No optimization result available")
    return result


@replay_router.post("/optimize/cancel")
async def optimize_cancel():
    """Cancel active optimization."""
    cancelled = await cancel_optimization()
    if cancelled:
        return {"status": "cancelled"}
    raise HTTPException(status_code=404, detail="No active optimization to cancel")


@replay_router.get("/optimize/history")
async def optimize_history(limit: int = 10):
    """List previous optimization results."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    cursor = _database.replay_optimizations.find(
        {}, {"_id": 0, "results": {"$slice": 5}, "heatmap": 0}
    ).sort("stored_at", -1).limit(limit)

    runs = []
    async for doc in cursor:
        for key in ("stored_at", "started_at", "finished_at"):
            if key in doc and isinstance(doc[key], datetime):
                doc[key] = doc[key].isoformat()
        runs.append(doc)

    return {"optimizations": runs, "total": len(runs)}


# ── Preset Endpoints ──

@replay_router.get("/presets/create")
async def create_preset_get(payload: str):
    """GET companion for POST /presets — proxy-safe (no JSON body)."""
    import json as _json
    try:
        data = _json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload JSON")
    req = PresetCreateRequest(**data)
    return await create_preset(req)


@replay_router.post("/presets")
async def create_preset(request: PresetCreateRequest):
    """Save current config as a named preset."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    existing = await _database.replay_presets.find_one({"name": request.name})
    if existing:
        raise HTTPException(status_code=409, detail=f"Preset '{request.name}' already exists")

    doc = {
        "preset_id": str(uuid.uuid4())[:12],
        "name": request.name,
        "config": request.config,
        "description": request.description,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    await _database.replay_presets.insert_one(doc)
    doc.pop("_id", None)
    doc["created_at"] = doc["created_at"].isoformat()
    doc["updated_at"] = doc["updated_at"].isoformat()
    return doc


@replay_router.get("/presets")
async def list_presets():
    """List all saved presets."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    presets = []
    async for doc in _database.replay_presets.find({}, {"_id": 0}).sort("created_at", -1).limit(200):
        for key in ("created_at", "updated_at"):
            if key in doc and isinstance(doc[key], datetime):
                doc[key] = doc[key].isoformat()
        presets.append(doc)

    return {"presets": presets}


@replay_router.get("/presets/{preset_id}")
async def get_preset(preset_id: str):
    """Get a single preset by ID."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    doc = await _database.replay_presets.find_one({"preset_id": preset_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Preset not found")

    for key in ("created_at", "updated_at"):
        if key in doc and isinstance(doc[key], datetime):
            doc[key] = doc[key].isoformat()
    return doc


@replay_router.put("/presets/{preset_id}")
async def update_preset(preset_id: str, request: PresetUpdateRequest):
    """Update preset name, config, or description."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    update_fields = {"updated_at": datetime.now(timezone.utc)}
    if request.name is not None:
        update_fields["name"] = request.name
    if request.config is not None:
        update_fields["config"] = request.config
    if request.description is not None:
        update_fields["description"] = request.description

    result = await _database.replay_presets.update_one(
        {"preset_id": preset_id}, {"$set": update_fields}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Preset not found")

    doc = await _database.replay_presets.find_one({"preset_id": preset_id}, {"_id": 0})
    for key in ("created_at", "updated_at"):
        if key in doc and isinstance(doc[key], datetime):
            doc[key] = doc[key].isoformat()
    return doc


@replay_router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: str):
    """Delete a preset."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    result = await _database.replay_presets.delete_one({"preset_id": preset_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"deleted": True, "preset_id": preset_id}


# ── Schedule Endpoints ──

@replay_router.get("/schedule/save")
async def create_or_update_schedule_get(payload: str):
    """GET companion for POST /schedule — proxy-safe (no JSON body)."""
    import json as _json
    try:
        data = _json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload JSON")
    req = ScheduleRequest(**data)
    return await create_or_update_schedule(req)


@replay_router.post("/schedule")
async def create_or_update_schedule(request: ScheduleRequest):
    """Create or update the auto-tune schedule. Only one schedule exists at a time."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    from services.scheduler import update_schedule
    schedule = await update_schedule(_database, request.dict())
    return schedule


@replay_router.get("/schedule")
async def get_schedule():
    """Get the current schedule configuration."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    doc = await _database.replay_schedules.find_one({"active": True}, {"_id": 0})
    if not doc:
        return {"enabled": False, "message": "No schedule configured"}
    for key in ("created_at", "updated_at", "last_run_at", "next_run_at"):
        if key in doc and isinstance(doc[key], datetime):
            doc[key] = doc[key].isoformat()
    return doc


@replay_router.delete("/schedule")
async def disable_schedule():
    """Disable the active schedule."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    from services.scheduler import disable_schedule
    await disable_schedule(_database)
    return {"enabled": False, "message": "Schedule disabled"}


@replay_router.get("/schedule/history")
async def schedule_execution_history(limit: int = 20):
    """Get history of scheduled optimization runs."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    runs = []
    cursor = _database.replay_schedule_history.find(
        {}, {"_id": 0}
    ).sort("executed_at", -1).limit(limit)
    async for doc in cursor:
        for key in ("executed_at", "completed_at"):
            if key in doc and isinstance(doc[key], datetime):
                doc[key] = doc[key].isoformat()
        runs.append(doc)

    return {"history": runs, "total": len(runs)}
