"""
Scalp Engine — Quantum Trading Scalp
=====================================
Motor de sinal de 3 camadas para scalping em micro futuros (MNQ/MES).
Suporta dois modos operacionais:

MODO FLUXO  — OFI fast/slow + CVD + absorção (tick-based, janelas de 500/2000 trades)
MODO ZONAS  — Regime + VWAP/VP/ONH-ONL como zonas de interesse; SL/TP adaptativos

Ambos os modos compartilham a estrutura S1 → S2 → S3:
  S1 — Regime: classifica o contexto de mercado
  S2 — Confirmação: valida filtros de qualidade antes de armar S3
  S3 — Execução: gera parâmetros de ordem bracket OCO

Parâmetros de execução:
    FLUXO — SL/TP/BE em ticks fixos (MNQ: 6/10/4 | MES: 4/8/3)
    ZONES — SL/TP baseados em tipo de zona; TP estrutural via VWAP/VP levels
"""

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum
from zoneinfo import ZoneInfo

from services.scalp_zones import (
    evaluate_zones,
    detect_day_regime,
    ScalpDayRegime,
    REGIME_RISK_PARAMS,
)

logger = logging.getLogger("scalp_engine")

TICK_SIZE = 0.25


# ═══════════════════════════════════════════════════════════════════
# Parâmetros padrão
# ═══════════════════════════════════════════════════════════════════

SCALP_PARAMS: Dict[str, Dict[str, float]] = {
    'MNQ': {'sl_ticks': 6, 'tp_ticks': 10, 'be_ticks': 4},
    'MES': {'sl_ticks': 4, 'tp_ticks': 8,  'be_ticks': 3},
}

# ATR multiplier — usado no cálculo de target estrutural ZONES
CANDLE_TP_ATR = 1.5

# ATR mínimo em pts (fallback se buffer for insuficiente)
ATR_MIN_FALLBACK = {'MNQ': 5.0, 'MES': 2.0}

# ── HF1: Cache de ATR com TTL=60s ─────────────────────────────────────────────
ATR_CACHE_TTL  = 60.0   # segundos — ATR M1 muda < 5% por hora; recalcular a cada min é suficiente
_atr_cache: Dict[str, Dict[str, Any]] = {}  # {symbol: {'atr': float, 'ts': float, 'from_bars': bool}}

# ── SQS — Scalp Quality Score (análogo ao MQS do V3) ──────────────────────────
SCALP_SQS_MIN_TRADES    = 15      # mínimo de trades no buffer para OFI/CVD fiáveis
SCALP_SQS_STALE_TRADE_S = 30.0   # máximo de segundos desde o último trade
SCALP_SQS_GHOST_CYCLES  = 2      # ciclos consecutivos com OFI=CVD=0 → ghost feed

# ── Sessão RTH e warm-up ───────────────────────────────────────────────────────
SCALP_RTH_END_MINUTES   = 390.0  # 6.5h = fim da sessão RTH (09:30 ET + 390min = 16:00 ET)
_ENGINE_ET              = ZoneInfo("America/New_York")  # DST-aware; resolve 14:30 UTC (EST) / 13:30 UTC (EDT)
SCALP_MIN_WARMUP_BARS   = 5      # barras M1 mínimas para ATR fiável (ZONES/CANDLE)

# ── Sessão Globex (overnight) ──────────────────────────────────────────────────
# Globex: 18:00 ET (pre-close) → 09:30 ET next day, excluindo halt CME 17:00-18:00 ET
_GLOBEX_OPEN_H  = 18.0   # 18:00 ET — abertura Globex
_GLOBEX_CLOSE_H =  9.5   # 09:30 ET — fecho Globex / abertura RTH
_CME_HALT_START = 17.0   # 17:00 ET — início halt diário
_CME_HALT_END   = 18.0   # 18:00 ET — fim halt diário


def _is_globex_session(dt_et: "datetime") -> bool:
    """True se dt_et está dentro do horário Globex (18:00–09:30 ET, exc. halt 17:00–18:00 ET)."""
    h = dt_et.hour + dt_et.minute / 60.0
    if _CME_HALT_START <= h < _CME_HALT_END:
        return False  # janela de halt CME — nem RTH nem Globex
    return h >= _GLOBEX_OPEN_H or h < _GLOBEX_CLOSE_H


# ═══════════════════════════════════════════════════════════════════
# Enums compartilhados
# ═══════════════════════════════════════════════════════════════════

class ScalpMode(str, Enum):
    FLOW  = "FLOW"   # Modo Fluxo: OFI/CVD/absorção (tick-based)
    ZONES = "ZONES"  # Modo Zonas: regime + VWAP/VP/ONH/ONL como zonas de interesse


class ScalpRegime(str, Enum):
    # Modo Fluxo
    BULLISH_FLOW   = "BULLISH_FLOW"
    BEARISH_FLOW   = "BEARISH_FLOW"
    ABSORPTION_BUY = "ABSORPTION_BUY"
    ABSORPTION_SEL = "ABSORPTION_SEL"
    # Modo Candle
    MOMENTUM_BULL  = "MOMENTUM_BULL"   # Candle bullish forte + acima VWAP
    MOMENTUM_BEAR  = "MOMENTUM_BEAR"   # Candle bearish forte + abaixo VWAP
    REVERSAL_BULL  = "REVERSAL_BULL"   # Pullback em VWAP com rejeição bearish
    REVERSAL_BEAR  = "REVERSAL_BEAR"   # Pullback em VWAP com rejeição bullish
    # Comuns
    NEUTRAL        = "NEUTRAL"
    NO_DATA        = "NO_DATA"


def _derive_zone_block_gate(
    status: str,
    block_reasons: list,
    gamma_block_reasons: list,
) -> Optional[str]:
    """
    Converte block_reasons textuais em código estruturado para análise de stacking.
    Retorna None quando o sinal passou (ACTIVE_SIGNAL).

    Hierarquia de precedência (mais específico primeiro):
      ZONE_SCORE_MIN  → gate per-zona zone_min_score bloqueou
      ZONE_CONF_MIN   → gate per-zona zone_min_confluence bloqueou
      GAMMA_SUPPRESSED→ SHORT_GAMMA suppression (bloco de mercado)
      NO_ZONE         → preço fora de todas as zonas activas
      ZONE_OFI_FAST   → OFI fast não alinhado / fraco
      ZONE_OFI_SLOW   → OFI slow bloqueou a entrada
      ZONE_TAPE_DRY   → tape seco em fade (tape speed)
      ZONE_CONF_LOW   → confluência de zonas insuficiente
      ZONE_SCORE_LOW  → score abaixo do threshold
      ZONE_ENTRY      → outra razão de evaluate_zone_entry
    """
    if status == "ACTIVE_SIGNAL":
        return None
    if status == "NO_ZONE":
        return "NO_ZONE"
    # Verificar gamma antes de qualquer razão de bloqueio
    if gamma_block_reasons:
        return "GAMMA_SUPPRESSED"
    all_reasons = block_reasons or []
    for r in all_reasons:
        rl = r.lower()
        if "zone_min_score" in rl:
            return "ZONE_SCORE_MIN"
        if "zone_min_confluence" in rl:
            return "ZONE_CONF_MIN"
    if all_reasons:
        r0 = all_reasons[0].lower()
        if "ofi slow" in r0:
            return "ZONE_OFI_SLOW"
        if "ofi fast" in r0:
            return "ZONE_OFI_FAST"
        if "tape" in r0:
            return "ZONE_TAPE_DRY"
        if "conf" in r0:
            return "ZONE_CONF_LOW"
        if "score" in r0 or "abaixo" in r0 or "threshold" in r0:
            return "ZONE_SCORE_LOW"
        return "ZONE_ENTRY"
    return "NO_SIGNAL"


class ScalpSignalQuality(str, Enum):
    STRONG   = "STRONG"
    MODERATE = "MODERATE"
    WEAK     = "WEAK"
    NO_TRADE = "NO_TRADE"


# ═══════════════════════════════════════════════════════════════════
# ScalpSignal — snapshot unificado para ambos os modos
# ═══════════════════════════════════════════════════════════════════

class ScalpSignal:
    def __init__(self):
        self.signal_id: str = uuid.uuid4().hex
        self.symbol: str = ""
        self.timestamp: str = ""
        self.mode: str = ScalpMode.FLOW

        # S1
        self.s1_regime: ScalpRegime = ScalpRegime.NO_DATA
        self.s1_confidence: float = 0.0
        self.s1_direction: Optional[str] = None

        # S2
        self.s2_passed: bool = False
        self.s2_quality: ScalpSignalQuality = ScalpSignalQuality.NO_TRADE
        self.s2_risk_modifier: float = 1.0
        self.s2_block_reasons: List[str] = []
        self.s2_filters: Dict[str, Any] = {}
        # F7: razões de bloqueio específicas das EMA Pullback Zones (Gate 5 e Gate 6)
        # Distintas de s2_block_reasons (que são gates de evaluate_zone_entry)
        self.ema_block_reasons: List[str] = []
        # F5-2-B: razões de bloqueio específicas de ambiente Gamma (SHORT_GAMMA suppression)
        self.gamma_block_reasons: List[str] = []

        # S3
        self.s3_action: Optional[str] = None
        self.s3_entry_price: Optional[float] = None
        self.s3_stop_loss_price: Optional[float] = None
        self.s3_take_profit_price: Optional[float] = None
        self.s3_breakeven: Optional[float] = None
        self.s3_quantity: int = 1

        # Indicadores — Modo Fluxo
        self.ofi_fast: float = 0.0
        self.ofi_slow: float = 0.0
        self.absorption_flag: bool = False
        self.absorption_side: str = "NONE"
        self.cvd: float = 0.0
        self.cvd_trend: str = "NEUTRAL"
        self.delta_ratio: Optional[float] = None

        # Indicadores — ATR/VWAP (partilhados ZONES/FLOW)
        self.atr_m1: Optional[float] = None
        self.vwap: Optional[float] = None
        self.body_ratio: Optional[float] = None       # 0–1 (força do candle M1)
        self.volume_ratio: Optional[float] = None     # vol atual / média vol

        # D30 — deslocamento de preço nos últimos 30 minutos
        # entry_price − close_30min_ago (pts). None = dados insuficientes.
        self.disp_30m:  Optional[float] = None
        self.d30_state: Optional[str]   = None  # "OK" | "RISK" | "BLOCKED" | None

        # Indicadores adicionais — persistidos no snapshot para AutoTune
        self.speed_ratio: Optional[float] = None       # ratio EMA rápida/lenta do tape (tape speed)
        self.session_minutes: Optional[float] = None   # minutos desde abertura RTH
        self.rth_open_price: Optional[float] = None    # primeiro preço RTH do dia (09:30 ET); referência de bias intraday

        # Comuns
        self.last_price: float = 0.0
        self.feed_connected: bool = False
        self.scalp_status: str = "NO_SIGNAL"
        self.atr_source: str = "live"    # "live" | "fallback" | "n/a"

        # Gate audit trail — funil de bloqueio por stage (gravado nos snapshots)
        # block_gate: código estruturado do primeiro gate que bloqueou o sinal.
        #   Valores possíveis:
        #     Engine:  D30_RANGE
        #     Zonas:   NO_ZONE, ZONE_SCORE_MIN, ZONE_CONF_MIN, ZONE_OFI_FAST,
        #              ZONE_OFI_SLOW, ZONE_TAPE_DRY, ZONE_CONF_LOW, ZONE_SCORE_LOW,
        #              GAMMA_SUPPRESSED, ZONE_ENTRY
        #     Passado: None (ACTIVE_SIGNAL sem bloqueio)
        self.block_gate: Optional[str] = None

        # Proveniência dos dados de mercado — para audit trail nos snapshots
        # "live"   → valor computado de dados DataBento frescos neste ciclo
        # "seed"   → valor injetado do snapshot anterior no startup (warm-start)
        # "none"   → campo não disponível (buffer insuficiente / API falhou)
        self.vwap_source: str = "none"          # "live" | "seed" | "none"
        self.vp_session_source: str = "none"    # "live" | "seed" | "none"
        self.vp_d1_source: str = "none"         # "live" | "none"

        # Contexto de mercado (VWAP bands, VP, ONH/ONL)
        self.vwap_zone: Optional[str] = None
        self.vwap_upper_1: Optional[float] = None
        self.vwap_lower_1: Optional[float] = None
        self.vwap_upper_2: Optional[float] = None
        self.vwap_lower_2: Optional[float] = None
        self.vwap_upper_3: Optional[float] = None
        self.vwap_lower_3: Optional[float] = None
        self.vwap_std: Optional[float] = None
        # VP Sessão
        self.poc: Optional[float] = None
        self.vah: Optional[float] = None
        self.val: Optional[float] = None
        # VP D-1
        self.d1_poc:  Optional[float] = None
        self.d1_vah:  Optional[float] = None
        self.d1_val:  Optional[float] = None
        # Extremos absolutos RTH D-1 — máximo e mínimo do pregão anterior
        self.d1_high: Optional[float] = None
        self.d1_low:  Optional[float] = None
        # Overnight
        self.onh: Optional[float] = None
        self.onl: Optional[float] = None
        self.on_poc: Optional[float] = None
        # Initial Balance
        self.ibh: Optional[float] = None
        self.ibl: Optional[float] = None
        # Target estrutural (próximo nível VWAP na direção do trade)
        self.structural_target: Optional[float] = None
        self.structural_target_label: Optional[str] = None

        # Modo ZONES — contexto de regime e zonas
        self.day_regime: Optional[str] = None          # ScalpDayRegime
        self.zones_nearby: List[Dict] = []             # zonas próximas do preço
        self.active_zone: Optional[Dict] = None        # zona que gerou o sinal
        self.zone_type_str: Optional[str] = None       # ZoneType.value — campo de 1ª classe (redundante com active_zone["type"] mas robusto a None)
        self.zone_quality: Optional[str] = None        # STRONG | MODERATE | WEAK
        self.zone_s3_extra: Dict[str, Any] = {}        # campos adicionais do s3 de zonas
        self.zone_score_breakdown: Optional[Dict] = None  # ScoreBreakdown serializado
        self.zone_active_params: Optional[Dict] = None   # Params activos na avaliação (auditoria)

        # Macro context — Term Structure + Gamma (ZONES mode; vazio em FLOW/CANDLE)
        self.macro_context: Dict[str, Any] = {}        # ts_ratio/ts_state/gamma_regime/call_wall/…

        # Item 4/5/6 — campos de contexto de regime e bias intraday
        self.price_vs_rth_open: str = "NEUTRAL"        # "ABOVE"|"BELOW"|"NEUTRAL" vs RTH open price
        self.regime_bias:       str = "NEUTRAL"        # "BULLISH"|"BEARISH"|"NEUTRAL" (Item 5)
        self.regime_cvd_conf:   str = "NEUTRAL"        # "CONFIRMED"|"CONTESTED"|"NEUTRAL" (Item 6)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "mode": self.mode,
            "scalp_status": self.scalp_status,
            "block_gate":   self.block_gate,
            "s1": {
                "regime": self.s1_regime,
                "confidence": self.s1_confidence,
                "direction": self.s1_direction,
            },
            "s2": {
                "passed":           self.s2_passed,
                "quality":          self.s2_quality,
                "risk_modifier":    self.s2_risk_modifier,
                "block_reasons":      self.s2_block_reasons,
                "filters":            self.s2_filters,
                "ema_block_reasons":  self.ema_block_reasons,
                "gamma_block_reasons": self.gamma_block_reasons,
            },
            "s3": {
                "action": self.s3_action,
                "entry_price": self.s3_entry_price,
                "stop_loss_price": self.s3_stop_loss_price,
                "take_profit_price": self.s3_take_profit_price,
                "breakeven": self.s3_breakeven,
                "quantity": self.s3_quantity,
            },
            "indicators": {
                "ofi_fast": self.ofi_fast,
                "ofi_slow": self.ofi_slow,
                "absorption_flag": self.absorption_flag,
                "absorption_side": self.absorption_side,
                "cvd": self.cvd,
                "cvd_trend": self.cvd_trend,
                "delta_ratio": self.delta_ratio,
                "atr_m1": self.atr_m1,
                "vwap": self.vwap,
                "body_ratio": self.body_ratio,
                "volume_ratio": self.volume_ratio,
                "last_price": self.last_price,
                "feed_connected": self.feed_connected,
                # Campos adicionais para fidelidade do replay / AutoTune
                "speed_ratio":     self.speed_ratio,
                "session_minutes": self.session_minutes,
                "atr_source":      self.atr_source,
                "disp_30m":        self.disp_30m,
                "d30_state":       self.d30_state,
            },
            "levels": {
                "vwap": self.vwap,
                "vwap_zone": self.vwap_zone,
                "vwap_upper_1": self.vwap_upper_1,
                "vwap_lower_1": self.vwap_lower_1,
                "vwap_upper_2": self.vwap_upper_2,
                "vwap_lower_2": self.vwap_lower_2,
                "vwap_upper_3": self.vwap_upper_3,
                "vwap_lower_3": self.vwap_lower_3,
                "vwap_std": self.vwap_std,
                "poc": self.poc,
                "vah": self.vah,
                "val": self.val,
                "d1_poc":  self.d1_poc,
                "d1_vah":  self.d1_vah,
                "d1_val":  self.d1_val,
                "d1_high": self.d1_high,
                "d1_low":  self.d1_low,
                "onh": self.onh,
                "onl": self.onl,
                "on_poc": self.on_poc,
                "ibh": self.ibh,
                "ibl": self.ibl,
                "structural_target": self.structural_target,
                "structural_target_label": self.structural_target_label,
                "rth_open_price":   self.rth_open_price,
                # Proveniência — para data_quality do snapshot
                "vwap_source":      self.vwap_source,
                "vp_session_source": self.vp_session_source,
                "vp_d1_source":     self.vp_d1_source,
            },
            "zones": {
                "day_regime":       self.day_regime,
                "active_zone":      self.active_zone,
                "zone_type":        self.zone_type_str,
                "quality":          self.zone_quality,
                "nearby":           self.zones_nearby,
                "s3_extra":         self.zone_s3_extra,
                "score_breakdown":  self.zone_score_breakdown,
                "active_params":    self.zone_active_params,
                "price_vs_rth_open": self.price_vs_rth_open,  # Item 5
                "regime_bias":       self.regime_bias,          # Item 5
                "regime_cvd_conf":   self.regime_cvd_conf,      # Item 6
            },
            "macro_context": {
                "ts_ratio":           self.macro_context.get("ts_ratio", 0.0),
                "ts_state":           self.macro_context.get("ts_state", "UNKNOWN"),
                "ts_hard_stop":       self.macro_context.get("ts_hard_stop", False),
                "ts_fade_suppressed": self.macro_context.get("ts_fade_suppressed", False),
                "gamma_reliable":     self.macro_context.get("gamma_reliable", False),
                "gamma_call_wall":    self.macro_context.get("gamma_call_wall", 0.0),
                "gamma_put_wall":     self.macro_context.get("gamma_put_wall", 0.0),
                "gamma_sentiment":    self.macro_context.get("gamma_sentiment", "NEUTRAL"),
                "zero_gamma":         self.macro_context.get("zero_gamma", 0.0),
                "gamma_regime":       self.macro_context.get("gamma_regime", "UNKNOWN"),
            },
        }


# ═══════════════════════════════════════════════════════════════════
# MODO FLUXO — S1 / S2
# ═══════════════════════════════════════════════════════════════════

OFI_STRONG_THRESHOLD   = 0.35
OFI_MODERATE_THRESHOLD = 0.18
ABSORPTION_OFI_MIN     = 0.55
S2_OFI_FAST_MIN        = 0.20
S2_OFI_SLOW_MIN        = 0.08
S2_DELTA_RATIO_MIN     = 0.06
S1_FLOW_CONFIDENCE_MIN = 4.5
ANTI_FADE_OFI_SLOW     = 0.30


def evaluate_s1_flow(live_data: Dict[str, Any]) -> Tuple[ScalpRegime, float, Optional[str]]:
    if not live_data or not live_data.get("connected"):
        return ScalpRegime.NO_DATA, 0.0, None

    ofi_fast       = live_data.get("ofi_fast", 0.0)
    ofi_slow       = live_data.get("ofi_slow", 0.0)
    absorption_flag = live_data.get("absorption_flag", False)
    absorption_side = live_data.get("absorption_side", "NONE")
    cvd_trend      = live_data.get("cvd_trend", "NEUTRAL")
    abs_ofi        = abs(ofi_fast)

    # Absorção institucional (prioridade)
    if absorption_flag and abs_ofi >= ABSORPTION_OFI_MIN:
        confidence = min(10.0, 6.0 + abs_ofi * 6)
        if absorption_side == "SELL_ABSORBED":
            return ScalpRegime.ABSORPTION_BUY, round(confidence, 1), "LONG"
        if absorption_side == "BUY_ABSORBED":
            return ScalpRegime.ABSORPTION_SEL, round(confidence, 1), "SHORT"

    # Fluxo direcional forte
    if abs_ofi >= OFI_STRONG_THRESHOLD:
        cvd_aligned  = (ofi_fast > 0 and cvd_trend == "RISING") or \
                       (ofi_fast < 0 and cvd_trend == "FALLING")
        slow_aligned = (ofi_fast > 0 and ofi_slow > 0.10) or \
                       (ofi_fast < 0 and ofi_slow < -0.10)
        bonus = (0.5 if cvd_aligned else 0) + (0.5 if slow_aligned else 0)
        confidence = min(10.0, 5.0 + abs_ofi * 8 + bonus)
        if ofi_fast > 0:
            return ScalpRegime.BULLISH_FLOW, round(confidence, 1), "LONG"
        return ScalpRegime.BEARISH_FLOW, round(confidence, 1), "SHORT"

    # Fluxo direcional moderado
    if abs_ofi >= OFI_MODERATE_THRESHOLD:
        confidence = min(10.0, 3.0 + abs_ofi * 6)
        if ofi_fast > 0:
            return ScalpRegime.BULLISH_FLOW, round(confidence, 1), "LONG"
        return ScalpRegime.BEARISH_FLOW, round(confidence, 1), "SHORT"

    return ScalpRegime.NEUTRAL, round(abs_ofi * 5, 1), None


def evaluate_s2_flow(
    direction: Optional[str],
    live_data: Dict[str, Any],
    s1_confidence: float,
    delta_ratio: Optional[float],
) -> Tuple[bool, ScalpSignalQuality, float, List[str], Dict]:
    block_reasons: List[str] = []
    filters: Dict[str, Any] = {}

    if direction is None:
        return False, ScalpSignalQuality.NO_TRADE, 1.0, ["S1 sem direção"], {}

    ofi_fast   = live_data.get("ofi_fast", 0.0)
    ofi_slow   = live_data.get("ofi_slow", 0.0)
    cvd_trend  = live_data.get("cvd_trend", "NEUTRAL")
    sign = 1 if direction == "LONG" else -1

    filters["s1_confidence"] = {"value": s1_confidence, "min": S1_FLOW_CONFIDENCE_MIN, "passed": s1_confidence >= S1_FLOW_CONFIDENCE_MIN}
    if s1_confidence < S1_FLOW_CONFIDENCE_MIN:
        block_reasons.append(f"S1 confidence baixa ({s1_confidence:.1f})")

    ofi_fast_ok = ofi_fast * sign >= S2_OFI_FAST_MIN
    filters["ofi_fast"] = {"value": round(ofi_fast, 4), "threshold": S2_OFI_FAST_MIN * sign, "passed": ofi_fast_ok}
    if not ofi_fast_ok:
        block_reasons.append(f"OFI fast não alinhado ({ofi_fast:.4f})")

    ofi_slow_ok = ofi_slow * sign >= S2_OFI_SLOW_MIN
    filters["ofi_slow"] = {"value": round(ofi_slow, 4), "threshold": S2_OFI_SLOW_MIN * sign, "passed": ofi_slow_ok}
    if not ofi_slow_ok:
        block_reasons.append(f"OFI slow não alinhado ({ofi_slow:.4f})")

    cvd_ok = (direction == "LONG" and cvd_trend == "RISING") or \
             (direction == "SHORT" and cvd_trend == "FALLING")
    filters["cvd"] = {"value": cvd_trend, "required": "RISING" if direction == "LONG" else "FALLING", "passed": cvd_ok}

    if delta_ratio is not None:
        dr_ok = delta_ratio * sign >= S2_DELTA_RATIO_MIN
        filters["delta_ratio"] = {"value": round(delta_ratio, 4), "passed": dr_ok}
    else:
        filters["delta_ratio"] = {"value": None, "passed": None, "note": "N2 não ativo"}

    anti_fade = ofi_slow * sign < -ANTI_FADE_OFI_SLOW
    filters["anti_fade"] = {"ofi_slow": round(ofi_slow, 4), "triggered": anti_fade}
    if anti_fade:
        block_reasons.append(f"Anti-fade: OFI slow contra direção ({ofi_slow:.4f})")

    passed_count = sum([ofi_fast_ok, ofi_slow_ok, cvd_ok, s1_confidence >= S1_FLOW_CONFIDENCE_MIN])

    if anti_fade or len(block_reasons) >= 3:
        return False, ScalpSignalQuality.NO_TRADE, 1.0, block_reasons, filters
    if passed_count == 4:
        return True, ScalpSignalQuality.STRONG, 1.0, [], filters
    if passed_count == 3:
        return True, ScalpSignalQuality.MODERATE, 0.5, block_reasons, filters
    return False, ScalpSignalQuality.WEAK if passed_count == 2 and s1_confidence >= S1_FLOW_CONFIDENCE_MIN else ScalpSignalQuality.NO_TRADE, 1.0, block_reasons, filters


# ═══════════════════════════════════════════════════════════════════
# MODO CANDLE — Construção de barras M1 + S1 / S2
# ═══════════════════════════════════════════════════════════════════

# Mínimo de barras completas para calcular ATR
MIN_BARS_FOR_ATR = 5

# Thresholds de qualidade do candle
BODY_RATIO_STRONG   = 0.60   # candle com corpo ≥ 60% do range total
BODY_RATIO_MODERATE = 0.35   # candle com corpo ≥ 35%

# Posição vs VWAP: % de distância mínima para confirmar "acima/abaixo"
VWAP_PROXIMITY_PCT  = 0.0015  # 0.15% do preço

# Volume spike: candle atual deve ter vol ≥ N×média das últimas barras
VOLUME_SPIKE_MIN    = 1.2    # 20% acima da média
VOLUME_MIN_MODERATE = 0.90   # 90% da média (aceita moderado)

# S1 candle: confiança mínima para S2
S1_CANDLE_CONFIDENCE_MIN = 4.5


def build_m1_bars(trades_list: list, symbol: str) -> List[Dict[str, Any]]:
    """
    Agrega trades (LiveTradeRecord) em barras OHLCV de 1 minuto.
    Retorna lista de barras ordenadas cronologicamente (mais antiga → mais recente).
    """
    if not trades_list:
        return []

    bars: Dict[int, Dict] = {}

    for t in trades_list:
        # Trunca timestamp para o minuto (nanosegundos → segundos → minuto)
        ts_ns = t.ts
        ts_sec = ts_ns / 1e9
        # Minuto UTC
        minute_key = int(ts_sec // 60) * 60

        if minute_key not in bars:
            bars[minute_key] = {
                "ts": minute_key,
                "open": t.price,
                "high": t.price,
                "low": t.price,
                "close": t.price,
                "volume": t.size,
                "buy_volume": t.size if t.side == 'B' else 0.0,
                "sell_volume": t.size if t.side == 'A' else 0.0,
                "trade_count": 1,
            }
        else:
            b = bars[minute_key]
            b["high"]  = max(b["high"], t.price)
            b["low"]   = min(b["low"], t.price)
            b["close"] = t.price
            b["volume"] += t.size
            b["trade_count"] += 1
            if t.side == 'B':
                b["buy_volume"] += t.size
            elif t.side == 'A':
                b["sell_volume"] += t.size

    sorted_bars = sorted(bars.values(), key=lambda x: x["ts"])

    # Adiciona: delta_ratio, range, body, body_ratio por barra
    for b in sorted_bars:
        rng = b["high"] - b["low"]
        body = abs(b["close"] - b["open"])
        b["range"]       = round(rng, 2)
        b["body"]        = round(body, 2)
        b["body_ratio"]  = round(body / rng, 3) if rng > 0 else 0.0
        b["direction"]   = "BULL" if b["close"] >= b["open"] else "BEAR"
        total_vol = b["buy_volume"] + b["sell_volume"]
        b["delta_ratio"] = round((b["buy_volume"] - b["sell_volume"]) / total_vol, 3) if total_vol > 0 else 0.0

    return sorted_bars


def _compute_disp_30m(bars_m1: List[Dict], entry_price: float) -> Optional[float]:
    """
    Calcula deslocamento de preço nos últimos 30 minutos (D30).

    D30 = entry_price − close_30min_ago

    Valores negativos (BUY em downtrend) e positivos (SELL em uptrend) são o
    sinal discriminatório — threshold ±10/±20 pts a calibrar após N≥20 sessões.

    Retorna None se:
      - Menos de 5 barras disponíveis (buffer insuficiente / início de sessão)
      - Barra mais próxima do target está a > 120s de distância (gap Globex)

    Nota: usa bar["ts"] em epoch-seconds (int) — produto directo de build_m1_bars().
    Não usa timedelta.seconds (bug histórico) — diferença directa em segundos.
    """
    if not bars_m1 or len(bars_m1) < 5:
        return None

    last_ts    = bars_m1[-1]["ts"]          # epoch seconds
    target_ts  = last_ts - 30 * 60          # 30 minutos atrás

    closest = min(bars_m1, key=lambda b: abs(b["ts"] - target_ts))
    gap_sec = abs(closest["ts"] - target_ts)

    if gap_sec > 120:                       # gap > 2 min → dados insuficientes
        return None

    return round(entry_price - closest["close"], 2)


def compute_atr(bars: List[Dict], period: int = 10) -> Optional[float]:
    """Calcula ATR(period) usando True Range das barras M1."""
    if len(bars) < 2:
        return None
    trs = []
    for i in range(1, len(bars)):
        prev_close = bars[i - 1]["close"]
        high, low  = bars[i]["high"], bars[i]["low"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return round(sum(trs) / len(trs), 2) if trs else None
    return round(sum(trs[-period:]) / period, 2)


def compute_vwap_from_bars(bars: List[Dict]) -> Optional[float]:
    """VWAP da sessão calculado a partir das barras (approximação com midpoint × volume)."""
    total_pv = 0.0
    total_v  = 0.0
    for b in bars:
        mid = (b["high"] + b["low"] + b["close"]) / 3
        total_pv += mid * b["volume"]
        total_v  += b["volume"]
    if total_v == 0:
        return None
    return round(total_pv / total_v, 2)


# ═══════════════════════════════════════════════════════════════════
# Contexto de mercado: zona VWAP + target estrutural
# ═══════════════════════════════════════════════════════════════════

def classify_vwap_zone(
    price: float,
    vwap: float,
    u1: float, l1: float,
    u2: float, l2: float,
    u3: float, l3: float,
) -> str:
    """
    Classifica em qual zona VWAP o preço está.
    Retorna uma string legível como 'BETWEEN_VWAP_1SD_BULL'.
    """
    if price >= u3:
        return "ABOVE_3SD"
    if price >= u2:
        return "BETWEEN_2SD_3SD_BULL"
    if price >= u1:
        return "BETWEEN_1SD_2SD_BULL"
    if price >= vwap:
        return "BETWEEN_VWAP_1SD_BULL"
    if price >= l1:
        return "BETWEEN_VWAP_1SD_BEAR"
    if price >= l2:
        return "BETWEEN_1SD_2SD_BEAR"
    if price >= l3:
        return "BETWEEN_2SD_3SD_BEAR"
    return "BELOW_3SD"


def find_structural_target(
    direction: str,
    price: float,
    levels: Dict[str, Any],
    atr_fallback: float,
    symbol: str,
) -> Tuple[Optional[float], Optional[str]]:
    """
    Encontra o próximo nível estrutural na direção do trade.
    Prioridade: nível VWAP → VP (POC/VAH/VAL) → ONH/ONL → ATR fallback.
    O nível escolhido deve estar pelo menos MIN_TARGET_PTS à frente do preço
    e no máximo MAX_TARGET_PTS (evita targets muito distantes para scalp 1–3 min).
    """
    MIN_TARGET_PTS = {'MNQ': 2.0, 'MES': 1.0}.get(symbol, 2.0)
    MAX_TARGET_PTS = {'MNQ': 20.0, 'MES': 8.0}.get(symbol, 20.0)

    vwap   = levels.get("vwap", 0.0) or 0.0
    u1     = levels.get("vwap_upper_1", 0.0) or 0.0
    l1     = levels.get("vwap_lower_1", 0.0) or 0.0
    u2     = levels.get("vwap_upper_2", 0.0) or 0.0
    l2     = levels.get("vwap_lower_2", 0.0) or 0.0
    u3     = levels.get("vwap_upper_3", 0.0) or 0.0
    l3     = levels.get("vwap_lower_3", 0.0) or 0.0
    poc    = levels.get("poc", 0.0) or 0.0
    vah    = levels.get("vah", 0.0) or 0.0
    val    = levels.get("val", 0.0) or 0.0
    d1_poc = levels.get("d1_poc", 0.0) or 0.0
    d1_vah = levels.get("d1_vah", 0.0) or 0.0
    d1_val = levels.get("d1_val", 0.0) or 0.0
    onh    = levels.get("onh", 0.0) or 0.0
    onl    = levels.get("onl", 0.0) or 0.0

    def valid(lvl: float) -> bool:
        if lvl <= 0:
            return False
        dist = (lvl - price) if direction == "LONG" else (price - lvl)
        return MIN_TARGET_PTS <= dist <= MAX_TARGET_PTS

    if direction == "LONG":
        candidates = [
            (u1,     "+1σ VWAP"),
            (vwap,   "VWAP"),
            (u2,     "+2σ VWAP"),
            (u3,     "+3σ VWAP"),
            (vah,    "VAH Sessão"),
            (d1_vah, "VAH D-1"),
            (poc,    "POC Sessão"),
            (d1_poc, "POC D-1"),
            (onh,    "ONH"),
        ]
        # Ordena por distância crescente (preferência para nível mais próximo válido)
        candidates = [(lvl, lbl) for lvl, lbl in candidates if valid(lvl)]
        candidates.sort(key=lambda x: x[0] - price)
    else:
        candidates = [
            (l1,     "-1σ VWAP"),
            (vwap,   "VWAP"),
            (l2,     "-2σ VWAP"),
            (l3,     "-3σ VWAP"),
            (val,    "VAL Sessão"),
            (d1_val, "VAL D-1"),
            (poc,    "POC Sessão"),
            (d1_poc, "POC D-1"),
            (onl,    "ONL"),
        ]
        candidates = [(lvl, lbl) for lvl, lbl in candidates if valid(lvl)]
        candidates.sort(key=lambda x: price - x[0])

    if candidates:
        return candidates[0][0], candidates[0][1]

    # Fallback ATR
    atr = max(atr_fallback, ATR_MIN_FALLBACK.get(symbol, 5.0))
    tp_atr = round(price + CANDLE_TP_ATR * atr, 2) if direction == "LONG" else round(price - CANDLE_TP_ATR * atr, 2)
    return tp_atr, f"ATR×{CANDLE_TP_ATR}"


def populate_signal_levels(signal: "ScalpSignal", levels: Dict[str, Any]) -> None:
    """Preenche os campos de contexto de mercado no ScalpSignal a partir do dict de níveis."""
    if not levels:
        return
    signal.vwap_upper_1 = levels.get("vwap_upper_1")
    signal.vwap_lower_1 = levels.get("vwap_lower_1")
    signal.vwap_upper_2 = levels.get("vwap_upper_2")
    signal.vwap_lower_2 = levels.get("vwap_lower_2")
    signal.vwap_upper_3 = levels.get("vwap_upper_3")
    signal.vwap_lower_3 = levels.get("vwap_lower_3")
    signal.vwap_std     = levels.get("vwap_std")
    signal.poc          = levels.get("poc")
    signal.vah          = levels.get("vah")
    signal.val          = levels.get("val")
    signal.d1_poc       = levels.get("d1_poc")
    signal.d1_vah       = levels.get("d1_vah")
    signal.d1_val       = levels.get("d1_val")
    signal.d1_high      = levels.get("d1_high") or None
    signal.d1_low       = levels.get("d1_low")  or None
    signal.onh          = levels.get("onh")
    signal.onl          = levels.get("onl")
    signal.on_poc       = levels.get("on_poc")
    signal.ibh          = levels.get("ibh")
    signal.ibl          = levels.get("ibl")

    # Proveniência dos dados — audit trail para data_quality no snapshot
    signal.vwap_source       = levels.get("vwap_source", "none")
    signal.vp_session_source = levels.get("vp_session_source", "none")
    signal.vp_d1_source      = levels.get("vp_d1_source", "none")

    vwap = levels.get("vwap", 0.0) or 0.0
    if not signal.vwap and vwap:
        signal.vwap = vwap

    u1 = signal.vwap_upper_1 or 0.0
    l1 = signal.vwap_lower_1 or 0.0
    u2 = signal.vwap_upper_2 or 0.0
    l2 = signal.vwap_lower_2 or 0.0
    u3 = signal.vwap_upper_3 or 0.0
    l3 = signal.vwap_lower_3 or 0.0
    if vwap > 0 and u1 > 0 and l1 > 0 and signal.last_price > 0:
        signal.vwap_zone = classify_vwap_zone(signal.last_price, vwap, u1, l1, u2, l2, u3, l3)


def evaluate_s1_candle(
    bars: List[Dict],
    live_data: Dict[str, Any],
    symbol: str,
) -> Tuple[ScalpRegime, float, Optional[str], Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    S1 Modo Candle.
    Retorna: (regime, confidence, direction, atr_m1, vwap, body_ratio, volume_ratio)
    """
    if len(bars) < MIN_BARS_FOR_ATR:
        return ScalpRegime.NO_DATA, 0.0, None, None, None, None, None

    # Barras fechadas = todas exceto a atual (última ainda está se formando)
    closed_bars  = bars[:-1]
    current_bar  = bars[-1]  # barra em formação

    atr_m1 = compute_atr(closed_bars, period=min(10, len(closed_bars)))
    if atr_m1 is None:
        atr_m1 = ATR_MIN_FALLBACK.get(symbol, 5.0)

    vwap = compute_vwap_from_bars(closed_bars)
    last_price = live_data.get("last_price", current_bar["close"])

    # Última barra fechada (sinal principal)
    last_closed = closed_bars[-1] if closed_bars else current_bar
    direction_candle = last_closed["direction"]
    body_ratio = last_closed["body_ratio"]

    # Volume ratio vs média das 10 barras anteriores
    recent_vols = [b["volume"] for b in closed_bars[-11:-1]] if len(closed_bars) >= 11 else [b["volume"] for b in closed_bars[:-1]]
    avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 0.0
    volume_ratio = round(last_closed["volume"] / avg_vol, 2) if avg_vol > 0 else 1.0

    # Posição do preço vs VWAP
    if vwap is not None and vwap > 0:
        vwap_dist_pct = (last_price - vwap) / vwap
    else:
        vwap_dist_pct = 0.0

    above_vwap = vwap_dist_pct > VWAP_PROXIMITY_PCT
    below_vwap = vwap_dist_pct < -VWAP_PROXIMITY_PCT
    near_vwap  = not above_vwap and not below_vwap

    # Sequência de candles: verifica 2 últimas barras fechadas alinhadas
    if len(closed_bars) >= 2:
        prev_closed = closed_bars[-2]
        two_bar_aligned = prev_closed["direction"] == last_closed["direction"]
    else:
        two_bar_aligned = False

    confidence = 0.0
    regime     = ScalpRegime.NEUTRAL
    direction  = None

    # ── MOMENTUM — candle forte + posição vs VWAP confirmada ──
    if direction_candle == "BULL" and body_ratio >= BODY_RATIO_STRONG and above_vwap:
        confidence = 5.0
        confidence += (body_ratio - BODY_RATIO_STRONG) * 10   # até +4 pts
        confidence += 0.5 if two_bar_aligned else 0
        confidence += 0.5 if volume_ratio >= VOLUME_SPIKE_MIN else 0
        confidence  = min(10.0, confidence)
        regime = ScalpRegime.MOMENTUM_BULL
        direction = "LONG"

    elif direction_candle == "BEAR" and body_ratio >= BODY_RATIO_STRONG and below_vwap:
        confidence = 5.0
        confidence += (body_ratio - BODY_RATIO_STRONG) * 10
        confidence += 0.5 if two_bar_aligned else 0
        confidence += 0.5 if volume_ratio >= VOLUME_SPIKE_MIN else 0
        confidence  = min(10.0, confidence)
        regime = ScalpRegime.MOMENTUM_BEAR
        direction = "SHORT"

    # ── MOMENTUM MODERADO — candle moderado alinhado com VWAP ──
    elif direction_candle == "BULL" and body_ratio >= BODY_RATIO_MODERATE and above_vwap:
        confidence = 3.5 + body_ratio * 3 + (0.5 if two_bar_aligned else 0)
        confidence = min(10.0, confidence)
        regime = ScalpRegime.MOMENTUM_BULL
        direction = "LONG"

    elif direction_candle == "BEAR" and body_ratio >= BODY_RATIO_MODERATE and below_vwap:
        confidence = 3.5 + body_ratio * 3 + (0.5 if two_bar_aligned else 0)
        confidence = min(10.0, confidence)
        regime = ScalpRegime.MOMENTUM_BEAR
        direction = "SHORT"

    # ── REVERSAL — pullback no VWAP com rejeição (near_vwap + candle contra-tendência) ──
    elif near_vwap and direction_candle == "BULL" and body_ratio >= BODY_RATIO_MODERATE:
        # Candle bullish no VWAP — possível suporte
        confidence = 3.0 + body_ratio * 4 + (0.5 if volume_ratio >= VOLUME_SPIKE_MIN else 0)
        confidence = min(8.0, confidence)
        regime = ScalpRegime.REVERSAL_BULL
        direction = "LONG"

    elif near_vwap and direction_candle == "BEAR" and body_ratio >= BODY_RATIO_MODERATE:
        confidence = 3.0 + body_ratio * 4 + (0.5 if volume_ratio >= VOLUME_SPIKE_MIN else 0)
        confidence = min(8.0, confidence)
        regime = ScalpRegime.REVERSAL_BEAR
        direction = "SHORT"

    return regime, round(confidence, 1), direction, atr_m1, vwap, body_ratio, volume_ratio


# ═══════════════════════════════════════════════════════════════════
# S3 — Execução
# ═══════════════════════════════════════════════════════════════════

def compute_s3_params_flow(
    symbol: str, direction: str, last_price: float,
    risk_modifier: float, quantity: int = 1,
) -> Dict[str, Any]:
    """S3 Modo Fluxo: SL/TP/BE em ticks fixos."""
    params = SCALP_PARAMS.get(symbol, SCALP_PARAMS['MNQ'])
    sl_pts = params['sl_ticks'] * TICK_SIZE
    tp_pts = params['tp_ticks'] * TICK_SIZE
    be_pts = params['be_ticks'] * TICK_SIZE

    if direction == "LONG":
        sl_price = round(last_price - sl_pts, 2)
        tp_price = round(last_price + tp_pts, 2)
        action   = "buy"
    else:
        sl_price = round(last_price + sl_pts, 2)
        tp_price = round(last_price - tp_pts, 2)
        action   = "sell"

    return {
        "action": action, "entry_price": last_price,
        "stop_loss_price": sl_price, "take_profit_price": tp_price,
        "breakeven": round(be_pts, 2),
        "quantity": max(1, round(quantity * risk_modifier)),
        "sl_pts": sl_pts, "tp_pts": tp_pts, "be_pts": be_pts,
        "atr_m1": None,
    }


# ═══════════════════════════════════════════════════════════════════
# ScalpEngine — Orquestrador
# ═══════════════════════════════════════════════════════════════════

class ScalpEngine:
    def __init__(self, live_data_service, delta_zonal_service, levels_getter=None,
                 macro_context_getter=None):
        self._live   = live_data_service
        self._dzs    = delta_zonal_service
        self._levels_getter        = levels_getter         # async callable: (symbol) -> Dict
        self._macro_context_getter = macro_context_getter  # async callable: (symbol, price) -> Dict
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last_signal: Dict[str, ScalpSignal] = {}
        self._mode: ScalpMode = ScalpMode.FLOW  # modo atual
        # HF1+HF2: cache de resultado de zonas — partilhado entre engine e rota /zones
        self._last_zones_cache: Dict[str, Dict[str, Any]] = {}
        # {symbol: {'result': dict, 'atr': float, 'ts': float}}
        # R2: contador de ciclos consecutivos com fluxo zero por símbolo (ghost feed)
        self._ghost_cycles: Dict[str, int] = {}
        # P0: cache de deque de trades por símbolo — TTL 1s evita cópia O(n) redundante
        # quando evaluate() é chamado por múltiplos callers no mesmo segundo (poll + snapshot)
        self._trades_cache: Dict[str, tuple] = {}  # symbol -> (monotonic_ts, list)
        # Cache de levels (VWAP/VP/ONH/ONL) — TTL 5s: levels mudam no máximo 1×/min
        # evita round-trip ao levels_getter quando push_loop (2s) e auto_trader (5s) coincidem
        self._levels_cache: Dict[str, tuple] = {}  # symbol -> (monotonic_ts, dict)
        # Cache do resultado completo de evaluate() — TTL 2s (chave: symbol+mode)
        # Múltiplos callers (push_loop 2s, auto_trader 5s, HTTP poll, snapshot 30s) que cheguem
        # dentro do mesmo janela recebem o resultado já calculado sem re-executar o pipeline.
        # O auto_trader ainda usa o resultado para decidir entradas — 2s é seguro para hold 1–3min.
        self._eval_result_cache: Dict[tuple, tuple] = {}  # (symbol, mode) -> (monotonic_ts, ScalpSignal)
        # RTH Open price — primeiro preço capturado após 09:30 ET, por símbolo+data (chave: "SYMBOL:YYYY-MM-DD")
        # Reset automático por data: nova chave = novo dia; entradas antigas não consomem memória significativa.
        self._rth_open_prices: Dict[str, float] = {}  # "SYMBOL:YYYY-MM-DD" -> float

        # Item 3: Session HOD/LOD — para Fix 3c (range_consumed)
        self._session_hod: Dict[str, float] = {}   # "SYMBOL:YYYY-MM-DD" -> float
        self._session_lod: Dict[str, float] = {}   # "SYMBOL:YYYY-MM-DD" -> float

        # Item 4: regime snapshot counter — para Fix 3b (snapshots_in_regime)
        self._regime_snapshot_counts: Dict[str, int] = {}   # symbol -> count consecutivos no regime actual
        self._last_regime: Dict[str, str] = {}              # symbol -> regime.value anterior

    def set_mode(self, mode: str):
        try:
            self._mode = ScalpMode(mode.upper())
            # Invalida cache de resultados: troca de modo pode retornar sinal do modo anterior
            # se o mesmo modo for reativado dentro da janela de 2s do cache.
            self._eval_result_cache.clear()
            logger.info(f"ScalpEngine: modo alterado para {self._mode}")
        except ValueError:
            logger.warning(f"ScalpEngine: modo inválido '{mode}', mantendo {self._mode}")

    def get_mode(self) -> str:
        return self._mode.value

    def _get_lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._locks:
            self._locks[symbol] = asyncio.Lock()
        return self._locks[symbol]

    def _get_live_data(self, symbol: str) -> Dict[str, Any]:
        try:
            if hasattr(self._live, 'get_live_data'):
                return self._live.get_live_data(symbol) or {}
            if hasattr(self._live, 'buffers') and symbol in self._live.buffers:
                return self._live.buffers[symbol].get_data()
        except Exception as e:
            logger.warning(f"ScalpEngine: erro get_live_data {symbol}: {e}")
        return {}

    def _get_trades(self, symbol: str) -> list:
        try:
            cached_ts, cached_list = self._trades_cache.get(symbol, (0.0, None))
            if cached_list is not None and (time.monotonic() - cached_ts) < 1.0:
                return cached_list
            if hasattr(self._live, 'buffers') and symbol in self._live.buffers:
                trades = list(self._live.buffers[symbol].trades)
                self._trades_cache[symbol] = (time.monotonic(), trades)
                return trades
        except Exception as e:
            logger.warning(f"ScalpEngine: erro get_trades {symbol}: {e}")
        return []

    def _get_delta_ratio(self, symbol: str) -> Optional[float]:
        try:
            state = self._dzs.get_state(symbol)
            if state:
                best_dr, best_vol = None, 0
                for _, level_data in state.items():
                    if isinstance(level_data, dict):
                        vol = level_data.get("total_volume", 0)
                        dr  = level_data.get("delta_ratio")
                        if vol > best_vol and dr is not None:
                            best_vol, best_dr = vol, dr
                return best_dr
        except Exception:
            pass
        return None

    async def _get_levels(self, symbol: str) -> Dict[str, Any]:
        """Busca o contexto de mercado (VWAP bands, VP, ONH/ONL) via levels_getter.
        Cache de 5s: evita round-trip quando múltiplos callers avaliam no mesmo ciclo."""
        if self._levels_getter is None:
            return {}
        cached_ts, cached_lvl = self._levels_cache.get(symbol, (0.0, None))
        if cached_lvl is not None and (time.monotonic() - cached_ts) < 5.0:
            return cached_lvl
        try:
            lvl = await self._levels_getter(symbol) or {}
            self._levels_cache[symbol] = (time.monotonic(), lvl)
            return lvl
        except Exception as e:
            logger.warning(f"ScalpEngine: erro get_levels {symbol}: {e}")
            return self._levels_cache.get(symbol, (0.0, {}))[1] or {}  # stale data on error

    async def evaluate(self, symbol: str, quantity: int = 1, mode: Optional[str] = None, disabled_zone_types: Optional[List[str]] = None, zone_min_score_overrides: Optional[Dict[str, float]] = None, zone_min_confluence_overrides: Optional[Dict[str, float]] = None, r1_block: bool = True, r2_block: bool = True) -> ScalpSignal:
        """Avalia sinal completo (S1→S2→S3) no modo atual (ou no modo fornecido).

        Cache de resultado (2s TTL): quando múltiplos callers (push_loop, auto_trader, HTTP poll,
        snapshot) chegam dentro da mesma janela de 2s, o pipeline é executado apenas uma vez.
        O auto_trader usa o resultado para decisão de entrada — 2s é seguro para hold 1-3min.
        """
        effective_mode = ScalpMode(mode.upper()) if mode else self._mode

        # ── Cache de resultado: evita re-computação dentro do mesmo ciclo de 2s ──
        _cache_key = (symbol, effective_mode.value)
        _cached_ts, _cached_sig = self._eval_result_cache.get(_cache_key, (0.0, None))
        if _cached_sig is not None and (time.monotonic() - _cached_ts) < 2.0:
            return _cached_sig

        async with self._get_lock(symbol):
            # Double-check dentro do lock: outra corrotina pode ter calculado enquanto esperávamos
            _cached_ts2, _cached_sig2 = self._eval_result_cache.get(_cache_key, (0.0, None))
            if _cached_sig2 is not None and (time.monotonic() - _cached_ts2) < 2.0:
                return _cached_sig2

            _eval_t0 = time.perf_counter()
            signal = ScalpSignal()
            signal.symbol    = symbol
            signal.timestamp = datetime.now(timezone.utc).isoformat()
            signal.mode      = effective_mode.value

            live_data = self._get_live_data(symbol)
            signal.ofi_fast        = live_data.get("ofi_fast", 0.0)
            signal.ofi_slow        = live_data.get("ofi_slow", 0.0)
            signal.absorption_flag = live_data.get("absorption_flag", False)
            signal.absorption_side = live_data.get("absorption_side", "NONE")
            signal.cvd             = live_data.get("cvd", 0.0)
            signal.cvd_trend       = live_data.get("cvd_trend", "NEUTRAL")
            signal.last_price      = live_data.get("last_price", 0.0)
            signal.feed_connected  = live_data.get("connected", False)
            # Campos adicionais para snapshot/AutoTune — capturados para todos os modos
            signal.speed_ratio     = live_data.get("speed_ratio")
            _now_utc_se  = datetime.now(timezone.utc)
            # DST-aware RTH open: 09:30 ET resolves to 13:30 UTC (EDT) or 14:30 UTC (EST)
            _now_et_se   = _now_utc_se.astimezone(_ENGINE_ET)
            _rth_open_et = _now_et_se.replace(hour=9, minute=30, second=0, microsecond=0)
            _rth_open_utc = _rth_open_et.astimezone(timezone.utc)
            _raw_session_min       = (_now_utc_se - _rth_open_utc).total_seconds() / 60.0
            signal.session_minutes = max(0.0, _raw_session_min)

            # ── RTH Open Price: capturar primeiro preço após 09:30 ET (por símbolo+data) ──
            # Chave inclui data → reset automático a cada dia sem limpeza explícita.
            # Captura ocorre na primeira avaliação em que _raw_session_min >= 0 (já em RTH).
            _today_str     = _now_et_se.strftime("%Y-%m-%d")
            _rth_open_key  = f"{symbol}:{_today_str}"
            if _raw_session_min >= 0 and _rth_open_key not in self._rth_open_prices:
                _rth_open_px = signal.last_price
                if _rth_open_px and _rth_open_px > 0:
                    self._rth_open_prices[_rth_open_key] = _rth_open_px
                    logger.info(
                        "RTH Open captured: %s = %.2f (session_min=%.1f, date=%s)",
                        symbol, _rth_open_px, _raw_session_min, _today_str,
                    )
            signal.rth_open_price = self._rth_open_prices.get(_rth_open_key)

            # ── Item 3: Actualizar session HOD/LOD (para Fix 3c range_consumed) ───────
            # Apenas durante RTH (_raw_session_min >= 0). Reset automático por data.
            if _raw_session_min >= 0 and signal.last_price > 0:
                _cur_hod = self._session_hod.get(_rth_open_key, 0.0)
                _cur_lod = self._session_lod.get(_rth_open_key, float('inf'))
                if signal.last_price > _cur_hod:
                    self._session_hod[_rth_open_key] = signal.last_price
                if signal.last_price < _cur_lod:
                    self._session_lod[_rth_open_key] = signal.last_price

            # ── R5: Boundary de sessão RTH (09:30–16:00 ET, DST-aware) ───────────────
            # Permite avaliação durante Globex (18:00–09:30 ET, exc. halt 17:00–18:00 ET).
            # Fora de RTH e Globex → MARKET_CLOSED (early exit, evita sub-fetches desnecessários).
            _outside_rth   = _raw_session_min < 0 or _raw_session_min > SCALP_RTH_END_MINUTES
            _in_globex     = _is_globex_session(_now_et_se)
            if _outside_rth and not _in_globex:
                signal.scalp_status = "MARKET_CLOSED"
                self._last_signal[symbol] = signal
                self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                return signal

            # Busca contexto de mercado (VWAP/VP/ONH/ONL).
            # Em RTH: dados em tempo real. Em Globex: usa o cache da sessão RTH anterior —
            # VP D-1 e ONH/ONL são precisamente as zonas mais relevantes em overnight.
            market_levels = await self._get_levels(symbol)
            # Injectar rth_open_price nos levels — disponível em evaluate_zones() para Fix 5 bias
            if signal.rth_open_price:
                market_levels = {**market_levels, "rth_open_price": signal.rth_open_price}
            populate_signal_levels(signal, market_levels)

            # ── Gate de conexão WebSocket ──────────────────────────────────────────────
            if not live_data or not live_data.get("connected"):
                signal.scalp_status = "NO_DATA"
                self._last_signal[symbol] = signal
                self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                return signal

            # ── R1: SQS — Scalp Quality Score (buffer de trades) ──────────────────────
            # Fetch once — reused by ZONES ATR cache-miss and CANDLE to avoid
            # copying the full deque (up to maxlen trades) multiple times per call.
            _trades_sqs = self._get_trades(symbol)
            _n_trades   = len(_trades_sqs)
            if _n_trades < SCALP_SQS_MIN_TRADES:
                signal.scalp_status = "FEED_LOW_QUALITY"
                logger.debug(
                    "SQS [%s]: trades insuficientes (%d < %d)",
                    symbol, _n_trades, SCALP_SQS_MIN_TRADES,
                )
                self._last_signal[symbol] = signal
                self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                return signal

            _last_rec      = _trades_sqs[-1]
            _last_ts_ns    = getattr(_last_rec, 'ts', None) or (_last_rec.get("ts", 0) if isinstance(_last_rec, dict) else 0)
            _trade_age_s   = (time.time() - _last_ts_ns / 1e9) if _last_ts_ns > 0 else 9999.0
            if _trade_age_s > SCALP_SQS_STALE_TRADE_S:
                signal.scalp_status = "FEED_LOW_QUALITY"
                logger.debug(
                    "SQS [%s]: último trade há %.1fs (limite: %.0fs)",
                    symbol, _trade_age_s, SCALP_SQS_STALE_TRADE_S,
                )
                self._last_signal[symbol] = signal
                self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                return signal

            # ── R2: Ghost feed — conectado mas sem fluxo real ──────────────────────────
            _all_zero = (
                abs(live_data.get("ofi_fast", 0.0)) == 0.0
                and abs(live_data.get("ofi_slow", 0.0)) == 0.0
                and abs(live_data.get("cvd", 0.0)) == 0.0
            )
            if _all_zero:
                self._ghost_cycles[symbol] = self._ghost_cycles.get(symbol, 0) + 1
                if self._ghost_cycles[symbol] >= SCALP_SQS_GHOST_CYCLES:
                    signal.scalp_status = "FEED_GHOST"
                    logger.warning(
                        "SQS [%s]: ghost feed detectado (%d ciclos zero-flow)",
                        symbol, self._ghost_cycles[symbol],
                    )
                    self._last_signal[symbol] = signal
                    self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                    return signal
            else:
                self._ghost_cycles[symbol] = 0  # reset quando fluxo real volta

            delta_ratio = self._get_delta_ratio(symbol)
            signal.delta_ratio = delta_ratio

            # ── Modo Zonas ──
            if effective_mode == ScalpMode.ZONES:
                # Em RTH: requer market_levels preenchido. Em Globex: basta ter preço válido
                # (zonas são extraídas do VP D-1 / ONH-ONL histórico disponível em cache).
                _levels_empty = not market_levels
                if signal.last_price <= 0 or (_levels_empty and not _in_globex):
                    signal.scalp_status = "NO_DATA"
                    self._last_signal[symbol] = signal
                    self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                    return signal

                # Minutos desde abertura RTH (calculado na extracção de live_data, partilhado)
                on_session_minutes = signal.session_minutes

                # ── HF1: ATR com cache de 60s — evita reconstrução de N trades a cada poll ──
                # R3/R6: rastreia se ATR vem de barras reais ou de fallback fixo
                # F7: bars_z sempre construído (EMA pullback necessita barras frescas)
                _atr_from_bars = False
                bars_z: List[Dict] = []
                try:
                    bars_z = build_m1_bars(_trades_sqs, symbol)
                except Exception:
                    bars_z = []

                # ── D30 Fase 2: gate activo ─────────────────────────────────────────
                # Calibrado na simulação Apr 14–17 (57 trades):
                #   |D30| > 20 pts → BLOCKED  (WR=33%, EV=−78.94 — hard reject)
                #   |D30| 10–20   → RISK      (WR=43%, EV=−11.00 — downgrade)
                #   |D30| ≤ 10    → OK        (WR=56%, EV=+3.88)
                signal.disp_30m = _compute_disp_30m(bars_z, signal.last_price)
                if signal.disp_30m is not None:
                    _d30_abs = abs(signal.disp_30m)
                    if _d30_abs > 20.0:
                        signal.d30_state = "BLOCKED"
                    elif _d30_abs > 10.0:
                        signal.d30_state = "RISK"
                    else:
                        signal.d30_state = "OK"
                else:
                    signal.d30_state = None

                if signal.d30_state == "BLOCKED":
                    signal.scalp_status    = "D30_BLOCKED"
                    signal.block_gate      = "D30_RANGE"
                    signal.s2_block_reasons = [f"D30={signal.disp_30m:+.1f}pts (±20 threshold)"]
                    logger.info(
                        "D30 BLOCKED [%s]: disp_30m=%+.1f pts — trade rejeitado",
                        symbol, signal.disp_30m,
                    )
                    self._last_signal[symbol] = signal
                    self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                    return signal

                atr_cached = _atr_cache.get(symbol)
                if atr_cached and (time.monotonic() - atr_cached['ts']) < ATR_CACHE_TTL:
                    atr_z          = atr_cached['atr']
                    _atr_from_bars = atr_cached.get('from_bars', True)
                else:
                    atr_z = None
                    try:
                        if len(bars_z) >= MIN_BARS_FOR_ATR + 1:
                            _, _, _, atr_z, _, _, _ = evaluate_s1_candle(bars_z, live_data, symbol)
                            if atr_z:
                                _atr_from_bars = True
                                _atr_cache[symbol] = {'atr': atr_z, 'ts': time.monotonic(), 'from_bars': True}
                    except Exception:
                        pass
                    if not atr_z:
                        # Warm-start bridge: se o cache expirou mas havia um seed válido
                        # (from_bars=True de snapshot), re-extende em vez de cair no fallback.
                        # Mantém o engine fora do WARMING_UP até que barras suficientes acumulem.
                        if atr_cached and atr_cached.get('from_bars', False):
                            atr_z          = atr_cached['atr']
                            _atr_from_bars = True
                            _atr_cache[symbol] = {'atr': atr_z, 'ts': time.monotonic(), 'from_bars': True}
                            logger.debug(
                                "ATR warm-start bridge [%s]: seed=%.2f re-extendido (need=%d barras)",
                                symbol, atr_z, MIN_BARS_FOR_ATR + 1,
                            )
                        else:
                            # Fallback fixo — NÃO cacheia: força recalcular no próximo ciclo
                            atr_z = ATR_MIN_FALLBACK.get(symbol, 5.0)

                signal.atr_m1    = atr_z
                signal.atr_source = "live" if _atr_from_bars else "fallback"

                # ── R6: Warm-up gate — bloqueia ZONES sem ATR calibrado ────────────────
                if not _atr_from_bars:
                    signal.scalp_status = "WARMING_UP"
                    logger.debug("ZONES [%s]: ATR em fallback — aguardando barras M1 (%d necessárias)", symbol, SCALP_MIN_WARMUP_BARS)
                    self._last_signal[symbol] = signal
                    self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                    return signal

                # ── F5: macro_context (Term Structure + Gamma Levels) ─────────
                macro_ctx = {}
                if self._macro_context_getter is not None:
                    try:
                        macro_ctx = await self._macro_context_getter(
                            symbol, signal.last_price
                        ) or {}
                    except Exception as _mc_err:
                        logger.warning(
                            f"ScalpEngine: macro_context_getter falhou para {symbol}: {_mc_err}"
                        )
                signal.macro_context = macro_ctx  # persiste para snapshot/replay

                # Item 3b: snapshots_in_regime — passa contador de snapshots no regime actual
                _snap_count = self._regime_snapshot_counts.get(symbol, 999)

                # Item 3c: session HOD/LOD para range_consumed
                _s_hod = self._session_hod.get(_rth_open_key, 0.0)
                _s_lod = self._session_lod.get(_rth_open_key, float('inf'))
                _s_lod_safe = _s_lod if _s_lod != float('inf') else 0.0

                zones_result = evaluate_zones(
                    price=signal.last_price,
                    levels=market_levels,
                    live_data=live_data,
                    delta_ratio=delta_ratio,
                    atr=atr_z,
                    symbol=symbol,
                    quantity=quantity,
                    on_session_minutes=on_session_minutes,
                    macro_context=macro_ctx,
                    m1_bars=bars_z if bars_z else None,
                    s1_regime_value=signal.s1_regime if signal.s1_regime else None,
                    disabled_zone_types=disabled_zone_types or [],
                    zone_min_score_overrides=zone_min_score_overrides or {},
                    zone_min_confluence_overrides=zone_min_confluence_overrides or {},
                    snapshots_in_regime=_snap_count,
                    session_hod=_s_hod,
                    session_lod=_s_lod_safe,
                    d1_high=signal.d1_high or 0.0,
                    d1_low=signal.d1_low or 0.0,
                )

                # ── Item 4: Actualizar regime snapshot counts ─────────────────────────
                _new_regime_val = zones_result.get("regime") or "UNDEFINED"
                _prev_regime_val = self._last_regime.get(symbol, "")
                if _new_regime_val == _prev_regime_val:
                    self._regime_snapshot_counts[symbol] = self._regime_snapshot_counts.get(symbol, 0) + 1
                else:
                    self._regime_snapshot_counts[symbol] = 1  # novo regime — reset
                    if _prev_regime_val:
                        logger.debug(
                            "[Item 4] %s regime mudou: %s → %s (snapshots reset=1)",
                            symbol, _prev_regime_val, _new_regime_val,
                        )
                self._last_regime[symbol] = _new_regime_val

                # ── HF2: Persiste resultado para partilha com rota /zones ──────────────
                self._last_zones_cache[symbol] = {
                    'result': zones_result,
                    'atr':    atr_z,
                    'ts':     time.monotonic(),
                }

                signal.day_regime           = zones_result.get("regime")
                signal.zones_nearby         = zones_result.get("all_zones", [])
                signal.active_zone          = zones_result.get("best_zone")
                # zone_type_str: campo de 1ª classe — tripla cadeia de fallback:
                #   1. zones_result["zone_type"]   — novo campo top-level de evaluate_zones()
                #   2. best_zone["type"]           — campo aninhado (mesmo valor, redundante)
                #   3. active_zone.get("type")     — compatibilidade histórica
                _bz = zones_result.get("best_zone") or {}
                signal.zone_type_str        = (
                    zones_result.get("zone_type")
                    or _bz.get("type")
                    or (signal.active_zone.get("type") if signal.active_zone else None)
                )
                if zones_result.get("status") == "ACTIVE_SIGNAL" and not signal.zone_type_str:
                    logger.warning(
                        "ZONES [%s] ACTIVE_SIGNAL sem zone_type — best_zone=%s",
                        symbol, _bz
                    )
                signal.zone_quality         = zones_result.get("quality")
                signal.scalp_status         = zones_result.get("status", "NO_SIGNAL")
                signal.s2_block_reasons     = zones_result.get("block_reasons", [])
                signal.s2_passed            = (zones_result.get("status") == "ACTIVE_SIGNAL")
                # block_gate: código estruturado do gate que bloqueou (para análise de stacking)
                signal.block_gate           = _derive_zone_block_gate(
                    zones_result.get("status", "NO_SIGNAL"),
                    zones_result.get("block_reasons", []),
                    zones_result.get("gamma_block_reasons", []),
                )
                signal.zone_score_breakdown = zones_result.get("score_breakdown")
                signal.zone_active_params   = zones_result.get("active_params")
                # F7: Gate 5 (EMA_PRICE_COOLDOWN) e Gate 6 (EMA_ZONE_TYPE_COOLDOWN)
                signal.ema_block_reasons    = zones_result.get("ema_block_reasons", [])
                # F5-2-B: SHORT_GAMMA suppression tracking
                signal.gamma_block_reasons  = zones_result.get("gamma_block_reasons", [])
                # Fix A shadow: lista de SIGMA2_FADE_BUY removidas em RTH_OPEN
                # (registada em signal_log via Fix C para análise pós-sessão)
                signal.fix_a_shadow_zones   = zones_result.get("fix_a_shadow_zones", [])
                # Item 5/6: bias intraday e confirmação CVD de regime
                signal.price_vs_rth_open    = zones_result.get("price_vs_rth_open", "NEUTRAL")
                signal.regime_bias          = zones_result.get("regime_bias", "NEUTRAL")
                signal.regime_cvd_conf      = zones_result.get("regime_cvd_conf", "NEUTRAL")

                # ── D30 RISK gate: downgrade MODERATE/WEAK → D30_RISK ────────────────
                # STRONG pode sobreviver a D30 RISK (10–20 pts); MODERATE não.
                if (signal.d30_state == "RISK"
                        and signal.scalp_status == "ACTIVE_SIGNAL"
                        and signal.zone_quality != "STRONG"):
                    signal.scalp_status    = "D30_RISK"
                    signal.s2_passed       = False
                    signal.s2_block_reasons = (signal.s2_block_reasons or []) + [
                        f"D30_RISK={signal.disp_30m:+.1f}pts (10–20, quality={signal.zone_quality})"
                    ]
                    logger.info(
                        "D30 RISK [%s]: disp_30m=%+.1f pts, quality=%s — sinal bloqueado",
                        symbol, signal.disp_30m, signal.zone_quality,
                    )

                s3_zone = zones_result.get("s3")
                if s3_zone and signal.scalp_status == "ACTIVE_SIGNAL":
                    signal.s3_action           = s3_zone["action"]
                    signal.s3_entry_price      = s3_zone["entry_price"]
                    signal.s3_stop_loss_price  = s3_zone["stop_loss_price"]
                    signal.s3_take_profit_price= s3_zone["take_profit_price"]
                    signal.s3_breakeven        = s3_zone["be_pts"]
                    signal.s3_quantity         = s3_zone["quantity"]
                    signal.s1_direction        = "LONG" if s3_zone["action"] == "buy" else "SHORT"
                    signal.zone_s3_extra = {
                        k: v for k, v in s3_zone.items()
                        if k not in ("action", "entry_price", "stop_loss_price",
                                     "take_profit_price", "breakeven", "quantity")
                    }
                    _quality_map = {
                        "STRONG": ScalpSignalQuality.STRONG,
                        "MODERATE": ScalpSignalQuality.MODERATE,
                        "WEAK": ScalpSignalQuality.WEAK,
                    }
                    signal.s2_quality = _quality_map.get(
                        signal.zone_quality, ScalpSignalQuality.MODERATE
                    )

                # ── S1 Flow context — sempre populado em bi-modo ──────────────────────
                # O branch ZONES não chama evaluate_s1_flow → signal.s1_regime ficava
                # NO_DATA em 100% dos snapshots ZONES. Isto cega o AutoTune: não sabe
                # se a zona foi tocada em regime bullish, bearish ou neutro.
                # Solução: chamar evaluate_s1_flow aqui (função pura, O(1), sem I/O)
                # para popular s1_regime/confidence em TODOS os snapshots,
                # independentemente do modo. Preserva a direcção das zonas se já definida.
                _flow_regime, _flow_conf, _flow_dir = evaluate_s1_flow(live_data)
                signal.s1_regime     = _flow_regime
                signal.s1_confidence = _flow_conf
                if signal.s1_direction is None:
                    signal.s1_direction = _flow_dir
                logger.debug(
                    "ZONES [%s] S1 context: regime=%s conf=%.2f (bi-modal enrichment)",
                    symbol, _flow_regime.value if _flow_regime else "?", _flow_conf or 0.0,
                )

                # ── R1/R2: gates sessão×qualidade/regime ─────────────────────────────
                if signal.scalp_status == "ACTIVE_SIGNAL" and (r1_block or r2_block):
                    from services.trading_calendar_service import get_session_label as _gsl
                    _sess_now = _gsl()

                    # R1: MODERATE × RTH_MID → EV=−53.78 pts (WR=16.7%)
                    if (r1_block
                            and signal.zone_quality == "MODERATE"
                            and _sess_now == "RTH_MID"):
                        signal.scalp_status     = "BLOCKED"
                        signal.s2_passed        = False
                        signal.s2_block_reasons = (signal.s2_block_reasons or []) + [
                            "R1: MODERATE×RTH_MID bloqueado (EV=−53.78 pts)"
                        ]
                        logger.info(
                            "R1 [%s]: MODERATE×RTH_MID bloqueado — zona=%s",
                            symbol, signal.zone_type,
                        )

                    # R2: BEARISH_FLOW × RTH_MID|RTH_CLOSE → EV=−49.81 pts (WR=27.3%)
                    elif (r2_block
                            and signal.scalp_status == "ACTIVE_SIGNAL"
                            and signal.s1_regime == ScalpRegime.BEARISH_FLOW
                            and _sess_now in ("RTH_MID", "RTH_CLOSE")):
                        signal.scalp_status     = "BLOCKED"
                        signal.s2_passed        = False
                        signal.s2_block_reasons = (signal.s2_block_reasons or []) + [
                            f"R2: BEARISH_FLOW×{_sess_now} bloqueado (EV=−49.81 pts)"
                        ]
                        logger.info(
                            "R2 [%s]: BEARISH_FLOW×%s bloqueado — zona=%s",
                            symbol, _sess_now, signal.zone_type,
                        )

                self._last_signal[symbol] = signal
                self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                return signal

            # ── Modo Fluxo ──
            elif effective_mode == ScalpMode.FLOW:
                signal.atr_source = "n/a"   # FLOW usa SL/TP em ticks fixos; ATR não aplicável
                regime, confidence, direction = evaluate_s1_flow(live_data)
                signal.s1_regime     = regime
                signal.s1_confidence = confidence
                signal.s1_direction  = direction
                logger.debug(
                    "FLOW [%s] S1: regime=%s dir=%s conf=%.2f ofi_fast=%.4f",
                    symbol, regime.value if regime else "None",
                    direction, confidence or 0.0, signal.ofi_fast,
                )

                if direction is None or regime == ScalpRegime.NEUTRAL:
                    signal.scalp_status = "NO_SIGNAL"
                    self._last_signal[symbol] = signal
                    self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                    return signal

                passed, quality, risk_mod, reasons, filters = evaluate_s2_flow(
                    direction, live_data, confidence, delta_ratio
                )
                signal.s2_passed        = passed
                signal.s2_quality       = quality
                signal.s2_risk_modifier = risk_mod
                signal.s2_block_reasons = reasons
                signal.s2_filters       = filters

                if not passed:
                    signal.scalp_status = "BLOCKED" if quality == ScalpSignalQuality.NO_TRADE else "NO_SIGNAL"
                    self._last_signal[symbol] = signal
                    self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                    return signal

                if signal.last_price <= 0:
                    signal.scalp_status = "NO_SIGNAL"
                    self._last_signal[symbol] = signal
                    self._eval_result_cache[_cache_key] = (time.monotonic(), signal)
                    return signal

                s3 = compute_s3_params_flow(symbol, direction, signal.last_price, risk_mod, quantity)

            # S3 — preenche sinal
            signal.s3_action           = s3["action"]
            signal.s3_entry_price      = s3["entry_price"]
            signal.s3_stop_loss_price  = s3["stop_loss_price"]
            signal.s3_take_profit_price= s3["take_profit_price"]
            signal.s3_breakeven        = s3["breakeven"]
            signal.s3_quantity         = s3["quantity"]
            signal.scalp_status        = "ACTIVE_SIGNAL"

            _eval_ms = round((time.perf_counter() - _eval_t0) * 1000, 1)
            logger.info(
                "SIGNAL [%s] ACTIVE | modo=%s dir=%s price=%.2f "
                "SL=%.2f TP=%.2f qty=%d atr_source=%s | %.1fms",
                symbol, effective_mode.value,
                signal.s3_action.upper() if signal.s3_action else "?",
                signal.last_price,
                signal.s3_stop_loss_price or 0.0,
                signal.s3_take_profit_price or 0.0,
                signal.s3_quantity or 0,
                signal.atr_source,
                _eval_ms,
            )

            # Target estrutural — busca próximo nível VWAP/VP/ON na direção do trade
            if market_levels and s3.get("direction") is None:
                direction_for_target = s3["action"].upper()  # "buy" → "BUY"
                direction_for_target = "LONG" if direction_for_target == "BUY" else "SHORT"
            else:
                direction_for_target = direction  # já definido no escopo (LONG/SHORT)

            if market_levels and signal.last_price > 0:
                atr_fb = s3.get("atr_m1") or ATR_MIN_FALLBACK.get(symbol, 5.0)
                tgt, tgt_label = find_structural_target(
                    direction_for_target, signal.last_price, market_levels, atr_fb, symbol
                )
                signal.structural_target       = tgt
                signal.structural_target_label = tgt_label

            self._last_signal[symbol]               = signal
            self._eval_result_cache[_cache_key]     = (time.monotonic(), signal)
            return signal

    def get_last_signal(self, symbol: str) -> Optional[ScalpSignal]:
        return self._last_signal.get(symbol)
