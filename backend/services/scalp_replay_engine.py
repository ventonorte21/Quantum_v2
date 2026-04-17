"""
Scalp Replay Engine — Walk-Forward Backtest para Scalp Snapshots
================================================================
Consome scalp_snapshots do MongoDB e simula trades com parâmetros
configuráveis para os 2 modos: ZONES e FLOW.

Diferenças do Base Replay Engine:
- Snap interval: 30s (não 60s)
- Tick-based SL/TP (não ATR multipliers globais)
- 2 modos de sinal (ZONES/FLOW)
- Filtros de Gamma Regime e Term Structure integrados
- Re-avaliação de scores ZONES via score_breakdown salvo no snap
"""

import math
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("scalp_replay")

# ── Tick values ───────────────────────────────────────────────────────────────
TICK_VALUES = {
    "MNQ": {"tick_size": 0.25, "point_value": 2.0},
    "MES": {"tick_size": 0.25, "point_value": 5.0},
}

# ── Snapshots para simular hold de até 3 min (30s × 6 = 3 min) ──────────────
MAX_HOLD_SNAPS = 6   # 3 minutos (modo snapshot)

# ── DataBento M1 candle mode: hold de até 10 minutos ─────────────────────────
MAX_HOLD_MINS_CANDLE = 10  # 10 M1 candles

# ── Configuração padrão ───────────────────────────────────────────────────────
SCALP_DEFAULT_CONFIG: Dict[str, Any] = {
    # ── Scope ──────────────────────────────────────────────────────────────────
    "symbol": "MNQ",
    "mode_filter": None,          # None = todos os modos | "FLOW" | "CANDLE" | "ZONES"
    "start_date": None,
    "end_date": None,
    "session_filter": None,       # None = todas as sessões | "OVERNIGHT" | "RTH_OPEN" | "RTH_MID" | "RTH_CLOSE"

    # ── Modo de simulação de outcome ──────────────────────────────────────────
    # False (padrão): usa snapshots subsequentes a 30s (resolução grosseira)
    # True: busca candles M1 reais do DataBento (High/Low) — muito mais preciso
    "use_databento_candles": False,

    # ── Qualidade mínima para entrada ─────────────────────────────────────────
    # Aceita apenas sinais com quality >= min_quality
    # ZONES: determina score_threshold | FLOW: determina OFI tier | CANDLE: determina body tier
    "min_quality": "MODERATE",   # STRONG | MODERATE | WEAK

    # ── ZONES mode — parâmetros base (partilhados) ───────────────────────────
    "zones": {
        "score_strong_thresh":    4.0,   # total_score >= → STRONG
        "score_moderate_thresh":  2.5,   # total_score >= → MODERATE
        "ofi_slow_penalty":       1.5,   # pontos deduzidos quando OFI slow contra
        "confluence_boost":       1.5,   # bonus quando ≥ 2 zonas sobrepostas alinhadas
        # OFI slow — thresholds de ativação da penalidade por categoria de zona
        # Fade zones: mais tolerantes (slow naturalmente contra em reversões)
        # Momentum/pullback zones: mais exigentes (slow deve confirmar)
        "ofi_slow_fade_thresh":       0.55,
        "ofi_slow_momentum_thresh":   0.35,
        # Gamma filter: BOTH | LONG_GAMMA | SHORT_GAMMA
        "gamma_regime_filter":    "BOTH",
        # TS filters (True = pula trade quando flag ativa)
        "ts_hard_stop_skip":      True,
        "ts_fade_suppressed_skip_fades": True,
    },

    # ── ZONES mode — overrides por símbolo (herdam de "zones" para chaves ausentes)
    # Otimizar separadamente: MNQ tende a trends limpos; MES tem mais noise.
    "zones_mnq": {
        # Inicialmente vazio — herda tudo de "zones" base
        # O otimizador preenche: score_strong_thresh, score_moderate_thresh,
        # ofi_slow_penalty, ofi_slow_fade_thresh, ofi_slow_momentum_thresh
    },
    "zones_mes": {
        # Inicialmente vazio — herda tudo de "zones" base
        # MES historicamente requer thresholds mais altos para reduzir ruído
    },

    # ── FLOW mode — parâmetros ────────────────────────────────────────────────
    "flow": {
        "ofi_fast_strong":    0.35,   # OFI abs >= → regime STRONG
        "ofi_fast_moderate":  0.18,   # OFI abs >= → regime MODERATE
        "s2_ofi_fast_min":    0.20,   # S2: OFI fast mínimo
        "s2_ofi_slow_min":    0.08,   # S2: OFI slow mínimo (mesma direção)
        "s2_delta_ratio_min": 0.06,   # S2: delta ratio mínimo
        "s1_confidence_min":  4.5,    # S1: confiança mínima para S2
        "absorption_ofi_min": 0.55,   # OFI abs mínimo para activar regime de absorção
    },

    # ── Risk — tick-based (ZONES/FLOW) ────────────────────────────────────────
    "risk": {
        "sl_ticks_mnq":   6,
        "tp_ticks_mnq":   10,
        "sl_ticks_mes":   4,
        "tp_ticks_mes":   8,
        "slippage_ticks": 1,
        "commission":     2.50,    # round-trip por contrato
        "initial_capital": 25000.0,
        "max_daily_loss_pct": 5.0,
        "max_consecutive_losses": 3,
        "contracts": 1,
    },
}


_QUALITY_INT_MAP = {0: "WEAK", 1: "MODERATE", 2: "STRONG"}


def merge_scalp_config(user: Dict) -> Dict:
    """Deep-merge config do utilizador sobre defaults.

    Converte automaticamente `min_quality_int` (int 0-2) para
    a string `min_quality` usada internamente pelo replay engine.
    """
    u = dict(user)
    # Converte min_quality_int → min_quality (string)
    if "min_quality_int" in u:
        q_int = int(u.pop("min_quality_int"))
        u["min_quality"] = _QUALITY_INT_MAP.get(q_int, "MODERATE")

    cfg = {}
    for k, default_val in SCALP_DEFAULT_CONFIG.items():
        if k in u:
            if isinstance(default_val, dict) and isinstance(u[k], dict):
                cfg[k] = {**default_val, **u[k]}
            else:
                cfg[k] = u[k]
        else:
            cfg[k] = default_val
    return cfg


# ── Re-avaliadores de sinal ───────────────────────────────────────────────────

def _reevaluate_zones(snap: Dict, cfg: Dict) -> Tuple[Optional[str], str]:
    """Re-avalia sinal ZONES com thresholds configuráveis.

    Extrai dados do score_breakdown gravado no snapshot e aplica
    novos thresholds de score_strong/moderate, ofi_slow_penalty,
    confluence_boost, gamma_filter e ts_filter.

    Componentes configuráveis (re-aplicados com novos valores):
      - ofi_slow_penalty, confluence_boost

    Componentes congelados (re-somados do breakdown gravado):
      - tape_speed_modifier, late_session_penalty, gamma_modifier
      Estes não fazem parte do base_score — devem ser somados de volta
      para que o total re-calculado corresponda ao total do motor real.

    Suporta overrides por símbolo via zones_mnq / zones_mes no config.
    Cada override herda chaves ausentes do dict zones base.
    """
    # Merge: base zones + symbol-specific overrides (symbol-specific wins)
    sym = (snap.get("symbol") or "MNQ").upper()
    _sym_key = f"zones_{sym.lower()}"
    _base_zcfg = cfg["zones"]
    _sym_overrides = cfg.get(_sym_key) or {}
    zcfg = {**_base_zcfg, **_sym_overrides}

    # ── Gamma regime filter ────────────────────────────────────────────────────
    mc = snap.get("macro_context", {})
    gamma_regime = mc.get("gamma_regime", "UNKNOWN")
    gf = zcfg.get("gamma_regime_filter", "BOTH")
    if gf in ("LONG_GAMMA", "SHORT_GAMMA") and gamma_regime != gf:
        return None, f"[ZONES] gamma_filter={gf} bloqueou gamma_regime={gamma_regime}"

    # ── Term Structure — hard stop ─────────────────────────────────────────────
    if zcfg.get("ts_hard_stop_skip") and mc.get("ts_hard_stop"):
        return None, "[ZONES] ts_hard_stop ativo — pula entrada"

    # ── Term Structure — fade suppression ─────────────────────────────────────
    # Quando ts_fade_suppressed=True (TS ratio >= 1.05), sinais de zonas "fade"
    # são suprimidos. A categoria da zona activa determina se é fade.
    if zcfg.get("ts_fade_suppressed_skip_fades") and mc.get("ts_fade_suppressed"):
        active_zone = snap.get("zones", {}).get("active_zone") or {}
        zone_category = (active_zone.get("category") or "").lower()
        if zone_category == "fade":
            return None, "[ZONES] ts_fade_suppressed — zona fade suprimida (TS >= 1.05)"

    # ── Score re-evaluation ────────────────────────────────────────────────────
    sb = snap.get("zones", {}).get("score_breakdown")
    if not sb:
        # Sem score_breakdown — usa quality gravada como proxy
        quality = snap.get("zone_quality") or snap.get("s2_quality")
        if quality == "STRONG":
            total_score = zcfg["score_strong_thresh"]
        elif quality == "MODERATE":
            total_score = zcfg["score_moderate_thresh"]
        else:
            return None, "[ZONES] sem score_breakdown e quality WEAK/NO_TRADE"
    else:
        base_score = float(sb.get("base_score", 0.0))

        # ── OFI slow — re-avaliação a partir de dados brutos (permite testar novos thresholds) ──
        # Extrai ofi_slow raw dos indicadores para poder re-determinar se a penalidade se aplica
        # com thresholds diferentes dos originais (parâmetros do espaço de busca do optimizador).
        ind_raw      = snap.get("indicators", {})
        ofi_slow_raw_val = ind_raw.get("ofi_slow")

        # Categoria da zona activa determina qual threshold usar
        zones_raw       = snap.get("zones", {})
        active_zone_raw = zones_raw.get("active_zone") or {}
        zone_cat        = (active_zone_raw.get("category") or "").lower()
        is_fade_zone    = zone_cat == "fade"

        slow_thresh = (
            float(zcfg.get("ofi_slow_fade_thresh",     0.55)) if is_fade_zone
            else float(zcfg.get("ofi_slow_momentum_thresh", 0.35))
        )

        # Re-determina aplicação da penalidade a partir de dados brutos
        # Penalidade activa quando ofi_slow é forte e contrário à direcção do trade
        s3_action_pre = snap.get("s3_action") or (snap.get("s3", {}) or {}).get("action")
        if s3_action_pre:
            s3_action_pre = s3_action_pre.upper()
        if ofi_slow_raw_val is not None and s3_action_pre in ("BUY", "SELL"):
            ofi_slow_raw_f = float(ofi_slow_raw_val)
            if s3_action_pre == "BUY":
                ofi_slow_applied = ofi_slow_raw_f <= -slow_thresh
            else:
                ofi_slow_applied = ofi_slow_raw_f >= slow_thresh
        else:
            # Fallback: usa decisão original gravada no breakdown
            ofi_slow_applied = bool(sb.get("ofi_slow_penalty", 0.0))

        confluence_applied  = bool(sb.get("confluence_boost",  0.0))

        # Componentes congelados — re-somados do breakdown gravado
        # (não fazem parte do base_score; são modificadores independentes)
        tape_speed_frozen   = float(sb.get("tape_speed_modifier",   0.0))
        late_session_frozen = float(sb.get("late_session_penalty",  0.0))
        gamma_frozen        = float(sb.get("gamma_modifier",        0.0))
        # Item 5: Fix 5 bias — separado de base_score desde ScoreBreakdown v2.
        # Snaps pré-v2 não têm este campo; fallback=0.0 (o valor estava absorvido em base_score).
        rth_bias_frozen     = float(sb.get("rth_bias_modifier",     0.0))

        total_score = (
            base_score
            - (zcfg["ofi_slow_penalty"] if ofi_slow_applied else 0.0)
            + (zcfg["confluence_boost"]  if confluence_applied else 0.0)
            + tape_speed_frozen
            + late_session_frozen
            + gamma_frozen
            + rth_bias_frozen
        )

    # Classifica qualidade com novos thresholds
    if total_score >= zcfg["score_strong_thresh"]:
        quality = "STRONG"
    elif total_score >= zcfg["score_moderate_thresh"]:
        quality = "MODERATE"
    else:
        return None, f"[ZONES] score={total_score:.2f} abaixo moderate_thresh={zcfg['score_moderate_thresh']}"

    # Verifica qualidade mínima
    min_q = cfg.get("min_quality", "MODERATE")
    q_rank = {"STRONG": 3, "MODERATE": 2, "WEAK": 1}
    if q_rank.get(quality, 0) < q_rank.get(min_q, 2):
        return None, f"[ZONES] quality={quality} < min_quality={min_q}"

    # Direcção a partir do sinal activo
    s3_action = snap.get("s3_action") or (snap.get("s3", {}) or {}).get("action")
    if s3_action:
        s3_action = s3_action.upper()
    if not s3_action or s3_action not in ("BUY", "SELL"):
        return None, "[ZONES] sem action BUY/SELL"

    return s3_action, (
        f"[ZONES] score={total_score:.2f} quality={quality} gamma={gamma_regime} "
        f"tape={tape_speed_frozen if sb else 0:.2f} late={late_session_frozen if sb else 0:.2f}"
    )


def _reevaluate_flow(snap: Dict, cfg: Dict) -> Tuple[Optional[str], str]:
    """Re-avalia sinal FLOW com OFI/delta/absorção thresholds configuráveis.

    Espelha a lógica do motor real:
      - Absorção institucional tem prioridade sobre fluxo direcional (mesma ordem do evaluate_s1_flow)
      - S2 FLOW: ofi_fast, ofi_slow, delta, anti-fade
    """
    fcfg = cfg["flow"]
    ind  = snap.get("indicators", {})

    ofi_fast        = ind.get("ofi_fast") or 0.0
    ofi_slow        = ind.get("ofi_slow") or 0.0
    delta_ratio     = ind.get("delta_ratio") or 0.0
    absorption_flag = ind.get("absorption_flag", False)
    absorption_side = ind.get("absorption_side", "NONE") or "NONE"
    abs_ofi         = abs(ofi_fast)
    min_q           = cfg.get("min_quality", "MODERATE")

    # ── S1: Absorção institucional — prioridade (mesma lógica do evaluate_s1_flow) ──
    absorption_ofi_min = fcfg.get("absorption_ofi_min", 0.55)
    if absorption_flag and abs_ofi >= absorption_ofi_min:
        confidence = min(10.0, 6.0 + abs_ofi * 6)
        if absorption_side == "SELL_ABSORBED":
            direction = "LONG"
        elif absorption_side == "BUY_ABSORBED":
            direction = "SHORT"
        else:
            direction = None

        if direction is None:
            return None, f"[FLOW/ABS] absorption_side inválido: {absorption_side}"

        if confidence < fcfg.get("s1_confidence_min", 4.5):
            return None, f"[FLOW/ABS] confidence={confidence:.1f} < min"

        # Anti-fade: OFI slow forte contra a direção da absorção bloqueia
        sign = 1 if direction == "LONG" else -1
        if ofi_slow * sign < -0.30:
            return None, f"[FLOW/ABS] anti-fade: ofi_slow={ofi_slow:.3f} contra {direction}"

        # Absorção com confidence >= 6 → sempre STRONG (não aplica min_quality=STRONG gate)
        action = "BUY" if direction == "LONG" else "SELL"
        return action, f"[FLOW/ABS] {absorption_side} ofi={ofi_fast:.3f} conf={confidence:.1f}"

    # ── S1: Fluxo direcional — thresholds configuráveis ─────────────────────────
    if abs_ofi >= fcfg["ofi_fast_strong"]:
        confidence = 7.0
    elif abs_ofi >= fcfg["ofi_fast_moderate"]:
        confidence = 5.5
    else:
        return None, f"[FLOW] ofi_fast={ofi_fast:.3f} < moderate={fcfg['ofi_fast_moderate']}"

    if confidence < fcfg["s1_confidence_min"]:
        return None, f"[FLOW] confidence={confidence} < min={fcfg['s1_confidence_min']}"

    # ── S2: filtros com thresholds configuráveis ─────────────────────────────────
    direction    = "LONG" if ofi_fast > 0 else "SHORT"
    sign         = 1 if direction == "LONG" else -1
    ofi_same_dir = (direction == "LONG" and ofi_slow > 0) or (direction == "SHORT" and ofi_slow < 0)
    ofi_slow_ok  = abs(ofi_slow) >= fcfg["s2_ofi_slow_min"] and ofi_same_dir
    ofi_fast_ok  = abs(ofi_fast) >= fcfg["s2_ofi_fast_min"]
    delta_ok     = abs(delta_ratio) >= fcfg["s2_delta_ratio_min"]

    # Anti-fade: OFI slow forte contra direção → bloqueia
    if ofi_slow * sign < -0.30:
        return None, f"[FLOW] anti-fade: ofi_slow={ofi_slow:.3f} contra {direction}"

    passed_count = sum([ofi_fast_ok, ofi_slow_ok, delta_ok])
    if passed_count < 2:
        return None, f"[FLOW] S2: ofi_fast={ofi_fast_ok} ofi_slow={ofi_slow_ok} delta={delta_ok}"

    quality = "STRONG" if passed_count == 3 else "MODERATE"
    if quality == "MODERATE" and min_q == "STRONG":
        return None, "[FLOW] quality=MODERATE < min_quality=STRONG"

    action = "BUY" if direction == "LONG" else "SELL"
    return action, f"[FLOW] ofi={ofi_fast:.3f} quality={quality}"


def reevaluate_scalp_signal(snap: Dict, cfg: Dict) -> Tuple[Optional[str], str]:
    """Ponto de entrada central: despacha re-avaliação baseada no modo do snap."""
    mode = snap.get("mode", "FLOW")
    if mode == "ZONES":
        return _reevaluate_zones(snap, cfg)
    else:
        return _reevaluate_flow(snap, cfg)


# ── Helpers de metadados por snapshot ────────────────────────────────────────

def _snap_meta(snap: Dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extrai (zone_type, session_label, quality) de um snapshot para breakdown."""
    zones      = snap.get("zones") or {}
    active_zone = zones.get("active_zone") or {}
    s3_extra    = zones.get("s3_extra") or {}
    zone_type   = (active_zone.get("type")
                   or s3_extra.get("zone_type")
                   or None)
    session_label = snap.get("session_label")
    quality = snap.get("s2_quality") or snap.get("zone_quality")
    return zone_type, session_label, quality


def _breakdown(trades: List[Dict], key: str) -> Dict[str, Dict]:
    """Gera breakdown por campo: {valor: {trades, wins, win_rate, total_pnl}}."""
    buckets: Dict[str, Dict] = {}
    for t in trades:
        v = t.get(key) or "UNKNOWN"
        if v not in buckets:
            buckets[v] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        b = buckets[v]
        b["trades"] += 1
        b["total_pnl"] = round(b["total_pnl"] + t.get("pnl", 0.0), 2)
        if t.get("pnl", 0.0) > 0:
            b["wins"] += 1
    for b in buckets.values():
        b["win_rate"] = round(b["wins"] / b["trades"], 4) if b["trades"] else 0.0
    return buckets


# ── Simulação de posição ──────────────────────────────────────────────────────

class ScalpVirtualPosition:
    def __init__(self, action: str, entry_price: float, sl_price: float,
                 tp_price: float, contracts: int, commission: float,
                 point_value: float, snap_idx: int,
                 zone_type: Optional[str] = None,
                 session_label: Optional[str] = None,
                 quality: Optional[str] = None):
        self.action       = action
        self.entry_price  = entry_price
        self.sl_price     = sl_price
        self.tp_price     = tp_price
        self.contracts    = contracts
        self.commission   = commission
        self.point_value  = point_value
        self.snap_idx     = snap_idx
        self.zone_type    = zone_type
        self.session_label = session_label
        self.quality      = quality
        self.exit_price:  Optional[float] = None
        self.exit_reason: str = "open"
        self.pnl_usd:     float = 0.0

    def check_exit(self, price: float, snap_offset: int) -> bool:
        """Verifica se TP ou SL foi atingido no preço `price`."""
        if self.action == "BUY":
            if price >= self.tp_price:
                self._close(self.tp_price, "TP")
                return True
            elif price <= self.sl_price:
                self._close(self.sl_price, "SL")
                return True
        else:  # SELL
            if price <= self.tp_price:
                self._close(self.tp_price, "TP")
                return True
            elif price >= self.sl_price:
                self._close(self.sl_price, "SL")
                return True

        # Max hold atingido
        if snap_offset >= MAX_HOLD_SNAPS:
            self._close(price, "MAX_HOLD")
            return True
        return False

    def _close(self, price: float, reason: str):
        self.exit_price  = price
        self.exit_reason = reason
        pts = (price - self.entry_price) if self.action == "BUY" else (self.entry_price - price)
        gross = pts * self.point_value * self.contracts
        self.pnl_usd = gross - self.commission * self.contracts


def _compute_sl_tp(action: str, entry: float, cfg: Dict, mode: str, snap: Dict, symbol: str) -> Tuple[float, float]:
    """Calcula SL e TP baseado no modo e configuração."""
    risk = cfg["risk"]
    tick_size = TICK_VALUES.get(symbol, {}).get("tick_size", 0.25)

    # ZONES e FLOW: tick-based
    sl_key = f"sl_ticks_{'mnq' if symbol == 'MNQ' else 'mes'}"
    tp_key = f"tp_ticks_{'mnq' if symbol == 'MNQ' else 'mes'}"
    sl_pts = risk.get(sl_key, 6) * tick_size
    tp_pts = risk.get(tp_key, 10) * tick_size

    if action == "BUY":
        return entry - sl_pts, entry + tp_pts
    else:
        return entry + sl_pts, entry - tp_pts


# ── Motor de replay ───────────────────────────────────────────────────────────

def _compute_metrics(trades: List[Dict], equity_curve: List[float]) -> Dict[str, Any]:
    """Calcula métricas estatísticas a partir da lista de trades."""
    n = len(trades)
    if n == 0:
        return {
            "total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0,
            "profit_factor": 0.0, "sharpe_ratio": 0.0, "sortino_ratio": 0.0,
            "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
            "expectancy": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "return_pct": 0.0, "by_mode": {}, "by_exit": {},
        }

    wins  = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]

    win_rate = len(wins) / n
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (10.0 if gross_profit > 0 else 0.0)

    pnls = [t["pnl"] for t in trades]
    avg_pnl  = sum(pnls) / n
    std_pnl  = math.sqrt(sum((p - avg_pnl)**2 for p in pnls) / n) if n > 1 else 0.0
    sharpe   = (avg_pnl / std_pnl * math.sqrt(252 * 2)) if std_pnl > 0 else 0.0  # 2 signals/session/symbol

    downside = [p for p in pnls if p < 0]
    std_down = math.sqrt(sum(p**2 for p in downside) / n) if downside else 0.0
    sortino  = (avg_pnl / std_down * math.sqrt(252 * 2)) if std_down > 0 else sharpe

    # Max drawdown
    peak  = equity_curve[0] if equity_curve else 0.0
    mdd   = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq)
        if dd > mdd:
            mdd = dd
    mdd_pct = (mdd / equity_curve[0] * 100) if equity_curve and equity_curve[0] > 0 else 0.0

    total_pnl = sum(pnls)
    expectancy = avg_pnl

    # Calmar Ratio = Retorno Anualizado / Max Drawdown
    # Usa o mesmo factor de anualização que o Sharpe (252 sessões × 2 sinais por sessão)
    annualized_return = avg_pnl * 252 * 2
    if mdd > 0:
        calmar = annualized_return / mdd
    elif avg_pnl > 0:
        calmar = 10.0   # sem drawdown e rentável → valor alto máximo
    else:
        calmar = 0.0

    by_mode: Dict[str, Dict] = {}
    for t in trades:
        m = t.get("mode", "UNKNOWN")
        if m not in by_mode:
            by_mode[m] = {"n": 0, "pnl": 0.0, "wins": 0}
        by_mode[m]["n"]   += 1
        by_mode[m]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            by_mode[m]["wins"] += 1

    by_exit: Dict[str, int] = {}
    for t in trades:
        e = t.get("exit_reason", "?")
        by_exit[e] = by_exit.get(e, 0) + 1

    return {
        "total_trades":    n,
        "win_rate":        round(win_rate, 4),
        "total_pnl":       round(total_pnl, 2),
        "profit_factor":   round(pf, 4),
        "sharpe_ratio":    round(sharpe, 4),
        "sortino_ratio":   round(sortino, 4),
        "calmar_ratio":    round(calmar, 4),
        "max_drawdown":    round(mdd, 2),
        "max_drawdown_pct": round(mdd_pct, 2),
        "expectancy":      round(expectancy, 2),
        "avg_win":         round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss":        round(sum(losses) / len(losses), 2) if losses else 0.0,
        "return_pct":      round(total_pnl / equity_curve[0] * 100, 2) if equity_curve and equity_curve[0] > 0 else 0.0,
        "by_mode":         by_mode,
        "by_exit":         by_exit,
    }


# ── DataBento M1 candle fetch ─────────────────────────────────────────────────

async def _fetch_m1_candles(symbol: str, date_set: set) -> Dict[str, List[Dict]]:
    """
    Busca candles M1 do DataBento para cada data em date_set.
    Retorna dict: {date_str: [{ts_dt, open, high, low, close}, ...]}

    Usa a chave DATABENTO_KEY do ambiente. Se não disponível, retorna {}.
    Os preços são normalizados para float (divide por 1e9 se raw > 1e6).
    """
    api_key = os.environ.get("DATABENTO_KEY", "")
    if not api_key:
        logger.warning("REPLAY M1: DATABENTO_KEY não definido — usando modo snapshot")
        return {}

    try:
        import databento as db
    except ImportError:
        logger.warning("REPLAY M1: databento não instalado — usando modo snapshot")
        return {}

    import asyncio

    sym_continuous = f"{symbol}.v.0"
    result: Dict[str, List[Dict]] = {}

    def _price(raw) -> float:
        v = float(raw or 0)
        return v / 1e9 if v > 1e6 else v

    for date_str in sorted(date_set):
        try:
            start_dt = datetime.fromisoformat(f"{date_str}T00:00:00+00:00")
            end_dt   = start_dt + timedelta(days=1)

            client = db.Historical(key=api_key)
            kwargs = dict(
                dataset="GLBX.MDP3",
                symbols=sym_continuous,
                schema="ohlcv-1m",
                start=start_dt.isoformat(),
                end=end_dt.isoformat(),
                stype_in="continuous",
            )

            def _sync_fetch():
                data = client.timeseries.get_range(**kwargs)
                candles = []
                for rec in data:
                    ts_ns = getattr(rec, "ts_event", 0)
                    ts_dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
                    candles.append({
                        "ts_dt": ts_dt,
                        "open":  _price(getattr(rec, "open",  0)),
                        "high":  _price(getattr(rec, "high",  0)),
                        "low":   _price(getattr(rec, "low",   0)),
                        "close": _price(getattr(rec, "close", 0)),
                    })
                return candles

            loop = asyncio.get_event_loop()
            candles = await loop.run_in_executor(None, _sync_fetch)
            result[date_str] = candles
            logger.info("REPLAY M1: %s %s → %d candles", symbol, date_str, len(candles))

        except Exception as e:
            logger.warning("REPLAY M1: falha ao buscar candles %s %s: %s", symbol, date_str, e)
            result[date_str] = []

    return result


def _outcome_from_candles(
    action: str,
    entry: float,
    sl_price: float,
    tp_price: float,
    candles: List[Dict],
    entry_ts: datetime,
    max_hold_mins: int = MAX_HOLD_MINS_CANDLE,
) -> Tuple[float, str, Optional[datetime]]:
    """
    Simula outcome de um trade usando candles M1 reais do DataBento.

    Lógica:
    - Usa candles com ts_dt >= entry_ts (ignora candle de entrada parcial)
    - Para BUY : TP hit se high >= tp_price; SL hit se low <= sl_price
    - Para SELL: TP hit se low <= tp_price; SL hit se high >= sl_price
    - Tie-breaking conservador: se ambos na mesma candle → SL vence sempre
    - MAX_HOLD: se nenhum hit em max_hold_mins → fecha no close do último candle

    Retorna (exit_price, exit_reason, exit_ts)
    """
    cutoff = entry_ts + timedelta(minutes=max_hold_mins)
    relevant = [c for c in candles if c["ts_dt"] >= entry_ts and c["ts_dt"] < cutoff]

    for candle in relevant:
        h = candle["high"]
        l = candle["low"]
        c = candle["close"]
        ts = candle["ts_dt"]

        if action == "BUY":
            sl_hit = l <= sl_price
            tp_hit = h >= tp_price
        else:
            sl_hit = h >= sl_price
            tp_hit = l <= tp_price

        if sl_hit and tp_hit:
            return sl_price, "SL", ts   # conservador: SL sempre vence no tie
        if tp_hit:
            return tp_price, "TP", ts
        if sl_hit:
            return sl_price, "SL", ts

    # MAX_HOLD: fecha no close do último candle relevante
    if relevant:
        last = relevant[-1]
        return last["close"], "MAX_HOLD", last["ts_dt"]

    # Sem candles (gap de dados): MAX_HOLD no entry price
    return entry, "MAX_HOLD", cutoff


async def run_scalp_replay(database, config: Dict) -> Dict[str, Any]:
    """
    Executa replay walk-forward em scalp_snapshots com a configuração fornecida.

    Fluxo:
    1. Carrega snapshots do MongoDB (filtros de símbolo, datas, modo)
    2. Para cada snapshot: re-avalia sinal com novos thresholds
    3. Se sinal → abre posição virtual e fecha com os próximos snapshots
       (modo padrão: snapshots subsequentes a 30s)
       (modo M1: candles DataBento reais — ativar com use_databento_candles=True)
    4. Calcula métricas: win_rate, PnL, Sharpe, Sortino, etc.
    """
    _t0    = time.perf_counter()
    cfg    = merge_scalp_config(config)
    symbol = cfg["symbol"].upper()
    risk   = cfg["risk"]

    tick_info   = TICK_VALUES.get(symbol, {"tick_size": 0.25, "point_value": 2.0})
    point_value = tick_info["point_value"]
    tick_size   = tick_info["tick_size"]

    # ── 1. Carrega snapshots ──────────────────────────────────────────────────
    def _parse_date(val) -> Optional[datetime]:
        """Converte string YYYY-MM-DD ou datetime para datetime UTC."""
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        try:
            return datetime.fromisoformat(str(val).rstrip("Z")).replace(tzinfo=timezone.utc)
        except Exception:
            return None

    query: Dict[str, Any] = {"symbol": symbol}
    if cfg.get("mode_filter"):
        query["mode"] = cfg["mode_filter"]
    if cfg.get("session_filter"):
        query["session_label"] = cfg["session_filter"].upper()
    dt_from = _parse_date(cfg.get("start_date"))
    dt_to   = _parse_date(cfg.get("end_date"))
    if dt_from:
        query.setdefault("recorded_at", {})["$gte"] = dt_from
    if dt_to:
        # fim do dia: acrescenta 23:59:59
        query.setdefault("recorded_at", {})["$lte"] = dt_to.replace(
            hour=23, minute=59, second=59, microsecond=999999
        )

    cursor = database.scalp_snapshots.find(query, {"_id": 0}).sort("recorded_at", 1)
    snaps  = await cursor.to_list(length=10000)

    if not snaps:
        logger.warning(
            "REPLAY [%s] sem snapshots (modo=%s sessão=%s datas=%s→%s)",
            symbol, cfg.get("mode_filter", "ALL"),
            cfg.get("session_filter", "ALL"),
            cfg.get("start_date"), cfg.get("end_date"),
        )
        return {
            "replay_id": str(uuid.uuid4())[:12],
            "config": cfg,
            "metrics": _compute_metrics([], []),
            "trades": [],
            "equity_curve": [],
            "snapshots_used": 0,
        }

    # ── Log de início ─────────────────────────────────────────────────────────
    _first_ts = snaps[0].get("recorded_at")
    _last_ts  = snaps[-1].get("recorded_at")
    use_candles = bool(config.get("use_databento_candles", False))
    logger.info(
        "REPLAY [%s] início: %d snapshots | modo=%s | sessão=%s | outcome=%s | %s → %s",
        symbol, len(snaps), cfg.get("mode_filter", "ALL"),
        cfg.get("session_filter", "ALL"),
        "M1_CANDLES" if use_candles else "SNAPSHOTS",
        _first_ts, _last_ts,
    )

    # ── 2a. Pre-fetch M1 candles (opcional — modo DataBento) ────────────────
    # Chave: date_str → lista de candles ordenados por ts_dt
    candle_cache: Dict[str, List[Dict]] = {}
    if use_candles:
        unique_dates = set()
        for s in snaps:
            rat = s.get("recorded_at")
            if isinstance(rat, str):
                unique_dates.add(rat[:10])
            elif isinstance(rat, datetime):
                unique_dates.add(rat.strftime("%Y-%m-%d"))
        logger.info("REPLAY M1: pre-fetch candles para %d datas", len(unique_dates))
        candle_cache = await _fetch_m1_candles(symbol, unique_dates)
        fetched_dates = sum(1 for v in candle_cache.values() if v)
        logger.info("REPLAY M1: %d/%d datas com candles", fetched_dates, len(unique_dates))
        if fetched_dates == 0:
            logger.warning("REPLAY M1: sem candles — revertendo para modo snapshot")
            use_candles = False

    # ── 2b. Inicializa estado ────────────────────────────────────────────────
    capital       = float(risk.get("initial_capital", 25000.0))
    contracts     = int(risk.get("contracts", 1))
    commission    = float(risk.get("commission", 2.50))
    max_consec_l  = int(risk.get("max_consecutive_losses", 3))
    max_daily_pct = float(risk.get("max_daily_loss_pct", 5.0))

    trades:        List[Dict] = []
    equity_curve   = [capital]
    slippage_ticks = int(risk.get("slippage_ticks", 1))

    active_pos:    Optional[ScalpVirtualPosition] = None
    hold_until_ts: Optional[datetime] = None   # modo M1: bloqueia novas entradas
    consec_losses  = 0
    daily_loss     = 0.0
    current_day    = None
    _n_signals     = 0
    _n_skipped     = 0

    # ── 3. Itera snapshots ───────────────────────────────────────────────────
    for i, snap in enumerate(snaps):
        # Normaliza timestamp do snapshot
        rec_at = snap.get("recorded_at")
        if isinstance(rec_at, str):
            try:
                rec_at = datetime.fromisoformat(rec_at.rstrip("Z")).replace(tzinfo=timezone.utc)
            except Exception:
                rec_at = None
        elif isinstance(rec_at, datetime) and rec_at.tzinfo is None:
            rec_at = rec_at.replace(tzinfo=timezone.utc)

        day_str = rec_at.strftime("%Y-%m-%d") if rec_at else None
        if day_str != current_day:
            current_day = day_str
            daily_loss  = 0.0

        price = snap.get("last_price") or 0.0
        if not price:
            continue

        # ── Modo Snapshot: verifica exits para posição activa ────────────────
        if not use_candles and active_pos is not None:
            offset = i - active_pos.snap_idx
            closed = active_pos.check_exit(price, offset)
            if closed:
                pnl = active_pos.pnl_usd
                daily_loss    += min(0, pnl)
                capital       += pnl
                equity_curve.append(capital)
                consec_losses  = (consec_losses + 1) if pnl <= 0 else 0
                trades.append({
                    "snap_idx":     active_pos.snap_idx,
                    "action":       active_pos.action,
                    "entry":        active_pos.entry_price,
                    "exit":         active_pos.exit_price,
                    "exit_reason":  active_pos.exit_reason,
                    "pnl":          round(pnl, 2),
                    "mode":         snap.get("mode", "UNKNOWN"),
                    "contracts":    contracts,
                    "outcome_src":  "snapshot",
                    "zone_type":    active_pos.zone_type,
                    "session_label": active_pos.session_label,
                    "quality":      active_pos.quality,
                })
                active_pos = None

        # ── Modo M1: bloqueia novas entradas durante hold period ─────────────
        if use_candles and hold_until_ts is not None and rec_at is not None:
            if rec_at < hold_until_ts:
                continue

        # Limites de risco diário / perdas consecutivas
        if abs(daily_loss) >= capital * max_daily_pct / 100:
            _n_skipped += 1
            continue
        if consec_losses >= max_consec_l:
            _n_skipped += 1
            continue

        # Sem novas entradas com posição aberta (modo snapshot)
        if not use_candles and active_pos is not None:
            continue

        # ── 4. Re-avalia sinal ───────────────────────────────────────────────
        try:
            action, reason = reevaluate_scalp_signal(snap, cfg)
        except Exception as _re_err:
            logger.warning("REPLAY [%s] snap %d reevaluate error: %s", symbol, i, _re_err)
            continue
        if action is None:
            continue
        _n_signals += 1
        logger.debug("REPLAY [%s] snap %d → %s | %s", symbol, i, action, reason)

        # ── 5. Calcula entry, SL, TP ─────────────────────────────────────────
        slippage_pts = slippage_ticks * tick_size
        entry = (price + slippage_pts) if action == "BUY" else (price - slippage_pts)
        mode  = snap.get("mode", "FLOW")
        sl_price, tp_price = _compute_sl_tp(action, entry, cfg, mode, snap, symbol)

        # ── 6a. Modo M1: outcome imediato via candles DataBento ──────────────
        if use_candles and rec_at is not None:
            date_str_key = rec_at.strftime("%Y-%m-%d")
            day_candles  = candle_cache.get(date_str_key, [])

            exit_price, exit_reason, exit_ts = _outcome_from_candles(
                action=action,
                entry=entry,
                sl_price=sl_price,
                tp_price=tp_price,
                candles=day_candles,
                entry_ts=rec_at,
            )

            pts = (exit_price - entry) if action == "BUY" else (entry - exit_price)
            pnl = pts * point_value * contracts - commission * contracts

            daily_loss    += min(0, pnl)
            capital       += pnl
            equity_curve.append(capital)
            consec_losses  = (consec_losses + 1) if pnl <= 0 else 0
            hold_until_ts  = exit_ts  # bloqueia novas entradas até ao exit

            _zt, _sl, _ql = _snap_meta(snap)
            trades.append({
                "snap_idx":     i,
                "action":       action,
                "entry":        round(entry, 4),
                "exit":         round(exit_price, 4),
                "exit_reason":  exit_reason,
                "pnl":          round(pnl, 2),
                "mode":         mode,
                "contracts":    contracts,
                "outcome_src":  "m1_candle",
                "entry_ts":     rec_at.isoformat() if rec_at else None,
                "exit_ts":      exit_ts.isoformat() if exit_ts else None,
                "zone_type":    _zt,
                "session_label": _sl,
                "quality":      _ql,
            })
            continue

        # ── 6b. Modo Snapshot: abre posição virtual (fechada em iterações futuras)
        _zt, _sl, _ql = _snap_meta(snap)
        active_pos = ScalpVirtualPosition(
            action=action,
            entry_price=entry,
            sl_price=sl_price,
            tp_price=tp_price,
            contracts=contracts,
            commission=commission,
            point_value=point_value,
            snap_idx=i,
            zone_type=_zt,
            session_label=_sl,
            quality=_ql,
        )

    # Fecha posição ainda aberta no final (modo snapshot)
    if active_pos is not None and snaps:
        last_price = snaps[-1].get("last_price") or active_pos.entry_price
        active_pos._close(last_price, "EOD")
        pnl = active_pos.pnl_usd
        capital += pnl
        equity_curve.append(capital)
        trades.append({
            "snap_idx":    active_pos.snap_idx,
            "action":      active_pos.action,
            "entry":       active_pos.entry_price,
            "exit":        active_pos.exit_price,
            "exit_reason": active_pos.exit_reason,
            "pnl":          round(pnl, 2),
            "mode":         snaps[-1].get("mode", "UNKNOWN"),
            "contracts":    contracts,
            "outcome_src":  "snapshot",
            "zone_type":    active_pos.zone_type,
            "session_label": active_pos.session_label,
            "quality":      active_pos.quality,
        })

    metrics = _compute_metrics(trades, equity_curve)
    _elapsed = time.perf_counter() - _t0

    # ── Breakdowns por dimensão ────────────────────────────────────────────────
    by_zone_type = _breakdown(trades, "zone_type")
    by_session   = _breakdown(trades, "session_label")
    by_quality   = _breakdown(trades, "quality")

    logger.info(
        "REPLAY [%s] concluído em %.2fs | snaps=%d sinais=%d trades=%d skipped=%d "
        "| win=%.0f%% pnl=$%.2f sharpe=%.2f | outcome=%s",
        symbol, _elapsed, len(snaps), _n_signals, metrics["total_trades"], _n_skipped,
        metrics["win_rate"] * 100, metrics["total_pnl"], metrics["sharpe_ratio"],
        "M1_CANDLES" if use_candles else "SNAPSHOTS",
    )

    return {
        "replay_id":      str(uuid.uuid4())[:12],
        "config":         cfg,
        "metrics":        metrics,
        "trades":         trades[:200],
        "equity_curve":   [round(e, 2) for e in equity_curve],
        "snapshots_used": len(snaps),
        "outcome_source": "m1_candle" if use_candles else "snapshot",
        "by_zone_type":   by_zone_type,
        "by_session":     by_session,
        "by_quality":     by_quality,
    }
