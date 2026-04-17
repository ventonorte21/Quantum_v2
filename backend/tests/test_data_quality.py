"""
Test Data Quality Layer P0 — LKG, Circuit Breaker, DataEpoch, DQS
=================================================================
Tests for:
1. LKGStore: LIVE/STALE/FALLBACK confidence levels
2. CircuitBreaker: CLOSED/OPEN/HALF_OPEN states
3. DataEpochManager: seal_epoch() creates immutable snapshots
4. DQS Step Function: lot scaling based on data quality
5. API endpoint /api/v3/data-quality
6. V3 signal data_quality object
"""

import pytest
import requests
import os
import time
import sys

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')

from services.data_quality import (
    LKGStore, CircuitBreaker, CircuitBreakerRegistry, DataEpochManager,
    Confidence, BreakerState, compute_dqs, apply_dqs_lot_scaling,
    validate_data, STATIC_FALLBACKS, DQS_WEIGHTS, CONFIDENCE_MULTIPLIER,
)

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UNIT TESTS — LKGStore
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestLKGStore:
    """Unit tests for Last Known Good store"""

    def test_update_stores_live_data(self):
        """LKG Store: update() stores LIVE data"""
        store = LKGStore()
        test_data = {"value": 25.5, "source": "test"}
        
        store.update("vix", test_data)
        
        result, conf, age = store.get("vix", max_age_seconds=60)
        assert conf == Confidence.LIVE, f"Expected LIVE, got {conf}"
        assert result["value"] == 25.5
        assert result["_confidence"] == "LIVE"
        assert age >= 0 and age < 1  # Should be very fresh
        print("✅ LKG update() stores LIVE data correctly")

    def test_get_returns_live_within_ttl(self):
        """LKG Store: get() returns LIVE within TTL"""
        store = LKGStore()
        store.update("vix", {"value": 20.0, "source": "yahoo"})
        
        # Get immediately (within TTL)
        result, conf, age = store.get("vix", max_age_seconds=60)
        
        assert conf == Confidence.LIVE
        assert result["_confidence"] == "LIVE"
        assert age < 1
        print("✅ LKG get() returns LIVE within TTL")

    def test_get_returns_stale_past_ttl(self):
        """LKG Store: get() returns STALE (not random) when past TTL"""
        store = LKGStore()
        store.update("vix", {"value": 22.0, "source": "yahoo"})
        
        # Simulate time passing by manipulating the entry
        entry = store._entries["vix"]
        # VIX stale_ttl is 1800s per LKG_TTL_CONFIG — set age past that threshold
        entry.fetched_at = time.monotonic() - 2000  # 2000 seconds ago (> 1800s stale_ttl)
        
        # Get (should be stale based on LKG_TTL_CONFIG)
        result, conf, age = store.get("vix")
        
        assert conf == Confidence.STALE, f"Expected STALE, got {conf}"
        assert result["_confidence"] == "STALE"
        assert result["value"] == 22.0  # Still the real value, not random
        assert "lkg_stale" in result.get("source", "")
        print("✅ LKG get() returns STALE (deterministic) past TTL")

    def test_get_returns_fallback_when_no_data(self):
        """LKG Store: get() returns static FALLBACK when no real data exists"""
        store = LKGStore()
        
        # Never updated — should return static fallback
        result, conf, age = store.get("vix", max_age_seconds=60)
        
        assert conf == Confidence.FALLBACK, f"Expected FALLBACK, got {conf}"
        assert result["_confidence"] == "FALLBACK"
        assert result["value"] == STATIC_FALLBACKS["vix"]["value"]  # 20.0
        assert result["source"] == "static_fallback"
        assert age == -1  # Indicates no real data
        print("✅ LKG get() returns static FALLBACK when no real data")

    def test_fallback_values_are_deterministic(self):
        """Verify STATIC_FALLBACKS are deterministic (no np.random)"""
        store = LKGStore()
        
        # Get fallback multiple times — should be identical
        results = []
        for _ in range(5):
            result, _, _ = store.get("vix", max_age_seconds=60)
            results.append(result["value"])
        
        assert all(v == results[0] for v in results), "Fallback values should be deterministic"
        assert results[0] == 20.0  # Known static value
        print("✅ FALLBACK values are deterministic (no np.random)")

    def test_all_sources_have_static_fallbacks(self):
        """Verify all 7 data sources have static fallbacks defined"""
        required_keys = ["vix", "vxn", "rvx", "term_structure", "treasury", "gamma", "economic_calendar"]
        
        for key in required_keys:
            assert key in STATIC_FALLBACKS, f"Missing static fallback for {key}"
            assert "source" in STATIC_FALLBACKS[key], f"Missing source field in {key} fallback"
            assert STATIC_FALLBACKS[key]["source"] == "static_fallback"
        
        print(f"✅ All {len(required_keys)} sources have static fallbacks")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UNIT TESTS — CircuitBreaker
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCircuitBreaker:
    """Unit tests for Circuit Breaker pattern"""

    def test_initial_state_is_closed(self):
        """Circuit Breaker starts in CLOSED state"""
        cb = CircuitBreaker("test_source", failure_threshold=3, recovery_timeout=10)
        
        assert cb.state == BreakerState.CLOSED
        assert cb.can_execute() is True
        print("✅ Circuit Breaker starts in CLOSED state")

    def test_three_failures_opens_breaker(self):
        """Circuit Breaker: 3 failures -> OPEN state"""
        cb = CircuitBreaker("test_source", failure_threshold=3, recovery_timeout=10)
        
        # Record 3 failures
        cb.record_failure()
        assert cb.state == BreakerState.CLOSED
        cb.record_failure()
        assert cb.state == BreakerState.CLOSED
        cb.record_failure()
        
        assert cb.state == BreakerState.OPEN, f"Expected OPEN after 3 failures, got {cb.state}"
        assert cb.can_execute() is False
        print("✅ Circuit Breaker opens after 3 failures")

    def test_open_breaker_skips_calls(self):
        """Circuit Breaker: OPEN state skips calls"""
        cb = CircuitBreaker("test_source", failure_threshold=3, recovery_timeout=100)
        
        # Force open
        for _ in range(3):
            cb.record_failure()
        
        assert cb.state == BreakerState.OPEN
        assert cb.can_execute() is False
        print("✅ OPEN breaker skips calls")

    def test_recovery_timeout_transitions_to_half_open(self):
        """Circuit Breaker: after recovery_timeout -> HALF_OPEN"""
        cb = CircuitBreaker("test_source", failure_threshold=3, recovery_timeout=0.1)
        
        # Force open
        for _ in range(3):
            cb.record_failure()
        
        assert cb.state == BreakerState.OPEN
        
        # Wait for recovery timeout
        time.sleep(0.15)
        
        # Check if can execute (should transition to HALF_OPEN)
        can_exec = cb.can_execute()
        
        assert cb.state == BreakerState.HALF_OPEN, f"Expected HALF_OPEN, got {cb.state}"
        assert can_exec is True
        print("✅ Circuit Breaker transitions to HALF_OPEN after recovery_timeout")

    def test_half_open_allows_one_test_call(self):
        """Circuit Breaker: HALF_OPEN allows 1 test call"""
        cb = CircuitBreaker("test_source", failure_threshold=3, recovery_timeout=0.1)
        
        # Force open then wait
        for _ in range(3):
            cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # Triggers transition to HALF_OPEN
        
        assert cb.state == BreakerState.HALF_OPEN
        assert cb.can_execute() is True  # Should allow test call
        print("✅ HALF_OPEN allows test call")

    def test_success_in_half_open_closes_breaker(self):
        """Circuit Breaker: success in HALF_OPEN -> CLOSED (recovered)"""
        cb = CircuitBreaker("test_source", failure_threshold=3, recovery_timeout=0.1)
        
        # Force open then wait
        for _ in range(3):
            cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # Triggers HALF_OPEN
        
        # Record success
        cb.record_success()
        
        assert cb.state == BreakerState.CLOSED, f"Expected CLOSED after success, got {cb.state}"
        assert cb.failure_count == 0
        print("✅ Success in HALF_OPEN recovers to CLOSED")

    def test_failure_in_half_open_reopens_breaker(self):
        """Circuit Breaker: failure in HALF_OPEN -> OPEN"""
        cb = CircuitBreaker("test_source", failure_threshold=3, recovery_timeout=0.1)
        
        # Force open then wait
        for _ in range(3):
            cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # Triggers HALF_OPEN
        
        # Record failure in HALF_OPEN
        cb.record_failure()
        
        assert cb.state == BreakerState.OPEN, f"Expected OPEN after failure in HALF_OPEN, got {cb.state}"
        print("✅ Failure in HALF_OPEN reopens breaker")


class TestCircuitBreakerRegistry:
    """Unit tests for Circuit Breaker Registry"""

    def test_registry_creates_breakers_on_demand(self):
        """Registry creates breakers on first access"""
        registry = CircuitBreakerRegistry()
        
        cb = registry.get("vix_source")
        
        assert cb is not None
        assert cb.name == "vix_source"
        assert cb.state == BreakerState.CLOSED
        print("✅ Registry creates breakers on demand")

    def test_registry_returns_same_breaker(self):
        """Registry returns same breaker instance"""
        registry = CircuitBreakerRegistry()
        
        cb1 = registry.get("vix_source")
        cb2 = registry.get("vix_source")
        
        assert cb1 is cb2
        print("✅ Registry returns same breaker instance")

    def test_get_all_status(self):
        """Registry get_all_status returns all breaker states"""
        registry = CircuitBreakerRegistry()
        
        registry.get("vix")
        registry.get("treasury")
        
        status = registry.get_all_status()
        
        assert "vix" in status
        assert "treasury" in status
        assert status["vix"]["state"] == "CLOSED"
        print("✅ Registry get_all_status works correctly")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UNIT TESTS — DataEpochManager
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDataEpochManager:
    """Unit tests for DataEpoch RCU pattern"""

    def test_seal_epoch_creates_immutable_snapshot(self):
        """DataEpoch: seal_epoch() creates immutable epoch with all 7 data sources"""
        lkg = LKGStore()
        registry = CircuitBreakerRegistry()
        manager = DataEpochManager(lkg, registry)
        
        # Update draft with some data
        manager.update_draft("vix", {"value": 25.0}, Confidence.LIVE)
        manager.update_draft("treasury", {"us10y": 4.5}, Confidence.LIVE)
        
        # Seal epoch
        epoch = manager.seal_epoch()
        
        assert epoch is not None
        assert epoch.epoch_id == 1
        assert epoch.sealed_at is not None
        assert epoch.vix["value"] == 25.0
        assert epoch.treasury["us10y"] == 4.5
        # Other sources should have fallback data
        assert epoch.vxn is not None
        assert epoch.rvx is not None
        assert epoch.term_structure is not None
        assert epoch.gamma is not None
        assert epoch.economic_calendar is not None
        print("✅ seal_epoch() creates immutable epoch with all 7 sources")

    def test_epoch_manager_current_returns_last_sealed(self):
        """DataEpoch: epoch_manager.current returns last sealed epoch"""
        lkg = LKGStore()
        registry = CircuitBreakerRegistry()
        manager = DataEpochManager(lkg, registry)
        
        assert manager.current is None  # No epoch yet
        
        manager.update_draft("vix", {"value": 20.0}, Confidence.LIVE)
        epoch1 = manager.seal_epoch()
        
        assert manager.current is epoch1
        assert manager.current.epoch_id == 1
        
        manager.update_draft("vix", {"value": 22.0}, Confidence.LIVE)
        epoch2 = manager.seal_epoch()
        
        assert manager.current is epoch2
        assert manager.current.epoch_id == 2
        print("✅ epoch_manager.current returns last sealed epoch")

    def test_epoch_includes_confidence_map(self):
        """DataEpoch includes confidence_map for all sources"""
        lkg = LKGStore()
        registry = CircuitBreakerRegistry()
        manager = DataEpochManager(lkg, registry)
        
        manager.update_draft("vix", {"value": 25.0}, Confidence.LIVE)
        manager.update_draft("treasury", {"us10y": 4.5}, Confidence.STALE)
        
        epoch = manager.seal_epoch()
        
        assert "vix" in epoch.confidence_map
        assert "treasury" in epoch.confidence_map
        assert epoch.confidence_map["vix"] == "LIVE"
        assert epoch.confidence_map["treasury"] == "STALE"
        # Fallback sources
        assert epoch.confidence_map["vxn"] == "FALLBACK"
        print("✅ Epoch includes confidence_map for all sources")

    def test_epoch_computes_dqs(self):
        """DataEpoch computes DQS from confidence levels"""
        lkg = LKGStore()
        registry = CircuitBreakerRegistry()
        manager = DataEpochManager(lkg, registry)
        
        # All LIVE — should have high DQS
        manager.update_draft("vix", {"value": 25.0}, Confidence.LIVE)
        manager.update_draft("term_structure", {"ratio": 0.9}, Confidence.LIVE)
        manager.update_draft("treasury", {"us10y": 4.5}, Confidence.LIVE)
        manager.update_draft("gamma", {"net_gex": 1000}, Confidence.LIVE)
        
        epoch = manager.seal_epoch()
        
        assert epoch.dqs == 1.0  # All weighted sources are LIVE
        assert "dqs_breakdown" in dir(epoch)
        print(f"✅ Epoch computes DQS: {epoch.dqs}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UNIT TESTS — DQS Step Function
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDQSStepFunction:
    """Unit tests for Data Quality Score step function"""

    def test_all_live_dqs_is_1(self):
        """DQS: all LIVE -> DQS = 1.0"""
        confidence_map = {
            "vix": "LIVE",
            "term_structure": "LIVE",
            "treasury": "LIVE",
            "gamma": "LIVE",
        }
        
        dqs, breakdown = compute_dqs(confidence_map)
        
        assert dqs == 1.0, f"Expected DQS=1.0 for all LIVE, got {dqs}"
        print(f"✅ All LIVE -> DQS = {dqs}")

    def test_all_live_lot_unchanged(self):
        """DQS Step Function: all LIVE -> DQS >= 0.9 -> lot_pct unchanged (FULL)"""
        lot_pct = 100
        dqs = 1.0
        
        adjusted, action, reason = apply_dqs_lot_scaling(lot_pct, dqs)
        
        assert adjusted == 100
        assert action == "FULL"
        assert reason is None
        print("✅ DQS >= 0.9 -> lot_pct unchanged (FULL)")

    def test_stale_sources_reduce_dqs(self):
        """DQS: 2+ STALE sources -> DQS in [0.75, 0.9)"""
        confidence_map = {
            "vix": "STALE",
            "term_structure": "STALE",
            "treasury": "LIVE",
            "gamma": "LIVE",
        }
        
        dqs, breakdown = compute_dqs(confidence_map)
        
        # vix=3*0.7=2.1, term=3*0.7=2.1, treasury=4*1.0=4, gamma=3*1.0=3
        # total = 11.2 / 13 = 0.862
        assert 0.75 <= dqs < 0.9, f"Expected DQS in [0.75, 0.9), got {dqs}"
        print(f"✅ 2 STALE sources -> DQS = {dqs}")

    def test_stale_triggers_defensive_mode(self):
        """DQS Step Function: DQS in [0.75, 0.9) -> lot_pct halved (DEFENSIVE)"""
        lot_pct = 100
        dqs = 0.85  # In defensive range
        
        adjusted, action, reason = apply_dqs_lot_scaling(lot_pct, dqs)
        
        assert adjusted == 50  # Halved
        assert action == "DEFENSIVE"
        assert reason is not None
        print(f"✅ DQS {dqs} -> DEFENSIVE mode, lot {lot_pct}% -> {adjusted}%")

    def test_fallback_sources_reduce_dqs_significantly(self):
        """DQS: 3+ FALLBACK sources -> DQS < 0.75"""
        confidence_map = {
            "vix": "FALLBACK",
            "term_structure": "FALLBACK",
            "treasury": "FALLBACK",
            "gamma": "LIVE",
        }
        
        dqs, breakdown = compute_dqs(confidence_map)
        
        # vix=3*0.3=0.9, term=3*0.3=0.9, treasury=4*0.3=1.2, gamma=3*1.0=3
        # total = 6.0 / 13 = 0.462
        assert dqs < 0.75, f"Expected DQS < 0.75, got {dqs}"
        print(f"✅ 3 FALLBACK sources -> DQS = {dqs}")

    def test_low_dqs_triggers_hard_stop(self):
        """DQS Step Function: DQS < 0.75 -> HARD_STOP (v3_status=DQS_HARD_STOP)"""
        lot_pct = 100
        dqs = 0.5  # Below threshold
        
        adjusted, action, reason = apply_dqs_lot_scaling(lot_pct, dqs)
        
        assert adjusted == 0
        assert action == "HARD_STOP"
        assert "HARD STOP" in reason
        print(f"✅ DQS {dqs} -> HARD_STOP, lot = 0%")

    def test_dqs_weights_are_correct(self):
        """Verify DQS weights: vix=3, term_structure=3, treasury=4, gamma=3"""
        assert DQS_WEIGHTS["vix"] == 3
        assert DQS_WEIGHTS["term_structure"] == 3
        assert DQS_WEIGHTS["treasury"] == 4
        assert DQS_WEIGHTS["gamma"] == 3
        assert sum(DQS_WEIGHTS.values()) == 13
        print("✅ DQS weights are correct (total=13)")

    def test_confidence_multipliers_are_correct(self):
        """Verify confidence multipliers: LIVE=1.0, STALE=0.7, FALLBACK=0.3"""
        assert CONFIDENCE_MULTIPLIER["LIVE"] == 1.0
        assert CONFIDENCE_MULTIPLIER["STALE"] == 0.7
        assert CONFIDENCE_MULTIPLIER["FALLBACK"] == 0.3
        print("✅ Confidence multipliers are correct")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API TESTS — /api/v3/data-quality
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDataQualityAPI:
    """API tests for /api/v3/data-quality endpoint"""

    @pytest.fixture
    def api_client(self):
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "Cookie": f"session_token={SESSION_TOKEN}",
        })
        return session

    def test_data_quality_endpoint_returns_200(self, api_client):
        """GET /api/v3/data-quality returns 200"""
        response = api_client.get(f"{BASE_URL}/api/v3/data-quality")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✅ GET /api/v3/data-quality returns 200")

    def test_data_quality_returns_epoch_status(self, api_client):
        """GET /api/v3/data-quality returns epoch status"""
        response = api_client.get(f"{BASE_URL}/api/v3/data-quality")
        data = response.json()
        
        assert "epoch" in data, "Missing 'epoch' in response"
        epoch = data["epoch"]
        assert "epoch_id" in epoch
        assert "sealed_at" in epoch
        assert "dqs" in epoch
        assert "confidence_map" in epoch
        print(f"✅ Epoch status: id={epoch['epoch_id']}, dqs={epoch.get('dqs')}")

    def test_data_quality_returns_lkg_keys(self, api_client):
        """GET /api/v3/data-quality returns LKG keys for all 7 sources"""
        response = api_client.get(f"{BASE_URL}/api/v3/data-quality")
        data = response.json()
        
        assert "lkg_keys" in data, "Missing 'lkg_keys' in response"
        lkg_keys = data["lkg_keys"]
        
        required_keys = ["vix", "vxn", "rvx", "term_structure", "treasury", "gamma", "economic_calendar"]
        for key in required_keys:
            assert key in lkg_keys, f"Missing LKG key: {key}"
            assert "has_real_data" in lkg_keys[key]
            assert "confidence" in lkg_keys[key]
            assert "age_seconds" in lkg_keys[key]
        
        print(f"✅ LKG keys present for all 7 sources")
        for key in required_keys:
            print(f"   {key}: {lkg_keys[key]['confidence']} (has_real={lkg_keys[key]['has_real_data']})")

    def test_data_quality_returns_circuit_breakers(self, api_client):
        """GET /api/v3/data-quality returns circuit breakers"""
        response = api_client.get(f"{BASE_URL}/api/v3/data-quality")
        data = response.json()
        
        assert "circuit_breakers" in data, "Missing 'circuit_breakers' in response"
        print(f"✅ Circuit breakers: {list(data['circuit_breakers'].keys())}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API TESTS — V3 Signal data_quality object
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestV3SignalDataQuality:
    """API tests for V3 signal data_quality object"""

    @pytest.fixture
    def api_client(self):
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "Cookie": f"session_token={SESSION_TOKEN}",
        })
        return session

    def test_v3_signal_returns_data_quality_object(self, api_client):
        """V3 signal /api/v3/signal/MNQ returns data_quality object"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "data_quality" in data, "Missing 'data_quality' in V3 signal response"
        dq = data["data_quality"]
        
        assert "epoch_id" in dq, "Missing epoch_id in data_quality"
        assert "dqs" in dq, "Missing dqs in data_quality"
        assert "dqs_action" in dq, "Missing dqs_action in data_quality"
        assert "confidence_map" in dq, "Missing confidence_map in data_quality"
        
        print(f"✅ V3 signal data_quality: epoch={dq['epoch_id']}, dqs={dq['dqs']}, action={dq['dqs_action']}")

    def test_v3_signal_confidence_map_has_all_sources(self, api_client):
        """V3 signal confidence_map includes all weighted sources"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        data = response.json()
        
        conf_map = data["data_quality"]["confidence_map"]
        
        # At minimum, the DQS-weighted sources should be present
        weighted_sources = ["vix", "term_structure", "treasury", "gamma"]
        for src in weighted_sources:
            assert src in conf_map, f"Missing {src} in confidence_map"
            assert conf_map[src] in ["LIVE", "STALE", "FALLBACK"], f"Invalid confidence for {src}: {conf_map[src]}"
        
        print(f"✅ Confidence map: {conf_map}")

    def test_v3_signal_dqs_action_values(self, api_client):
        """V3 signal dqs_action is one of FULL, DEFENSIVE, HARD_STOP"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        data = response.json()
        
        dqs_action = data["data_quality"]["dqs_action"]
        
        assert dqs_action in ["FULL", "DEFENSIVE", "HARD_STOP"], f"Invalid dqs_action: {dqs_action}"
        print(f"✅ DQS action: {dqs_action}")

    def test_v3_signal_nivel1_includes_dqs(self, api_client):
        """V3 signal nivel_1 includes dqs and dqs_action for frontend"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        data = response.json()
        
        n1 = data.get("nivel_1", {})
        
        assert "dqs" in n1, "Missing dqs in nivel_1"
        assert "dqs_action" in n1, "Missing dqs_action in nivel_1"
        
        print(f"✅ nivel_1 includes dqs={n1['dqs']}, dqs_action={n1['dqs_action']}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VALIDATION TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDataValidation:
    """Unit tests for data validation"""

    def test_validate_vix_in_range(self):
        """validate_data accepts VIX in valid range"""
        assert validate_data("vix", {"value": 20.0}) is True
        assert validate_data("vix", {"value": 8.0}) is True
        assert validate_data("vix", {"value": 90.0}) is True
        print("✅ VIX validation accepts valid range [8, 90]")

    def test_validate_vix_out_of_range(self):
        """validate_data rejects VIX outside valid range"""
        assert validate_data("vix", {"value": 5.0}) is False
        assert validate_data("vix", {"value": 100.0}) is False
        print("✅ VIX validation rejects out-of-range values")

    def test_validate_treasury_spread(self):
        """validate_data accepts treasury spread in valid range"""
        assert validate_data("treasury_spread", {"spread": 0.5}) is True
        assert validate_data("treasury_spread", {"spread": -1.0}) is True
        assert validate_data("treasury_spread", {"spread": -5.0}) is False
        print("✅ Treasury spread validation works correctly")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
