"""
DataBento Live WebSocket Service
=================================
Background task that connects to DataBento Live API,
receives MNQ+MES trades in real-time, and maintains
rolling OFI/Absorption/CVD/TICK calculations in-memory.

Architecture:
    FastAPI startup → asyncio.create_task(live_service.start())
    DataBento Live WS → callback per trade → ring buffer → calc every N seconds
    V3 Engine reads live_service.get_live_data(symbol) → zero latency
"""

import asyncio
import logging
import os
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger("live_data_service")

# Symbols to subscribe (continuous front-month contracts)
LIVE_SYMBOLS = {
    'MNQ': 'MNQ.v.0',
    'MES': 'MES.v.0',
}

TICK_SIZES = {
    'MNQ': 0.25,
    'MES': 0.25,
}


class LiveTradeRecord:
    """Lightweight trade record from DataBento Live"""
    __slots__ = ('symbol', 'side', 'size', 'price', 'ts')

    def __init__(self, symbol: str, side: str, size: float, price: float, ts: int):
        self.symbol = symbol
        self.side = side
        self.size = size
        self.price = price
        self.ts = ts


class SymbolBuffer:
    """Ring buffer + rolling calculations for a single symbol"""

    def __init__(self, symbol: str, max_trades: int = 250_000):
        self.symbol = symbol
        self.trades = deque(maxlen=max_trades)
        self.tick_size = TICK_SIZES.get(symbol, 0.25)

        # Rolling accumulators (updated on each trade)
        self.total_buy_vol = 0.0
        self.total_sell_vol = 0.0
        self.total_trades = 0
        self.uptick = 0
        self.downtick = 0
        self.cvd = 0.0
        self.last_price = 0.0

        # Period price range — acumula o low/high de TODOS os ticks desde a última
        # chamada a consume_period_range(). Usado pelo monitor de posições para detectar
        # cruzamentos de SL/TP entre ciclos de snapshot, sem perder nenhum tick.
        self._period_low:  float = float('inf')
        self._period_high: float = float('-inf')

        # Computed indicators (updated every calc cycle)
        self.ofi_fast = 0.0
        self.ofi_slow = 0.0
        self.absorption_flag = False
        self.absorption_side = 'NONE'
        self.tick_index = 0
        self.cvd_trend = 'NEUTRAL'
        self.last_calc_time = datetime.now(timezone.utc)

        # Tape speed — trades/segundo na janela de cálculo actual
        # EMA fast (α=0.35, memória efectiva ≈6s a ciclo de 2s) vs slow (α=0.06, ≈33s)
        # speed_ratio = ema_fast / ema_slow: >1.4 = tape acelerado, <0.9 = tape a secar
        self.tape_speed: float = 0.0       # trades/seg desde o último ciclo de cálculo
        self.ema_speed_fast: float = 0.0   # EMA rápida da velocidade
        self.ema_speed_slow: float = 0.0   # EMA lenta da velocidade (baseline)
        self.speed_ratio: float = 1.0      # ema_fast / ema_slow
        self._trades_since_last_calc: int = 0
        self._last_calc_ts: Optional[datetime] = None

        # Status
        self.connected = False
        self.trade_count_since_start = 0
        self.last_trade_time = None

    def add_trade(self, side: str, size: float, price: float, ts: int):
        """Add a single trade to the buffer (called on each live tick)"""
        self.trades.append(LiveTradeRecord(self.symbol, side, size, price, ts))
        self.total_trades += 1
        self.trade_count_since_start += 1
        self._trades_since_last_calc += 1
        self.last_price = price
        self.last_trade_time = datetime.now(timezone.utc)

        # Acumula range do período para detecção de SL/TP tick-a-tick
        if price < self._period_low:
            self._period_low  = price
        if price > self._period_high:
            self._period_high = price

        if side == 'B':
            self.total_buy_vol += size
            self.uptick += 1
            self.cvd += size
        elif side == 'A':
            self.total_sell_vol += size
            self.downtick += 1
            self.cvd -= size

    def consume_period_range(self) -> tuple:
        """Retorna (period_low, period_high, last_price) dos ticks desde a última chamada
        e reset dos acumuladores. O monitor de posições chama isto a cada ciclo — garante
        que nenhum tick fica por verificar, mesmo entre janelas de 30s do snapshot."""
        low  = self._period_low  if self._period_low  != float('inf')  else self.last_price
        high = self._period_high if self._period_high != float('-inf') else self.last_price
        self._period_low  = float('inf')
        self._period_high = float('-inf')
        return low, high, self.last_price

    # Alphas das EMAs de velocidade do tape
    _EMA_SPEED_FAST_ALPHA: float = 0.35   # memória efectiva ≈ 3 ciclos ≈ 6s a 2s/ciclo
    _EMA_SPEED_SLOW_ALPHA: float = 0.06   # memória efectiva ≈ 17 ciclos ≈ 33s a 2s/ciclo

    # ── Janelas temporais para OFI (time-based) ──────────────────────────────
    # fast:slow = 1:5 em tempo (30s:150s) — dentro da proporção natural 1:4–1:6 para scalp.
    # 300s (1:10) capturava contexto de mercado 3× além do hold máximo (1–3 min),
    # convergindo para zero mais fortemente via lei dos grandes números e invalidando
    # os thresholds 0.55/0.35 calibrados implicitamente para janela de ~120s.
    # 150s mantém o slow dentro do horizonte de relevância do sistema.
    # Fallback count-based com WARNING quando ts=0: 500 fast / 1000 slow.
    # (1000 trades ≈ natural a ~7 trades/s em 150s — mais distinto que fallback 2000.)
    OFI_FAST_WINDOW_SECS: float = 30.0    # 30s — momentum de entrada imediato
    OFI_SLOW_WINDOW_SECS: float = 150.0   # 150s (2.5 min) — bias direcional recente

    def _time_window(self, trades_list: list, window_secs: float, fallback_count: int) -> list:
        """Filtra trades por janela de tempo. Itera do fim até ao cutoff (O(result)).
        Fallback count-based com WARNING quando timestamps ausentes (ts=0)."""
        if not trades_list:
            return trades_list
        last_ts = trades_list[-1].ts
        if last_ts <= 0:
            logger.warning(
                f"[{self.symbol}] OFI: ts=0 detectado — usando fallback count-based "
                f"({fallback_count} trades). Dados sintéticos ou corrupção de buffer."
            )
            n = min(fallback_count, len(trades_list))
            return trades_list[-n:]
        cutoff_ns = last_ts - int(window_secs * 1_000_000_000)
        result = []
        for t in reversed(trades_list):
            if t.ts < cutoff_ns:
                break
            result.append(t)
        if not result:
            n = min(fallback_count, len(trades_list))
            return trades_list[-n:]
        result.reverse()
        return result

    def calculate_indicators(self):
        """Recalculate OFI/Absorption/TapeSpeed from buffer (called every 1-5s)"""
        now = datetime.now(timezone.utc)
        trades_list = list(self.trades)
        total = len(trades_list)
        if total == 0:
            self._last_calc_ts = now
            self._trades_since_last_calc = 0
            return

        # OFI Fast: janela de 60s (fallback: 500 trades se ts indisponível)
        fast_window = self._time_window(trades_list, self.OFI_FAST_WINDOW_SECS, 500)
        fast_buy = sum(t.size for t in fast_window if t.side == 'B')
        fast_sell = sum(t.size for t in fast_window if t.side == 'A')
        fast_total = fast_buy + fast_sell
        self.ofi_fast = round((fast_buy - fast_sell) / fast_total, 4) if fast_total > 0 else 0.0

        # OFI Slow: janela de 150s / 2.5 min (fallback: 1000 trades se ts=0)
        slow_window = self._time_window(trades_list, self.OFI_SLOW_WINDOW_SECS, 1000)
        slow_buy = sum(t.size for t in slow_window if t.side == 'B')
        slow_sell = sum(t.size for t in slow_window if t.side == 'A')
        slow_total = slow_buy + slow_sell
        self.ofi_slow = round((slow_buy - slow_sell) / slow_total, 4) if slow_total > 0 else 0.0

        # Absorption: OFI extremo (>0.7) com contenção de preço na janela fast (60s)
        # Uses price RANGE (max - min) — correcto para round-trips (fix anterior).
        # Janela agora consistentemente 60s: absorção e OFI fast medem o mesmo período.
        if len(fast_window) >= 10:
            prices = [t.price for t in fast_window]
            price_range_ticks = (max(prices) - min(prices)) / self.tick_size if self.tick_size > 0 else 0

            self.absorption_flag = abs(self.ofi_fast) > 0.7 and price_range_ticks < 2
            if self.absorption_flag:
                self.absorption_side = 'SELL_ABSORBED' if self.ofi_fast > 0 else 'BUY_ABSORBED'
            else:
                self.absorption_side = 'NONE'
        else:
            self.absorption_flag = False
            self.absorption_side = 'NONE'

        # TICK Index
        self.tick_index = self.uptick - self.downtick

        # CVD Trend (compare first half vs second half of slow window)
        if len(slow_window) >= 100:
            mid = len(slow_window) // 2
            first_half_cvd = sum(t.size if t.side == 'B' else -t.size for t in slow_window[:mid])
            second_half_cvd = sum(t.size if t.side == 'B' else -t.size for t in slow_window[mid:])
            self.cvd_trend = 'RISING' if second_half_cvd > first_half_cvd else 'FALLING'
        else:
            self.cvd_trend = 'RISING' if self.cvd > 0 else 'FALLING'

        # ── Tape Speed — velocidade da fita via EMA ratio ─────────────────────
        # Mede trades/segundo no intervalo real entre ciclos de cálculo.
        # Ratio EMA_fast/EMA_slow normaliza pela própria sessão sem threshold fixo:
        #   ratio > 1.4 → tape acelerado (confirma momentum/breakout)
        #   ratio < 0.9 → tape a secar (confirma fades e reversões)
        # Seed na primeira leitura real elimina warmup com 0.0.
        if self._last_calc_ts is not None:
            elapsed_s = (now - self._last_calc_ts).total_seconds()
            if elapsed_s > 0.1:
                current_speed = self._trades_since_last_calc / elapsed_s
                self.tape_speed = round(current_speed, 2)
                if self.ema_speed_fast == 0.0 and self.ema_speed_slow == 0.0:
                    self.ema_speed_fast = current_speed
                    self.ema_speed_slow = current_speed
                    self.speed_ratio = 1.0
                else:
                    self.ema_speed_fast = (
                        self._EMA_SPEED_FAST_ALPHA * current_speed
                        + (1.0 - self._EMA_SPEED_FAST_ALPHA) * self.ema_speed_fast
                    )
                    self.ema_speed_slow = (
                        self._EMA_SPEED_SLOW_ALPHA * current_speed
                        + (1.0 - self._EMA_SPEED_SLOW_ALPHA) * self.ema_speed_slow
                    )
                    # mínimo de 0.5 t/s na EMA lenta para ratio ser numericamente estável
                    if self.ema_speed_slow > 0.5:
                        self.speed_ratio = round(self.ema_speed_fast / self.ema_speed_slow, 3)
                    else:
                        self.speed_ratio = 1.0

        self._last_calc_ts = now
        self._trades_since_last_calc = 0
        self.last_calc_time = now

    def get_data(self) -> Dict[str, Any]:
        """Return current indicator snapshot (read by V3 Engine)"""
        total_vol = self.total_buy_vol + self.total_sell_vol
        buy_pct = round(self.total_buy_vol / total_vol * 100, 1) if total_vol > 0 else 50.0

        return {
            'ofi_fast': self.ofi_fast,
            'ofi_slow': self.ofi_slow,
            'absorption_flag': self.absorption_flag,
            'absorption_side': self.absorption_side,
            'tick_index': self.tick_index,
            'uptick': self.uptick,
            'downtick': self.downtick,
            'cvd': round(self.cvd, 0),
            'cvd_trend': self.cvd_trend,
            'buy_volume': round(self.total_buy_vol, 0),
            'sell_volume': round(self.total_sell_vol, 0),
            'total_volume': round(total_vol, 0),
            'buy_pct': buy_pct,
            'total_trades': self.total_trades,
            'last_price': self.last_price,
            'last_trade_time': self.last_trade_time.isoformat() if self.last_trade_time else None,
            'last_calc_time': self.last_calc_time.isoformat(),
            'buffer_size': len(self.trades),
            'trade_count_since_start': self.trade_count_since_start,
            'source': 'databento_live',
            'connected': self.connected,
            # Tape speed — EMA ratio (fast/slow) normalizado pela sessão
            'tape_speed':     self.tape_speed,
            'ema_speed_fast': round(self.ema_speed_fast, 2),
            'ema_speed_slow': round(self.ema_speed_slow, 2),
            'speed_ratio':    self.speed_ratio,
        }


WATCHDOG_INTERVAL_S = 30     # how often the watchdog checks
WATCHDOG_STALE_LIMIT_S = 90  # force-reconnect if open market + buffer age > this
WATCHDOG_COOLDOWN_S = 120    # minimum time between watchdog-triggered reconnects


class LiveDataService:
    """
    Manages DataBento Live WebSocket connections for MNQ and MES.
    Runs as a FastAPI background task.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.buffers: Dict[str, SymbolBuffer] = {
            sym: SymbolBuffer(sym) for sym in LIVE_SYMBOLS
        }
        self.client = None
        self.running = False
        self.connected = False
        self.calc_interval = 2  # seconds between indicator recalculations
        self._calc_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._ws_task: Optional[asyncio.Task] = None
        self.start_time = None
        self.error_count = 0
        self.last_error = None
        self.reconnect_delay = 5  # seconds
        self._last_watchdog_reconnect: Optional[datetime] = None
        # instrument_id → symbol mapping (populated by symbology callback)
        self._id_map: Dict[int, str] = {}

    def _map_symbol(self, raw_symbol: str) -> Optional[str]:
        """Map DataBento instrument symbol back to our symbol key"""
        raw_upper = raw_symbol.upper()
        for sym, continuous in LIVE_SYMBOLS.items():
            if sym in raw_upper:
                return sym
        return None

    def _on_symbology(self, symbology_msg):
        """Callback for symbology mapping messages from DataBento Live.
        Maps instrument_id → our symbol key (MNQ, MES).
        """
        try:
            mappings = getattr(symbology_msg, 'mappings', [])
            for m in mappings:
                raw_sym = getattr(m, 'raw_symbol', '') or ''
                intervals = getattr(m, 'intervals', [])
                for interval in intervals:
                    symbol = getattr(interval, 'symbol', '') or ''
                    if symbol:
                        our_sym = self._map_symbol(symbol)
                        if our_sym:
                            # Find instrument_id for this symbol from the client
                            logger.info(f"Symbology mapped: {symbol} → {our_sym}")
        except Exception as e:
            logger.warning(f"Symbology callback error: {e}")

    def _on_trade(self, record):
        """Callback for each trade received from DataBento Live.

        DataBento Live sends instrument_id (int) per trade. We map this
        to our symbol key via _id_map, which is populated from the symbology
        mapping messages sent on subscription.
        """
        try:
            side = getattr(record, 'side', None)
            if side is None:
                return

            # Map side: DataBento uses 'A' (ask/sell) and 'B' (bid/buy)
            side_str = str(side)
            if 'A' in side_str:
                side_str = 'A'
            elif 'B' in side_str:
                side_str = 'B'
            else:
                return

            size = float(getattr(record, 'size', 1) or 1)
            price_raw = getattr(record, 'price', 0)
            price = float(price_raw) / 1e9 if price_raw > 1e6 else float(price_raw)
            ts = getattr(record, 'ts_event', 0)

            instrument_id = getattr(record, 'instrument_id', None)

            # Try instrument_id map first (fast path)
            sym = self._id_map.get(instrument_id)

            # Fallback: try raw symbol string
            if not sym:
                raw_symbol = getattr(record, 'symbol', '') or ''
                sym = self._map_symbol(raw_symbol)

            # Fallback: infer from price range (MNQ ~18k-25k, MES ~4k-7k)
            if not sym and price > 0:
                if price > 10000:
                    sym = 'MNQ'
                elif price > 1000:
                    sym = 'MES'

            # Cache the instrument_id mapping once resolved
            if sym and instrument_id is not None and instrument_id not in self._id_map:
                self._id_map[instrument_id] = sym
                logger.info(f"Learned instrument_id mapping: {instrument_id} → {sym} (price={price})")

            if sym and sym in self.buffers:
                self.buffers[sym].add_trade(side_str, size, price, ts)
            # If we still can't resolve, DROP the trade (don't pollute buffers)

        except Exception as e:
            self.error_count += 1
            if self.error_count % 1000 == 1:
                logger.error(f"Trade callback error #{self.error_count}: {e}")

    async def _calc_loop(self):
        """Periodically recalculate indicators for all symbols"""
        _stats_cycle = 0
        while self.running:
            try:
                for buf in self.buffers.values():
                    buf.calculate_indicators()
                _stats_cycle += 1

                # ── Feed health stats a cada ~60s (30 ciclos × calc_interval≈2s) ──
                if _stats_cycle % 30 == 0:
                    _now_stat = datetime.now(timezone.utc)
                    for sym, buf in self.buffers.items():
                        _buf_len  = len(buf.trades)
                        _buf_max  = buf.trades.maxlen or 1
                        _fill_pct = round(_buf_len / _buf_max * 100, 1)
                        _age_s    = (
                            (_now_stat - buf.last_trade_time).total_seconds()
                            if buf.last_trade_time else -1.0
                        )
                        logger.info(
                            "FEED [%s] buf=%d/%d(%.0f%%) last_trade=%.0fs "
                            "ofi_f=%.4f ofi_s=%.4f tape=%.1ft/s ratio=%.2f conn=%s",
                            sym, _buf_len, _buf_max, _fill_pct, _age_s,
                            buf.ofi_fast, buf.ofi_slow,
                            buf.tape_speed, buf.speed_ratio, buf.connected,
                        )

                await asyncio.sleep(self.calc_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Calc loop error: {e}")
                await asyncio.sleep(1)

    async def _watchdog_loop(self):
        """Force-reconnect when market is open but feed has gone stale.

        Problem this solves:
          DataBento keeps the WebSocket CONNECTED during the daily maintenance
          halt (17:00–18:00 ET) but stops sending trades. When Globex reopens
          at 18:00 the client does NOT re-subscribe automatically, so the buffer
          stays stale indefinitely and N3 stays frozen.

        Solution:
          Every WATCHDOG_INTERVAL_S seconds, if the market is open AND every
          symbol buffer is stale for > WATCHDOG_STALE_LIMIT_S, we call
          client.stop() which unblocks wait_for_close() in _connect_and_stream()
          and lets the start() reconnect loop re-establish a fresh connection.
        """
        from services.feed_health import is_cme_market_open

        await asyncio.sleep(WATCHDOG_INTERVAL_S)  # give initial connection time to settle

        while self.running:
            try:
                market = is_cme_market_open()
                if not market["open"]:
                    # Market halted or weekend — silence is expected, do nothing
                    await asyncio.sleep(WATCHDOG_INTERVAL_S)
                    continue

                now = datetime.now(timezone.utc)

                # Enforce cooldown to avoid rapid-fire reconnects
                if self._last_watchdog_reconnect:
                    elapsed = (now - self._last_watchdog_reconnect).total_seconds()
                    if elapsed < WATCHDOG_COOLDOWN_S:
                        await asyncio.sleep(WATCHDOG_INTERVAL_S)
                        continue

                # Check if ALL buffers with past activity are stale
                stale_syms = []
                for sym, buf in self.buffers.items():
                    if not buf.last_trade_time:
                        continue  # never received data — different problem, skip
                    age = (now - buf.last_trade_time).total_seconds()
                    if age > WATCHDOG_STALE_LIMIT_S:
                        stale_syms.append((sym, age))

                if len(stale_syms) == len([b for b in self.buffers.values() if b.last_trade_time]):
                    # Every active buffer is stale — force reconnect
                    worst = max(stale_syms, key=lambda x: x[1])
                    logger.warning(
                        f"Watchdog: feed stale during open market "
                        f"({worst[0]} age={worst[1]:.0f}s > {WATCHDOG_STALE_LIMIT_S}s) — "
                        f"forcing reconnect"
                    )
                    self._last_watchdog_reconnect = now
                    self.reconnect_delay = 5  # reset backoff — this is a scheduled reconnect
                    if self.client:
                        try:
                            self.client.stop()
                        except Exception as e:
                            logger.debug(f"Watchdog client.stop() error (ok): {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}")

            await asyncio.sleep(WATCHDOG_INTERVAL_S)

    async def _connect_and_stream(self):
        """Connect to DataBento Live and stream trades"""
        try:
            import databento as db

            logger.info("Connecting to DataBento Live...")

            # Clear instrument_id map — IDs can change between Globex sessions
            self._id_map.clear()

            self.client = db.Live(
                key=self.api_key,
                reconnect_policy="reconnect",
            )

            # Subscribe to trades for both symbols
            continuous_symbols = list(LIVE_SYMBOLS.values())
            self.client.subscribe(
                dataset="GLBX.MDP3",
                schema="trades",
                stype_in="continuous",
                symbols=continuous_symbols,
            )

            # Add trade callback
            self.client.add_callback(self._on_trade)

            # Pre-populate instrument_id map from client's symbology
            # DataBento logs "added symbology mapping MESM6 to 42005163"
            # We can extract these from client internals after subscribe
            try:
                # Access internal symbology map if available
                sym_map = getattr(self.client, '_sym_map', None) or getattr(self.client, 'symbology_map', None)
                if sym_map:
                    for mapped_sym, iid in sym_map.items():
                        our_sym = self._map_symbol(str(mapped_sym))
                        if our_sym:
                            self._id_map[int(iid)] = our_sym
                            logger.info(f"Pre-loaded instrument_id: {iid} → {our_sym} ({mapped_sym})")
            except Exception as e:
                logger.debug(f"Could not pre-load symbology map: {e} (will learn from first trades)")

            # Mark connected
            self.connected = True
            for buf in self.buffers.values():
                buf.connected = True
            logger.info(f"DataBento Live connected — streaming {continuous_symbols}")

            # Start streaming
            self.client.start()

            # Wait for close (blocks until disconnect)
            await self.client.wait_for_close()

        except Exception as e:
            self.last_error = str(e)
            self.connected = False
            for buf in self.buffers.values():
                buf.connected = False
            logger.error(f"DataBento Live connection error: {e}")

    async def start(self):
        """Start the live data service (called from FastAPI lifespan)"""
        if not self.api_key:
            logger.warning("No DataBento API key — Live data service disabled")
            return

        self.running = True
        self.start_time = datetime.now(timezone.utc)

        # Start indicator calculation loop
        self._calc_task = asyncio.create_task(self._calc_loop())

        # Start watchdog — detects stale feed during open market and forces reconnect
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

        # Connect with auto-reconnect
        while self.running:
            await self._connect_and_stream()

            if self.running:
                # If the watchdog already reset reconnect_delay to 5 (session reconnect),
                # use that; otherwise apply exponential backoff for genuine errors.
                logger.info(f"Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                # Only back off if we're still using the error path (delay > 5)
                if self.reconnect_delay > 5:
                    self.reconnect_delay = min(60, self.reconnect_delay * 1.5)
                else:
                    # Reset backoff after a clean session reconnect
                    self.reconnect_delay = 5
            else:
                break

    async def stop(self):
        """Gracefully stop the live data service"""
        self.running = False

        if self._calc_task:
            self._calc_task.cancel()
            try:
                await self._calc_task
            except asyncio.CancelledError:
                pass

        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        if self.client:
            try:
                self.client.stop()
            except Exception:
                pass

        self.connected = False
        logger.info("LiveDataService stopped")

    def get_live_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get current live indicator data for a symbol (called by V3 Engine)"""
        buf = self.buffers.get(symbol)
        if buf and buf.connected and buf.trade_count_since_start > 0:
            return buf.get_data()
        return None

    def is_live(self, symbol: str) -> bool:
        """Check if live data is available for a symbol"""
        buf = self.buffers.get(symbol)
        if not buf or not buf.connected:
            return False
        # Consider live if we received a trade in the last 30 seconds
        if buf.last_trade_time:
            age = (datetime.now(timezone.utc) - buf.last_trade_time).total_seconds()
            return age < 30
        return False

    def get_status(self) -> Dict[str, Any]:
        """Get overall service status"""
        return {
            'running': self.running,
            'connected': self.connected,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'uptime_seconds': (datetime.now(timezone.utc) - self.start_time).total_seconds() if self.start_time else 0,
            'error_count': self.error_count,
            'last_error': self.last_error,
            'symbols': {
                sym: {
                    'connected': buf.connected,
                    'trades_received': buf.trade_count_since_start,
                    'buffer_size': len(buf.trades),
                    'last_price': buf.last_price,
                    'last_trade_age_s': round((datetime.now(timezone.utc) - buf.last_trade_time).total_seconds(), 1) if buf.last_trade_time else None,
                    'ofi_fast': buf.ofi_fast,
                    'ofi_slow': buf.ofi_slow,
                    'absorption': buf.absorption_flag,
                }
                for sym, buf in self.buffers.items()
            }
        }
