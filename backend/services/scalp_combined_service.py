"""
scalp_combined_service.py
─────────────────────────
Análise combinada de duas dimensões ortogonais:

  D1 — Diagnóstico (scalp_snapshots):
        fire rate, block reasons, OFI slow tipping
  D2 — Calibração  (scalp_trades):
        win rate global, por quality, por zone_type

Regras de inferência cruzam D1 + D2 para gerar sugestões de
delta de parâmetros concretos com rationale em português.

Schedule autónomo gravado em `scalp_combined_schedule`.
Histórico em `scalp_combined_history` (TTL 180 dias).
auto_apply aplica bounded deltas via scalp_config + módulo scalp_zones.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta, time as dt_time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

logger = logging.getLogger("scalp_combined")

# ── Safety bounds por parâmetro ───────────────────────────────────────────────
PARAM_BOUNDS: Dict[str, Tuple[float, float]] = {
    # Grupo OFI Slow (par dependente — calibráveis individualmente)
    "zones_ofi_slow_fade_thresh":     (0.35, 0.75),
    "zones_ofi_slow_momentum_thresh": (0.20, 0.55),
    # Grupo Score (par dependente — invariante: strong > moderate)
    "zones_score_moderate_thresh":    (1.80, 3.50),
    "zones_score_strong_thresh":      (3.00, 5.50),
    # Grupo 3 — OFI Fast (par dependente — invariante: break_min > fast_min)
    "zones_ofi_fast_min":             (0.05, 0.25),   # espelha OFI_FAST_MIN  em scalp_zones
    "zones_ofi_break_min":            (0.15, 0.50),   # espelha OFI_BREAK_MIN em scalp_zones
}

# ── Defaults (espelham constantes em scalp_zones.py) ─────────────────────────
PARAM_DEFAULTS: Dict[str, float] = {
    "zones_ofi_slow_fade_thresh":     0.55,
    "zones_ofi_slow_momentum_thresh": 0.35,
    "zones_score_moderate_thresh":    2.50,
    "zones_score_strong_thresh":      4.00,
    "zones_ofi_fast_min":             0.12,
    "zones_ofi_break_min":            0.30,
}

# Regimes — N mínimo para considerar calibrável
S1_REGIME_MIN_CALIBRAVEL = 15   # trades por regime (S1) para gerar sugestão

# ── Thresholds de decisão ─────────────────────────────────────────────────────
OFI_TIP_THRESHOLD       = 40.0   # % para sugerir relaxar fade/momentum thresh
FIRE_RATE_LOW_THRESH    = 20.0   # % fire rate baixo (over-blocking)
FIRE_RATE_HIGH_THRESH   = 55.0   # % fire rate alto (under-filtering)

# D2 — thresholds de edge globais (fallback; valores por instrumento em EV_THRESHOLDS)
EV_ACCEPT               = 0.30   # pts expectativa mínima — fallback genérico
EV_UNACCEPTABLE         = 0.00   # pts expectativa que indica ausência de edge — fallback
PF_ACCEPT               = 1.15   # profit factor mínimo para relaxar threshold
PF_UNACCEPTABLE         = 1.00   # profit factor que indica sem edge
WL_RATIO_ACCEPT         = 1.10   # win/loss ratio mínimo (tamanho médio wins / losses)

# WR mantido como dimensão auxiliar (validação confirmatória)
WIN_RATE_ACCEPT         = 45.0   # % win rate mínimo — critério mais permissivo (EV/PF são primários)
WIN_RATE_UNACCEPTABLE   = 40.0   # % win rate máximo para apertar

# ── EV thresholds por instrumento ─────────────────────────────────────────────
# Calibrados por valor de ponto e custo estimado de round-trip:
#   MNQ  1pt = $2.00  → 0.80pts ≈ $1.60  (cobre comissão + slippage + margem mínima)
#   MES  1pt = $5.00  → 0.40pts ≈ $2.00  (escala proporcional)
#   NQ   1pt = $20.0  → 0.12pts ≈ $2.40  (tick menor, threshold mais apertado em pts)
#   ES   1pt = $50.0  → 0.08pts ≈ $4.00  (full-size, threshold muito apertado em pts)
# Quando o instrumento não estiver mapeado, usam-se os fallbacks EV_ACCEPT / EV_UNACCEPTABLE.
EV_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "MNQ": {"accept": 0.80, "unacceptable": 0.20},
    "MES": {"accept": 0.40, "unacceptable": 0.10},
    "NQ":  {"accept": 0.12, "unacceptable": 0.00},
    "ES":  {"accept": 0.08, "unacceptable": 0.00},
}


def _ev_thresholds(symbol: str) -> Tuple[float, float]:
    """
    Devolve (ev_accept, ev_unacceptable) calibrados para o instrumento.

    Uso:
        ev_accept, ev_bad = _ev_thresholds(symbol)
        _has_edge(slice, min_pnl=ev_accept)
        _no_edge(slice, ev_bad=ev_bad)
    """
    t = EV_THRESHOLDS.get((symbol or "").upper(), {})
    return (
        float(t.get("accept",      EV_ACCEPT)),
        float(t.get("unacceptable", EV_UNACCEPTABLE)),
    )

MIN_TRADES_BASIC        = 10     # trades mínimos para qual. suggestion
MIN_TRADES_SCORE        = 15     # trades mínimos para score thresh suggestion
MIN_TRADES_QUALITY_GATE = 10     # trades mínimos para min_quality suggestion
MIN_SNAPSHOTS           = 30     # snapshots mínimos para D1


# ══════════════════════════════════════════════════════════════════════════════
# Filtro de sessão — centralizado
# ══════════════════════════════════════════════════════════════════════════════

def _session_group_filter(session: Optional[str]) -> dict:
    """
    Traduz o parâmetro de sessão para um filtro MongoDB.
      NY      → session_label $in [RTH_OPEN, RTH_MID, RTH_CLOSE]
      GLOBEX  → session_label $in [OVERNIGHT, HALTED]
      RTH_ALL → alias legado de NY
      granular (ex. "RTH_OPEN") → igualdade directa
      None    → sem filtro
    """
    from services.trading_calendar_service import NY_SESSION_LABELS, GLOBEX_SESSION_LABELS
    if session in ("NY", "RTH_ALL"):
        return {"session_label": {"$in": list(NY_SESSION_LABELS)}}
    if session == "GLOBEX":
        return {"session_label": {"$in": list(GLOBEX_SESSION_LABELS)}}
    if session:
        return {"session_label": session}
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# Dimensão 1 — Diagnóstico (snapshots)
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_dimension_1(db, symbol: str, days: int, session: Optional[str] = None) -> Dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    col    = db["scalp_snapshots"]

    # Filtro base reutilizável em todos os sub-pipelines
    _sf: Dict = {"symbol": symbol, "recorded_at": {"$gte": cutoff}, **_session_group_filter(session)}

    # ZONES mode nunca popula s1_regime (usa zones.day_regime) → s1_regime é sempre NO_DATA.
    # Para FLOW/CANDLE mode, excluímos snapshots onde S1 não determinou regime (sem dados OFI).
    base_match: Dict = {
        **_sf,
        "$or": [
            {"mode": "ZONES"},
            {"s1_regime": {"$ne": "NO_DATA"}},
        ],
    }

    # Expressão que normaliza scalp_status cross-mode para métricas D1:
    #   ZONES + ACTIVE_SIGNAL → "READY"      (zona activa, qualidade ≥ MODERATE = sinal gerado)
    #   ZONES + NO_SIGNAL     → "QUALITY_BLOCK" (zona encontrada mas score insuficiente)
    #   ZONES + NO_ZONE       → "NO_ZONE"    (sem zona próxima — sem oportunidade)
    #   FLOW  + BLOCKED       → "S2_BLOCKED" (bloqueio explícito S2 FLOW mode)
    #   qualquer outro        → valor original
    _norm_status_expr = {
        "$switch": {
            "branches": [
                {"case": {"$and": [{"$eq": ["$mode", "ZONES"]},
                                   {"$eq": ["$scalp_status", "ACTIVE_SIGNAL"]}]},
                 "then": "READY"},
                {"case": {"$and": [{"$eq": ["$mode", "ZONES"]},
                                   {"$eq": ["$scalp_status", "NO_SIGNAL"]}]},
                 "then": "QUALITY_BLOCK"},
                {"case": {"$eq": ["$scalp_status", "BLOCKED"]},
                 "then": "S2_BLOCKED"},
            ],
            "default": "$scalp_status",
        }
    }

    pipeline_outcome = [
        {"$match": base_match},
        {"$addFields": {"_norm": _norm_status_expr}},
        {"$group": {
            "_id":   "$_norm",
            "total": {"$sum": 1},
        }},
    ]
    outcome_raw: Dict[str, int] = {}
    async for doc in col.aggregate(pipeline_outcome):
        outcome_raw[doc["_id"]] = doc["total"]

    total_active = sum(outcome_raw.values())
    # READY  = ACTIVE_SIGNAL em ZONES, READY em FLOW/CANDLE
    # S2_BLOCKED = BLOCKED em FLOW, S2_BLOCKED em FLOW, QUALITY_BLOCK em ZONES (zona encontrada, score < limiar)
    ready      = outcome_raw.get("READY", 0)
    s2_blocked = outcome_raw.get("S2_BLOCKED", 0) + outcome_raw.get("QUALITY_BLOCK", 0)

    global_fire_rate   = round(ready / total_active * 100, 1) if total_active else 0.0
    s2_blocked_rate    = round(s2_blocked / total_active * 100, 1) if total_active else 0.0

    # Block reasons — mode-aware:
    #   ZONES: snapshots com zona activa mas score insuficiente (NO_SIGNAL + active_zone presente)
    #   FLOW:  snapshots com scalp_status S2_BLOCKED ou BLOCKED
    _reasons_filter = {
        **_sf,
        "$or": [
            # ZONES mode: zona encontrada mas qualidade bloqueou
            {"mode": "ZONES", "zones.active_zone": {"$ne": None},
             "s2.block_reasons": {"$exists": True, "$not": {"$size": 0}}},
            # FLOW/CANDLE mode: bloqueio explícito
            {"mode": {"$ne": "ZONES"}, "scalp_status": {"$in": ["S2_BLOCKED", "BLOCKED"]},
             "s2.block_reasons": {"$exists": True, "$not": {"$size": 0}}},
        ],
    }
    pipeline_reasons = [
        {"$match": _reasons_filter},
        {"$unwind": "$s2.block_reasons"},
        {"$group": {"_id": "$s2.block_reasons", "total": {"$sum": 1}}},
        {"$sort": {"total": -1}},
        {"$limit": 1},
    ]
    top_reason     = None
    top_reason_pct = 0.0
    total_mentions = 0

    pipeline_total_mentions = [
        {"$match": _reasons_filter},
        {"$project": {"cnt": {"$size": {"$ifNull": ["$s2.block_reasons", []]}}}},
        {"$group": {"_id": None, "total": {"$sum": "$cnt"}}},
    ]
    async for doc in col.aggregate(pipeline_total_mentions):
        total_mentions = doc["total"]

    async for doc in col.aggregate(pipeline_reasons):
        raw_reason = doc["_id"] or ""
        top_reason = re.sub(r"\s*\([\d.]+\)\s*$", "", raw_reason).strip()
        top_reason_pct = round(doc["total"] / total_mentions * 100, 1) if total_mentions else 0.0

    # OFI Slow impact
    pipeline_ofi = [
        {"$match": {**_sf,
            "mode":        "ZONES",
            "zones.score_breakdown": {"$exists": True, "$ne": None},
        }},
        {"$group": {
            "_id":               None,
            "total":             {"$sum": 1},
            "with_penalty":      {"$sum": {"$cond": [{"$lt": ["$zones.score_breakdown.ofi_slow_penalty", 0]}, 1, 0]}},
            "tipped_to_block":   {"$sum": {
                "$cond": [
                    {"$and": [
                        {"$lt": ["$zones.score_breakdown.ofi_slow_penalty", 0]},
                        {"$lt": ["$zones.score_breakdown.total_score", 2.5]},
                        {"$gte": [
                            {"$add": ["$zones.score_breakdown.total_score",
                                      {"$abs": "$zones.score_breakdown.ofi_slow_penalty"}]},
                            2.5,
                        ]},
                    ]},
                    1, 0,
                ],
            }},
        }},
    ]
    ofi_total = ofi_with_penalty = ofi_tipped = 0
    async for doc in col.aggregate(pipeline_ofi):
        ofi_total       = doc["total"]
        ofi_with_penalty = doc["with_penalty"]
        ofi_tipped      = doc["tipped_to_block"]

    tipped_rate = round(ofi_tipped / ofi_with_penalty * 100, 1) if ofi_with_penalty else 0.0

    n_total_snap = await col.count_documents(_sf)

    return {
        "n_snapshots":        n_total_snap,
        "global_fire_rate":   global_fire_rate,
        "s2_blocked_rate":    s2_blocked_rate,
        "ofi_slow_tipped_rate": tipped_rate,
        "ofi_with_penalty":   ofi_with_penalty,
        "ofi_total_zones":    ofi_total,
        "top_block_reason":   top_reason,
        "top_block_reason_pct": top_reason_pct,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Dimensão 2 — Calibração (trades)
# ══════════════════════════════════════════════════════════════════════════════

def _edge_metrics(rows: List[Dict]) -> Dict:
    """
    Calcula métricas de edge para um subconjunto de trades.

    Retorna:
      n, win_rate, avg_pnl_pts (expectativa), profit_factor, wl_ratio
    """
    n          = sum(r["n"]           for r in rows)
    wins       = sum(r["wins"]        for r in rows)
    sum_pnl    = sum(r["sum_pnl"]     for r in rows)
    gross_prof = sum(r["gross_profit"] for r in rows)
    gross_loss = sum(r["gross_loss"]   for r in rows)
    n_w        = sum(r["n_wins"]       for r in rows)
    n_l        = sum(r["n_losses"]     for r in rows)

    if n == 0:
        return {"n": 0, "win_rate": None, "avg_pnl_pts": None,
                "profit_factor": None, "wl_ratio": None}

    win_rate      = round(wins / n * 100, 1)
    avg_pnl       = round(sum_pnl / n, 3)
    profit_factor = round(gross_prof / gross_loss, 3) if gross_loss > 0 else None
    avg_win_sz    = gross_prof / n_w  if n_w > 0 else 0.0
    avg_loss_sz   = gross_loss / n_l  if n_l > 0 else 0.0
    wl_ratio      = round(avg_win_sz / avg_loss_sz, 3) if avg_loss_sz > 0 else None

    return {
        "n":             n,
        "win_rate":      win_rate,
        "avg_pnl_pts":   avg_pnl,
        "profit_factor": profit_factor,
        "wl_ratio":      wl_ratio,
    }


async def _fetch_dimension_2(db, symbol: str, days: int, session: Optional[str] = None) -> Dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    col    = db["scalp_trades"]

    trade_match: Dict = {
        "symbol":      symbol,
        "exit_reason": {"$in": ["TARGET_HIT", "STOP_HIT"]},
        "created_at":  {"$gte": cutoff.isoformat()},
        **_session_group_filter(session),
    }

    # A5: prefere pnl_pts_weighted (ponderado por lote) quando disponível
    _pnl_field = {"$ifNull": ["$pnl_pts_weighted", {"$ifNull": ["$pnl_pts", 0]}]}

    def _main_pipeline(match: Dict) -> List[Dict]:
        return [
            {"$match": match},
            {"$addFields": {
                "_pnl": _pnl_field,
                "_win": {"$cond": [{"$eq": ["$exit_reason", "TARGET_HIT"]}, 1, 0]},
                "_pos": {"$cond": [{"$gt": [_pnl_field, 0]}, _pnl_field, 0]},
                "_neg": {"$cond": [{"$lt": [_pnl_field, 0]},
                                   {"$abs": _pnl_field}, 0]},
            }},
            {"$group": {
                "_id": {
                    "mode":      {"$ifNull": ["$mode",       "UNKNOWN"]},
                    "quality":   {"$ifNull": ["$s2_quality", "UNKNOWN"]},
                    "zone_type": {"$ifNull": ["$zone_type",  "UNKNOWN"]},
                },
                "n":            {"$sum": 1},
                "wins":         {"$sum": "$_win"},
                "sum_pnl":      {"$sum": "$_pnl"},
                "gross_profit": {"$sum": "$_pos"},
                "gross_loss":   {"$sum": "$_neg"},
                "n_wins":       {"$sum": {"$cond": [{"$gt": ["$_pos", 0]}, 1, 0]}},
                "n_losses":     {"$sum": {"$cond": [{"$gt": ["$_neg", 0]}, 1, 0]}},
            }},
        ]

    # A3: pipeline secundário por regime S1
    _regime_pipeline = [
        {"$match": trade_match},
        {"$addFields": {"_pnl": _pnl_field}},
        {"$group": {
            "_id": {"$ifNull": ["$s1_regime", "DESCONHECIDO"]},
            "n":   {"$sum": 1},
        }},
    ]

    # A7: pipeline de 30d para detecção de drift (paralelo)
    cutoff_30d  = datetime.now(timezone.utc) - timedelta(days=30)
    match_30d   = {**trade_match, "created_at": {"$gte": cutoff_30d.isoformat()}}

    rows, regime_counts, rows_30d = [], {}, []
    async for doc in col.aggregate(_main_pipeline(trade_match)):
        rows.append({
            "mode":         doc["_id"]["mode"],
            "quality":      doc["_id"]["quality"],
            "zone_type":    doc["_id"]["zone_type"],
            "n":            doc["n"],
            "wins":         doc["wins"],
            "sum_pnl":      doc["sum_pnl"],
            "gross_profit": doc["gross_profit"],
            "gross_loss":   doc["gross_loss"],
            "n_wins":       doc["n_wins"],
            "n_losses":     doc["n_losses"],
        })
    async for doc in col.aggregate(_regime_pipeline):
        regime_counts[doc["_id"]] = doc["n"]
    async for doc in col.aggregate(_main_pipeline(match_30d)):
        rows_30d.append({
            "mode": doc["_id"]["mode"], "quality": doc["_id"]["quality"],
            "zone_type": doc["_id"]["zone_type"], "n": doc["n"],
            "wins": doc["wins"], "sum_pnl": doc["sum_pnl"],
            "gross_profit": doc["gross_profit"], "gross_loss": doc["gross_loss"],
            "n_wins": doc["n_wins"], "n_losses": doc["n_losses"],
        })

    overall  = _edge_metrics(rows)
    zones    = _edge_metrics([r for r in rows if r["mode"]    == "ZONES"])
    strong   = _edge_metrics([r for r in rows if r["quality"] == "STRONG"])
    moderate = _edge_metrics([r for r in rows if r["quality"] == "MODERATE"])

    # breakdown por zone_type
    zone_types: Dict[str, Dict] = {}
    for zt in {r["zone_type"] for r in rows}:
        zone_types[zt] = _edge_metrics([r for r in rows if r["zone_type"] == zt])

    # A3: estado de calibração por regime S1
    REGIME_STATUS_MIN = S1_REGIME_MIN_CALIBRAVEL
    regime_calibration: Dict[str, Dict] = {}
    for regime, n in regime_counts.items():
        if n >= REGIME_STATUS_MIN:
            status = "CALIBRÁVEL"
        elif n > 0:
            status = f"INSUFICIENTE — {n} trade(s) em {days}d, parâmetros fixos mantidos"
        else:
            status = "SEM DADOS"
        regime_calibration[regime] = {"n_trades": n, "status": status}

    # A7: drift signal — compara PF 90d vs 30d
    overall_30d = _edge_metrics(rows_30d)
    pf_90d      = overall["profit_factor"]
    pf_30d      = overall_30d["profit_factor"]
    n_30d       = overall_30d["n"]
    if pf_90d is None or pf_30d is None or n_30d < 10:
        drift_signal = "INSUFICIENTE"
    else:
        drift_pct = abs(pf_30d - pf_90d) / max(pf_90d, 0.01) * 100
        drift_signal = "DRIFT" if drift_pct >= 15.0 else "ESTÁVEL"

    return {
        "n_trades":   overall["n"],
        "pnl_source": "weighted",   # indica que usa pnl_pts_weighted com fallback
        # Drift temporal (A7)
        "drift_signal":          drift_signal,
        "pf_90d":                pf_90d,
        "pf_30d":                pf_30d,
        "n_trades_30d":          n_30d,
        # Regime calibration (A3)
        "regime_calibration":    regime_calibration,
        # Globais
        "overall_win_rate":      overall["win_rate"],
        "overall_avg_pnl":       overall["avg_pnl_pts"],
        "overall_profit_factor": overall["profit_factor"],
        "overall_wl_ratio":      overall["wl_ratio"],
        # ZONES
        "zones_win_rate":        zones["win_rate"],
        "zones_avg_pnl":         zones["avg_pnl_pts"],
        "zones_profit_factor":   zones["profit_factor"],
        "zones_wl_ratio":        zones["wl_ratio"],
        "n_zones":               zones["n"],
        # STRONG quality
        "strong_win_rate":       strong["win_rate"],
        "strong_avg_pnl":        strong["avg_pnl_pts"],
        "strong_profit_factor":  strong["profit_factor"],
        "n_strong":              strong["n"],
        # MODERATE quality
        "moderate_win_rate":     moderate["win_rate"],
        "moderate_avg_pnl":      moderate["avg_pnl_pts"],
        "moderate_profit_factor": moderate["profit_factor"],
        "moderate_wl_ratio":     moderate["wl_ratio"],
        "n_moderate":            moderate["n"],
        # zone_type breakdown
        "by_zone_type":          zone_types,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Parâmetros actuais (scalp_config + defaults)
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_current_params(db, session_group: Optional[str] = None) -> Dict:
    """
    Devolve os parâmetros activos.
    Se session_group for "NY" ou "GLOBEX", devolve os params específicos dessa sessão
    com fallback para os globais se não estiverem definidos.
    """
    doc = await db["scalp_config"].find_one({"id": "default"}, {"_id": 0}) or {}
    params: Dict = {}
    for k, v in PARAM_DEFAULTS.items():
        params[k] = float(doc.get(k, v))
    params["min_quality_to_execute"] = doc.get("min_quality_to_execute", "STRONG")

    # Overlay de params específicos da sessão (overriding os globais)
    if session_group in ("NY", "GLOBEX"):
        sp = (doc.get("session_params") or {}).get(session_group, {})
        for k in PARAM_DEFAULTS:
            if k in sp:
                params[k] = float(sp[k])
        if "min_quality_to_execute" in sp:
            params["min_quality_to_execute"] = sp["min_quality_to_execute"]

    params["_session_group"] = session_group  # metadata
    return params


# ══════════════════════════════════════════════════════════════════════════════
# Motor de sugestões combinadas
# ══════════════════════════════════════════════════════════════════════════════

def _clamp(value: float, param: str) -> float:
    lo, hi = PARAM_BOUNDS[param]
    return max(lo, min(hi, value))


def _has_edge(d2_slice: Dict, min_pnl: float = EV_ACCEPT, min_pf: float = PF_ACCEPT) -> bool:
    """
    Edge confirmado: expectativa positiva E profit factor aceitável.

    Hierarquia de critérios:
      1. EV ≥ min_pnl AND PF ≥ min_pf  — critério primário duplo
      2. PF ≥ min_pf AND WR ≥ 45%       — fallback quando avg_pnl_pts não está disponível
      3. EV ≥ min_pnl AND WR ≥ 45%      — fallback quando profit_factor não está disponível

    WR é APENAS fallback (critério 2/3 só actua quando avg_pnl_pts é None).
    Quando EV está disponível mas abaixo do threshold, WR não pode substituí-lo —
    isso garantiria o correcto comportamento com thresholds calibrados por instrumento.
    """
    pnl = d2_slice.get("avg_pnl_pts")
    pf  = d2_slice.get("profit_factor")
    wr  = d2_slice.get("win_rate")
    ev_ok = pnl is not None and pnl >= min_pnl
    pf_ok = pf  is not None and pf  >= min_pf
    # WR como critério auxiliar — apenas quando EV não está disponível (pnl is None)
    wr_ok = pnl is None and wr is not None and wr >= WIN_RATE_ACCEPT
    return (ev_ok and pf_ok) or (ev_ok and wr_ok) or (pf_ok and wr_ok)


def _no_edge(d2_slice: Dict, ev_bad: float = EV_UNACCEPTABLE) -> bool:
    """
    Sem edge: expectativa abaixo do limiar aceitável OU profit factor < 1.

    ev_bad deve ser obtido via _ev_thresholds(symbol)[1] para calibração
    correcta ao instrumento (cobre custos de transacção reais).
    """
    pnl = d2_slice.get("avg_pnl_pts")
    pf  = d2_slice.get("profit_factor")
    wr  = d2_slice.get("win_rate")
    if pnl is not None and pnl < ev_bad:
        return True
    if pf is not None and pf < PF_UNACCEPTABLE:
        return True
    if pnl is None and pf is None and wr is not None and wr < WIN_RATE_UNACCEPTABLE:
        return True
    return False


def _fmt_edge(d2_slice: Dict, n: int) -> str:
    """Formata sumário de métricas para rationale."""
    parts = []
    if d2_slice.get("avg_pnl_pts") is not None:
        parts.append(f"EV={d2_slice['avg_pnl_pts']:+.3f}pts")
    if d2_slice.get("profit_factor") is not None:
        parts.append(f"PF={d2_slice['profit_factor']:.2f}")
    if d2_slice.get("wl_ratio") is not None:
        parts.append(f"W/L={d2_slice['wl_ratio']:.2f}")
    if d2_slice.get("win_rate") is not None:
        parts.append(f"WR={d2_slice['win_rate']}%")
    parts.append(f"n={n}")
    return " | ".join(parts)


def _suggest(d1: Dict, d2: Dict, current: Dict, max_delta: float,
             symbol: str = "MNQ") -> List[Dict]:
    suggestions: List[Dict] = []

    # Thresholds de EV calibrados ao instrumento
    ev_accept, ev_bad = _ev_thresholds(symbol)

    fire_rate   = d1["global_fire_rate"]
    tipped_rate = d1["ofi_slow_tipped_rate"]
    n_trades    = d2["n_trades"]
    n_zones     = d2["n_zones"]
    n_moderate  = d2["n_moderate"]
    n_snaps     = d1["n_snapshots"]

    fade_cur  = current["zones_ofi_slow_fade_thresh"]
    mom_cur   = current["zones_ofi_slow_momentum_thresh"]
    score_cur = current["zones_score_moderate_thresh"]
    quality   = current["min_quality_to_execute"]

    # slices para reutilização nas regras de inferência
    zones_slice    = {"avg_pnl_pts": d2["zones_avg_pnl"],    "profit_factor": d2["zones_profit_factor"],
                      "wl_ratio": d2["zones_wl_ratio"],       "win_rate": d2["zones_win_rate"]}
    overall_slice  = {"avg_pnl_pts": d2["overall_avg_pnl"],  "profit_factor": d2["overall_profit_factor"],
                      "wl_ratio": d2["overall_wl_ratio"],     "win_rate": d2["overall_win_rate"]}
    moderate_slice = {"avg_pnl_pts": d2["moderate_avg_pnl"], "profit_factor": d2["moderate_profit_factor"],
                      "wl_ratio": d2["moderate_wl_ratio"],    "win_rate": d2["moderate_win_rate"]}

    # ── Regra 1: OFI Slow está over-blocking + edge confirmado em ZONES ───────
    # D1: alto tipping rate → OFI Slow penaliza excessivamente candidatos
    # D2: expectativa positiva + PF aceitável → os sinais que passam têm edge real
    if (tipped_rate > OFI_TIP_THRESHOLD
            and n_snaps >= MIN_SNAPSHOTS
            and n_zones >= MIN_TRADES_BASIC
            and _has_edge(zones_slice, min_pnl=ev_accept)):

        edge_str = _fmt_edge(zones_slice, n_zones)

        delta_fade = min(0.05, max_delta)
        new_fade   = _clamp(fade_cur - delta_fade, "zones_ofi_slow_fade_thresh")
        if new_fade != fade_cur:
            suggestions.append({
                "param":     "zones_ofi_slow_fade_thresh",
                "current":   fade_cur,
                "suggested": round(new_fade, 3),
                "delta":     round(new_fade - fade_cur, 3),
                "rationale": (
                    f"OFI Slow está a bloquear {tipped_rate}% dos candidatos penalizados. "
                    f"D2 confirma edge real em ZONES ({edge_str}), portanto o bloqueio está a "
                    f"eliminar oportunidades lucrativas. Relaxar threshold fade de {fade_cur} "
                    f"para {round(new_fade,3)} — delta máximo seguro por run."
                ),
                "confidence": "high" if n_zones >= 20 else "medium",
                "dimension":  "D1×D2",
            })

        delta_mom = min(0.03, max_delta)
        new_mom   = _clamp(mom_cur - delta_mom, "zones_ofi_slow_momentum_thresh")
        if new_mom != mom_cur:
            suggestions.append({
                "param":     "zones_ofi_slow_momentum_thresh",
                "current":   mom_cur,
                "suggested": round(new_mom, 3),
                "delta":     round(new_mom - mom_cur, 3),
                "rationale": (
                    f"Ajuste conservador complementar ao fade threshold: momentum de {mom_cur} "
                    f"para {round(new_mom,3)}. Mantém exigência diferenciada por tipo de zona."
                ),
                "confidence": "medium",
                "dimension":  "D1×D2",
            })

    # ── Regra 2: Fire rate baixo + edge global confirmado → score excessivo ───
    # D1: sistema bloqueia demasiado (< 20% fire rate)
    # D2: trades que passam têm expectativa + PF positivos — logo o threshold está alto
    if (fire_rate < FIRE_RATE_LOW_THRESH
            and n_snaps >= MIN_SNAPSHOTS
            and n_trades >= MIN_TRADES_SCORE
            and _has_edge(overall_slice, min_pnl=ev_accept)):

        edge_str    = _fmt_edge(overall_slice, n_trades)
        delta_score = min(0.15, max_delta)
        new_score   = _clamp(score_cur - delta_score, "zones_score_moderate_thresh")
        if new_score != score_cur:
            suggestions.append({
                "param":     "zones_score_moderate_thresh",
                "current":   score_cur,
                "suggested": round(new_score, 3),
                "delta":     round(new_score - score_cur, 3),
                "rationale": (
                    f"Fire rate de {fire_rate}% indica over-blocking. "
                    f"D2 confirma edge nos trades executados ({edge_str}), o que sugere que "
                    f"o score threshold moderado de {score_cur} bloqueia sinais com edge real. "
                    f"Reduzir para {round(new_score,3)} aumenta frequência sem degradar qualidade."
                ),
                "confidence": "high" if n_trades >= 25 else "medium",
                "dimension":  "D1×D2",
            })

    # ── Regra 3: Fire rate alto + sem edge → score permissivo demais ──────────
    # D1: sistema dispara demasiado (> 55% fire rate)
    # D2: expectativa negativa OU PF < 1 → os sinais executados não têm edge
    if (fire_rate > FIRE_RATE_HIGH_THRESH
            and n_snaps >= MIN_SNAPSHOTS
            and n_trades >= MIN_TRADES_SCORE
            and _no_edge(overall_slice, ev_bad=ev_bad)):

        ev_str      = f"{d2['overall_avg_pnl']:+.3f}pts" if d2["overall_avg_pnl"] is not None else "N/D"
        pf_str      = f"{d2['overall_profit_factor']:.2f}" if d2["overall_profit_factor"] is not None else "N/D"
        delta_score = min(0.15, max_delta)
        new_score   = _clamp(score_cur + delta_score, "zones_score_moderate_thresh")
        if new_score != score_cur:
            suggestions.append({
                "param":     "zones_score_moderate_thresh",
                "current":   score_cur,
                "suggested": round(new_score, 3),
                "delta":     round(new_score - score_cur, 3),
                "rationale": (
                    f"Fire rate de {fire_rate}% é excessivo e D2 mostra ausência de edge "
                    f"(EV={ev_str}, PF={pf_str}, n={n_trades}). "
                    f"Sistema está a executar sinais sem expectativa positiva. "
                    f"Elevar score threshold de {score_cur} para {round(new_score,3)} aumenta selectividade."
                ),
                "confidence": "high" if n_trades >= 25 else "medium",
                "dimension":  "D1×D2",
            })

    # ── Regra 4: MODERATE tem edge real + gate em STRONG → relaxar gate ───────
    # D2 puro: edge em sinais MODERATE demonstrado (EV + PF)
    # O gate STRONG está a desperdiçar sinais lucrativos
    if (n_moderate >= MIN_TRADES_QUALITY_GATE
            and quality == "STRONG"
            and _has_edge(moderate_slice, min_pnl=ev_accept, min_pf=PF_ACCEPT)):

        edge_str = _fmt_edge(moderate_slice, n_moderate)
        suggestions.append({
            "param":     "min_quality_to_execute",
            "current":   "STRONG",
            "suggested": "MODERATE",
            "delta":     None,
            "rationale": (
                f"Sinais MODERATE têm edge positivo confirmado ({edge_str}). "
                f"O gate STRONG está a eliminar oportunidades com expectativa real. "
                f"Note: esta mudança aumenta o volume de sinais — monitorize drawdown."
            ),
            "confidence": "high" if n_moderate >= 20 else "medium",
            "dimension":  "D2",
        })

    # ── Regra 5: MODERATE sem edge + gate em MODERATE → apertar gate ──────────
    # D2 puro: sinais MODERATE têm EV negativo ou PF < 1
    if (n_moderate >= MIN_TRADES_QUALITY_GATE
            and quality == "MODERATE"
            and _no_edge(moderate_slice, ev_bad=ev_bad)):

        ev_str  = f"{d2['moderate_avg_pnl']:+.3f}pts" if d2["moderate_avg_pnl"] is not None else "N/D"
        pf_str  = f"{d2['moderate_profit_factor']:.2f}" if d2["moderate_profit_factor"] is not None else "N/D"
        suggestions.append({
            "param":     "min_quality_to_execute",
            "current":   "MODERATE",
            "suggested": "STRONG",
            "delta":     None,
            "rationale": (
                f"Sinais MODERATE não têm edge real (EV={ev_str}, PF={pf_str}, n={n_moderate}). "
                f"Elevar o gate para STRONG eliminará trades com expectativa negativa."
            ),
            "confidence": "high" if n_moderate >= 20 else "medium",
            "dimension":  "D2",
        })

    # ── Regra 6 (A6): OFI Fast — fire rate baixo + block reason dominado por OFI fast ──
    # D1: bloco principal é OFI fast + fire_rate baixo
    # D2: edge confirmado — logo o threshold está demasiado exigente
    ofi_fast_cur   = current.get("zones_ofi_fast_min",   PARAM_DEFAULTS["zones_ofi_fast_min"])
    ofi_break_cur  = current.get("zones_ofi_break_min",  PARAM_DEFAULTS["zones_ofi_break_min"])
    top_reason     = d1.get("top_block_reason") or ""
    top_reason_pct = d1.get("top_block_reason_pct") or 0.0

    if (("ofi_fast" in top_reason.lower() or "OFI fast" in top_reason)
            and top_reason_pct > 30.0
            and fire_rate < FIRE_RATE_LOW_THRESH
            and n_snaps >= MIN_SNAPSHOTS
            and n_trades >= MIN_TRADES_SCORE
            and _has_edge(overall_slice, min_pnl=ev_accept)):

        delta_fast = min(0.02, max_delta)
        new_fast   = _clamp(ofi_fast_cur - delta_fast, "zones_ofi_fast_min")
        if new_fast != ofi_fast_cur:
            suggestions.append({
                "param":     "zones_ofi_fast_min",
                "current":   ofi_fast_cur,
                "suggested": round(new_fast, 3),
                "delta":     round(new_fast - ofi_fast_cur, 3),
                "rationale": (
                    f"OFI fast é a razão de bloqueio dominante ({top_reason_pct:.0f}% dos bloqueios). "
                    f"Fire rate de {fire_rate:.1f}% com edge confirmado em D2 indica threshold "
                    f"excessivamente restritivo. Reduzir zones_ofi_fast_min de {ofi_fast_cur} para "
                    f"{round(new_fast, 3)} — delta mínimo seguro para manter selecividade."
                ),
                "confidence": "medium",
                "dimension":  "D1×D2",
                "group":      "OFI_FAST",
            })

        # Invariante: ofi_break_min > ofi_fast_min — ajustar proporcionalmente se necessário
        if new_fast < ofi_fast_cur and ofi_break_cur <= new_fast:
            new_break = _clamp(new_fast + 0.10, "zones_ofi_break_min")
            suggestions.append({
                "param":     "zones_ofi_break_min",
                "current":   ofi_break_cur,
                "suggested": round(new_break, 3),
                "delta":     round(new_break - ofi_break_cur, 3),
                "rationale": (
                    f"Ajuste de invariante do Grupo 3: zones_ofi_break_min deve ser "
                    f"> zones_ofi_fast_min. Novo fast_min={round(new_fast,3)} exige "
                    f"break_min ≥ {round(new_fast+0.10,3)}."
                ),
                "confidence": "high",
                "dimension":  "INVARIANTE",
                "group":      "OFI_FAST",
            })

    # ── A4: Constraint invariante Grupo 1 (score: strong > moderate) ─────────────
    # Se ambos os thresholds de score forem sugeridos no mesmo ciclo,
    # valida que o strong sugerido > moderate sugerido.
    sug_by_param = {s["param"]: s for s in suggestions}
    if "zones_score_moderate_thresh" in sug_by_param and "zones_score_strong_thresh" in sug_by_param:
        mod_sug   = sug_by_param["zones_score_moderate_thresh"]["suggested"]
        strong_sug = sug_by_param["zones_score_strong_thresh"]["suggested"]
        if strong_sug <= mod_sug:
            # Corrige: garante separação mínima de 0.50
            corrected = round(mod_sug + 0.50, 3)
            corrected = min(corrected, PARAM_BOUNDS["zones_score_strong_thresh"][1])
            for s in suggestions:
                if s["param"] == "zones_score_strong_thresh":
                    s["suggested"] = corrected
                    s["delta"]     = round(corrected - s["current"], 3)
                    s["rationale"] += (
                        f" [Corrigido por invariante Grupo 1: strong({corrected}) > moderate({mod_sug}).]"
                    )
    elif "zones_score_moderate_thresh" in sug_by_param and "zones_score_strong_thresh" not in sug_by_param:
        # Se só moderate mudou, valida contra strong_thresh actual
        mod_sug    = sug_by_param["zones_score_moderate_thresh"]["suggested"]
        strong_act = current.get("zones_score_strong_thresh", PARAM_DEFAULTS["zones_score_strong_thresh"])
        if mod_sug >= strong_act:
            # Remove a sugestão de moderate para não violar invariante
            suggestions = [s for s in suggestions if s["param"] != "zones_score_moderate_thresh"]
            logger.warning(
                f"_suggest: sugestão zones_score_moderate_thresh={mod_sug} removida — "
                f"violaria invariante (strong_thresh actual={strong_act})"
            )

    return suggestions


def _net_assessment(d1: Dict, d2: Dict, symbol: str = "MNQ") -> str:
    if d1["n_snapshots"] < MIN_SNAPSHOTS or d2["n_trades"] < MIN_TRADES_BASIC:
        return "INSUFFICIENT_DATA"
    fire  = d1["global_fire_rate"]
    ev_accept, ev_bad = _ev_thresholds(symbol)
    pf    = d2["overall_profit_factor"]
    ev    = d2["overall_avg_pnl"]
    _sl   = {"avg_pnl_pts": ev, "profit_factor": pf, "win_rate": d2["overall_win_rate"]}
    has_e = _has_edge(_sl, min_pnl=ev_accept)
    no_e  = _no_edge( _sl, ev_bad=ev_bad)

    if fire < FIRE_RATE_LOW_THRESH:
        return "OVER_BLOCKING"
    if fire > FIRE_RATE_HIGH_THRESH and no_e:
        return "UNDER_FILTERING"
    if fire > FIRE_RATE_HIGH_THRESH and not has_e:
        return "UNDER_FILTERING"
    return "BALANCED"


# ══════════════════════════════════════════════════════════════════════════════
# API pública — análise
# ══════════════════════════════════════════════════════════════════════════════

def _build_single_analysis(
    symbol: str, session: Optional[str], days_diag: int, days_cal: int,
    d1: Dict, d2: Dict, current: Dict, max_delta: float,
) -> Dict:
    """Monta o dict de análise combinada para uma sessão/grupo."""
    sg = current.pop("_session_group", None)
    suggestions = _suggest(d1, d2, current, max_delta, symbol=symbol)
    # Tagueia sugestões com o grupo de sessão para routing correcto no apply
    if sg in ("NY", "GLOBEX"):
        for s in suggestions:
            s["session_group"] = sg
    assessment = _net_assessment(d1, d2, symbol=symbol)
    ev_accept, ev_bad = _ev_thresholds(symbol)
    return {
        "symbol":        symbol,
        "session":       session,
        "session_group": sg,
        "days_diag":     days_diag,
        "days_cal":      days_cal,
        "sample": {
            "n_snapshots": d1["n_snapshots"],
            "n_trades":    d2["n_trades"],
            "sufficient":  d1["n_snapshots"] >= MIN_SNAPSHOTS and d2["n_trades"] >= MIN_TRADES_BASIC,
        },
        "ev_thresholds": {
            "accept":      ev_accept,
            "unacceptable": ev_bad,
            "source":      "instrument" if symbol.upper() in EV_THRESHOLDS else "fallback",
        },
        "dimension_1":    d1,
        "dimension_2":    d2,
        "current_params": current,
        "suggestions":    suggestions,
        "net_assessment": assessment,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    }


async def get_combined_analysis(
    db,
    symbol:    str = "MNQ",
    days_diag: int = 30,
    days_cal:  int = 90,
    max_delta: float = 0.05,
    session:   Optional[str] = None,
) -> Dict:
    """
    session=None  → modo ALL: análise dupla NY + Globex em paralelo.
    session="NY"  → análise apenas da sessão NY com params específicos NY.
    session="GLOBEX" → análise apenas Globex com params específicos Globex.
    session=granular → análise filtrada por label (sem params específicos de grupo).
    """
    from services.trading_calendar_service import VALID_SESSION_GROUPS

    # Determina o grupo de sessão para leitura de params
    sg = session if session in VALID_SESSION_GROUPS else None

    if session is None:
        # Modo ALL — análise paralela NY + Globex, cada uma com os seus params
        (d1_ny, d2_ny, p_ny), (d1_gx, d2_gx, p_gx) = await asyncio.gather(
            asyncio.gather(
                _fetch_dimension_1(db, symbol, days_diag, session="NY"),
                _fetch_dimension_2(db, symbol, days_cal,  session="NY"),
                _fetch_current_params(db, session_group="NY"),
            ),
            asyncio.gather(
                _fetch_dimension_1(db, symbol, days_diag, session="GLOBEX"),
                _fetch_dimension_2(db, symbol, days_cal,  session="GLOBEX"),
                _fetch_current_params(db, session_group="GLOBEX"),
            ),
        )
        now = datetime.now(timezone.utc).isoformat()
        return {
            "symbol":       symbol,
            "session":      None,
            "mode":         "ALL",
            "days_diag":    days_diag,
            "days_cal":     days_cal,
            "generated_at": now,
            "ny":     _build_single_analysis(symbol, "NY",     days_diag, days_cal, d1_ny, d2_ny, p_ny, max_delta),
            "globex": _build_single_analysis(symbol, "GLOBEX", days_diag, days_cal, d1_gx, d2_gx, p_gx, max_delta),
        }

    # Modo sessão específica
    d1, d2, current = await asyncio.gather(
        _fetch_dimension_1(db, symbol, days_diag, session=session),
        _fetch_dimension_2(db, symbol, days_cal,  session=session),
        _fetch_current_params(db, session_group=sg),
    )
    return _build_single_analysis(symbol, session, days_diag, days_cal, d1, d2, current, max_delta)


# ══════════════════════════════════════════════════════════════════════════════
# Apply — aplica sugestões ao scalp_config e globals scalp_zones
# ══════════════════════════════════════════════════════════════════════════════

async def apply_combined_suggestions(
    db,
    suggestions: List[Dict],
    dry_run: bool = True,
) -> Dict:
    applied = []
    skipped = []
    now     = datetime.now(timezone.utc).isoformat()

    if dry_run:
        return {
            "dry_run":    True,
            "applied":    [],
            "skipped":    [],
            "would_apply": [s["param"] for s in suggestions],
            "applied_at": now,
        }

    # Agrupa sugestões por destino: globais vs por grupo de sessão
    global_update:   Dict[str, Any] = {"updated_at": now, "combined_tuned_at": now}
    session_updates: Dict[str, Dict[str, Any]] = {}  # {"NY": {...}, "GLOBEX": {...}}

    for s in suggestions:
        param      = s["param"]
        suggested  = s["suggested"]
        sg         = s.get("session_group")  # "NY", "GLOBEX" ou None (global)

        valid_param = (param in PARAM_DEFAULTS) or (param == "min_quality_to_execute")
        in_bounds   = True
        if param in PARAM_BOUNDS:
            lo, hi    = PARAM_BOUNDS[param]
            in_bounds = (lo <= suggested <= hi)

        if not valid_param:
            skipped.append({"param": param, "session_group": sg, "reason": "parâmetro desconhecido"})
            continue
        if not in_bounds:
            lo, hi = PARAM_BOUNDS[param]
            skipped.append({"param": param, "session_group": sg, "reason": f"fora dos bounds [{lo},{hi}]"})
            continue

        if sg in ("NY", "GLOBEX"):
            # Param específico de sessão
            if sg not in session_updates:
                session_updates[sg] = {}
            session_updates[sg][param] = suggested
            applied.append({"param": param, "session_group": sg, "new_value": suggested})

            # Hot-reload no scalp_zones._SESSION_PARAMS
            try:
                import services.scalp_zones as sz
                sz.update_session_params(sg, {param: suggested})
                logger.info(f"CombinedService: session_params[{sg}].{param} → {suggested}")
            except Exception as e:
                logger.warning(f"CombinedService: hot-reload session_params falhou: {e}")
        else:
            # Param global
            global_update[param] = suggested
            applied.append({"param": param, "session_group": None, "new_value": suggested})

            # Hot-reload global em scalp_zones (para retrocompatibilidade)
            try:
                import services.scalp_zones as sz
                _ZONE_MAP = {
                    "zones_ofi_slow_fade_thresh":     "OFI_SLOW_BLOCK_FADE",
                    "zones_ofi_slow_momentum_thresh": "OFI_SLOW_BLOCK_MOMENTUM",
                    "zones_ofi_fast_min":             "OFI_FAST_MIN",
                    "zones_ofi_break_min":            "OFI_BREAK_MIN",
                }
                if param in _ZONE_MAP:
                    setattr(sz, _ZONE_MAP[param], suggested)
                    logger.info(f"CombinedService: global {_ZONE_MAP[param]} → {suggested}")
            except Exception as e:
                logger.warning(f"CombinedService: hot-reload global falhou: {e}")

    # Persiste no MongoDB
    if len(global_update) > 2:  # além de updated_at e combined_tuned_at
        await db["scalp_config"].update_one(
            {"id": "default"},
            {"$set": global_update},
            upsert=True,
        )

    for sg, sp in session_updates.items():
        mongo_set = {f"session_params.{sg}.{k}": v for k, v in sp.items()}
        mongo_set["updated_at"] = now
        await db["scalp_config"].update_one(
            {"id": "default"},
            {"$set": mongo_set},
            upsert=True,
        )

    return {
        "dry_run":    False,
        "applied":    applied,
        "skipped":    skipped,
        "applied_at": now,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Schedule — CRUD
# ══════════════════════════════════════════════════════════════════════════════

_combined_task = None


def _dt_to_iso(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _compute_next_run(data: Dict) -> datetime:
    freq    = data.get("frequency", "daily")
    now     = datetime.now(timezone.utc)
    hour    = int(data.get("hour_utc", 6))
    dow     = int(data.get("day_of_week", 6))     # 0=Mon … 6=Sun
    custom  = int(data.get("custom_hours", 24))

    if freq == "custom_hours":
        return now + timedelta(hours=custom)

    today_at = now.replace(hour=hour, minute=0, second=0, microsecond=0)

    if freq == "daily":
        t = today_at if today_at > now else today_at + timedelta(days=1)
        return t

    if freq == "weekly":
        days_ahead = (dow - now.weekday()) % 7
        if days_ahead == 0 and today_at <= now:
            days_ahead = 7
        return today_at + timedelta(days=days_ahead)

    return now + timedelta(hours=custom)


async def get_combined_schedule(db) -> Optional[Dict]:
    doc = await db["scalp_combined_schedule"].find_one({}, {"_id": 0})
    if not doc:
        return None
    for k in ("created_at", "updated_at", "next_run_at", "last_run_at"):
        if k in doc:
            doc[k] = _dt_to_iso(doc[k])
    return doc


async def update_combined_schedule(db, data: Dict) -> Dict:
    global _combined_task

    now      = datetime.now(timezone.utc)
    next_run = _compute_next_run(data)

    doc = {
        "active":           data.get("enabled", True),
        "frequency":        data.get("frequency", "daily"),
        "custom_hours":     int(data.get("custom_hours", 24)),
        "day_of_week":      int(data.get("day_of_week", 6)),
        "hour_utc":         int(data.get("hour_utc", 6)),
        "symbol":           data.get("symbol", "MNQ"),
        "days_diag":        int(data.get("days_diag", 30)),
        "days_cal":         int(data.get("days_cal", 90)),
        "max_delta":        float(data.get("max_delta", 0.05)),
        "min_snapshots":    int(data.get("min_snapshots", MIN_SNAPSHOTS)),
        "min_trades":       int(data.get("min_trades", MIN_TRADES_BASIC)),
        "auto_apply":       bool(data.get("auto_apply", False)),
        "created_at":       now,
        "updated_at":       now,
        "next_run_at":      next_run,
        "last_run_at":      None,
    }

    await db["scalp_combined_schedule"].delete_many({})
    await db["scalp_combined_schedule"].insert_one(doc)

    if _combined_task and not _combined_task.done():
        _combined_task.cancel()
    if doc["active"]:
        _combined_task = asyncio.create_task(_combined_loop(db))
        logger.info(f"CombinedScheduler: iniciado. Próximo run: {next_run.isoformat()}")

    doc.pop("_id", None)
    for k in ("created_at", "updated_at", "next_run_at"):
        doc[k] = _dt_to_iso(doc[k])
    doc["last_run_at"] = None
    return doc


async def disable_combined_schedule(db) -> None:
    global _combined_task
    await db["scalp_combined_schedule"].update_many({}, {"$set": {"active": False}})
    if _combined_task and not _combined_task.done():
        _combined_task.cancel()
        _combined_task = None
    logger.info("CombinedScheduler: desactivado")


async def start_combined_scheduler(db) -> None:
    global _combined_task
    # Garante índices e existência da colecção sempre (independente de schedule)
    try:
        await db["scalp_combined_history"].create_index(
            "run_at",
            expireAfterSeconds=180 * 86400,
            name="ttl_180d",
            background=True,
        )
        await db["scalp_combined_history"].create_index(
            [("symbol", 1), ("run_at", -1)],
            name="sym_run_at",
            background=True,
        )
    except Exception:
        pass
    logger.info("CombinedScheduler: índices de histórico criados")
    doc = await db["scalp_combined_schedule"].find_one({"active": True})
    if not doc:
        logger.info("CombinedScheduler: sem schedule activo")
        return
    _combined_task = asyncio.create_task(_combined_loop(db))
    logger.info("CombinedScheduler: retomado no startup")


# ══════════════════════════════════════════════════════════════════════════════
# Loop e execução agendada
# ══════════════════════════════════════════════════════════════════════════════

_combined_running = False


def _make_aware(dt) -> Optional[datetime]:
    """Converte datetime naive (vindo do MongoDB) para UTC-aware."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    return None


async def _combined_loop(db) -> None:
    global _combined_running
    try:
        while True:
            schedule = await db["scalp_combined_schedule"].find_one({"active": True})
            if not schedule:
                logger.info("CombinedScheduler: desactivado, parando loop")
                break

            now      = datetime.now(timezone.utc)
            next_run = _make_aware(schedule.get("next_run_at"))

            if next_run is not None and now >= next_run:
                if not _combined_running:
                    _combined_running = True
                    try:
                        await _execute_combined_run(db, schedule)
                    finally:
                        _combined_running = False

            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"CombinedScheduler loop error: {e}", exc_info=True)


def _scheduler_gate_check(
    analysis: Dict,
    schedule: Dict,
    now: datetime,
) -> Tuple[bool, str]:
    """
    Gate triplo antes de auto_apply no scheduler.

    1. Modo ALL bloqueado — não é possível determinar sessão de destino.
    2. N mínimo de trades — verifica contra schedule.min_trades.
    3. Janela temporal — params NY só aplicáveis após 16:15 ET (pós-RTH).

    Devolve (blocked: bool, reason: str).
    """
    # Critério 1: modo ALL é ambíguo para auto_apply
    if analysis.get("mode") == "ALL":
        return True, ("auto_apply bloqueado em modo ALL — seleccione sessão NY ou GLOBEX "
                      "para aplicação segura de parâmetros.")

    # Critério 2: N mínimo de trades
    min_trades_gate = int(schedule.get("min_trades", MIN_TRADES_BASIC))
    n_trades = analysis.get("sample", {}).get("n_trades", 0)
    if n_trades < min_trades_gate:
        return True, (f"auto_apply bloqueado: apenas {n_trades} trades — "
                      f"mínimo exigido={min_trades_gate}. "
                      f"Parâmetros fixos mantidos até N suficiente.")

    # Critério 3: Janela temporal — parâmetros NY só após RTH (16:15 ET, DST-aware)
    session_group = analysis.get("session_group") or analysis.get("session")
    if session_group == "NY":
        # Usa ZoneInfo para resolver correctamente EDT (UTC-4) e EST (UTC-5)
        now_et    = now.astimezone(_ET)
        et_t      = now_et.time()
        # Pós-RTH: após 16:15 ET ou antes das 06:00 ET (janela overnight segura)
        in_post_rth = et_t >= dt_time(16, 15) or et_t < dt_time(6, 0)
        if not in_post_rth:
            return True, (f"auto_apply NY bloqueado: hora actual {now_et.strftime('%H:%M')} ET "
                          f"está dentro do horário RTH. Aplicação automática de params NY "
                          f"só permitida após 16:15 ET.")

    return False, ""


async def _execute_combined_run(db, schedule: Dict) -> None:
    symbol     = schedule.get("symbol", "MNQ")
    days_diag  = int(schedule.get("days_diag", 30))
    days_cal   = int(schedule.get("days_cal",  90))
    max_delta  = float(schedule.get("max_delta",  0.05))
    auto_apply = bool(schedule.get("auto_apply", False))
    now        = datetime.now(timezone.utc)

    logger.info(f"CombinedScheduler: a executar análise combinada ({symbol})")

    try:
        analysis = await get_combined_analysis(db, symbol, days_diag, days_cal, max_delta)

        apply_result = None
        gate_blocked, gate_reason = False, ""

        if auto_apply:
            gate_blocked, gate_reason = _scheduler_gate_check(analysis, schedule, now)
            if gate_blocked:
                logger.warning(f"CombinedScheduler: {gate_reason}")

        if auto_apply and not gate_blocked:
            sug = analysis.get("suggestions") or []
            if not sug and analysis.get("mode") == "ALL":
                # modo ALL: agrega sugestões dos dois painéis
                sug = (analysis.get("ny",     {}).get("suggestions") or []) + \
                      (analysis.get("globex", {}).get("suggestions") or [])
            if sug:
                apply_result = await apply_combined_suggestions(
                    db, sug, dry_run=False,
                )
                logger.info(
                    f"CombinedScheduler: auto_apply → "
                    f"{len(apply_result['applied'])} aplicados, "
                    f"{len(apply_result['skipped'])} ignorados"
                )

        # Gravar no histórico (inclui razão de gate se bloqueado)
        hist = {
            "symbol":       symbol,
            "run_at":       now,
            "analysis":     analysis,
            "apply_result": apply_result,
            "auto_applied": auto_apply and bool(apply_result and apply_result["applied"]),
            "gate_blocked": gate_blocked,
            "gate_reason":  gate_reason if gate_blocked else None,
        }
        await db["scalp_combined_history"].insert_one(hist)

        # Calcular próximo run e actualizar schedule
        next_run = _compute_next_run(schedule)
        await db["scalp_combined_schedule"].update_one(
            {"active": True},
            {"$set": {"last_run_at": now, "next_run_at": next_run}},
        )
        logger.info(f"CombinedScheduler: run concluído. Próximo: {next_run.isoformat()}")

    except Exception as e:
        logger.error(f"CombinedScheduler: erro no run: {e}", exc_info=True)


async def run_combined_now(db, symbol: str = "MNQ") -> Dict:
    """Trigger manual imediato. Usa o schedule activo se existir."""
    schedule = await db["scalp_combined_schedule"].find_one({"active": True}) or {}
    days_diag  = int(schedule.get("days_diag",  30))
    days_cal   = int(schedule.get("days_cal",   90))
    max_delta  = float(schedule.get("max_delta", 0.05))
    auto_apply = bool(schedule.get("auto_apply", False))

    analysis = await get_combined_analysis(db, symbol, days_diag, days_cal, max_delta)

    apply_result  = None
    gate_blocked, gate_reason = False, ""

    if auto_apply:
        gate_blocked, gate_reason = _scheduler_gate_check(
            analysis, schedule, datetime.now(timezone.utc)
        )
        if gate_blocked:
            logger.warning(f"run_combined_now gate: {gate_reason}")

    if auto_apply and not gate_blocked:
        sug = analysis.get("suggestions") or []
        if not sug and analysis.get("mode") == "ALL":
            sug = (analysis.get("ny",     {}).get("suggestions") or []) + \
                  (analysis.get("globex", {}).get("suggestions") or [])
        if sug:
            apply_result = await apply_combined_suggestions(
                db, sug, dry_run=False,
            )

    now = datetime.now(timezone.utc)
    hist = {
        "symbol":       symbol,
        "run_at":       now,
        "analysis":     analysis,
        "apply_result": apply_result,
        "auto_applied": auto_apply and bool(apply_result and apply_result["applied"]),
        "gate_blocked": gate_blocked,
        "gate_reason":  gate_reason if gate_blocked else None,
        "triggered":    "manual",
    }
    await db["scalp_combined_history"].insert_one(hist)

    if schedule:
        await db["scalp_combined_schedule"].update_one(
            {"active": True},
            {"$set": {"last_run_at": now}},
        )

    sug_list    = analysis.get("suggestions") or []
    n_sug       = len(sug_list)
    was_applied = bool(apply_result and apply_result.get("applied"))
    return {
        "status":         "ok",
        "n_suggestions":  n_sug,
        "applied":        was_applied,
        "auto_applied":   auto_apply and was_applied,
        "gate_blocked":   gate_blocked,
        "gate_reason":    gate_reason if gate_blocked else None,
        "net_assessment": analysis.get("net_assessment"),
        "analysis":       analysis,
        "apply_result":   apply_result,
        "triggered_at":   now.isoformat(),
    }


async def get_combined_history(db, symbol: str = "MNQ", limit: int = 20) -> List[Dict]:
    rows = []
    cursor = (
        db["scalp_combined_history"]
        .find({"symbol": symbol}, {"_id": 0, "analysis.dimension_1": 1, "analysis.dimension_2": 1,
                                    "analysis.net_assessment": 1, "analysis.suggestions": 1,
                                    "analysis.generated_at": 1, "apply_result": 1,
                                    "auto_applied": 1, "run_at": 1, "triggered": 1})
        .sort("run_at", -1)
        .limit(limit)
    )
    async for doc in cursor:
        doc["run_at"] = _dt_to_iso(doc.get("run_at"))
        rows.append(doc)
    return rows
