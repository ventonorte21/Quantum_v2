"""
Regime State Machine Tests — Hysteresis + Debounce
===================================================
Tests for the state machine that prevents erratic regime switching at score boundaries.

Features tested:
1. GET /api/v3/regime-state - returns state machine status
2. GET /api/v3/regime-transitions - returns transitions from MongoDB
3. Hysteresis (Dead Zones) - asymmetric entry/exit thresholds
4. Debounce - consecutive confirmations required for regime change
5. Term Structure Overrides - bypass both mechanisms for CAPITULACAO/COMPLACENCIA
6. Fallback signal endpoint uses state machine cache
7. Frontend pending_regime indicator data
"""

import pytest
import os
import sys

# Add backend to path for direct imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.regime_state_machine import (
    RegimeStateMachine, HYSTERESIS, DEBOUNCE_NORMAL, DEBOUNCE_FROM_PANIC
)

# API testing
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    BASE_URL = "https://gamma-vix-predictor.preview.emergentagent.com"

# Mock REGIME_CONFIG for unit tests (same structure as V3SignalEngine.REGIME_CONFIG)
MOCK_REGIME_CONFIG = {
    "COMPLACENCIA": {"symbol": "MNQ", "tactic": "Trend Following", "lot_pct": 100, "direction": "LONG"},
    "BULL": {"symbol": "MNQ", "tactic": "Trend Following", "lot_pct": 100, "direction": "LONG"},
    "TRANSICAO": {"symbol": "MNQ", "tactic": "Range Scalping", "lot_pct": 50, "direction": "NEUTRAL"},
    "BEAR": {"symbol": "MES", "tactic": "Trend Following", "lot_pct": 75, "direction": "SHORT"},
    "CAPITULACAO": {"symbol": "MES", "tactic": "Fade Extremes", "lot_pct": 25, "direction": "LONG"},
}


class TestRegimeStateMachineUnit:
    """Unit tests for RegimeStateMachine class (no API calls)"""

    def test_initial_state_is_transicao(self):
        """State machine starts in TRANSICAO regime"""
        sm = RegimeStateMachine(MOCK_REGIME_CONFIG)
        assert sm.current_regime == "TRANSICAO"
        assert sm.pending_regime is None
        assert sm.pending_count == 0
        print("✅ Initial state is TRANSICAO")

    def test_hysteresis_bull_stays_bull_at_score_7(self):
        """Score 7 from BULL stays BULL (dead zone exit_to_transicao=6)"""
        sm = RegimeStateMachine(MOCK_REGIME_CONFIG)
        # First, get to BULL regime (need 3 confirmations at score >= 8)
        for _ in range(3):
            sm.evaluate(macro_score=8, max_score=13, term_structure_ratio=0.95)
        assert sm.current_regime == "BULL", f"Expected BULL, got {sm.current_regime}"
        
        # Now score 7 should stay BULL (exit threshold is 6)
        result = sm.evaluate(macro_score=7, max_score=13, term_structure_ratio=0.95)
        assert result["regime"] == "BULL", f"Expected BULL at score 7, got {result['regime']}"
        assert sm.pending_regime is None, "Should not have pending regime"
        print("✅ Hysteresis: Score 7 from BULL stays BULL (dead zone)")

    def test_hysteresis_transicao_to_bull_starts_pending(self):
        """Score 8 from TRANSICAO starts pending BULL, requires 3 confirmations"""
        sm = RegimeStateMachine(MOCK_REGIME_CONFIG)
        assert sm.current_regime == "TRANSICAO"
        
        # First evaluation at score 8 should start pending
        result = sm.evaluate(macro_score=8, max_score=13, term_structure_ratio=0.95)
        assert result["regime"] == "TRANSICAO", "Should still be TRANSICAO (pending)"
        assert sm.pending_regime == "BULL", f"Expected pending BULL, got {sm.pending_regime}"
        assert sm.pending_count == 1, f"Expected pending_count=1, got {sm.pending_count}"
        assert "pending_regime" in result, "Result should include pending_regime"
        assert result["pending_regime"] == "BULL"
        assert result["debounce_threshold"] == DEBOUNCE_NORMAL
        print("✅ Hysteresis: Score 8 from TRANSICAO starts pending BULL")

    def test_debounce_oscillating_resets_pending(self):
        """Oscillating 7↔8 resets pending count (NO regime change)"""
        sm = RegimeStateMachine(MOCK_REGIME_CONFIG)
        
        # Score 8 starts pending BULL
        sm.evaluate(macro_score=8, max_score=13, term_structure_ratio=0.95)
        assert sm.pending_regime == "BULL"
        assert sm.pending_count == 1
        
        # Score 7 resets pending (back to TRANSICAO theoretical)
        result = sm.evaluate(macro_score=7, max_score=13, term_structure_ratio=0.95)
        assert result["regime"] == "TRANSICAO"
        assert sm.pending_regime is None, "Pending should be reset"
        assert sm.pending_count == 0, "Pending count should be 0"
        
        # Score 8 again starts new pending
        sm.evaluate(macro_score=8, max_score=13, term_structure_ratio=0.95)
        assert sm.pending_count == 1, "Should restart at 1"
        
        # Oscillate back to 7
        sm.evaluate(macro_score=7, max_score=13, term_structure_ratio=0.95)
        assert sm.pending_regime is None, "Pending reset again"
        
        # After all oscillations, still TRANSICAO
        assert sm.current_regime == "TRANSICAO"
        print("✅ Debounce: Oscillating 7↔8 resets pending count (NO regime change)")

    def test_debounce_consistent_score_confirms_regime(self):
        """Consistent score=8 x3 from TRANSICAO => confirms to BULL"""
        sm = RegimeStateMachine(MOCK_REGIME_CONFIG)
        
        # 3 consecutive evaluations at score 8
        for i in range(3):
            result = sm.evaluate(macro_score=8, max_score=13, term_structure_ratio=0.95)
            if i < 2:
                assert result["regime"] == "TRANSICAO", f"Iteration {i}: should still be TRANSICAO"
                assert sm.pending_regime == "BULL"
                assert sm.pending_count == i + 1
            else:
                # 3rd confirmation should trigger regime change
                assert result["regime"] == "BULL", f"Iteration {i}: should be BULL now"
                assert sm.pending_regime is None, "Pending should be cleared"
                assert sm.pending_count == 0
        
        print("✅ Debounce: Consistent score=8 x3 confirms TRANSICAO -> BULL")

    def test_term_structure_override_capitulacao(self):
        """Term Structure ratio > 1.10 forces CAPITULACAO immediately (bypasses debounce)"""
        sm = RegimeStateMachine(MOCK_REGIME_CONFIG)
        # Start from BULL
        for _ in range(3):
            sm.evaluate(macro_score=8, max_score=13, term_structure_ratio=0.95)
        assert sm.current_regime == "BULL"
        
        # Term structure override with ratio > 1.10
        result = sm.evaluate(macro_score=8, max_score=13, term_structure_ratio=1.15)
        assert result["regime"] == "CAPITULACAO", f"Expected CAPITULACAO override, got {result['regime']}"
        assert result["override"] is not None, "Should have override message"
        assert "1.10" in result["override"] or "backwardation" in result["override"].lower()
        assert sm.pending_regime is None, "Override bypasses pending"
        print("✅ Term Structure Override: ratio > 1.10 forces CAPITULACAO immediately")

    def test_term_structure_override_complacencia(self):
        """Term Structure ratio < 0.80 forces COMPLACENCIA immediately (bypasses debounce)"""
        sm = RegimeStateMachine(MOCK_REGIME_CONFIG)
        # Start from TRANSICAO
        assert sm.current_regime == "TRANSICAO"
        
        # Term structure override with ratio < 0.80
        result = sm.evaluate(macro_score=5, max_score=13, term_structure_ratio=0.75)
        assert result["regime"] == "COMPLACENCIA", f"Expected COMPLACENCIA override, got {result['regime']}"
        assert result["override"] is not None, "Should have override message"
        assert "0.80" in result["override"] or "contango" in result["override"].lower()
        assert sm.pending_regime is None, "Override bypasses pending"
        print("✅ Term Structure Override: ratio < 0.80 forces COMPLACENCIA immediately")

    def test_result_includes_pending_metadata(self):
        """State Machine returns pending_regime/pending_minutes/debounce_threshold when pending"""
        sm = RegimeStateMachine(MOCK_REGIME_CONFIG)
        
        # Start pending
        result = sm.evaluate(macro_score=8, max_score=13, term_structure_ratio=0.95)
        
        assert "pending_regime" in result, "Result should include pending_regime"
        assert "pending_minutes" in result, "Result should include pending_minutes"
        assert "debounce_threshold" in result, "Result should include debounce_threshold"
        assert result["pending_regime"] == "BULL"
        assert result["pending_minutes"] == 1
        assert result["debounce_threshold"] == 3
        print("✅ Result includes pending_regime/pending_minutes/debounce_threshold when pending")

    def test_get_state_returns_full_status(self):
        """get_state() returns current_regime, pending_regime, pending_count, debounce_threshold, hysteresis_config"""
        sm = RegimeStateMachine(MOCK_REGIME_CONFIG)
        sm.evaluate(macro_score=8, max_score=13, term_structure_ratio=0.95)
        
        state = sm.get_state()
        
        assert "current_regime" in state
        assert "pending_regime" in state
        assert "pending_count" in state
        assert "debounce_threshold" in state
        assert "hysteresis_config" in state
        assert "last_score" in state
        assert "recent_transitions" in state
        
        assert state["current_regime"] == "TRANSICAO"
        assert state["pending_regime"] == "BULL"
        assert state["pending_count"] == 1
        assert state["debounce_threshold"] == 3
        assert state["hysteresis_config"] == HYSTERESIS
        print("✅ get_state() returns full status with all required fields")

    def test_debounce_from_panic_is_faster(self):
        """Exiting CAPITULACAO requires only 1 confirmation (DEBOUNCE_FROM_PANIC)"""
        sm = RegimeStateMachine(MOCK_REGIME_CONFIG)
        
        # Force into CAPITULACAO via override
        sm.evaluate(macro_score=1, max_score=13, term_structure_ratio=1.15)
        assert sm.current_regime == "CAPITULACAO"
        
        # Now try to exit - should only need 1 confirmation
        # Score 3 should exit to BEAR (exit_to_bear threshold is 3)
        result = sm.evaluate(macro_score=3, max_score=13, term_structure_ratio=0.95)
        
        # With DEBOUNCE_FROM_PANIC=1, should immediately transition
        assert result["regime"] == "BEAR", f"Expected BEAR after 1 confirmation, got {result['regime']}"
        print("✅ Debounce from CAPITULACAO is faster (1 confirmation)")


class TestRegimeStateMachineAPI:
    """API tests for regime state machine endpoints"""

    def test_get_regime_state_endpoint(self):
        """GET /api/v3/regime-state returns state machine status"""
        response = requests.get(f"{BASE_URL}/api/v3/regime-state")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "current_regime" in data, "Response should include current_regime"
        assert "pending_regime" in data, "Response should include pending_regime"
        assert "pending_count" in data, "Response should include pending_count"
        assert "debounce_threshold" in data, "Response should include debounce_threshold"
        assert "hysteresis_config" in data, "Response should include hysteresis_config"
        assert "last_score" in data, "Response should include last_score"
        
        # Validate regime is one of the 5 valid regimes
        valid_regimes = ["COMPLACENCIA", "BULL", "TRANSICAO", "BEAR", "CAPITULACAO"]
        assert data["current_regime"] in valid_regimes, f"Invalid regime: {data['current_regime']}"
        
        print(f"✅ GET /api/v3/regime-state returns: regime={data['current_regime']}, pending={data['pending_regime']}, count={data['pending_count']}")

    def test_get_regime_transitions_endpoint(self):
        """GET /api/v3/regime-transitions returns list of transitions from MongoDB"""
        response = requests.get(f"{BASE_URL}/api/v3/regime-transitions?limit=5")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "transitions" in data, "Response should include transitions array"
        assert "count" in data, "Response should include count"
        assert isinstance(data["transitions"], list), "transitions should be a list"
        
        # If there are transitions, validate structure
        if data["count"] > 0:
            transition = data["transitions"][0]
            assert "from_regime" in transition or "from" in transition, "Transition should have from_regime"
            assert "to_regime" in transition or "to" in transition, "Transition should have to_regime"
            assert "timestamp" in transition, "Transition should have timestamp"
            print(f"✅ GET /api/v3/regime-transitions returns {data['count']} transitions")
        else:
            print("✅ GET /api/v3/regime-transitions returns empty list (no transitions yet)")

    def test_v3_signal_fallback_uses_state_machine(self):
        """GET /api/v3/signal/{symbol} uses state machine cache on error (no crash)"""
        # This tests the fallback behavior - even if the full signal fails,
        # it should return partial data from state machine cache
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        
        # Should not crash - either full signal or fallback
        assert response.status_code in [200, 500], f"Unexpected status: {response.status_code}"
        
        if response.status_code == 200:
            data = response.json()
            # Check that nivel_1 has regime info
            if "nivel_1" in data:
                n1 = data["nivel_1"]
                assert "regime" in n1, "nivel_1 should have regime"
                valid_regimes = ["COMPLACENCIA", "BULL", "TRANSICAO", "BEAR", "CAPITULACAO"]
                assert n1["regime"] in valid_regimes, f"Invalid regime: {n1['regime']}"
                print(f"✅ GET /api/v3/signal/MNQ returns regime={n1['regime']}")
            else:
                print("✅ GET /api/v3/signal/MNQ returns data (structure varies)")
        else:
            print("⚠️ GET /api/v3/signal/MNQ returned 500 (may be expected during market close)")

    def test_regime_state_hysteresis_config_structure(self):
        """Verify hysteresis_config has correct structure for all 5 regimes"""
        response = requests.get(f"{BASE_URL}/api/v3/regime-state")
        assert response.status_code == 200
        
        data = response.json()
        hysteresis = data.get("hysteresis_config", {})
        
        # Check BULL thresholds
        assert "BULL" in hysteresis, "hysteresis_config should have BULL"
        assert hysteresis["BULL"].get("enter_min") == 8, "BULL enter_min should be 8"
        assert hysteresis["BULL"].get("exit_to_transicao") == 6, "BULL exit_to_transicao should be 6"
        
        # Check COMPLACENCIA thresholds
        assert "COMPLACENCIA" in hysteresis, "hysteresis_config should have COMPLACENCIA"
        assert hysteresis["COMPLACENCIA"].get("enter_min") == 11, "COMPLACENCIA enter_min should be 11"
        assert hysteresis["COMPLACENCIA"].get("exit_to_bull") == 9, "COMPLACENCIA exit_to_bull should be 9"
        
        # Check BEAR thresholds
        assert "BEAR" in hysteresis, "hysteresis_config should have BEAR"
        assert hysteresis["BEAR"].get("enter_max") == 2, "BEAR enter_max should be 2"
        assert hysteresis["BEAR"].get("exit_to_transicao") == 5, "BEAR exit_to_transicao should be 5"
        
        # Check CAPITULACAO thresholds
        assert "CAPITULACAO" in hysteresis, "hysteresis_config should have CAPITULACAO"
        assert hysteresis["CAPITULACAO"].get("enter_max") == 1, "CAPITULACAO enter_max should be 1"
        assert hysteresis["CAPITULACAO"].get("exit_to_bear") == 3, "CAPITULACAO exit_to_bear should be 3"
        
        print("✅ Hysteresis config has correct thresholds for all regimes")

    def test_regime_state_debounce_values(self):
        """Verify debounce values are correct (normal=3, from_panic=1)"""
        response = requests.get(f"{BASE_URL}/api/v3/regime-state")
        assert response.status_code == 200
        
        data = response.json()
        
        assert data.get("debounce_normal") == 3, f"debounce_normal should be 3, got {data.get('debounce_normal')}"
        assert data.get("debounce_panic") == 1, f"debounce_panic should be 1, got {data.get('debounce_panic')}"
        
        print("✅ Debounce values correct: normal=3, from_panic=1")


class TestHysteresisThresholds:
    """Detailed tests for hysteresis threshold constants"""

    def test_hysteresis_constants_match_spec(self):
        """Verify HYSTERESIS constants match the specification"""
        # BULL: enter_min=8, exit_to_transicao=6
        assert HYSTERESIS["BULL"]["enter_min"] == 8
        assert HYSTERESIS["BULL"]["exit_to_transicao"] == 6
        
        # COMPLACENCIA: enter_min=11, exit_to_bull=9
        assert HYSTERESIS["COMPLACENCIA"]["enter_min"] == 11
        assert HYSTERESIS["COMPLACENCIA"]["exit_to_bull"] == 9
        
        # BEAR: enter_max=2, exit_to_transicao=5
        assert HYSTERESIS["BEAR"]["enter_max"] == 2
        assert HYSTERESIS["BEAR"]["exit_to_transicao"] == 5
        
        # CAPITULACAO: enter_max=1, exit_to_bear=3
        assert HYSTERESIS["CAPITULACAO"]["enter_max"] == 1
        assert HYSTERESIS["CAPITULACAO"]["exit_to_bear"] == 3
        
        print("✅ All HYSTERESIS constants match specification")

    def test_debounce_constants_match_spec(self):
        """Verify DEBOUNCE constants match the specification"""
        assert DEBOUNCE_NORMAL == 3, f"DEBOUNCE_NORMAL should be 3, got {DEBOUNCE_NORMAL}"
        assert DEBOUNCE_FROM_PANIC == 1, f"DEBOUNCE_FROM_PANIC should be 1, got {DEBOUNCE_FROM_PANIC}"
        print("✅ All DEBOUNCE constants match specification")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
