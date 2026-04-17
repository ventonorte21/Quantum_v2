"""
Delta Zonal Ancorado (Anchored Zonal Delta) — V2
=================================================
Motor único com dois modos operacionais, buffers adaptativos via ATR,
Welford Online para Z-Score persistente e Delta Ratio para validacao direcional.

N2_STRUCTURE: Monitora niveis de "massa" (VWAP, POC, VAH, VAL).
  - Buffer = fracao do ATR de M5 (5 minutos)
  - Reset apenas quando preco SAI do buffer para o lado oposto
  - Acumula volume delta enquanto preco orbita dentro do buffer
  - Z-Score via Welford Online (persistente, imune a restart)
  - Delta Ratio = net_delta / total_volume (qualidade direcional)
  - Output: Aceitacao/Rejeicao de territorio + autorizacao para N3

N3_EXTREME: Monitora niveis de "extremo" (Call/Put Wall, ZGL, VWAP +/-3s).
  - Buffer = fracao do ATR de M1 (1 minuto)
  - Micro-zona adaptativa: nivel +/- (fracao x ATR_M1)
  - Absorcao ELASTICA: price_range <= 0.25 x ATR(M1) (nao mais 2 ticks fixos)
  - Detecta absorcao institucional em tempo real
  - Output: Sinal de absorcao + timing de entrada

Buffer fracoes (Opcao C — topo do range, conservador):
  N2: VWAP/POC = 1.00xATR(M5), VAH/VAL = 0.75xATR(M5)
  N3: VWAP+/-3s = 0.50xATR(M1), ZGL = 0.50xATR(M1), Gamma Walls = 0.25xATR(M1)
"""

import logging
import math
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ── ATR Fraction Configuration (Option C — Top of Range) ──
N2_ATR_FRACTIONS = {
    'vwap': 1.00,
    'poc': 1.00,
    'vah': 0.75,
    'val': 0.75,
}

N3_ATR_FRACTIONS = {
    'vwap_pos3s': 0.50,
    'vwap_neg3s': 0.50,
    'zgl': 0.50,
    'call_wall': 0.25,
    'put_wall': 0.25,
}

# ── Elastic Absorption: fraction of ATR(M1) for max price range ──
N3_ABSORPTION_ATR_FRACTION = 0.25


# ══════════════════════════════════════════════════════════════
# Welford Online Algorithm — Persistent Z-Score
# ══════════════════════════════════════════════════════════════

class WelfordAccumulator:
    """Online algorithm for computing mean and variance with exponential decay.

    Uses an exponential forgetting factor (alpha) so that the distribution
    adapts to regime shifts (e.g., VIX 12 → VIX 30). With alpha ≈ 0.03-0.05,
    the effective window is ~20-33 observations — roughly 1 month of daily
    session-level interactions.

    Stores only 3 values (n, mean, m2) per level. Persists to MongoDB
    and warm-starts on restart — no data loss between server restarts.
    """

    # Exponential decay factor: ~0.04 ≈ effective window of ~25 sessions
    DEFAULT_ALPHA = 0.04

    def __init__(self, alpha: float = DEFAULT_ALPHA):
        self._stats: Dict[str, Dict[str, float]] = {}
        self._alpha = alpha

    def _key(self, symbol: str, level_name: str, mode: str) -> str:
        return f"{symbol}_{mode}_{level_name}"

    def get_stats(self, symbol: str, level_name: str, mode: str) -> Dict[str, float]:
        key = self._key(symbol, level_name, mode)
        if key not in self._stats:
            self._stats[key] = {"n": 0, "mean": 0.0, "m2": 0.0}
        return self._stats[key]

    def update(self, symbol: str, level_name: str, mode: str, value: float):
        """Push a new observation with exponential forgetting.

        For the first 10 observations, uses standard Welford (cold start phase).
        After that, applies exponential decay so recent observations carry more weight.
        This prevents historical VIX-12 volumes from dominating Z-Scores when VIX is 30.
        """
        stats = self.get_stats(symbol, level_name, mode)
        stats["n"] += 1
        n = stats["n"]

        if n <= 10:
            # Cold start: standard Welford (need enough data before decaying)
            delta = value - stats["mean"]
            stats["mean"] += delta / n
            delta2 = value - stats["mean"]
            stats["m2"] += delta * delta2
        else:
            # Exponential Welford: weight recent observations more heavily
            alpha = self._alpha
            delta = value - stats["mean"]
            stats["mean"] = (1 - alpha) * stats["mean"] + alpha * value
            delta2 = value - stats["mean"]
            stats["m2"] = (1 - alpha) * stats["m2"] + alpha * delta * delta2

    def compute_zscore(self, symbol: str, level_name: str, mode: str, current_value: float) -> float:
        """Compute Z-Score of current_value vs accumulated (exponentially-weighted) distribution."""
        stats = self.get_stats(symbol, level_name, mode)
        if stats["n"] < 3:
            return 0.0
        # For exponential Welford, m2 approximates the exponentially-weighted variance directly
        if stats["n"] <= 10:
            variance = stats["m2"] / (stats["n"] - 1)
        else:
            # Exponential: m2 already represents the decayed variance estimate
            variance = stats["m2"]
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std < 1.0:
            return 0.0
        return round((current_value - stats["mean"]) / std, 2)

    def get_baseline(self, symbol: str, level_name: str, mode: str) -> float:
        """Volume baseline = mean * 0.5 (minimum meaningful volume)."""
        stats = self.get_stats(symbol, level_name, mode)
        return max(stats["mean"] * 0.5, 10.0)

    def get_mean(self, symbol: str, level_name: str, mode: str) -> float:
        stats = self.get_stats(symbol, level_name, mode)
        return stats["mean"]

    def load_from_db(self, docs: List[Dict]):
        """Warm-start from MongoDB documents."""
        for doc in docs:
            key = doc.get("key", "")
            if key:
                self._stats[key] = {
                    "n": doc.get("n", 0),
                    "mean": doc.get("mean", 0.0),
                    "m2": doc.get("m2", 0.0),
                }
        logger.info(f"Welford warm-start: loaded {len(docs)} accumulators")

    def export_for_db(self) -> List[Dict]:
        """Export all accumulators for MongoDB persistence."""
        docs = []
        for key, stats in self._stats.items():
            if stats["n"] > 0:
                docs.append({
                    "key": key,
                    "n": stats["n"],
                    "mean": round(stats["mean"], 4),
                    "m2": round(stats["m2"], 4),
                    "updated_at": datetime.now(timezone.utc),
                })
        return docs


# ══════════════════════════════════════════════════════════════
# Delta Zonal Service (V2)
# ══════════════════════════════════════════════════════════════

class DeltaZonalService:
    """
    Processes DataBento trades against reference levels and computes:
    - Volume delta per zone (above/below each level)
    - Z-Score via Welford Online (persistent, restart-safe)
    - Delta Ratio (directional quality: net_delta / total_volume)
    - Absorption detection at extreme levels (N3) with elastic price range
    - ATR-adaptive buffers per level type
    """

    _cache: Dict[str, Any] = {}
    _cache_time: Dict[str, datetime] = {}
    CACHE_DURATION = 30

    # Cap: use only the most recent N trades to bound CPU time.
    # At 100 trades/s, 5000 trades = ~50 seconds of recent data — more than
    # enough for real-time flow analysis. Prevents O(100k × 12 levels) spikes.
    MAX_TRADES = 5000

    def __init__(self):
        self.welford = WelfordAccumulator()

    # ── ATR Computation from Trades ──

    @classmethod
    def compute_atr_from_trades(cls, trades: List[Dict], interval_seconds: int, window: int = 14) -> float:
        """Aggregate trades into OHLC candles and compute ATR."""
        if not trades or len(trades) < 2:
            return 0.0

        # Cap to most recent MAX_TRADES to bound CPU time
        if len(trades) > cls.MAX_TRADES:
            trades = trades[-cls.MAX_TRADES:]

        interval_ns = interval_seconds * 1_000_000_000
        candles = {}

        for trade in trades:
            price = trade['price']
            ts = trade.get('ts', 0)
            if ts <= 0:
                continue
            bucket = (ts // interval_ns) * interval_ns

            if bucket not in candles:
                candles[bucket] = {
                    'open': price, 'high': price,
                    'low': price, 'close': price,
                }
            else:
                c = candles[bucket]
                c['high'] = max(c['high'], price)
                c['low'] = min(c['low'], price)
                c['close'] = price

        sorted_keys = sorted(candles.keys())
        if len(sorted_keys) < 3:
            return 0.0

        sorted_candles = [candles[k] for k in sorted_keys]

        true_ranges = []
        for i in range(1, len(sorted_candles)):
            h = sorted_candles[i]['high']
            low = sorted_candles[i]['low']
            prev_c = sorted_candles[i - 1]['close']
            tr = max(h - low, abs(h - prev_c), abs(low - prev_c))
            true_ranges.append(tr)

        if not true_ranges:
            return 0.0

        effective_window = min(window, len(true_ranges))
        return round(float(np.mean(true_ranges[-effective_window:])), 4)

    # ══════════════════════════════════════════════════════════════
    # N2 — STRUCTURAL (VWAP, POC, VAH, VAL) with ATR(M5) buffers
    #   + Delta Ratio + Welford Z-Score
    # ══════════════════════════════════════════════════════════════

    def compute_n2_structure(
        self,
        trades: List[Dict],
        levels: Dict[str, float],
        tick_size: float = 0.25,
        atr_m5: float = 0.0,
        symbol: str = "MNQ",
    ) -> Dict[str, Any]:
        """
        N2 Delta Zonal with ATR(M5)-based buffer per level.
        Now includes Delta Ratio and Welford-based Z-Score.
        """
        if not trades or not levels:
            return self._empty_n2_result()

        # Cap to most recent MAX_TRADES — flow analysis needs recency, not history
        if len(trades) > self.MAX_TRADES:
            trades = trades[-self.MAX_TRADES:]

        fallback_buffer = tick_size * 8
        result = {}

        for level_name, level_price in levels.items():
            if not level_price or level_price <= 0:
                continue

            fraction = N2_ATR_FRACTIONS.get(level_name, 0.75)
            buffer = atr_m5 * fraction if atr_m5 > 0 else fallback_buffer
            upper_bound = level_price + buffer
            lower_bound = level_price - buffer

            buy_volume_above = 0
            sell_volume_above = 0
            buy_volume_below = 0
            sell_volume_below = 0
            buy_volume_buffer = 0
            sell_volume_buffer = 0
            last_zone = None
            crossing_count = 0
            first_ts_after_cross = None

            for trade in trades:
                price = trade['price']
                size = trade['size']
                side = trade['side']
                ts = trade.get('ts', 0)

                if price > upper_bound:
                    current_zone = 'above'
                elif price < lower_bound:
                    current_zone = 'below'
                else:
                    if side == 'B':
                        buy_volume_buffer += size
                    elif side == 'A':
                        sell_volume_buffer += size
                    continue

                if last_zone is not None and current_zone != last_zone:
                    total_vol = (buy_volume_above + sell_volume_above +
                                 buy_volume_below + sell_volume_below +
                                 buy_volume_buffer + sell_volume_buffer)
                    if total_vol > 0:
                        # Update Welford with crossing volume
                        self.welford.update(symbol, level_name, 'n2', total_vol)

                    buy_volume_above = 0
                    sell_volume_above = 0
                    buy_volume_below = 0
                    sell_volume_below = 0
                    buy_volume_buffer = 0
                    sell_volume_buffer = 0
                    crossing_count += 1
                    first_ts_after_cross = ts

                last_zone = current_zone

                if price > level_price:
                    if side == 'B':
                        buy_volume_above += size
                    elif side == 'A':
                        sell_volume_above += size
                else:
                    if side == 'B':
                        buy_volume_below += size
                    elif side == 'A':
                        sell_volume_below += size

            # ── Compute metrics ──
            total_above = buy_volume_above + sell_volume_above
            total_below = buy_volume_below + sell_volume_below
            total_buffer = buy_volume_buffer + sell_volume_buffer
            total_volume = total_above + total_below + total_buffer

            delta_above = buy_volume_above - sell_volume_above
            delta_below = buy_volume_below - sell_volume_below
            delta_buffer = buy_volume_buffer - sell_volume_buffer
            net_delta = delta_above + delta_below + delta_buffer

            total_buy = buy_volume_above + buy_volume_below + buy_volume_buffer
            buy_pct = round(total_buy / total_volume * 100, 1) if total_volume > 0 else 50.0
            sell_pct = round(100 - buy_pct, 1)

            # Welford Z-Score (persistent)
            zscore = self.welford.compute_zscore(symbol, level_name, 'n2', total_volume)

            # Volume Baseline from Welford mean
            baseline = self.welford.get_baseline(symbol, level_name, 'n2')

            # Delta Ratio = net_delta / total_volume (directional quality)
            delta_ratio = round(net_delta / total_volume, 4) if total_volume > 0 else 0.0

            time_since_last_cross = 0
            if first_ts_after_cross and trades[-1].get('ts', 0):
                time_since_last_cross = round((trades[-1]['ts'] - first_ts_after_cross) / 1e9, 0)

            last_price = trades[-1]['price'] if trades else 0
            if last_price > upper_bound:
                position = 'above'
            elif last_price < lower_bound:
                position = 'below'
            else:
                position = 'in_buffer'

            # Volume significance check
            volume_significant = total_volume > baseline

            result[level_name] = {
                'level_price': round(level_price, 2),
                'buffer': round(buffer, 2),
                'buffer_range': f'{round(lower_bound, 2)} - {round(upper_bound, 2)}',
                'atr_fraction': fraction,
                'position': position,
                'delta_above': round(delta_above, 0),
                'delta_below': round(delta_below, 0),
                'delta_buffer': round(delta_buffer, 0),
                'net_delta': round(net_delta, 0),
                'total_volume': round(total_volume, 0),
                'volume_in_buffer': round(total_buffer, 0),
                'buy_pct': buy_pct,
                'sell_pct': sell_pct,
                'zscore': zscore,
                'delta_ratio': delta_ratio,
                'volume_baseline': round(baseline, 0),
                'volume_significant': volume_significant,
                'crossing_count': crossing_count,
                'seconds_since_cross': time_since_last_cross,
                'signal': self._n2_signal(net_delta, zscore, delta_ratio, position, total_volume, volume_significant),
            }

        return {
            'mode': 'N2_STRUCTURE',
            'atr_m5': round(atr_m5, 4) if atr_m5 > 0 else None,
            'levels': result,
            'summary': self._n2_summary(result),
        }

    @staticmethod
    def _n2_signal(net_delta: float, zscore: float, delta_ratio: float,
                   position: str, volume: float, volume_significant: bool) -> str:
        if not volume_significant:
            return 'INSUFFICIENT_VOLUME'
        if zscore > 2.0 and delta_ratio > 0.20:
            return 'STRONG_BULLISH_ACCEPTANCE'
        if zscore > 2.0 and delta_ratio < -0.20:
            return 'STRONG_BEARISH_ACCEPTANCE'
        if zscore > 1.0 and delta_ratio > 0.15:
            return 'BULLISH_ACCEPTANCE'
        if zscore > 1.0 and delta_ratio < -0.15:
            return 'BEARISH_ACCEPTANCE'
        if zscore > 1.0 and abs(delta_ratio) > 0.25:
            return 'REJECTION'
        if abs(zscore) < 0.5:
            return 'LOW_ACTIVITY'
        if delta_ratio > 0.10:
            return 'MILD_BULLISH'
        if delta_ratio < -0.10:
            return 'MILD_BEARISH'
        return 'NEUTRAL'

    @staticmethod
    def _n2_summary(levels: Dict) -> Dict:
        if not levels:
            return {
                'status': 'NO_DATA', 'total_delta': 0,
                'bullish_levels': 0, 'bearish_levels': 0,
                'max_zscore': 0, 'max_zscore_level': '',
                'avg_delta_ratio': 0.0, 'interpretation': 'Sem dados de fluxo',
            }

        bullish = 0
        bearish = 0
        total_delta = 0
        max_zscore = 0
        max_zscore_level = ''
        ratios = []

        for name, data in levels.items():
            if data['net_delta'] > 0:
                bullish += 1
            elif data['net_delta'] < 0:
                bearish += 1
            total_delta += data['net_delta']
            ratios.append(data.get('delta_ratio', 0.0))
            if abs(data['zscore']) > abs(max_zscore):
                max_zscore = data['zscore']
                max_zscore_level = name

        avg_ratio = round(sum(ratios) / len(ratios), 4) if ratios else 0.0

        if bullish > bearish and total_delta > 0:
            status = 'BULLISH_CONFIRMED'
            interp = f'Fluxo comprador dominante em {bullish}/{len(levels)} niveis. Delta total: +{total_delta:.0f}'
        elif bearish > bullish and total_delta < 0:
            status = 'BEARISH_CONFIRMED'
            interp = f'Fluxo vendedor dominante em {bearish}/{len(levels)} niveis. Delta total: {total_delta:.0f}'
        else:
            status = 'MIXED'
            interp = f'Fluxo misto. Bullish: {bullish}, Bearish: {bearish}, Delta: {total_delta:.0f}'

        return {
            'status': status,
            'total_delta': round(total_delta, 0),
            'bullish_levels': bullish,
            'bearish_levels': bearish,
            'max_zscore': max_zscore,
            'max_zscore_level': max_zscore_level,
            'avg_delta_ratio': avg_ratio,
            'interpretation': interp,
        }

    # ══════════════════════════════════════════════════════════════
    # N3 — EXTREME (Gamma Walls, ZGL, VWAP +/-3s) with ATR(M1)
    #   + Elastic Absorption (0.25 x ATR instead of 2 ticks)
    # ══════════════════════════════════════════════════════════════

    def compute_n3_extreme(
        self,
        trades: List[Dict],
        levels: Dict[str, float],
        tick_size: float = 0.25,
        atr_m1: float = 0.0,
        symbol: str = "MNQ",
        zone_ttl_ns: int = 30_000_000_000,  # 30 seconds in nanoseconds
    ) -> Dict[str, Any]:
        """
        N3 Delta Zonal with ATR(M1)-based micro-zone per extreme level.
        Absorption is ELASTIC: price_range <= 0.25 x ATR(M1).

        Time-Decay Accumulator (TTL 30s):
        When price exits the micro-zone, the accumulator is NOT reset immediately.
        It stays alive for `zone_ttl_ns` (30s). If price re-enters within the TTL,
        the volumes are aggregated seamlessly — stitching institutional Iceberg/Reload
        waves into a single absorption tape. Only resets after 30s of sustained absence.
        """
        if not trades or not levels:
            return self._empty_n3_result()

        # Cap to most recent MAX_TRADES — absorption detection is a real-time signal
        if len(trades) > self.MAX_TRADES:
            trades = trades[-self.MAX_TRADES:]

        fallback_range = tick_size * 4
        result = {}

        # Elastic absorption limit (replaces fixed 2-tick rule)
        absorption_price_limit = N3_ABSORPTION_ATR_FRACTION * atr_m1 if atr_m1 > 0 else tick_size * 2

        for level_name, level_price in levels.items():
            if not level_price or level_price <= 0:
                continue

            fraction = N3_ATR_FRACTIONS.get(level_name, 0.25)
            micro_zone_range = atr_m1 * fraction if atr_m1 > 0 else fallback_range

            zone_low = level_price - micro_zone_range
            zone_high = level_price + micro_zone_range

            in_zone = False
            zone_trades = []
            zone_start_ts = None
            touch_count = 0
            buy_volume = 0
            sell_volume = 0
            price_high_in_zone = 0
            price_low_in_zone = float('inf')
            last_exit_ts = None  # TTL: timestamp when price last left the zone

            for trade in trades:
                price = trade['price']
                ts = trade.get('ts', 0)

                if zone_low <= price <= zone_high:
                    if not in_zone:
                        # Re-entering zone: check if TTL expired
                        if last_exit_ts is not None and (ts - last_exit_ts) > zone_ttl_ns:
                            # TTL expired: commit old accumulator to Welford and reset
                            total_vol = buy_volume + sell_volume
                            if total_vol > 0:
                                self.welford.update(symbol, level_name, 'n3', total_vol)
                            buy_volume = 0
                            sell_volume = 0
                            zone_trades = []
                            price_high_in_zone = price
                            price_low_in_zone = price
                            zone_start_ts = ts
                        elif last_exit_ts is not None:
                            # Re-entering within TTL: stitch waves (no reset)
                            pass
                        else:
                            # First entry ever
                            zone_start_ts = ts
                            price_high_in_zone = price
                            price_low_in_zone = price

                        in_zone = True
                        touch_count += 1
                        last_exit_ts = None

                    zone_trades.append(trade)
                    price_high_in_zone = max(price_high_in_zone, price)
                    price_low_in_zone = min(price_low_in_zone, price)

                    if trade['side'] == 'B':
                        buy_volume += trade['size']
                    elif trade['side'] == 'A':
                        sell_volume += trade['size']
                else:
                    if in_zone:
                        # Price exited zone: start TTL countdown (don't reset yet)
                        last_exit_ts = ts
                        in_zone = False

            total_volume = buy_volume + sell_volume
            net_delta = buy_volume - sell_volume
            buy_pct = round(buy_volume / total_volume * 100, 1) if total_volume > 0 else 50.0
            sell_pct = round(100 - buy_pct, 1)

            price_range_abs = (price_high_in_zone - price_low_in_zone) if price_low_in_zone < float('inf') else 0
            price_range_ticks = round(price_range_abs / tick_size, 1) if tick_size > 0 else 0

            # Welford Z-Score
            zscore = self.welford.compute_zscore(symbol, level_name, 'n3', total_volume)
            baseline = self.welford.get_baseline(symbol, level_name, 'n3')

            last_ts = zone_trades[-1]['ts'] if zone_trades else 0
            time_in_zone = round((last_ts - zone_start_ts) / 1e9, 1) if zone_start_ts and last_ts else 0

            # ── ELASTIC Absorption Detection ──
            # OLD: volume > 50 AND |zscore| > 1.0 AND price_range <= 2 ticks
            # NEW: volume > baseline AND |zscore| > 1.0 AND price_range <= 0.25 x ATR(M1)
            volume_significant = total_volume > baseline
            high_volume = volume_significant and abs(zscore) > 1.0
            price_contained = price_range_abs <= absorption_price_limit
            absorption_detected = high_volume and price_contained and total_volume > 0

            absorption_type = 'NONE'
            if absorption_detected:
                if net_delta < 0 and price_range_abs <= absorption_price_limit * 0.5:
                    absorption_type = 'BUY_ABSORPTION'
                elif net_delta > 0 and price_range_abs <= absorption_price_limit * 0.5:
                    absorption_type = 'SELL_ABSORPTION'
                else:
                    absorption_type = 'CONTESTED'

            last_price = trades[-1]['price'] if trades else 0
            level_held = in_zone or (abs(last_price - level_price) / level_price < 0.003 if level_price > 0 else False)

            # Delta Ratio for N3 (useful for CAPITULACAO exhaustion detection)
            delta_ratio = round(net_delta / total_volume, 4) if total_volume > 0 else 0.0

            result[level_name] = {
                'level_price': round(level_price, 2),
                'buffer': round(micro_zone_range, 2),
                'atr_fraction': fraction,
                'zone_range': f'{round(zone_low, 2)} - {round(zone_high, 2)}',
                'in_zone_now': in_zone,
                'touch_count': touch_count,
                'buy_volume': round(buy_volume, 0),
                'sell_volume': round(sell_volume, 0),
                'net_delta': round(net_delta, 0),
                'total_volume': round(total_volume, 0),
                'buy_pct': buy_pct,
                'sell_pct': sell_pct,
                'zscore': zscore,
                'delta_ratio': delta_ratio,
                'volume_baseline': round(baseline, 0),
                'price_range_ticks': price_range_ticks,
                'price_range_abs': round(price_range_abs, 4),
                'absorption_limit': round(absorption_price_limit, 4),
                'time_in_zone_sec': time_in_zone,
                'absorption_detected': absorption_detected,
                'absorption_type': absorption_type,
                'level_held': level_held,
                'signal': self._n3_signal(absorption_detected, absorption_type, net_delta, zscore, in_zone, volume_significant),
            }

        return {
            'mode': 'N3_EXTREME',
            'atr_m1': round(atr_m1, 4) if atr_m1 > 0 else None,
            'absorption_price_limit': round(absorption_price_limit, 4),
            'levels': result,
            'summary': self._n3_summary(result),
        }

    @staticmethod
    def _n3_signal(absorption: bool, absorption_type: str, net_delta: float,
                   zscore: float, in_zone: bool, volume_significant: bool) -> str:
        if absorption and absorption_type == 'BUY_ABSORPTION':
            return 'INSTITUTIONAL_BUY_ABSORPTION'
        if absorption and absorption_type == 'SELL_ABSORPTION':
            return 'INSTITUTIONAL_SELL_ABSORPTION'
        if absorption and absorption_type == 'CONTESTED':
            return 'CONTESTED_LEVEL'
        if in_zone and volume_significant and abs(zscore) > 1.5:
            return 'HIGH_ACTIVITY_AT_LEVEL'
        if in_zone and volume_significant:
            return 'ACTIVE_AT_LEVEL'
        if not in_zone:
            return 'AWAY_FROM_LEVEL'
        return 'MONITORING'

    @staticmethod
    def _n3_summary(levels: Dict) -> Dict:
        if not levels:
            return {
                'status': 'NO_LEVELS', 'active_absorptions': [],
                'active_levels': [], 'interpretation': 'Sem niveis extremos monitorados',
            }

        active_absorptions = []
        active_levels = []

        for name, data in levels.items():
            if data['absorption_detected']:
                active_absorptions.append({
                    'level': name,
                    'type': data['absorption_type'],
                    'volume': data['total_volume'],
                    'zscore': data['zscore'],
                    'delta_ratio': data.get('delta_ratio', 0.0),
                })
            if data['in_zone_now']:
                active_levels.append(name)

        if active_absorptions:
            primary = active_absorptions[0]
            status = 'ABSORPTION_ACTIVE'
            interp = f'Absorcao detectada em {primary["level"]}: {primary["type"]} (Z={primary["zscore"]}, Vol={primary["volume"]:.0f})'
        elif active_levels:
            status = 'AT_EXTREME_LEVEL'
            interp = f'Preco na micro-zona de: {", ".join(active_levels)}'
        else:
            status = 'NO_EXTREME_CONTACT'
            interp = 'Preco afastado dos niveis extremos'

        return {
            'status': status,
            'active_absorptions': active_absorptions,
            'active_levels': active_levels,
            'interpretation': interp,
        }

    # ── Empty results ──

    @staticmethod
    def _empty_n2_result() -> Dict:
        return {
            'mode': 'N2_STRUCTURE',
            'atr_m5': None,
            'levels': {},
            'summary': {
                'status': 'NO_DATA', 'total_delta': 0,
                'bullish_levels': 0, 'bearish_levels': 0,
                'max_zscore': 0, 'max_zscore_level': '',
                'avg_delta_ratio': 0.0,
                'interpretation': 'Sem dados de trades',
            },
        }

    @staticmethod
    def _empty_n3_result() -> Dict:
        return {
            'mode': 'N3_EXTREME',
            'atr_m1': None,
            'absorption_price_limit': 0,
            'levels': {},
            'summary': {
                'status': 'NO_LEVELS', 'active_absorptions': [],
                'active_levels': [], 'interpretation': 'Sem dados de trades',
            },
        }

    # ── Welford Persistence ──

    async def persist_welford(self, database):
        """Save all Welford accumulators to MongoDB."""
        docs = self.welford.export_for_db()
        if not docs:
            return
        collection = database.welford_stats
        for doc in docs:
            await collection.update_one(
                {"key": doc["key"]},
                {"$set": doc},
                upsert=True,
            )
        logger.debug(f"Welford persisted: {len(docs)} accumulators")

    async def load_welford(self, database):
        """Load Welford accumulators from MongoDB on startup."""
        collection = database.welford_stats
        docs = []
        async for doc in collection.find({}, {"_id": 0}):
            docs.append(doc)
        self.welford.load_from_db(docs)

    async def seed_welford_from_snapshots(self, database, days: int = 30):
        """Seed Welford accumulators from historical snapshots.

        For levels with n < 3 crossings (cold start problem), this computes
        baseline statistics from the last N days of snapshots stored in MongoDB.

        This ensures that even extreme levels (Call Wall, Put Wall, ZGL)
        that are rarely touched have meaningful Z-Score baselines from day 1.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        pipeline = [
            {"$match": {"recorded_at": {"$gte": cutoff}}},
            {"$project": {
                "_id": 0,
                "symbol": 1,
                "dz_n2.levels": 1,
                "dz_n3.levels": 1,
            }},
        ]

        seeded = 0
        async for doc in database.v3_snapshots.aggregate(pipeline):
            symbol = doc.get("symbol", "")
            if not symbol:
                continue

            # Seed N2 levels
            n2_levels = doc.get("dz_n2", {}).get("levels", {})
            for level_name, level_data in n2_levels.items():
                vol = level_data.get("total_volume", 0)
                if vol > 0:
                    stats = self.welford.get_stats(symbol, level_name, 'n2')
                    if stats["n"] < 3:
                        self.welford.update(symbol, level_name, 'n2', vol)
                        seeded += 1

            # Seed N3 levels
            n3_levels = doc.get("dz_n3", {}).get("levels", {})
            for level_name, level_data in n3_levels.items():
                vol = level_data.get("total_volume", 0)
                if vol > 0:
                    stats = self.welford.get_stats(symbol, level_name, 'n3')
                    if stats["n"] < 3:
                        self.welford.update(symbol, level_name, 'n3', vol)
                        seeded += 1

        if seeded > 0:
            logger.info(f"Welford seeded from snapshots: {seeded} observations from last {days} days")
            await self.persist_welford(database)
        else:
            logger.info("Welford seed: no historical snapshots found or all accumulators already warm")

    # ── Simulation (market closed) ──

    def simulate_n2(self, symbol: str, levels: Dict[str, float], atr_m5: float = 0.0) -> Dict[str, Any]:
        """Generate simulated N2 DZ data when market is closed.
        Uses secrets.SystemRandom for deterministic non-security simulation data.
        """
        import secrets
        rng = secrets.SystemRandom()
        sim_atr = atr_m5 if atr_m5 > 0 else 5.0
        result = {}
        for level_name, level_price in levels.items():
            if not level_price or level_price <= 0:
                continue
            fraction = N2_ATR_FRACTIONS.get(level_name, 0.75)
            buffer = sim_atr * fraction
            delta = rng.randint(-200, 200)
            volume = rng.randint(100, 800)
            buy_pct = round(rng.uniform(35, 65), 1)
            buffer_vol = rng.randint(20, 200)
            net_d = round(delta, 0)
            d_ratio = round(net_d / volume, 4) if volume > 0 else 0.0
            result[level_name] = {
                'level_price': round(level_price, 2),
                'buffer': round(buffer, 2),
                'buffer_range': f'{round(level_price - buffer, 2)} - {round(level_price + buffer, 2)}',
                'atr_fraction': fraction,
                'position': rng.choice(['above', 'below', 'in_buffer']),
                'delta_above': round(delta * 0.4, 0),
                'delta_below': round(delta * 0.3, 0),
                'delta_buffer': round(delta * 0.3, 0),
                'net_delta': net_d,
                'total_volume': volume,
                'volume_in_buffer': buffer_vol,
                'buy_pct': buy_pct,
                'sell_pct': round(100 - buy_pct, 1),
                'zscore': round(rng.uniform(-1.5, 1.5), 2),
                'delta_ratio': d_ratio,
                'volume_baseline': round(volume * 0.5, 0),
                'volume_significant': True,
                'crossing_count': rng.randint(0, 3),
                'seconds_since_cross': rng.randint(60, 3600),
                'signal': rng.choice(['MILD_BULLISH', 'MILD_BEARISH', 'NEUTRAL', 'LOW_ACTIVITY']),
            }
        return {
            'mode': 'N2_STRUCTURE',
            'atr_m5': round(sim_atr, 4),
            'source': 'simulated',
            'levels': result,
            'summary': self._n2_summary(result),
        }

    def simulate_n3(self, symbol: str, levels: Dict[str, float], atr_m1: float = 0.0) -> Dict[str, Any]:
        """Generate simulated N3 DZ data when market is closed."""
        import secrets
        rng = secrets.SystemRandom()
        sim_atr = atr_m1 if atr_m1 > 0 else 2.0
        absorption_limit = N3_ABSORPTION_ATR_FRACTION * sim_atr
        result = {}
        for level_name, level_price in levels.items():
            if not level_price or level_price <= 0:
                continue
            fraction = N3_ATR_FRACTIONS.get(level_name, 0.25)
            micro_zone = sim_atr * fraction
            volume = rng.randint(0, 100)
            buy_vol = round(volume * rng.uniform(0.3, 0.7))
            sell_vol = volume - buy_vol
            net_d = buy_vol - sell_vol
            d_ratio = round(net_d / volume, 4) if volume > 0 else 0.0
            result[level_name] = {
                'level_price': round(level_price, 2),
                'buffer': round(micro_zone, 2),
                'atr_fraction': fraction,
                'zone_range': f'{round(level_price - micro_zone, 2)} - {round(level_price + micro_zone, 2)}',
                'in_zone_now': False,
                'touch_count': rng.randint(0, 3),
                'buy_volume': buy_vol,
                'sell_volume': sell_vol,
                'net_delta': net_d,
                'total_volume': volume,
                'buy_pct': round(buy_vol / volume * 100, 1) if volume > 0 else 50.0,
                'sell_pct': round(sell_vol / volume * 100, 1) if volume > 0 else 50.0,
                'zscore': round(rng.uniform(-0.5, 0.5), 2),
                'delta_ratio': d_ratio,
                'volume_baseline': 10.0,
                'price_range_ticks': rng.randint(0, 8),
                'price_range_abs': 0.0,
                'absorption_limit': round(absorption_limit, 4),
                'time_in_zone_sec': 0,
                'absorption_detected': False,
                'absorption_type': 'NONE',
                'level_held': False,
                'signal': 'AWAY_FROM_LEVEL',
            }
        return {
            'mode': 'N3_EXTREME',
            'atr_m1': round(sim_atr, 4),
            'absorption_price_limit': round(absorption_limit, 4),
            'source': 'simulated',
            'levels': result,
            'summary': self._n3_summary(result),
        }


# Singleton
delta_zonal_service = DeltaZonalService()
