"""
Replay Engine — Walk-Forward Backtest
======================================
Consumes enriched V3 snapshots from MongoDB and simulates trades
with configurable parameters. ML-ready: serializable configs,
batch execution, objective functions.

Resolution: 1 snapshot = 1 minute (60s interval).

Signal Re-Evaluation (DYNAMIC mode):
  Instead of blindly accepting the pre-computed action (BUY/SELL) from
  snapshots, extracts raw Z-Score, Delta Ratio, and OFI values and
  re-evaluates them against user-configurable thresholds. This enables
  Walk-Forward Optimization by testing thousands of parameter combos.
"""

import re
import math
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


# ── Tick values per symbol (dollar P&L per point) ──
TICK_VALUES = {
    "MNQ": {"tick_size": 0.25, "tick_value": 0.50, "point_value": 2.0},
    "MES": {"tick_size": 0.25, "tick_value": 1.25, "point_value": 5.0},
    "MYM": {"tick_size": 1.0, "tick_value": 0.50, "point_value": 0.50},
    "M2K": {"tick_size": 0.10, "tick_value": 0.50, "point_value": 5.0},
}

# ── V3 Engine Rule: Regime → Allowed Symbol ──
# Each regime dictates which instrument can be traded.
# TRANSICAO/CAPITULACAO → MES only | BULL/BEAR/COMPLACENCIA → MNQ only
REGIME_ALLOWED_SYMBOL = {
    "COMPLACENCIA": "MNQ",
    "BULL": "MNQ",
    "TRANSICAO": "MES",
    "BEAR": "MNQ",
    "CAPITULACAO": "MES",
}


class ObjectiveFunction(str, Enum):
    SHARPE = "sharpe"
    SORTINO = "sortino"
    PROFIT_FACTOR = "profit_factor"
    NET_PNL = "net_pnl"
    MIN_DRAWDOWN = "min_drawdown"
    EXPECTANCY = "expectancy"


# ── Default Configuration ──

DEFAULT_CONFIG = {
    # ── Signal Re-Evaluation Mode ──
    # DYNAMIC = re-evaluate raw signals against thresholds below
    # STATIC  = accept original action from snapshot (legacy behavior)
    "signal_mode": "DYNAMIC",

    # N2 Thresholds — Z-Score minimum per regime
    "zscore_min": {
        "COMPLACENCIA": -0.5,
        "BULL": 1.0,
        "BEAR": 1.0,
        "TRANSICAO": 1.0,
        "CAPITULACAO": 1.5,
    },

    # N2 Thresholds — Delta Ratio minimum per regime
    # BULL/COMPLACENCIA: ratio must be ABOVE this (bullish)
    # BEAR: ratio must be BELOW negative of this (bearish)
    # TRANSICAO: ratio must be opposite ± this (rejection)
    # CAPITULACAO: ratio must be ABOVE this (exhaustion)
    "delta_ratio_min": {
        "COMPLACENCIA": 0.15,
        "BULL": 0.20,
        "BEAR": 0.20,
        "TRANSICAO": 0.25,
        "CAPITULACAO": -0.10,
    },

    # OFI confirmation threshold per regime (absolute value)
    "ofi_threshold": {
        "COMPLACENCIA": 0.3,
        "BULL": 0.4,
        "BEAR": 0.4,
        "TRANSICAO": 0.2,
        "CAPITULACAO": 0.2,
    },

    # R:R viability — min |entry - POC| in ATR_M5 multiples (TRANSICAO)
    "rr_min_atr_mult": 1.5,

    # Require DZ volume significance (reject POSITIONAL_ONLY signals)
    "require_volume_significance": False,

    # Session time filter (ET hours, 24h format). None = disabled
    "session_start_hour": None,  # e.g. 9.5 = 09:30 ET
    "session_end_hour": None,    # e.g. 15.5 = 15:30 ET

    # ── Risk ──
    "sl_atr_mult": {"TREND": 1.5, "RANGE": 0.5, "FADE": 1.0},
    "max_daily_loss_pct": 5.0,
    "max_consecutive_losses": 3,

    # Targets
    "tp_mode": "DEFAULT",  # DEFAULT = use archetype logic, FIXED_RR = fixed R:R
    "fixed_rr": 2.0,       # Only used if tp_mode == FIXED_RR
    "rr_min_threshold": 1.5,

    # Scale-Out
    "scale_out_enabled": True,
    "scale_out_pct": 50,
    "scale_out_trigger_mult": 2.0,

    # Trailing (TREND)
    "trailing_enabled": True,
    "trail_trigger_atr_mult": 1.0,
    "trail_stop_atr_mult": 0.75,

    # Break-even (FADE)
    "breakeven_enabled": True,
    "breakeven_trigger_atr_mult": 1.0,
    "breakeven_ticks_buffer": 4,

    # Sizing
    "initial_capital": 25000.0,
    "sizing_mode": "risk_pct",  # "fixed" or "risk_pct"
    "contracts_per_signal": 2,  # used when sizing_mode = "fixed"
    "risk_pct_per_trade": 1.0,  # % of capital risked per trade (sizing_mode = "risk_pct")
    "max_contracts": 10,        # hard ceiling regardless of sizing mode
    "use_lot_pct": True,

    # Execution
    "slippage_ticks": 1,
    "commission_per_contract": 2.50,  # round-trip

    # Scope
    "symbols": ["MES", "MNQ"],
    "regimes_filter": [],  # empty = all regimes
    "min_snapshots_between_trades": 5,  # cooldown: 5 minutes between trades
    "max_concurrent_positions": 1,  # V3 Engine rule: 1 active position at a time (across all symbols)

    # Time
    "start_date": None,
    "end_date": None,
}


def merge_config(user_config: Dict) -> Dict:
    """Deep-merge user config over defaults."""
    config = {}
    for key, default_val in DEFAULT_CONFIG.items():
        if key in user_config:
            if isinstance(default_val, dict) and isinstance(user_config[key], dict):
                merged = {**default_val, **user_config[key]}
                config[key] = merged
            else:
                config[key] = user_config[key]
        else:
            config[key] = default_val
    return config


# ── Regex patterns to extract raw values from legacy n3.reason strings ──
_RE_ZSCORE = re.compile(r'Z[=:]?\s*([-+]?\d+\.?\d*)', re.IGNORECASE)
_RE_DELTA = re.compile(r'delta[=:]?\s*([-+]?\d+\.?\d*)', re.IGNORECASE)


def _extract_raw_from_reason(reason: str) -> Tuple[Optional[float], Optional[float]]:
    """Parse Z-Score and delta from legacy n3.reason text.

    Examples:
      "N2 DZ VAL: alta atividade (Z=1.1, delta=37). Compra na borda da VA."
      → (1.1, 37.0)
    """
    zscore = None
    delta_raw = None
    m_z = _RE_ZSCORE.search(reason or "")
    if m_z:
        zscore = float(m_z.group(1))
    m_d = _RE_DELTA.search(reason or "")
    if m_d:
        delta_raw = float(m_d.group(1))
    return zscore, delta_raw


def _extract_dz_metrics_for_regime(snap: Dict) -> Tuple[float, float, bool, str]:
    """Extract zscore + delta_ratio from dz_n2.levels based on regime logic.

    Returns: (zscore, delta_ratio, volume_significant, trigger_level)
    """
    regime = snap.get("regime", "TRANSICAO")
    dz_n2 = snap.get("dz_n2", {})
    levels = dz_n2.get("levels", {})
    ctx = snap.get("context", {})

    vah_dz = levels.get("vah", {})
    val_dz = levels.get("val", {})
    vwap_dz = levels.get("vwap", {})

    if regime in ("COMPLACENCIA", "BULL"):
        ref = vah_dz if vah_dz.get("volume_significant") else vwap_dz
        level = "vah" if vah_dz.get("volume_significant") else "vwap"
    elif regime == "TRANSICAO":
        vah_price = ctx.get("vah") or vah_dz.get("level_price", 0)
        val_price = ctx.get("val") or val_dz.get("level_price", 0)
        current = ctx.get("last_price") or ctx.get("vwap", 0)
        dist_vah = abs(current - vah_price) if vah_price else float('inf')
        dist_val = abs(current - val_price) if val_price else float('inf')
        if dist_vah <= dist_val:
            ref = vah_dz
            level = "vah"
        else:
            ref = val_dz
            level = "val"
    elif regime in ("BEAR", "CAPITULACAO"):
        ref = val_dz if val_dz.get("volume_significant") else vwap_dz
        level = "val" if val_dz.get("volume_significant") else "vwap"
    else:
        ref = vwap_dz
        level = "vwap"

    return (
        ref.get("zscore", 0.0),
        ref.get("delta_ratio", 0.0),
        ref.get("volume_significant", False),
        level,
    )


def extract_raw_signals(snap: Dict) -> Dict[str, Any]:
    """Extract raw signal data from a snapshot, regardless of its generation.

    Priority:
      1. n2.n2_signal (enriched snapshots)
      2. dz_n2.levels (intermediate snapshots)
      3. n3.reason regex parsing (legacy snapshots)

    Returns dict with keys:
      zscore, delta_ratio, ofi_fast, volume_significant, trigger_level, source
    """
    # ── Source 1: Enriched n2_signal ──
    n2_sig = snap.get("n2", {}).get("n2_signal", {})
    if n2_sig and n2_sig.get("trigger_zscore") is not None:
        return {
            "zscore": n2_sig.get("trigger_zscore", 0.0),
            "delta_ratio": n2_sig.get("trigger_delta_ratio", 0.0),
            "ofi_fast": snap.get("context", {}).get("ofi_fast"),
            "volume_significant": True,  # enriched always has vol sig
            "trigger_level": n2_sig.get("trigger_level", ""),
            "source": "n2_signal",
        }

    # ── Source 2: dz_n2 levels ──
    dz_n2 = snap.get("dz_n2", {})
    if dz_n2.get("levels"):
        z, ratio, vol_sig, level = _extract_dz_metrics_for_regime(snap)
        return {
            "zscore": z,
            "delta_ratio": ratio,
            "ofi_fast": snap.get("context", {}).get("ofi_fast"),
            "volume_significant": vol_sig,
            "trigger_level": level,
            "source": "dz_n2",
        }

    # ── Source 3: Parse n3.reason text (legacy) ──
    reason = snap.get("n3", {}).get("reason", "")
    zscore, delta_raw = _extract_raw_from_reason(reason)
    if zscore is not None:
        # Legacy delta is net_delta (integer), not ratio (float).
        # Normalize: if |delta| > 2, treat as net_delta → approx ratio
        delta_ratio = 0.0
        if delta_raw is not None:
            if abs(delta_raw) > 2.0:
                # Heuristic: net_delta > 0 = bullish flow. Map to 0..1 range
                delta_ratio = 1.0 if delta_raw > 50 else (
                    -1.0 if delta_raw < -50 else delta_raw / 50.0
                )
            else:
                delta_ratio = delta_raw

        # Infer trigger_level from reason text
        level = "val"
        if "VAH" in reason.upper():
            level = "vah"
        elif "VWAP" in reason.upper():
            level = "vwap"

        return {
            "zscore": zscore,
            "delta_ratio": delta_ratio,
            "ofi_fast": None,  # legacy has no OFI
            "volume_significant": True,  # legacy was already filtered
            "trigger_level": level,
            "source": "legacy_parse",
        }

    # ── No raw data available ──
    return {
        "zscore": None,
        "delta_ratio": None,
        "ofi_fast": None,
        "volume_significant": False,
        "trigger_level": "",
        "source": "none",
    }


def reevaluate_signal(snap: Dict, config: Dict) -> Tuple[str, str]:
    """Re-evaluate a snapshot's signal against configurable thresholds.

    Returns: (action, reason)
      action: "BUY", "SELL", or "WAIT"
      reason: human-readable explanation
    """
    regime = snap.get("regime", "UNKNOWN")
    raw = extract_raw_signals(snap)

    # If no raw data at all, fall back to static
    if raw["source"] == "none" or raw["zscore"] is None:
        original_action = snap.get("action", "WAIT")
        return original_action, f"[STATIC fallback] sem dados raw ({regime})"

    zscore = raw["zscore"]
    delta_ratio = raw["delta_ratio"] or 0.0
    ofi_fast = raw["ofi_fast"]
    vol_sig = raw["volume_significant"]
    level = raw["trigger_level"]

    # ── Volume significance check ──
    if config.get("require_volume_significance") and not vol_sig:
        return "WAIT", f"[FILTRADO] volume nao significativo ({regime}, {level})"

    # ── Get thresholds for this regime ──
    z_min = config.get("zscore_min", {}).get(regime, 1.0)
    dr_min = config.get("delta_ratio_min", {}).get(regime, 0.20)
    ofi_thresh = config.get("ofi_threshold", {}).get(regime, 0.2)

    # ── Evaluate per regime ──
    if regime == "COMPLACENCIA":
        # Melt-up: Z relaxed (> z_min, default -0.5), ratio bullish (> dr_min)
        z_ok = zscore > z_min
        ratio_ok = delta_ratio > dr_min
        if z_ok and ratio_ok:
            action = "BUY"
            reason = f"[DYNAMIC] COMPLACENCIA: Z={zscore:.2f}>{z_min}, DR={delta_ratio:.3f}>{dr_min}"
        else:
            action = "WAIT"
            reason = f"[FILTRADO] COMPLACENCIA: Z={zscore:.2f}(min={z_min}), DR={delta_ratio:.3f}(min={dr_min})"
            return action, reason

    elif regime == "BULL":
        z_ok = zscore > z_min
        ratio_ok = delta_ratio > dr_min
        if z_ok and ratio_ok:
            action = "BUY"
            reason = f"[DYNAMIC] BULL: Z={zscore:.2f}>{z_min}, DR={delta_ratio:.3f}>{dr_min}"
        else:
            action = "WAIT"
            reason = f"[FILTRADO] BULL: Z={zscore:.2f}(min={z_min}), DR={delta_ratio:.3f}(min={dr_min})"
            return action, reason

    elif regime == "TRANSICAO":
        # Rejection: Z must be high, delta must be OPPOSITE to border
        z_ok = abs(zscore) > z_min

        if level == "vah":
            # Testing VAH → rejection = sellers took over → SELL
            rejection = delta_ratio < -dr_min
            action = "SELL" if z_ok and rejection else "WAIT"
        elif level == "val":
            # Testing VAL → rejection = buyers took over → BUY
            rejection = delta_ratio > dr_min
            action = "BUY" if z_ok and rejection else "WAIT"
        else:
            action = "WAIT"
            return action, f"[FILTRADO] TRANSICAO: borda indefinida (level={level})"

        if action == "WAIT":
            reason = (
                f"[FILTRADO] TRANSICAO@{level}: Z={zscore:.2f}(min={z_min}), "
                f"DR={delta_ratio:.3f}(need {'<-' if level == 'vah' else '>'}{dr_min})"
            )
            return action, reason

        # ── R:R viability check (TRANSICAO only) ──
        ctx = snap.get("context", {})
        poc = ctx.get("poc", 0)
        entry_price = snap.get("n3", {}).get("entry_price") or ctx.get("last_price") or 0
        atr_m5 = ctx.get("atr_m5") or ctx.get("atr") or 0
        rr_mult = config.get("rr_min_atr_mult", 1.5)
        if poc and entry_price and atr_m5 and rr_mult > 0:
            rr_distance = abs(entry_price - poc)
            rr_threshold = rr_mult * atr_m5
            if rr_distance < rr_threshold:
                return "WAIT", (
                    f"[FILTRADO] TRANSICAO R:R: |entry-POC|={rr_distance:.2f} < "
                    f"{rr_mult}x ATR_M5={rr_threshold:.2f}"
                )

        reason = (
            f"[DYNAMIC] TRANSICAO@{level}: Z={zscore:.2f}, "
            f"DR={delta_ratio:.3f}, rejection=True"
        )

    elif regime == "BEAR":
        z_ok = zscore > z_min
        ratio_ok = delta_ratio < -dr_min
        if z_ok and ratio_ok:
            action = "SELL"
            reason = f"[DYNAMIC] BEAR: Z={zscore:.2f}>{z_min}, DR={delta_ratio:.3f}<-{dr_min}"
        else:
            action = "WAIT"
            reason = f"[FILTRADO] BEAR: Z={zscore:.2f}(min={z_min}), DR={delta_ratio:.3f}(max=-{dr_min})"
            return action, reason

    elif regime == "CAPITULACAO":
        z_ok = abs(zscore) > z_min
        exhaustion = delta_ratio > dr_min  # sellers weakened
        if z_ok and exhaustion:
            action = "BUY"
            reason = f"[DYNAMIC] CAPITULACAO: Z={zscore:.2f}>{z_min}, DR={delta_ratio:.3f}>{dr_min} (exaustao)"
        else:
            action = "WAIT"
            reason = f"[FILTRADO] CAPITULACAO: Z={zscore:.2f}(min={z_min}), DR={delta_ratio:.3f}(min={dr_min})"
            return action, reason
    else:
        return "WAIT", f"[FILTRADO] regime desconhecido: {regime}"

    # ── OFI confirmation (if available) ──
    if ofi_fast is not None and ofi_thresh > 0:
        if action == "BUY" and ofi_fast < ofi_thresh:
            return "WAIT", f"{reason} → [OFI REJECT] ofi={ofi_fast:.3f} < {ofi_thresh}"
        if action == "SELL" and ofi_fast > -ofi_thresh:
            return "WAIT", f"{reason} → [OFI REJECT] ofi={ofi_fast:.3f} > -{ofi_thresh}"

    return action, reason


def _check_session_filter(snap: Dict, config: Dict) -> bool:
    """Check if snapshot falls within session time filter.
    Returns True if OK to trade, False if outside session window.
    """
    start_h = config.get("session_start_hour")
    end_h = config.get("session_end_hour")
    if start_h is None or end_h is None:
        return True

    snap_time = snap.get("recorded_at")
    if not isinstance(snap_time, datetime):
        return True

    # Convert to ET (UTC-5 / UTC-4 depending on DST, approximate as UTC-4)
    et_hour = snap_time.hour - 4
    if et_hour < 0:
        et_hour += 24
    et_decimal = et_hour + snap_time.minute / 60.0

    return start_h <= et_decimal <= end_h


# ── Position Tracking ──

class VirtualPosition:
    """Tracks a single virtual position through snapshots."""

    def __init__(self, entry_snap: Dict, config: Dict, trade_preview: Dict, override_side: str = None):
        self.id = str(uuid.uuid4())[:8]
        self.symbol = entry_snap["symbol"]
        self.regime = entry_snap.get("regime", "UNKNOWN")
        self.archetype = trade_preview.get("archetype", "RANGE")
        self.side = override_side or entry_snap.get("action", "BUY")
        self.is_long = self.side == "BUY"
        self.sign = 1 if self.is_long else -1

        tick_info = TICK_VALUES.get(self.symbol, TICK_VALUES["MES"])
        self.point_value = tick_info["point_value"]
        self.tick_size = tick_info["tick_size"]

        # Slippage
        slippage = config.get("slippage_ticks", 1) * self.tick_size
        raw_entry = trade_preview.get("entry_price", entry_snap.get("context", {}).get("last_price", 0))
        self.entry_price = raw_entry + (slippage if not self.is_long else -slippage) if raw_entry else 0

        # SL / TP from trade_preview (position_manager output)
        atr_m1 = trade_preview.get("atr_m1", 0) or entry_snap.get("context", {}).get("atr_m1", 2.0)
        sl_mult = config.get("sl_atr_mult", {}).get(self.archetype, 1.0)
        self.hard_stop = trade_preview.get("hard_stop") or (self.entry_price - self.sign * sl_mult * atr_m1)
        self.current_stop = self.hard_stop

        # Contracts — risk-based or fixed
        sizing_mode = config.get("sizing_mode", "risk_pct")
        lot_pct = 1.0
        if config.get("use_lot_pct", True):
            lot_pct = (entry_snap.get("n1", {}).get("lot_pct", 100) or 100) / 100.0

        max_contracts = config.get("max_contracts", 10)

        if sizing_mode == "risk_pct" and self.entry_price and self.hard_stop:
            capital = config.get("_current_capital", config.get("initial_capital", 25000))
            risk_pct = config.get("risk_pct_per_trade", 1.0) / 100.0
            risk_amount = capital * risk_pct
            risk_per_contract = abs(self.entry_price - self.hard_stop) * self.point_value
            if risk_per_contract > 0:
                raw_qty = int(risk_amount / risk_per_contract)
                self.quantity = max(1, min(int(raw_qty * lot_pct), max_contracts))
            else:
                self.quantity = 1
        else:
            raw_qty = config.get("contracts_per_signal", 2)
            self.quantity = max(1, min(int(raw_qty * lot_pct), max_contracts))

        self.remaining_qty = self.quantity

        if config.get("tp_mode") == "FIXED_RR":
            risk = abs(self.entry_price - self.hard_stop)
            self.take_profit = self.entry_price + self.sign * risk * config.get("fixed_rr", 2.0)
        else:
            self.take_profit = trade_preview.get("take_profit")

        # Scale-out
        self.scale_out_done = False
        self.scale_out_pnl = 0.0
        if config.get("scale_out_enabled") and self.archetype == "TREND" and self.quantity > 1:
            risk = abs(self.entry_price - self.hard_stop)
            so_mult = config.get("scale_out_trigger_mult", 2.0)
            self.scale_out_price = self.entry_price + self.sign * risk * so_mult
            self.scale_out_qty = max(1, int(self.quantity * config.get("scale_out_pct", 50) / 100))
        else:
            self.scale_out_price = None
            self.scale_out_qty = 0

        # Trailing (TREND)
        self.trailing_active = False
        self.best_price = self.entry_price
        if config.get("trailing_enabled") and self.archetype == "TREND":
            self.trail_trigger_dist = atr_m1 * config.get("trail_trigger_atr_mult", 1.0)
            self.trail_stop_dist = atr_m1 * config.get("trail_stop_atr_mult", 0.75)
        else:
            self.trail_trigger_dist = 0
            self.trail_stop_dist = 0

        # Break-even (FADE)
        self.breakeven_active = False
        if config.get("breakeven_enabled") and self.archetype == "FADE":
            be_dist = atr_m1 * config.get("breakeven_trigger_atr_mult", 1.0) + config.get("breakeven_ticks_buffer", 4) * self.tick_size
            self.breakeven_trigger = self.entry_price + self.sign * be_dist
        else:
            self.breakeven_trigger = None

        # Metadata
        self.entry_time = entry_snap.get("recorded_at")
        self.entry_reason = entry_snap.get("n3", {}).get("reason", "")
        self.exit_time = None
        self.exit_price = None
        self.exit_reason = None
        self.commission = config.get("commission_per_contract", 2.50) * self.quantity

    def update(self, snapshot: Dict) -> Optional[Dict]:
        """Process a new snapshot. Returns trade result dict if position closed, else None."""
        ctx = snapshot.get("context", {})
        price = ctx.get("last_price") or ctx.get("vwap") or snapshot.get("n3", {}).get("entry_price")
        if not price or not self.entry_price:
            return None

        # Track best price for trailing
        if self.is_long:
            self.best_price = max(self.best_price, price)
        else:
            self.best_price = min(self.best_price, price)

        # 1. Check Scale-Out (TREND only)
        if not self.scale_out_done and self.scale_out_price and self.scale_out_qty > 0:
            hit = (self.is_long and price >= self.scale_out_price) or (not self.is_long and price <= self.scale_out_price)
            if hit:
                so_pnl = (self.scale_out_price - self.entry_price) * self.sign * self.point_value * self.scale_out_qty
                self.scale_out_pnl = so_pnl
                self.remaining_qty -= self.scale_out_qty
                self.scale_out_done = True
                # Move stop to entry (break-even for remaining)
                self.current_stop = self.entry_price

        # 2. Check Trailing Stop (TREND)
        if self.trailing_active or (self.trail_trigger_dist > 0):
            favorable_dist = (self.best_price - self.entry_price) * self.sign
            if favorable_dist >= self.trail_trigger_dist:
                self.trailing_active = True
                new_stop = self.best_price - self.sign * self.trail_stop_dist
                if self.is_long:
                    self.current_stop = max(self.current_stop, new_stop)
                else:
                    self.current_stop = min(self.current_stop, new_stop)

        # 3. Check Break-even (FADE)
        if not self.breakeven_active and self.breakeven_trigger:
            be_hit = (self.is_long and price >= self.breakeven_trigger) or (not self.is_long and price <= self.breakeven_trigger)
            if be_hit:
                self.breakeven_active = True
                self.current_stop = self.entry_price + self.sign * self.tick_size

        # 4. Check Stop Loss
        sl_hit = (self.is_long and price <= self.current_stop) or (not self.is_long and price >= self.current_stop)
        if sl_hit:
            return self._close(self.current_stop, snapshot, "STOP_LOSS")

        # 5. Check Take Profit
        if self.take_profit:
            tp_hit = (self.is_long and price >= self.take_profit) or (not self.is_long and price <= self.take_profit)
            if tp_hit:
                return self._close(self.take_profit, snapshot, "TAKE_PROFIT")

        return None

    def _close(self, exit_price: float, snapshot: Dict, reason: str) -> Dict:
        """Close position and return trade result."""
        self.exit_price = exit_price
        self.exit_time = snapshot.get("recorded_at")
        self.exit_reason = reason

        main_pnl = (exit_price - self.entry_price) * self.sign * self.point_value * self.remaining_qty
        gross_pnl = main_pnl + self.scale_out_pnl
        net_pnl = gross_pnl - self.commission

        duration_min = 0
        if self.entry_time and self.exit_time:
            if isinstance(self.entry_time, datetime) and isinstance(self.exit_time, datetime):
                duration_min = (self.exit_time - self.entry_time).total_seconds() / 60

        return {
            "id": self.id,
            "symbol": self.symbol,
            "regime": self.regime,
            "archetype": self.archetype,
            "side": self.side,
            "quantity": self.quantity,
            "entry_price": round(self.entry_price, 2),
            "exit_price": round(exit_price, 2),
            "hard_stop": round(self.hard_stop, 2) if self.hard_stop else None,
            "take_profit": round(self.take_profit, 2) if self.take_profit else None,
            "entry_time": self.entry_time.isoformat() if isinstance(self.entry_time, datetime) else str(self.entry_time),
            "exit_time": self.exit_time.isoformat() if isinstance(self.exit_time, datetime) else str(self.exit_time),
            "exit_reason": reason,
            "entry_reason": self.entry_reason[:120],
            "gross_pnl": round(gross_pnl, 2),
            "net_pnl": round(net_pnl, 2),
            "commission": round(self.commission, 2),
            "scale_out_pnl": round(self.scale_out_pnl, 2),
            "scale_out_done": self.scale_out_done,
            "trailing_active": self.trailing_active,
            "breakeven_active": self.breakeven_active,
            "duration_minutes": round(duration_min, 1),
            "max_favorable": round((self.best_price - self.entry_price) * self.sign, 2),
            "max_adverse": round(abs(self.entry_price - self.hard_stop), 2) if self.hard_stop else 0,
        }

    def force_close(self, snapshot: Dict) -> Dict:
        """Force close at current price (end of data or timeout)."""
        ctx = snapshot.get("context", {})
        price = ctx.get("last_price") or ctx.get("vwap") or self.entry_price
        return self._close(price, snapshot, "END_OF_DATA")


# ── Metrics Calculator ──

def compute_metrics(trades: List[Dict], config: Dict) -> Dict[str, Any]:
    """Compute performance metrics from trade list."""
    if not trades:
        return _empty_metrics()

    net_pnls = [t["net_pnl"] for t in trades]
    gross_pnls = [t["gross_pnl"] for t in trades]

    winners = [p for p in net_pnls if p > 0]
    losers = [p for p in net_pnls if p <= 0]

    total_pnl = sum(net_pnls)
    gross_total = sum(gross_pnls)
    total_commission = sum(t["commission"] for t in trades)

    win_rate = len(winners) / len(trades) * 100 if trades else 0
    avg_winner = sum(winners) / len(winners) if winners else 0
    avg_loser = sum(losers) / len(losers) if losers else 0
    profit_factor = abs(sum(winners) / sum(losers)) if losers and sum(losers) != 0 else float('inf') if winners else 0
    expectancy = total_pnl / len(trades) if trades else 0

    # Equity curve + drawdown
    equity_curve = []
    running_pnl = 0
    peak = 0
    max_dd = 0
    max_dd_pct = 0
    capital = config.get("initial_capital", 25000)

    for t in trades:
        running_pnl += t["net_pnl"]
        current_equity = capital + running_pnl
        equity_curve.append({
            "time": t["exit_time"],
            "pnl": round(running_pnl, 2),
            "equity": round(current_equity, 2),
            "trade_id": t["id"],
        })
        peak = max(peak, running_pnl)
        dd = peak - running_pnl
        if dd > max_dd:
            max_dd = dd
        dd_pct = dd / (capital + peak) * 100 if (capital + peak) > 0 else 0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    # Sharpe Ratio (annualized, assuming ~252 trading days, ~6.5h/day, ~390 min/day)
    if len(net_pnls) > 1:
        mean_ret = sum(net_pnls) / len(net_pnls)
        variance = sum((r - mean_ret) ** 2 for r in net_pnls) / (len(net_pnls) - 1)
        std_ret = math.sqrt(variance) if variance > 0 else 0
        sharpe = (mean_ret / std_ret) * math.sqrt(252) if std_ret > 0 else 0
    else:
        sharpe = 0

    # Sortino Ratio (only downside deviation)
    if len(net_pnls) > 1:
        mean_ret = sum(net_pnls) / len(net_pnls)
        downside_returns = [min(0, r - 0) for r in net_pnls]  # target = 0 (no loss)
        downside_variance = sum(r ** 2 for r in downside_returns) / (len(net_pnls) - 1)
        downside_dev = math.sqrt(downside_variance) if downside_variance > 0 else 0
        sortino = (mean_ret / downside_dev) * math.sqrt(252) if downside_dev > 0 else 0
    else:
        sortino = 0

    # Consecutive losses
    max_consec_losses = 0
    current_streak = 0
    for p in net_pnls:
        if p <= 0:
            current_streak += 1
            max_consec_losses = max(max_consec_losses, current_streak)
        else:
            current_streak = 0

    # Breakdown by regime
    regime_breakdown = {}
    for t in trades:
        r = t["regime"]
        if r not in regime_breakdown:
            regime_breakdown[r] = {"trades": 0, "wins": 0, "pnl": 0, "avg_pnl": 0}
        regime_breakdown[r]["trades"] += 1
        regime_breakdown[r]["pnl"] = round(regime_breakdown[r]["pnl"] + t["net_pnl"], 2)
        if t["net_pnl"] > 0:
            regime_breakdown[r]["wins"] += 1
    for r in regime_breakdown:
        rb = regime_breakdown[r]
        rb["win_rate"] = round(rb["wins"] / rb["trades"] * 100, 1) if rb["trades"] > 0 else 0
        rb["avg_pnl"] = round(rb["pnl"] / rb["trades"], 2) if rb["trades"] > 0 else 0

    # Breakdown by archetype
    arch_breakdown = {}
    for t in trades:
        a = t["archetype"]
        if a not in arch_breakdown:
            arch_breakdown[a] = {"trades": 0, "wins": 0, "pnl": 0}
        arch_breakdown[a]["trades"] += 1
        arch_breakdown[a]["pnl"] = round(arch_breakdown[a]["pnl"] + t["net_pnl"], 2)
        if t["net_pnl"] > 0:
            arch_breakdown[a]["wins"] += 1
    for a in arch_breakdown:
        ab = arch_breakdown[a]
        ab["win_rate"] = round(ab["wins"] / ab["trades"] * 100, 1) if ab["trades"] > 0 else 0

    # Breakdown by exit reason
    exit_breakdown = {}
    for t in trades:
        er = t["exit_reason"]
        if er not in exit_breakdown:
            exit_breakdown[er] = {"count": 0, "pnl": 0}
        exit_breakdown[er]["count"] += 1
        exit_breakdown[er]["pnl"] = round(exit_breakdown[er]["pnl"] + t["net_pnl"], 2)

    return {
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "gross_pnl": round(gross_total, 2),
        "total_commission": round(total_commission, 2),
        "avg_winner": round(avg_winner, 2),
        "avg_loser": round(avg_loser, 2),
        "largest_winner": round(max(net_pnls), 2) if net_pnls else 0,
        "largest_loser": round(min(net_pnls), 2) if net_pnls else 0,
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else 999.99,
        "expectancy": round(expectancy, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "max_consecutive_losses": max_consec_losses,
        "avg_duration_min": round(sum(t["duration_minutes"] for t in trades) / len(trades), 1) if trades else 0,
        "equity_curve": equity_curve,
        "regime_breakdown": regime_breakdown,
        "archetype_breakdown": arch_breakdown,
        "exit_breakdown": exit_breakdown,
        "initial_capital": config.get("initial_capital", 25000),
        "final_capital": round(capital + total_pnl, 2),
        "return_pct": round(total_pnl / capital * 100, 2) if capital > 0 else 0,
    }


def _empty_metrics() -> Dict:
    return {
        "total_trades": 0, "winners": 0, "losers": 0, "win_rate": 0,
        "total_pnl": 0, "gross_pnl": 0, "total_commission": 0,
        "avg_winner": 0, "avg_loser": 0, "largest_winner": 0, "largest_loser": 0,
        "profit_factor": 0, "expectancy": 0, "sharpe_ratio": 0, "sortino_ratio": 0,
        "max_drawdown": 0, "max_drawdown_pct": 0, "max_consecutive_losses": 0,
        "avg_duration_min": 0, "equity_curve": [], "regime_breakdown": {},
        "archetype_breakdown": {}, "exit_breakdown": {},
        "initial_capital": 25000, "final_capital": 25000, "return_pct": 0,
    }


# ── Main Replay Runner ──

async def run_replay(database, user_config: Dict) -> Dict[str, Any]:
    """Execute a Walk-Forward replay on stored snapshots.

    Returns a complete run result with config, trades, and metrics.
    """
    config = merge_config(user_config)
    run_id = str(uuid.uuid4())[:12]
    started_at = datetime.now(timezone.utc)

    # Build MongoDB query
    query: Dict[str, Any] = {}
    symbols = config.get("symbols", ["MES", "MNQ"])
    if len(symbols) == 1:
        query["symbol"] = symbols[0]
    else:
        query["symbol"] = {"$in": symbols}

    if config.get("start_date"):
        query.setdefault("recorded_at", {})
        start = config["start_date"]
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        query["recorded_at"]["$gte"] = start

    if config.get("end_date"):
        query.setdefault("recorded_at", {})
        end = config["end_date"]
        if isinstance(end, str):
            end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        query["recorded_at"]["$lte"] = end

    if config.get("regimes_filter"):
        query["regime"] = {"$in": config["regimes_filter"]}

    # Fetch snapshots sorted chronologically
    cursor = database.v3_snapshots.find(query, {"_id": 0}).sort("recorded_at", 1)
    snapshots = await cursor.to_list(length=50000)

    if not snapshots:
        return _build_run_result(run_id, config, started_at, [], _empty_metrics(), len(snapshots))

    # Track re-evaluation stats
    eval_stats = {
        "total_evaluated": 0,
        "dynamic_accepted": 0,
        "dynamic_filtered": 0,
        "static_fallback": 0,
        "session_filtered": 0,
        "by_source": {"n2_signal": 0, "dz_n2": 0, "legacy_parse": 0, "none": 0},
    }

    # Simulate
    trades = []
    active_positions: Dict[str, VirtualPosition] = {}  # symbol -> VirtualPosition (1 per symbol max)
    last_trade_idx: Dict[str, int] = {}  # symbol -> last trade snapshot index
    daily_pnl = 0.0
    daily_losses = 0
    current_day = None

    for idx, snap in enumerate(snapshots):
        snap_time = snap.get("recorded_at")
        snap_day = snap_time.date() if isinstance(snap_time, datetime) else None
        snap_symbol = snap.get("symbol", "MES")

        # Reset daily counters
        if snap_day and snap_day != current_day:
            current_day = snap_day
            daily_pnl = 0.0
            daily_losses = 0

        # Check daily loss limit
        capital = config.get("initial_capital", 25000)
        running_pnl = sum(t.get("net_pnl", 0) for t in trades)
        current_capital = capital + running_pnl
        config["_current_capital"] = current_capital
        max_daily_loss = capital * config.get("max_daily_loss_pct", 5.0) / 100
        if daily_pnl < -max_daily_loss:
            # Force close ALL active positions
            for sym, pos in list(active_positions.items()):
                result = pos.force_close(snap)
                result["exit_reason"] = "DAILY_LOSS_LIMIT"
                trades.append(result)
            active_positions.clear()
            continue

        # Check consecutive loss limit
        if daily_losses >= config.get("max_consecutive_losses", 3):
            for sym, pos in list(active_positions.items()):
                result = pos.force_close(snap)
                result["exit_reason"] = "CONSECUTIVE_LOSS_LIMIT"
                trades.append(result)
            active_positions.clear()
            continue

        # Update active position for THIS symbol (if exists)
        if snap_symbol in active_positions:
            result = active_positions[snap_symbol].update(snap)
            if result:
                trades.append(result)
                daily_pnl += result["net_pnl"]
                if result["net_pnl"] <= 0:
                    daily_losses += 1
                else:
                    daily_losses = 0
                del active_positions[snap_symbol]

        # Check for new signal (only if no active position FOR THIS SYMBOL and cooldown passed)
        # V3 Engine rule: max_concurrent_positions (default=1) across ALL symbols
        max_concurrent = config.get("max_concurrent_positions", 1)
        if snap_symbol not in active_positions and len(active_positions) < max_concurrent:
            cooldown = config.get("min_snapshots_between_trades", 5)
            sym_last_idx = last_trade_idx.get(snap_symbol, -999)
            if idx - sym_last_idx < cooldown:
                continue

            # ── Session time filter ──
            if not _check_session_filter(snap, config):
                eval_stats["session_filtered"] += 1
                continue

            # ── V3 Engine Rule: Regime → Symbol filter ──
            # Each regime dictates which instrument can be traded.
            # Skip snapshots where the symbol doesn't match the regime's allowed symbol.
            snap_regime = snap.get("regime", "TRANSICAO")
            allowed_sym = REGIME_ALLOWED_SYMBOL.get(snap_regime)
            if allowed_sym and snap_symbol != allowed_sym:
                eval_stats["session_filtered"] += 1
                continue

            # ── Determine action: DYNAMIC re-evaluation or STATIC ──
            signal_mode = config.get("signal_mode", "DYNAMIC")
            original_action = snap.get("action", "WAIT")

            if signal_mode == "DYNAMIC":
                # Track raw signal source
                raw = extract_raw_signals(snap)
                eval_stats["by_source"][raw["source"]] = eval_stats["by_source"].get(raw["source"], 0) + 1

                action, eval_reason = reevaluate_signal(snap, config)
                eval_stats["total_evaluated"] += 1

                if action in ("BUY", "SELL"):
                    eval_stats["dynamic_accepted"] += 1
                elif "[STATIC fallback]" in eval_reason:
                    eval_stats["static_fallback"] += 1
                    # In DYNAMIC mode, static fallbacks still use original action
                    action = original_action
                else:
                    eval_stats["dynamic_filtered"] += 1
            else:
                action = original_action
                eval_reason = "[STATIC] action original do snapshot"

            if action not in ("BUY", "SELL"):
                continue

            tp = snap.get("trade_preview")
            if tp and tp.get("redirect"):
                continue

            # Fallback for legacy snapshots without trade_preview
            if not tp or not tp.get("hard_stop"):
                n3 = snap.get("n3", {})
                ctx = snap.get("context", {})
                entry = n3.get("entry_price") or ctx.get("last_price")
                atr_m1 = ctx.get("atr_m1") or ctx.get("atr")
                # Estimate atr_m1 from VWAP bands if unavailable
                if not atr_m1:
                    bands = ctx.get("vwap_bands", {})
                    u1 = bands.get("upper_1", 0)
                    vwap = ctx.get("vwap", 0)
                    if u1 and vwap:
                        atr_m1 = (u1 - vwap) * 2  # 1-sigma ≈ atr_m1 proxy
                if not atr_m1:
                    atr_m1 = 2.0  # absolute fallback
                regime = snap.get("regime", "TRANSICAO")

                # Determine archetype from regime
                arch_map = {"COMPLACENCIA": "TREND", "BULL": "TREND", "BEAR": "TREND",
                            "TRANSICAO": "RANGE", "CAPITULACAO": "FADE"}
                archetype = arch_map.get(regime, "RANGE")

                if entry and atr_m1 > 0:
                    is_long = action == "BUY"
                    sign = 1 if is_long else -1
                    sl_mult = config.get("sl_atr_mult", {}).get(archetype, 1.0)
                    hard_stop = entry - sign * sl_mult * atr_m1
                    take_profit = n3.get("take_profit") or (entry + sign * sl_mult * atr_m1 * 2)

                    tp = {
                        "archetype": archetype,
                        "entry_price": entry,
                        "hard_stop": round(hard_stop, 2),
                        "take_profit": round(take_profit, 2),
                        "atr_m1": atr_m1,
                        "implied_side": "LONG" if is_long else "SHORT",
                        "fallback": True,
                    }
                else:
                    continue

            active_positions[snap_symbol] = VirtualPosition(snap, config, tp, override_side=action)
            last_trade_idx[snap_symbol] = idx

    # Force close any remaining positions at end of data
    if active_positions and snapshots:
        last_snap = snapshots[-1]
        for sym, pos in active_positions.items():
            result = pos.force_close(last_snap)
            trades.append(result)

    metrics = compute_metrics(trades, config)
    return _build_run_result(run_id, config, started_at, trades, metrics, len(snapshots), eval_stats)


def _build_run_result(run_id: str, config: Dict, started_at: datetime,
                      trades: List[Dict], metrics: Dict, snapshot_count: int,
                      eval_stats: Dict = None) -> Dict:
    finished_at = datetime.now(timezone.utc)
    result = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "config": config,
        "snapshot_count": snapshot_count,
        "trades": trades,
        "metrics": metrics,
    }
    if eval_stats:
        result["eval_stats"] = eval_stats
    return result


async def run_batch(database, configs: List[Dict], objective: str = "sharpe") -> Dict[str, Any]:
    """Run multiple configs and rank by objective function.

    ML-ready: accepts a list of parameter configs, runs each,
    and returns results sorted by the chosen objective.
    """
    results = []
    for i, cfg in enumerate(configs):
        logger.info(f"Batch run {i+1}/{len(configs)}")
        result = await run_replay(database, cfg)
        obj_value = _extract_objective(result["metrics"], objective)
        results.append({
            "rank": 0,
            "objective_value": round(obj_value, 4),
            "run_id": result["run_id"],
            "config": result["config"],
            "metrics_summary": {
                "total_trades": result["metrics"]["total_trades"],
                "win_rate": result["metrics"]["win_rate"],
                "total_pnl": result["metrics"]["total_pnl"],
                "sharpe_ratio": result["metrics"]["sharpe_ratio"],
                "sortino_ratio": result["metrics"]["sortino_ratio"],
                "profit_factor": result["metrics"]["profit_factor"],
                "max_drawdown": result["metrics"]["max_drawdown"],
                "return_pct": result["metrics"]["return_pct"],
            },
        })

    # Sort by objective (descending for most, ascending for drawdown)
    reverse = objective != "min_drawdown"
    results.sort(key=lambda x: x["objective_value"], reverse=reverse)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    return {
        "batch_id": str(uuid.uuid4())[:12],
        "objective": objective,
        "total_configs": len(configs),
        "results": results,
    }


def _extract_objective(metrics: Dict, objective: str) -> float:
    mapping = {
        "sharpe": "sharpe_ratio",
        "sortino": "sortino_ratio",
        "profit_factor": "profit_factor",
        "net_pnl": "total_pnl",
        "min_drawdown": "max_drawdown",
        "expectancy": "expectancy",
    }
    key = mapping.get(objective, "sharpe_ratio")
    return metrics.get(key, 0)
