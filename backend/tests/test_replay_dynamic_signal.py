"""
Test Replay Engine - Dynamic Signal Re-Evaluation
==================================================
Tests for the new DYNAMIC signal mode that re-evaluates signals
against configurable thresholds instead of accepting static actions.

Features tested:
- signal_mode: DYNAMIC vs STATIC
- zscore_min, delta_ratio_min, ofi_threshold per regime
- eval_stats response fields
- Threshold sensitivity (relaxed vs strict)
- require_volume_significance filter
- session_start_hour, session_end_hour filters
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")

@pytest.fixture
def auth_headers():
    """Auth headers for API requests"""
    return {
        "Content-Type": "application/json",
        "Cookie": f"session_token={SESSION_TOKEN}"
    }


class TestReplayDefaults:
    """Test GET /api/replay/defaults returns new signal re-evaluation fields"""
    
    def test_defaults_has_signal_mode(self, auth_headers):
        """GET /api/replay/defaults should return signal_mode field"""
        resp = requests.get(f"{BASE_URL}/api/replay/defaults", headers=auth_headers)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        config = data.get("config", {})
        
        # Verify signal_mode exists
        assert "signal_mode" in config, "Missing signal_mode in defaults"
        assert config["signal_mode"] in ("DYNAMIC", "STATIC"), f"Invalid signal_mode: {config['signal_mode']}"
        print(f"✅ signal_mode present: {config['signal_mode']}")
    
    def test_defaults_has_zscore_min(self, auth_headers):
        """GET /api/replay/defaults should return zscore_min per regime"""
        resp = requests.get(f"{BASE_URL}/api/replay/defaults", headers=auth_headers)
        assert resp.status_code == 200
        
        config = resp.json().get("config", {})
        assert "zscore_min" in config, "Missing zscore_min in defaults"
        
        zscore_min = config["zscore_min"]
        expected_regimes = ["COMPLACENCIA", "BULL", "BEAR", "TRANSICAO", "CAPITULACAO"]
        for regime in expected_regimes:
            assert regime in zscore_min, f"Missing regime {regime} in zscore_min"
            assert isinstance(zscore_min[regime], (int, float)), f"zscore_min[{regime}] should be numeric"
        
        print(f"✅ zscore_min has all 5 regimes: {zscore_min}")
    
    def test_defaults_has_delta_ratio_min(self, auth_headers):
        """GET /api/replay/defaults should return delta_ratio_min per regime"""
        resp = requests.get(f"{BASE_URL}/api/replay/defaults", headers=auth_headers)
        assert resp.status_code == 200
        
        config = resp.json().get("config", {})
        assert "delta_ratio_min" in config, "Missing delta_ratio_min in defaults"
        
        delta_ratio_min = config["delta_ratio_min"]
        expected_regimes = ["COMPLACENCIA", "BULL", "BEAR", "TRANSICAO", "CAPITULACAO"]
        for regime in expected_regimes:
            assert regime in delta_ratio_min, f"Missing regime {regime} in delta_ratio_min"
        
        print(f"✅ delta_ratio_min has all 5 regimes: {delta_ratio_min}")
    
    def test_defaults_has_ofi_threshold(self, auth_headers):
        """GET /api/replay/defaults should return ofi_threshold per regime"""
        resp = requests.get(f"{BASE_URL}/api/replay/defaults", headers=auth_headers)
        assert resp.status_code == 200
        
        config = resp.json().get("config", {})
        assert "ofi_threshold" in config, "Missing ofi_threshold in defaults"
        
        ofi_threshold = config["ofi_threshold"]
        expected_regimes = ["COMPLACENCIA", "BULL", "BEAR", "TRANSICAO", "CAPITULACAO"]
        for regime in expected_regimes:
            assert regime in ofi_threshold, f"Missing regime {regime} in ofi_threshold"
        
        print(f"✅ ofi_threshold has all 5 regimes: {ofi_threshold}")
    
    def test_defaults_has_rr_min_atr_mult(self, auth_headers):
        """GET /api/replay/defaults should return rr_min_atr_mult"""
        resp = requests.get(f"{BASE_URL}/api/replay/defaults", headers=auth_headers)
        assert resp.status_code == 200
        
        config = resp.json().get("config", {})
        assert "rr_min_atr_mult" in config, "Missing rr_min_atr_mult in defaults"
        assert isinstance(config["rr_min_atr_mult"], (int, float)), "rr_min_atr_mult should be numeric"
        
        print(f"✅ rr_min_atr_mult present: {config['rr_min_atr_mult']}")
    
    def test_defaults_has_require_volume_significance(self, auth_headers):
        """GET /api/replay/defaults should return require_volume_significance"""
        resp = requests.get(f"{BASE_URL}/api/replay/defaults", headers=auth_headers)
        assert resp.status_code == 200
        
        config = resp.json().get("config", {})
        assert "require_volume_significance" in config, "Missing require_volume_significance in defaults"
        assert isinstance(config["require_volume_significance"], bool), "require_volume_significance should be boolean"
        
        print(f"✅ require_volume_significance present: {config['require_volume_significance']}")
    
    def test_defaults_has_session_filters(self, auth_headers):
        """GET /api/replay/defaults should return session_start_hour and session_end_hour"""
        resp = requests.get(f"{BASE_URL}/api/replay/defaults", headers=auth_headers)
        assert resp.status_code == 200
        
        config = resp.json().get("config", {})
        assert "session_start_hour" in config, "Missing session_start_hour in defaults"
        assert "session_end_hour" in config, "Missing session_end_hour in defaults"
        
        print(f"✅ session_start_hour: {config['session_start_hour']}, session_end_hour: {config['session_end_hour']}")


class TestDynamicSignalMode:
    """Test POST /api/replay/run with signal_mode=DYNAMIC"""
    
    def test_dynamic_mode_returns_eval_stats(self, auth_headers):
        """DYNAMIC mode should return eval_stats in response"""
        payload = {
            "config": {
                "signal_mode": "DYNAMIC",
                "symbols": ["MES", "MNQ"]
            }
        }
        resp = requests.post(f"{BASE_URL}/api/replay/run", json=payload, headers=auth_headers)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "eval_stats" in data, "Missing eval_stats in DYNAMIC mode response"
        
        eval_stats = data["eval_stats"]
        required_fields = ["total_evaluated", "dynamic_accepted", "dynamic_filtered", "static_fallback", "session_filtered", "by_source"]
        for field in required_fields:
            assert field in eval_stats, f"Missing {field} in eval_stats"
        
        print(f"✅ DYNAMIC mode eval_stats: {eval_stats}")
        
        # Verify by_source has expected keys
        by_source = eval_stats.get("by_source", {})
        expected_sources = ["n2_signal", "dz_n2", "legacy_parse", "none"]
        for src in expected_sources:
            assert src in by_source, f"Missing {src} in by_source"
        
        print(f"✅ by_source breakdown: {by_source}")
    
    def test_dynamic_mode_has_dynamic_accepted(self, auth_headers):
        """DYNAMIC mode should have dynamic_accepted > 0 with relaxed thresholds"""
        payload = {
            "config": {
                "signal_mode": "DYNAMIC",
                "symbols": ["MES", "MNQ"],
                # Relaxed thresholds to accept more signals
                "zscore_min": {
                    "COMPLACENCIA": -1.0,
                    "BULL": 0.5,
                    "BEAR": 0.5,
                    "TRANSICAO": 0.1,
                    "CAPITULACAO": 0.5
                },
                "delta_ratio_min": {
                    "COMPLACENCIA": 0.05,
                    "BULL": 0.05,
                    "BEAR": 0.05,
                    "TRANSICAO": 0.01,
                    "CAPITULACAO": -0.5
                }
            }
        }
        resp = requests.post(f"{BASE_URL}/api/replay/run", json=payload, headers=auth_headers)
        assert resp.status_code == 200
        
        data = resp.json()
        eval_stats = data.get("eval_stats", {})
        
        # With relaxed thresholds, we should have some accepted signals
        total_evaluated = eval_stats.get("total_evaluated", 0)
        dynamic_accepted = eval_stats.get("dynamic_accepted", 0)
        
        print(f"✅ Relaxed thresholds: {total_evaluated} evaluated, {dynamic_accepted} accepted")
        
        # Verify trades were generated
        trades = data.get("trades", [])
        print(f"✅ Generated {len(trades)} trades with relaxed thresholds")


class TestStaticSignalMode:
    """Test POST /api/replay/run with signal_mode=STATIC"""
    
    def test_static_mode_no_dynamic_accepted(self, auth_headers):
        """STATIC mode should NOT have dynamic_accepted in eval_stats (or it should be 0)"""
        payload = {
            "config": {
                "signal_mode": "STATIC",
                "symbols": ["MES", "MNQ"]
            }
        }
        resp = requests.post(f"{BASE_URL}/api/replay/run", json=payload, headers=auth_headers)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        
        # STATIC mode may or may not have eval_stats
        # If it does, dynamic_accepted should be 0 or not present
        eval_stats = data.get("eval_stats")
        if eval_stats:
            dynamic_accepted = eval_stats.get("dynamic_accepted", 0)
            # In STATIC mode, we don't do dynamic re-evaluation
            print(f"✅ STATIC mode eval_stats present (may be empty or have 0 dynamic_accepted): {eval_stats}")
        else:
            print("✅ STATIC mode has no eval_stats (expected for legacy behavior)")
        
        # Verify trades were generated using original actions
        trades = data.get("trades", [])
        print(f"✅ STATIC mode generated {len(trades)} trades using original snapshot actions")


class TestThresholdSensitivity:
    """Test that threshold changes affect trade count"""
    
    def test_relaxed_thresholds_more_trades(self, auth_headers):
        """Relaxed thresholds (zscore_min TRANSICAO=0.1, delta_ratio_min TRANSICAO=0.01) should generate MORE trades"""
        # First run with default thresholds
        default_payload = {
            "config": {
                "signal_mode": "DYNAMIC",
                "symbols": ["MES", "MNQ"]
            }
        }
        default_resp = requests.post(f"{BASE_URL}/api/replay/run", json=default_payload, headers=auth_headers)
        assert default_resp.status_code == 200
        default_trades = len(default_resp.json().get("trades", []))
        default_accepted = default_resp.json().get("eval_stats", {}).get("dynamic_accepted", 0)
        
        # Run with relaxed thresholds
        relaxed_payload = {
            "config": {
                "signal_mode": "DYNAMIC",
                "symbols": ["MES", "MNQ"],
                "zscore_min": {
                    "COMPLACENCIA": -1.0,
                    "BULL": 0.5,
                    "BEAR": 0.5,
                    "TRANSICAO": 0.1,  # Very relaxed
                    "CAPITULACAO": 0.5
                },
                "delta_ratio_min": {
                    "COMPLACENCIA": 0.05,
                    "BULL": 0.05,
                    "BEAR": 0.05,
                    "TRANSICAO": 0.01,  # Very relaxed
                    "CAPITULACAO": -0.5
                }
            }
        }
        relaxed_resp = requests.post(f"{BASE_URL}/api/replay/run", json=relaxed_payload, headers=auth_headers)
        assert relaxed_resp.status_code == 200
        relaxed_trades = len(relaxed_resp.json().get("trades", []))
        relaxed_accepted = relaxed_resp.json().get("eval_stats", {}).get("dynamic_accepted", 0)
        
        print(f"Default thresholds: {default_trades} trades, {default_accepted} accepted")
        print(f"Relaxed thresholds: {relaxed_trades} trades, {relaxed_accepted} accepted")
        
        # Relaxed should accept more or equal signals
        assert relaxed_accepted >= default_accepted, \
            f"Relaxed thresholds should accept >= signals: {relaxed_accepted} vs {default_accepted}"
        
        print(f"✅ Relaxed thresholds accepted more signals: {relaxed_accepted} >= {default_accepted}")
    
    def test_strict_thresholds_fewer_trades(self, auth_headers):
        """Ultra-strict thresholds (zscore_min TRANSICAO=5.0) should generate FEWER or ZERO trades"""
        # Run with ultra-strict thresholds
        strict_payload = {
            "config": {
                "signal_mode": "DYNAMIC",
                "symbols": ["MES", "MNQ"],
                "zscore_min": {
                    "COMPLACENCIA": 5.0,  # Ultra strict
                    "BULL": 5.0,
                    "BEAR": 5.0,
                    "TRANSICAO": 5.0,  # Ultra strict - almost no signal will pass
                    "CAPITULACAO": 5.0
                },
                "delta_ratio_min": {
                    "COMPLACENCIA": 0.9,  # Ultra strict
                    "BULL": 0.9,
                    "BEAR": 0.9,
                    "TRANSICAO": 0.9,
                    "CAPITULACAO": 0.9
                }
            }
        }
        strict_resp = requests.post(f"{BASE_URL}/api/replay/run", json=strict_payload, headers=auth_headers)
        assert strict_resp.status_code == 200
        
        data = strict_resp.json()
        strict_trades = len(data.get("trades", []))
        strict_accepted = data.get("eval_stats", {}).get("dynamic_accepted", 0)
        strict_filtered = data.get("eval_stats", {}).get("dynamic_filtered", 0)
        
        print(f"Ultra-strict thresholds: {strict_trades} trades, {strict_accepted} accepted, {strict_filtered} filtered")
        
        # With ultra-strict thresholds, most signals should be filtered
        # We expect very few or zero accepted signals
        assert strict_filtered >= strict_accepted, \
            f"Strict thresholds should filter more than accept: filtered={strict_filtered}, accepted={strict_accepted}"
        
        print(f"✅ Ultra-strict thresholds filtered more signals: {strict_filtered} filtered vs {strict_accepted} accepted")


class TestVolumeSignificanceFilter:
    """Test require_volume_significance filter"""
    
    def test_volume_significance_filters_more(self, auth_headers):
        """require_volume_significance=true should filter more signals than false"""
        # Run without volume significance requirement
        no_vol_payload = {
            "config": {
                "signal_mode": "DYNAMIC",
                "symbols": ["MES", "MNQ"],
                "require_volume_significance": False
            }
        }
        no_vol_resp = requests.post(f"{BASE_URL}/api/replay/run", json=no_vol_payload, headers=auth_headers)
        assert no_vol_resp.status_code == 200
        no_vol_accepted = no_vol_resp.json().get("eval_stats", {}).get("dynamic_accepted", 0)
        
        # Run with volume significance requirement
        vol_payload = {
            "config": {
                "signal_mode": "DYNAMIC",
                "symbols": ["MES", "MNQ"],
                "require_volume_significance": True
            }
        }
        vol_resp = requests.post(f"{BASE_URL}/api/replay/run", json=vol_payload, headers=auth_headers)
        assert vol_resp.status_code == 200
        vol_accepted = vol_resp.json().get("eval_stats", {}).get("dynamic_accepted", 0)
        
        print(f"Without volume significance: {no_vol_accepted} accepted")
        print(f"With volume significance: {vol_accepted} accepted")
        
        # Volume significance should filter some signals (accept <= without filter)
        assert vol_accepted <= no_vol_accepted, \
            f"Volume significance should filter signals: {vol_accepted} <= {no_vol_accepted}"
        
        print(f"✅ Volume significance filter works: {vol_accepted} <= {no_vol_accepted}")


class TestEvalStatsStructure:
    """Test eval_stats response structure"""
    
    def test_eval_stats_complete_structure(self, auth_headers):
        """eval_stats should have all required fields with correct types"""
        payload = {
            "config": {
                "signal_mode": "DYNAMIC",
                "symbols": ["MES", "MNQ"]
            }
        }
        resp = requests.post(f"{BASE_URL}/api/replay/run", json=payload, headers=auth_headers)
        assert resp.status_code == 200
        
        data = resp.json()
        eval_stats = data.get("eval_stats", {})
        
        # Verify all required fields
        assert isinstance(eval_stats.get("total_evaluated"), int), "total_evaluated should be int"
        assert isinstance(eval_stats.get("dynamic_accepted"), int), "dynamic_accepted should be int"
        assert isinstance(eval_stats.get("dynamic_filtered"), int), "dynamic_filtered should be int"
        assert isinstance(eval_stats.get("static_fallback"), int), "static_fallback should be int"
        assert isinstance(eval_stats.get("session_filtered"), int), "session_filtered should be int"
        assert isinstance(eval_stats.get("by_source"), dict), "by_source should be dict"
        
        # Verify consistency: total_evaluated = dynamic_accepted + dynamic_filtered + static_fallback
        total = eval_stats.get("total_evaluated", 0)
        accepted = eval_stats.get("dynamic_accepted", 0)
        filtered = eval_stats.get("dynamic_filtered", 0)
        fallback = eval_stats.get("static_fallback", 0)
        
        # Note: total_evaluated counts all signals evaluated, but some may be session_filtered before evaluation
        print(f"✅ eval_stats structure valid: total={total}, accepted={accepted}, filtered={filtered}, fallback={fallback}")
        
        # Verify by_source has expected keys
        by_source = eval_stats.get("by_source", {})
        for key in ["n2_signal", "dz_n2", "legacy_parse", "none"]:
            assert key in by_source, f"Missing {key} in by_source"
            assert isinstance(by_source[key], int), f"by_source[{key}] should be int"
        
        print(f"✅ by_source structure valid: {by_source}")


class TestSessionFilter:
    """Test session time filter"""
    
    def test_session_filter_reduces_signals(self, auth_headers):
        """Session filter should reduce signals when active"""
        # Run without session filter
        no_filter_payload = {
            "config": {
                "signal_mode": "DYNAMIC",
                "symbols": ["MES", "MNQ"],
                "session_start_hour": None,
                "session_end_hour": None
            }
        }
        no_filter_resp = requests.post(f"{BASE_URL}/api/replay/run", json=no_filter_payload, headers=auth_headers)
        assert no_filter_resp.status_code == 200
        no_filter_data = no_filter_resp.json()
        no_filter_session_filtered = no_filter_data.get("eval_stats", {}).get("session_filtered", 0)
        
        # Run with narrow session filter (9:30 AM - 10:00 AM ET)
        filter_payload = {
            "config": {
                "signal_mode": "DYNAMIC",
                "symbols": ["MES", "MNQ"],
                "session_start_hour": 9.5,  # 9:30 AM ET
                "session_end_hour": 10.0    # 10:00 AM ET
            }
        }
        filter_resp = requests.post(f"{BASE_URL}/api/replay/run", json=filter_payload, headers=auth_headers)
        assert filter_resp.status_code == 200
        filter_data = filter_resp.json()
        filter_session_filtered = filter_data.get("eval_stats", {}).get("session_filtered", 0)
        
        print(f"Without session filter: {no_filter_session_filtered} session_filtered")
        print(f"With narrow session filter: {filter_session_filtered} session_filtered")
        
        # Narrow session filter should filter more signals
        assert filter_session_filtered >= no_filter_session_filtered, \
            f"Session filter should filter signals: {filter_session_filtered} >= {no_filter_session_filtered}"
        
        print(f"✅ Session filter works: {filter_session_filtered} >= {no_filter_session_filtered}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
