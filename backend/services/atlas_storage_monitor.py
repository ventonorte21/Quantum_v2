"""
atlas_storage_monitor.py
━━━━━━━━━━━━━━━━━━━━━━━━
Monitor periódico do espaço utilizado no MongoDB Atlas.

Níveis de alerta:
  WARN     ≥ 70%  — aviso antecipado
  HIGH     ≥ 85%  — acção recomendada (apagar snapshots antigos / upgrade)
  CRITICAL ≥ 95%  — risco de escrita parar

Envia Telegram (alert_system_event) quando cada nível é atingido,
com cooldown de 6h por nível para evitar spam.

Padrão de uso:
  await start_storage_monitor(database, plan_limit_mb=512)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger("atlas_storage")

# ── Configuração ────────────────────────────────────────────────────────────

# Limites standard por plano Atlas (MB de storageSize + indexSize)
PLAN_LIMITS_MB: Dict[str, float] = {
    "M0":  512.0,
    "M2":  2_048.0,
    "M5":  5_120.0,
    "M10": 10_240.0,
    "M20": 20_480.0,
    "M30": 51_200.0,
}

THRESHOLDS = [
    (0.95, "CRITICAL", "🔴"),
    (0.85, "HIGH",     "🟠"),
    (0.70, "WARN",     "🟡"),
]

ALERT_COOLDOWN_H = 6          # horas entre alertas do mesmo nível
CHECK_INTERVAL_S = 3_600      # verificar de hora a hora

# ── Estado em memória ───────────────────────────────────────────────────────

_status: Dict[str, Any] = {
    "checked_at":       None,
    "storage_mb":       None,
    "index_mb":         None,
    "total_mb":         None,
    "limit_mb":         None,
    "pct_used":         None,
    "level":            "OK",
    "plan":             "M0",
    "collections":      {},
    "last_alert_at":    {},   # nível → datetime UTC
    "alert_count":      0,
    "scheduler_running": False,
    "error":            None,
}

_task: Optional[asyncio.Task] = None


# ── Helpers ─────────────────────────────────────────────────────────────────

def get_storage_status() -> Dict[str, Any]:
    return dict(_status)


async def _collect_stats(database) -> Dict[str, Any]:
    """Executa dbStats e collStats no Atlas e retorna um resumo."""
    db_stats = await database.command("dbStats", scale=1)  # bytes
    storage_b  = db_stats.get("storageSize", 0)
    index_b    = db_stats.get("indexSize",   0)
    total_b    = storage_b + index_b

    colls = await database.list_collection_names()
    coll_detail: Dict[str, Dict] = {}
    for name in sorted(colls):
        try:
            cs = await database.command("collStats", name, scale=1024 * 1024)
            coll_detail[name] = {
                "docs":       cs.get("count", 0),
                "data_mb":    round(cs.get("size",        0) / (1024**2), 3),
                "storage_mb": round(cs.get("storageSize", 0) / (1024**2), 3),
            }
        except Exception:
            pass

    return {
        "storage_mb":  round(storage_b  / (1024**2), 3),
        "index_mb":    round(index_b    / (1024**2), 3),
        "total_mb":    round(total_b    / (1024**2), 3),
        "objects":     db_stats.get("objects", 0),
        "collections": coll_detail,
    }


def _resolve_level(pct: float) -> tuple[str, str]:
    """Retorna (level_name, emoji) para a percentagem dada."""
    for threshold, name, emoji in THRESHOLDS:
        if pct >= threshold:
            return name, emoji
    return "OK", "🟢"


def _should_alert(level: str) -> bool:
    """Verifica cooldown por nível de alerta."""
    if level == "OK":
        return False
    last = _status["last_alert_at"].get(level)
    if last is None:
        return True
    return datetime.now(timezone.utc) - last >= timedelta(hours=ALERT_COOLDOWN_H)


async def _send_alert(level: str, emoji: str, pct: float,
                      total_mb: float, limit_mb: float, plan: str):
    """Envia alerta Telegram via alert_system_event."""
    try:
        from services.telegram_alerts import alert_system_event
        free_mb   = max(0.0, limit_mb - total_mb)
        title     = f"{emoji} Atlas Storage {level} ({pct:.1%})"
        detail    = (
            f"Plano: {plan} | Limite: {limit_mb:.0f} MB\n"
            f"Usado: <b>{total_mb:.1f} MB</b>  •  Livre: {free_mb:.1f} MB\n"
            f"Percentagem: <b>{pct:.1%}</b>\n\n"
        )
        if level == "CRITICAL":
            detail += "⛔ <b>Acção imediata</b>: apagar snapshots antigos ou fazer upgrade do plano."
        elif level == "HIGH":
            detail += "⚠️ Considera apagar snapshots antigos ou fazer upgrade do plano Atlas."
        else:
            detail += "Monitoriza de perto — o espaço está a diminuir."

        await alert_system_event(title, detail, level=level.lower())
        _status["last_alert_at"][level] = datetime.now(timezone.utc)
        _status["alert_count"] += 1
        logger.warning("Atlas Storage ALERTA enviado: %s (%.1f%%)", level, pct * 100)
    except Exception as exc:
        logger.error("Erro ao enviar alerta Telegram: %s", exc)


# ── Loop principal ──────────────────────────────────────────────────────────

async def _monitor_loop(database, plan: str, limit_mb: float):
    logger.info(
        "AtlasStorageMonitor iniciado (plano=%s, limite=%.0f MB, intervalo=%ds)",
        plan, limit_mb, CHECK_INTERVAL_S,
    )
    while True:
        try:
            raw = await _collect_stats(database)
            total_mb = raw["total_mb"]
            pct      = total_mb / limit_mb if limit_mb > 0 else 0.0
            level, emoji = _resolve_level(pct)

            _status.update({
                "checked_at":  datetime.now(timezone.utc).isoformat(),
                "storage_mb":  raw["storage_mb"],
                "index_mb":    raw["index_mb"],
                "total_mb":    total_mb,
                "limit_mb":    limit_mb,
                "pct_used":    round(pct, 4),
                "level":       level,
                "plan":        plan,
                "collections": raw["collections"],
                "error":       None,
            })

            logger.info(
                "Atlas Storage: %.1f/%.0f MB (%.1f%%) — %s %s",
                total_mb, limit_mb, pct * 100, emoji, level,
            )

            if _should_alert(level):
                await _send_alert(level, emoji, pct, total_mb, limit_mb, plan)

        except Exception as exc:
            _status["error"] = str(exc)
            logger.error("AtlasStorageMonitor erro: %s", exc)

        await asyncio.sleep(CHECK_INTERVAL_S)


# ── Inicialização ────────────────────────────────────────────────────────────

def start_storage_monitor(database, plan: str = "M0") -> bool:
    """
    Inicia o monitor em background.
    Retorna True se arrancou, False se já estava a correr.
    """
    global _task

    if _task and not _task.done():
        return False

    limit_mb = PLAN_LIMITS_MB.get(plan.upper(), PLAN_LIMITS_MB["M0"])
    _status["plan"]             = plan.upper()
    _status["limit_mb"]         = limit_mb
    _status["scheduler_running"] = True

    loop = asyncio.get_event_loop()
    _task = loop.create_task(_monitor_loop(database, plan.upper(), limit_mb))
    return True


async def check_now(database, plan: str = "M0") -> Dict[str, Any]:
    """
    Executa uma verificação imediata (não espera pelo próximo ciclo).
    Útil para o endpoint manual.
    """
    limit_mb = PLAN_LIMITS_MB.get(plan.upper(), PLAN_LIMITS_MB["M0"])
    raw      = await _collect_stats(database)
    total_mb = raw["total_mb"]
    pct      = total_mb / limit_mb if limit_mb > 0 else 0.0
    level, emoji = _resolve_level(pct)

    _status.update({
        "checked_at":  datetime.now(timezone.utc).isoformat(),
        "storage_mb":  raw["storage_mb"],
        "index_mb":    raw["index_mb"],
        "total_mb":    total_mb,
        "limit_mb":    limit_mb,
        "pct_used":    round(pct, 4),
        "level":       level,
        "plan":        plan.upper(),
        "collections": raw["collections"],
        "error":       None,
    })

    if _should_alert(level):
        await _send_alert(level, emoji, pct, total_mb, limit_mb, plan)

    return get_storage_status()
