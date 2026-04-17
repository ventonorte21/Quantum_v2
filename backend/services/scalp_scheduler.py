"""
Scalp Auto-Tune Scheduler
==========================
Schedule periódico de Walk-Forward Optimization para o sistema Scalp V3.

Colecções MongoDB:
  scalp_tune_schedule  — documento único com configuração activa
  scalp_tune_history   — histórico de runs (TTL 180 dias)

Frequências suportadas:
  daily        — todos os dias a uma hora UTC
  weekly       — um dia da semana a uma hora UTC
  custom_hours — a cada N horas desde o último run

Auto-apply:
  Se improvement_pct >= threshold AND auto_apply=True,
  os melhores params são gravados na scalp_config via scalp_config collection.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger("scalp_scheduler")

_scheduler_task:    Optional[asyncio.Task] = None
_scheduler_running: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# Helpers de tempo
# ══════════════════════════════════════════════════════════════════════════════

def _compute_next_run(schedule: Dict) -> datetime:
    """Calcula o próximo instante de execução com base na configuração."""
    now  = datetime.now(timezone.utc)
    freq = schedule.get("frequency", "weekly")
    hour = int(schedule.get("hour_utc", 6))

    if freq == "daily":
        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run

    if freq == "custom_hours":
        custom_h = int(schedule.get("custom_hours", 24))
        last_run = schedule.get("last_run_at")
        if isinstance(last_run, datetime):
            return last_run + timedelta(hours=custom_h)
        return now + timedelta(hours=custom_h)

    # weekly (padrão)
    dow = int(schedule.get("day_of_week", 6))   # 0=Seg, 6=Dom
    next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    days_ahead = dow - now.weekday()
    if days_ahead < 0 or (days_ahead == 0 and next_run <= now):
        days_ahead += 7
    next_run += timedelta(days=days_ahead)
    return next_run


def _dt_to_iso(v) -> Optional[str]:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


# ══════════════════════════════════════════════════════════════════════════════
# CRUD do schedule
# ══════════════════════════════════════════════════════════════════════════════

async def get_scalp_schedule(database) -> Optional[Dict]:
    """Retorna o schedule activo ou None."""
    doc = await database.scalp_tune_schedule.find_one({}, {"_id": 0})
    if not doc:
        return None
    for k in ("created_at", "updated_at", "next_run_at", "last_run_at"):
        if k in doc:
            doc[k] = _dt_to_iso(doc[k])
    return doc


async def update_scalp_schedule(database, data: Dict) -> Dict:
    """Cria ou actualiza o schedule Scalp Auto-Tune. Reinicia o loop."""
    global _scheduler_task

    now      = datetime.now(timezone.utc)
    next_run = _compute_next_run(data)

    doc = {
        "active":                    data.get("enabled", True),
        "frequency":                 data.get("frequency", "weekly"),
        "custom_hours":              data.get("custom_hours", 24),
        "day_of_week":               data.get("day_of_week", 6),
        "hour_utc":                  data.get("hour_utc", 6),
        "mode":                      data.get("mode", "FLOW"),
        "method":                    data.get("method", "BAYESIAN"),
        "objective":                 data.get("objective", "sharpe"),
        "symbol":                    data.get("symbol", "MNQ"),
        "train_days":                int(data.get("train_days", 10)),
        "test_days":                 int(data.get("test_days", 3)),
        "n_folds":                   int(data.get("n_folds", 4)),
        "n_random":                  int(data.get("n_random", 5)),
        "n_iter":                    int(data.get("n_iter", 15)),
        "min_snapshots":             int(data.get("min_snapshots", 50)),
        "auto_apply":                bool(data.get("auto_apply", False)),
        "improvement_threshold_pct": float(data.get("improvement_threshold_pct", 5.0)),
        "created_at":                now,
        "updated_at":                now,
        "next_run_at":               next_run,
        "last_run_at":               None,
    }

    await database.scalp_tune_schedule.delete_many({})
    await database.scalp_tune_schedule.insert_one(doc)

    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    if doc["active"]:
        _scheduler_task = asyncio.create_task(_scheduler_loop(database))
        logger.info(f"ScalpScheduler: iniciado. Próximo run: {next_run.isoformat()}")

    doc.pop("_id", None)
    for k in ("created_at", "updated_at", "next_run_at"):
        doc[k] = _dt_to_iso(doc[k])
    doc["last_run_at"] = None
    return doc


async def disable_scalp_schedule(database) -> None:
    """Desactiva e para o scheduler."""
    global _scheduler_task
    await database.scalp_tune_schedule.update_many({}, {"$set": {"active": False}})
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None
    logger.info("ScalpScheduler: desactivado")


async def start_scalp_scheduler(database) -> None:
    """Chamado no startup para retomar o schedule activo."""
    global _scheduler_task
    doc = await database.scalp_tune_schedule.find_one({"active": True})
    if not doc:
        logger.info("ScalpScheduler: sem schedule activo")
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop(database))
    logger.info("ScalpScheduler: retomado no startup")


# ══════════════════════════════════════════════════════════════════════════════
# Loop principal
# ══════════════════════════════════════════════════════════════════════════════

async def _scheduler_loop(database) -> None:
    global _scheduler_running
    try:
        while True:
            schedule = await database.scalp_tune_schedule.find_one({"active": True})
            if not schedule:
                logger.info("ScalpScheduler: schedule desactivado, parando loop")
                break

            now      = datetime.now(timezone.utc)
            next_run = schedule.get("next_run_at")

            # Motor devolve datetimes naive ao ler do MongoDB (timezone não preservado).
            # Normalizar para UTC-aware antes de comparar com `now` (UTC-aware).
            if isinstance(next_run, datetime) and next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=timezone.utc)

            if isinstance(next_run, datetime) and now >= next_run:
                if not _scheduler_running:
                    _scheduler_running = True
                    try:
                        await _execute_scheduled_run(database, schedule)
                    except Exception as e:
                        logger.error(f"ScalpScheduler run failed: {e}")
                    finally:
                        _scheduler_running = False

            await asyncio.sleep(60)

    except asyncio.CancelledError:
        logger.info("ScalpScheduler: loop cancelado")


# ══════════════════════════════════════════════════════════════════════════════
# Execução de um run agendado
# ══════════════════════════════════════════════════════════════════════════════

async def _execute_scheduled_run(database, schedule: Dict) -> None:
    """Executa um Walk-Forward completo e opcionalmente aplica os melhores params."""
    from services.scalp_optimizer import run_walk_forward

    run_id     = str(uuid.uuid4())[:12]
    started_at = datetime.now(timezone.utc)
    symbol     = schedule.get("symbol", "MNQ")

    logger.info(f"ScalpScheduler [{run_id}]: iniciando WalkForward para {symbol}")

    # Verifica snapshots mínimos
    n_snaps = await database.scalp_snapshots.count_documents({"symbol": symbol})
    min_snaps = int(schedule.get("min_snapshots", 50))
    if n_snaps < min_snaps:
        logger.warning(
            f"ScalpScheduler [{run_id}]: snapshots insuficientes "
            f"({n_snaps} < {min_snaps}) — run cancelado"
        )
        await _record_history(database, run_id, started_at, schedule,
                              error=f"Snapshots insuficientes: {n_snaps}/{min_snaps}")
        _advance_next_run(database, schedule)
        return

    base_config = {
        "symbol":      symbol,
        "mode_filter": schedule.get("mode"),
    }

    try:
        result = await run_walk_forward(
            database    = database,
            base_config = base_config,
            objective   = schedule.get("objective", "sharpe"),
            method      = schedule.get("method", "BAYESIAN"),
            mode        = schedule.get("mode", "FLOW"),
            train_days  = int(schedule.get("train_days", 10)),
            test_days   = int(schedule.get("test_days", 3)),
            n_folds     = int(schedule.get("n_folds", 4)),
            n_random    = int(schedule.get("n_random", 5)),
            n_iter      = int(schedule.get("n_iter", 15)),
        )
    except Exception as e:
        logger.error(f"ScalpScheduler [{run_id}]: walk-forward falhou: {e}")
        await _record_history(database, run_id, started_at, schedule, error=str(e))
        await _advance_next_run(database, schedule)
        return

    completed_at = datetime.now(timezone.utc)
    folds        = result.get("folds", [])
    agg          = result.get("aggregate", {})

    # Melhores params do fold mais recente (se existir)
    best_params: Optional[Dict] = None
    if folds:
        best_params = folds[-1].get("best_params")

    # Auto-apply
    applied = False
    threshold_pct = float(schedule.get("improvement_threshold_pct", 5.0))
    if schedule.get("auto_apply") and best_params and agg.get("avg_sharpe", 0) > 0:
        try:
            await _apply_best_params(database, best_params, symbol)
            applied = True
            logger.info(f"ScalpScheduler [{run_id}]: params auto-aplicados")
        except Exception as e:
            logger.warning(f"ScalpScheduler [{run_id}]: auto-apply falhou: {e}")

    await _record_history(database, run_id, started_at, schedule,
                          completed_at=completed_at, result=result,
                          best_params=best_params, applied=applied,
                          agg=agg, n_snaps=n_snaps)
    await _advance_next_run(database, schedule)
    logger.info(
        f"ScalpScheduler [{run_id}]: concluído em "
        f"{round((completed_at - started_at).total_seconds(), 1)}s | "
        f"folds={len(folds)} | applied={applied}"
    )


async def _apply_best_params(database, flat_params: Dict, symbol: str) -> None:
    """
    Aplica os melhores parâmetros encontrados ao scalp_config.
    Apenas actualiza campos mapeados directamente ao config.
    """
    now = datetime.now(timezone.utc)

    # Mapeamento flat → campos do config MongoDB
    update = {}
    field_map = {
        "risk.sl_ticks_mnq": "sl_ticks_mnq",
        "risk.tp_ticks_mnq": "tp_ticks_mnq",
        "risk.sl_ticks_mes": "sl_ticks_mes",
        "risk.tp_ticks_mes": "tp_ticks_mes",
    }
    for flat_key, cfg_key in field_map.items():
        if flat_key in flat_params:
            update[cfg_key] = flat_params[flat_key]

    if not update:
        return

    update["auto_tuned_at"]     = now.isoformat()
    update["auto_tuned_params"] = flat_params

    await database.scalp_config.update_one(
        {"id": "default"},
        {"$set": update},
        upsert=True,
    )
    logger.info(f"ScalpScheduler: scalp_config actualizado com {list(update.keys())}")


async def _record_history(database, run_id: str, started_at: datetime,
                          schedule: Dict, completed_at: Optional[datetime] = None,
                          result: Optional[Dict] = None,
                          best_params: Optional[Dict] = None,
                          applied: bool = False,
                          agg: Optional[Dict] = None,
                          n_snaps: int = 0,
                          error: Optional[str] = None) -> None:
    """Grava o resultado no histórico de runs."""
    end = completed_at or datetime.now(timezone.utc)
    doc = {
        "run_id":       run_id,
        "started_at":   started_at,
        "completed_at": end,
        "duration_s":   round((end - started_at).total_seconds(), 1),
        "symbol":       schedule.get("symbol", "MNQ"),
        "mode":         schedule.get("mode", "FLOW"),
        "objective":    schedule.get("objective", "sharpe"),
        "method":       schedule.get("method", "BAYESIAN"),
        "n_snapshots":  n_snaps,
        "n_folds":      len(result.get("folds", [])) if result else 0,
        "aggregate":    agg or {},
        "best_params":  best_params,
        "auto_applied": applied,
        "error":        error,
        "status":       "error" if error else ("applied" if applied else "completed"),
    }
    await database.scalp_tune_history.insert_one(doc)


async def _advance_next_run(database, schedule: Dict) -> None:
    """Avança o next_run_at e actualiza last_run_at."""
    now       = datetime.now(timezone.utc)
    sched_mod = {**schedule, "last_run_at": now}
    next_run  = _compute_next_run(sched_mod)
    await database.scalp_tune_schedule.update_one(
        {"active": True},
        {"$set": {"last_run_at": now, "next_run_at": next_run, "updated_at": now}},
    )


# ══════════════════════════════════════════════════════════════════════════════
# Histórico
# ══════════════════════════════════════════════════════════════════════════════

async def get_scalp_schedule_history(database, limit: int = 20) -> list:
    """Retorna os últimos runs ordenados por data desc."""
    cursor = database.scalp_tune_history.find(
        {}, {"_id": 0}
    ).sort("started_at", -1).limit(limit)
    docs = []
    async for doc in cursor:
        for k in ("started_at", "completed_at"):
            if k in doc and isinstance(doc[k], datetime):
                doc[k] = doc[k].isoformat()
        docs.append(doc)
    return docs


async def ensure_scalp_schedule_indexes(database) -> None:
    """Cria índices TTL para o histórico de runs."""
    try:
        await database.scalp_tune_history.create_index(
            "started_at",
            expireAfterSeconds=180 * 24 * 3600,
            name="ttl_180d",
        )
        await database.scalp_tune_history.create_index(
            [("symbol", 1), ("started_at", -1)],
            name="sym_time",
        )
        logger.info("ScalpScheduler: índices de histórico criados")
    except Exception as e:
        logger.debug(f"ScalpScheduler: índices já existem ({e})")
