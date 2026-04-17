"""
Trading Calendar Service — NYSE Hours, Holidays, DST, News Blackouts
=====================================================================
Provides production-grade trading session management:
- NYSE holidays & early closes via exchange_calendars (accurate, DST-aware)
- Automatic DST adjustment (America/New_York via UTC offsets from exchange_calendars)
- Economic news blackout windows with resilient monthly calendar strategy
- CME Futures pre-market start → 30min before NYSE close
- Background-friendly: all methods are sync or async as needed

Calendar Fetch Strategy:
1. Fetch entire MONTH of events on first request / weekly refresh
2. Store in MongoDB (news_calendar_monthly collection)
3. On failure: use stored monthly calendar as fallback
4. Retry hourly on failure, never block trades due to calendar issues
5. If all data considered invalid: deactivate blackout + emit dashboard alert
6. Reactivate blackout automatically when fetch succeeds
"""

import logging
import httpx
from datetime import datetime, timezone, timedelta, time as dt_time
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

logger = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')

DEFAULT_PREMARKET_START = dt_time(4, 0)
DEFAULT_PRE_CLOSE_MINUTES = 30

# Calendar refresh intervals
CALENDAR_WEEKLY_REFRESH_SECONDS = 7 * 24 * 3600  # 1 week
CALENDAR_HOURLY_RETRY_SECONDS = 3600              # 1 hour retry on failure
CALENDAR_MONTHLY_MAX_AGE_SECONDS = 35 * 24 * 3600 # 35 days (beyond this, data is stale)


RTH_SESSIONS          = {"RTH_OPEN", "RTH_MID", "RTH_CLOSE"}   # backward compat alias
NY_SESSION_LABELS     = frozenset({"RTH_OPEN", "RTH_MID", "RTH_CLOSE"})
GLOBEX_SESSION_LABELS = frozenset({"OVERNIGHT", "HALTED"})
SESSION_GROUP_MAP     = {**{s: "NY" for s in NY_SESSION_LABELS},
                         **{s: "GLOBEX" for s in GLOBEX_SESSION_LABELS}}
VALID_SESSION_GROUPS  = frozenset({"NY", "GLOBEX"})

# Inclui aliases de grupo (NY, GLOBEX) e labels granulares — válidos nos endpoints
VALID_SESSIONS = frozenset({
    "RTH_OPEN", "RTH_MID", "RTH_CLOSE", "OVERNIGHT", "HALTED",
    "RTH_ALL",   # alias legado
    "NY",        # grupo NY  = RTH_OPEN + RTH_MID + RTH_CLOSE
    "GLOBEX",    # grupo Globex = OVERNIGHT + HALTED
})


def get_session_label(dt: Optional[datetime] = None) -> str:
    """
    Classifica o momento em rótulo de sessão para micro-futuros CME (MNQ/MES):
      RTH_OPEN   → 09:30–10:29 ET  (abertura, alta volatilidade)
      RTH_MID    → 10:30–13:59 ET  (meio-dia, menor volatilidade)
      RTH_CLOSE  → 14:00–16:14 ET  (fecho, volatilidade retoma)
      HALTED     → 17:00–17:59 ET  (janela de manutenção CME)
      OVERNIGHT  → restante (Globex)
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    now_et = dt.astimezone(ET)
    total_min = now_et.hour * 60 + now_et.minute
    if 570 <= total_min < 630:   # 09:30–10:29
        return "RTH_OPEN"
    if 630 <= total_min < 840:   # 10:30–13:59
        return "RTH_MID"
    if 840 <= total_min < 975:   # 14:00–16:14
        return "RTH_CLOSE"
    if 1020 <= total_min < 1080: # 17:00–17:59
        return "HALTED"
    return "OVERNIGHT"


def get_session_group(session_label: Optional[str] = None) -> str:
    """
    Resolve um session_label granular para o grupo de sessão de alto nível.

      NY     → RTH_OPEN, RTH_MID, RTH_CLOSE  (09:30–16:14 ET)
      GLOBEX → OVERNIGHT, HALTED             (restante)

    Se session_label for None, usa o momento actual.
    Se session_label já for "NY" ou "GLOBEX", devolve directamente.
    """
    if session_label in ("NY", "GLOBEX"):
        return session_label
    if session_label is None:
        session_label = get_session_label()
    return SESSION_GROUP_MAP.get(session_label, "GLOBEX")


class TradingCalendarService:
    """NYSE-aware trading calendar with news blackout support."""

    def __init__(self, database=None):
        self.db = database
        self.nyse = xcals.get_calendar('XNYS')
        # Session cache (daily)
        self._session_cache: Dict[str, dict] = {}
        # Monthly calendar state
        self._monthly_events: List[dict] = []
        self._monthly_fetched_at: Optional[datetime] = None
        self._monthly_month_key: Optional[str] = None  # e.g. "2026-04"
        self._last_fetch_attempt: Optional[datetime] = None
        self._last_fetch_success: bool = False
        self._consecutive_failures: int = 0
        # Blackout override state
        self._blackout_suspended: bool = False
        self._blackout_suspend_reason: Optional[str] = None

    # ─── Session Info (unchanged) ───────────────────────────────────

    def get_session_info(self, dt: Optional[datetime] = None) -> dict:
        if dt is None:
            dt = datetime.now(timezone.utc)

        now_et = dt.astimezone(ET)
        today = now_et.date()
        cache_key = str(today)

        if cache_key in self._session_cache:
            cached = self._session_cache[cache_key]
            # Update dynamic fields (change within the day)
            cached['now_utc'] = dt.isoformat()
            cached['now_et'] = now_et.isoformat()
            from services.feed_health import is_cme_market_open
            cme_status = is_cme_market_open(dt)
            cached['is_cme_halted'] = not cme_status['open']
            hour_et = now_et.hour
            minute_et = now_et.minute
            if cached['is_cme_halted']:
                cached['cme_session'] = 'halted'
            elif (hour_et > 9 or (hour_et == 9 and minute_et >= 30)) and hour_et < 16:
                cached['cme_session'] = 'rth'
            else:
                cached['cme_session'] = 'globex'
            cached['cme_reason'] = cme_status.get('reason', '')
            # Update is_market_open for NYSE (also changes through the day)
            mo = cached.get('market_open_utc')
            mc = cached.get('market_close_utc')
            if mo and mc:
                from datetime import datetime as _dt_cls
                mou = _dt_cls.fromisoformat(mo)
                mcu = _dt_cls.fromisoformat(mc)
                cached['is_market_open'] = mou <= dt < mcu
                cached['minutes_to_close'] = round(max(0, (mcu - dt).total_seconds() / 60), 1)
            return cached

        ts_today = pd.Timestamp(today.isoformat())
        is_session = self.nyse.is_session(ts_today)
        is_weekend = today.weekday() >= 5

        is_sunday_evening_session = False
        if today.weekday() == 6 and now_et.hour >= 18:
            monday = today + timedelta(days=1)
            ts_monday = pd.Timestamp(monday.isoformat())
            if self.nyse.is_session(ts_monday):
                is_sunday_evening_session = True
                is_session = True
                is_weekend = False
                ts_today = ts_monday

        is_early_close = False
        early_close_time = None
        if is_session:
            early_dates = self.nyse.early_closes
            if ts_today in early_dates:
                is_early_close = True

        if is_session:
            market_open_utc, market_close_utc = self.nyse.session_open_close(ts_today)
            market_open_utc = market_open_utc.to_pydatetime().replace(tzinfo=timezone.utc)
            market_close_utc = market_close_utc.to_pydatetime().replace(tzinfo=timezone.utc)

            if is_early_close:
                early_close_et = datetime.combine(today, dt_time(13, 0), tzinfo=ET)
                market_close_utc = early_close_et.astimezone(timezone.utc)
                early_close_time = '13:00 ET'

            market_open_et = market_open_utc.astimezone(ET)
            market_close_et = market_close_utc.astimezone(ET)

            premarket_start_et = datetime.combine(today, DEFAULT_PREMARKET_START, tzinfo=ET)
            premarket_start_utc = premarket_start_et.astimezone(timezone.utc)

            auto_end_utc = market_close_utc - timedelta(minutes=DEFAULT_PRE_CLOSE_MINUTES)
            auto_end_et = auto_end_utc.astimezone(ET)

            is_premarket = premarket_start_utc <= dt < market_open_utc
            is_market_open = market_open_utc <= dt < market_close_utc
            is_auto_window = market_open_utc <= dt < auto_end_utc
            minutes_to_close = max(0, (market_close_utc - dt).total_seconds() / 60)
            minutes_to_auto_end = max(0, (auto_end_utc - dt).total_seconds() / 60)
        else:
            market_open_utc = market_close_utc = None
            market_open_et = market_close_et = None
            premarket_start_utc = premarket_start_et = None
            auto_end_utc = auto_end_et = None
            is_premarket = False
            is_market_open = False
            is_auto_window = False
            minutes_to_close = 0
            minutes_to_auto_end = 0

        next_session = None
        try:
            next_ts = self.nyse.next_session(ts_today)
            next_open, next_close = self.nyse.session_open_close(next_ts)
            next_session = {
                'date': str(next_ts.date()),
                'open_utc': next_open.to_pydatetime().replace(tzinfo=timezone.utc).isoformat(),
                'close_utc': next_close.to_pydatetime().replace(tzinfo=timezone.utc).isoformat(),
            }
        except Exception:
            pass

        is_dst = bool(now_et.dst())

        # CME Globex halt detection (17:00-18:00 ET Mon-Thu, weekend)
        from services.feed_health import is_cme_market_open
        cme_status = is_cme_market_open(dt)
        is_cme_halted = not cme_status['open']

        # Determine CME session type for frontend polling
        hour_et = now_et.hour
        minute_et = now_et.minute
        if is_cme_halted:
            cme_session = 'halted'
        elif (hour_et > 9 or (hour_et == 9 and minute_et >= 30)) and hour_et < 16:
            cme_session = 'rth'
        else:
            cme_session = 'globex'

        result = {
            'date': str(today),
            'is_trading_day': is_session,
            'is_weekend': is_weekend,
            'is_sunday_evening_session': is_sunday_evening_session,
            'is_early_close': is_early_close,
            'early_close_time': early_close_time,
            'is_dst': is_dst,
            'tz_offset': str(now_et.strftime('%z')),
            'market_open_utc': market_open_utc.isoformat() if market_open_utc else None,
            'market_close_utc': market_close_utc.isoformat() if market_close_utc else None,
            'market_open_et': market_open_et.isoformat() if market_open_et else None,
            'market_close_et': market_close_et.isoformat() if market_close_et else None,
            'premarket_start_utc': premarket_start_utc.isoformat() if premarket_start_utc else None,
            'auto_end_utc': auto_end_utc.isoformat() if auto_end_utc else None,
            'auto_end_et': auto_end_et.isoformat() if auto_end_et else None,
            'is_premarket': is_premarket,
            'is_market_open': is_market_open,
            'is_auto_trading_window': is_auto_window,
            'is_cme_halted': is_cme_halted,
            'cme_session': cme_session,
            'cme_reason': cme_status.get('reason', ''),
            'minutes_to_close': round(minutes_to_close, 1),
            'minutes_to_auto_end': round(minutes_to_auto_end, 1),
            'next_session': next_session,
            'now_utc': dt.isoformat(),
            'now_et': now_et.isoformat(),
        }

        self._session_cache[cache_key] = result
        return result

    def is_within_auto_trading_hours(self, dt: Optional[datetime] = None) -> Tuple[bool, str]:
        info = self.get_session_info(dt)
        if not info['is_trading_day']:
            if info['is_weekend']:
                return False, 'Fim de semana'
            return False, 'Feriado NYSE'
        if not info['is_auto_trading_window']:
            if info['minutes_to_close'] <= 0:
                return False, 'Mercado fechado'
            if info['minutes_to_auto_end'] <= 0:
                return False, f'Encerramento automatico: {DEFAULT_PRE_CLOSE_MINUTES}min antes do close'
            return False, 'Fora do horario de pre-mercado (antes das 4:00 AM ET)'
        return True, 'Dentro da janela de trading'

    def is_within_globex_auto_hours(self, dt: Optional[datetime] = None) -> Tuple[bool, str]:
        if dt is None:
            dt = datetime.now(timezone.utc)
        now_et = dt.astimezone(ET)
        today = now_et.date()
        hour = now_et.hour
        minute = now_et.minute

        weekday = today.weekday()
        if weekday == 5:
            return False, 'Sabado — CME fechado'
        if weekday == 6 and hour < 18:
            return False, 'Domingo pre-18h — CME ainda fechado'
        if hour == 17:
            return False, 'Halt diario CME (17:00-18:00 ET)'

        if hour >= 18:
            target_date = today + timedelta(days=1)
        else:
            target_date = today

        ts_target = pd.Timestamp(target_date.isoformat())

        if not self.nyse.is_session(ts_target):
            ts_today = pd.Timestamp(today.isoformat())
            if self.nyse.is_session(ts_today) and ts_today in self.nyse.early_closes:
                if hour >= 13:
                    return False, f'Feriado amanha ({target_date}) — Globex encerrada apos early close'
            return False, f'Feriado NYSE ({target_date}) — Globex nao opera'

        if hour == 9 and minute >= 25:
            return False, 'Proximo da abertura NYSE (09:25+ ET) — Globex Auto encerra'
        if 10 <= hour < 18:
            return False, 'Sessao NYSE ativa — usar NYSE Auto'

        return True, 'Dentro da janela Globex (18:00-09:25 ET)'

    # ─── Monthly Calendar Strategy ──────────────────────────────────

    def _current_month_key(self) -> str:
        """e.g. '2026-04'"""
        return datetime.now(ET).strftime('%Y-%m')

    def _needs_refresh(self) -> bool:
        """Check if we need to fetch new calendar data."""
        now = datetime.now(timezone.utc)
        month_key = self._current_month_key()

        # Different month → always refresh
        if self._monthly_month_key != month_key:
            return True

        # Never fetched
        if self._monthly_fetched_at is None:
            return True

        age = (now - self._monthly_fetched_at).total_seconds()

        # If last fetch was successful → weekly refresh
        if self._last_fetch_success:
            return age >= CALENDAR_WEEKLY_REFRESH_SECONDS

        # If last fetch failed → hourly retry
        if self._last_fetch_attempt:
            since_attempt = (now - self._last_fetch_attempt).total_seconds()
            return since_attempt >= CALENDAR_HOURLY_RETRY_SECONDS

        return True

    async def _ensure_monthly_calendar(self) -> None:
        """Load or refresh the monthly calendar. Non-blocking on failure."""
        if not self._needs_refresh() and self._monthly_events:
            return

        now = datetime.now(timezone.utc)
        month_key = self._current_month_key()
        self._last_fetch_attempt = now

        # Try fetching from API
        events = await self._fetch_monthly_events(month_key)

        if events is not None:
            self._monthly_events = events
            self._monthly_fetched_at = now
            self._monthly_month_key = month_key
            self._last_fetch_success = True
            self._consecutive_failures = 0

            # Persist to MongoDB
            if self.db is not None:
                await self.db['news_calendar_monthly'].update_one(
                    {'month': month_key},
                    {'$set': {
                        'month': month_key,
                        'events': events,
                        'fetched_at': now.isoformat(),
                        'event_count': len(events),
                    }},
                    upsert=True
                )

            # Reactivate blackout if it was suspended
            if self._blackout_suspended:
                logger.info("Calendar restored — reactivating news blackout")
                self._blackout_suspended = False
                self._blackout_suspend_reason = None

            logger.info(
                "Monthly calendar updated: %s, %d US Red events, source=API",
                month_key, len(events)
            )
            return

        # API failed — increment failure counter
        self._consecutive_failures += 1
        self._last_fetch_success = False

        # Try MongoDB fallback if we have no in-memory data
        if not self._monthly_events and self.db is not None:
            cached = await self.db['news_calendar_monthly'].find_one(
                {'month': month_key}, {'_id': 0}
            )
            if cached and cached.get('events'):
                self._monthly_events = cached['events']
                self._monthly_month_key = month_key
                fetched_str = cached.get('fetched_at', '')
                try:
                    from dateutil import parser as dtparser
                    self._monthly_fetched_at = dtparser.parse(fetched_str)
                except Exception:
                    pass
                logger.info(
                    "Monthly calendar loaded from MongoDB fallback: %s, %d events (fetched: %s)",
                    month_key, len(cached['events']), fetched_str
                )

            # Try previous month as last resort (first days of new month)
            if not self._monthly_events:
                now_et = now.astimezone(ET)
                if now_et.day <= 3:
                    prev_month = (now_et.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
                    prev_cached = await self.db['news_calendar_monthly'].find_one(
                        {'month': prev_month}, {'_id': 0}
                    )
                    if prev_cached and prev_cached.get('events'):
                        self._monthly_events = prev_cached['events']
                        self._monthly_month_key = prev_month
                        logger.info("Using previous month calendar as fallback: %s", prev_month)

        # ── Blackout suspension decision ──
        # Rules:
        #   1. If we have cached data < 1 week old → NEVER suspend, just use it and retry hourly
        #   2. Suspend ONLY if: (a) zero cache + first attempt failed, OR
        #      (b) cache is > 1 week old AND retry also failed
        STALE_THRESHOLD = 7 * 24 * 3600  # 1 week

        if self._monthly_events:
            # We have cached data — check its age
            cache_age = (now - self._monthly_fetched_at).total_seconds() if self._monthly_fetched_at else float('inf')

            if cache_age <= STALE_THRESHOLD:
                # Cache is fresh (< 1 week) → use it, no blackout suspension
                if self._blackout_suspended:
                    logger.info("Calendar cache still fresh (%.0fh old) — reactivating blackout", cache_age / 3600)
                    self._blackout_suspended = False
                    self._blackout_suspend_reason = None
                logger.info(
                    "Calendar fetch failed (attempt %d) — using cached data (%d events, %.0fh old). Retry in 1h.",
                    self._consecutive_failures, len(self._monthly_events), cache_age / 3600
                )
            else:
                # Cache is stale (> 1 week) → suspend blackout + alert
                logger.warning(
                    "Calendar fetch failed (attempt %d) — cached data is STALE (%.0fh old, > 168h). "
                    "Suspending blackout. Retry in 1h.",
                    self._consecutive_failures, len(self._monthly_events), cache_age / 3600
                )
                self._blackout_suspended = True
                self._blackout_suspend_reason = (
                    f'Calendario economico desatualizado (>{int(cache_age / 3600)}h). '
                    f'Usando dados antigos. Blackout desativado ate atualizacao bem-sucedida.'
                )
        else:
            # Zero cached data → suspend blackout immediately
            logger.error(
                "Calendar fetch failed (attempt %d) — NO cached data available. "
                "Suspending blackout to avoid blocking trades.",
                self._consecutive_failures
            )
            self._blackout_suspended = True
            self._blackout_suspend_reason = (
                f'Calendario economico indisponivel apos {self._consecutive_failures} tentativas. '
                f'Nenhum dado em cache. Blackout desativado por seguranca.'
            )

    async def _fetch_monthly_events(self, month_key: str) -> Optional[List[dict]]:
        """Fetch all US RED events for the entire month from faireconomy/jblanked."""
        year, month = month_key.split('-')
        year, month = int(year), int(month)

        # Calculate month date range
        from calendar import monthrange
        _, last_day = monthrange(year, month)
        start_date = f'{year}-{month:02d}-01'
        end_date = f'{year}-{month:02d}-{last_day:02d}'

        # Primary: faireconomy.media (weekly calendar, covers ~1 week)
        # We fetch the "thisweek" endpoint and also "nextweek" to cover more
        events = []
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; TradingBot/1.0)'}
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
                urls = [
                    'https://nfs.faireconomy.media/ff_calendar_thisweek.json',
                    'https://nfs.faireconomy.media/ff_calendar_nextweek.json',
                ]
                for url in urls:
                    try:
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            raw = resp.json()
                            if isinstance(raw, list):
                                parsed = self._parse_ff_events(raw, month_key)
                                events.extend(parsed)
                        elif resp.status_code == 429:
                            logger.info("FairEconomy rate limited (429)")
                            break
                    except Exception as e:
                        logger.warning(f"FairEconomy fetch error for {url}: {e}")

            if events:
                # Deduplicate by name+date
                seen = set()
                unique = []
                for ev in events:
                    key = (ev['name'], ev['date'])
                    if key not in seen:
                        seen.add(key)
                        unique.append(ev)
                return unique
        except Exception as e:
            logger.warning(f"FairEconomy monthly fetch error: {e}")

        # Fallback: jblanked with date range (may need API key)
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(
                    'https://www.jblanked.com/news/api/forex-factory/calendar/range/',
                    params={'from': start_date, 'to': end_date, 'currency': 'USD', 'impact': 'High'}
                )
                if resp.status_code == 200:
                    raw = resp.json()
                    if isinstance(raw, list):
                        return self._parse_ff_events(raw, month_key)
        except Exception as e:
            logger.warning(f"JBlanked monthly fetch error: {e}")

        return None

    def _parse_ff_events(self, raw_events: list, month_key: str) -> List[dict]:
        """Parse ForexFactory events, filter to US RED only and given month."""
        events = []
        for ev in raw_events:
            country = (ev.get('country') or ev.get('Currency') or ev.get('currency') or '').upper()
            impact = (ev.get('impact') or ev.get('Impact') or '').lower()
            name = ev.get('title') or ev.get('Name') or ev.get('name') or 'Unknown'
            date_str = ev.get('date') or ev.get('Date') or ''

            # Only US RED (High impact)
            if country != 'USD':
                continue
            if impact != 'high':
                continue

            # Parse datetime
            utc_str = None
            date_only = ''
            time_str = ''
            try:
                from dateutil import parser as dtparser
                ev_dt = dtparser.parse(date_str)
                if ev_dt.tzinfo is None:
                    ev_dt = ev_dt.replace(tzinfo=ET)
                ev_et = ev_dt.astimezone(ET)
                ev_month = ev_et.strftime('%Y-%m')
                if ev_month != month_key:
                    continue
                date_only = ev_et.strftime('%Y-%m-%d')
                time_str = ev_et.strftime('%I:%M%p').lstrip('0').lower()
                utc_str = ev_dt.astimezone(timezone.utc).isoformat()
            except Exception:
                # Try to extract date from string
                if month_key[:7] not in date_str:
                    continue
                date_only = date_str[:10]

            events.append({
                'name': name,
                'currency': 'USD',
                'impact': 'High',
                'date': date_only,
                'time': time_str or (ev.get('time') or ev.get('Time') or ''),
                'utc': utc_str,
                'forecast': ev.get('forecast') or ev.get('Forecast') or '',
                'previous': ev.get('previous') or ev.get('Previous') or '',
            })

        return events

    # ─── Public API ─────────────────────────────────────────────────

    async def get_news_blackouts(self, date_str: Optional[str] = None) -> List[dict]:
        """
        Get economic news events for a date. Uses monthly calendar with
        weekly refresh and hourly retry on failure.
        """
        if date_str is None:
            date_str = datetime.now(ET).strftime('%Y-%m-%d')

        # Ensure monthly calendar is loaded/refreshed
        await self._ensure_monthly_calendar()

        # Filter monthly events for the requested date
        daily_events = [
            ev for ev in self._monthly_events
            if ev.get('date', '') == date_str
        ]

        return daily_events

    def check_news_blackout(self, events: List[dict],
                            minutes_before: int = 15,
                            minutes_after: int = 15,
                            dt: Optional[datetime] = None) -> Tuple[bool, Optional[dict]]:
        """
        Check if current time falls within a news blackout window.
        Returns (is_blackout, event_info).
        
        If blackout is suspended (calendar unavailable), returns (False, None)
        with suspend info available via get_calendar_status().
        """
        if dt is None:
            dt = datetime.now(timezone.utc)

        # If blackout suspended due to data unavailability, never block trades
        if self._blackout_suspended:
            return False, None

        for ev in events:
            utc_str = ev.get('utc')
            if not utc_str:
                continue
            try:
                event_time = datetime.fromisoformat(utc_str)
                blackout_start = event_time - timedelta(minutes=minutes_before)
                blackout_end = event_time + timedelta(minutes=minutes_after)

                if blackout_start <= dt <= blackout_end:
                    remaining = (blackout_end - dt).total_seconds() / 60
                    return True, {
                        'event': ev['name'],
                        'impact': ev['impact'],
                        'event_time_utc': utc_str,
                        'blackout_start_utc': blackout_start.isoformat(),
                        'blackout_end_utc': blackout_end.isoformat(),
                        'minutes_remaining': round(remaining, 1),
                    }
            except Exception:
                continue

        return False, None

    def get_upcoming_events(self, events: List[dict], hours_ahead: int = 4,
                            dt: Optional[datetime] = None) -> List[dict]:
        if dt is None:
            dt = datetime.now(timezone.utc)
        cutoff = dt + timedelta(hours=hours_ahead)

        upcoming = []
        for ev in events:
            utc_str = ev.get('utc')
            if not utc_str:
                continue
            try:
                event_time = datetime.fromisoformat(utc_str)
                if dt <= event_time <= cutoff:
                    minutes_until = (event_time - dt).total_seconds() / 60
                    upcoming.append({**ev, 'minutes_until': round(minutes_until, 1)})
            except Exception:
                continue

        return sorted(upcoming, key=lambda x: x.get('minutes_until', 9999))

    def get_calendar_status(self) -> dict:
        """Return full calendar health status for the dashboard."""
        now = datetime.now(timezone.utc)
        age_s = (now - self._monthly_fetched_at).total_seconds() if self._monthly_fetched_at else None
        attempt_ago_s = (now - self._last_fetch_attempt).total_seconds() if self._last_fetch_attempt else None

        return {
            'month': self._monthly_month_key,
            'event_count': len(self._monthly_events),
            'fetched_at': self._monthly_fetched_at.isoformat() if self._monthly_fetched_at else None,
            'age_seconds': round(age_s) if age_s else None,
            'last_fetch_success': self._last_fetch_success,
            'last_attempt_ago_s': round(attempt_ago_s) if attempt_ago_s else None,
            'consecutive_failures': self._consecutive_failures,
            'blackout_suspended': self._blackout_suspended,
            'blackout_suspend_reason': self._blackout_suspend_reason,
            'refresh_strategy': 'weekly' if self._last_fetch_success else 'hourly_retry',
            'next_refresh_in_s': self._next_refresh_seconds(),
        }

    def _next_refresh_seconds(self) -> Optional[int]:
        if self._monthly_fetched_at is None:
            return 0
        now = datetime.now(timezone.utc)
        if self._last_fetch_success:
            next_at = self._monthly_fetched_at + timedelta(seconds=CALENDAR_WEEKLY_REFRESH_SECONDS)
        else:
            ref = self._last_fetch_attempt or self._monthly_fetched_at
            next_at = ref + timedelta(seconds=CALENDAR_HOURLY_RETRY_SECONDS)
        remaining = (next_at - now).total_seconds()
        return max(0, round(remaining))
