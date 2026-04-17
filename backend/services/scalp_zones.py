"""
Scalp Zones Model — Quantum Trading Scalp
==========================================
Modelo de scalping orientado a zonas de interesse (ZOI) filtrado por regime de mercado.

Fluxo:
  1. detect_day_regime()    — classifica o regime do dia com base em VWAP + VP D-1
  2. identify_zones()       — mapeia zonas ativas para o regime atual
  3. evaluate_zone_entry()  — confirma entrada com OFI/Delta/Absorção na zona
  4. compute_zone_s3()      — gera parâmetros de ordem adaptados ao regime + zona

Regimes de Mercado (ScalpDayRegime):
  EXPANSION_BULL  — preço acima D1_VAH + acima VWAP → busca pullbacks para compra
  EXPANSION_BEAR  — preço abaixo D1_VAL + abaixo VWAP → busca rallies para venda
  ROTATION        — preço dentro da value area D-1 → fade nos extremos, magnetismo no POC
  BREAKOUT_BULL   — preço rompeu acima D1_VAH mas ainda perto → teste de aceitação
  BREAKOUT_BEAR   — preço rompeu abaixo D1_VAL mas ainda perto → teste de aceitação
  UNDEFINED       — dados insuficientes para classificar

Zona de Interesse (ZoneOfInterest):
  Cada zona tem: tipo, nível, direção esperada, tolerância, target, parâmetros de risco.

Implementações Fase 1 + Fase 2:
  F1-1: OFI slow → penalidade -1.5 pts; threshold diferenciado: fade=0.55, momentum=0.35
  F1-2: SIGMA2_FADE_SELL (+3σ) removido em EXPANSION_BULL; SIGMA2_FADE_BUY (-3σ) em EXPANSION_BEAR
  F1-3: ONH_BREAK_BUY / ONL_BREAK_SELL exigem ofi_fast > 0.30 como AND gate
  F1-4: Session VP (VAH/VAL/POC da sessão) hibernado nos primeiros 90 min RTH
  F2-1: Tolerance hard caps por símbolo (MNQ/MES) para VWAP, VP, ON
  F2-2: Zone Cooldown — (symbol, zone_type) hiberna 3 min após sinal válido (score >= 2.5)
  F2-3: ScoreBreakdown — payload estruturado com componentes de score

Melhorias implementadas:
  FIX: absorption_side — corrigido expected_abs para "SELL_ABSORBED"/"BUY_ABSORBED" (bug anterior
       usava "BUY"/"SELL" que nunca batia, fazendo o bónus +1.5 nunca disparar)
  CVD: usa cvd_trend (RISING/FALLING/NEUTRAL) em vez de sinal absoluto do CVD acumulado —
       reflecte momentum recente do delta, mais relevante para scalp de 1-3 min
  LATE_SESSION: penalidade -0.5 pts após 15:30 ET (≥360 min de sessão RTH) — via late_session_penalty
  OFI_SLOW_BLOCK: threshold diferenciado por categoria — fade=0.55 (tolerante), momentum=0.35 (exigente)
  D1_POC TARGET: target conservador para D1_POC Magnet — D1_VAH/VAL só se ≤1.5×ATR de distância,
                 senão Session VAH/VAL ou ±1σ VWAP (evita targets demasiado ambiciosos em VA largas)

Otimizações HF1/HF2/HF3:
  HF2-A: Pré-filtro de proximidade — zonas a mais de PROXIMITY_WINDOW_ATR × ATR não instanciadas
  HF2-B: Early-exit no loop de avaliação — STRONG em zona de prioridade 1 encerra loop
          FIX: loop agora itera zonas ordenadas por prioridade (sorted) para garantir que
               early-exit em priority<=2 não descarta zona priority-1 ainda não avaliada
  HF3:   Histerese de Regime diferenciada por tipo:
           ROTATION/EXPANSION/UNDEFINED: 5s (debounce de spike intrabar)
           BREAKOUT_BULL/BEAR: 60s (exige 1 barra M1 completa sustentada acima D1_VAH/abaixo D1_VAL)

Fase 5:
  F5-1: Term Structure Hard Gate — VIX/VIX3M ratio ≥1.10 suspende TODAS as zonas (HARD_STOP);
         ratio ≥1.05 suprime fade zones (ROTATION/contra-tendência) — pânico ativo.
  F5-2: Gamma Levels como ZONES — Call Wall e Put Wall dos dealers (Yahoo Finance options,
         convertidos para pontos de futuros via GammaRatioService) adicionados como zonas
         estruturais de nível 3. Gated por source=='yahoo_finance_options'; fallback omitido.
  F5-2-B: SHORT_GAMMA — GAMMA_CALL_WALL_SELL e GAMMA_PUT_WALL_BUY suprimidos em ambiente
           SHORT_GAMMA (dealers são compradores de delta na subida → Call Wall = catalisador,
           não tecto). Em LONG_GAMMA e UNKNOWN o comportamento padrão mantém-se.

Correcções arquitecturais aplicadas:
  SESSION_POC: SESSION_POC_BUY e SESSION_POC_SELL eliminados — POC move-se continuamente
               ao longo da sessão; tratá-lo como zona estática com cooldown fixo criava
               instabilidade onde o nível podia deslocar vários ATR durante o cooldown.
               SESSION_VAH e SESSION_VAL (extremos acumulados) são mais estáveis e mantidos.
  VWSD:        σ das bandas VWAP agora calculado por Volume-Weighted Standard Deviation
               em vez de std simples — barras com volume alto dominam o desvio, reflectindo
               correctamente a dispersão ponderada pelo fluxo.

Fase 6:
  F6:   Flow Base Gate — confluence_boost e gamma_modifier positivo só são somados ao score
         se o score base de fluxo (OFI fast + delta + absorção + CVD) for >= 1.5 pts.
         Impede que modificadores geográficos/macro "promovam" um sinal de fluxo fraco para
         MODERATE. Penalidades negativas de gamma_modifier passam sempre (protecção).
         ScoreBreakdown.base_flow_gated=True quando o gate foi activado.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("scalp_zones")

TICK_SIZE    = 0.25
ATR_FALLBACK = {'MNQ': 5.0, 'MES': 2.0}

# Cap máximo de SL por tipo de zona × símbolo — evita stops desproporcionais em dias de ATR elevado
# Derivado de análise empírica: SIGMA2_FADE_BUY MNQ avg SL wins=4.38pts, losses=10.62pts
# Cap a 6pts para MNQ (≈1.4× avg win SL) e 2.5pts para MES
ZONE_SL_MAX_PTS: Dict[str, Dict[str, float]] = {
    'SIGMA2_FADE_BUY':  {'MNQ': 6.0, 'MES': 2.5},
    'SIGMA2_FADE_SELL': {'MNQ': 6.0, 'MES': 2.5},
}

# ── Fase 2-1: Tetos absolutos de tolerância por símbolo ──────────────────────
TOLERANCE_CAPS: Dict[str, Dict[str, float]] = {
    'MNQ': {'vwap': 3.0, 'vp': 2.5, 'on': 2.0, 'ib': 2.0, 'gamma': 2.5},  # 1-B: 4.0→2.5
    'MES': {'vwap': 1.5, 'vp': 1.25, 'on': 1.0, 'ib': 1.0, 'gamma': 1.25},  # 1-B: 2.0→1.25
}
TOLERANCE_CAPS_DEFAULT = {'vwap': 3.0, 'vp': 2.5, 'on': 2.0, 'ib': 2.0, 'gamma': 2.5}

# ── Fase 2-2: Cooldown de zona (em memória — reset em restart é risco aceito) ─
ZONE_COOLDOWN_SECS: float = 180.0   # 3 minutos wall-clock
_zone_cooldown: Dict[Tuple[str, str], float] = {}  # (symbol, zone_type) → timestamp

# ── HF2-A: Pré-filtro de proximidade ─────────────────────────────────────────
# Zonas a mais de N×ATR do preço corrente não são instanciadas
PROXIMITY_WINDOW_ATR: float = 10.0

# ── F7: EMA Pullback Zones — constantes ──────────────────────────────────────
EMA_WARMUP_BARS       = 39        # EMA_34 + 5 barras de buffer (39 min mínimos RTH)
EMA_TOUCH_ATR_MULT    = 0.40      # limiar de toque: preço dentro de 0.4×ATR da EMA
EMA_TOUCH_CAPS: Dict[str, float] = {'MNQ': 2.5, 'MES': 0.8}
# Cap corrigido: 4.0/1.0 eram demasiado altos (cap só activava com ATR>10pts — condição extrema).
# Com ATR_M1 típico MNQ≈5-6pts: min(0.4×5.5, 2.5) = min(2.2, 2.5) = 2.2 — cap actua em ATR>6.25pts.
EMA_SL_ATR_MULT       = 0.60      # SL fixo na entrada: EMA_21 ± 0.6×ATR_m1
EMA_TP_ATR_MULT       = 1.50      # TP explícito: EMA_21 ± 1.5×ATR_m1 → R:R = 1.5/0.6 = 2.5:1
EMA_BE_ATR_MULT       = 0.40      # break-even: 0.4×ATR a favor
EMA_VWAP_PROXIMITY    = 0.30      # bónus VWAP: EMA_21 dentro de 0.30×ATR de nível VWAP
EMA_COOLDOWN_SECS     = 180.0     # cooldown por coordenada de preço (3 min)
EMA_PRICE_COOLDOWN_ATR = 2.0      # re-entrada bloqueada se EMA_21 novo < 2×ATR do anterior
# ── Interacção de cooldowns (EMA):
# Dois mecanismos coexistem — o mais restritivo que bloquear primeiro prevalece:
#   1. Zone-type cooldown (_zone_cooldown): bloqueia por 180s qualquer EMA_PULLBACK_BUY/SELL após sinal
#   2. Price-coord cooldown (_ema_price_cooldown): bloqueia re-entrada se EMA_21 actual dentro de
#      2×ATR do EMA_21 do último trade em 180s (específico ao nível de preço; liberta quando a
#      EMA se afasta suficientemente mesmo antes dos 180s expirarem)
# Rationale: o tipo-cooldown previne frequência excessiva; o price-cooldown previne re-entrada
# no mesmo nível antes da EMA se mover. Ambos protegem contra churning.

_ema_price_cooldown: Dict[Tuple[str, str], Tuple[float, float]] = {}
# {(symbol, direction): (ema21_at_entry, monotonic_timestamp)}

# ── HF3: Histerese de Regime ──────────────────────────────────────────────────
# BREAKOUT exige barra M1 completa (≥60s) para confirmar aceitação de preço acima D1_VAH/abaixo D1_VAL.
# Outros regimes confirmam em 5s (debounce de spike intrabar).
REGIME_HYSTERESIS_SECS: float  = 5.0    # histerese padrão (ROTATION, EXPANSION, UNDEFINED)
BREAKOUT_HYSTERESIS_SECS: float = 60.0  # BREAKOUT_BULL/BEAR: exige 1 barra M1 sustentada
# _BREAKOUT_REGIMES definido após ScalpDayRegime enum (ver abaixo)
_regime_hysteresis: Dict[str, Dict] = {}
# {symbol: {'active': ScalpDayRegime, 'pending': ScalpDayRegime|None, 'pending_since': float}}


def _is_zone_in_cooldown(symbol: str, zone_type_value: str) -> bool:
    key = (symbol, zone_type_value)
    last_t = _zone_cooldown.get(key)
    if last_t is None:
        return False
    return (time.monotonic() - last_t) < ZONE_COOLDOWN_SECS


def _register_zone_cooldown(symbol: str, zone_type_value: str) -> None:
    _zone_cooldown[(symbol, zone_type_value)] = time.monotonic()


# ── F7: EMA pullback cooldown por coordenada de preço ────────────────────────

def _is_ema_in_cooldown(symbol: str, direction: str, ema21: float, atr: float) -> bool:
    """Retorna True se já existe entrada recente em EMA deste símbolo/direção
    e a nova EMA_21 está dentro de 2×ATR da entrada anterior (mesmo nível de preço)."""
    key = (symbol, direction)
    entry = _ema_price_cooldown.get(key)
    if entry is None:
        return False
    last_price, last_ts = entry
    if (time.monotonic() - last_ts) >= EMA_COOLDOWN_SECS:
        return False
    return abs(ema21 - last_price) <= EMA_PRICE_COOLDOWN_ATR * atr


def _register_ema_cooldown(symbol: str, direction: str, ema21: float) -> None:
    _ema_price_cooldown[(symbol, direction)] = (ema21, time.monotonic())


# ── F7: Cálculo de EMA (seed SMA, incremental) ───────────────────────────────

def _compute_ema(closes: List[float], period: int) -> List[float]:
    """EMA com seed SMA nos primeiros `period` candles.
    Retorna série completa alinhada com closes[period-1:].
    Retorna [] se len(closes) < period."""
    if len(closes) < period:
        return []
    k = 2.0 / (period + 1)
    ema: List[float] = [sum(closes[:period]) / period]
    for c in closes[period:]:
        ema.append(c * k + ema[-1] * (1.0 - k))
    return ema


# ── F7: Builder de zonas EMA pullback ────────────────────────────────────────

_EMA_NO_BLOCK = None   # sentinela de "sem bloqueio" — usar como constante para clareza

def _build_ema_pullback_zones(
    bars_m1: List[Dict[str, Any]],
    price: float,
    atr: float,
    symbol: str,
    s1_regime_value: str,
    vwap: float,
    vwap_std: float,
    on_session_minutes: float,
) -> Tuple[List[Tuple['ZoneOfInterest', float]], Optional[str]]:
    """
    F7: Avalia zonas de pullback em EMA dinâmica.

    Retorna: (zones, block_reason)
      zones        — lista de (ZoneOfInterest, confluence_boost) para injectar em evaluate_zones()
      block_reason — str com razão de bloqueio se zones=[] por gate de cooldown ou contexto;
                     None se zones=[] por falta de condições (warm-up, regime, preço fora de zona)

    Razões de bloqueio reportadas (propagadas para result["ema_block_reasons"] no snapshot):
      "EMA_PRICE_COOLDOWN"  — Gate 5: coordenada de preço em cooldown (180s, ±2×ATR do último trade)
      None                  — gates 1–4 ou preço fora do limiar de toque (estado normal, não bloqueio)

    Arquitectura EMA:
      EMA 21 — nível de zona primário (suporte/resistência dinâmico)
      EMA 34 — confirmador de cluster (convergência com EMA 21 → boost)
      EMA  9 — trigger de entrada (preço toca EMA 9 enquanto EMA 21 próxima)

    Qualidade por convergência:
      EMA9 + EMA21 + EMA34 dentro do limiar → base_boost = 1.0 (potential STRONG)
      EMA21 + EMA34 (sem EMA9 trigger)       → base_boost = 0.5 (potential MODERATE)
      EMA21 sozinha + EMA9 trigger            → base_boost = 0.0 (depende de OFI/CVD)

    Bónus VWAP: EMA_21 dentro de 0.30×ATR de um nível VWAP → +0.5 pts.

    Gates obrigatórios (em ordem — o primeiro a falhar prevalece):
      1. Warm-up: ≥ EMA_WARMUP_BARS (39) barras M1
      2. Sessão: on_session_minutes ≥ EMA_WARMUP_BARS
      3. Regime: s1_regime_value BULLISH_FLOW (→ LONG) ou BEARISH_FLOW (→ SHORT)
      4. VWAP context: LONG exige price ≥ vwap; SHORT exige price ≤ vwap
      5. Cooldown de coordenada de preço → block_reason="EMA_PRICE_COOLDOWN"
    """
    # ── Gate 1: warm-up barras (estado normal pre-mercado, sem block_reason) ──
    if len(bars_m1) < EMA_WARMUP_BARS:
        return ([], _EMA_NO_BLOCK)

    # ── Gate 2: sessão mínima (estado normal no início da sessão) ─────────────
    if on_session_minutes < EMA_WARMUP_BARS:
        return ([], _EMA_NO_BLOCK)

    # ── Gate 3: regime direcional ─────────────────────────────────────────────
    if s1_regime_value == "BULLISH_FLOW":
        direction = "LONG"
    elif s1_regime_value == "BEARISH_FLOW":
        direction = "SHORT"
    else:
        return ([], _EMA_NO_BLOCK)

    # ── Gate 4: VWAP context ──────────────────────────────────────────────────
    if vwap > 0:
        if direction == "LONG" and price < vwap:
            return ([], _EMA_NO_BLOCK)
        if direction == "SHORT" and price > vwap:
            return ([], _EMA_NO_BLOCK)

    # ── Calcular EMAs ─────────────────────────────────────────────────────────
    closes = [b["close"] for b in bars_m1 if b.get("close") is not None]
    if len(closes) < EMA_WARMUP_BARS:
        return ([], _EMA_NO_BLOCK)

    ema9_series  = _compute_ema(closes, 9)
    ema21_series = _compute_ema(closes, 21)
    ema34_series = _compute_ema(closes, 34)

    if not ema21_series or not ema34_series:
        return ([], _EMA_NO_BLOCK)

    ema9  = ema9_series[-1]  if ema9_series  else None
    ema21 = ema21_series[-1]
    ema34 = ema34_series[-1]

    # ── Limiar de toque com cap absoluto ──────────────────────────────────────
    cap          = EMA_TOUCH_CAPS.get(symbol, 2.5)
    touch_thresh = min(EMA_TOUCH_ATR_MULT * atr, cap)

    # ── Verificar proximidade: EMA_21 estrutural + EMA_34 confirmador ─────────
    ema21_touched = abs(price - ema21) <= touch_thresh
    ema34_touched = abs(price - ema34) <= touch_thresh
    ema9_in_zone  = (
        ema9 is not None
        and abs(price - ema9) <= touch_thresh
        and ema21_touched  # trigger só conta se EMA_21 também está na zona
    )

    if not ema21_touched:
        return ([], _EMA_NO_BLOCK)  # preço fora de zona — estado normal, não bloqueio

    # ── Classificar convergência → base_boost ─────────────────────────────────
    if ema21_touched and ema34_touched and ema9_in_zone:
        # Triplo cluster: EMA21 + EMA34 + EMA9 trigger
        base_boost    = 1.0
        priority      = 2       # zona de alta prioridade
        label_cluster = "EMA9+21+34 cluster"
    elif ema21_touched and ema34_touched:
        # Duplo cluster: EMA21 + EMA34 (sem trigger EMA9)
        base_boost    = 0.5
        priority      = 3
        label_cluster = "EMA21+34 cluster"
    elif ema21_touched and ema9_in_zone:
        # EMA21 + trigger EMA9 (EMA34 mais afastada)
        base_boost    = 0.0
        priority      = 3
        label_cluster = "EMA21 + trigger EMA9"
    else:
        # EMA21 sozinha — depende inteiramente de OFI/CVD
        base_boost    = 0.0
        priority      = 4
        label_cluster = "EMA21"

    # ── Bónus VWAP proximity ──────────────────────────────────────────────────
    vwap_bonus = 0.0
    vwap_label = ""
    vwap_levels = []
    if vwap > 0:
        vwap_levels.append((vwap, "VWAP"))
    if vwap > 0 and vwap_std > 0:
        vwap_levels += [
            (vwap + vwap_std,     "+1σ"),
            (vwap - vwap_std,     "-1σ"),
            (vwap + 2 * vwap_std, "+2σ"),
            (vwap - 2 * vwap_std, "-2σ"),
        ]
    for lvl, lname in vwap_levels:
        if lvl > 0 and abs(ema21 - lvl) <= EMA_VWAP_PROXIMITY * atr:
            vwap_bonus = 0.5
            vwap_label = f" @ {lname}"
            break

    total_boost = base_boost + vwap_bonus

    # ── Gate 5: cooldown por coordenada de preço ─────────────────────────────
    # block_reason="EMA_PRICE_COOLDOWN" é propagado para result["ema_block_reasons"]
    # no snapshot de MongoDB — distingue este bloqueio do zone-type cooldown (Gate 6).
    if _is_ema_in_cooldown(symbol, direction, ema21, atr):
        logger.debug(
            "[EMA] PRICE_COOLDOWN: %s %s EMA21=%.2f (dentro de %.1f×ATR do último trade EMA)",
            symbol, direction, ema21, EMA_PRICE_COOLDOWN_ATR,
        )
        return ([], "EMA_PRICE_COOLDOWN")

    # ── Construir ZoneOfInterest ──────────────────────────────────────────────
    zone_type = ZoneType.EMA_PULLBACK_BUY if direction == "LONG" else ZoneType.EMA_PULLBACK_SELL
    label     = f"EMA Pullback {direction} — {label_cluster}{vwap_label} (EMA21={ema21:.2f})"

    # TP explícito: EMA_21 ± EMA_TP_ATR_MULT × ATR_m1
    # Resultado: R:R = EMA_TP_ATR_MULT / EMA_SL_ATR_MULT = 1.5 / 0.6 = 2.5:1 declarado.
    # Não usa fallback ATR×1.5 do compute_zone_s3 — o target é intencionalmente fixado aqui.
    tp_distance = EMA_TP_ATR_MULT * atr
    if direction == "LONG":
        ema_target      = round(ema21 + tp_distance, 2)
        ema_target_lbl  = f"EMA21+{EMA_TP_ATR_MULT}×ATR ({ema_target:.2f})"
    else:
        ema_target      = round(ema21 - tp_distance, 2)
        ema_target_lbl  = f"EMA21-{EMA_TP_ATR_MULT}×ATR ({ema_target:.2f})"

    zone = ZoneOfInterest(
        zone_type    = zone_type,
        level        = ema21,
        direction    = direction,
        tolerance    = touch_thresh,
        target       = ema_target,
        target_label = ema_target_lbl,
        sl_atr_mult  = EMA_SL_ATR_MULT,
        be_atr_mult  = EMA_BE_ATR_MULT,
        priority     = priority,
        label        = label,
    )

    logger.debug(
        "[EMA] Zona construída: %s %s EMA21=%.2f EMA34=%.2f EMA9=%s "
        "touch=%.2f boost=%.1f prio=%d target=%.2f",
        symbol, direction, ema21, ema34,
        f"{ema9:.2f}" if ema9 else "N/A",
        touch_thresh, total_boost, priority, ema_target,
    )

    return ([(zone, total_boost)], _EMA_NO_BLOCK)


# ═══════════════════════════════════════════════════════════════════
# Regimes de Mercado
# ═══════════════════════════════════════════════════════════════════

class ScalpDayRegime(str, Enum):
    EXPANSION_BULL = "EXPANSION_BULL"
    EXPANSION_BEAR = "EXPANSION_BEAR"
    ROTATION       = "ROTATION"
    BREAKOUT_BULL  = "BREAKOUT_BULL"
    BREAKOUT_BEAR  = "BREAKOUT_BEAR"
    RTH_OPEN       = "RTH_OPEN"    # primeiros FIX_A_RTH_OPEN_DURATION_MIN de sessão RTH (nativo)
    UNDEFINED      = "UNDEFINED"


# Set de regimes que requerem histerese longa (BREAKOUT_HYSTERESIS_SECS = 60s)
_BREAKOUT_REGIMES = {ScalpDayRegime.BREAKOUT_BULL, ScalpDayRegime.BREAKOUT_BEAR}


# ═══════════════════════════════════════════════════════════════════
# Tipos de Zona
# ═══════════════════════════════════════════════════════════════════

class ZoneType(str, Enum):
    VWAP_PULLBACK_BUY    = "VWAP_PULLBACK_BUY"
    VWAP_PULLBACK_SELL   = "VWAP_PULLBACK_SELL"
    SIGMA1_PULLBACK_BUY  = "SIGMA1_PULLBACK_BUY"
    SIGMA1_PULLBACK_SELL = "SIGMA1_PULLBACK_SELL"

    SIGMA2_FADE_SELL     = "SIGMA2_FADE_SELL"
    SIGMA2_FADE_BUY      = "SIGMA2_FADE_BUY"

    D1_VAH_FADE_SELL     = "D1_VAH_FADE_SELL"
    D1_VAL_FADE_BUY      = "D1_VAL_FADE_BUY"
    D1_POC_MAGNET_BUY    = "D1_POC_MAGNET_BUY"
    D1_POC_MAGNET_SELL   = "D1_POC_MAGNET_SELL"

    SESSION_POC_BUY      = "SESSION_POC_BUY"
    SESSION_POC_SELL     = "SESSION_POC_SELL"
    SESSION_VAH_FADE     = "SESSION_VAH_FADE"
    SESSION_VAL_FADE     = "SESSION_VAL_FADE"

    ONH_FADE_SELL        = "ONH_FADE_SELL"
    ONL_FADE_BUY         = "ONL_FADE_BUY"
    ONH_BREAK_BUY        = "ONH_BREAK_BUY"
    ONL_BREAK_SELL       = "ONL_BREAK_SELL"

    BREAKOUT_RETEST_BUY  = "BREAKOUT_RETEST_BUY"
    BREAKOUT_RETEST_SELL = "BREAKOUT_RETEST_SELL"

    IBH_FADE_SELL        = "IBH_FADE_SELL"
    IBL_FADE_BUY         = "IBL_FADE_BUY"
    IBH_PULLBACK_BUY     = "IBH_PULLBACK_BUY"
    IBL_PULLBACK_SELL    = "IBL_PULLBACK_SELL"

    GAMMA_CALL_WALL_SELL  = "GAMMA_CALL_WALL_SELL"
    GAMMA_PUT_WALL_BUY    = "GAMMA_PUT_WALL_BUY"

    # ── F7: EMA Pullback Zones (dynamic levels) ───────────────────────────────
    EMA_PULLBACK_BUY  = "EMA_PULLBACK_BUY"
    EMA_PULLBACK_SELL = "EMA_PULLBACK_SELL"


# Tipos que são zonas Session VP (hibernadas nos primeiros 90 min RTH)
_SESSION_VP_TYPES = {
    ZoneType.SESSION_VAH_FADE,
    ZoneType.SESSION_VAL_FADE,
    # SESSION_POC_BUY / SESSION_POC_SELL eliminados: POC em movimento contínuo não é
    # nível estático — tratá-lo como zona com cooldown fixo gera instabilidade de re-instanciação.
    # SESSION_VAH e SESSION_VAL (extremos acumulados) são mais estáveis e mantidos.
}

# Tipos D-1 VP (usados na deduplicação)
_D1_ZONE_TYPES = {
    ZoneType.D1_VAH_FADE_SELL,
    ZoneType.D1_VAL_FADE_BUY,
    ZoneType.D1_POC_MAGNET_BUY,
    ZoneType.D1_POC_MAGNET_SELL,
}

# Tipos de Break/Pullback que exigem OFI fast > 0.30 como AND gate
_BREAK_ZONE_TYPES = {
    ZoneType.ONH_BREAK_BUY,    ZoneType.ONL_BREAK_SELL,
    ZoneType.IBH_PULLBACK_BUY, ZoneType.IBL_PULLBACK_SELL,
    ZoneType.EMA_PULLBACK_BUY, ZoneType.EMA_PULLBACK_SELL,  # F7: EMA pullback exige OFI fast confirmado
}

# Tipos IB (Initial Balance — só ativos depois das 10:30 ET)
_IB_ZONE_TYPES = {
    ZoneType.IBH_FADE_SELL,
    ZoneType.IBL_FADE_BUY,
    ZoneType.IBH_PULLBACK_BUY,
    ZoneType.IBL_PULLBACK_SELL,
}

# Tipos de Fade em ROTATION (para referência futura na late-session penalty F3)
_FADE_ZONE_TYPES = {
    ZoneType.SIGMA2_FADE_SELL, ZoneType.SIGMA2_FADE_BUY,
    ZoneType.D1_VAH_FADE_SELL, ZoneType.D1_VAL_FADE_BUY,
    ZoneType.SESSION_VAH_FADE,  ZoneType.SESSION_VAL_FADE,
    ZoneType.ONH_FADE_SELL,     ZoneType.ONL_FADE_BUY,
    ZoneType.IBH_FADE_SELL,     ZoneType.IBL_FADE_BUY,
    ZoneType.GAMMA_CALL_WALL_SELL, ZoneType.GAMMA_PUT_WALL_BUY,
}

# Tipos Gamma (F5-2 — Call Wall / Put Wall dos dealers)
_GAMMA_ZONE_TYPES = {
    ZoneType.GAMMA_CALL_WALL_SELL,
    ZoneType.GAMMA_PUT_WALL_BUY,
}


# ═══════════════════════════════════════════════════════════════════
# Parâmetros de risco por regime
# ═══════════════════════════════════════════════════════════════════

REGIME_RISK_PARAMS: Dict[str, Dict] = {
    ScalpDayRegime.ROTATION: {
        'sl_atr': 0.8, 'be_atr': 0.6, 'conviction': 'MODERATE',
        'description': 'Value area rotation — stops mais curtos, fade nos extremos',
    },
    ScalpDayRegime.EXPANSION_BULL: {
        'sl_atr': 1.0, 'be_atr': 0.8, 'conviction': 'HIGH',
        'description': 'Tendência bullish — pullbacks para compra com stop maior',
    },
    ScalpDayRegime.EXPANSION_BEAR: {
        'sl_atr': 1.0, 'be_atr': 0.8, 'conviction': 'HIGH',
        'description': 'Tendência bearish — rallies para venda com stop maior',
    },
    ScalpDayRegime.BREAKOUT_BULL: {
        'sl_atr': 1.2, 'be_atr': 1.0, 'conviction': 'HIGH',
        'description': 'Rompimento bullish — stop mais largo para acomodar retest',
    },
    ScalpDayRegime.BREAKOUT_BEAR: {
        'sl_atr': 1.2, 'be_atr': 1.0, 'conviction': 'HIGH',
        'description': 'Rompimento bearish — stop mais largo para acomodar retest',
    },
    ScalpDayRegime.UNDEFINED: {
        'sl_atr': 1.0, 'be_atr': 0.8, 'conviction': 'LOW',
        'description': 'Regime indefinido — parâmetros conservadores',
    },
}

# Tolerâncias base (em múltiplos de ATR) — limitadas pelos TOLERANCE_CAPS
ZONE_TOLERANCE_ATR = {
    'vwap':     0.30,
    'sigma':    0.25,
    'vp':       0.25,
    'on':       0.20,
    'breakout': 0.30,
}

# ── Fase 1-1/1-3: Constantes de fluxo ────────────────────────────────────────
OFI_FAST_MIN         = 0.12   # OFI fast mínimo para confirmar direção
OFI_SLOW_BLOCK_FADE  = 0.55   # threshold OFI slow para fade zones (mais tolerante — slow naturalmente contra)
OFI_SLOW_BLOCK_MOMENTUM = 0.35  # threshold OFI slow para momentum/pullback zones (mais exigente)
OFI_SLOW_PENALTY_PTS = 1.5    # pontos deduzidos quando OFI slow está contra direção

# ── Parâmetros por grupo de sessão (NY / GLOBEX) ──────────────────────────────
# Populado no startup e actualizado pelo AutoTune apply.
# Formato: {"NY": {"zones_ofi_slow_fade_thresh": 0.55, ...}, "GLOBEX": {...}}
_SESSION_PARAMS: Dict[str, Dict] = {}


def update_session_params(session_group: str, params: Dict) -> None:
    """Actualiza parâmetros de sessão em runtime (startup + AutoTune apply)."""
    _SESSION_PARAMS[session_group] = {**_SESSION_PARAMS.get(session_group, {}), **params}


def get_session_params(session_group: str) -> Dict:
    """Retorna parâmetros específicos da sessão, dict vazio se não definido."""
    return _SESSION_PARAMS.get(session_group, {})


OFI_BREAK_MIN        = 0.30   # mínimo ofi_fast direcional para zonas de break ONH/ONL (F1-3)
DELTA_RATIO_MIN      = 0.08

# ── Fix A: bloquear SIGMA2_FADE_BUY durante toda a sessão RTH_OPEN ────────────
# RTH_OPEN = 09:30–10:29 ET = primeiros 60 minutos de sessão.
# Evidência directa: 3/3 STOP_HIT em RTH_OPEN (17, 24, 49 min após open) — 0% WR, -$1025.
# Critério de revisão: shadow log acumulado com ≥3 sessões RTH_OPEN
# → comparar PnL real vs PnL hipotético (trades que Fix A teria permitido).
# Migração futura: critério ATR_M1 < 1.5×baseline_OVN (adaptativo).
FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_BUY:  bool  = True   # toggle de emergência; False desactiva só o lado BUY
FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_SELL: bool  = True   # toggle independente para o lado SELL
# Argumento: mesmo que evidência directa seja de FADE_BUY (3/3 STOP_HIT), o price discovery
# e OFI incoerente em RTH_OPEN afectam igualmente a fade de alta (+2σ). Shadow log acumula
# dados de SELL para validação retrospectiva antes de qualquer decisão de reversão.
FIX_A_RTH_OPEN_DURATION_MIN:           float = 60.0   # RTH_OPEN = primeiros 60 min

# ── Fix B: F1-2 estendido — OFI slow bearish em SIGMA2_FADE_BUY → BLOCKED ────
# Hypothesis (evidence="conceptual — OFI slow bruto não disponível para Apr 16").
# validate_after="Fix C activo + N≥30 trades com OFI slow bruto registado".
# Threshold -0.30: se OFI slow estava entre -0.30 e -0.54 hoje (desconhecido),
# Fix B teria bloqueado os 3 trades; se estava ≥ -0.30, impacto zero.
# Rever threshold com dados reais de Fix C antes de ajustar globalmente.
FIX_B_HYPOTHESIS:               bool  = True    # marcar como hipótese; hypothesis→False quando N≥30 validado
FIX_B_OFI_SLOW_FADE_BUY_THRESH: float = 0.30   # threshold absoluto (sinal bearish: ofi_slow < -0.30)

# ── Fix D: Tape Speed bónus condicional — OFI slow não deve opor-se à direcção ─
# Problema actual: +0.3 pts aplicado incondicionalmente quando speed_ratio ≤ 0.9
# em qualquer zona de fade. Tape lento com OFI slow contra direcção pode ser pausa
# antes de continuation — não reversão. O bónus nesse contexto é indevido.
# Correcção: bónus apenas quando OFI slow está alinhado (≥0 para LONG, ≤0 para SHORT).
# Não requer dados adicionais — é correcção de comportamento existente, não novo critério.
# toggle de emergência: False restaura comportamento original (incondicional).
FIX_D_TAPE_SPEED_CONDITIONAL: bool = True

# ── Fix E: CVD opõe-se à fade → penalidade −0.5 pts ──────────────────────────
# CVD FALLING numa fade LONG = pressão vendedora de médio prazo numa entrada compradora.
# CVD RISING numa fade SHORT = pressão compradora de médio prazo numa entrada vendedora.
# Ambos indicam contexto adverso ao trade — o CVD não está a confirmar a reversão.
# Complementar ao bónus existente (+0.5 quando alinhado): assimetria total = 1.0 pt.
# Toggle de emergência: False suspende penalidade sem afectar bónus de alinhamento.
FIX_E_CVD_FADE_PENALTY_ENABLED: bool  = True
FIX_E_CVD_FADE_PENALTY_PTS:     float = 0.5

# ── Fix 3: gap_open, snapshots_in_regime, range_consumed ─────────────────────
# Filtros de contexto estrutural — hypothesis:True (toggles de emergência disponíveis).
# gap_open:           abs(rth_open − d1_poc) > 0.5×ATR em RTH_OPEN → fades arriscadas.
#   NOTA: a referência é d1_poc (centro de valor do dia anterior), não prev_close.
#   Isto detecta "abertura fora da value area anterior" (gap de market profile),
#   que é mais relevante que gap de preço bruto (open−close). Escolha intencional.
# snapshots_in_regime: < 3 avaliações no regime actual → regime não confirmado, suprime fades.
#   NOTA: EMA_PULLBACK (zona de continuação) é excluída desta supressão — apenas fades.
# range_consumed:     (HOD−LOD)/(D1_HIGH−D1_LOW) > 1.5 → range de hoje excede 1.5× range típico diário.
#   Redesign v2: normalizado por D1_HIGH−D1_LOW (range RTH do dia anterior) em vez de ATR_M1.
#   ATR_M1 e range diário são escalas incompatíveis → threshold 1.8 bloqueava 100% dos trades.
#   Com D1 como denominador: 0.72× hoje (normal), >1.5× apenas em dias verdadeiramente excepcionais.
FIX_3_GAP_OPEN_ENABLED:       bool  = True
FIX_3_GAP_OPEN_ATR_MULT:      float = 0.5   # abs(rth_open − d1_poc) > N×ATR → abertura fora da value area
FIX_3_SNAPSHOTS_ENABLED:      bool  = True
FIX_3_SNAPSHOTS_MIN:          int   = 3     # mínimo de snapshots consecutivos para confirmar regime
FIX_3_RANGE_CONSUMED_ENABLED: bool  = True
FIX_3_RANGE_CONSUMED_THRESH:  float = 1.5   # (HOD−LOD)/(D1_HIGH−D1_LOW) — range hoje vs range D1

# ── Fix 5: Price vs RTH Open — bias intraday (bónus + penalidade simétricos) ──
# Preço acima do open RTH → bias BULLISH.  Preço abaixo → bias BEARISH.
# Fade ALINHADA: +FIX_5_RTH_BIAS_SCORE  (mean-reversion em favor do bias)
# Fade OPOSTA:   +FIX_5_RTH_BIAS_PENALTY (mean-reversion contra o bias)
# Swing total: ±0.6 pts entre fade alinhada e oposta. hypothesis:True — calibrar N≥30.
FIX_5_RTH_BIAS_ENABLED:         bool  = True
FIX_5_RTH_BIAS_SCORE:           float = 0.3    # bónus quando fade alinhada com bias intraday
FIX_5_RTH_BIAS_ATR_THRESH:      float = 0.50   # price − rth_open > N×ATR = sinal significativo
# NOTA: 0.50×ATR cria zona NEUTRAL com ~6-7 pts em MNQ (ATR_M1≈13) e ~1.2 pts em MES.
# 0.10×ATR era excessivamente sensível — NEUTRAL tornava-se praticamente impossível.
FIX_5_RTH_BIAS_PENALTY_ENABLED: bool  = True
FIX_5_RTH_BIAS_PENALTY:         float = -0.3   # penalidade quando fade OPÕE o bias intraday
# Mecanismo simétrico ao bónus: se "preço ABOVE + fade BUY" vale +0.3,
# "preço ABOVE + fade SELL" (contra mean-reversion) vale −0.3.
# Hipótese: fades contra o bias têm WR inferior — calibrar com N≥30 por categoria.
# Toggle independente do bónus: FIX_5_RTH_BIAS_ENABLED pode ser True com penalty False.

# ── Fix 6: CVD como confirmação de regime S1 ──────────────────────────────────
# EXPANSION_BULL confirmado se CVD RISING; EXPANSION_BEAR se CVD FALLING.
# Regime CONTESTED = desalinhamento entre preço e fluxo acumulado — campo informativo apenas.
# Não gate hard por agora: aguarda N≥30 por regime para calibrar impacto.
FIX_6_CVD_REGIME_CONF_ENABLED: bool = True

# ── Penalidade de sessão tardia ───────────────────────────────────────────────
LATE_SESSION_MIN     = 360    # minutos de sessão RTH (≥6h = após 15:30 ET)
LATE_SESSION_PENALTY = -0.5   # penalidade suave em sessão tardia (liquidez reduzida)

# ── F6: Flow Base Gate ────────────────────────────────────────────────────────
# Score base de fluxo mínimo (OFI fast + delta + absorção + CVD) para que modificadores
# estruturais de bónus (confluence_boost e gamma_modifier positivo) possam ser aplicados.
# Penalidades negativas (gamma_modifier < 0) continuam a aplicar-se abaixo do gate —
# punir um trade de fluxo fraco que também tem gamma contra é protecção, não inflação.
BASE_FLOW_GATE: float = 1.2

# ── F3-1: Confluence Score Boost ─────────────────────────────────────────────
CONFLUENCE_BOOST_PTS:  float = 1.5  # bonus quando ≥2 zonas alinhadas sobrepostas em in_zone
CONFLUENCE_WINDOW_ATR: float = 0.5  # janela de sobreposição (em múltiplos de ATR)

# ── Tape Speed — EMA ratio (fast/slow) assimétrico por categoria de zona ──────
# Momentum/Breakout/Pullback: tape acelerado confirma; tape lento sugere probe mecânico
# Fade: tape a secar é o sinal de reversão genuína; tape acelerado = risco continuation
TAPE_SPEED_MOMENTUM_MIN   = 1.4   # ratio mínimo em zonas momentum/breakout para não penalizar
TAPE_SPEED_FADE_BONUS_MAX = 0.9   # ratio máximo em zonas fade para bónus (tape a secar)
TAPE_SPEED_FADE_PENALTY   = 1.6   # ratio a partir do qual fade é penalizado (tape acelerado)
TAPE_SPEED_BONUS_PTS      = 0.3   # bónus por tape lento em fade (reversão confirmada)
TAPE_SPEED_PENALTY_PTS    = 0.5   # penalidade por tape rápido em fade ou lento em momentum


# ═══════════════════════════════════════════════════════════════════
# Fase 2-3: ScoreBreakdown — payload estruturado de score
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ScoreBreakdown:
    base_score:           float
    ofi_slow_penalty:     float = 0.0
    tape_speed_modifier:  float = 0.0  # +0.3 fade lento / -0.5 fade rápido ou momentum lento
    late_session_penalty: float = 0.0
    confluence_boost:     float = 0.0  # F3-1: +1.5 pts quando ≥2 zonas alinhadas sobrepostas
    gamma_modifier:       float = 0.0  # 1-D/1-E: gamma_sentiment + ZGL gamma_regime
    # Item 5: Fix 5 bias modifier — separado de base_score para que AutoTune possa
    # re-simular com diferentes valores de FIX_5_RTH_BIAS_SCORE / PENALTY.
    # +0.3 quando fade alinhada com bias; -0.3 quando oposta; 0.0 quando NEUTRAL.
    rth_bias_modifier:    float = 0.0
    base_flow_gated:      bool  = False  # F6: bónus bloqueados por fluxo base insuficiente
    delta_quality_capped: bool  = False  # delta_ratio divergente → qualidade cap MODERATE

    @property
    def total_score(self) -> float:
        return (self.base_score
                + self.ofi_slow_penalty
                + self.tape_speed_modifier
                + self.late_session_penalty
                + self.confluence_boost
                + self.gamma_modifier
                + self.rth_bias_modifier)

    def to_dict(self) -> Dict[str, float]:
        return {
            "base_score":           round(self.base_score, 3),
            "ofi_slow_penalty":     round(self.ofi_slow_penalty, 3),
            "tape_speed_modifier":  round(self.tape_speed_modifier, 3),
            "late_session_penalty": round(self.late_session_penalty, 3),
            "confluence_boost":     round(self.confluence_boost, 3),
            "gamma_modifier":       round(self.gamma_modifier, 3),
            "rth_bias_modifier":    round(self.rth_bias_modifier, 3),
            "total_score":          round(self.total_score, 3),
            "base_flow_gated":      self.base_flow_gated,
            "delta_quality_capped": self.delta_quality_capped,
        }


# ═══════════════════════════════════════════════════════════════════
# Dataclass: Zona de Interesse
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ZoneOfInterest:
    zone_type:    ZoneType
    level:        float
    direction:    str
    tolerance:    float
    target:       Optional[float]
    target_label: str
    sl_atr_mult:  float
    be_atr_mult:  float
    priority:     int = 5
    label:        str = ""

    def in_zone(self, price: float) -> bool:
        return abs(price - self.level) <= self.tolerance

    def distance_to(self, price: float) -> float:
        return abs(price - self.level)


# ═══════════════════════════════════════════════════════════════════
# 1. Detecção de Regime
# ═══════════════════════════════════════════════════════════════════

def detect_day_regime(
    price: float,
    vwap: float,
    vwap_std: float,
    d1_poc: float,
    d1_vah: float,
    d1_val: float,
    atr: float,
    symbol: str,
) -> ScalpDayRegime:
    """
    Classifica o regime do dia baseado na posição do preço vs VWAP e VP D-1.

    EXPANSION_BULL:  preço acima D1_VAH E acima VWAP
    EXPANSION_BEAR:  preço abaixo D1_VAL E abaixo VWAP
    BREAKOUT_BULL:   preço acima D1_VAH mas próximo (dentro de 1×ATR) — teste de aceitação
    BREAKOUT_BEAR:   preço abaixo D1_VAL mas próximo (dentro de 1×ATR)
    ROTATION:        preço dentro da value area D-1

    Se não há dados de VP D-1, usa posição vs VWAP ±1σ como proxy.
    """
    if price <= 0 or vwap <= 0:
        return ScalpDayRegime.UNDEFINED

    has_d1 = d1_vah > 0 and d1_val > 0

    if not has_d1:
        upper_ref = vwap + (vwap_std or atr * 0.8)
        lower_ref = vwap - (vwap_std or atr * 0.8)
        if price > upper_ref:
            return ScalpDayRegime.EXPANSION_BULL
        elif price < lower_ref:
            return ScalpDayRegime.EXPANSION_BEAR
        else:
            return ScalpDayRegime.ROTATION

    breakout_buffer = atr * 1.0

    above_vah  = price > d1_vah
    below_val  = price < d1_val
    above_vwap = price > vwap

    if above_vah:
        dist_from_vah = price - d1_vah
        if dist_from_vah <= breakout_buffer and above_vwap:
            return ScalpDayRegime.BREAKOUT_BULL
        elif above_vwap:
            return ScalpDayRegime.EXPANSION_BULL
        else:
            return ScalpDayRegime.ROTATION
    elif below_val:
        dist_from_val = d1_val - price
        if dist_from_val <= breakout_buffer and not above_vwap:
            return ScalpDayRegime.BREAKOUT_BEAR
        elif not above_vwap:
            return ScalpDayRegime.EXPANSION_BEAR
        else:
            return ScalpDayRegime.ROTATION
    else:
        return ScalpDayRegime.ROTATION


# ═══════════════════════════════════════════════════════════════════
# 1b. Histerese de Regime (HF3)
# ═══════════════════════════════════════════════════════════════════

def apply_regime_hysteresis(symbol: str, raw_regime: ScalpDayRegime) -> ScalpDayRegime:
    """
    HF3: Filtra mudanças de regime instáveis via debounce de 5 segundos.

    - Se o regime bruto é igual ao ativo → mantém ativo, cancela qualquer pendente.
    - Se é diferente do ativo:
        * Se o pendente ainda não existe (ou mudou) → inicia timer.
        * Se o pendente se mantém por >= REGIME_HYSTERESIS_SECS → confirma transição.
        * Se o timer ainda não expirou → mantém regime ativo (ignora candidato).
    - UNDEFINED nunca dispara histerese — é aceito imediatamente.
    """
    now = time.monotonic()
    state = _regime_hysteresis.get(symbol)

    if state is None:
        _regime_hysteresis[symbol] = {
            'active':        raw_regime,
            'pending':       None,
            'pending_since': 0.0,
        }
        return raw_regime

    active = state['active']

    if raw_regime == active:
        if state['pending'] is not None:
            state['pending']       = None
            state['pending_since'] = 0.0
        return active

    if raw_regime == ScalpDayRegime.UNDEFINED:
        state['active']        = raw_regime
        state['pending']       = None
        state['pending_since'] = 0.0
        return raw_regime

    if state['pending'] != raw_regime:
        state['pending']       = raw_regime
        state['pending_since'] = now
        logger.debug(
            f"[REGIME] {symbol}: pendente {raw_regime.value} (ativo: {active.value}) "
            f"— aguardando {REGIME_HYSTERESIS_SECS:.0f}s de confirmação"
        )
        return active

    elapsed = now - state['pending_since']
    required = BREAKOUT_HYSTERESIS_SECS if raw_regime in _BREAKOUT_REGIMES else REGIME_HYSTERESIS_SECS
    if elapsed >= required:
        logger.info(
            f"[REGIME] {symbol}: transição confirmada {active.value} → {raw_regime.value} "
            f"({elapsed:.1f}s contínuos, requerido={required:.0f}s)"
        )
        state['active']        = raw_regime
        state['pending']       = None
        state['pending_since'] = 0.0
        return raw_regime

    return active


# ═══════════════════════════════════════════════════════════════════
# 2. Identificação de Zonas
# ═══════════════════════════════════════════════════════════════════

def _make_zone(
    zone_type: ZoneType,
    level: float,
    direction: str,
    tol: float,
    target: Optional[float],
    target_label: str,
    sl_mult: float,
    be_mult: float,
    priority: int,
    label: str,
) -> Optional[ZoneOfInterest]:
    if level <= 0:
        return None
    return ZoneOfInterest(
        zone_type=zone_type, level=level, direction=direction,
        tolerance=tol, target=target, target_label=target_label,
        sl_atr_mult=sl_mult, be_atr_mult=be_mult, priority=priority, label=label,
    )


def _apply_cap(value: float, cap: float) -> float:
    """Limita tolerância ao teto definido (hard cap)."""
    return min(value, cap)


def identify_zones(
    regime: ScalpDayRegime,
    price: float,
    levels: Dict[str, Any],
    atr: float,
    symbol: str,
    on_session_minutes: float = 999,
    macro_context: Optional[Dict[str, Any]] = None,
) -> List[ZoneOfInterest]:
    """
    Retorna lista de zonas de interesse ativas para o regime e preço atuais.

    Modificações F1/F2:
    - F1-2: SIGMA2_FADE ±3σ não geradas em EXPANSION (apenas ROTATION)
    - F1-4: Session VP (VAH/VAL/POC sessão) hibernadas nos primeiros 90 min RTH
    - F2-1: Tolerâncias limitadas por TOLERANCE_CAPS (MNQ/MES)
    - Deduplicação: Session zones suprimidas quando D1 zone está na mesma direção e ≤0.5×ATR

    Fase 5:
    - F5-1: Term Structure Hard Gate — ratio ≥1.10 retorna lista vazia (HARD_STOP);
            ratio ≥1.05 filtra _FADE_ZONE_TYPES após criação.
    - F5-2: Gamma Levels — Call Wall / Put Wall adicionados como zonas de prioridade 3
            quando macro_context['gamma_reliable']=True.
    """
    ctx = macro_context or {}

    # ── F5-1: Term Structure Hard Gate ───────────────────────────────────────
    ts_ratio          = float(ctx.get('ts_ratio', 0.0) or 0.0)
    ts_hard_stop      = ts_ratio >= 1.10
    ts_fade_suppressed = ts_ratio >= 1.05

    if ts_hard_stop:
        logger.warning(
            f"[ZONES] F5-1 HARD_STOP {symbol}: VIX/VIX3M={ts_ratio:.3f} ≥1.10 "
            "— pânico extremo, todas as zonas suspensas"
        )
        return []
    zones: List[ZoneOfInterest] = []

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
    onh      = levels.get("onh", 0.0) or 0.0
    onl      = levels.get("onl", 0.0) or 0.0
    ibh      = levels.get("ibh", 0.0) or 0.0
    ibl      = levels.get("ibl", 0.0) or 0.0
    ib_locked = bool(levels.get("ib_locked", False))

    # ── Fase 2-1: Tolerâncias com hard caps ──────────────────────────────────
    caps = TOLERANCE_CAPS.get(symbol, TOLERANCE_CAPS_DEFAULT)

    tol_vwap = _apply_cap(max(atr * ZONE_TOLERANCE_ATR['vwap'], TICK_SIZE * 4), caps['vwap'])
    tol_sig  = _apply_cap(max(atr * ZONE_TOLERANCE_ATR['sigma'], TICK_SIZE * 3), caps['vwap'])
    tol_vp   = _apply_cap(max(atr * ZONE_TOLERANCE_ATR['vp'], TICK_SIZE * 4), caps['vp'])
    tol_on   = _apply_cap(max(atr * ZONE_TOLERANCE_ATR['on'], TICK_SIZE * 3), caps['on'])
    tol_ib   = _apply_cap(max(atr * ZONE_TOLERANCE_ATR['on'], TICK_SIZE * 3), caps['ib'])

    risk_p = REGIME_RISK_PARAMS.get(regime, REGIME_RISK_PARAMS[ScalpDayRegime.UNDEFINED])
    sl_m   = risk_p['sl_atr']
    be_m   = risk_p['be_atr']

    # ── HF2-A: Pré-filtro de proximidade ─────────────────────────────────────
    prox_max = PROXIMITY_WINDOW_ATR * atr

    def add(z):
        if z is not None and abs(z.level - price) <= prox_max:
            zones.append(z)

    # ── Fase 1-4: Session VP só ativa após 90 min RTH ─────────────────────────
    session_vp_active = on_session_minutes >= 90

    # ── Overnight Fade/Break (primeiras 90 min de RTH) ───────────────────────
    on_active = on_session_minutes <= 90

    if on_active and onh > 0:
        add(_make_zone(ZoneType.ONH_FADE_SELL, onh, "SHORT", tol_on,
            target=d1_poc if d1_poc > 0 and d1_poc < onh else vwap,
            target_label="D1 POC" if d1_poc > 0 and d1_poc < onh else "VWAP",
            sl_mult=0.7, be_mult=0.5, priority=1,
            label=f"ONH Fade Sell @ {onh:.2f}"))
        # F1-3: ONH Break exige ofi_fast > 0.30 — verificado em evaluate_zone_entry
        add(_make_zone(ZoneType.ONH_BREAK_BUY, onh, "LONG", tol_on,
            target=u1 if u1 > onh else u2,
            target_label="+1σ VWAP" if u1 > onh else "+2σ VWAP",
            sl_mult=0.8, be_mult=0.7, priority=2,
            label=f"ONH Break Buy @ {onh:.2f}"))

    if on_active and onl > 0:
        add(_make_zone(ZoneType.ONL_FADE_BUY, onl, "LONG", tol_on,
            target=d1_poc if d1_poc > 0 and d1_poc > onl else vwap,
            target_label="D1 POC" if d1_poc > 0 and d1_poc > onl else "VWAP",
            sl_mult=0.7, be_mult=0.5, priority=1,
            label=f"ONL Fade Buy @ {onl:.2f}"))
        # F1-3: ONL Break exige ofi_fast > 0.30 — verificado em evaluate_zone_entry
        add(_make_zone(ZoneType.ONL_BREAK_SELL, onl, "SHORT", tol_on,
            target=l1 if l1 < onl else l2,
            target_label="-1σ VWAP" if l1 < onl else "-2σ VWAP",
            sl_mult=0.8, be_mult=0.7, priority=2,
            label=f"ONL Break Sell @ {onl:.2f}"))

    # ── Initial Balance (só após cristalização às 10:30 ET — ib_locked=True) ─
    if ib_locked and ibh > 0 and ibl > 0:
        if regime == ScalpDayRegime.ROTATION:
            # Fade: IBH/IBL atuam como teto/chão do range → target VWAP ou POC
            add(_make_zone(ZoneType.IBH_FADE_SELL, ibh, "SHORT", tol_ib,
                target=poc if poc > 0 and poc < ibh else vwap,
                target_label="Session POC" if poc > 0 and poc < ibh else "VWAP",
                sl_mult=0.7, be_mult=0.5, priority=1,
                label=f"IB High Fade Sell @ {ibh:.2f}"))
            add(_make_zone(ZoneType.IBL_FADE_BUY, ibl, "LONG", tol_ib,
                target=poc if poc > 0 and poc > ibl else vwap,
                target_label="Session POC" if poc > 0 and poc > ibl else "VWAP",
                sl_mult=0.7, be_mult=0.5, priority=1,
                label=f"IB Low Fade Buy @ {ibl:.2f}"))

        elif regime in (ScalpDayRegime.EXPANSION_BULL, ScalpDayRegime.BREAKOUT_BULL):
            # IBH virou suporte — pullback a IBH depois do break é LONG
            # Pullback requer OFI fast > 0.30 (AND gate via _BREAK_ZONE_TYPES)
            if price > ibh:
                add(_make_zone(ZoneType.IBH_PULLBACK_BUY, ibh, "LONG", tol_ib,
                    target=u1 if u1 > ibh else u2,
                    target_label="+1σ VWAP" if u1 > ibh else "+2σ VWAP",
                    sl_mult=0.8, be_mult=0.7, priority=2,
                    label=f"IB High Pullback Buy @ {ibh:.2f}"))

        elif regime in (ScalpDayRegime.EXPANSION_BEAR, ScalpDayRegime.BREAKOUT_BEAR):
            # IBL virou resistência — pullback a IBL depois do break é SHORT
            # Pullback requer OFI fast > 0.30 (AND gate via _BREAK_ZONE_TYPES)
            if price < ibl:
                add(_make_zone(ZoneType.IBL_PULLBACK_SELL, ibl, "SHORT", tol_ib,
                    target=l1 if l1 < ibl else l2,
                    target_label="-1σ VWAP" if l1 < ibl else "-2σ VWAP",
                    sl_mult=0.8, be_mult=0.7, priority=2,
                    label=f"IB Low Pullback Sell @ {ibl:.2f}"))

    # ── Zonas por Regime ──────────────────────────────────────────────────────

    if regime == ScalpDayRegime.ROTATION:
        add(_make_zone(ZoneType.D1_VAH_FADE_SELL, d1_vah, "SHORT", tol_vp,
            target=d1_poc if d1_poc > 0 else vwap,
            target_label="D1 POC" if d1_poc > 0 else "VWAP",
            sl_mult=0.8, be_mult=0.5, priority=2,
            label=f"D1 VAH Fade Sell @ {d1_vah:.2f}"))
        add(_make_zone(ZoneType.D1_VAL_FADE_BUY, d1_val, "LONG", tol_vp,
            target=d1_poc if d1_poc > 0 else vwap,
            target_label="D1 POC" if d1_poc > 0 else "VWAP",
            sl_mult=0.8, be_mult=0.5, priority=2,
            label=f"D1 VAL Fade Buy @ {d1_val:.2f}"))
        if d1_poc > 0:
            if price < d1_poc:
                # Target conservador: D1 VAH só se ≤1.5×ATR de distância, senão Session VAH
                # ou +1σ VWAP — evita targets demasiado ambiciosos em value areas largas
                poc_to_vah = (d1_vah - d1_poc) if d1_vah > d1_poc else 0
                if d1_vah > d1_poc and poc_to_vah <= 1.5 * atr:
                    poc_buy_target, poc_buy_label = d1_vah, "D1 VAH"
                elif vah > 0 and vah > d1_poc:
                    poc_buy_target, poc_buy_label = vah, "Session VAH"
                else:
                    poc_buy_target, poc_buy_label = u1, "+1σ VWAP"
                add(_make_zone(ZoneType.D1_POC_MAGNET_BUY, d1_poc, "LONG", tol_vp,
                    target=poc_buy_target,
                    target_label=poc_buy_label,
                    sl_mult=0.7, be_mult=0.5, priority=3,
                    label=f"D1 POC Buy @ {d1_poc:.2f}"))
            else:
                # Target conservador: D1 VAL só se ≤1.5×ATR de distância, senão Session VAL
                poc_to_val = (d1_poc - d1_val) if d1_val > 0 and d1_val < d1_poc else 0
                if d1_val > 0 and d1_val < d1_poc and poc_to_val <= 1.5 * atr:
                    poc_sell_target, poc_sell_label = d1_val, "D1 VAL"
                elif val > 0 and val < d1_poc:
                    poc_sell_target, poc_sell_label = val, "Session VAL"
                else:
                    poc_sell_target, poc_sell_label = l1, "-1σ VWAP"
                add(_make_zone(ZoneType.D1_POC_MAGNET_SELL, d1_poc, "SHORT", tol_vp,
                    target=poc_sell_target,
                    target_label=poc_sell_label,
                    sl_mult=0.7, be_mult=0.5, priority=3,
                    label=f"D1 POC Sell @ {d1_poc:.2f}"))
        # +2σ/-2σ fade (ROTATION apenas — ±3σ mantidos em ROTATION)
        add(_make_zone(ZoneType.SIGMA2_FADE_SELL, u2, "SHORT", tol_sig,
            target=vwap, target_label="VWAP",
            sl_mult=0.7, be_mult=0.5, priority=3,
            label=f"+2σ Fade Sell @ {u2:.2f}"))
        add(_make_zone(ZoneType.SIGMA2_FADE_SELL, u3, "SHORT", tol_sig,
            target=u1, target_label="+1σ VWAP",
            sl_mult=0.7, be_mult=0.4, priority=2,
            label=f"+3σ Fade Sell @ {u3:.2f}"))
        add(_make_zone(ZoneType.SIGMA2_FADE_BUY, l2, "LONG", tol_sig,
            target=vwap, target_label="VWAP",
            sl_mult=0.7, be_mult=0.5, priority=3,
            label=f"-2σ Fade Buy @ {l2:.2f}"))
        add(_make_zone(ZoneType.SIGMA2_FADE_BUY, l3, "LONG", tol_sig,
            target=l1, target_label="-1σ VWAP",
            sl_mult=0.7, be_mult=0.4, priority=2,
            label=f"-3σ Fade Buy @ {l3:.2f}"))
        # Session VP (F1-4: apenas após 90 min RTH)
        if session_vp_active:
            add(_make_zone(ZoneType.SESSION_VAH_FADE, vah, "SHORT", tol_vp,
                target=poc if poc > 0 else vwap,
                target_label="Session POC" if poc > 0 else "VWAP",
                sl_mult=0.8, be_mult=0.5, priority=3,
                label=f"Session VAH Fade @ {vah:.2f}"))
            add(_make_zone(ZoneType.SESSION_VAL_FADE, val, "LONG", tol_vp,
                target=poc if poc > 0 else vwap,
                target_label="Session POC" if poc > 0 else "VWAP",
                sl_mult=0.8, be_mult=0.5, priority=3,
                label=f"Session VAL Fade @ {val:.2f}"))
            # SESSION_POC_BUY / SESSION_POC_SELL eliminados: POC move-se continuamente ao longo
            # da sessão — instanciá-lo como zona estática com cooldown fixo cria instabilidade
            # onde o nível de referência pode deslocar vários ATR durante o cooldown de 3 min.
            # SESSION_VAH e SESSION_VAL (extremos acumulados) são estáveis e cobrem as bordas.

    elif regime == ScalpDayRegime.EXPANSION_BULL:
        add(_make_zone(ZoneType.VWAP_PULLBACK_BUY, vwap, "LONG", tol_vwap,
            target=u2 if u2 > 0 else u1,
            target_label="+2σ VWAP" if u2 > 0 else "+1σ VWAP",
            sl_mult=sl_m, be_mult=be_m, priority=1,
            label=f"VWAP Pullback Buy @ {vwap:.2f}"))
        add(_make_zone(ZoneType.SIGMA1_PULLBACK_BUY, u1, "LONG", tol_sig,
            target=u2 if u2 > 0 else d1_vah,
            target_label="+2σ VWAP" if u2 > 0 else "D1 VAH",
            sl_mult=sl_m, be_mult=be_m, priority=2,
            label=f"+1σ Pullback Buy @ {u1:.2f}"))
        if d1_poc > 0 and price > d1_poc:
            # Target conservador: D1 VAH só se ≤1.5×ATR, senão Session VAH ou +1σ VWAP
            poc_to_vah_exp = (d1_vah - d1_poc) if d1_vah > d1_poc else 0
            if d1_vah > d1_poc and poc_to_vah_exp <= 1.5 * atr:
                exp_poc_target, exp_poc_label = d1_vah, "D1 VAH"
            elif vah > 0 and vah > d1_poc:
                exp_poc_target, exp_poc_label = vah, "Session VAH"
            else:
                exp_poc_target, exp_poc_label = u1, "+1σ VWAP"
            add(_make_zone(ZoneType.D1_POC_MAGNET_BUY, d1_poc, "LONG", tol_vp,
                target=exp_poc_target,
                target_label=exp_poc_label,
                sl_mult=sl_m * 1.1, be_mult=be_m, priority=3,
                label=f"D1 POC Support Buy @ {d1_poc:.2f}"))
        # F1-2: Sem SIGMA2_FADE_SELL (+3σ) em EXPANSION_BULL

    elif regime == ScalpDayRegime.EXPANSION_BEAR:
        add(_make_zone(ZoneType.VWAP_PULLBACK_SELL, vwap, "SHORT", tol_vwap,
            target=l2 if l2 > 0 else l1,
            target_label="-2σ VWAP" if l2 > 0 else "-1σ VWAP",
            sl_mult=sl_m, be_mult=be_m, priority=1,
            label=f"VWAP Rally Sell @ {vwap:.2f}"))
        add(_make_zone(ZoneType.SIGMA1_PULLBACK_SELL, l1, "SHORT", tol_sig,
            target=l2 if l2 > 0 else d1_val,
            target_label="-2σ VWAP" if l2 > 0 else "D1 VAL",
            sl_mult=sl_m, be_mult=be_m, priority=2,
            label=f"-1σ Rally Sell @ {l1:.2f}"))
        if d1_poc > 0 and price < d1_poc:
            # Target conservador: D1 VAL só se ≤1.5×ATR, senão Session VAL ou -1σ VWAP
            poc_to_val_exp = (d1_poc - d1_val) if d1_val > 0 and d1_val < d1_poc else 0
            if d1_val > 0 and d1_val < d1_poc and poc_to_val_exp <= 1.5 * atr:
                exp_poc_s_target, exp_poc_s_label = d1_val, "D1 VAL"
            elif val > 0 and val < d1_poc:
                exp_poc_s_target, exp_poc_s_label = val, "Session VAL"
            else:
                exp_poc_s_target, exp_poc_s_label = l1, "-1σ VWAP"
            add(_make_zone(ZoneType.D1_POC_MAGNET_SELL, d1_poc, "SHORT", tol_vp,
                target=exp_poc_s_target,
                target_label=exp_poc_s_label,
                sl_mult=sl_m * 1.1, be_mult=be_m, priority=3,
                label=f"D1 POC Resistance Sell @ {d1_poc:.2f}"))
        # F1-2: Sem SIGMA2_FADE_BUY (-3σ) em EXPANSION_BEAR

    elif regime == ScalpDayRegime.BREAKOUT_BULL:
        add(_make_zone(ZoneType.BREAKOUT_RETEST_BUY, d1_vah, "LONG", tol_vp,
            target=u2 if u2 > d1_vah else u1,
            target_label="+2σ VWAP" if u2 > d1_vah else "+1σ VWAP",
            sl_mult=sl_m, be_mult=be_m, priority=1,
            label=f"D1 VAH Retest Buy @ {d1_vah:.2f}"))
        if vwap < d1_vah:
            add(_make_zone(ZoneType.VWAP_PULLBACK_BUY, vwap, "LONG", tol_vwap,
                target=d1_vah,
                target_label="D1 VAH",
                sl_mult=sl_m, be_mult=be_m, priority=2,
                label=f"VWAP Pullback Buy @ {vwap:.2f}"))

    elif regime == ScalpDayRegime.BREAKOUT_BEAR:
        add(_make_zone(ZoneType.BREAKOUT_RETEST_SELL, d1_val, "SHORT", tol_vp,
            target=l2 if l2 < d1_val else l1,
            target_label="-2σ VWAP" if l2 < d1_val else "-1σ VWAP",
            sl_mult=sl_m, be_mult=be_m, priority=1,
            label=f"D1 VAL Retest Sell @ {d1_val:.2f}"))
        if vwap > d1_val:
            add(_make_zone(ZoneType.VWAP_PULLBACK_SELL, vwap, "SHORT", tol_vwap,
                target=d1_val,
                target_label="D1 VAL",
                sl_mult=sl_m, be_mult=be_m, priority=2,
                label=f"VWAP Rally Sell @ {vwap:.2f}"))

    # ── F5-1: Suprime fade zones se TS em backwardation (pânico ativo) ──────────
    if ts_fade_suppressed:
        before = len(zones)
        zones = [z for z in zones if z.zone_type not in _FADE_ZONE_TYPES]
        removed = before - len(zones)
        if removed:
            logger.info(
                f"[ZONES] F5-1 FADE_SUPPRESSED {symbol}: VIX/VIX3M={ts_ratio:.3f} ≥1.05 "
                f"— {removed} fade zone(s) removidas"
            )

    # ── F5-2: Gamma Levels como ZONES (Call Wall / Put Wall dos dealers) ─────
    gamma_reliable = bool(ctx.get('gamma_reliable', False))
    if gamma_reliable:
        call_wall   = float(ctx.get('gamma_call_wall', 0.0) or 0.0)
        put_wall    = float(ctx.get('gamma_put_wall',  0.0) or 0.0)
        gamma_regime_ctx = str(ctx.get('gamma_regime', 'UNKNOWN'))
        tol_gamma   = _apply_cap(
            max(atr * ZONE_TOLERANCE_ATR['on'], TICK_SIZE * 4),
            caps.get('gamma', 4.0),
        )

        # F5-2-B: SHORT_GAMMA — os dealers estão SHORT de gamma (vendedores de vol).
        # Nesse ambiente compram delta na subida e vendem na descida → Call Wall é
        # catalisador, não tecto; Put Wall é trampolim, não chão. As zonas SELL/BUY
        # convencionais são semanticamente erradas em SHORT_GAMMA — suprimidas por completo.
        # Em LONG_GAMMA e UNKNOWN o comportamento padrão (Call Wall = SELL, Put Wall = BUY) mantém-se.
        if gamma_regime_ctx == 'SHORT_GAMMA':
            logger.info(
                f"[ZONES] F5-2-B SHORT_GAMMA {symbol}: zonas Gamma CALL_WALL_SELL e PUT_WALL_BUY "
                "suprimidas — dealers compram delta na subida (Call Wall = catalisador, não tecto)"
            )
        else:
            if call_wall > 0:
                # 1-C: cascade de targets Session POC → D1 POC → VWAP → ATR×1.5
                if poc > 0 and poc < call_wall:
                    gamma_target, gamma_target_lbl = poc, "Session POC"
                elif d1_poc > 0 and d1_poc < call_wall:
                    gamma_target, gamma_target_lbl = d1_poc, "D1 POC"
                elif vwap > 0 and vwap < call_wall:
                    gamma_target, gamma_target_lbl = vwap, "VWAP"
                else:
                    gamma_target, gamma_target_lbl = None, "ATR×1.5"
                add(_make_zone(
                    ZoneType.GAMMA_CALL_WALL_SELL, call_wall, "SHORT", tol_gamma,
                    target=gamma_target, target_label=gamma_target_lbl,
                    sl_mult=0.8, be_mult=0.6, priority=3,
                    label=f"Gamma Call Wall @ {call_wall:.2f}",
                ))

            if put_wall > 0:
                # 1-C: cascade de targets Session POC → D1 POC → VWAP → ATR×1.5
                if poc > 0 and poc > put_wall:
                    gamma_target, gamma_target_lbl = poc, "Session POC"
                elif d1_poc > 0 and d1_poc > put_wall:
                    gamma_target, gamma_target_lbl = d1_poc, "D1 POC"
                elif vwap > 0 and vwap > put_wall:
                    gamma_target, gamma_target_lbl = vwap, "VWAP"
                else:
                    gamma_target, gamma_target_lbl = None, "ATR×1.5"
                add(_make_zone(
                    ZoneType.GAMMA_PUT_WALL_BUY, put_wall, "LONG", tol_gamma,
                    target=gamma_target, target_label=gamma_target_lbl,
                    sl_mult=0.8, be_mult=0.6, priority=3,
                    label=f"Gamma Put Wall @ {put_wall:.2f}",
                ))

    # ── Deduplicação IB×ONH/ONL: se IBH≈ONH ou IBL≈ONL (≤0.5×ATR), suprime ONH/ONL ──
    # Relevante apenas na janela 10:30–11:00 ET onde ambas coexistem.
    if ib_locked:
        ib_on_suppress: set = set()
        for z in zones:
            if z.zone_type not in {ZoneType.ONH_FADE_SELL, ZoneType.ONH_BREAK_BUY} and \
               z.zone_type not in {ZoneType.ONL_FADE_BUY, ZoneType.ONL_BREAK_SELL}:
                continue
            ref = ibh if z.level == onh or (onh > 0 and abs(z.level - onh) < 0.1) else ibl
            if ref > 0 and abs(z.level - ref) <= 0.5 * atr:
                ib_on_suppress.add(id(z))
                logger.debug(
                    f"[ZONES] Dedup IB×ON: {z.zone_type.value} @ {z.level:.2f} "
                    f"suprimida por IB @ {ref:.2f}"
                )
        if ib_on_suppress:
            zones = [z for z in zones if id(z) not in ib_on_suppress]

    # ── Deduplicação Session×D1: Session zones suprimidas quando D1 zone alinhada está próxima ──
    d1_zones     = [z for z in zones if z.zone_type in _D1_ZONE_TYPES]
    suppress_ids = set()
    for sz in zones:
        if sz.zone_type not in _SESSION_VP_TYPES:
            continue
        for dz in d1_zones:
            if sz.direction == dz.direction and abs(sz.level - dz.level) <= 0.5 * atr:
                suppress_ids.add(id(sz))
                logger.debug(
                    f"[ZONES] Dedup Session×D1: {sz.zone_type.value} @ {sz.level:.2f} "
                    f"suprimida por {dz.zone_type.value} @ {dz.level:.2f}"
                )
                break
    if suppress_ids:
        zones = [z for z in zones if id(z) not in suppress_ids]

    # ── F3-2: Deduplicação D1×D1 — suprime zona D1 de menor prioridade ────────
    # quando duas zonas D1 de mesma direção estão a ≤ 0.5×ATR uma da outra
    d1_zones_cur = [z for z in zones if z.zone_type in _D1_ZONE_TYPES]
    d1_sorted    = sorted(d1_zones_cur, key=lambda z: z.priority)  # menor número = melhor
    d1_suppress  = set()
    for i, za in enumerate(d1_sorted):
        if id(za) in d1_suppress:
            continue
        for zb in d1_sorted[i + 1:]:
            if id(zb) in d1_suppress:
                continue
            if za.direction == zb.direction and abs(za.level - zb.level) <= 0.5 * atr:
                d1_suppress.add(id(zb))
                logger.debug(
                    f"[ZONES] Dedup D1×D1: {zb.zone_type.value} @ {zb.level:.2f} "
                    f"suprimida por {za.zone_type.value} @ {za.level:.2f}"
                )
    if d1_suppress:
        zones = [z for z in zones if id(z) not in d1_suppress]

    # ── F5-2: Dedup Gamma×Structural — suprime gamma zone quando zona estrutural
    # de mesma direção está a ≤0.5×ATR (zona mais precisa prevalece) ─────────
    if gamma_reliable:
        non_gamma = [z for z in zones if z.zone_type not in _GAMMA_ZONE_TYPES]
        gamma_suppress: set = set()
        for gz in zones:
            if gz.zone_type not in _GAMMA_ZONE_TYPES:
                continue
            for sz in non_gamma:
                if sz.direction == gz.direction and abs(sz.level - gz.level) <= 0.5 * atr:
                    gamma_suppress.add(id(gz))
                    logger.debug(
                        f"[ZONES] Dedup Gamma×Structural: {gz.zone_type.value} @ {gz.level:.2f} "
                        f"suprimida por {sz.zone_type.value} @ {sz.level:.2f}"
                    )
                    break
        if gamma_suppress:
            zones = [z for z in zones if id(z) not in gamma_suppress]

    zones.sort(key=lambda z: (z.priority, z.distance_to(price)))
    return zones


# ═══════════════════════════════════════════════════════════════════
# 3. Avaliação de Entrada na Zona
# ═══════════════════════════════════════════════════════════════════

def evaluate_zone_entry(
    zone: ZoneOfInterest,
    live_data: Dict[str, Any],
    delta_ratio: Optional[float],
    atr: float,
    confluence_boost: float = 0.0,
    macro_context: Optional[Dict[str, Any]] = None,
    on_session_minutes: float = 0.0,
    session_params: Optional[Dict] = None,
    symbol: Optional[str] = None,
    regime_bias: str = "NEUTRAL",
) -> Tuple[bool, str, float, List[str], ScoreBreakdown]:
    """
    Verifica se uma zona está gerando entrada válida.

    Retorna:
      (passed, quality, risk_modifier, block_reasons, score_breakdown)
      quality: 'STRONG' | 'MODERATE' | 'WEAK' | 'BLOCKED' | 'NO_TRADE'

    Indicadores de fluxo (score base):
    1. OFI fast: +2.0 (alinhado) / +1.0 (fraco) / 0 (contra) — principal indicador de pressão
    2. OFI slow: penalidade −1.5 se oposto forte (threshold: fade=0.55, momentum=0.35)
    3. Delta ratio: quality cap — se diverge fortemente (< −DELTA_RATIO_MIN direcional),
       limita qualidade a MODERATE mesmo que score ≥ 4.0 (auditor de divergência, não score)
    4. Absorção: +1.5 (alinhada) / −0.5 (contrária); SELL_ABSORBED para LONG, BUY_ABSORBED para SHORT
    5. CVD trend: +0.5 se RISING (LONG) ou FALLING (SHORT) — momentum direcional recente
       Fix E: −0.5 se FALLING (LONG fade) ou RISING (SHORT fade) — CVD opõe-se à reversão
       Intervalo CVD em zonas de fade: [−0.5, +0.5]; em momentum: [0, +0.5]
    6. Tape speed (EMA ratio): assimétrico por categoria —
       Fade: ≤0.9 → +0.3 (tape a secar confirma reversão) — Fix D: condicional a OFI slow alinhado;
             ≥1.6 → −0.5 (continuation risk)
       Momentum/Breakout: <1.4 → −0.5 (tape lento = probe mecânico)

    Modificadores estruturais (apenas se base_flow >= BASE_FLOW_GATE = 1.5):
    - F3-1: confluence_boost +1.5 se ≥2 zonas alinhadas sobrepostas
    - 1-D: gamma_sentiment ±0.5/−0.3 (apenas zonas Gamma)
    - 1-E: ZGL gamma_regime ±0.5 — bónus positivo SUPRIMIDO em zonas Gamma (evita dupla contagem);
           penalidades negativas aplicam-se a 100% das zonas (escudo macroestrutura)

    Protecções:
    - F1-3: ONH_BREAK / ONL_BREAK hard block se ofi_fast < 0.30
    - F6 Flow Base Gate: bónus bloqueados se fluxo base insuficiente
    - Late session: −0.5 pts após 15:30 ET (≥360 min RTH)
    """
    sign    = 1.0 if zone.direction == "LONG" else -1.0
    reasons = []

    # Resolve parâmetros de sessão (session_params override > módulo global)
    # Suporta overrides per-símbolo: zones_score_strong_thresh_mnq / _mes
    _sp  = session_params or {}
    _sfx = f"_{symbol.lower()}" if symbol else ""
    _fade_thresh = float(_sp.get(f"zones_ofi_slow_fade_thresh{_sfx}",
                                  _sp.get("zones_ofi_slow_fade_thresh",     OFI_SLOW_BLOCK_FADE)))
    _mom_thresh  = float(_sp.get(f"zones_ofi_slow_momentum_thresh{_sfx}",
                                  _sp.get("zones_ofi_slow_momentum_thresh", OFI_SLOW_BLOCK_MOMENTUM)))
    _mod_thresh  = float(_sp.get(f"zones_score_moderate_thresh{_sfx}",
                                  _sp.get("zones_score_moderate_thresh",    2.5)))
    _str_thresh  = float(_sp.get(f"zones_score_strong_thresh{_sfx}",
                                  _sp.get("zones_score_strong_thresh",      4.0)))
    score   = 0.0

    ofi_fast  = live_data.get("ofi_fast", 0.0) or 0.0
    ofi_slow  = live_data.get("ofi_slow", 0.0) or 0.0
    abs_flag  = live_data.get("absorption_flag", False)
    abs_side  = live_data.get("absorption_side", "NONE")
    cvd_trend = live_data.get("cvd_trend", "NEUTRAL") or "NEUTRAL"

    # ── AND gate: Break/Pullback zones exigem OFI fast > 0.30 na direção ────
    # Cobre: ONH_BREAK_BUY, ONL_BREAK_SELL, IBH_PULLBACK_BUY, IBL_PULLBACK_SELL
    if zone.zone_type in _BREAK_ZONE_TYPES:
        if ofi_fast * sign < OFI_BREAK_MIN:
            msg = (f"{zone.zone_type.value} requer OFI fast > {OFI_BREAK_MIN:.2f} "
                   f"na direção (atual: {ofi_fast:.3f})")
            reasons.append(msg)
            bd = ScoreBreakdown(base_score=0.0)
            return False, "BLOCKED", 1.0, reasons, bd

    # ── Fix B: F1-2 estendido — OFI slow bearish forte em SIGMA2_FADE_BUY ─────
    # Quando OFI slow < −threshold, o fluxo vendedor de fundo prevalece sobre a
    # absorção local no 2σ: a rejeição passa a ser continuação disfarçada de
    # distribuição, não reversão genuína.
    # HYPOTHESIS = True: threshold de −0.30 não validado empiricamente (Fix C
    # não estava activo). Rever após N≥30 trades com ofi_slow_raw registado.
    if zone.zone_type == ZoneType.SIGMA2_FADE_BUY:
        if ofi_slow < -FIX_B_OFI_SLOW_FADE_BUY_THRESH:
            _hyp_tag = " [hypothesis=True]" if FIX_B_HYPOTHESIS else ""
            msg = (
                f"F1-2-EXT: SIGMA2_FADE_BUY bloqueada — OFI slow bearish forte "
                f"({ofi_slow:.3f} < -{FIX_B_OFI_SLOW_FADE_BUY_THRESH:.2f}){_hyp_tag}"
            )
            reasons.append(msg)
            logger.info(
                "[FIX-B] SIGMA2_FADE_BUY bloqueada por OFI slow bearish "
                "(ofi_slow=%.3f < -%.2f)%s",
                ofi_slow, FIX_B_OFI_SLOW_FADE_BUY_THRESH, _hyp_tag,
            )
            bd = ScoreBreakdown(base_score=0.0)
            return False, "BLOCKED", 1.0, reasons, bd

    # 1. OFI fast alinhado
    # Fade zones: comprador forte no VAH / vendedor forte no VAL = absorção = confirmação da fade.
    # Usa |ofi_fast| — actividade de tape em qualquer direcção confirma liquidez no nível.
    # Momentum/breakout/pullback: exige OFI na direcção do trade (sign).
    _is_fade = zone.zone_type in _FADE_ZONE_TYPES
    _ofi_directional = abs(ofi_fast) if _is_fade else ofi_fast * sign
    if _ofi_directional >= OFI_FAST_MIN:
        score += 2.0
        if _is_fade and ofi_fast * sign < 0:
            reasons.append(f"OFI absorção em fade (|{ofi_fast:.3f}|) +2.0 pts")
    elif _ofi_directional >= OFI_FAST_MIN * 0.5:
        score += 1.0
        reasons.append(f"OFI fast fraco ({ofi_fast:.3f})")
    else:
        reasons.append(f"OFI fast não alinhado ({ofi_fast:.3f})")

    # ── F1-1: OFI slow oposto → penalidade -1.5 pts ──────────────────────────
    # Threshold diferenciado: fade zones têm slow naturalmente contra (preço chegou movendo-se
    # contra o trade), threshold mais tolerante. Momentum/pullback exigem slow alinhado.
    ofi_slow_penalty = 0.0
    slow_block = (_fade_thresh if zone.zone_type in _FADE_ZONE_TYPES
                  else _mom_thresh)
    if ofi_slow * sign < -slow_block:
        ofi_slow_penalty = -OFI_SLOW_PENALTY_PTS
        score += ofi_slow_penalty
        reasons.append(
            f"OFI slow contra direção ({ofi_slow:.3f}) "
            f"— penalidade {ofi_slow_penalty:.1f} pts"
        )

    base_before_ofi_slow = score - ofi_slow_penalty  # base limpo para o breakdown
    _rth_bias_modifier: float = 0.0  # Item 5: tracking separado para AutoTune

    # 2. Delta ratio — quality cap (não contribui ao score; penaliza qualidade se diverge forte)
    # delta_ratio e ofi_fast partilham a mesma fonte (tape), correlação ~0.75-0.90.
    # Somar pontos independentes criaria inflação de score. Alternativa: auditor de divergência:
    # se delta_ratio contraria fortemente a direção → cap em MODERATE mesmo que score total ≥ 4.0.
    delta_quality_capped = False
    if delta_ratio is not None and delta_ratio * sign < -DELTA_RATIO_MIN:
        delta_quality_capped = True
        reasons.append(
            f"Delta ratio divergente ({delta_ratio:.3f}) — qualidade limitada a MODERATE"
        )

    # 3. Absorção alinhada
    # LONG quer SELL_ABSORBED (compradores absorvendo vendedores → pressão bullish sustentada)
    # SHORT quer BUY_ABSORBED (vendedores absorvendo compradores → pressão bearish sustentada)
    expected_abs = "SELL_ABSORBED" if zone.direction == "LONG" else "BUY_ABSORBED"
    if abs_flag and abs_side == expected_abs:
        score += 1.5
        base_before_ofi_slow += 1.5
        reasons.append(f"Absorção alinhada ({abs_side}) +1.5 pts")
    elif abs_flag and abs_side not in (expected_abs, "NONE"):
        score -= 0.5
        base_before_ofi_slow -= 0.5
        reasons.append(f"Absorção contrária ({abs_side}) −0.5 pts")

    # 4. CVD trend alinhado (momentum recente do delta, mais informativo que sinal absoluto)
    if (zone.direction == "LONG" and cvd_trend == "RISING") or \
       (zone.direction == "SHORT" and cvd_trend == "FALLING"):
        score += 0.5
        base_before_ofi_slow += 0.5
        reasons.append(f"CVD alinhado ({cvd_trend}) +0.5 pts")
    elif cvd_trend not in ("NEUTRAL", "RISING", "FALLING"):
        pass  # valor desconhecido — não pontua nem penaliza

    # Fix E: CVD opõe-se à direcção da fade → penalidade
    # FALLING num LONG fade = pressão vendedora de médio prazo contra a entrada compradora.
    # RISING num SHORT fade = pressão compradora de médio prazo contra a entrada vendedora.
    # Assimetria com o bónus de alinhamento: intervalo total CVD = [−0.5, +0.5] em zonas de fade.
    if FIX_E_CVD_FADE_PENALTY_ENABLED and zone.zone_type in _FADE_ZONE_TYPES:
        if zone.direction == "LONG" and cvd_trend == "FALLING":
            score -= FIX_E_CVD_FADE_PENALTY_PTS
            base_before_ofi_slow -= FIX_E_CVD_FADE_PENALTY_PTS
            reasons.append(
                f"Fix E: CVD FALLING em fade LONG "
                f"— penalidade −{FIX_E_CVD_FADE_PENALTY_PTS:.1f} pts"
            )
        elif zone.direction == "SHORT" and cvd_trend == "RISING":
            score -= FIX_E_CVD_FADE_PENALTY_PTS
            base_before_ofi_slow -= FIX_E_CVD_FADE_PENALTY_PTS
            reasons.append(
                f"Fix E: CVD RISING em fade SHORT "
                f"— penalidade −{FIX_E_CVD_FADE_PENALTY_PTS:.1f} pts"
            )

    # Fix 5: Price vs RTH Open — bias intraday (bónus/penalidade simétricos)
    # Alinhado:  fade BUY  quando bias BULLISH (preço ABOVE open) → mean-reversion valid → +0.3
    #            fade SELL quando bias BEARISH (preço BELOW open) → mean-reversion valid → +0.3
    # Oposto:    fade SELL quando bias BULLISH (short into rally vs open) → contra trend → −0.3
    #            fade BUY  quando bias BEARISH (buy into sell-off vs open) → contra trend → −0.3
    if FIX_5_RTH_BIAS_ENABLED and regime_bias != "NEUTRAL" and zone.zone_type in _FADE_ZONE_TYPES:
        if zone.direction == "LONG" and regime_bias == "BULLISH":
            score += FIX_5_RTH_BIAS_SCORE
            base_before_ofi_slow += FIX_5_RTH_BIAS_SCORE
            _rth_bias_modifier += FIX_5_RTH_BIAS_SCORE
            reasons.append(
                f"Fix 5: fade BUY alinhada com bias BULLISH (preço acima RTH open) "
                f"+{FIX_5_RTH_BIAS_SCORE:.1f} pts"
            )
        elif zone.direction == "SHORT" and regime_bias == "BEARISH":
            score += FIX_5_RTH_BIAS_SCORE
            base_before_ofi_slow += FIX_5_RTH_BIAS_SCORE
            _rth_bias_modifier += FIX_5_RTH_BIAS_SCORE
            reasons.append(
                f"Fix 5: fade SELL alinhada com bias BEARISH (preço abaixo RTH open) "
                f"+{FIX_5_RTH_BIAS_SCORE:.1f} pts"
            )
        elif FIX_5_RTH_BIAS_PENALTY_ENABLED:
            # Fade OPÕE o bias: short when BULLISH, long when BEARISH
            if zone.direction == "SHORT" and regime_bias == "BULLISH":
                score += FIX_5_RTH_BIAS_PENALTY
                base_before_ofi_slow += FIX_5_RTH_BIAS_PENALTY
                _rth_bias_modifier += FIX_5_RTH_BIAS_PENALTY
                reasons.append(
                    f"Fix 5: fade SELL contra bias BULLISH (short into rally vs RTH open) "
                    f"{FIX_5_RTH_BIAS_PENALTY:.1f} pts"
                )
            elif zone.direction == "LONG" and regime_bias == "BEARISH":
                score += FIX_5_RTH_BIAS_PENALTY
                base_before_ofi_slow += FIX_5_RTH_BIAS_PENALTY
                _rth_bias_modifier += FIX_5_RTH_BIAS_PENALTY
                reasons.append(
                    f"Fix 5: fade BUY contra bias BEARISH (long into sell-off vs RTH open) "
                    f"{FIX_5_RTH_BIAS_PENALTY:.1f} pts"
                )

    # 5. Tape Speed — EMA ratio assimétrico por categoria de zona
    # Lido directamente de live_data (calculado em live_data_service pelo EMA loop).
    # Thresholds assimétricos: momentum exige aceleração; fade prefere desaceleração.
    # Neutro se speed_ratio não disponível (mercado fechado, primeiro ciclo de cálculo).
    tape_speed_modifier = 0.0
    speed_ratio = live_data.get("speed_ratio")
    if speed_ratio is not None and speed_ratio > 0:
        is_fade_zone = zone.zone_type in _FADE_ZONE_TYPES
        if is_fade_zone:
            if speed_ratio <= TAPE_SPEED_FADE_BONUS_MAX:
                # Tape a secar numa zona de fade → possível reversão genuína.
                # Fix D: bónus condicional — OFI slow não deve opor-se à direcção.
                # Tape lento com OFI slow contra direcção = pausa antes de continuation, não reversão.
                _fix_d_ofi_aligned = (
                    not FIX_D_TAPE_SPEED_CONDITIONAL
                    or (zone.direction == "LONG"  and ofi_slow >= 0)
                    or (zone.direction == "SHORT" and ofi_slow <= 0)
                )
                if _fix_d_ofi_aligned:
                    tape_speed_modifier = TAPE_SPEED_BONUS_PTS
                    score += tape_speed_modifier
                    # NÃO soma a base_before_ofi_slow: tape_speed é componente separado no breakdown
                    reasons.append(
                        f"Tape a secar em fade (ratio={speed_ratio:.2f}) "
                        f"— reversão confirmada +{TAPE_SPEED_BONUS_PTS:.1f} pts"
                    )
                else:
                    # Fix D activo: OFI slow opõe-se — bónus suspenso
                    reasons.append(
                        f"Fix D: bónus tape speed suspenso — OFI slow opõe-se "
                        f"(ofi_slow={ofi_slow:.3f}, dir={zone.direction}, ratio={speed_ratio:.2f})"
                    )
            elif speed_ratio >= TAPE_SPEED_FADE_PENALTY:
                # Tape acelerado numa zona de fade → risco de continuation, não reversão
                tape_speed_modifier = -TAPE_SPEED_PENALTY_PTS
                score += tape_speed_modifier
                reasons.append(
                    f"Tape acelerado em fade (ratio={speed_ratio:.2f}) "
                    f"— risco continuation −{TAPE_SPEED_PENALTY_PTS:.1f} pts"
                )
        else:
            # Zonas de momentum/breakout/pullback: tape lento sugere probe mecânico
            if speed_ratio < TAPE_SPEED_MOMENTUM_MIN:
                tape_speed_modifier = -TAPE_SPEED_PENALTY_PTS
                score += tape_speed_modifier
                reasons.append(
                    f"Tape lento em momentum (ratio={speed_ratio:.2f}) "
                    f"— convicção reduzida −{TAPE_SPEED_PENALTY_PTS:.1f} pts"
                )
            # Tape neutro ou acelerado em momentum → sem bónus (OFI fast já captura a pressão)

    # ── 1-D + 1-E: Gamma modifier (sentiment + ZGL regime) ───────────────────
    gamma_modifier = 0.0
    ctx_gm = macro_context or {}
    gamma_regime    = ctx_gm.get('gamma_regime', 'UNKNOWN')
    gamma_sentiment = ctx_gm.get('gamma_sentiment', 'NEUTRAL')
    zero_gamma      = float(ctx_gm.get('zero_gamma', 0.0) or 0.0)

    # 1-D: Gamma sentiment modifier (apenas para zonas Gamma)
    if zone.zone_type in _GAMMA_ZONE_TYPES and gamma_sentiment not in ('NEUTRAL', 'UNKNOWN', ''):
        # GAMMA_CALL_WALL_SELL direction=SHORT, sentiment=NEGATIVE → dealers bearish → alinhado
        # GAMMA_PUT_WALL_BUY  direction=LONG,  sentiment=POSITIVE → dealers bullish → alinhado
        direction_is_short = zone.direction == "SHORT"
        sentiment_is_negative = gamma_sentiment in ('NEGATIVE', 'BEARISH')
        sentiment_is_positive = gamma_sentiment in ('POSITIVE', 'BULLISH')
        aligned = (direction_is_short and sentiment_is_negative) or \
                  (not direction_is_short and sentiment_is_positive)
        if aligned:
            gamma_modifier += 0.5
            reasons.append(f"Gamma sentiment alinhado ({gamma_sentiment}) +0.5 pts")
        else:
            gamma_modifier -= 0.3
            reasons.append(f"Gamma sentiment oposto ({gamma_sentiment}) −0.3 pts")

    # 1-E: ZGL gamma regime modifier (todas as zonas quando zero_gamma disponível)
    # Fix double-count: bónus positivo suprimido em zonas Gamma (GAMMA_CALL_WALL / GAMMA_PUT_WALL)
    # porque o nível da zona já existe por causa do gamma — contar +0.5 seria dupla contagem.
    # Penalidades negativas continuam ativas em 100% das zonas — são protecção, não inflação.
    if gamma_regime in ('LONG_GAMMA', 'SHORT_GAMMA') and zero_gamma > 0:
        is_fade       = zone.zone_type in _FADE_ZONE_TYPES
        is_long_gamma = gamma_regime == 'LONG_GAMMA'
        is_gamma_zone = zone.zone_type in _GAMMA_ZONE_TYPES
        # Fade em long-gamma → dealers estabilizam → fade favorecido
        # Momentum em short-gamma → dealers amplificam → momentum favorecido
        regime_confirms = (is_fade and is_long_gamma) or (not is_fade and not is_long_gamma)
        zgl_label = "LONG_GAMMA" if is_long_gamma else "SHORT_GAMMA"
        if regime_confirms:
            if not is_gamma_zone:
                # Bónus positivo apenas em zonas não-Gamma (evita dupla contagem)
                gamma_modifier += 0.5
                reasons.append(
                    f"Gamma regime confirma {'fade' if is_fade else 'momentum'} ({zgl_label}) +0.5 pts"
                )
            # Zonas Gamma: regime já está incorporado no nível — sem bónus extra
        else:
            # Penalidade negativa aplica-se a TODAS as zonas (escudo de risco macroestrutura)
            gamma_modifier -= 0.5
            reasons.append(
                f"Gamma regime opõe {'fade' if is_fade else 'momentum'} ({zgl_label}) −0.5 pts"
            )

    # ── F6: Flow Base Gate ────────────────────────────────────────────────────
    # Bónus estruturais (confluence_boost e gamma_modifier positivo) só são aplicados
    # se o score base de fluxo (OFI fast + delta + absorção + CVD) for >= BASE_FLOW_GATE.
    # Penalidades negativas (gamma_modifier < 0) passam sempre — são protecção, não inflação.
    base_flow_gated = base_before_ofi_slow < BASE_FLOW_GATE
    if base_flow_gated:
        effective_confluence = 0.0
        effective_gamma      = min(gamma_modifier, 0.0)  # só penalidades negativas passam
        if confluence_boost > 0.0 or gamma_modifier > 0.0:
            blocked_pts = confluence_boost + max(gamma_modifier, 0.0)
            reasons.append(
                f"F6 Flow Gate: score base={base_before_ofi_slow:.1f} < {BASE_FLOW_GATE} "
                f"— bónus de confluência/gamma bloqueados ({blocked_pts:+.1f} pts suprimidos)"
            )
    else:
        effective_confluence = confluence_boost
        effective_gamma      = gamma_modifier

    # ── Late Session Penalty ──────────────────────────────────────────────────
    # Após 15:30 ET (≥360 min de sessão RTH), liquidez reduz e slippage aumenta.
    # Penalidade suave que eleva o threshold efectivo sem bloquear sinais fortes.
    late_session_penalty = 0.0
    if on_session_minutes >= LATE_SESSION_MIN:
        late_session_penalty = LATE_SESSION_PENALTY
        reasons.append(
            f"Sessão tardia ({on_session_minutes:.0f} min, ≥{LATE_SESSION_MIN}) "
            f"— penalidade {LATE_SESSION_PENALTY:.1f} pts"
        )

    breakdown = ScoreBreakdown(
        base_score=round(base_before_ofi_slow - _rth_bias_modifier, 3),
        ofi_slow_penalty=round(ofi_slow_penalty, 3),
        tape_speed_modifier=round(tape_speed_modifier, 3),
        late_session_penalty=round(late_session_penalty, 3),
        confluence_boost=round(effective_confluence, 3),
        gamma_modifier=round(effective_gamma, 3),
        rth_bias_modifier=round(_rth_bias_modifier, 3),
        base_flow_gated=base_flow_gated,
        delta_quality_capped=delta_quality_capped,
    )

    total = breakdown.total_score  # inclui todos os componentes via @property

    if effective_confluence > 0:
        reasons_confluence = f"Confluência de zonas +{effective_confluence:.1f} pts"
        reasons = [reasons_confluence] + [r for r in reasons if "Score" not in r]

    if total >= _str_thresh:
        quality = "STRONG"
        risk_modifier = 1.0
        passed = True
    elif total >= _mod_thresh:
        quality = "MODERATE"
        risk_modifier = 0.85
        passed = True
    elif total >= 1.5:
        quality = "WEAK"
        risk_modifier = 0.70
        passed = False
        reasons.append(f"Score insuficiente ({total:.1f}/{_str_thresh:.1f})")
    else:
        quality = "NO_TRADE"
        risk_modifier = 1.0
        passed = False
        reasons.append(f"Score muito baixo ({total:.1f}/{_str_thresh:.1f}) — sem confirmação de fluxo")

    # Delta quality cap: delta_ratio fortemente divergente limita STRONG → MODERATE
    # O trade ainda passa mas com position sizing reduzido (risk_modifier=0.85).
    if delta_quality_capped and quality == "STRONG":
        quality = "MODERATE"
        risk_modifier = 0.85

    return passed, quality, risk_modifier, reasons, breakdown


# ═══════════════════════════════════════════════════════════════════
# 4. Parâmetros S3 adaptados à zona + regime
# ═══════════════════════════════════════════════════════════════════

def compute_zone_s3(
    zone: ZoneOfInterest,
    regime: ScalpDayRegime,
    price: float,
    atr: float,
    risk_modifier: float,
    symbol: str,
    quantity: int = 1,
) -> Dict[str, Any]:
    """
    Gera parâmetros de ordem (SL, TP, BE) adaptados à zona e ao regime.
    SL = sl_atr_mult × ATR
    BE = be_atr_mult × ATR
    TP = target estrutural da zona se válido, senão ATR × 1.5
    """
    atr_eff = max(atr, ATR_FALLBACK.get(symbol, 5.0))

    sl_pts = round(zone.sl_atr_mult * atr_eff, 2)
    be_pts = round(zone.be_atr_mult * atr_eff, 2)

    # Cap de SL por zona — impede stops desproporcionais em dias de ATR elevado
    _zone_type_str = zone.zone_type.value if hasattr(zone.zone_type, 'value') else str(zone.zone_type)
    _sl_cap = ZONE_SL_MAX_PTS.get(_zone_type_str, {}).get(symbol)
    if _sl_cap and sl_pts > _sl_cap:
        sl_pts = _sl_cap
        be_pts = min(be_pts, sl_pts * 0.7)

    min_tp_pts = {'MNQ': 2.0, 'MES': 1.0}.get(symbol, 2.0)
    max_tp_pts = {'MNQ': 25.0, 'MES': 10.0}.get(symbol, 25.0)

    if zone.target and zone.target > 0:
        raw_tp_pts = abs(zone.target - price)
        if min_tp_pts <= raw_tp_pts <= max_tp_pts:
            tp_pts  = round(raw_tp_pts, 2)
            tp_label = zone.target_label
        else:
            tp_pts  = round(1.5 * atr_eff, 2)
            tp_label = "ATR×1.5 (target fora de range)"
    else:
        tp_pts  = round(1.5 * atr_eff, 2)
        tp_label = "ATR×1.5"

    tp_was_adjusted = False
    if tp_pts < sl_pts:
        tp_pts          = sl_pts
        tp_label       += " (ajustado para R:R≥1.0)"
        tp_was_adjusted = True

    if zone.direction == "LONG":
        sl_price = round(price - sl_pts, 2)
        tp_price = round(price + tp_pts, 2)
        action   = "buy"
    else:
        sl_price = round(price + sl_pts, 2)
        tp_price = round(price - tp_pts, 2)
        action   = "sell"

    return {
        "action":            action,
        "entry_price":       price,
        "stop_loss_price":   sl_price,
        "take_profit_price": tp_price,
        "breakeven":         be_pts,
        "quantity":          max(1, round(quantity * risk_modifier)),
        "sl_pts":            sl_pts,
        "tp_pts":            tp_pts,
        "be_pts":            be_pts,
        "atr_m1":            atr_eff,
        "tp_label":          tp_label,
        "tp_was_adjusted":   tp_was_adjusted,
        "zone_type":         zone.zone_type.value,
        "zone_label":        zone.label,
        "regime":            regime.value,
        "rr_ratio":          round(tp_pts / sl_pts, 2) if sl_pts > 0 else 0,
    }


# ═══════════════════════════════════════════════════════════════════
# 5. Função principal: avaliar todas as zonas e retornar a melhor
# ═══════════════════════════════════════════════════════════════════

def evaluate_zones(
    price: float,
    levels: Dict[str, Any],
    live_data: Dict[str, Any],
    delta_ratio: Optional[float],
    atr: float,
    symbol: str,
    quantity: int = 1,
    on_session_minutes: float = 999,
    macro_context: Optional[Dict[str, Any]] = None,
    session_group: Optional[str] = None,
    m1_bars: Optional[List[Dict[str, Any]]] = None,
    s1_regime_value: Optional[str] = None,
    disabled_zone_types: Optional[List[str]] = None,
    zone_min_score_overrides: Optional[Dict[str, float]] = None,
    zone_min_confluence_overrides: Optional[Dict[str, float]] = None,
    snapshots_in_regime: int = 999,
    session_hod: float = 0.0,
    session_lod: float = 0.0,
    d1_high: float = 0.0,
    d1_low: float = 0.0,
) -> Dict[str, Any]:
    """
    Ponto de entrada principal do modelo de zonas.

    1. F5-1: Verifica Term Structure Hard Gate antes de qualquer computação
    2. Detecta o regime do dia
    3. Identifica zonas ativas (com F1/F2/F5 aplicados)
    4. F7: Injeta zonas EMA pullback dinâmicas (se m1_bars + s1_regime_value fornecidos)
    5. Para cada zona in_zone, avalia entrada de fluxo
    6. Aplica cooldown (F2-2): zonas com signal recente hibernam 3 min
    7. Retorna a melhor zona com parâmetros S3, score_breakdown e status

    Retorna dict com:
      regime, zones_nearby, best_zone, s3, status, quality,
      block_reasons, score_breakdown, macro_context_applied
    """
    ctx      = macro_context or {}
    ts_ratio = float(ctx.get('ts_ratio', 0.0) or 0.0)

    # Resolve parâmetros de sessão para este ciclo de avaliação
    try:
        from services.trading_calendar_service import get_session_group as _get_sg, get_session_label as _get_sl
        _sg = session_group or _get_sg(_get_sl())
    except Exception:
        _sg = session_group or "NY"
    _zone_session_params = get_session_params(_sg)

    # ── F5-1: HARD_STOP antecipado — evita qualquer computação em pânico extremo ──
    if ts_ratio >= 1.10:
        return {
            "regime":        "UNDEFINED",
            "all_zones":     [],
            "best_zone":     None,
            "s3":            None,
            "status":        "HARD_STOP",
            "quality":       "NO_TRADE",
            "block_reasons": [
                f"Term Structure BACKWARDATION extrema (VIX/VIX3M={ts_ratio:.3f} ≥ 1.10) "
                "— pânico ativo, todas as zonas suspensas"
            ],
            "score_breakdown": None,
            "active_params": {
                "base_flow_gate": BASE_FLOW_GATE,
                "score_moderate": float(_zone_session_params.get("zones_score_moderate_thresh", 2.5)),
                "score_strong":   float(_zone_session_params.get("zones_score_strong_thresh", 4.0)),
                "ofi_slow_fade":  float(_zone_session_params.get("zones_ofi_slow_fade_thresh", OFI_SLOW_BLOCK_FADE)),
                "d30_threshold":  20.0,
            },
            "macro_context_applied": {
                "ts_ratio":           ts_ratio,
                "ts_state":           ctx.get('ts_state', 'UNKNOWN'),
                "ts_hard_stop":       True,
                "ts_fade_suppressed": True,
                "gamma_reliable":     ctx.get('gamma_reliable', False),
                "gamma_call_wall":    ctx.get('gamma_call_wall', 0.0),
                "gamma_put_wall":     ctx.get('gamma_put_wall', 0.0),
                "zero_gamma":         ctx.get('zero_gamma', 0.0),
                "gamma_regime":       ctx.get('gamma_regime', 'UNKNOWN'),
                "gamma_sentiment":    ctx.get('gamma_sentiment', 'NEUTRAL'),
            },
        }

    vwap     = levels.get("vwap", 0.0) or 0.0
    vwap_std = levels.get("vwap_std", 0.0) or 0.0
    d1_poc   = levels.get("d1_poc", 0.0) or 0.0
    d1_vah   = levels.get("d1_vah", 0.0) or 0.0
    d1_val   = levels.get("d1_val", 0.0) or 0.0

    # HF3: raw → histerese → regime estabilizado (evita flip-flop em breakouts)
    raw_regime = detect_day_regime(price, vwap, vwap_std, d1_poc, d1_vah, d1_val, atr, symbol)
    regime     = apply_regime_hysteresis(symbol, raw_regime)

    # ── Item 4: RTH_OPEN como regime nativo ──────────────────────────────────
    # Override após histerese (baseado em tempo, não preço — bypass de debounce correcto).
    # Primeiros FIX_A_RTH_OPEN_DURATION_MIN de sessão RTH → regime = RTH_OPEN.
    if (on_session_minutes is not None
            and 0.0 <= on_session_minutes < FIX_A_RTH_OPEN_DURATION_MIN):
        regime = ScalpDayRegime.RTH_OPEN

    # ── Item 6: CVD como confirmação de regime S1 (informativo — não gate hard) ──
    _cvd_trend_ctx = live_data.get("cvd_trend", "NEUTRAL") or "NEUTRAL"
    _regime_cvd_conf = "NEUTRAL"
    if FIX_6_CVD_REGIME_CONF_ENABLED:
        if regime == ScalpDayRegime.EXPANSION_BULL:
            _regime_cvd_conf = (
                "CONFIRMED" if _cvd_trend_ctx == "RISING"
                else "CONTESTED" if _cvd_trend_ctx == "FALLING"
                else "NEUTRAL"
            )
        elif regime == ScalpDayRegime.EXPANSION_BEAR:
            _regime_cvd_conf = (
                "CONFIRMED" if _cvd_trend_ctx == "FALLING"
                else "CONTESTED" if _cvd_trend_ctx == "RISING"
                else "NEUTRAL"
            )
        # ROTATION, RTH_OPEN, BREAKOUT, UNDEFINED: sem confirmação direcional por CVD
    if _regime_cvd_conf != "NEUTRAL":
        logger.debug(
            "[Fix 6] %s regime=%s cvd=%s → cvd_conf=%s",
            symbol, regime.value, _cvd_trend_ctx, _regime_cvd_conf,
        )

    # ── Item 5: Price vs RTH Open — bias intraday ─────────────────────────────
    _rth_open_price_ctx = levels.get("rth_open_price", 0.0) or 0.0
    _price_vs_rth_open  = "NEUTRAL"
    _regime_bias        = "NEUTRAL"
    if FIX_5_RTH_BIAS_ENABLED and _rth_open_price_ctx > 0:
        _bias_thr = atr * FIX_5_RTH_BIAS_ATR_THRESH
        if price > _rth_open_price_ctx + _bias_thr:
            _price_vs_rth_open = "ABOVE"
            _regime_bias       = "BULLISH"
        elif price < _rth_open_price_ctx - _bias_thr:
            _price_vs_rth_open = "BELOW"
            _regime_bias       = "BEARISH"
        logger.debug(
            "[Fix 5] %s price=%.2f rth_open=%.2f → %s bias=%s",
            symbol, price, _rth_open_price_ctx, _price_vs_rth_open, _regime_bias,
        )

    all_zones = identify_zones(
        regime, price, levels, atr, symbol, on_session_minutes,
        macro_context=macro_context,
    )

    if disabled_zone_types:
        _disabled_set = set(disabled_zone_types)
        all_zones = [z for z in all_zones if z.zone_type.value not in _disabled_set]
        if _disabled_set:
            logger.debug("[ZONES] Filtradas %d zonas desactivadas: %s", len(_disabled_set), _disabled_set)

    in_zone = [z for z in all_zones if z.in_zone(price)]

    # ── Fix A: Bloquear SIGMA2_FADE_* no regime RTH_OPEN ─────────────────────
    # Refactorizado: condição usa regime nativo RTH_OPEN (Item 4) em vez de comparação de tempo.
    # Semântica idêntica — RTH_OPEN é activado pelos mesmos FIX_A_RTH_OPEN_DURATION_MIN.
    # BUY: evidência directa — 3/3 STOP_HIT, 0% WR, −$1025.
    # SELL: argumento estrutural simétrico — shadow log acumula para validação retrospectiva.
    # Toggle independente por direcção: FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_BUY /
    #   FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_SELL (ambos True por defeito).
    _fix_a_shadow: List[Dict] = []
    if regime == ScalpDayRegime.RTH_OPEN and (FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_BUY
                                               or FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_SELL):
        _fix_a_blocked_types: set = set()
        if FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_BUY:
            _fix_a_blocked_types.add(ZoneType.SIGMA2_FADE_BUY)
        if FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_SELL:
            _fix_a_blocked_types.add(ZoneType.SIGMA2_FADE_SELL)

        _filtered_in_zone: List['ZoneOfInterest'] = []
        for _z in in_zone:
            if _z.zone_type in _fix_a_blocked_types:
                _fix_a_shadow.append({
                    "zone_type":   _z.zone_type.value,
                    "level":       _z.level,
                    "label":       _z.label,
                    "session_min": round(on_session_minutes, 1) if on_session_minutes else 0,
                    "blocked_by":  "FIX_A_RTH_OPEN",
                })
                logger.info(
                    "[FIX-A] %s bloqueada — regime=RTH_OPEN "
                    "(session_min=%.1f < %.0f) — zona level=%.2f shadow=True",
                    _z.zone_type.value,
                    on_session_minutes or 0.0, FIX_A_RTH_OPEN_DURATION_MIN, _z.level,
                )
            else:
                _filtered_in_zone.append(_z)
        in_zone = _filtered_in_zone

    # ── Fix 3a: gap_open — gap significativo em RTH_OPEN suprime fades/EMA ────
    # gap = abs(rth_open_price − d1_poc) > FIX_3_GAP_OPEN_ATR_MULT × ATR.
    # Em dias de gap, o price discovery é especialmente violento → fades arriscadas.
    # Aplicado apenas dentro do regime RTH_OPEN (complemento ao Fix A).
    if (FIX_3_GAP_OPEN_ENABLED
            and regime == ScalpDayRegime.RTH_OPEN
            and _rth_open_price_ctx > 0
            and d1_poc > 0
            and atr > 0):
        _gap_size = abs(_rth_open_price_ctx - d1_poc)
        if _gap_size > FIX_3_GAP_OPEN_ATR_MULT * atr:
            _gap_blocked = {
                ZoneType.SIGMA2_FADE_BUY, ZoneType.SIGMA2_FADE_SELL,
                ZoneType.EMA_PULLBACK_BUY, ZoneType.EMA_PULLBACK_SELL,
            }
            _before_gap = len(in_zone)
            in_zone = [_z for _z in in_zone if _z.zone_type not in _gap_blocked]
            _after_gap = len(in_zone)
            if _before_gap != _after_gap:
                logger.info(
                    "[Fix 3a] gap_open suprimiu %d zona(s) — %s gap=%.2f atr=%.2f "
                    "(rth_open=%.2f d1_poc=%.2f)",
                    _before_gap - _after_gap, symbol,
                    _gap_size, atr, _rth_open_price_ctx, d1_poc,
                )

    # ── Fix 3b: snapshots_in_regime — regime não confirmado suprime fades ─────
    # Primeiras FIX_3_SNAPSHOTS_MIN avaliações num regime novo → incerteza alta.
    # Fades (reversão) têm maior risco em regime recentemente mudado.
    if (FIX_3_SNAPSHOTS_ENABLED
            and snapshots_in_regime < FIX_3_SNAPSHOTS_MIN):
        # EMA_PULLBACK excluído: é zona de continuação de tendência, não de reversão.
        # Suprimir EMA_PULLBACK em regime recente seria excessivamente conservador —
        # pullbacks cedo num novo regime podem ser as melhores entradas.
        _snap_blocked = {
            ZoneType.SIGMA2_FADE_BUY, ZoneType.SIGMA2_FADE_SELL,
            ZoneType.VWAP_PULLBACK_BUY,    ZoneType.VWAP_PULLBACK_SELL,
        }
        _before_snap = len(in_zone)
        in_zone = [_z for _z in in_zone if _z.zone_type not in _snap_blocked]
        _after_snap = len(in_zone)
        if _before_snap != _after_snap:
            logger.info(
                "[Fix 3b] snapshots_in_regime=%d < %d — regime=%s "
                "suprimiu %d fade zona(s) em %s",
                snapshots_in_regime, FIX_3_SNAPSHOTS_MIN,
                regime.value, _before_snap - _after_snap, symbol,
            )

    # ── Fix 3c: range_consumed v2 — range hoje vs range D1 suprime fades ────────
    # (HOD−LOD) / (D1_HIGH−D1_LOW) > 1.5 → range de hoje excedeu 1.5× o range típico diário.
    # Normalização por D1 (range RTH do dia anterior) em vez de ATR_M1:
    #   ATR_M1 e range diário são escalas incompatíveis; D1 como denominador é equivalente.
    # Exemplo: hoje 284/394=0.72× → abaixo de 1.5 → fades permitidas.
    #          dia excepcional 700/394=1.78× → acima de 1.5 → fades suprimidas.
    _d1_range = (d1_high - d1_low) if (d1_high > 0 and d1_low > 0 and d1_high > d1_low) else 0.0
    if (FIX_3_RANGE_CONSUMED_ENABLED
            and session_hod > 0 and session_lod > 0
            and session_hod > session_lod
            and _d1_range > 0):
        _range_ratio = (session_hod - session_lod) / _d1_range
        if _range_ratio > FIX_3_RANGE_CONSUMED_THRESH:
            _range_blocked = {
                ZoneType.SIGMA2_FADE_BUY,   ZoneType.SIGMA2_FADE_SELL,
                ZoneType.EMA_PULLBACK_BUY,  ZoneType.EMA_PULLBACK_SELL,
                ZoneType.VWAP_PULLBACK_BUY,     ZoneType.VWAP_PULLBACK_SELL,
            }
            _before_range = len(in_zone)
            in_zone = [_z for _z in in_zone if _z.zone_type not in _range_blocked]
            _after_range = len(in_zone)
            if _before_range != _after_range:
                logger.info(
                    "[Fix 3c] range_consumed_d1=%.2f > %.1f — suprimiu %d fade zona(s) em %s "
                    "(hod=%.2f lod=%.2f range_hoje=%.1f d1_range=%.1f)",
                    _range_ratio, FIX_3_RANGE_CONSUMED_THRESH,
                    _before_range - _after_range, symbol,
                    session_hod, session_lod,
                    session_hod - session_lod, _d1_range,
                )

    # ── F7: EMA Pullback Zones — injecção de zonas dinâmicas ─────────────────
    # Boosts individuais por id(zone): EMA zones têm boost de convergência próprio,
    # independente do mecanismo de confluência estática (F3-1).
    #
    # Ordem de verificação de cooldown (auditável e explícita):
    #   Gate 5 — EMA_PRICE_COOLDOWN: verificado em _build_ema_pullback_zones()
    #             bloqueia ANTES da zona ser criada; block_reason propagado abaixo
    #   Gate 6 — EMA_ZONE_TYPE_COOLDOWN: verificado no loop de evaluate_zone_entry()
    #             bloqueia DEPOIS da zona ser criada; block_reason registado no loop
    # O primeiro gate que bloqueia prevalece. Ambos são registados em ema_block_reasons.
    _ema_zone_boosts: Dict[int, float] = {}
    _ema_zones_injected: List['ZoneOfInterest'] = []
    _ema_block_reasons: List[str] = []

    if m1_bars is not None and s1_regime_value is not None:
        try:
            ema_candidates, ema_build_block = _build_ema_pullback_zones(
                bars_m1            = m1_bars,
                price              = price,
                atr                = atr,
                symbol             = symbol,
                s1_regime_value    = s1_regime_value,
                vwap               = vwap,
                vwap_std           = vwap_std,
                on_session_minutes = on_session_minutes,
            )
            if ema_build_block:
                _ema_block_reasons.append(ema_build_block)
            for ema_zone, ema_boost in ema_candidates:
                if disabled_zone_types and ema_zone.zone_type.value in set(disabled_zone_types):
                    logger.debug("[EMA] Zona %s desactivada por disabled_zone_types", ema_zone.zone_type.value)
                    continue
                in_zone.append(ema_zone)
                _ema_zones_injected.append(ema_zone)
                _ema_zone_boosts[id(ema_zone)] = ema_boost
                logger.info(
                    "[EMA] Zona injectada: %s %s EMA21=%.2f boost=%.1f prio=%d",
                    symbol, ema_zone.direction, ema_zone.level, ema_boost, ema_zone.priority,
                )
        except Exception as _ema_err:
            logger.warning("[EMA] Erro em _build_ema_pullback_zones: %s", _ema_err)

    # ── F5-2-B: Gamma SHORT_GAMMA block_reason — rastreável pelo AutoTuner ────
    # Capturado aqui (evaluate_zones) para que o resultado seja auditável no snapshot.
    # identify_zones já suprime as zonas; aqui registamos o motivo para diagnóstico.
    _gamma_block_reasons: List[str] = []
    _gamma_regime_ctx = str(ctx.get('gamma_regime', 'UNKNOWN'))
    _gamma_reliable   = bool(ctx.get('gamma_reliable', False))
    _gamma_short_suppressed = _gamma_reliable and _gamma_regime_ctx == 'SHORT_GAMMA'
    if _gamma_short_suppressed:
        _gamma_block_reasons.append("GAMMA_SHORT_GAMMA_SUPPRESSED")

    result: Dict[str, Any] = {
        "regime":          regime.value,
        "all_zones":       [
            {
                "type":      z.zone_type.value,
                "level":     z.level,
                "direction": z.direction,
                "label":     z.label,
                "distance":  round(z.distance_to(price), 2),
                "in_zone":   z.in_zone(price),
            }
            for z in (list(all_zones[:8]) + _ema_zones_injected)
        ],
        "best_zone":         None,
        "s3":                None,
        "status":            "NO_SIGNAL",
        "quality":           "NO_TRADE",
        "block_reasons":     list(_gamma_block_reasons),
        "ema_block_reasons": list(_ema_block_reasons),  # F7: EMA-specific cooldown reasons; updated below
        "gamma_block_reasons": list(_gamma_block_reasons),  # F5-2-B: SHORT_GAMMA suppression tracking
        "score_breakdown":      None,
        "fix_a_shadow_zones":   _fix_a_shadow,       # Fix A: SIGMA2_FADE bloqueadas em RTH_OPEN
        "price_vs_rth_open":    _price_vs_rth_open,  # Item 5: "ABOVE"|"BELOW"|"NEUTRAL"
        "regime_bias":          _regime_bias,         # Item 5: "BULLISH"|"BEARISH"|"NEUTRAL"
        "regime_cvd_conf":      _regime_cvd_conf,     # Item 6: "CONFIRMED"|"CONTESTED"|"NEUTRAL"
        # Parâmetros activos no momento da avaliação — auditoria parametros×outcomes
        "active_params": {
            "base_flow_gate":       BASE_FLOW_GATE,
            "score_moderate":       float(_zone_session_params.get(
                                        f"zones_score_moderate_thresh_{symbol.lower()}" if symbol else "",
                                        _zone_session_params.get("zones_score_moderate_thresh", 2.5))),
            "score_strong":         float(_zone_session_params.get(
                                        f"zones_score_strong_thresh_{symbol.lower()}" if symbol else "",
                                        _zone_session_params.get("zones_score_strong_thresh", 4.0))),
            "ofi_slow_fade":        float(_zone_session_params.get(
                                        f"zones_ofi_slow_fade_thresh_{symbol.lower()}" if symbol else "",
                                        _zone_session_params.get("zones_ofi_slow_fade_thresh", OFI_SLOW_BLOCK_FADE))),
            "d30_threshold":        20.0,
        },
        "macro_context_applied": {
            "ts_ratio":                  ts_ratio,
            "ts_state":                  ctx.get('ts_state', 'UNKNOWN'),
            "ts_hard_stop":              False,
            "ts_fade_suppressed":        ts_ratio >= 1.05,
            "gamma_reliable":            _gamma_reliable,
            "gamma_call_wall":           ctx.get('gamma_call_wall', 0.0),
            "gamma_short_suppressed":    _gamma_short_suppressed,
            "gamma_put_wall":            ctx.get('gamma_put_wall', 0.0),
            "zero_gamma":                ctx.get('zero_gamma', 0.0),
            "gamma_regime":              ctx.get('gamma_regime', 'UNKNOWN'),
            "gamma_sentiment":           ctx.get('gamma_sentiment', 'NEUTRAL'),
        },
    }

    if not in_zone:
        result["status"] = "NO_ZONE"
        result["block_reasons"] = list(_gamma_block_reasons) + ["Preço fora de todas as zonas de interesse"]
        return result

    best_zone      = None
    best_score_num = -1.0
    best_passed    = False
    best_quality   = "NO_TRADE"
    best_risk_mod  = 1.0
    best_reasons: List[str] = []
    best_s3: Optional[Dict] = None
    best_breakdown: Optional[ScoreBreakdown] = None

    score_map = {"STRONG": 3, "MODERATE": 2, "WEAK": 1, "BLOCKED": -1, "NO_TRADE": 0}

    # ── F3-1: Mapa de confluência — zonas in_zone mesma direção dentro de CONFLUENCE_WINDOW_ATR ──
    confluence_ids: set = set()
    for _i, _za in enumerate(in_zone):
        for _zb in in_zone[_i + 1:]:
            if (_za.direction == _zb.direction
                    and abs(_za.level - _zb.level) <= CONFLUENCE_WINDOW_ATR * atr):
                confluence_ids.add(id(_za))
                confluence_ids.add(id(_zb))
                logger.info(
                    f"[ZONES] F3-1 Confluência: {symbol} "
                    f"{_za.zone_type.value} @ {_za.level:.2f} "
                    f"+ {_zb.zone_type.value} @ {_zb.level:.2f} "
                    f"→ boost +{CONFLUENCE_BOOST_PTS}pts"
                )

    for zone in sorted(in_zone, key=lambda z: z.priority):
        # ── Fase 2-2: Cooldown — ignora zonas recentemente disparadas ─────────
        if _is_zone_in_cooldown(symbol, zone.zone_type.value):
            remaining = ZONE_COOLDOWN_SECS - (time.monotonic() - _zone_cooldown.get((symbol, zone.zone_type.value), 0))
            logger.debug(
                f"[ZONES] Cooldown ativo: {symbol} {zone.zone_type.value} (restam {remaining:.0f}s)"
            )
            # ── F7: registar Gate 6 para zonas EMA (distingue de EMA_PRICE_COOLDOWN) ──
            if zone.zone_type in (ZoneType.EMA_PULLBACK_BUY, ZoneType.EMA_PULLBACK_SELL):
                reason = (
                    f"EMA_ZONE_TYPE_COOLDOWN ({zone.zone_type.value} restam {remaining:.0f}s)"
                )
                _ema_block_reasons.append(reason)
                result["ema_block_reasons"] = list(_ema_block_reasons)
            continue

        # F7: EMA zones usam boost de convergência próprio; zonas estáticas usam confluência F3-1
        if id(zone) in _ema_zone_boosts:
            boost = _ema_zone_boosts[id(zone)]
        else:
            boost = CONFLUENCE_BOOST_PTS if id(zone) in confluence_ids else 0.0

        passed, quality, risk_mod, reasons, breakdown = evaluate_zone_entry(
            zone, live_data, delta_ratio, atr,
            confluence_boost=boost,
            macro_context=macro_context,
            on_session_minutes=on_session_minutes,
            session_params=_zone_session_params,
            symbol=symbol,
            regime_bias=_regime_bias,
        )

        # ── Gate per-zona: score mínimo (zone_min_score_overrides) ──────────────
        _zt_val = zone.zone_type.value
        _zms = (zone_min_score_overrides or {}).get(_zt_val)
        if passed and _zms is not None and breakdown.total_score < _zms:
            passed  = False
            quality = "BLOCKED"
            reasons = reasons + [
                f"zone_min_score: {breakdown.total_score:.2f} < {_zms:.1f} "
                f"(threshold per-zona {_zt_val})"
            ]
            logger.debug(
                "[ZONES] %s %s bloqueada por zone_min_score: %.2f < %.1f",
                symbol, _zt_val, breakdown.total_score, _zms,
            )

        # ── Gate per-zona: confluence mínimo (zone_min_confluence_overrides) ────
        _zmc = (zone_min_confluence_overrides or {}).get(_zt_val)
        if passed and _zmc is not None:
            _eff_conf = breakdown.confluence_boost if breakdown else 0.0
            _sp_str   = float(_zone_session_params.get(
                f"zones_score_strong_thresh_{symbol.lower()}",
                _zone_session_params.get("zones_score_strong_thresh", 4.0)
            ))
            if _eff_conf < _zmc and breakdown.total_score < _sp_str:
                passed  = False
                quality = "BLOCKED"
                reasons = reasons + [
                    f"zone_min_confluence: conf={_eff_conf:.2f} < {_zmc:.2f} "
                    f"e score={breakdown.total_score:.2f} < STRONG={_sp_str:.1f} "
                    f"(threshold per-zona {_zt_val})"
                ]
                logger.debug(
                    "[ZONES] %s %s bloqueada por zone_min_confluence: "
                    "conf=%.2f < %.2f (score=%.2f < STRONG=%.1f)",
                    symbol, _zt_val, _eff_conf, _zmc,
                    breakdown.total_score, _sp_str,
                )

        score_num = score_map.get(quality, 0) - zone.priority * 0.1

        if passed and score_num > best_score_num:
            best_score_num = score_num
            best_zone      = zone
            best_passed    = passed
            best_quality   = quality
            best_risk_mod  = risk_mod
            best_reasons   = reasons
            best_breakdown = breakdown
            best_s3 = compute_zone_s3(zone, regime, price, atr, risk_mod, symbol, quantity)

            # ── HF2-B: Early-exit — STRONG em zona prioritária encerra busca ──
            if quality == "STRONG" and zone.priority <= 2:
                logger.debug(
                    f"[ZONES] Early-exit: {symbol} {zone.zone_type.value} STRONG "
                    f"(priority={zone.priority})"
                )
                break

        elif not best_passed and quality not in ("BLOCKED",):
            if best_zone is None or score_num > best_score_num:
                best_score_num = score_num
                best_zone      = zone
                best_quality   = quality
                best_reasons   = reasons
                best_breakdown = breakdown

    if best_zone:
        _bz_type = best_zone.zone_type.value if best_zone.zone_type else None
        result["best_zone"] = {
            "type":         _bz_type,
            "level":        best_zone.level,
            "direction":    best_zone.direction,
            "label":        best_zone.label,
            "target":       best_zone.target,
            "target_label": best_zone.target_label,
            "priority":     best_zone.priority,
            "category":     "fade" if best_zone.zone_type in _FADE_ZONE_TYPES else "momentum",
        }
        # Campo de conveniência no top-level — evita .get("best_zone", {}).get("type")
        result["zone_type"] = _bz_type
        if best_breakdown:
            result["score_breakdown"] = best_breakdown.to_dict()

    if best_passed and best_s3:
        result["s3"]      = best_s3
        result["status"]  = "ACTIVE_SIGNAL"
        result["quality"] = best_quality

        # ── Fase 2-2: Registra cooldown para sinais válidos (score >= MODERATE) ──
        if best_quality in ("STRONG", "MODERATE"):
            _register_zone_cooldown(symbol, best_zone.zone_type.value)
            logger.info(
                f"[ZONES] Cooldown registrado: {symbol} {best_zone.zone_type.value} "
                f"por {ZONE_COOLDOWN_SECS:.0f}s"
            )
            # ── F7: EMA zones registam cooldown por coordenada de preço ────────
            if best_zone.zone_type in (ZoneType.EMA_PULLBACK_BUY, ZoneType.EMA_PULLBACK_SELL):
                _ema_dir = "LONG" if best_zone.zone_type == ZoneType.EMA_PULLBACK_BUY else "SHORT"
                _register_ema_cooldown(symbol, _ema_dir, best_zone.level)
                logger.info(
                    "[EMA] Cooldown de preço registado: %s %s EMA21=%.2f por %.0fs",
                    symbol, _ema_dir, best_zone.level, EMA_COOLDOWN_SECS,
                )
    else:
        result["status"]        = "BLOCKED" if best_quality == "BLOCKED" else "NO_SIGNAL"
        result["quality"]       = best_quality
        result["block_reasons"] = list(_gamma_block_reasons) + best_reasons

    return result
