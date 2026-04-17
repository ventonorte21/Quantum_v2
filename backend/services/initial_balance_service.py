"""
InitialBalance Service — IBH/IBL Tracker para RTH (09:30–10:30 ET)

Regras do documento de especificação:
  - Antes das 10:30 ET: zonas IB NÃO existem (ib_locked=False).
    O modelo usa ONH/ONL para cobrir os extremos matinais.
  - Exatamente às 10:30 ET: IBH/IBL cristalizam a partir das trades RTH
    ocorridas desde 09:30. A partir deste momento tornam-se zonas Nível 1.
  - Reset diário ao detetar nova data ET.

Fonte de dados: buffer de trades do live_data_service (mesmo mecanismo
que o OvernightInventoryService usa para ONH/ONL).
"""
import logging
from datetime import datetime, time as dt_time, timezone
from typing import Dict, Optional

import pytz

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")


class InitialBalanceService:

    def __init__(self, live_data_service=None):
        self.live_data_service = live_data_service
        self._ibh:      Dict[str, float] = {}
        self._ibl:      Dict[str, float] = {}
        self._last_date: Dict[str, str]  = {}

    def set_live_data_service(self, lds) -> None:
        self.live_data_service = lds

    def get_ib_levels(self, symbol: str) -> Dict:
        """
        Retorna {'ibh': float, 'ibl': float, 'ib_locked': bool}.

        ib_locked=True apenas depois das 10:30 ET.
        Antes disso os valores são computados (IB em formação) mas
        ib_locked=False sinaliza ao engine que NÃO deve usar estes níveis.
        """
        now_et    = datetime.now(ET)
        today_str = now_et.date().isoformat()

        # Reset diário
        if self._last_date.get(symbol) != today_str:
            self._ibh.pop(symbol, None)
            self._ibl.pop(symbol, None)
            self._last_date[symbol] = today_str

        hour_frac = now_et.hour + now_et.minute / 60
        ib_started = hour_frac >= 9.5    # >= 09:30
        ib_locked  = hour_frac >= 10.5   # >= 10:30

        if not ib_started:
            return {'ibh': 0.0, 'ibl': 0.0, 'ib_locked': False}

        if not self.live_data_service:
            return {'ibh': 0.0, 'ibl': 0.0, 'ib_locked': False}

        # Cache: após cristalização, evita re-scan do buffer a cada poll
        if ib_locked and self._ibh.get(symbol) and self._ibl.get(symbol):
            return {
                'ibh': self._ibh[symbol],
                'ibl': self._ibl[symbol],
                'ib_locked': True,
            }

        buf = self.live_data_service.buffers.get(symbol)
        if not buf or not buf.trades:
            return {'ibh': 0.0, 'ibl': 0.0, 'ib_locked': False}

        today = now_et.date()
        ib_start_utc = datetime.combine(today, dt_time(9, 30), tzinfo=ET).astimezone(timezone.utc)
        ib_end_utc   = datetime.combine(today, dt_time(10, 30), tzinfo=ET).astimezone(timezone.utc)

        prices = []
        for trade in buf.trades:
            ts = trade.get('timestamp')
            if ts is None:
                continue
            if isinstance(ts, (int, float)):
                trade_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            elif isinstance(ts, datetime):
                trade_dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            else:
                continue
            if ib_start_utc <= trade_dt <= ib_end_utc:
                price = trade.get('price', 0)
                if price > 0:
                    prices.append(price)

        if not prices:
            return {'ibh': 0.0, 'ibl': 0.0, 'ib_locked': False}

        ibh = round(max(prices), 2)
        ibl = round(min(prices), 2)

        if ib_locked:
            self._ibh[symbol] = ibh
            self._ibl[symbol] = ibl
            logger.info(f"[IB] {symbol} cristalizado: IBH={ibh:.2f} IBL={ibl:.2f}")

        return {'ibh': ibh, 'ibl': ibl, 'ib_locked': ib_locked}


initial_balance_service = InitialBalanceService()
