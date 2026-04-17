"""
MacroContextService
===================
Serviço dedicado de Term Structure (VIX/VIX3M) e Gamma Levels (QQQ/SPY)
para o Scalp Engine — substitui completamente a dependência do sistema V3.

Fontes de dados (Yahoo Finance — públicas, sem API key):
  - Term Structure : ^VIX / ^VIX3M  (cache 15min RTH | 60min off-hours)
  - Gamma Exposure : options chain QQQ (MNQ) ou SPY (MES)  (cache 60min)
  - ETF→Futures    : ratio D-1 via GammaRatioService ou real-time fallback

Interface pública:
  await MacroContextService.get_context(symbol, futures_price, gamma_ratio_svc=None)
  → Dict com ts_ratio/ts_state/ts_hard_stop/ts_fade_suppressed/
          gamma_reliable/gamma_call_wall/gamma_put_wall/gamma_sentiment/
          zero_gamma/gamma_regime
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import pandas as pd
import yfinance as yf
from scipy.stats import norm

logger = logging.getLogger("macro_context_service")

# ── TTLs ──────────────────────────────────────────────────────────────────────
_TS_TTL_RTH      = 15 * 60   # 15 min em RTH
_TS_TTL_OFFHOURS = 60 * 60   # 60 min fora de RTH
_GAMMA_TTL       = 60 * 60   # 60 min (options chain muda devagar)

# ── ETF map ───────────────────────────────────────────────────────────────────
_ETF_MAP = {"MNQ": "QQQ", "MES": "SPY"}

# ── Resposta neutra (fail-safe) ────────────────────────────────────────────────
_NEUTRAL: Dict[str, Any] = {
    "ts_ratio":           0.0,
    "ts_state":           "UNKNOWN",
    "ts_hard_stop":       False,
    "ts_fade_suppressed": False,
    "gamma_reliable":     False,
    "gamma_call_wall":    0.0,
    "gamma_put_wall":     0.0,
    "gamma_sentiment":    "NEUTRAL",
    "zero_gamma":         0.0,
    "gamma_regime":       "UNKNOWN",
}


# ═══════════════════════════════════════════════════════════════════════════════
# MacroContextService
# ═══════════════════════════════════════════════════════════════════════════════

class MacroContextService:
    """
    Serviço autônomo de contexto macro para o Scalp Engine.
    Não possui dependência alguma do sistema V3.
    """

    # Term Structure cache (global — não é por símbolo)
    _ts_data:    Optional[Dict] = None
    _ts_ts:      float          = 0.0   # time.monotonic() do último fetch

    # Gamma cache — por símbolo
    _gamma_data: Dict[str, Dict] = {}
    _gamma_ts:   Dict[str, float] = {}

    # ── TTL helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _rth_active() -> bool:
        now = datetime.now(timezone.utc)
        minutes_since_open = (now.hour * 60 + now.minute) - (14 * 60 + 30)
        return 0 <= minutes_since_open <= 390 and now.weekday() < 5

    @classmethod
    def _ts_ttl(cls) -> float:
        return _TS_TTL_RTH if cls._rth_active() else _TS_TTL_OFFHOURS

    # ── Ponto de entrada público ────────────────────────────────────────────────

    @classmethod
    async def get_context(
        cls,
        symbol: str,
        futures_price: float = 0.0,
        gamma_ratio_svc=None,
    ) -> Dict[str, Any]:
        """
        Retorna o macro_context completo para um símbolo.
        Qualquer falha individual retorna contexto neutro para aquele campo.
        """
        ctx = dict(_NEUTRAL)

        # Term Structure (shared entre símbolos)
        try:
            ts = await cls._get_term_structure()
            if ts:
                ts_ratio = float(ts.get("ratio", 0.0) or 0.0)
                ctx["ts_ratio"]           = ts_ratio
                ctx["ts_state"]           = ts.get("state", "UNKNOWN")
                ctx["ts_hard_stop"]       = ts_ratio >= 1.10
                ctx["ts_fade_suppressed"] = ts_ratio >= 1.05
        except Exception as e:
            logger.warning("MacroContextService: term_structure falhou: %s", e)

        # Gamma Levels
        try:
            gamma = await cls._get_gamma_levels(symbol, futures_price, gamma_ratio_svc)
            ctx.update(gamma)
        except Exception as e:
            logger.warning("MacroContextService: gamma_levels falhou [%s]: %s", symbol, e)

        return ctx

    # ── Term Structure ──────────────────────────────────────────────────────────

    @classmethod
    async def _get_term_structure(cls) -> Optional[Dict]:
        age = time.monotonic() - cls._ts_ts
        if cls._ts_data and age < cls._ts_ttl():
            return cls._ts_data

        try:
            result = await asyncio.to_thread(cls._fetch_ts_sync)
            if result:
                cls._ts_data = result
                cls._ts_ts   = time.monotonic()
                logger.debug(
                    "MacroContextService: TS atualizado ratio=%.3f state=%s",
                    result["ratio"], result["state"],
                )
            return cls._ts_data
        except Exception as e:
            logger.warning("MacroContextService: fetch TS falhou: %s", e)
            return cls._ts_data  # retorna cache antigo se existir

    @staticmethod
    def _fetch_ts_sync() -> Optional[Dict]:
        """Síncrono — roda em thread pool. Busca ^VIX e ^VIX3M do Yahoo Finance."""
        try:
            vix   = yf.Ticker("^VIX")
            vix3m = yf.Ticker("^VIX3M")
            v_info    = vix.info
            v3m_info  = vix3m.info
            vix_val   = float(v_info.get("regularMarketPrice")  or v_info.get("previousClose",  20))
            vix3m_val = float(v3m_info.get("regularMarketPrice") or v3m_info.get("previousClose", 22))

            ratio = vix_val / vix3m_val if vix3m_val > 0 else 1.0

            if ratio > 1.10:
                state = "STRONG_BACKWARDATION"
            elif ratio > 1.00:
                state = "BACKWARDATION"
            elif ratio > 0.95:
                state = "FLAT"
            elif ratio > 0.85:
                state = "CONTANGO"
            else:
                state = "STEEP_CONTANGO"

            return {
                "vix":   round(vix_val,   2),
                "vix3m": round(vix3m_val, 2),
                "ratio": round(ratio,     4),
                "state": state,
            }
        except Exception as e:
            logger.warning("_fetch_ts_sync falhou: %s", e)
            return None

    # ── Gamma Levels ────────────────────────────────────────────────────────────

    @classmethod
    async def _get_gamma_levels(
        cls,
        symbol: str,
        futures_price: float,
        gamma_ratio_svc=None,
    ) -> Dict[str, Any]:
        age = time.monotonic() - cls._gamma_ts.get(symbol, 0.0)
        if symbol in cls._gamma_data and age < _GAMMA_TTL:
            cached = cls._gamma_data[symbol]
            return await cls._apply_ratio(cached, futures_price, gamma_ratio_svc, cached.get("_etf_spot", 0.0))

        etf = _ETF_MAP.get(symbol, "SPY")
        try:
            raw = await asyncio.to_thread(cls._fetch_gamma_sync, etf)
        except Exception as e:
            logger.warning("MacroContextService: fetch gamma [%s] falhou: %s", symbol, e)
            return {}

        if not raw:
            return {}

        raw["_etf_spot"] = raw.get("spot_price", 0.0)
        cls._gamma_data[symbol] = raw
        cls._gamma_ts[symbol]   = time.monotonic()

        return await cls._apply_ratio(raw, futures_price, gamma_ratio_svc, raw["_etf_spot"])

    @staticmethod
    def _fetch_gamma_sync(etf_ticker: str) -> Optional[Dict]:
        """Síncrono — roda em thread pool. Busca options chain do ETF no Yahoo Finance."""
        try:
            ticker = yf.Ticker(etf_ticker)
            info   = ticker.info
            spot   = float(info.get("regularMarketPrice") or info.get("previousClose", 100))
            expirations = ticker.options
            if not expirations:
                return None

            best_exp = best_calls = best_puts = None
            best_oi  = 0
            for exp_str in expirations[:8]:
                try:
                    chain = ticker.option_chain(exp_str)
                    oi = int(chain.calls["openInterest"].sum() + chain.puts["openInterest"].sum())
                    if oi > best_oi:
                        best_oi, best_exp = oi, exp_str
                        best_calls, best_puts = chain.calls, chain.puts
                except Exception:
                    continue

            if best_calls is None:
                chain = ticker.option_chain(expirations[0])
                best_calls, best_puts = chain.calls, chain.puts
                best_exp = expirations[0]

            gex = MacroContextService._calculate_gex(best_calls, best_puts, spot, best_exp)
            gex["spot_price"]  = round(spot, 2)
            gex["expiration"]  = best_exp
            gex["etf"]         = etf_ticker
            gex["fetched_at"]  = time.monotonic()
            return gex

        except Exception as e:
            logger.warning("_fetch_gamma_sync [%s] falhou: %s", etf_ticker, e)
            return None

    @staticmethod
    def _calculate_gex(
        calls: pd.DataFrame,
        puts:  pd.DataFrame,
        spot:  float,
        expiration: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Black-Scholes GEX → Call Wall, Put Wall, Zero Gamma Level."""
        T = 7 / 365
        if expiration:
            try:
                exp_dt = datetime.strptime(expiration, "%Y-%m-%d")
                T = max((exp_dt - datetime.now()).days, 1) / 365
            except Exception:
                pass

        r   = 0.045
        rng = spot * 0.15
        c   = calls[(calls["strike"] >= spot - rng) & (calls["strike"] <= spot + rng)].copy()
        p   = puts[ (puts["strike"]  >= spot - rng) & (puts["strike"]  <= spot + rng)].copy()

        mult = 100
        call_gex_total = put_gex_total = 0.0
        sk_call: Dict[float, float] = {}
        sk_put:  Dict[float, float] = {}
        sk_net:  Dict[float, float] = {}

        for df, sign, sk_map in [(c, 1, sk_call), (p, -1, sk_put)]:
            for _, row in df.iterrows():
                K  = float(row["strike"])
                oi = float(row.get("openInterest", 0) or 0)
                iv = float(row.get("impliedVolatility", 0) or 0)
                if oi == 0 or iv <= 0:
                    continue
                d1    = (math.log(spot / K) + (r + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
                gamma = norm.pdf(d1) / (spot * iv * math.sqrt(T))
                gex   = gamma * oi * mult * spot * spot / 1e6
                sk    = round(K, 1)
                sk_map[sk]  = sk_map.get(sk, 0.0)  + gex
                sk_net[sk]  = sk_net.get(sk, 0.0)  + sign * gex
                if sign == 1:
                    call_gex_total += gex
                else:
                    put_gex_total  += gex

        net_gex = call_gex_total - put_gex_total

        call_wall = max(sk_call, key=sk_call.get) if sk_call else None
        put_wall  = max(sk_put,  key=sk_put.get)  if sk_put  else None

        # Zero Gamma Level — cruzamento do GEX acumulado
        zgl = None
        if sk_net:
            prev_sk = prev_cum = None
            cum = 0.0
            for sk in sorted(sk_net):
                cum += sk_net[sk]
                if prev_sk is not None and prev_cum * cum < 0:
                    ratio = abs(prev_cum) / (abs(prev_cum) + abs(cum))
                    zgl   = round(prev_sk + ratio * (sk - prev_sk), 2)
                    break
                prev_sk, prev_cum = sk, cum
            if zgl is None and sk_net:
                total_abs = sum(abs(v) for v in sk_net.values())
                if total_abs > 0:
                    zgl = round(sum(sk * abs(sk_net[sk]) for sk in sk_net) / total_abs, 2)

        zgl_signal = "ABOVE_ZGL" if (zgl and spot > zgl) else "BELOW_ZGL" if zgl else "NEUTRAL"
        sentiment  = "POSITIVE" if net_gex > 0 else "NEGATIVE"

        return {
            "net_gex":    round(net_gex, 2),
            "call_wall":  round(call_wall, 2) if call_wall else None,
            "put_wall":   round(put_wall,  2) if put_wall  else None,
            "zgl":        zgl,
            "zgl_signal": zgl_signal,
            "sentiment":  sentiment,
        }

    # ── ETF → Futures conversion ────────────────────────────────────────────────

    @staticmethod
    async def _apply_ratio(
        raw: Dict,
        futures_price: float,
        gamma_ratio_svc,
        etf_spot: float,
    ) -> Dict[str, Any]:
        ratio = 1.0
        if gamma_ratio_svc is not None and etf_spot > 0:
            try:
                rd = await gamma_ratio_svc.get_ratio(
                    raw.get("etf", "SPY"),         # unused by GammaRatioService but fine
                    realtime_futures_price=futures_price,
                    realtime_etf_price=etf_spot,
                )
                ratio = float(rd.get("ratio", 1.0) or 1.0)
            except Exception as e:
                logger.warning("MacroContextService: get_ratio falhou: %s", e)
        elif futures_price > 0 and etf_spot > 0:
            ratio = futures_price / etf_spot

        call_wall_etf = float(raw.get("call_wall") or 0.0)
        put_wall_etf  = float(raw.get("put_wall")  or 0.0)
        zgl_etf       = float(raw.get("zgl")       or 0.0)
        zgl_signal    = raw.get("zgl_signal", "NEUTRAL")

        reliable = call_wall_etf > 0 or put_wall_etf > 0
        gamma_regime = (
            "LONG_GAMMA"  if zgl_signal == "ABOVE_ZGL" else
            "SHORT_GAMMA" if zgl_signal == "BELOW_ZGL" else
            "UNKNOWN"
        )

        logger.debug(
            "MacroContextService: call=%.2f put=%.2f zgl=%.2f regime=%s ratio=%.4f",
            call_wall_etf * ratio, put_wall_etf * ratio, zgl_etf * ratio, gamma_regime, ratio,
        )

        return {
            "gamma_reliable":  reliable,
            "gamma_call_wall": round(call_wall_etf * ratio, 2) if call_wall_etf > 0 else 0.0,
            "gamma_put_wall":  round(put_wall_etf  * ratio, 2) if put_wall_etf  > 0 else 0.0,
            "gamma_sentiment": raw.get("sentiment", "NEUTRAL"),
            "zero_gamma":      round(zgl_etf * ratio, 2) if zgl_etf > 0 else 0.0,
            "gamma_regime":    gamma_regime,
        }

    # ── Diagnóstico ────────────────────────────────────────────────────────────

    @classmethod
    def cache_status(cls) -> Dict[str, Any]:
        now = time.monotonic()
        ts_age = int(now - cls._ts_ts) if cls._ts_data else None
        gamma_ages = {
            sym: int(now - ts) for sym, ts in cls._gamma_ts.items()
        }
        return {
            "ts_cached":    cls._ts_data is not None,
            "ts_age_s":     ts_age,
            "ts_ttl_s":     int(cls._ts_ttl()),
            "gamma_cached": list(cls._gamma_data.keys()),
            "gamma_age_s":  gamma_ages,
            "gamma_ttl_s":  int(_GAMMA_TTL),
        }
