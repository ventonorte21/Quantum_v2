"""
Test Data Quality Layer P2 — DEAD TTL, MQS Binary Fuse, Exponential Backoff
============================================================================
Tests for 3 production improvements on top of P0 (LKG, CircuitBreaker, DQS) and P1 (Pre-Warm, Staggered):

1. DEAD TTL in LKG: When data exceeds dead_ttl, confidence becomes DEAD with multiplier 0.0
   - VIX: 90min (5400s), Treasury: 120min (7200s), Gamma: 28h (100800s)
   - DEAD multiplier = 0.0 → forces DQS collapse → DEFENSIVE/HARD_STOP

2. MQS (Microstructure Quality Score): Binary fuse for N2/N3 (DataBento live feed)
   - 3 components: ws_connected, buffer_has_trades (>=10), last_trade_fresh (<=30s)
   - If MQS fails → N3 freezes execution, DQS/N1 intact, v3_status=MQS_FREEZE

3. Exponential Backoff in Circuit Breaker: 120s → 240s → 480s → max 900s
   - HALF_OPEN fail → OPEN with doubled recovery_timeout
   - Success resets everything (timeout to base, cycles to 0)

NOTE: P0 tests in test_data_quality.py (39/39), P1 tests in test_p1_optimizations.py (22/22)
"""

import pytest
import requests
import os
import time
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')

from services.data_quality import (
    LKGStore, CircuitBreaker, CircuitBreakerRegistry, DataEpochManager,
    Confidence, BreakerState, compute_dqs, apply_dqs_lot_scaling,
    LKG_TTL_CONFIG, CONFIDENCE_MULTIPLIER, DQS_WEIGHTS,
    BACKOFF_BASE, BACKOFF_MULTIPLIER, BACKOFF_MAX,
    MicrostructureQualityScore, MQS_MAX_TRADE_AGE_S, MQS_MIN_BUFFER_SIZE,
)

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. DEAD TTL TESTS — LKG Store with 4 Confidence Levels
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDeadTTL:
    """Tests for DEAD TTL feature in LKG Store"""

    def test_vix_dead_at_6000s(self):
        """LKG DEAD TTL: VIX at age=6000s returns confidence=DEAD (dead_ttl=5400s)"""
        store = LKGStore()
        store.update("vix", {"value": 25.0, "source": "yahoo"})
        
        # Simulate 6000s age (past dead_ttl=5400s)
        entry = store._entries["vix"]
        entry.fetched_at = time.monotonic() - 6000
        
        result, conf, age = store.get("vix")
        
        assert conf == Confidence.DEAD, f"Expected DEAD at 6000s, got {conf}"
        assert result["_confidence"] == "DEAD"
        assert "lkg_dead" in result.get("source", "")
        assert age >= 6000
        print(f"✅ VIX at age=6000s returns DEAD (dead_ttl=5400s)")

    def test_treasury_dead_at_8000s(self):
        """LKG DEAD TTL: Treasury at age=8000s returns confidence=DEAD (dead_ttl=7200s)"""
        store = LKGStore()
        store.update("treasury", {"us10y": 4.5, "source": "yahoo"})
        
        # Simulate 8000s age (past dead_ttl=7200s)
        entry = store._entries["treasury"]
        entry.fetched_at = time.monotonic() - 8000
        
        result, conf, age = store.get("treasury")
        
        assert conf == Confidence.DEAD, f"Expected DEAD at 8000s, got {conf}"
        assert result["_confidence"] == "DEAD"
        print(f"✅ Treasury at age=8000s returns DEAD (dead_ttl=7200s)")

    def test_gamma_dead_at_101000s(self):
        """LKG DEAD TTL: Gamma at age=101000s returns confidence=DEAD (dead_ttl=100800s)"""
        store = LKGStore()
        store.update("gamma", {"net_gex": 1000, "source": "yahoo"})
        
        # Simulate 101000s age (past dead_ttl=100800s = 28h)
        entry = store._entries["gamma"]
        entry.fetched_at = time.monotonic() - 101000
        
        result, conf, age = store.get("gamma")
        
        assert conf == Confidence.DEAD, f"Expected DEAD at 101000s, got {conf}"
        assert result["_confidence"] == "DEAD"
        print(f"✅ Gamma at age=101000s returns DEAD (dead_ttl=100800s)")

    def test_four_confidence_levels(self):
        """LKG 4 confidence levels: LIVE, STALE, DEAD, FALLBACK"""
        store = LKGStore()
        
        # LIVE: within stale_ttl
        store.update("vix", {"value": 20.0, "source": "yahoo"})
        _, conf, _ = store.get("vix")
        assert conf == Confidence.LIVE, f"Expected LIVE, got {conf}"
        
        # STALE: between stale_ttl and dead_ttl
        entry = store._entries["vix"]
        entry.fetched_at = time.monotonic() - 3000  # 3000s > 1800s (stale) but < 5400s (dead)
        _, conf, _ = store.get("vix")
        assert conf == Confidence.STALE, f"Expected STALE, got {conf}"
        
        # DEAD: past dead_ttl
        entry.fetched_at = time.monotonic() - 6000  # 6000s > 5400s (dead)
        _, conf, _ = store.get("vix")
        assert conf == Confidence.DEAD, f"Expected DEAD, got {conf}"
        
        # FALLBACK: never had data
        _, conf, _ = store.get("unknown_key")
        assert conf == Confidence.FALLBACK, f"Expected FALLBACK, got {conf}"
        
        print("✅ LKG 4 confidence levels: LIVE, STALE, DEAD, FALLBACK")

    def test_dead_multiplier_is_zero(self):
        """DEAD multiplier is 0.0 (not 0.3 like FALLBACK or 0.7 like STALE)"""
        assert CONFIDENCE_MULTIPLIER[Confidence.LIVE.value] == 1.0
        assert CONFIDENCE_MULTIPLIER[Confidence.STALE.value] == 0.7
        assert CONFIDENCE_MULTIPLIER[Confidence.DEAD.value] == 0.0
        assert CONFIDENCE_MULTIPLIER[Confidence.FALLBACK.value] == 0.3
        print("✅ DEAD multiplier is 0.0 (LIVE=1.0, STALE=0.7, FALLBACK=0.3)")

    def test_dqs_collapses_when_vix_dead(self):
        """DQS collapses when N1 source goes DEAD: VIX DEAD alone → DQS ~0.769 → DEFENSIVE"""
        # VIX=DEAD (0.0), term_structure=LIVE (1.0), treasury=LIVE (1.0), gamma=LIVE (1.0)
        # DQS = (3*0.0 + 3*1.0 + 4*1.0 + 3*1.0) / 13 = 10/13 = 0.769
        confidence_map = {
            "vix": "DEAD",
            "term_structure": "LIVE",
            "treasury": "LIVE",
            "gamma": "LIVE",
        }
        
        dqs, breakdown = compute_dqs(confidence_map)
        
        assert 0.75 <= dqs < 0.9, f"Expected DQS in DEFENSIVE range [0.75, 0.9), got {dqs}"
        
        # Verify lot scaling
        adjusted, action, reason = apply_dqs_lot_scaling(100, dqs)
        assert action == "DEFENSIVE", f"Expected DEFENSIVE, got {action}"
        
        print(f"✅ VIX DEAD alone → DQS={dqs:.3f} → {action}")

    def test_dqs_collapses_when_two_sources_dead(self):
        """DQS collapses when 2 N1 sources go DEAD: VIX+TS DEAD → DQS ~0.538 → HARD_STOP"""
        # VIX=DEAD (0.0), term_structure=DEAD (0.0), treasury=LIVE (1.0), gamma=LIVE (1.0)
        # DQS = (3*0.0 + 3*0.0 + 4*1.0 + 3*1.0) / 13 = 7/13 = 0.538
        confidence_map = {
            "vix": "DEAD",
            "term_structure": "DEAD",
            "treasury": "LIVE",
            "gamma": "LIVE",
        }
        
        dqs, breakdown = compute_dqs(confidence_map)
        
        assert dqs < 0.75, f"Expected DQS < 0.75 for HARD_STOP, got {dqs}"
        
        # Verify lot scaling
        adjusted, action, reason = apply_dqs_lot_scaling(100, dqs)
        assert action == "HARD_STOP", f"Expected HARD_STOP, got {action}"
        assert adjusted == 0, f"Expected lot=0 for HARD_STOP, got {adjusted}"
        
        print(f"✅ VIX+TS DEAD → DQS={dqs:.3f} → {action}")

    def test_ttl_config_values(self):
        """Verify LKG_TTL_CONFIG has correct stale_ttl and dead_ttl values"""
        # VIX: 30min stale, 90min dead
        assert LKG_TTL_CONFIG["vix"]["stale_ttl"] == 1800
        assert LKG_TTL_CONFIG["vix"]["dead_ttl"] == 5400
        
        # Treasury: 30min stale, 120min dead
        assert LKG_TTL_CONFIG["treasury"]["stale_ttl"] == 1800
        assert LKG_TTL_CONFIG["treasury"]["dead_ttl"] == 7200
        
        # Gamma: 24h stale, 28h dead
        assert LKG_TTL_CONFIG["gamma"]["stale_ttl"] == 86400
        assert LKG_TTL_CONFIG["gamma"]["dead_ttl"] == 100800
        
        print("✅ LKG_TTL_CONFIG values correct: VIX(90min), Treasury(120min), Gamma(28h)")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. EXPONENTIAL BACKOFF TESTS — Circuit Breaker
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestExponentialBackoff:
    """Tests for Exponential Backoff in Circuit Breaker"""

    def test_backoff_constants(self):
        """Verify backoff constants: base=120, multiplier=2.0, max=900"""
        assert BACKOFF_BASE == 120.0, f"Expected BACKOFF_BASE=120, got {BACKOFF_BASE}"
        assert BACKOFF_MULTIPLIER == 2.0, f"Expected BACKOFF_MULTIPLIER=2.0, got {BACKOFF_MULTIPLIER}"
        assert BACKOFF_MAX == 900.0, f"Expected BACKOFF_MAX=900, got {BACKOFF_MAX}"
        print("✅ Backoff constants: base=120s, multiplier=2.0, max=900s")

    def test_half_open_fail_doubles_timeout(self):
        """Circuit Breaker Exponential Backoff: HALF_OPEN fail → OPEN with doubled recovery_timeout"""
        cb = CircuitBreaker("test_backoff", failure_threshold=3, recovery_timeout=BACKOFF_BASE)
        
        # Force OPEN
        for _ in range(3):
            cb.record_failure()
        assert cb.state == BreakerState.OPEN
        assert cb.recovery_timeout == BACKOFF_BASE  # 120s
        
        # Wait and transition to HALF_OPEN
        cb.last_failure_time = time.monotonic() - BACKOFF_BASE - 1
        cb.can_execute()
        assert cb.state == BreakerState.HALF_OPEN
        
        # Fail in HALF_OPEN → should double timeout
        cb.record_failure()
        
        assert cb.state == BreakerState.OPEN
        assert cb.consecutive_open_cycles == 1
        expected_timeout = BACKOFF_BASE * (BACKOFF_MULTIPLIER ** 1)  # 120 * 2 = 240
        assert cb.recovery_timeout == expected_timeout, f"Expected {expected_timeout}, got {cb.recovery_timeout}"
        
        print(f"✅ HALF_OPEN fail → OPEN with recovery_timeout={cb.recovery_timeout}s")

    def test_consecutive_cycles_double_timeout(self):
        """Circuit Breaker Backoff: consecutive cycles double the timeout (120→240→480→900 max)"""
        cb = CircuitBreaker("test_consecutive", failure_threshold=3, recovery_timeout=BACKOFF_BASE)
        
        # First HALF_OPEN fail: 120 * 2^1 = 240
        # Second HALF_OPEN fail: 120 * 2^2 = 480
        # Third HALF_OPEN fail: 120 * 2^3 = 960 → capped at 900
        expected_timeouts = [240, 480, 900]
        
        for cycle, expected in enumerate(expected_timeouts):
            # Force OPEN (first time) or already OPEN from previous fail
            if cb.state != BreakerState.OPEN:
                for _ in range(3):
                    cb.record_failure()
            
            # Wait and transition to HALF_OPEN
            cb.last_failure_time = time.monotonic() - cb.recovery_timeout - 1
            cb.can_execute()
            assert cb.state == BreakerState.HALF_OPEN
            
            # Fail in HALF_OPEN → doubles timeout
            cb.record_failure()
            
            assert cb.recovery_timeout == expected, f"Cycle {cycle}: expected {expected}s, got {cb.recovery_timeout}s"
            print(f"   Cycle {cycle}: recovery_timeout = {cb.recovery_timeout}s")
        
        # One more cycle should still be capped at 900
        cb.last_failure_time = time.monotonic() - cb.recovery_timeout - 1
        cb.can_execute()
        cb.record_failure()
        assert cb.recovery_timeout == 900, f"Expected max 900s, got {cb.recovery_timeout}s"
        
        print("✅ Consecutive cycles: 240→480→900 (max)")

    def test_success_resets_everything(self):
        """Circuit Breaker Backoff: success resets everything (timeout to base, cycles to 0)"""
        cb = CircuitBreaker("test_reset", failure_threshold=3, recovery_timeout=BACKOFF_BASE)
        
        # Build up some backoff
        for _ in range(3):
            cb.record_failure()
        cb.last_failure_time = time.monotonic() - BACKOFF_BASE - 1
        cb.can_execute()
        cb.record_failure()  # Now at 240s
        
        assert cb.recovery_timeout == 240
        assert cb.consecutive_open_cycles == 1
        
        # Wait and transition to HALF_OPEN
        cb.last_failure_time = time.monotonic() - cb.recovery_timeout - 1
        cb.can_execute()
        
        # Success in HALF_OPEN → should reset everything
        cb.record_success()
        
        assert cb.state == BreakerState.CLOSED
        assert cb.recovery_timeout == BACKOFF_BASE, f"Expected reset to {BACKOFF_BASE}, got {cb.recovery_timeout}"
        assert cb.consecutive_open_cycles == 0, f"Expected cycles=0, got {cb.consecutive_open_cycles}"
        assert cb.failure_count == 0
        
        print("✅ Success resets: timeout→120s, cycles→0, failures→0")

    def test_status_shows_backoff_info(self):
        """Circuit Breaker status shows consecutive_open_cycles and recovery_timeout_s"""
        cb = CircuitBreaker("test_status", failure_threshold=3, recovery_timeout=BACKOFF_BASE)
        
        # Build up some backoff
        for _ in range(3):
            cb.record_failure()
        cb.last_failure_time = time.monotonic() - BACKOFF_BASE - 1
        cb.can_execute()
        cb.record_failure()
        
        status = cb.get_status()
        
        assert "consecutive_open_cycles" in status
        assert "recovery_timeout_s" in status
        assert "backoff_multiplier" in status
        assert status["consecutive_open_cycles"] == 1
        assert status["recovery_timeout_s"] == 240
        assert status["backoff_multiplier"] == 2.0
        
        print(f"✅ Status includes: consecutive_open_cycles={status['consecutive_open_cycles']}, recovery_timeout_s={status['recovery_timeout_s']}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. MQS (MICROSTRUCTURE QUALITY SCORE) TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MockSymbolBuffer:
    """Mock SymbolBuffer for MQS testing"""
    def __init__(self, connected=True, trade_count=100, last_trade_age_s=5.0, last_price=21000.0):
        self.connected = connected
        self.trade_count_since_start = trade_count
        self.last_price = last_price
        if last_trade_age_s is not None:
            self.last_trade_time = datetime.now(timezone.utc) - timedelta(seconds=last_trade_age_s)
        else:
            self.last_trade_time = None


class MockLiveDataService:
    """Mock LiveDataService for MQS testing"""
    def __init__(self, buffers: dict):
        self.buffers = buffers


class TestMQS:
    """Tests for Microstructure Quality Score (MQS) binary fuse"""

    def test_mqs_thresholds(self):
        """Verify MQS thresholds: max_trade_age=30s, min_buffer_size=10"""
        assert MQS_MAX_TRADE_AGE_S == 30.0, f"Expected MQS_MAX_TRADE_AGE_S=30, got {MQS_MAX_TRADE_AGE_S}"
        assert MQS_MIN_BUFFER_SIZE == 10, f"Expected MQS_MIN_BUFFER_SIZE=10, got {MQS_MIN_BUFFER_SIZE}"
        print("✅ MQS thresholds: max_trade_age=30s, min_buffer_size=10")

    def test_mqs_all_pass(self):
        """MQS evaluate: ws_connected=True, buffer>=10 trades, last_trade<=30s → ok=True, score=1.0"""
        mqs = MicrostructureQualityScore()
        
        mock_buffer = MockSymbolBuffer(connected=True, trade_count=100, last_trade_age_s=5.0)
        mock_service = MockLiveDataService({"MNQ": mock_buffer})
        
        result = mqs.evaluate("MNQ", mock_service)
        
        assert result["ok"] is True, f"Expected ok=True, got {result['ok']}"
        assert result["score"] == 1.0, f"Expected score=1.0, got {result['score']}"
        assert result["reason"] is None
        assert result["components"]["ws_connected"] is True
        assert result["components"]["buffer_has_trades"] is True
        assert result["components"]["last_trade_fresh"] is True
        
        print(f"✅ MQS all pass: ok={result['ok']}, score={result['score']}")

    def test_mqs_ws_disconnected(self):
        """MQS evaluate: ws_connected=False → ok=False with reason containing 'WebSocket desconectado'"""
        mqs = MicrostructureQualityScore()
        
        mock_buffer = MockSymbolBuffer(connected=False, trade_count=100, last_trade_age_s=5.0)
        mock_service = MockLiveDataService({"MNQ": mock_buffer})
        
        result = mqs.evaluate("MNQ", mock_service)
        
        assert result["ok"] is False
        assert "WebSocket desconectado" in result["reason"]
        assert result["components"]["ws_connected"] is False
        
        print(f"✅ MQS ws_disconnected: ok={result['ok']}, reason='{result['reason']}'")

    def test_mqs_buffer_insufficient(self):
        """MQS evaluate: buffer<10 trades → ok=False with reason containing 'Buffer insuficiente'"""
        mqs = MicrostructureQualityScore()
        
        mock_buffer = MockSymbolBuffer(connected=True, trade_count=5, last_trade_age_s=5.0)
        mock_service = MockLiveDataService({"MNQ": mock_buffer})
        
        result = mqs.evaluate("MNQ", mock_service)
        
        assert result["ok"] is False
        assert "Buffer insuficiente" in result["reason"]
        assert result["components"]["buffer_has_trades"] is False
        
        print(f"✅ MQS buffer_insufficient: ok={result['ok']}, reason='{result['reason']}'")

    def test_mqs_last_trade_stale(self):
        """MQS evaluate: last_trade>30s → ok=False with reason containing 'Ultimo trade stale'"""
        mqs = MicrostructureQualityScore()
        
        mock_buffer = MockSymbolBuffer(connected=True, trade_count=100, last_trade_age_s=45.0)
        mock_service = MockLiveDataService({"MNQ": mock_buffer})
        
        result = mqs.evaluate("MNQ", mock_service)
        
        assert result["ok"] is False
        assert "Ultimo trade stale" in result["reason"]
        assert result["components"]["last_trade_fresh"] is False
        
        print(f"✅ MQS last_trade_stale: ok={result['ok']}, reason='{result['reason']}'")

    def test_mqs_multiple_failures(self):
        """MQS evaluate: multiple failures combine in reason"""
        mqs = MicrostructureQualityScore()
        
        mock_buffer = MockSymbolBuffer(connected=False, trade_count=5, last_trade_age_s=45.0)
        mock_service = MockLiveDataService({"MNQ": mock_buffer})
        
        result = mqs.evaluate("MNQ", mock_service)
        
        assert result["ok"] is False
        assert result["score"] == 0.0  # 0/3 passed
        assert "WebSocket desconectado" in result["reason"]
        assert "Buffer insuficiente" in result["reason"]
        assert "Ultimo trade stale" in result["reason"]
        
        print(f"✅ MQS multiple failures: score={result['score']}, reason='{result['reason']}'")

    def test_mqs_partial_score(self):
        """MQS evaluate: partial pass gives score 0.33 or 0.67"""
        mqs = MicrostructureQualityScore()
        
        # 2 of 3 pass (ws_connected=True, buffer=True, last_trade=False)
        mock_buffer = MockSymbolBuffer(connected=True, trade_count=100, last_trade_age_s=45.0)
        mock_service = MockLiveDataService({"MNQ": mock_buffer})
        
        result = mqs.evaluate("MNQ", mock_service)
        
        assert result["ok"] is False
        assert result["score"] == round(2/3, 2), f"Expected score=0.67, got {result['score']}"
        
        print(f"✅ MQS partial: 2/3 pass → score={result['score']}")

    def test_mqs_is_ok_method(self):
        """MQS is_ok() quick check method"""
        mqs = MicrostructureQualityScore()
        
        # First evaluate
        mock_buffer = MockSymbolBuffer(connected=True, trade_count=100, last_trade_age_s=5.0)
        mock_service = MockLiveDataService({"MNQ": mock_buffer})
        mqs.evaluate("MNQ", mock_service)
        
        assert mqs.is_ok("MNQ") is True
        assert mqs.is_ok("UNKNOWN") is False  # Not evaluated
        
        print("✅ MQS is_ok() method works correctly")

    def test_mqs_get_status(self):
        """MQS get_status() returns evaluation results"""
        mqs = MicrostructureQualityScore()
        
        mock_buffer = MockSymbolBuffer(connected=True, trade_count=100, last_trade_age_s=5.0)
        mock_service = MockLiveDataService({"MNQ": mock_buffer})
        mqs.evaluate("MNQ", mock_service)
        
        # Get status for specific symbol
        status = mqs.get_status("MNQ")
        assert "ok" in status
        assert "score" in status
        assert "components" in status
        
        # Get status for all symbols
        all_status = mqs.get_status()
        assert "MNQ" in all_status
        
        print("✅ MQS get_status() returns evaluation results")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. API TESTS — /api/v3/data-quality with MQS and TTL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDataQualityAPIv2:
    """API tests for /api/v3/data-quality with new P2 features"""

    @pytest.fixture
    def api_client(self):
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "Cookie": f"session_token={SESSION_TOKEN}",
        })
        return session

    def test_data_quality_shows_ttl_config(self, api_client):
        """GET /api/v3/data-quality now shows lkg_keys with stale_ttl and dead_ttl per source"""
        response = api_client.get(f"{BASE_URL}/api/v3/data-quality")
        assert response.status_code == 200
        
        data = response.json()
        lkg_keys = data.get("lkg_keys", {})
        
        # Verify TTL config is present for each source
        for key in ["vix", "treasury", "gamma"]:
            assert key in lkg_keys, f"Missing {key} in lkg_keys"
            assert "stale_ttl" in lkg_keys[key], f"Missing stale_ttl for {key}"
            assert "dead_ttl" in lkg_keys[key], f"Missing dead_ttl for {key}"
        
        # Verify specific values
        assert lkg_keys["vix"]["dead_ttl"] == 5400, "VIX dead_ttl should be 5400s (90min)"
        assert lkg_keys["treasury"]["dead_ttl"] == 7200, "Treasury dead_ttl should be 7200s (120min)"
        assert lkg_keys["gamma"]["dead_ttl"] == 100800, "Gamma dead_ttl should be 100800s (28h)"
        
        print("✅ GET /api/v3/data-quality shows stale_ttl and dead_ttl per source")
        for key in ["vix", "treasury", "gamma"]:
            print(f"   {key}: stale_ttl={lkg_keys[key]['stale_ttl']}s, dead_ttl={lkg_keys[key]['dead_ttl']}s")

    def test_data_quality_shows_mqs_status(self, api_client):
        """GET /api/v3/data-quality now shows mqs status per symbol (MNQ, MES)"""
        response = api_client.get(f"{BASE_URL}/api/v3/data-quality")
        assert response.status_code == 200
        
        data = response.json()
        
        assert "mqs" in data, "Missing 'mqs' in response"
        mqs_status = data["mqs"]
        
        # Should have status for MNQ and MES
        assert "MNQ" in mqs_status or len(mqs_status) > 0, "MQS should have symbol status"
        
        print(f"✅ GET /api/v3/data-quality shows MQS status: {list(mqs_status.keys())}")
        for sym, status in mqs_status.items():
            ok = status.get("ok", "N/A")
            score = status.get("score", "N/A")
            print(f"   {sym}: ok={ok}, score={score}")

    def test_circuit_breaker_shows_backoff_info(self, api_client):
        """Circuit Breaker status in API shows consecutive_open_cycles and recovery_timeout_s"""
        response = api_client.get(f"{BASE_URL}/api/v3/data-quality")
        assert response.status_code == 200
        
        data = response.json()
        breakers = data.get("circuit_breakers", {})
        
        if breakers:
            # Check first breaker has backoff fields
            first_breaker = list(breakers.values())[0]
            assert "consecutive_open_cycles" in first_breaker, "Missing consecutive_open_cycles"
            assert "recovery_timeout_s" in first_breaker, "Missing recovery_timeout_s"
            assert "backoff_multiplier" in first_breaker, "Missing backoff_multiplier"
            
            print("✅ Circuit breaker status includes backoff info")
            for name, status in list(breakers.items())[:3]:
                print(f"   {name}: cycles={status.get('consecutive_open_cycles')}, timeout={status.get('recovery_timeout_s')}s")
        else:
            print("⚠️ No circuit breakers in response (may be expected if none registered)")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. V3 SIGNAL TESTS — MQS Integration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestV3SignalMQS:
    """API tests for V3 signal MQS integration"""

    @pytest.fixture
    def api_client(self):
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "Cookie": f"session_token={SESSION_TOKEN}",
        })
        return session

    def test_v3_signal_includes_mqs(self, api_client):
        """V3 signal includes mqs in data_quality object"""
        # Retry up to 3 times for transient 502 errors
        for attempt in range(3):
            response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
            if response.status_code == 200:
                break
            time.sleep(5)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        dq = data.get("data_quality", {})
        
        assert "mqs" in dq, "Missing 'mqs' in data_quality"
        mqs = dq["mqs"]
        
        assert "ok" in mqs, "Missing 'ok' in mqs"
        assert "score" in mqs, "Missing 'score' in mqs"
        assert "components" in mqs, "Missing 'components' in mqs"
        
        print(f"✅ V3 signal includes MQS: ok={mqs['ok']}, score={mqs['score']}")

    def test_v3_status_mqs_freeze(self, api_client):
        """V3 status = MQS_FREEZE when MQS fails (N3 frozen, DQS/N1 intact)"""
        # Retry up to 3 times for transient 502 errors
        for attempt in range(3):
            response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
            if response.status_code == 200:
                break
            time.sleep(5)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        v3_status = data.get("v3_status", "")
        mqs = data.get("data_quality", {}).get("mqs", {})
        
        # If MQS failed, status should be MQS_FREEZE
        if not mqs.get("ok", True):
            assert v3_status == "MQS_FREEZE", f"Expected MQS_FREEZE when MQS fails, got {v3_status}"
            print(f"✅ V3 status = MQS_FREEZE (MQS failed: {mqs.get('reason')})")
        else:
            # MQS passed, status should NOT be MQS_FREEZE
            assert v3_status != "MQS_FREEZE" or v3_status in ["ACTIVE_SIGNAL", "FILTERS_PASSED", "WAITING", "DQS_HARD_STOP"]
            print(f"✅ V3 status = {v3_status} (MQS passed)")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. P0+P1 REGRESSION TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestP0P1Regression:
    """Selective regression tests for P0+P1 features"""

    @pytest.fixture
    def api_client(self):
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "Cookie": f"session_token={SESSION_TOKEN}",
        })
        return session

    def test_lkg_live_stale_fallback_still_work(self):
        """P0+P1 regression: LKG LIVE/STALE/FALLBACK still work"""
        store = LKGStore()
        
        # LIVE
        store.update("test", {"value": 100})
        _, conf, _ = store.get("test")
        assert conf == Confidence.LIVE
        
        # STALE (between stale and dead TTL)
        entry = store._entries["test"]
        entry.fetched_at = time.monotonic() - 3000  # Past stale, before dead
        _, conf, _ = store.get("test")
        assert conf == Confidence.STALE
        
        # FALLBACK
        _, conf, _ = store.get("unknown")
        assert conf == Confidence.FALLBACK
        
        print("✅ P0+P1 regression: LKG LIVE/STALE/FALLBACK still work")

    def test_circuit_breaker_states_still_work(self):
        """P0+P1 regression: CircuitBreaker CLOSED/OPEN/HALF_OPEN still work"""
        cb = CircuitBreaker("test_regression", failure_threshold=3, recovery_timeout=0.1)
        
        assert cb.state == BreakerState.CLOSED
        
        for _ in range(3):
            cb.record_failure()
        assert cb.state == BreakerState.OPEN
        
        time.sleep(0.15)
        cb.can_execute()
        assert cb.state == BreakerState.HALF_OPEN
        
        cb.record_success()
        assert cb.state == BreakerState.CLOSED
        
        print("✅ P0+P1 regression: CircuitBreaker states still work")

    def test_data_epoch_still_works(self):
        """P0+P1 regression: DataEpoch seal_epoch() still works"""
        lkg = LKGStore()
        registry = CircuitBreakerRegistry()
        manager = DataEpochManager(lkg, registry)
        
        manager.update_draft("vix", {"value": 25.0}, Confidence.LIVE)
        epoch = manager.seal_epoch()
        
        assert epoch is not None
        assert epoch.epoch_id >= 1
        assert epoch.dqs is not None
        
        print(f"✅ P0+P1 regression: DataEpoch seal_epoch() works (epoch_id={epoch.epoch_id})")

    def test_dqs_step_function_still_works(self):
        """P0+P1 regression: DQS step function still works"""
        # FULL
        _, action, _ = apply_dqs_lot_scaling(100, 1.0)
        assert action == "FULL"
        
        # DEFENSIVE
        _, action, _ = apply_dqs_lot_scaling(100, 0.85)
        assert action == "DEFENSIVE"
        
        # HARD_STOP
        _, action, _ = apply_dqs_lot_scaling(100, 0.5)
        assert action == "HARD_STOP"
        
        print("✅ P0+P1 regression: DQS step function still works")

    def test_pre_warm_staggered_still_work(self, api_client):
        """P0+P1 regression: Pre-warm and staggered refresh still work"""
        response = api_client.get(f"{BASE_URL}/api/v3/data-quality")
        assert response.status_code == 200
        
        data = response.json()
        lkg_keys = data.get("lkg_keys", {})
        
        # At least some sources should have real data (pre-warm worked)
        real_data_count = sum(1 for v in lkg_keys.values() if v.get("has_real_data"))
        assert real_data_count >= 3, f"Expected >=3 sources with real data, got {real_data_count}"
        
        print(f"✅ P0+P1 regression: Pre-warm/staggered work ({real_data_count}/7 sources have real data)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
