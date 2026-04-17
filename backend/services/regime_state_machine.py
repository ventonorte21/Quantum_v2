"""
Regime State Machine — Hysteresis + Temporal Debounce
=====================================================
Prevents erratic regime switching at score boundaries.

Two mechanisms:
  1. Hysteresis (Dead Zone): Asymmetric entry/exit thresholds per regime.
     Score 7 coming from TRANSICAO = still TRANSICAO.
     Score 7 coming from BULL = still BULL.

  2. Temporal Debounce: New regime must persist for N consecutive evaluations
     before being confirmed. Prevents 1-minute VIX spikes from flipping regime.

Exception: Term Structure overrides (CAPITULACAO/COMPLACENCIA) bypass both
mechanisms — panic doesn't wait for confirmation.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


# ── Hysteresis Thresholds ──
# Each regime defines the score needed to ENTER it (from outside)
# and the score needed to EXIT it (return to neighbor).
# The gap between entry and exit is the "dead zone".

HYSTERESIS = {
    # To ENTER COMPLACENCIA: score >= 12 (from BULL) — requires near-perfect conditions
    # To EXIT COMPLACENCIA back to BULL: score <= 9
    "COMPLACENCIA": {"enter_min": 12, "exit_to_bull": 9},

    # To ENTER BULL: score >= 9 (from TRANSICAO) — requires excellence in >=2 primary sensors
    # To EXIT BULL back to TRANSICAO: score <= 6
    "BULL": {"enter_min": 9, "exit_to_transicao": 6},

    # TRANSICAO: score 4-8 (default zone, no hysteresis needed to enter)
    "TRANSICAO": {"range_min": 4, "range_max": 8},

    # To ENTER BEAR: score <= 3 (from TRANSICAO)
    # To EXIT BEAR back to TRANSICAO: score >= 6 (conservative recovery — avoids dead-cat-bounce)
    "BEAR": {"enter_max": 3, "exit_to_transicao": 6},

    # CAPITULACAO: score <= 1 or Term Structure override (> 1.10)
    # To EXIT CAPITULACAO back to BEAR: score >= 3
    "CAPITULACAO": {"enter_max": 1, "exit_to_bear": 3},
}

# Debounce: consecutive evaluations required to confirm regime change
DEBOUNCE_NORMAL = 3       # Standard transitions
DEBOUNCE_FROM_PANIC = 1   # Exiting CAPITULACAO (recovery is fast)


class RegimeStateMachine:
    """Stateful regime detector with hysteresis and temporal debounce."""

    def __init__(self, regime_config: Dict[str, Dict[str, Any]]):
        """Initialize with REGIME_CONFIG dict from V3SignalEngine."""
        self._regime_config = regime_config
        self.current_regime: str = "TRANSICAO"
        self.pending_regime: Optional[str] = None
        self.pending_count: int = 0
        self.last_score: int = 0
        self.last_override: Optional[str] = None
        self.transition_log: list = []

    def evaluate(self, macro_score: int, max_score: int,
                 term_structure_ratio: float) -> Dict[str, Any]:
        """Evaluate regime with hysteresis + debounce.

        Returns same structure as the original detect_regime().
        """
        override = None

        # ── Step 0: Term Structure Override (bypasses everything) ──
        if term_structure_ratio > 1.10:
            new_regime = "CAPITULACAO"
            override = (
                f"Term Structure Override: ratio {term_structure_ratio:.3f} > 1.10 "
                f"(backwardation extrema)"
            )
            self._force_regime(new_regime, override)
            self.last_score = macro_score
            self.last_override = override
            return self._build_result(macro_score, max_score, term_structure_ratio, override)

        if term_structure_ratio < 0.80:
            new_regime = "COMPLACENCIA"
            override = (
                f"Term Structure Override: ratio {term_structure_ratio:.3f} < 0.80 "
                f"(contango extremo)"
            )
            self._force_regime(new_regime, override)
            self.last_score = macro_score
            self.last_override = override
            return self._build_result(macro_score, max_score, term_structure_ratio, override)

        # ── Step 1: Compute theoretical regime with hysteresis ──
        theoretical = self._apply_hysteresis(macro_score)

        # ── Step 2: Debounce temporal ──
        if theoretical == self.current_regime:
            # No change — reset pending
            if self.pending_regime is not None:
                logger.debug(
                    "Regime debounce reset: pending %s cancelled (score=%d, current=%s)",
                    self.pending_regime, macro_score, self.current_regime
                )
            self.pending_regime = None
            self.pending_count = 0
        else:
            # Regime wants to change
            if self.pending_regime == theoretical:
                self.pending_count += 1
            else:
                # New pending regime
                self.pending_regime = theoretical
                self.pending_count = 1
                logger.info(
                    "Regime shift detected: %s -> %s (pending, score=%d, need %d confirmations)",
                    self.current_regime, theoretical, macro_score,
                    self._get_debounce_threshold()
                )

            # Check if debounce threshold reached
            threshold = self._get_debounce_threshold()
            if self.pending_count >= threshold:
                old = self.current_regime
                self.current_regime = theoretical
                self.pending_regime = None
                self.pending_count = 0
                reason = (
                    f"Regime Shift Confirmado: {old} -> {self.current_regime} "
                    f"(score={macro_score}, {threshold} confirmacoes)"
                )
                logger.info(reason)
                self._log_transition(old, self.current_regime, macro_score, reason)

        self.last_score = macro_score
        self.last_override = override
        return self._build_result(macro_score, max_score, term_structure_ratio, override)

    def _apply_hysteresis(self, score: int) -> str:
        """Determine theoretical regime considering dead zones."""
        current = self.current_regime

        if current == "COMPLACENCIA":
            if score <= HYSTERESIS["COMPLACENCIA"]["exit_to_bull"]:
                return "BULL"
            return "COMPLACENCIA"

        if current == "BULL":
            if score >= HYSTERESIS["COMPLACENCIA"]["enter_min"]:
                return "COMPLACENCIA"
            if score <= HYSTERESIS["BULL"]["exit_to_transicao"]:
                return "TRANSICAO"
            return "BULL"

        if current == "TRANSICAO":
            if score >= HYSTERESIS["BULL"]["enter_min"]:
                return "BULL"
            if score <= HYSTERESIS["BEAR"]["enter_max"]:
                return "BEAR"
            return "TRANSICAO"

        if current == "BEAR":
            if score >= HYSTERESIS["BEAR"]["exit_to_transicao"]:
                return "TRANSICAO"
            if score <= HYSTERESIS["CAPITULACAO"]["enter_max"]:
                return "CAPITULACAO"
            return "BEAR"

        if current == "CAPITULACAO":
            if score >= HYSTERESIS["CAPITULACAO"]["exit_to_bear"]:
                return "BEAR"
            return "CAPITULACAO"

        # Unknown state — fallback
        return self._raw_regime(score)

    def _raw_regime(self, score: int) -> str:
        """Stateless regime detection used for cold start / fallback.
        MUST mirror HYSTERESIS enter_min values exactly to avoid cold-start inconsistency.
        """
        if score >= 12:
            return "COMPLACENCIA"
        if score >= 9:
            return "BULL"
        if score >= 4:
            return "TRANSICAO"
        if score >= 2:
            return "BEAR"
        return "CAPITULACAO"

    def _get_debounce_threshold(self) -> int:
        """Get debounce count based on current regime."""
        if self.current_regime == "CAPITULACAO":
            return DEBOUNCE_FROM_PANIC
        return DEBOUNCE_NORMAL

    def _force_regime(self, new_regime: str, reason: str):
        """Force immediate regime change (bypasses debounce)."""
        if new_regime != self.current_regime:
            old = self.current_regime
            self.current_regime = new_regime
            self.pending_regime = None
            self.pending_count = 0
            logger.warning(
                "OVERRIDE: %s -> %s (immediate, %s)", old, new_regime, reason
            )
            self._log_transition(old, new_regime, self.last_score, f"OVERRIDE: {reason}")

    def _log_transition(self, old: str, new: str, score: int, reason: str):
        """Record transition for audit trail."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from": old,
            "to": new,
            "score": score,
            "reason": reason,
        }
        self.transition_log.append(entry)
        if len(self.transition_log) > 100:
            self.transition_log = self.transition_log[-100:]

    def _build_result(self, macro_score: int, max_score: int,
                      term_structure_ratio: float, override: Optional[str]) -> Dict[str, Any]:
        """Build the regime result dict (same shape as original detect_regime)."""
        config = self._regime_config[self.current_regime]

        result = {
            "regime": self.current_regime,
            "macro_score": macro_score,
            "max_score": max_score,
            "term_structure_ratio": round(term_structure_ratio, 3),
            "override": override,
            "target_symbol": config["symbol"],
            "tactic": config["tactic"],
            "lot_pct": config["lot_pct"],
            "direction": config["direction"],
        }

        # Add state machine metadata for frontend
        if self.pending_regime:
            result["pending_regime"] = self.pending_regime
            result["pending_minutes"] = self.pending_count
            result["debounce_threshold"] = self._get_debounce_threshold()

        return result

    def get_state(self) -> Dict[str, Any]:
        """Get current state for API/debug."""
        return {
            "current_regime": self.current_regime,
            "pending_regime": self.pending_regime,
            "pending_count": self.pending_count,
            "debounce_threshold": self._get_debounce_threshold(),
            "last_score": self.last_score,
            "last_override": self.last_override,
            "recent_transitions": self.transition_log[-10:],
            "hysteresis_config": HYSTERESIS,
            "debounce_normal": DEBOUNCE_NORMAL,
            "debounce_panic": DEBOUNCE_FROM_PANIC,
        }

    async def persist_transition(self, database, old: str, new: str,
                                 score: int, reason: str):
        """Persist transition to MongoDB for long-term analysis."""
        doc = {
            "timestamp": datetime.now(timezone.utc),
            "from_regime": old,
            "to_regime": new,
            "score": score,
            "reason": reason,
        }
        try:
            await database.regime_transitions.insert_one(doc)
        except Exception as e:
            logger.error("Failed to persist regime transition: %s", e)
