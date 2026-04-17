"""
Position Manager Service — Gestão de Posição por Arquétipo
============================================================
Solução intermediária com 3 arquétipos de gestão que capturam
as diferenças mais relevantes entre os 5 regimes:

TREND  (Regimes 1, 2, 4): Trailing ativo via VWAP central, SL 1.5×ATR(M1)
RANGE  (Regime 3):         Fire & forget — SL atrás VAH/VAL, TP fixo no POC
FADE   (Regime 5):         Break-even após +1×ATR(M1) + 4 ticks, TP em -1σ VWAP

Fluxo:
  N3 dispara → calculate_position_params() → SignalStack OCO (Entry+SL+TP)
  RANGE: nada mais (tese binária)
  FADE:  monitora break-even
  TREND: monitora trailing (UPDATE ORDER empurra SL)
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


# ── Archetype Definitions ──

class Archetype(str, Enum):
    TREND = "TREND"
    RANGE = "RANGE"
    FADE = "FADE"


REGIME_TO_ARCHETYPE = {
    'COMPLACENCIA': Archetype.TREND,
    'BULL': Archetype.TREND,
    'BEAR': Archetype.TREND,
    'TRANSICAO': Archetype.RANGE,
    'CAPITULACAO': Archetype.FADE,
}

# ATR(M1) multipliers for hard stop per archetype
SL_ATR_MULTIPLIER = {
    Archetype.TREND: 1.5,
    Archetype.RANGE: 0.5,
    Archetype.FADE: 1.0,  # Wider stop for FADE — high-VIX microstructure is gappy
}

# Break-even config for FADE (now handled by breakeven param in webhook)
# Kept for reference in position document building
FADE_BE_ATR_MULTIPLIER = 1.0  # +1.0×ATR(M1) to trigger break-even
FADE_BE_BUFFER_TICKS = 4       # 4 ticks above threshold before moving SL


class PositionState(str, Enum):
    OPEN = "OPEN"
    MANAGING = "MANAGING"
    BREAK_EVEN = "BREAK_EVEN"
    CLOSED = "CLOSED"


class CloseReason(str, Enum):
    HARD_STOP = "HARD_STOP"
    TRAILING_STOP = "TRAILING_STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    BREAK_EVEN = "BREAK_EVEN"
    FLOW_EXIT = "FLOW_EXIT"
    MANUAL = "MANUAL"
    TIME_EXIT = "TIME_EXIT"


# ══════════════════════════════════════════════
# Core Calculator — Pure logic, no I/O
# ══════════════════════════════════════════════

def get_archetype(regime: str) -> Archetype:
    """Map regime name to archetype."""
    return REGIME_TO_ARCHETYPE.get(regime.upper(), Archetype.RANGE)


def calculate_position_params(
    regime: str,
    entry_price: float,
    side: str,
    atr_m1: float,
    levels: Dict[str, Any],
    tick_size: float = 0.25,
) -> Dict[str, Any]:
    """
    Calculate SL, TP, trailing config based on regime archetype.

    Args:
        regime: One of COMPLACENCIA, BULL, BEAR, TRANSICAO, CAPITULACAO
        entry_price: Entry price
        side: 'BUY' or 'SELL'
        atr_m1: ATR computed from M1 candles
        levels: Dict with keys like vwap, poc, vah, val, call_wall, put_wall, vwap_upper_1s, vwap_lower_1s
        tick_size: Instrument tick size

    Returns:
        Dict with sl, tp, trailing_type, trailing_config, archetype, etc.
    """
    archetype = get_archetype(regime)
    is_long = side.upper() in ('BUY', 'LONG')
    sign = 1 if is_long else -1

    # ATR fallback
    if atr_m1 <= 0:
        atr_m1 = tick_size * 20  # ~5 points for MNQ

    result = {
        'archetype': archetype.value,
        'regime': regime,
        'side': side.upper(),
        'entry_price': round(entry_price, 2),
        'atr_m1': round(atr_m1, 4),
        'tick_size': tick_size,
    }

    if archetype == Archetype.TREND:
        result.update(_calc_trend(entry_price, is_long, sign, atr_m1, levels, tick_size))
    elif archetype == Archetype.RANGE:
        result.update(_calc_range(entry_price, is_long, sign, atr_m1, levels, tick_size))
    elif archetype == Archetype.FADE:
        result.update(_calc_fade(entry_price, is_long, sign, atr_m1, levels, tick_size))

    return result


def _calc_trend(entry: float, is_long: bool, sign: int, atr: float, levels: Dict, tick: float) -> Dict:
    """
    TREND (Regimes 1, 2, 4)
    SL: 1.5×ATR(M1) behind entry
    Trailing: VWAP central
    TP: Open (trailing tira)
    """
    sl_distance = SL_ATR_MULTIPLIER[Archetype.TREND] * atr
    sl = entry - sign * sl_distance

    # TP: use call_wall (long) or put_wall (short) as theoretical target
    # but keep open — trailing will manage exit
    call_wall = levels.get('call_wall', 0)
    put_wall = levels.get('put_wall', 0)
    tp_theoretical = call_wall if is_long and call_wall > entry else put_wall if not is_long and put_wall < entry else None

    vwap = levels.get('vwap', entry)

    return {
        'hard_stop': round(sl, 2),
        'take_profit': round(tp_theoretical, 2) if tp_theoretical else None,
        'tp_type': 'OPEN_TRAILING',
        'trailing_type': 'VWAP_CENTRAL',
        'trailing_config': {
            'reference': 'vwap',
            'current_vwap': round(vwap, 2),
            'description': 'Trailing acompanha VWAP central. SL empurrado para VWAP quando favorável.',
        },
        'monitoring_required': True,
        'break_even': None,
        # Scale-out: realize 50% at 2×SL distance, move remaining stop to entry
        'scale_out': {
            'enabled': True,
            'pct': 50,
            'trigger_multiple': 2.0,  # 2× initial risk
            'trigger_price': round(entry + sign * sl_distance * 2.0, 2),
            'move_stop_to_entry': True,
        },
    }


def _calc_range(entry: float, is_long: bool, sign: int, atr: float, levels: Dict, tick: float) -> Dict:
    """
    RANGE (Regime 3 — Transição)
    SL: 0.5×ATR(M1) behind VAH (short) or VAL (long)
    Trailing: NONE (tese binária)
    TP: Fixed at POC
    """
    sl_distance = SL_ATR_MULTIPLIER[Archetype.RANGE] * atr
    vah = levels.get('vah', entry + 10)
    val = levels.get('val', entry - 10)
    poc = levels.get('poc', entry)

    if is_long:
        # Long from VAL → SL behind VAL
        sl = val - sl_distance
        tp = poc
    else:
        # Short from VAH → SL above VAH
        sl = vah + sl_distance
        tp = poc

    return {
        'hard_stop': round(sl, 2),
        'take_profit': round(tp, 2),
        'tp_type': 'FIXED_POC',
        'trailing_type': 'NONE',
        'trailing_config': None,
        'monitoring_required': False,
        'break_even': None,
    }


def _calc_fade(entry: float, is_long: bool, sign: int, atr: float, levels: Dict, tick: float) -> Dict:
    """
    FADE (Regime 5 — Capitulação)
    SL: 0.5×ATR(M1) behind Put Wall (long) or Call Wall (short)
    Trailing: Break-even after +1.0×ATR(M1) + 4 ticks
    TP: -1σ VWAP band or VWAP central
    """
    sl_distance = SL_ATR_MULTIPLIER[Archetype.FADE] * atr
    put_wall = levels.get('put_wall', entry - 20)
    call_wall = levels.get('call_wall', entry + 20)
    vwap = levels.get('vwap', entry)
    vwap_lower_1s = levels.get('vwap_lower_1s', vwap - atr)
    vwap_upper_1s = levels.get('vwap_upper_1s', vwap + atr)

    if is_long:
        # Long fading at Put Wall
        sl = put_wall - sl_distance
        tp = vwap_lower_1s if vwap_lower_1s > entry else vwap
    else:
        # Short fading at Call Wall
        sl = call_wall + sl_distance
        tp = vwap_upper_1s if vwap_upper_1s < entry else vwap

    be_threshold = entry + sign * (FADE_BE_ATR_MULTIPLIER * atr + FADE_BE_BUFFER_TICKS * tick)

    return {
        'hard_stop': round(sl, 2),
        'take_profit': round(tp, 2),
        'tp_type': 'FIXED_VWAP_1S',
        'trailing_type': 'BREAK_EVEN',
        'trailing_config': {
            'be_threshold': round(be_threshold, 2),
            'be_atr_multiplier': FADE_BE_ATR_MULTIPLIER,
            'be_buffer_ticks': FADE_BE_BUFFER_TICKS,
            'be_target': round(entry, 2),
            'description': f'Break-even quando preço atinge {round(be_threshold, 2)} (+1.0×ATR + 4 ticks). SL move para {round(entry, 2)}.',
        },
        'monitoring_required': True,
        'break_even': {
            'threshold': round(be_threshold, 2),
            'target_sl': round(entry, 2),
            'triggered': False,
        },
    }


# ══════════════════════════════════════════════
# Position Document Builder
# ══════════════════════════════════════════════

def create_position_document(
    position_params: Dict,
    order_id: str,
    symbol: str,
    trade_symbol: str,
    quantity: int,
    paper: bool = True,
) -> Dict[str, Any]:
    """Build a MongoDB-ready position document."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        'id': str(uuid.uuid4()),
        'order_id': order_id,
        'symbol': symbol,
        'trade_symbol': trade_symbol,
        'side': position_params['side'],
        'quantity': quantity,
        'archetype': position_params['archetype'],
        'regime': position_params['regime'],
        'entry_price': position_params['entry_price'],
        'hard_stop': position_params['hard_stop'],
        'current_stop': position_params['hard_stop'],
        'take_profit': position_params['take_profit'],
        'tp_type': position_params['tp_type'],
        'trailing_type': position_params['trailing_type'],
        'trailing_config': position_params.get('trailing_config'),
        'break_even': position_params.get('break_even'),
        'monitoring_required': position_params['monitoring_required'],
        'atr_m1': position_params['atr_m1'],
        'state': PositionState.OPEN.value,
        'paper': paper,
        'pnl': 0.0,
        'pnl_ticks': 0,
        'max_favorable': 0.0,
        'max_adverse': 0.0,
        'events': [
            {'type': 'OPENED', 'price': position_params['entry_price'], 'ts': now, 'detail': f'{position_params["archetype"]} position opened'}
        ],
        'opened_at': now,
        'closed_at': None,
        'close_reason': None,
        'close_price': None,
    }


# ══════════════════════════════════════════════
# Note: Active position monitoring (trailing, break-even) is now
# delegated to the broker via webhook params:
#   - TREND: trail_trigger / trail_stop / trail_freq
#   - FADE:  breakeven
# No server-side tick-by-tick monitoring loop is needed.
# ══════════════════════════════════════════════
