"""
Feed Health Monitor
====================
Periodic health check for the DataBento live feed.
Distinguishes between technical failures and expected market closures
using the CME Globex schedule for E-mini/Micro equity index futures.

CME Globex Schedule (MNQ/MES):
  Open:  Sunday 18:00 ET → Friday 17:00 ET
  Daily Halt: 17:00–18:00 ET (Mon–Thu)
  Weekend: Friday 17:00 ET → Sunday 18:00 ET

States:
  LIVE    — Feed active, trades flowing (< 30s age)
  CLOSED  — Market closed per CME schedule (expected silence)
  STALE   — Market OPEN but feed delayed (> 30s, < 300s)
  DEAD    — Market OPEN but feed broken (> 300s or disconnected)
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from enum import Enum

logger = logging.getLogger("feed_health")

# ── CME Globex schedule constants (all in ET / America/New_York) ──
# ET = UTC-5 (EST) or UTC-4 (EDT). We use a fixed offset approach
# and check DST via the month heuristic (Mar-Nov = EDT).
MARKET_OPEN_HOUR = 18   # 6:00 PM ET
MARKET_CLOSE_HOUR = 17  # 5:00 PM ET
# Maintenance halt is 17:00–18:00 ET every day (Mon-Thu)

STALE_THRESHOLD_S = 30
DEAD_THRESHOLD_S = 300
CHECK_INTERVAL_S = 15


class FeedState(str, Enum):
    LIVE = "LIVE"
    CLOSED = "CLOSED"
    STALE = "STALE"
    DEAD = "DEAD"


def _utc_to_et(utc_dt: datetime) -> datetime:
    """Convert UTC datetime to Eastern Time (approximate DST handling)."""
    month = utc_dt.month
    # EDT (UTC-4): March second Sunday → November first Sunday
    # Simplified: Apr-Oct always EDT, Nov-Mar always EST, Mar/Nov edge cases
    if 4 <= month <= 10:
        offset = timedelta(hours=-4)
    else:
        offset = timedelta(hours=-5)
    return utc_dt + offset


def is_cme_market_open(utc_now: Optional[datetime] = None) -> dict:
    """Check if CME Globex is open for MNQ/MES trading.

    Returns dict with:
      open: bool
      reason: str (why closed if closed)
      next_open_utc: datetime (estimate of next open)
    """
    if utc_now is None:
        utc_now = datetime.now(timezone.utc)

    et_now = _utc_to_et(utc_now)
    weekday = et_now.weekday()  # 0=Mon, 6=Sun
    hour = et_now.hour

    # Weekend closure: Friday 17:00 ET → Sunday 18:00 ET
    if weekday == 5:  # Saturday
        next_open = et_now.replace(hour=18, minute=0, second=0) + timedelta(days=1)
        return {"open": False, "reason": "Weekend (Sabado)", "next_open_et": next_open.strftime("%a %H:%M ET")}

    if weekday == 6:  # Sunday
        if hour < 18:
            next_open_str = "Domingo 18:00 ET"
            return {"open": False, "reason": "Weekend (Domingo pre-abertura)", "next_open_et": next_open_str}
        else:
            return {"open": True, "reason": "Globex aberto (Domingo noite)", "next_open_et": None}

    if weekday == 4:  # Friday
        if hour >= 17:
            return {"open": False, "reason": "Weekend (Sexta pos-fecho)", "next_open_et": "Domingo 18:00 ET"}

    # Daily maintenance halt: 17:00–18:00 ET (Mon-Thu)
    if weekday in (0, 1, 2, 3):  # Mon-Thu
        if hour == 17:
            return {"open": False, "reason": "Manutencao diaria (17:00-18:00 ET)", "next_open_et": "Hoje 18:00 ET"}

    # Friday before 17:00 is open
    if weekday == 4 and hour < 17:
        return {"open": True, "reason": "Globex aberto (Sexta)", "next_open_et": None}

    # Mon-Thu: open if past 18:00 or before 17:00 (next day)
    # Since halt is 17-18, and we already caught hour==17 above:
    if 0 <= hour < 17 or hour >= 18:
        return {"open": True, "reason": "Globex aberto", "next_open_et": None}

    return {"open": True, "reason": "Globex aberto", "next_open_et": None}


def evaluate_feed_state(
    connected: bool,
    last_trade_age_s: Optional[float],
    trades_received: int,
    utc_now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Evaluate the health state of a single symbol's feed.

    Returns:
      state: FeedState
      reason: str
      market: dict (from is_cme_market_open)
    """
    market = is_cme_market_open(utc_now)

    if not market["open"]:
        return {
            "state": FeedState.CLOSED,
            "reason": market["reason"],
            "market": market,
        }

    # Market is open — check feed health
    if not connected:
        return {
            "state": FeedState.DEAD,
            "reason": "WebSocket desconectado durante horario de mercado",
            "market": market,
        }

    if trades_received == 0 or last_trade_age_s is None:
        return {
            "state": FeedState.DEAD,
            "reason": "Nenhum trade recebido desde conexao (mercado aberto)",
            "market": market,
        }

    if last_trade_age_s > DEAD_THRESHOLD_S:
        return {
            "state": FeedState.DEAD,
            "reason": f"Ultimo trade ha {last_trade_age_s:.0f}s (limite: {DEAD_THRESHOLD_S}s)",
            "market": market,
        }

    if last_trade_age_s > STALE_THRESHOLD_S:
        return {
            "state": FeedState.STALE,
            "reason": f"Feed atrasado: {last_trade_age_s:.0f}s sem trade (limite: {STALE_THRESHOLD_S}s)",
            "market": market,
        }

    return {
        "state": FeedState.LIVE,
        "reason": f"Feed ativo ({trades_received} trades, age: {last_trade_age_s:.1f}s)",
        "market": market,
    }


class FeedHealthMonitor:
    """Background monitor that periodically evaluates feed health
    and persists snapshots to MongoDB for historical analysis."""

    def __init__(self, live_data_service, db):
        self.live_service = live_data_service
        self.db = db
        self.collection_name = "feed_health_log"
        self._task: Optional[asyncio.Task] = None
        self._current_state: Dict[str, Dict] = {}
        self._running = False

    @property
    def current_state(self) -> Dict[str, Dict]:
        return self._current_state

    def _evaluate_symbol(self, symbol: str) -> Dict[str, Any]:
        buf = self.live_service.buffers.get(symbol)
        if not buf:
            return evaluate_feed_state(False, None, 0)

        age = None
        if buf.last_trade_time:
            age = (datetime.now(timezone.utc) - buf.last_trade_time).total_seconds()

        return evaluate_feed_state(
            connected=buf.connected,
            last_trade_age_s=age,
            trades_received=buf.trade_count_since_start,
        )

    async def _check_loop(self):
        """Main loop: evaluate every CHECK_INTERVAL_S, persist to MongoDB."""
        while self._running:
            try:
                now_tz = datetime.now(timezone.utc)
                for symbol in ('MNQ', 'MES'):
                    result = self._evaluate_symbol(symbol)
                    buf = self.live_service.buffers.get(symbol)
                    buf_connected      = buf.connected if buf else False
                    buf_trades         = buf.trade_count_since_start if buf else 0
                    buf_size           = len(buf.trades) if buf else 0
                    buf_last_age       = round((datetime.now(timezone.utc) - buf.last_trade_time).total_seconds(), 1) if (buf and buf.last_trade_time) else None
                    self._current_state[symbol] = {
                        **result,
                        "state":           result["state"].value,
                        "symbol":          symbol,
                        "checked_at":      now_tz.isoformat(),
                        "connected":       buf_connected,
                        "trades_received": buf_trades,
                        "last_trade_age_s": buf_last_age,
                        "buffer_size":     buf_size,
                    }

                    # Persist to MongoDB (only on state changes or every 5 min)
                    prev = getattr(self, f'_prev_{symbol}', None)
                    state_changed = prev != result["state"].value
                    minute_mark = now_tz.second < CHECK_INTERVAL_S  # roughly every minute

                    if state_changed or minute_mark:
                        try:
                            doc = {
                                "symbol": symbol,
                                "state": result["state"].value,
                                "reason": result["reason"],
                                "market_open": result["market"]["open"],
                                "market_reason": result["market"]["reason"],
                                "connected": buf_connected,
                                "trades_received": buf_trades,
                                "last_trade_age_s": buf_last_age,
                                "last_price": buf.last_price if buf else 0,
                                "ofi_fast": buf.ofi_fast if buf else 0,
                                "buffer_size": buf_size,
                                "timestamp": now_tz,
                            }
                            await self.db[self.collection_name].insert_one(doc)
                        except Exception as e:
                            logger.warning(f"Failed to persist health check: {e}")

                    setattr(self, f'_prev_{symbol}', result["state"].value)

            except Exception as e:
                logger.error(f"Feed health check error: {e}")

            await asyncio.sleep(CHECK_INTERVAL_S)

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info("FeedHealthMonitor started (interval: %ds)", CHECK_INTERVAL_S)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("FeedHealthMonitor stopped")

    async def get_report(self, symbol: str, hours: int = 24) -> Dict[str, Any]:
        """Generate a feed quality report for the last N hours."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        try:
            cursor = self.db[self.collection_name].find(
                {"symbol": symbol, "timestamp": {"$gte": since}},
                {"_id": 0}
            ).sort("timestamp", 1)
            docs = await cursor.to_list(length=10000)
        except Exception as e:
            logger.error(f"Report query failed: {e}")
            docs = []

        if not docs:
            return {
                "symbol": symbol,
                "period_hours": hours,
                "total_checks": 0,
                "states": {},
                "uptime_pct": 0,
                "incidents": [],
                "timeline": [],
            }

        # Count states
        state_counts = {}
        for d in docs:
            s = d.get("state", "UNKNOWN")
            state_counts[s] = state_counts.get(s, 0) + 1

        total = len(docs)
        live_count = state_counts.get("LIVE", 0)
        closed_count = state_counts.get("CLOSED", 0)
        market_open_checks = total - closed_count

        uptime_pct = round((live_count / market_open_checks * 100), 1) if market_open_checks > 0 else 100.0

        # Detect incidents (STALE or DEAD periods)
        incidents = []
        current_incident = None
        for d in docs:
            st = d.get("state")
            ts = d.get("timestamp")
            if isinstance(ts, datetime):
                ts_str = ts.isoformat()
            else:
                ts_str = str(ts)

            if st in ("STALE", "DEAD"):
                if not current_incident or current_incident["state"] != st:
                    if current_incident:
                        current_incident["end"] = ts_str
                        incidents.append(current_incident)
                    current_incident = {
                        "state": st,
                        "start": ts_str,
                        "end": None,
                        "reason": d.get("reason", ""),
                        "checks": 1,
                    }
                else:
                    current_incident["checks"] += 1
            else:
                if current_incident:
                    current_incident["end"] = ts_str
                    incidents.append(current_incident)
                    current_incident = None

        if current_incident:
            current_incident["end"] = docs[-1].get("timestamp", "").isoformat() if isinstance(docs[-1].get("timestamp"), datetime) else str(docs[-1].get("timestamp", ""))
            incidents.append(current_incident)

        # Serialize timeline (last 100 points for chart)
        timeline = []
        step = max(1, len(docs) // 100)
        for i in range(0, len(docs), step):
            d = docs[i]
            ts = d.get("timestamp")
            timeline.append({
                "t": ts.isoformat() if isinstance(ts, datetime) else str(ts),
                "state": d.get("state"),
                "age": d.get("last_trade_age_s"),
                "trades": d.get("trades_received", 0),
                "price": d.get("last_price", 0),
            })

        return {
            "symbol": symbol,
            "period_hours": hours,
            "total_checks": total,
            "states": state_counts,
            "uptime_pct": uptime_pct,
            "market_open_checks": market_open_checks,
            "incidents": incidents,
            "incident_count": len(incidents),
            "timeline": timeline,
        }
