"""
Data Quality Layer — LKG, Circuit Breaker, DataEpoch (RCU), DQS, MQS
=====================================================================
Production-grade data reliability infrastructure for the V3 Trading Engine.

Components:
  1. LKGStore: Last Known Good with DEAD TTL — 4 confidence levels
     (LIVE → STALE → DEAD → FALLBACK).
  2. CircuitBreaker: Per-source with Exponential Backoff
     (CLOSED → OPEN → HALF_OPEN, backoff 120s → 240s → 480s → max 900s).
  3. DataEpoch: Read-Copy-Update immutable snapshot (unchanged).
  4. DQS: Data Quality Score for N1 macro (weighted, step-function).
  5. MQS: Microstructure Quality Score — binary fuse for N2/N3
     (DataBento live feed health). If MQS fails → N3 freezes execution.
  6. Validation: Sanity ranges per source.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
from copy import deepcopy

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. CONFIDENCE LEVELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Confidence(str, Enum):
    LIVE = "LIVE"           # Fresh data from source (within STALE TTL)
    STALE = "STALE"         # Real data past STALE TTL but within DEAD TTL
    DEAD = "DEAD"           # Real data past DEAD TTL — zombie, unsafe
    FALLBACK = "FALLBACK"   # Static historical constants (never had real data)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. LKG STORE — Last Known Good with DEAD TTL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Per-source TTL configuration (seconds)
# stale_ttl: LIVE → STALE transition
# dead_ttl:  STALE → DEAD transition (data becomes zombie)
LKG_TTL_CONFIG = {
    "vix":                {"stale_ttl": 1800, "dead_ttl": 5400},    # 30min stale, 90min dead
    "vxn":                {"stale_ttl": 1800, "dead_ttl": 5400},    # 30min stale, 90min dead
    "term_structure":     {"stale_ttl": 1800, "dead_ttl": 7200},    # 30min stale, 120min dead
    "treasury":           {"stale_ttl": 1800, "dead_ttl": 7200},    # 30min stale, 120min dead
    "gamma":              {"stale_ttl": 86400, "dead_ttl": 100800}, # 24h stale, 28h dead
    "economic_calendar":  {"stale_ttl": 300, "dead_ttl": 1800},     # 5min stale, 30min dead
}

# Historical medians as static fallback (never np.random)
STATIC_FALLBACKS = {
    "vix": {
        "value": 20.0, "previous_close": 20.0, "change": 0.0,
        "change_percent": 0.0, "day_high": 20.0, "day_low": 20.0,
        "month_mean": 20.0, "month_std": 3.0, "month_high": 25.0,
        "month_low": 15.0, "percentile": 50.0, "regime": "NORMAL",
        "signal": "CONFIRM", "score": 0.7,
        "source": "static_fallback",
    },
    "vxn": {
        "value": 22.0, "previous_close": 22.0, "change": 0.0,
        "change_percent": 0.0, "day_high": 22.0, "day_low": 22.0,
        "month_mean": 22.0, "month_std": 4.0, "month_high": 28.0,
        "month_low": 16.0, "percentile": 50.0, "regime": "NORMAL",
        "signal": "CONFIRM", "score": 0.7, "index": "NASDAQ-100",
        "source": "static_fallback",
    },
    "term_structure": {
        "vix": 20.0, "vix3m": 22.0, "ratio": 0.909,
        "state": "CONTANGO", "description": "Normal market state",
        "signal": "CONFIRM", "market_implication": "Steady state",
        "trend": "STABLE", "trend_description": "Static fallback",
        "stats": {
            "backwardation_days_30d": 3, "total_days": 30,
            "backwardation_pct": 10.0, "avg_ratio_30d": 0.91,
            "ratio_std_30d": 0.05,
        },
        "history": [],
        "source": "static_fallback",
    },
    "treasury": {
        "us10y": 4.25, "us10y_prev": 4.25, "us10y_change": 0.0,
        "us10y_change_pct": 0.0, "us10y_month_range": [4.0, 4.5],
        "us10y_month_mean": 4.25,
        "us2y": 3.95, "us2y_prev": 3.95, "us2y_change": 0.0,
        "us2y_change_pct": 0.0, "us2y_month_range": [3.7, 4.2],
        "us2y_month_mean": 3.95, "us2y_source": "static_fallback",
        "spread": 0.30, "spread_bps": 30.0,
        "spread_prev": 0.30, "spread_change": 0.0,
        "spread_month_mean": 0.30, "spread_month_std": 0.05,
        "spread_month_range": [0.20, 0.40],
        "curve_state": "FLAT", "signal": "CAUTION", "score": 2,
        "max_score": 4, "interpretation": "Dados de fallback estatico",
        "spread_type": "2s10s",
        "source": "static_fallback",
    },
    "gamma": {
        "net_gex": 0, "sentiment": "NEUTRAL", "zgl_signal": "NEUTRAL",
        "call_wall": 0, "put_wall": 0, "zgl": 0, "spot_price": 0,
        "source": "static_fallback",
    },
    "economic_calendar": {
        "events": [], "event_count": 0, "has_news_today": False,
        "latest_released": None, "latest_released_utc": None,
        "source": "static_fallback",
    },
}


@dataclass
class LKGEntry:
    """Single Last Known Good entry."""
    data: Dict[str, Any]
    confidence: Confidence
    fetched_at: float  # time.monotonic()
    real_timestamp: str  # ISO UTC


class LKGStore:
    """Last Known Good store with per-source DEAD TTL."""

    def __init__(self):
        self._entries: Dict[str, LKGEntry] = {}

    def update(self, key: str, data: Dict[str, Any]):
        """Store fresh LIVE data as the new LKG."""
        self._entries[key] = LKGEntry(
            data=deepcopy(data),
            confidence=Confidence.LIVE,
            fetched_at=time.monotonic(),
            real_timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def get(self, key: str, max_age_seconds: float = 0) -> tuple:
        """Get data with 4-level confidence assessment.

        Uses per-source TTL config (LKG_TTL_CONFIG) for STALE/DEAD thresholds.
        The max_age_seconds param is kept for backward compat but TTL config takes priority.

        Returns: (data_dict, confidence_enum, age_seconds)
        """
        entry = self._entries.get(key)
        if entry is None:
            # Never had real data — use static fallback
            static = STATIC_FALLBACKS.get(key, {})
            result = deepcopy(static)
            result["_confidence"] = Confidence.FALLBACK.value
            result["_age_seconds"] = -1
            result["timestamp"] = datetime.now(timezone.utc).isoformat()
            return result, Confidence.FALLBACK, -1

        age = time.monotonic() - entry.fetched_at
        ttl_cfg = LKG_TTL_CONFIG.get(key, {"stale_ttl": 1800, "dead_ttl": 5400})
        stale_ttl = ttl_cfg["stale_ttl"]
        dead_ttl = ttl_cfg["dead_ttl"]

        result = deepcopy(entry.data)
        result["_age_seconds"] = round(age, 1)

        if age <= stale_ttl:
            # Fresh — LIVE confidence
            result["_confidence"] = Confidence.LIVE.value
            return result, Confidence.LIVE, age
        elif age <= dead_ttl:
            # Past stale TTL but within dead TTL — degraded but usable
            result["_confidence"] = Confidence.STALE.value
            result["source"] = f"lkg_stale ({result.get('source', 'unknown')})"
            return result, Confidence.STALE, age
        else:
            # Past dead TTL — zombie data, unsafe for trading decisions
            result["_confidence"] = Confidence.DEAD.value
            result["source"] = f"lkg_dead ({result.get('source', 'unknown')})"
            logger.warning(
                "LKG DEAD: %s is %.0fs old (dead_ttl=%.0fs). Data is zombie.",
                key, age, dead_ttl,
            )
            return result, Confidence.DEAD, age

    def has_real_data(self, key: str) -> bool:
        return key in self._entries

    def get_age(self, key: str) -> float:
        """Get age in seconds of the LKG entry. Returns -1 if no entry."""
        entry = self._entries.get(key)
        if entry is None:
            return -1
        return time.monotonic() - entry.fetched_at


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. CIRCUIT BREAKER with Exponential Backoff
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BreakerState(str, Enum):
    CLOSED = "CLOSED"       # Normal operation
    OPEN = "OPEN"           # Source is down, skip calls
    HALF_OPEN = "HALF_OPEN" # Testing one request


# Exponential backoff limits
BACKOFF_BASE = 120.0       # Initial recovery timeout (seconds)
BACKOFF_MULTIPLIER = 2.0   # Double each consecutive failure cycle
BACKOFF_MAX = 900.0        # Max 15 minutes


class CircuitBreaker:
    """Per-source circuit breaker with exponential backoff."""

    def __init__(self, name: str, failure_threshold: int = 3,
                 recovery_timeout: float = BACKOFF_BASE):
        self.name = name
        self.failure_threshold = failure_threshold
        self._base_timeout = recovery_timeout
        self.recovery_timeout = recovery_timeout
        self.state = BreakerState.CLOSED
        self.failure_count = 0
        self.consecutive_open_cycles = 0
        self.last_failure_time: float = 0
        self.last_success_time: float = 0

    def can_execute(self) -> bool:
        """Check if we should attempt the call."""
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = BreakerState.HALF_OPEN
                logger.info(
                    "CircuitBreaker[%s]: OPEN -> HALF_OPEN (%.0fs elapsed, backoff=%.0fs)",
                    self.name, elapsed, self.recovery_timeout,
                )
                return True
            return False
        # HALF_OPEN: allow one test request
        return True

    def record_success(self):
        """Record a successful call — reset everything."""
        if self.state == BreakerState.HALF_OPEN:
            logger.info("CircuitBreaker[%s]: HALF_OPEN -> CLOSED (recovered)", self.name)
        self.state = BreakerState.CLOSED
        self.failure_count = 0
        self.consecutive_open_cycles = 0
        self.recovery_timeout = self._base_timeout  # Reset backoff
        self.last_success_time = time.monotonic()

    def record_failure(self):
        """Record a failed call with exponential backoff on repeated OPEN cycles."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()

        if self.state == BreakerState.HALF_OPEN:
            # Test request failed — back to OPEN with increased backoff
            self.consecutive_open_cycles += 1
            self.recovery_timeout = min(
                self._base_timeout * (BACKOFF_MULTIPLIER ** self.consecutive_open_cycles),
                BACKOFF_MAX,
            )
            self.state = BreakerState.OPEN
            logger.warning(
                "CircuitBreaker[%s]: HALF_OPEN -> OPEN (test failed, cycle=%d, next_backoff=%.0fs)",
                self.name, self.consecutive_open_cycles, self.recovery_timeout,
            )
        elif self.failure_count >= self.failure_threshold:
            if self.state != BreakerState.OPEN:
                self.consecutive_open_cycles = 0
                logger.warning(
                    "CircuitBreaker[%s]: -> OPEN (%d consecutive failures, backoff=%.0fs)",
                    self.name, self.failure_count, self.recovery_timeout,
                )
            self.state = BreakerState.OPEN

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout_s": self.recovery_timeout,
            "base_timeout_s": self._base_timeout,
            "consecutive_open_cycles": self.consecutive_open_cycles,
            "backoff_multiplier": BACKOFF_MULTIPLIER,
            "last_failure_ago_s": round(time.monotonic() - self.last_failure_time, 1) if self.last_failure_time else None,
            "last_success_ago_s": round(time.monotonic() - self.last_success_time, 1) if self.last_success_time else None,
        }


class CircuitBreakerRegistry:
    """Central registry for all circuit breakers."""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get(self, name: str, failure_threshold: int = 3,
            recovery_timeout: float = BACKOFF_BASE) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                name, failure_threshold, recovery_timeout,
            )
        return self._breakers[name]

    def get_all_status(self) -> Dict[str, Any]:
        return {n: b.get_status() for n, b in self._breakers.items()}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. DATA EPOCH (RCU — Read-Copy-Update)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class DataEpoch:
    """Immutable snapshot of all macro data for a single evaluation cycle."""
    epoch_id: int
    sealed_at: str  # ISO UTC
    vix: Dict[str, Any] = field(default_factory=dict)
    vxn: Dict[str, Any] = field(default_factory=dict)
    term_structure: Dict[str, Any] = field(default_factory=dict)
    treasury: Dict[str, Any] = field(default_factory=dict)
    gamma: Dict[str, Any] = field(default_factory=dict)
    economic_calendar: Dict[str, Any] = field(default_factory=dict)
    dqs: float = 1.0
    dqs_breakdown: Dict[str, Any] = field(default_factory=dict)
    confidence_map: Dict[str, str] = field(default_factory=dict)


class DataEpochManager:
    """Manages the RCU lifecycle: draft → seal → read."""

    def __init__(self, lkg_store: LKGStore, breaker_registry: CircuitBreakerRegistry):
        self._lkg = lkg_store
        self._breakers = breaker_registry
        self._epoch_counter = 0
        self._current_epoch: Optional[DataEpoch] = None
        self._draft: Dict[str, Any] = {}
        self._draft_confidence: Dict[str, Confidence] = {}

    def update_draft(self, key: str, data: Dict[str, Any], confidence: Confidence):
        """Background tasks call this to update individual components."""
        self._draft[key] = deepcopy(data)
        self._draft_confidence[key] = confidence

    def seal_epoch(self) -> DataEpoch:
        """Seal the current draft into an immutable DataEpoch.

        Called at the beginning of each V3 evaluation cycle.
        For any missing component in the draft, falls back to LKG
        which now applies DEAD TTL automatically.
        """
        self._epoch_counter += 1
        now = datetime.now(timezone.utc).isoformat()

        sealed = {}
        confidence_map = {}

        for key in LKG_TTL_CONFIG:
            if key in self._draft:
                sealed[key] = self._draft[key]
                confidence_map[key] = self._draft_confidence.get(key, Confidence.LIVE).value
            else:
                # Not in draft — get from LKG (applies STALE/DEAD TTL automatically)
                data, conf, _ = self._lkg.get(key)
                sealed[key] = data
                confidence_map[key] = conf.value

        # Compute DQS
        dqs, dqs_breakdown = compute_dqs(confidence_map)

        epoch = DataEpoch(
            epoch_id=self._epoch_counter,
            sealed_at=now,
            vix=sealed.get("vix", {}),
            vxn=sealed.get("vxn", {}),
            term_structure=sealed.get("term_structure", {}),
            treasury=sealed.get("treasury", {}),
            gamma=sealed.get("gamma", {}),
            economic_calendar=sealed.get("economic_calendar", {}),
            dqs=dqs,
            dqs_breakdown=dqs_breakdown,
            confidence_map=confidence_map,
        )

        self._current_epoch = epoch

        # Clear draft for next cycle
        self._draft.clear()
        self._draft_confidence.clear()

        return epoch

    @property
    def current(self) -> Optional[DataEpoch]:
        return self._current_epoch

    def get_status(self) -> Dict[str, Any]:
        ep = self._current_epoch
        return {
            "epoch_id": ep.epoch_id if ep else 0,
            "sealed_at": ep.sealed_at if ep else None,
            "dqs": ep.dqs if ep else None,
            "dqs_breakdown": ep.dqs_breakdown if ep else {},
            "confidence_map": ep.confidence_map if ep else {},
            "breakers": self._breakers.get_all_status(),
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. DQS — Data Quality Score (Step-Function)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Weights: how much each source contributes to the N1 macro score
# Primary sensors (intraday-relevant): VIX=4, TS=4, Gamma=4
# Macro tiebreaker (weekly signal):    YieldCurve=1
# Total = 13 (matches max_score). Dead primary sensor → DQS < 0.75 → HARD_STOP
DQS_WEIGHTS = {
    "vix": 4,              # 0-4 pts in N1 — primary sensor
    "term_structure": 4,   # 0-4 pts + override — primary sensor
    "treasury": 1,         # 0-1 pt (binary tiebreaker) — dead treasury = negligible DQS impact
    "gamma": 4,            # 0-4 pts in N1 — primary sensor
}

# Confidence → quality multiplier (DEAD = 0.0 → forces DQS collapse)
CONFIDENCE_MULTIPLIER = {
    Confidence.LIVE.value: 1.0,
    Confidence.STALE.value: 0.7,
    Confidence.DEAD.value: 0.0,
    Confidence.FALLBACK.value: 0.3,
}


def compute_dqs(confidence_map: Dict[str, str]) -> tuple:
    """Compute Data Quality Score from confidence levels.

    Returns: (dqs_float, breakdown_dict)
    """
    total_weight = sum(DQS_WEIGHTS.values())
    weighted_sum = 0.0
    breakdown = {}

    for key, weight in DQS_WEIGHTS.items():
        conf = confidence_map.get(key, Confidence.FALLBACK.value)
        multiplier = CONFIDENCE_MULTIPLIER.get(conf, 0.3)
        contribution = weight * multiplier
        weighted_sum += contribution
        breakdown[key] = {
            "confidence": conf,
            "weight": weight,
            "multiplier": multiplier,
            "contribution": round(contribution, 2),
        }

    dqs = round(weighted_sum / total_weight, 3) if total_weight > 0 else 0.0
    breakdown["total_weight"] = total_weight
    breakdown["weighted_sum"] = round(weighted_sum, 2)

    return dqs, breakdown


def apply_dqs_lot_scaling(lot_pct: int, dqs: float) -> tuple:
    """Apply step-function lot degradation based on DQS.

    Returns: (adjusted_lot_pct, dqs_action, reason)
    """
    if dqs >= 0.9:
        return lot_pct, "FULL", None
    elif dqs >= 0.75:
        adjusted = max(lot_pct // 2, 25)
        reason = f"DQS={dqs:.3f} (modo defensivo): lote {lot_pct}% -> {adjusted}%"
        logger.warning("DQS DEFENSIVE: %s", reason)
        return adjusted, "DEFENSIVE", reason
    else:
        reason = (
            f"DQS={dqs:.3f} < 0.75: HARD STOP. "
            f"Dados macro insuficientes para operar com seguranca."
        )
        logger.error("DQS HARD STOP: %s", reason)
        return 0, "HARD_STOP", reason


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. MQS — Microstructure Quality Score (Binary Fuse for N2/N3)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# MQS thresholds
MQS_MAX_TRADE_AGE_S = 30.0    # If last trade > 30s ago → feed is stale
MQS_MIN_BUFFER_SIZE = 10      # Need at least 10 trades in buffer


class MicrostructureQualityScore:
    """Binary fuse for N2/N3 execution.

    Answers: "Do we have eyes open to pull the trigger NOW?"
    Independent of DQS (macro). If MQS fails, N3 freezes execution
    but DQS/N1 regime stays intact for when the feed returns.
    """

    def __init__(self):
        self._status: Dict[str, Dict[str, Any]] = {}

    def evaluate(self, symbol: str, live_data_service) -> Dict[str, Any]:
        """Evaluate microstructure health for a symbol.

        Returns: {
            "ok": bool,          # True = N3 can execute
            "score": float,      # 0.0 to 1.0
            "reason": str|None,  # Why MQS failed (if not ok)
            "components": {...}  # Breakdown
        }
        """
        buf = live_data_service.buffers.get(symbol)
        components = {
            "ws_connected": False,
            "buffer_has_trades": False,
            "last_trade_fresh": False,
            "last_trade_age_s": None,
            "buffer_size": 0,
        }

        # Component 1: WebSocket connected
        if buf and buf.connected:
            components["ws_connected"] = True

        # Component 2: Buffer has minimum trades
        if buf and buf.trade_count_since_start >= MQS_MIN_BUFFER_SIZE:
            components["buffer_has_trades"] = True
            components["buffer_size"] = buf.trade_count_since_start

        # Component 3: Last trade is fresh (within 30s)
        if buf and buf.last_trade_time:
            age = (datetime.now(timezone.utc) - buf.last_trade_time).total_seconds()
            components["last_trade_age_s"] = round(age, 1)
            if age <= MQS_MAX_TRADE_AGE_S:
                components["last_trade_fresh"] = True

        # Score: all 3 must pass for ok=True
        passed = sum([
            components["ws_connected"],
            components["buffer_has_trades"],
            components["last_trade_fresh"],
        ])
        score = round(passed / 3.0, 2)
        ok = passed == 3

        reason = None
        if not ok:
            failures = []
            if not components["ws_connected"]:
                failures.append("Feed ao vivo desconectado")
            if not components["buffer_has_trades"]:
                count = components["buffer_size"]
                failures.append(
                    f"Feed a aquecer — {count} trade{'s' if count != 1 else ''} recebido{'s' if count != 1 else ''} "
                    f"(mín. {MQS_MIN_BUFFER_SIZE})"
                )
            if not components["last_trade_fresh"]:
                age_s = components["last_trade_age_s"]
                if age_s is not None:
                    age_label = f"{int(age_s)}s" if age_s < 120 else f"{int(age_s // 60)}min {int(age_s % 60)}s"
                    failures.append(f"Sem atividade — último trade há {age_label} (limite: {MQS_MAX_TRADE_AGE_S}s)")
                else:
                    failures.append("Sem atividade — nenhum trade recebido ainda")
            reason = " | ".join(failures)

        result = {
            "ok": ok,
            "score": score,
            "reason": reason,
            "components": components,
        }

        self._status[symbol] = result
        return result

    def is_ok(self, symbol: str) -> bool:
        """Quick check: can N3 execute for this symbol?"""
        status = self._status.get(symbol)
        return status["ok"] if status else False

    def get_status(self, symbol: str = None) -> Dict[str, Any]:
        if symbol:
            return self._status.get(symbol, {"ok": False, "score": 0, "reason": "Not evaluated"})
        return self._status


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. VALIDATION (Sanity Checks)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VALIDATION_RANGES = {
    "vix": ("value", 8.0, 90.0),
    "vxn": ("value", 8.0, 100.0),
    "term_structure": ("ratio", 0.5, 2.0),
    "treasury_us10y": ("us10y", 0.0, 15.0),
    "treasury_us2y": ("us2y", 0.0, 15.0),
    "treasury_spread": ("spread", -3.0, 5.0),
}


def validate_data(key: str, data: Dict[str, Any]) -> bool:
    """Validate fetched data is within sane ranges.

    Returns True if valid, False if suspicious.
    """
    rule = VALIDATION_RANGES.get(key)
    if not rule:
        return True
    field_name, min_val, max_val = rule
    value = data.get(field_name)
    if value is None:
        return False
    try:
        v = float(value)
        if v < min_val or v > max_val:
            logger.warning(
                "Validation FAIL for %s.%s: %.4f not in [%.1f, %.1f]",
                key, field_name, v, min_val, max_val,
            )
            return False
        return True
    except (TypeError, ValueError):
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SINGLETON INSTANCES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

lkg_store = LKGStore()
breaker_registry = CircuitBreakerRegistry()
epoch_manager = DataEpochManager(lkg_store, breaker_registry)
mqs = MicrostructureQualityScore()
