"""
Overnight Inventory Service

Captures and manages static overnight levels (ONH, ONL, ON POC) for use
as "Glass Walls" during the first hour of NYSE regular session.

Lifecycle:
1. At 09:29:59 ET, consolidate all ticks from 18:00 ET → 09:29 ET
2. Compute ONH, ONL, ON POC and store in MongoDB
3. N3 uses these levels as trigger zones with ±0.5×ATR_M1 buffers
4. Levels decay in relevance after 10:30 ET (first hour only)
"""

import logging
from datetime import datetime, timezone, timedelta, time as dt_time
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger('overnight_inventory')
ET = ZoneInfo('America/New_York')


class OvernightInventoryService:
    """Manages overnight inventory levels (ONH, ONL, ON POC)."""

    def __init__(self, db=None, live_data_service=None):
        self.db = db
        self.live_data_service = live_data_service
        self._today_inventory: Optional[Dict] = None
        self._snapshot_taken_date: Optional[str] = None

    def set_db(self, db):
        self.db = db

    def set_live_data_service(self, live_data_service):
        self.live_data_service = live_data_service

    def _get_today_date_et(self) -> str:
        """Get today's date in ET for keying inventory."""
        now_et = datetime.now(ET)
        return now_et.strftime('%Y-%m-%d')

    def is_snapshot_time(self) -> bool:
        """Check if it's time to capture overnight snapshot (09:25-09:30 ET)."""
        now_et = datetime.now(ET)
        t = now_et.time()
        return dt_time(9, 25) <= t <= dt_time(9, 30) and now_et.weekday() < 5

    def is_first_hour(self) -> Tuple[bool, float]:
        """
        Check if we're in the first hour of NYSE (09:30-10:30 ET).
        Returns (is_first_hour, relevance_factor).
        Relevance decays linearly: 1.0 at 09:30 → 0.1 at 10:30 → 0.0 at 11:00.
        """
        now_et = datetime.now(ET)
        t = now_et.time()

        if now_et.weekday() >= 5:
            return False, 0.0

        if t < dt_time(9, 30) or t >= dt_time(11, 0):
            return False, 0.0

        # Minutes since 09:30
        minutes_since_open = (now_et.hour - 9) * 60 + now_et.minute - 30
        if minutes_since_open <= 60:
            # Full relevance in first hour (09:30-10:30)
            relevance = 1.0 - (minutes_since_open / 60) * 0.5  # 1.0 → 0.5
            return True, relevance
        else:
            # Decay from 10:30 to 11:00 (0.5 → 0.0)
            minutes_past_first_hour = minutes_since_open - 60
            relevance = max(0.0, 0.5 - (minutes_past_first_hour / 30) * 0.5)
            return False, relevance

    async def capture_overnight_inventory(self, symbol: str) -> Optional[Dict]:
        """
        Capture ONH, ONL, ON POC from overnight session (18:00 ET prev day → 09:29 ET today).
        Called at ~09:29 ET.
        """
        today_str = self._get_today_date_et()

        # Check if already captured today
        if self._snapshot_taken_date == today_str and self._today_inventory:
            return self._today_inventory

        # Get trades from live data service buffer
        if not self.live_data_service:
            logger.warning("No live_data_service available for overnight inventory")
            return None

        buf = self.live_data_service.buffers.get(symbol)
        if not buf or not buf.trades:
            logger.warning(f"No trade data for {symbol} to compute overnight inventory")
            return None

        # Filter trades from overnight session (18:00 ET yesterday → 09:29 ET today)
        now_et = datetime.now(ET)
        today = now_et.date()
        overnight_start_et = datetime.combine(
            today - timedelta(days=1), dt_time(18, 0), tzinfo=ET
        )
        # Handle Monday (overnight starts Friday 18:00)
        if today.weekday() == 0:  # Monday
            overnight_start_et = datetime.combine(
                today - timedelta(days=3), dt_time(18, 0), tzinfo=ET
            )
        rth_start_et = datetime.combine(today, dt_time(9, 29), tzinfo=ET)

        overnight_start_utc = overnight_start_et.astimezone(timezone.utc)
        rth_start_utc = rth_start_et.astimezone(timezone.utc)

        # Filter trades within overnight window
        overnight_trades = []
        prices = []
        volume_at_price = {}

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

            if overnight_start_utc <= trade_dt <= rth_start_utc:
                price = trade.get('price', 0)
                size = trade.get('size', 1)
                if price > 0:
                    prices.append(price)
                    overnight_trades.append(trade)
                    # Accumulate volume at price for POC calculation
                    rounded_price = round(price, 2)
                    volume_at_price[rounded_price] = volume_at_price.get(rounded_price, 0) + size

        if not prices:
            logger.info(f"No overnight trades found for {symbol} ({overnight_start_et} → {rth_start_et})")
            return None

        onh = max(prices)
        onl = min(prices)
        on_poc = max(volume_at_price, key=volume_at_price.get) if volume_at_price else (onh + onl) / 2

        inventory = {
            'symbol': symbol,
            'date': today_str,
            'onh': round(onh, 2),
            'onl': round(onl, 2),
            'on_poc': round(on_poc, 2),
            'on_range': round(onh - onl, 2),
            'trade_count': len(overnight_trades),
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'overnight_start': overnight_start_utc.isoformat(),
            'rth_start': rth_start_utc.isoformat(),
        }

        # Store in MongoDB (async Motor)
        if self.db is not None:
            try:
                await self.db.overnight_inventory.update_one(
                    {'symbol': symbol, 'date': today_str},
                    {'$set': inventory},
                    upsert=True,
                )
                logger.info(
                    f"Overnight Inventory captured for {symbol}: "
                    f"ONH={onh:.2f}, ONL={onl:.2f}, ON_POC={on_poc:.2f}, "
                    f"Range={onh-onl:.2f}, Trades={len(overnight_trades)}"
                )
            except Exception as e:
                logger.error(f"Failed to store overnight inventory: {e}")

        self._today_inventory = inventory
        self._snapshot_taken_date = today_str
        return inventory

    async def get_today_inventory(self, symbol: str) -> Optional[Dict]:
        """Get today's overnight inventory from cache or MongoDB."""
        today_str = self._get_today_date_et()

        # Return from cache
        if (self._snapshot_taken_date == today_str
                and self._today_inventory
                and self._today_inventory.get('symbol') == symbol):
            return self._today_inventory

        # Try MongoDB (async Motor)
        if self.db is not None:
            try:
                doc = await self.db.overnight_inventory.find_one(
                    {'symbol': symbol, 'date': today_str},
                    {'_id': 0},
                )
                if doc:
                    self._today_inventory = doc
                    self._snapshot_taken_date = today_str
                    return doc
            except Exception as e:
                logger.error(f"Failed to fetch overnight inventory: {e}")

        return None


overnight_inventory_service = OvernightInventoryService()
