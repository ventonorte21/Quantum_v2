"""
Scalp Diagnostics Service
=========================
Analisa snapshots históricos para responder:
  1. outcome_distribution  — READY / S2_BLOCKED / S1_NO_DATA por regime (taxa de disparo %)
  2. s2_block_reasons      — quais gates bloqueiam mais, por regime
  3. zone_quality_dist     — distribuição de qualidade ZONES por regime
  4. ofi_slow_impact       — impacto da penalidade OFI Slow nos scores de zona

Motor de sugestões automáticas gera texto em português quando n >= MIN_SAMPLE.
"""

import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


MIN_SAMPLE = 30          # Mínimo de snapshots para emitir sugestão
MODERATE_THRESH = 2.5    # Threshold score moderado padrão (usado para OFI analysis)

# Expressão MongoDB para regime mode-aware:
#   ZONES mode → zones.day_regime  (ex: ROTATION, EXPANSION_BEAR)
#   FLOW/CANDLE → s1_regime        (ex: BULL_TREND, ROTATION)
_REGIME_EXPR: Dict = {
    "$cond": [
        {"$eq": ["$mode", "ZONES"]},
        {"$ifNull": ["$zones.day_regime", "NO_ZONE"]},
        {"$ifNull": ["$s1_regime",        "NO_DATA"]},
    ]
}

# Labels que pertencem a cada grupo de sessão de alto nível
_NY_LABELS     = ["RTH_OPEN", "RTH_MID", "RTH_CLOSE"]
_GLOBEX_LABELS = ["OVERNIGHT", "HALTED"]


def _session_group_filter(session: Optional[str]) -> Dict[str, Any]:
    """Devolve fragmento do filtro MongoDB para session_label dado o selector de sessão."""
    if session is None:
        return {}
    if session == "RTH_ALL" or session == "NY":
        return {"session_label": {"$in": _NY_LABELS}}
    if session == "GLOBEX":
        return {"session_label": {"$in": _GLOBEX_LABELS}}
    return {"session_label": session}


# ── Normalização de block_reason ──────────────────────────────────────────────

def _normalize_reason(raw: str) -> str:
    """Remove sufixo numérico entre parênteses. Ex: 'OFI fast (0.15)' → 'OFI fast'."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()


# ── Aggregation 1: Outcome distribution ──────────────────────────────────────

async def _outcome_distribution(col, base_filter: dict) -> Dict[str, Any]:
    pipeline = [
        {"$match": {**base_filter, "scalp_status": {"$ne": "MARKET_CLOSED"}}},
        {"$addFields": {
            "_regime": _REGIME_EXPR,
            "_outcome": {
                "$switch": {
                    "branches": [
                        # S1_NO_DATA só se aplica a modo FLOW/CANDLE sem regime determinado
                        {"case": {"$and": [
                            {"$ne": ["$mode", "ZONES"]},
                            {"$in": ["$s1_regime", ["NO_DATA", None]]},
                        ]}, "then": "S1_NO_DATA"},
                        {"case": {"$in": ["$s3_action", ["buy", "sell"]]},
                         "then": "READY"},
                        {"case": {"$eq": ["$s2_passed", False]},
                         "then": "S2_BLOCKED"},
                    ],
                    "default": "OTHER",
                }
            }
        }},
        {"$group": {
            "_id": {"regime": "$_regime", "outcome": "$_outcome"},
            "count": {"$sum": 1},
        }},
        {"$group": {
            "_id": "$_id.regime",
            "breakdown": {"$push": {"outcome": "$_id.outcome", "count": "$count"}},
            "total": {"$sum": "$count"},
        }},
        {"$sort": {"total": -1}},
    ]

    rows = []
    async for doc in col.aggregate(pipeline):
        regime = doc["_id"] or "NO_DATA"
        total  = doc["total"]
        bd     = {b["outcome"]: b["count"] for b in doc["breakdown"]}
        ready  = bd.get("READY", 0)
        rows.append({
            "regime":     regime,
            "total":      total,
            "ready":      ready,
            "s2_blocked": bd.get("S2_BLOCKED", 0),
            "s1_no_data": bd.get("S1_NO_DATA", 0),
            "other":      bd.get("OTHER", 0),
            "fire_rate":  round(ready / total * 100, 1) if total else 0.0,
        })

    total_all  = sum(r["total"] for r in rows)
    total_ready = sum(r["ready"] for r in rows)
    return {
        "rows":           rows,
        "total_snapshots": total_all,
        "total_ready":    total_ready,
        "global_fire_rate": round(total_ready / total_all * 100, 1) if total_all else 0.0,
    }


# ── Aggregation 2: S2 block reasons ──────────────────────────────────────────

async def _s2_block_reasons(col, base_filter: dict) -> Dict[str, Any]:
    pipeline = [
        {"$match": {**base_filter,
                    "s2_passed": False,
                    "scalp_status": {"$ne": "MARKET_CLOSED"},
                    "s2.block_reasons": {"$exists": True, "$ne": []}}},
        {"$addFields": {"_regime": _REGIME_EXPR}},
        {"$unwind": "$s2.block_reasons"},
        {"$group": {
            "_id": {"reason": "$s2.block_reasons", "regime": "$_regime"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"count": -1}},
    ]

    raw: List[Dict] = []
    async for doc in col.aggregate(pipeline):
        raw.append({
            "reason_raw": doc["_id"]["reason"],
            "reason":     _normalize_reason(doc["_id"]["reason"]),
            "regime":     doc["_id"]["regime"] or "NO_DATA",
            "count":      doc["count"],
        })

    total_mentions = sum(r["count"] for r in raw)

    by_reason: Dict[str, Dict] = {}
    for r in raw:
        key = r["reason"]
        if key not in by_reason:
            by_reason[key] = {"reason": key, "total": 0, "by_regime": {}}
        by_reason[key]["total"] += r["count"]
        by_reason[key]["by_regime"][r["regime"]] = r["count"]

    sorted_reasons = sorted(by_reason.values(), key=lambda x: x["total"], reverse=True)
    for row in sorted_reasons:
        row["pct"] = round(row["total"] / total_mentions * 100, 1) if total_mentions else 0.0

    return {
        "rows":           sorted_reasons,
        "total_mentions": total_mentions,
    }


# ── Aggregation 3: Zone quality distribution ──────────────────────────────────

async def _zone_quality_distribution(col, base_filter: dict) -> Dict[str, Any]:
    pipeline = [
        {"$match": {**base_filter, "scalp_status": {"$ne": "MARKET_CLOSED"}}},
        {"$addFields": {"_regime": _REGIME_EXPR}},
        {"$group": {
            "_id": {"quality": "$zone_quality", "regime": "$_regime"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"count": -1}},
    ]

    rows: List[Dict] = []
    async for doc in col.aggregate(pipeline):
        rows.append({
            "quality": doc["_id"]["quality"] or "NONE",
            "regime":  doc["_id"]["regime"]  or "NO_DATA",
            "count":   doc["count"],
        })

    quality_totals: Dict[str, int] = {}
    for r in rows:
        q = r["quality"]
        quality_totals[q] = quality_totals.get(q, 0) + r["count"]

    total = sum(quality_totals.values())
    quality_pct = {q: round(c / total * 100, 1) for q, c in quality_totals.items()} if total else {}

    return {
        "rows":          rows,
        "quality_totals": quality_totals,
        "quality_pct":   quality_pct,
        "total":         total,
    }


# ── Aggregation 4: OFI Slow penalty impact ────────────────────────────────────

async def _ofi_slow_impact(col, base_filter: dict) -> Dict[str, Any]:
    zones_filter = {**base_filter, "mode": "ZONES",
                    "scalp_status": {"$ne": "MARKET_CLOSED"},
                    "zones.score_breakdown": {"$exists": True, "$ne": None}}

    pipeline = [
        {"$match": zones_filter},
        {"$addFields": {
            "_penalty":     "$zones.score_breakdown.ofi_slow_penalty",
            "_total_score": "$zones.score_breakdown.total_score",
            "_has_penalty": {"$lt": [
                {"$ifNull": ["$zones.score_breakdown.ofi_slow_penalty", 0]}, 0
            ]},
        }},
        {"$addFields": {
            "_would_pass": {
                "$and": [
                    "$_has_penalty",
                    {"$lt": [{"$ifNull": ["$_total_score", 0]}, MODERATE_THRESH]},
                    {"$gte": [
                        {"$subtract": [
                            {"$ifNull": ["$_total_score", 0]},
                            {"$ifNull": ["$_penalty", 0]},
                        ]},
                        MODERATE_THRESH,
                    ]},
                ]
            }
        }},
        {"$group": {
            "_id":           None,
            "total":         {"$sum": 1},
            "with_penalty":  {"$sum": {"$cond": ["$_has_penalty", 1, 0]}},
            "would_pass":    {"$sum": {"$cond": ["$_would_pass", 1, 0]}},
            "avg_penalty":   {"$avg": "$_penalty"},
            "avg_score":     {"$avg": "$_total_score"},
        }},
    ]

    result = None
    async for doc in col.aggregate(pipeline):
        result = doc
        break

    if not result:
        return {"total": 0, "with_penalty": 0, "would_pass": 0,
                "penalty_rate": 0.0, "tipped_into_block_rate": 0.0,
                "avg_penalty": 0.0, "avg_score": 0.0}

    total        = result["total"]
    with_penalty = result["with_penalty"]
    would_pass   = result["would_pass"]

    return {
        "total":                  total,
        "with_penalty":           with_penalty,
        "would_pass":             would_pass,
        "penalty_rate":           round(with_penalty / total * 100, 1) if total else 0.0,
        "tipped_into_block_rate": round(would_pass / with_penalty * 100, 1) if with_penalty else 0.0,
        "avg_penalty":            round(result["avg_penalty"] or 0.0, 3),
        "avg_score":              round(result["avg_score"]   or 0.0, 3),
    }


# ── Aggregation 5: Gamma Suppression Rate ─────────────────────────────────────

async def _gamma_suppression_rate(col, base_filter: dict) -> Dict[str, Any]:
    """
    Calcula taxa de snapshots onde SHORT_GAMMA suprimiu zonas Gamma.
    Útil para AutoTune medir se o ambiente de mercado é estruturalmente avesso (short gamma).
    """
    pipeline = [
        {"$match": {**base_filter, "mode": "ZONES",
                    "scalp_status": {"$ne": "MARKET_CLOSED"}}},
        {"$group": {
            "_id":       None,
            "total":     {"$sum": 1},
            "suppressed": {"$sum": {
                "$cond": [
                    {"$eq": ["$macro_context.gamma_short_suppressed", True]},
                    1, 0,
                ]
            }},
        }},
    ]

    result = None
    async for doc in col.aggregate(pipeline):
        result = doc
        break

    if not result or result["total"] == 0:
        return {"total": 0, "suppressed": 0, "suppression_rate": 0.0}

    total      = result["total"]
    suppressed = result["suppressed"]
    return {
        "total":            total,
        "suppressed":       suppressed,
        "suppression_rate": round(suppressed / total * 100, 1),
    }


# ── Motor de sugestões ────────────────────────────────────────────────────────

def _generate_suggestions(outcomes: Dict, reasons: Dict, zone_dist: Dict,
                           ofi: Dict, gamma_sup: Dict) -> List[Dict[str, str]]:
    suggestions = []
    total_snaps = outcomes.get("total_snapshots", 0)
    if total_snaps < MIN_SAMPLE:
        return suggestions

    # 1. Fire rate baixo por regime
    for row in outcomes.get("rows", []):
        if row["total"] < MIN_SAMPLE:
            continue
        if row["fire_rate"] < 20.0 and row["regime"] not in ("NO_DATA", "NEUTRAL", "NO_ZONE"):
            suggestions.append({
                "level":   "warning",
                "message": (
                    f"Regime {row['regime']}: taxa de disparo {row['fire_rate']}% — "
                    f"apenas {row['ready']} de {row['total']} avaliações geraram sinal. "
                    f"Considere revisar os thresholds de S3 para este regime."
                ),
            })

    # 2. S2_BLOCKED dominante
    total_blocked = sum(r["s2_blocked"] for r in outcomes.get("rows", []))
    total_eval    = sum(r["total"] for r in outcomes.get("rows", [])
                        if r["regime"] not in ("NO_DATA", "NO_ZONE"))
    if total_eval >= MIN_SAMPLE and total_blocked / total_eval > 0.40:
        pct = round(total_blocked / total_eval * 100, 1)
        suggestions.append({
            "level":   "warning",
            "message": (
                f"S2 está bloqueando {pct}% das avaliações de mercado aberto. "
                f"Revise os critérios de qualidade — o sistema pode estar excessivamente seletivo."
            ),
        })

    # 3. Um reason domina > 55%
    reason_rows = reasons.get("rows", [])
    if reason_rows and reasons.get("total_mentions", 0) >= MIN_SAMPLE:
        top = reason_rows[0]
        if top["pct"] > 55.0:
            suggestions.append({
                "level":   "warning",
                "message": (
                    f"'{top['reason']}' representa {top['pct']}% de todos os bloqueios de S2. "
                    f"Este gate está dominando — considere revisar o threshold específico."
                ),
            })

    # 4. OFI Slow penalty virando bloqueio frequentemente
    if ofi.get("with_penalty", 0) >= MIN_SAMPLE:
        tipped = ofi.get("tipped_into_block_rate", 0.0)
        if tipped > 40.0:
            suggestions.append({
                "level":   "warning",
                "message": (
                    f"A penalidade OFI Slow está convertendo {tipped}% dos candidatos penalizados "
                    f"em bloqueios (score passou de ≥{MODERATE_THRESH} para <{MODERATE_THRESH}). "
                    f"Considere reduzir ofi_slow_fade_thresh ou ofi_slow_momentum_thresh no Auto Tune."
                ),
            })

    # 5. SHORT_GAMMA suprimindo zonas com frequência alta
    sup_rate = gamma_sup.get("suppression_rate", 0.0)
    sup_n    = gamma_sup.get("total", 0)
    if sup_n >= MIN_SAMPLE and sup_rate > 30.0:
        suggestions.append({
            "level":   "info",
            "message": (
                f"SHORT_GAMMA está suprimindo zonas Gamma em {sup_rate}% dos ciclos ZONES "
                f"({gamma_sup['suppressed']} de {sup_n} snapshots). "
                f"O ambiente de mercado é estruturalmente avesso ao risco — zonas Gamma pouco úteis neste período."
            ),
        })

    # 6. Positivo: fire rate saudável
    if outcomes["global_fire_rate"] >= 20.0 and len(suggestions) == 0:
        suggestions.append({
            "level":   "ok",
            "message": (
                f"Taxa de disparo global {outcomes['global_fire_rate']}% — sistema seletivo mas funcional. "
                f"Sem anomalias detectadas com {total_snaps} snapshots analisados."
            ),
        })

    return suggestions


# ── Orquestrador principal ────────────────────────────────────────────────────

async def get_scalp_diagnostics(
    database,
    symbol:      str,
    days:        int            = 30,
    mode_filter: Optional[str] = None,
    session:     Optional[str] = None,
) -> Dict[str, Any]:
    col    = database.scalp_snapshots
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    base_filter: Dict[str, Any] = {
        "symbol":      symbol.upper(),
        "recorded_at": {"$gte": cutoff},
    }
    if mode_filter:
        base_filter["mode"] = mode_filter.upper()
    base_filter.update(_session_group_filter(session))

    outcomes, reasons, zone_dist, ofi, gamma_sup = await asyncio.gather(
        _outcome_distribution(col, base_filter),
        _s2_block_reasons(col, base_filter),
        _zone_quality_distribution(col, base_filter),
        _ofi_slow_impact(col, base_filter),
        _gamma_suppression_rate(col, base_filter),
    )

    suggestions = _generate_suggestions(outcomes, reasons, zone_dist, ofi, gamma_sup)

    return {
        "symbol":                    symbol.upper(),
        "days":                      days,
        "mode_filter":               mode_filter,
        "session":                   session,
        "outcome_distribution":      outcomes,
        "s2_block_reasons":          reasons,
        "zone_quality_distribution": zone_dist,
        "ofi_slow_impact":           ofi,
        "gamma_suppression":         gamma_sup,
        "suggestions":               suggestions,
        "min_sample":                MIN_SAMPLE,
    }
