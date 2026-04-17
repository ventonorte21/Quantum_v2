"""
Gamma Ratio Service — D-1 ETF→Futures Multiplier with Rollover Awareness

Stores the previous day's closing ratio (futures_close / etf_close) in MongoDB.
Uses DataBento continuous contract (volume leader = .v.0) so the ratio
automatically reflects the front-month active contract, even during rollover.

During quarterly rolls (3rd week of Mar/Jun/Sep/Dec), the continuous contract
(.v.0) switches from the expiring month to the next when volume migrates.
This means the D-1 close already uses the new contract's price, which includes
~3 months of cost-of-carry basis, avoiding an artificial gap in the gamma walls.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
import yfinance as yf

logger = logging.getLogger(__name__)

# Futures root → ETF mapping (same as gamma exposure)
FUTURES_ETF_MAP = {
    'MNQ': 'QQQ',
    'MES': 'SPY',
}

# CME quarterly expiry months: H=Mar, M=Jun, U=Sep, Z=Dec
ROLL_MONTHS = {3, 6, 9, 12}


class GammaRatioService:
    """Manages D-1 closing ratio for ETF→Futures gamma level projection."""

    def __init__(self, databento_service, database):
        self.databento_service = databento_service
        self.db = database
        self.collection = database['gamma_ratios']
        # In-memory cache: {symbol: {date, ratio, futures_close, etf_close, ...}}
        self._cache: Dict[str, dict] = {}

    async def get_ratio(self, symbol: str, realtime_futures_price: float = 0,
                        realtime_etf_price: float = 0) -> dict:
        """
        Get the D-1 ratio for a symbol. Falls back to real-time if unavailable.

        Returns: {
            ratio: float,
            source: 'd1_close' | 'realtime_fallback',
            date: str (ISO date),
            futures_close: float,
            etf_close: float,
            contract: str (e.g. 'MNQ.v.0'),
            is_roll_period: bool,
        }
        """
        today = datetime.now(timezone.utc).date()
        cache_key = symbol

        # Check in-memory cache first (valid for today)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if cached.get('computed_for') == str(today) and cached.get('ratio', 0) > 0:
                return cached

        # Check MongoDB
        stored = await self.collection.find_one(
            {'symbol': symbol, 'computed_for': str(today)},
            {'_id': 0}
        )
        if stored and stored.get('ratio', 0) > 0:
            self._cache[cache_key] = stored
            return stored

        # Compute fresh D-1 ratio
        result = await self._compute_d1_ratio(symbol, today)

        if result and result.get('ratio', 0) > 0:
            # Store in MongoDB (upsert by symbol+date)
            await self.collection.update_one(
                {'symbol': symbol, 'computed_for': str(today)},
                {'$set': result},
                upsert=True
            )
            self._cache[cache_key] = result
            return result

        # Fallback to real-time ratio
        rt_ratio = (realtime_futures_price / realtime_etf_price
                     if realtime_etf_price > 0 and realtime_futures_price > 0 else 1.0)
        fallback = {
            'symbol': symbol,
            'ratio': round(rt_ratio, 6),
            'source': 'realtime_fallback',
            'computed_for': str(today),
            'futures_close': realtime_futures_price,
            'etf_close': realtime_etf_price,
            'contract': f'{symbol}.v.0',
            'is_roll_period': self._is_roll_period(today),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        self._cache[cache_key] = fallback
        return fallback

    async def _compute_d1_ratio(self, symbol: str, today) -> Optional[dict]:
        """
        Compute D-1 ratio using DataBento continuous contract close + yfinance ETF close.
        The continuous contract (.v.0) is the volume leader, so during rollover
        it automatically uses the new front-month contract.
        """
        etf_symbol = FUTURES_ETF_MAP.get(symbol)
        if not etf_symbol:
            logger.warning(f"No ETF mapping for {symbol}")
            return None

        futures_close = await self._get_futures_d1_close(symbol)
        etf_close = self._get_etf_d1_close(etf_symbol)

        if not futures_close or not etf_close or etf_close <= 0:
            logger.warning(
                f"Cannot compute D-1 ratio for {symbol}: "
                f"futures_close={futures_close}, etf_close={etf_close}"
            )
            return None

        ratio = futures_close / etf_close
        is_roll = self._is_roll_period(today)

        result = {
            'symbol': symbol,
            'etf_symbol': etf_symbol,
            'ratio': round(ratio, 6),
            'futures_close': round(futures_close, 2),
            'etf_close': round(etf_close, 2),
            'source': 'd1_close',
            'contract': f'{symbol}.v.0',
            'is_roll_period': is_roll,
            'computed_for': str(today),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

        if is_roll:
            logger.info(
                f"ROLL PERIOD for {symbol}: D-1 ratio={ratio:.6f} "
                f"(futures={futures_close}, etf={etf_close}). "
                f"Using volume leader via continuous contract."
            )

        return result

    async def _get_futures_d1_close(self, symbol: str) -> Optional[float]:
        """Get previous trading day's closing price from DataBento continuous contract."""
        if not self.databento_service or not self.databento_service.client:
            logger.warning("DataBento client unavailable for D-1 close")
            return None

        try:
            from datetime import date
            continuous = f'{symbol}.v.0'
            now = datetime.now(timezone.utc)

            # Fetch last 5 days of daily OHLCV to guarantee we get D-1
            start = (now - timedelta(days=7)).strftime('%Y-%m-%dT00:00')
            end = now.strftime('%Y-%m-%dT00:00')

            import databento as db
            data = self.databento_service.client.timeseries.get_range(
                dataset=self.databento_service.dataset,
                symbols=continuous,
                schema='ohlcv-1d',
                start=start,
                end=end,
                stype_in='continuous',
            )
            df = data.to_df()
            if df.empty:
                return None

            # Last complete day's close
            last_row = df.iloc[-1]
            close_price = float(last_row['close'])
            logger.info(f"D-1 close for {symbol}: {close_price:.2f} (contract: {continuous})")
            return close_price

        except Exception as e:
            logger.error(f"Error fetching D-1 futures close for {symbol}: {e}")
            return None

    @staticmethod
    def _get_etf_d1_close(etf_symbol: str) -> Optional[float]:
        """Get previous trading day's closing price from yfinance."""
        try:
            tk = yf.Ticker(etf_symbol)
            hist = tk.history(period='5d')
            if hist.empty:
                return None
            close = float(hist['Close'].iloc[-1])
            logger.info(f"D-1 close for {etf_symbol}: ${close:.2f}")
            return close
        except Exception as e:
            logger.error(f"Error fetching D-1 ETF close for {etf_symbol}: {e}")
            return None

    @staticmethod
    def _is_roll_period(today) -> bool:
        """
        Check if we're in a CME quarterly roll period.
        Roll happens on the 2nd Friday before the 3rd Friday of the roll month.
        Simplification: flag the entire 3rd week (days 15-21) of roll months,
        plus the preceding 5 business days for early rollers.
        """
        if today.month not in ROLL_MONTHS:
            return False
        # 3rd week: days 10-21 covers the typical roll window
        return 10 <= today.day <= 21
