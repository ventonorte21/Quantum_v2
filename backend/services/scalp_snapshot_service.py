"""
Scalp Snapshot Service — Walk-Forward Data Collection para Auto-Tune

Grava o estado completo do ScalpEngine (S1/S2/S3 + indicadores + zonas + níveis)
a cada 30s durante horas de mercado para MNQ e MES.

Colecção MongoDB: scalp_snapshots
TTL: 90 dias (volume menor que Base pois não é 1-min)
Tamanho estimado: ~50 MB/mês para 2 símbolos @ 30s
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from services.trading_calendar_service import get_session_label

logger = logging.getLogger("scalp_snapshot_service")

SCALP_SNAPSHOT_SYMBOLS   = ["MNQ", "MES"]
SCALP_SNAPSHOT_INTERVAL  = 30          # segundos
SCALP_SNAPSHOT_TTL       = 90 * 24 * 3600  # 90 dias


# ── Índices ──────────────────────────────────────────────────────────────────

async def ensure_scalp_snapshot_indexes(database):
    col = database.scalp_snapshots
    try:
        await col.create_index(
            "recorded_at",
            expireAfterSeconds=SCALP_SNAPSHOT_TTL,
            name="ttl_90d",
        )
        await col.create_index(
            [("symbol", 1), ("recorded_at", -1)],
            name="sym_time_desc",
        )
        await col.create_index(
            [("symbol", 1), ("s3_action", 1), ("recorded_at", -1)],
            name="sym_action_time",
        )
        await col.create_index(
            [("symbol", 1), ("s1_regime", 1), ("recorded_at", -1)],
            name="sym_regime_time",
        )
        await col.create_index(
            [("symbol", 1), ("mode", 1), ("recorded_at", -1)],
            name="sym_mode_time",
        )
        await col.create_index(
            [("symbol", 1), ("zone_quality", 1), ("recorded_at", -1)],
            name="sym_quality_time",
        )
        await col.create_index(
            [("symbol", 1), ("macro_context.gamma_regime", 1), ("recorded_at", -1)],
            name="sym_gamma_regime_time",
        )
        await col.create_index(
            [("symbol", 1), ("macro_context.ts_hard_stop", 1), ("recorded_at", -1)],
            name="sym_ts_hard_stop_time",
        )
        await col.create_index(
            [("symbol", 1), ("session_label", 1), ("recorded_at", -1)],
            name="sym_session_time",
        )
        await col.create_index(
            [("symbol", 1), ("scalp_status", 1), ("recorded_at", -1)],
            name="sym_status_time",
        )
        await col.create_index(
            [("symbol", 1), ("zones.day_regime", 1), ("recorded_at", -1)],
            name="sym_day_regime_time",
        )
        await col.create_index(
            [("symbol", 1), ("macro_context.gamma_short_suppressed", 1), ("recorded_at", -1)],
            name="sym_gamma_short_suppressed_time",
        )
        # Param Audit: queries por mode × active_zone × zone_score
        await col.create_index(
            [("symbol", 1), ("mode", 1), ("scalp_status", 1), ("zone_score", -1)],
            name="sym_mode_status_score",
        )
        await col.create_index(
            [("symbol", 1), ("mode", 1), ("zones.active_zone", 1), ("zone_score", -1)],
            name="sym_mode_active_zone_score",
        )
        # Gate audit trail — análise de stacking e frequência por gate
        await col.create_index(
            [("symbol", 1), ("block_gate", 1), ("recorded_at", -1)],
            name="sym_block_gate_time",
        )
        logger.info("Scalp snapshot indexes ensured on scalp_snapshots collection")
    except Exception as e:
        logger.warning(f"Scalp snapshot index creation (may already exist): {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm_q(val) -> str:
    """Normaliza qualidade: 'ScalpSignalQuality.MODERATE' → 'MODERATE'. Nunca grava enum repr."""
    if val is None:
        return "NO_TRADE"
    s = str(val)
    if "." in s:
        return s.split(".")[-1]
    return s

# ── Builder ───────────────────────────────────────────────────────────────────

def build_scalp_snapshot_document(symbol: str, sig_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Constrói documento completo a partir de ScalpSignal.to_dict().
    Mantém toda a informação necessária para replay / Auto-Tune.
    """
    now = datetime.now(timezone.utc)

    s1  = sig_dict.get("s1", {})
    s2  = sig_dict.get("s2", {})
    s3  = sig_dict.get("s3", {})
    ind = sig_dict.get("indicators", {})
    lvl = sig_dict.get("levels", {})
    zn  = sig_dict.get("zones", {})
    mc  = sig_dict.get("macro_context", {})

    return {
        # ── Top-level (indexados) ──────────────────────────────────────────────
        "symbol":        symbol,
        "recorded_at":   now,
        "session_label": get_session_label(now),
        "mode":          sig_dict.get("mode", "FLOW"),
        "scalp_status":  sig_dict.get("scalp_status", "NO_SIGNAL"),
        # Gate audit trail — primeiro gate que bloqueou o sinal (engine + zonas).
        # None = sinal passou todos os gates (ACTIVE_SIGNAL).
        # Permite queries de stacking: quantas vezes D30_RANGE co-ocorre com ZONE_OFI_FAST?
        # Valores: D30_RANGE | NO_ZONE | ZONE_SCORE_MIN | ZONE_CONF_MIN |
        #          ZONE_OFI_FAST | ZONE_OFI_SLOW | ZONE_TAPE_DRY | ZONE_CONF_LOW |
        #          ZONE_SCORE_LOW | ZONE_ENTRY | GAMMA_SUPPRESSED | NO_SIGNAL
        "block_gate":    sig_dict.get("block_gate"),
        "s1_regime":     s1.get("regime"),
        "s1_direction":  s1.get("direction"),
        "s2_passed":     s2.get("passed", False),
        "s2_quality":    _norm_q(s2.get("quality")),
        "s3_action":    (s3.get("action") or "").upper() or None,
        "last_price":   ind.get("last_price"),
        "zone_quality": _norm_q(zn.get("quality")),
        "zone_score":   (zn.get("score_breakdown") or {}).get("total_score"),

        # ── S1 completo ───────────────────────────────────────────────────────
        "s1": {
            "regime":     s1.get("regime"),
            "confidence": s1.get("confidence"),
            "direction":  s1.get("direction"),
        },

        # ── S2 completo ───────────────────────────────────────────────────────
        "s2": {
            "passed":              s2.get("passed"),
            "quality":             s2.get("quality"),
            "risk_modifier":       s2.get("risk_modifier"),
            # Todas as razões de bloqueio mergeadas (inclui gamma + ema + gates)
            "block_reasons":       s2.get("block_reasons", []),
            "filters":             s2.get("filters", {}),
            # F7: razões de bloqueio específicas EMA (Gate 5 e Gate 6)
            "ema_block_reasons":   s2.get("ema_block_reasons", []),
            # F5-2-B: razões de bloqueio específicas SHORT_GAMMA suppression
            "gamma_block_reasons": s2.get("gamma_block_reasons", []),
        },

        # ── S3 completo ───────────────────────────────────────────────────────
        "s3": {
            "action":            s3.get("action"),
            "entry_price":       s3.get("entry_price"),
            "stop_loss_price":   s3.get("stop_loss_price"),
            "take_profit_price": s3.get("take_profit_price"),
            "breakeven":         s3.get("breakeven"),
            "quantity":          s3.get("quantity"),
            # Flag de R:R equalizado — True quando tp_pts foi ajustado para tp≥sl
            "tp_was_adjusted":   zn.get("s3_extra", {}).get("tp_was_adjusted", False),
            "rr_ratio":          zn.get("s3_extra", {}).get("rr_ratio"),
            "tp_label":          zn.get("s3_extra", {}).get("tp_label"),
            "sl_pts":            zn.get("s3_extra", {}).get("sl_pts"),
            "tp_pts":            zn.get("s3_extra", {}).get("tp_pts"),
        },

        # ── Indicadores ────────────────────────────────────────────────────────
        "indicators": {
            "ofi_fast":        ind.get("ofi_fast"),
            "ofi_slow":        ind.get("ofi_slow"),
            "absorption_flag": ind.get("absorption_flag"),
            "absorption_side": ind.get("absorption_side"),
            "cvd":             ind.get("cvd"),
            "cvd_trend":       ind.get("cvd_trend"),
            "delta_ratio":     ind.get("delta_ratio"),
            "atr_m1":          ind.get("atr_m1"),
            "vwap":            ind.get("vwap"),
            "candle_direction": ind.get("candle_direction"),
            "body_ratio":      ind.get("body_ratio"),
            "volume_ratio":    ind.get("volume_ratio"),
            "feed_connected":  ind.get("feed_connected"),
            # Campos adicionais para fidelidade do replay AutoTune
            "speed_ratio":     ind.get("speed_ratio"),      # ratio EMA tape → tape_speed_modifier
            "session_minutes": ind.get("session_minutes"),  # minutos RTH → late_session_penalty
            # D30 Fase 2 — gate de deslocamento 30 min
            "disp_30m":        ind.get("disp_30m"),         # pts de deslocamento vs abertura 30m
            "d30_state":       ind.get("d30_state"),        # "OK"|"RISK"|"BLOCKED"|None
        },

        # ── Níveis estruturais ────────────────────────────────────────────────
        "levels": {
            "vwap":           lvl.get("vwap"),
            "vwap_zone":      lvl.get("vwap_zone"),
            "vwap_upper_1":   lvl.get("vwap_upper_1"),
            "vwap_lower_1":   lvl.get("vwap_lower_1"),
            "vwap_upper_2":   lvl.get("vwap_upper_2"),
            "vwap_lower_2":   lvl.get("vwap_lower_2"),
            "vwap_upper_3":   lvl.get("vwap_upper_3"),
            "vwap_lower_3":   lvl.get("vwap_lower_3"),
            "vwap_std":       lvl.get("vwap_std"),
            "poc":            lvl.get("poc"),
            "vah":            lvl.get("vah"),
            "val":            lvl.get("val"),
            "d1_poc":         lvl.get("d1_poc"),
            "d1_vah":         lvl.get("d1_vah"),
            "d1_val":         lvl.get("d1_val"),
            "d1_high":        lvl.get("d1_high"),
            "d1_low":         lvl.get("d1_low"),
            "onh":            lvl.get("onh"),
            "onl":            lvl.get("onl"),
            "on_poc":         lvl.get("on_poc"),
            "ibh":            lvl.get("ibh"),
            "ibl":            lvl.get("ibl"),
            "structural_target":       lvl.get("structural_target"),
            "structural_target_label": lvl.get("structural_target_label"),
            # ── Item 5: RTH Open price — necessário para replay AutoTune do Fix 5 bias ──
            # None antes da primeira avaliação RTH do dia (Globex / pre-market).
            "rth_open_price":          lvl.get("rth_open_price"),
        },

        # ── Zonas (modo ZONES) ────────────────────────────────────────────────
        "zones": {
            "day_regime":          zn.get("day_regime"),
            "active_zone":         zn.get("active_zone"),
            "quality":             zn.get("quality"),
            "nearby":              zn.get("nearby", []),
            "s3_extra":            zn.get("s3_extra", {}),
            "score_breakdown":     zn.get("score_breakdown"),
            "active_params":       zn.get("active_params"),
            # F7: Gate 5 (EMA_PRICE_COOLDOWN) e Gate 6 (EMA_ZONE_TYPE_COOLDOWN)
            # Vazio quando sem bloqueio EMA. Duplicado em s2.ema_block_reasons.
            # Mantido em zones para backward-compat com snapshots anteriores.
            "ema_block_reasons":   s2.get("ema_block_reasons", []),
            # F5-2-B: SHORT_GAMMA suppression tracking. Duplicado em s2.gamma_block_reasons.
            # Mantido em zones para simetria com ema_block_reasons e queries por zona.
            "gamma_block_reasons": s2.get("gamma_block_reasons", []),
            # ── Item 5: Bias intraday vs RTH Open ─────────────────────────────
            # Necessário para AutoTune estratificar por contexto de bias.
            # "ABOVE"|"BELOW"|"NEUTRAL" — threshold 0.10×ATR.
            "price_vs_rth_open":   zn.get("price_vs_rth_open", "NEUTRAL"),
            # "BULLISH"|"BEARISH"|"NEUTRAL" — regime_bias derivado de price_vs_rth_open.
            "regime_bias":         zn.get("regime_bias", "NEUTRAL"),
            # ── Item 6: CVD Regime Confirmation ──────────────────────────────
            # "CONFIRMED"|"CONTESTED"|"NEUTRAL" — alinhamento CVD com regime EXPANSION.
            "regime_cvd_conf":     zn.get("regime_cvd_conf", "NEUTRAL"),
        },

        # ── Data Quality / Proveniência ───────────────────────────────────────
        # Registra a origem de cada dado estrutural neste snapshot.
        # Usado pelo Auto-Tune para filtrar snapshots com dados aproximados.
        #
        # Valores de *_source:
        #   "live"  → dado calculado de DataBento neste ciclo (melhor qualidade)
        #   "seed"  → dado do snapshot anterior injetado no warm-start (aprox.)
        #   "none"  → dado não disponível (buffer insuficiente / API falhou)
        "data_quality": {
            "vwap_source":       lvl.get("vwap_source", "none"),
            "vp_session_source": lvl.get("vp_session_source", "none"),
            "vp_d1_source":      lvl.get("vp_d1_source", "none"),
            # atr_source já existe no campo indicators — replicado aqui para facilitar queries
            "atr_source":        sig_dict.get("indicators", {}).get("atr_source", "none"),
        },

        # ── Macro Context: Term Structure + Gamma ─────────────────────────────
        # Preenchido apenas no modo ZONES; vazio ({}) em FLOW e CANDLE.
        "macro_context": {
            # Term Structure (VIX/VXN ratio)
            "ts_ratio":           mc.get("ts_ratio", 0.0),
            "ts_state":           mc.get("ts_state", "UNKNOWN"),
            "ts_hard_stop":       mc.get("ts_hard_stop", False),
            "ts_fade_suppressed": mc.get("ts_fade_suppressed", False),
            # Gamma Walls (convertidas para pontos de futuros)
            "gamma_reliable":        mc.get("gamma_reliable", False),
            "gamma_call_wall":       mc.get("gamma_call_wall", 0.0),
            "gamma_put_wall":        mc.get("gamma_put_wall", 0.0),
            "gamma_sentiment":       mc.get("gamma_sentiment", "NEUTRAL"),
            # Zero Gamma Level e Regime
            "zero_gamma":            mc.get("zero_gamma", 0.0),
            "gamma_regime":          mc.get("gamma_regime", "UNKNOWN"),
            # F5-2-B: bool derivado (gamma_reliable=True AND gamma_regime=SHORT_GAMMA)
            # Pré-computado para facilitar queries de AutoTune sem $and complexo.
            "gamma_short_suppressed": (
                mc.get("gamma_reliable", False)
                and mc.get("gamma_regime", "UNKNOWN") == "SHORT_GAMMA"
            ),
        },
    }


# ── Gravação ─────────────────────────────────────────────────────────────────

_SKIP_SNAPSHOT_STATUSES = frozenset({
    "MARKET_CLOSED",
    "NO_DATA",
    "FEED_GHOST",
    "FEED_LOW_QUALITY",
    "WARMING_UP",
})


async def _evaluate_and_record(database, scalp_engine, symbol: str) -> Dict[str, Any]:
    """Avalia e grava snapshot para um símbolo. Retorna {"ok": bool, "reason": str}."""
    try:
        sig = await scalp_engine.evaluate(symbol)
        sig_dict = sig.to_dict()

        # Gate de status — não vale a pena gravar estados sem sinal útil
        status = sig_dict.get("scalp_status", "")
        if status in _SKIP_SNAPSHOT_STATUSES:
            logger.info(
                "Scalp snapshot %s: status=%s — skipped",
                symbol, status,
            )
            return {"ok": False, "reason": status}

        # Não grava snapshot sem preço real (feed sem dados / fim de semana)
        last_price = sig_dict.get("indicators", {}).get("last_price") or 0.0
        if last_price <= 0:
            logger.info(
                "Scalp snapshot %s: last_price=%.2f — skipped (sem preço)",
                symbol, last_price,
            )
            return {"ok": False, "reason": "no_price"}

        doc = build_scalp_snapshot_document(symbol, sig_dict)
        _n_ind = len(doc.get("indicators", {}))
        _has_d30 = "disp_30m" in doc.get("indicators", {})
        logger.info("Scalp snapshot %s: gravando status=%s price=%.2f ind=%d d30=%s",
                    symbol, status, last_price, _n_ind, _has_d30)
        await database.scalp_snapshots.insert_one(doc)
        logger.info("Scalp snapshot %s: OK gravado", symbol)
        return {"ok": True, "reason": "recorded"}

    except Exception as e:
        logger.warning("Scalp snapshot error for %s: %s", symbol, e)
        return {"ok": False, "reason": str(e)}


async def record_scalp_snapshots(database, scalp_engine, trading_calendar=None) -> Dict[str, Any]:
    """Grava snapshots do ScalpEngine para todos os símbolos em paralelo."""
    if trading_calendar:
        now = datetime.now(timezone.utc)
        session = trading_calendar.get_session_info(now)
        if session.get("is_cme_halted", False):
            return {"recorded": 0, "reason": "cme_halted"}

    # Avalia MNQ e MES em paralelo — locks são por símbolo, sem conflito
    # return_exceptions=True isola falhas por símbolo: um erro em MNQ não cancela MES
    raw = await asyncio.gather(
        *[_evaluate_and_record(database, scalp_engine, sym) for sym in SCALP_SNAPSHOT_SYMBOLS],
        return_exceptions=True,
    )
    results = [
        r if isinstance(r, dict) else {"ok": False, "reason": str(r)}
        for r in raw
    ]

    recorded = sum(1 for r in results if r.get("ok"))
    errors   = [
        {"symbol": sym, "error": r.get("reason", "unknown")}
        for sym, r in zip(SCALP_SNAPSHOT_SYMBOLS, results)
        if not r.get("ok")
    ]

    return {
        "recorded":      recorded,
        "total_symbols": len(SCALP_SNAPSHOT_SYMBOLS),
        "errors":        errors,
    }


# ── Stats ────────────────────────────────────────────────────────────────────

async def get_scalp_snapshot_stats(database) -> Dict[str, Any]:
    col = database.scalp_snapshots

    # Single $facet aggregation replaces 8 separate DB round-trips
    pipeline = [
        {"$facet": {
            "meta": [
                {"$group": {
                    "_id":        None,
                    "total":      {"$sum": 1},
                    "oldest":     {"$min": "$recorded_at"},
                    "newest":     {"$max": "$recorded_at"},
                    "actionable": {"$sum": {"$cond": [{"$in": [{"$toUpper": {"$ifNull": ["$s3_action", ""]}}, ["BUY", "SELL"]]}, 1, 0]}},
                }},
            ],
            "by_symbol": [
                {"$group": {"_id": "$symbol", "count": {"$sum": 1}}},
            ],
            "by_mode": [
                {"$group": {"_id": "$mode",      "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ],
            "by_regime": [
                {"$group": {"_id": "$s1_regime", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ],
            "by_quality": [
                {"$group": {"_id": "$s2_quality","count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ],
            "by_session": [
                {"$group": {"_id": "$session_label", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ],
            "by_vwap_source": [
                {"$group": {"_id": "$data_quality.vwap_source", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ],
            "score_breakdown_coverage": [
                {"$match": {"mode": "ZONES", "s3_action": {"$in": ["BUY", "SELL", "buy", "sell"]}}},
                {"$group": {
                    "_id": None,
                    "total":  {"$sum": 1},
                    "has_sb": {"$sum": {"$cond": [{"$ifNull": ["$zones.score_breakdown", False]}, 1, 0]}},
                }},
            ],
        }},
    ]

    result = None
    async for doc in col.aggregate(pipeline):
        result = doc
        break

    meta_list  = (result or {}).get("meta", [])
    meta       = meta_list[0] if meta_list else {}
    total      = meta.get("total",      0)
    oldest_dt  = meta.get("oldest")
    newest_dt  = meta.get("newest")
    actionable = meta.get("actionable", 0)

    by_symbol  = {d["_id"]: d["count"] for d in (result or {}).get("by_symbol", []) if d["_id"]}
    by_mode    = {d["_id"]: d["count"] for d in (result or {}).get("by_mode",   []) if d["_id"]}
    by_regime  = {d["_id"]: d["count"] for d in (result or {}).get("by_regime", []) if d["_id"]}
    by_quality = {d["_id"]: d["count"] for d in (result or {}).get("by_quality",[]) if d["_id"]}
    by_session = {d["_id"]: d["count"] for d in (result or {}).get("by_session", []) if d["_id"]}
    by_vwap_source = {d["_id"]: d["count"] for d in (result or {}).get("by_vwap_source", []) if d.get("_id") is not None}

    sb_coverage_list = (result or {}).get("score_breakdown_coverage", [])
    sb_coverage = sb_coverage_list[0] if sb_coverage_list else {}
    sb_total = sb_coverage.get("total", 0)
    sb_has   = sb_coverage.get("has_sb", 0)

    estimated_mb = round((total * 3500) / (1024 * 1024), 1)

    return {
        "total_snapshots":   total,
        "estimated_size_mb": estimated_mb,
        "oldest":  oldest_dt.isoformat() if oldest_dt else None,
        "newest":  newest_dt.isoformat() if newest_dt else None,
        "by_symbol":   by_symbol,
        "by_mode":     by_mode,
        "by_regime":   by_regime,
        "by_quality":  by_quality,
        "by_session":  by_session,
        "data_quality": {
            "vwap_source":           by_vwap_source,
            "zones_actionable":      sb_total,
            "score_breakdown_pct":   round(sb_has / sb_total * 100, 1) if sb_total else 100.0,
        },
        "actionable_signals": actionable,
        "ttl_days":           SCALP_SNAPSHOT_TTL // 86400,
        "interval_seconds":   SCALP_SNAPSHOT_INTERVAL,
    }


# ── Query ─────────────────────────────────────────────────────────────────────

async def query_scalp_snapshots(
    database,
    symbol:        str,
    start:         Optional[datetime] = None,
    end:           Optional[datetime] = None,
    mode:          Optional[str]      = None,
    regime:        Optional[str]      = None,
    quality:       Optional[str]      = None,
    gamma_regime:  Optional[str]      = None,
    ts_hard_stop:  Optional[bool]     = None,
    action_only:   bool               = False,
    limit:         int                = 500,
) -> List[Dict[str, Any]]:
    """Query snapshots com filtros para replay/Auto-Tune."""
    query: Dict[str, Any] = {"symbol": symbol}

    if start or end:
        query["recorded_at"] = {}
        if start:
            query["recorded_at"]["$gte"] = start
        if end:
            query["recorded_at"]["$lte"] = end

    if mode:
        query["mode"] = mode

    if regime:
        query["s1_regime"] = regime

    if quality:
        query["s2_quality"] = quality

    if gamma_regime:
        query["macro_context.gamma_regime"] = gamma_regime

    if ts_hard_stop is not None:
        query["macro_context.ts_hard_stop"] = ts_hard_stop

    if action_only:
        query["s3_action"] = {"$in": ["BUY", "SELL"]}

    cursor = database.scalp_snapshots.find(
        query, {"_id": 0}
    ).sort("recorded_at", 1).limit(limit)

    results = []
    async for doc in cursor:
        if "recorded_at" in doc and hasattr(doc["recorded_at"], "isoformat"):
            doc["recorded_at"] = doc["recorded_at"].isoformat()
        results.append(doc)

    return results
