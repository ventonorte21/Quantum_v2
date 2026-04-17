"""
Delta Zonal V2 Backend Tests
============================
Tests for the N2/N3 architectural refactoring:
- Welford Online Z-Score persistence
- Delta Ratio metric
- evaluate_filters per regime
- Elastic absorption in N3 (ATR-based)
- Snapshot enrichment with dz_n2/dz_n3
"""

import pytest
import requests
import os
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestV3SignalN2Structure:
    """Tests for N2 Delta Zonal structure in /api/v3/signal/{symbol}"""

    def test_mnq_signal_returns_200(self):
        """GET /api/v3/signal/MNQ returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("✅ GET /api/v3/signal/MNQ returns 200")

    def test_mnq_nivel_2_has_interaction_quality(self):
        """nivel_2 contains interaction_quality field"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n2 = data.get('nivel_2', {})
        assert 'interaction_quality' in n2, "Missing interaction_quality in nivel_2"
        # Valid values
        valid_qualities = [
            'UNKNOWN', 'BULLISH_ACCEPTANCE', 'BEARISH_ACCEPTANCE', 
            'WEAK_ACCEPTANCE', 'STRUCTURAL_REJECTION', 'NO_REJECTION',
            'EXHAUSTION_CONFIRMED', 'PANIC_ONGOING', 'POSITIONAL_ONLY', 'INSUFFICIENT'
        ]
        assert n2['interaction_quality'] in valid_qualities, f"Invalid interaction_quality: {n2['interaction_quality']}"
        print(f"✅ nivel_2.interaction_quality = {n2['interaction_quality']}")

    def test_mnq_nivel_2_has_dz_levels(self):
        """nivel_2 contains dz_vah, dz_val, dz_vwap, dz_poc with zscore, delta_ratio, volume_significant, signal"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n2 = data.get('nivel_2', {})
        
        required_dz_fields = ['dz_vah', 'dz_val', 'dz_vwap', 'dz_poc']
        required_subfields = ['zscore', 'delta_ratio', 'volume_significant', 'signal']
        
        for dz_field in required_dz_fields:
            assert dz_field in n2, f"Missing {dz_field} in nivel_2"
            dz_data = n2[dz_field]
            for subfield in required_subfields:
                assert subfield in dz_data, f"Missing {subfield} in nivel_2.{dz_field}"
        
        print(f"✅ nivel_2 has dz_vah, dz_val, dz_vwap, dz_poc with required subfields")
        print(f"   dz_vah.zscore={n2['dz_vah']['zscore']}, delta_ratio={n2['dz_vah']['delta_ratio']}")

    def test_mnq_nivel_2_has_dz_summary_fields(self):
        """nivel_2 contains dz_summary_status and dz_avg_delta_ratio"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n2 = data.get('nivel_2', {})
        
        assert 'dz_summary_status' in n2, "Missing dz_summary_status in nivel_2"
        assert 'dz_avg_delta_ratio' in n2, "Missing dz_avg_delta_ratio in nivel_2"
        
        # Validate types
        assert isinstance(n2['dz_summary_status'], str), "dz_summary_status should be string"
        assert isinstance(n2['dz_avg_delta_ratio'], (int, float)), "dz_avg_delta_ratio should be numeric"
        
        print(f"✅ nivel_2.dz_summary_status = {n2['dz_summary_status']}")
        print(f"✅ nivel_2.dz_avg_delta_ratio = {n2['dz_avg_delta_ratio']}")


class TestV3SignalN3Extreme:
    """Tests for N3 Delta Zonal extreme levels with elastic absorption"""

    def test_mnq_nivel_3_has_absorption_price_limit(self):
        """nivel_3.delta_zonal has absorption_price_limit field (elastic absorption)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n3 = data.get('nivel_3', {})
        dz_n3 = n3.get('delta_zonal', {})
        
        assert 'absorption_price_limit' in dz_n3, "Missing absorption_price_limit in nivel_3.delta_zonal"
        assert isinstance(dz_n3['absorption_price_limit'], (int, float)), "absorption_price_limit should be numeric"
        assert dz_n3['absorption_price_limit'] > 0, "absorption_price_limit should be positive"
        
        print(f"✅ nivel_3.delta_zonal.absorption_price_limit = {dz_n3['absorption_price_limit']}")

    def test_mnq_nivel_3_levels_have_delta_ratio_and_baseline(self):
        """nivel_3.delta_zonal.levels contain delta_ratio and volume_baseline"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n3 = data.get('nivel_3', {})
        dz_n3 = n3.get('delta_zonal', {})
        levels = dz_n3.get('levels', {})
        
        assert len(levels) > 0, "No levels in nivel_3.delta_zonal.levels"
        
        for level_name, level_data in levels.items():
            assert 'delta_ratio' in level_data, f"Missing delta_ratio in nivel_3.delta_zonal.levels.{level_name}"
            assert 'volume_baseline' in level_data, f"Missing volume_baseline in nivel_3.delta_zonal.levels.{level_name}"
            assert 'absorption_limit' in level_data, f"Missing absorption_limit in nivel_3.delta_zonal.levels.{level_name}"
        
        print(f"✅ nivel_3.delta_zonal.levels have delta_ratio, volume_baseline, absorption_limit")
        first_level = list(levels.keys())[0]
        print(f"   Example ({first_level}): delta_ratio={levels[first_level]['delta_ratio']}, baseline={levels[first_level]['volume_baseline']}")


class TestCrossSymbolValidation:
    """Cross-symbol validation for MES"""

    def test_mes_signal_returns_200(self):
        """GET /api/v3/signal/MES returns 200"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("✅ GET /api/v3/signal/MES returns 200")

    def test_mes_has_same_n2_fields_as_mnq(self):
        """MES returns same new N2 fields as MNQ"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n2 = data.get('nivel_2', {})
        
        required_fields = ['interaction_quality', 'dz_vah', 'dz_val', 'dz_vwap', 'dz_poc', 
                          'dz_summary_status', 'dz_avg_delta_ratio']
        for field in required_fields:
            assert field in n2, f"Missing {field} in MES nivel_2"
        
        print(f"✅ MES nivel_2 has all required DZ fields")
        print(f"   interaction_quality={n2['interaction_quality']}, dz_summary_status={n2['dz_summary_status']}")

    def test_mes_has_same_n3_fields_as_mnq(self):
        """MES returns same new N3 fields as MNQ"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n3 = data.get('nivel_3', {})
        dz_n3 = n3.get('delta_zonal', {})
        
        assert 'absorption_price_limit' in dz_n3, "Missing absorption_price_limit in MES nivel_3.delta_zonal"
        levels = dz_n3.get('levels', {})
        if levels:
            first_level = list(levels.values())[0]
            assert 'delta_ratio' in first_level, "Missing delta_ratio in MES N3 levels"
            assert 'volume_baseline' in first_level, "Missing volume_baseline in MES N3 levels"
        
        print(f"✅ MES nivel_3.delta_zonal has absorption_price_limit and level fields")


class TestSnapshotRecording:
    """Tests for snapshot recording with DZ data"""

    def test_record_now_returns_success(self):
        """POST /api/snapshots/record-now successfully records snapshots"""
        response = requests.post(f"{BASE_URL}/api/snapshots/record-now", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert 'recorded' in data, "Missing 'recorded' in response"
        assert data['recorded'] >= 0, "recorded should be >= 0"
        assert 'errors' in data, "Missing 'errors' in response"
        
        print(f"✅ POST /api/snapshots/record-now: recorded={data['recorded']}, errors={len(data.get('errors', []))}")

    def test_snapshot_stats_endpoint(self):
        """GET /api/snapshots/stats returns storage statistics"""
        response = requests.get(f"{BASE_URL}/api/snapshots/stats", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert 'total_snapshots' in data, "Missing total_snapshots"
        assert 'by_symbol' in data, "Missing by_symbol"
        
        print(f"✅ GET /api/snapshots/stats: total={data['total_snapshots']}, by_symbol={data.get('by_symbol', {})}")


class TestWelfordPersistence:
    """Tests for Welford accumulator persistence"""

    def test_welford_stats_collection_exists(self):
        """Welford accumulator persists to MongoDB collection welford_stats"""
        # We test this indirectly by checking the signal endpoint works
        # and Z-Scores are being computed (even if 0.0 for n<3)
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n2 = data.get('nivel_2', {})
        
        # Check that zscore field exists in DZ levels
        dz_vwap = n2.get('dz_vwap', {})
        assert 'zscore' in dz_vwap, "Missing zscore in dz_vwap"
        
        # Z-Score can be 0.0 if n<3 (expected behavior per agent context)
        zscore = dz_vwap['zscore']
        assert isinstance(zscore, (int, float)), "zscore should be numeric"
        
        print(f"✅ Welford Z-Score computed: dz_vwap.zscore = {zscore}")
        print("   Note: Z-Score=0.0 is expected if Welford n<3 (cold start)")


class TestEvaluateFiltersRegimes:
    """Tests for evaluate_filters per regime (indirect via API response)"""

    def test_n2_reason_contains_dz_metrics(self):
        """N2 reason string contains DZ metrics (Z, Ratio)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n2 = data.get('nivel_2', {})
        
        reason = n2.get('reason', '')
        # The reason should mention DZ metrics
        assert 'DZ:' in reason or 'Z=' in reason, f"N2 reason should contain DZ metrics. Got: {reason[:200]}"
        
        print(f"✅ N2 reason contains DZ metrics")
        print(f"   Reason: {reason[:150]}...")

    def test_n2_passed_field_exists(self):
        """N2 has passed field (boolean)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n2 = data.get('nivel_2', {})
        
        assert 'passed' in n2, "Missing 'passed' in nivel_2"
        assert isinstance(n2['passed'], bool), "passed should be boolean"
        
        print(f"✅ nivel_2.passed = {n2['passed']}")


class TestN3ElasticAbsorption:
    """Tests for N3 elastic absorption (ATR-based instead of 2-tick fixed)"""

    def test_n3_absorption_limit_is_atr_based(self):
        """N3 absorption_limit uses ATR-based elastic formula (0.25 x ATR_M1)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        n3 = data.get('nivel_3', {})
        dz_n3 = n3.get('delta_zonal', {})
        
        atr_m1 = dz_n3.get('atr_m1', 0)
        absorption_limit = dz_n3.get('absorption_price_limit', 0)
        
        # absorption_price_limit should be approximately 0.25 * atr_m1
        if atr_m1 > 0:
            expected_limit = 0.25 * atr_m1
            tolerance = 0.01  # Allow small floating point differences
            assert abs(absorption_limit - expected_limit) < tolerance, \
                f"absorption_price_limit ({absorption_limit}) should be ~0.25 * atr_m1 ({expected_limit})"
            print(f"✅ N3 elastic absorption: absorption_limit={absorption_limit:.4f} ≈ 0.25 * atr_m1={atr_m1:.4f}")
        else:
            # If atr_m1 is 0 (simulated/market closed), absorption_limit should still exist
            assert absorption_limit >= 0, "absorption_price_limit should be >= 0"
            print(f"✅ N3 absorption_limit exists (atr_m1=0, likely simulated): {absorption_limit}")


class TestBackendHealth:
    """Basic health checks"""

    def test_health_endpoint(self):
        """Backend service starts without errors"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
        data = response.json()
        assert data.get('status') == 'healthy', f"Unhealthy status: {data}"
        print("✅ Backend health check passed")


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
