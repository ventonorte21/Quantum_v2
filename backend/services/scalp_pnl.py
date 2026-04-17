"""
scalp_pnl.py — Funções compartilhadas de cálculo de PnL para o pipeline Scalp.

Usado por:
  - services/scalp_auto_trader.py  (execução e monitoramento de trades)
  - routes/scalp.py                (endpoints de estatísticas e fechamento manual)
"""

import math
from typing import Dict

# MNQ=$2.00/pt | MES=$5.00/pt | tick_size=0.25 para ambos
DOLLAR_PER_POINT: Dict[str, float] = {
    "MNQ": 2.0,
    "MES": 5.0,
}

TICK_SIZE: Dict[str, float] = {
    "MNQ": 0.25,
    "MES": 0.25,
}


def round_to_tick(price: float, symbol: str) -> float:
    """Arredonda um preço para o tick mais próximo do instrumento."""
    tick = TICK_SIZE.get(symbol, 0.25)
    return round(round(price / tick) * tick, 2)


def align_sl_to_tick(sl_price: float, action: str, symbol: str) -> float:
    """Alinha o SL ao tick do broker, usando arredondamento conservador por direcção.

    O Tradovate/PickMyTrade cria a stop order com a distância em pontos aplicada
    ao fill price (não ao signal price) e arredonda para o tick. Como o fill é
    tipicamente igual ou inferior ao signal price para ordens a mercado, o stop
    real fica sistematicamente 1 tick abaixo do SL calculado pelo sistema.

    Para garantir que o threshold interno detecta o fill real:
      - SELL (SL acima da entrada): arredonda para BAIXO → tick = floor
        Ex.: 26359.68 → 26359.50  (alinha com o stop do broker)
      - BUY  (SL abaixo da entrada): arredonda para CIMA  → tick = ceil
        Ex.: 26149.32 → 26149.50  (alinha com o stop do broker)

    Args:
        sl_price: Stop loss price do sinal (signal.s3_stop_loss_price).
        action:   "buy" ou "sell".
        symbol:   Símbolo do contrato (ex.: "MNQ", "MES").

    Returns:
        SL alinhado ao tick, 2 casas decimais.
    """
    tick = TICK_SIZE.get(symbol, 0.25)
    if action == "sell":
        return round(math.floor(sl_price / tick) * tick, 2)
    return round(math.ceil(sl_price / tick) * tick, 2)


def pnl_usd(symbol: str, pnl_pts: float, quantity: int = 1) -> float:
    """Converte PnL em pontos para USD.

    Args:
        symbol:    Símbolo do contrato (ex.: "MNQ", "MES").
        pnl_pts:   PnL em pontos (positivo = lucro).
        quantity:  Número de contratos.

    Returns:
        PnL em USD arredondado a 2 casas decimais.
        Usa $2.00/pt como fallback para símbolos desconhecidos.
    """
    dpp = DOLLAR_PER_POINT.get(symbol, 2.0)
    return round(pnl_pts * dpp * quantity, 2)


def compute_pnl_pts(action: str, entry: float, exit_price: float) -> float:
    """Calcula PnL em pontos dado o sentido do trade.

    Args:
        action:     "buy" (long) ou "sell" (short).
        entry:      Preço de entrada.
        exit_price: Preço de saída.

    Returns:
        PnL em pontos arredondado a 4 casas decimais. Positivo = lucro.
    """
    if action == "buy":
        return round(exit_price - entry, 4)
    return round(entry - exit_price, 4)
