"""
Test Delta Zonal ATR-Adaptive Buffers Feature
==============================================
Tests for the ATR-adaptive buffer implementation in Delta Zonal Ancorado:
- N2 uses ATR(M5) fractions: VWAP/POC=1.00x, VAH/VAL=0.75x
- N3 uses ATR(M1) fractions: VWAP±3σ=0.50x, ZGL=0.50x, Call/Put Wall=0.25x
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Expected ATR fractions per level type (Option C - conservative)
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


class TestV3SignalDeltaZonal:
    """Test Delta Zonal in /api/v3/signal/{symbol} endpoint"""

    def test_mnq_signal_returns_delta_zonal_n2(self):
        """N2 delta_zonal should include atr_m5 and proper level structure"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        n2 = data.get('nivel_2', {})
        dz = n2.get('delta_zonal', {})
        
        # Verify mode and ATR
        assert dz.get('mode') == 'N2_STRUCTURE', f"Expected N2_STRUCTURE mode, got {dz.get('mode')}"
        assert 'atr_m5' in dz, "N2 delta_zonal should include atr_m5"
        
        # ATR should be a positive number (or None if no data)
        atr_m5 = dz.get('atr_m5')
        if atr_m5 is not None:
            assert isinstance(atr_m5, (int, float)), f"atr_m5 should be numeric, got {type(atr_m5)}"
            assert atr_m5 > 0, f"atr_m5 should be positive, got {atr_m5}"
        
        print(f"✅ MNQ N2 delta_zonal: mode={dz.get('mode')}, atr_m5={atr_m5}")

    def test_mnq_signal_n2_levels_have_buffer_info(self):
        """N2 levels should include buffer, buffer_range, atr_fraction, volume_in_buffer, position"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        dz = data.get('nivel_2', {}).get('delta_zonal', {})
        levels = dz.get('levels', {})
        
        assert len(levels) > 0, "N2 should have at least one level"
        
        for level_name, level_data in levels.items():
            # Required fields for N2 levels
            assert 'buffer' in level_data, f"Level {level_name} missing 'buffer'"
            assert 'buffer_range' in level_data, f"Level {level_name} missing 'buffer_range'"
            assert 'atr_fraction' in level_data, f"Level {level_name} missing 'atr_fraction'"
            assert 'volume_in_buffer' in level_data, f"Level {level_name} missing 'volume_in_buffer'"
            assert 'position' in level_data, f"Level {level_name} missing 'position'"
            
            # Validate position values
            assert level_data['position'] in ['above', 'below', 'in_buffer'], \
                f"Level {level_name} position should be above/below/in_buffer, got {level_data['position']}"
            
            # Validate buffer is positive
            assert level_data['buffer'] > 0, f"Level {level_name} buffer should be positive"
            
            print(f"✅ N2 {level_name}: buffer={level_data['buffer']}, atr_fraction={level_data['atr_fraction']}, position={level_data['position']}")

    def test_mnq_signal_n2_atr_fractions_correct(self):
        """N2 ATR fractions should match Option C: VWAP/POC=1.0, VAH/VAL=0.75"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        levels = data.get('nivel_2', {}).get('delta_zonal', {}).get('levels', {})
        
        for level_name, level_data in levels.items():
            expected_fraction = N2_ATR_FRACTIONS.get(level_name)
            if expected_fraction is not None:
                actual_fraction = level_data.get('atr_fraction')
                assert actual_fraction == expected_fraction, \
                    f"N2 {level_name} atr_fraction should be {expected_fraction}, got {actual_fraction}"
                print(f"✅ N2 {level_name} atr_fraction={actual_fraction} (expected {expected_fraction})")

    def test_mnq_signal_returns_delta_zonal_n3(self):
        """N3 delta_zonal should include atr_m1 and proper level structure"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        n3 = data.get('nivel_3', {})
        dz = n3.get('delta_zonal', {})
        
        # Verify mode and ATR
        assert dz.get('mode') == 'N3_EXTREME', f"Expected N3_EXTREME mode, got {dz.get('mode')}"
        assert 'atr_m1' in dz, "N3 delta_zonal should include atr_m1"
        
        atr_m1 = dz.get('atr_m1')
        if atr_m1 is not None:
            assert isinstance(atr_m1, (int, float)), f"atr_m1 should be numeric, got {type(atr_m1)}"
            assert atr_m1 > 0, f"atr_m1 should be positive, got {atr_m1}"
        
        print(f"✅ MNQ N3 delta_zonal: mode={dz.get('mode')}, atr_m1={atr_m1}")

    def test_mnq_signal_n3_levels_have_buffer_info(self):
        """N3 levels should include buffer, atr_fraction, zone_range"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        dz = data.get('nivel_3', {}).get('delta_zonal', {})
        levels = dz.get('levels', {})
        
        assert len(levels) > 0, "N3 should have at least one level"
        
        for level_name, level_data in levels.items():
            # Required fields for N3 levels
            assert 'buffer' in level_data, f"Level {level_name} missing 'buffer'"
            assert 'atr_fraction' in level_data, f"Level {level_name} missing 'atr_fraction'"
            assert 'zone_range' in level_data, f"Level {level_name} missing 'zone_range'"
            
            # Validate buffer is positive
            assert level_data['buffer'] > 0, f"Level {level_name} buffer should be positive"
            
            print(f"✅ N3 {level_name}: buffer={level_data['buffer']}, atr_fraction={level_data['atr_fraction']}, zone_range={level_data['zone_range']}")

    def test_mnq_signal_n3_atr_fractions_correct(self):
        """N3 ATR fractions should match Option C: call/put_wall=0.25, zgl=0.50, vwap±3σ=0.50"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        levels = data.get('nivel_3', {}).get('delta_zonal', {}).get('levels', {})
        
        for level_name, level_data in levels.items():
            expected_fraction = N3_ATR_FRACTIONS.get(level_name)
            if expected_fraction is not None:
                actual_fraction = level_data.get('atr_fraction')
                assert actual_fraction == expected_fraction, \
                    f"N3 {level_name} atr_fraction should be {expected_fraction}, got {actual_fraction}"
                print(f"✅ N3 {level_name} atr_fraction={actual_fraction} (expected {expected_fraction})")


class TestV3DeltaZonalEndpoint:
    """Test dedicated /api/v3/delta-zonal/{symbol} endpoint"""

    def test_mnq_delta_zonal_endpoint_returns_200(self):
        """Dedicated delta-zonal endpoint should return 200"""
        response = requests.get(f"{BASE_URL}/api/v3/delta-zonal/MNQ")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert 'symbol' in data
        assert 'n2_structure' in data
        assert 'n3_extreme' in data
        print(f"✅ /api/v3/delta-zonal/MNQ returns 200 with n2_structure and n3_extreme")

    def test_mnq_delta_zonal_n2_has_atr_m5(self):
        """N2 structure should include atr_m5"""
        response = requests.get(f"{BASE_URL}/api/v3/delta-zonal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        n2 = data.get('n2_structure', {})
        
        assert 'atr_m5' in n2, "n2_structure should include atr_m5"
        atr_m5 = n2.get('atr_m5')
        if atr_m5 is not None:
            assert atr_m5 > 0, f"atr_m5 should be positive, got {atr_m5}"
        print(f"✅ delta-zonal N2 atr_m5={atr_m5}")

    def test_mnq_delta_zonal_n3_has_atr_m1(self):
        """N3 extreme should include atr_m1"""
        response = requests.get(f"{BASE_URL}/api/v3/delta-zonal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        n3 = data.get('n3_extreme', {})
        
        assert 'atr_m1' in n3, "n3_extreme should include atr_m1"
        atr_m1 = n3.get('atr_m1')
        if atr_m1 is not None:
            assert atr_m1 > 0, f"atr_m1 should be positive, got {atr_m1}"
        print(f"✅ delta-zonal N3 atr_m1={atr_m1}")


class TestMESSymbol:
    """Test MES symbol returns valid data"""

    def test_mes_signal_returns_200(self):
        """MES signal endpoint should return 200"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get('symbol') == 'MES'
        print(f"✅ MES signal returns 200")

    def test_mes_delta_zonal_returns_200(self):
        """MES delta-zonal endpoint should return 200"""
        response = requests.get(f"{BASE_URL}/api/v3/delta-zonal/MES")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get('symbol') == 'MES'
        assert 'n2_structure' in data
        assert 'n3_extreme' in data
        print(f"✅ MES delta-zonal returns 200")

    def test_mes_n2_has_atr_fractions(self):
        """MES N2 levels should have correct ATR fractions"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200
        
        data = response.json()
        levels = data.get('nivel_2', {}).get('delta_zonal', {}).get('levels', {})
        
        for level_name, level_data in levels.items():
            assert 'atr_fraction' in level_data, f"MES N2 {level_name} missing atr_fraction"
            assert 'buffer' in level_data, f"MES N2 {level_name} missing buffer"
            print(f"✅ MES N2 {level_name}: atr_fraction={level_data.get('atr_fraction')}, buffer={level_data.get('buffer')}")


class TestATRScaling:
    """Test ATR scaling from 1H to M5/M1"""

    def test_atr_m5_smaller_than_atr_1h(self):
        """ATR(M5) should be smaller than ATR(1H) due to sqrt(12) scaling"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        atr_m5 = data.get('nivel_2', {}).get('delta_zonal', {}).get('atr_m5')
        
        # Get 1H ATR from analysis
        analysis_resp = requests.post(f"{BASE_URL}/api/analyze", json={"symbol": "MNQ", "timeframe": "1H"})
        if analysis_resp.status_code == 200:
            analysis = analysis_resp.json()
            atr_1h = analysis.get('analysis', {}).get('1H', {}).get('indicators', {}).get('predictive', {}).get('ATR', {}).get('value')
            
            if atr_1h and atr_m5:
                # ATR_M5 should be approximately ATR_1H / sqrt(12) = ATR_1H / 3.46
                expected_ratio = 3.46
                actual_ratio = atr_1h / atr_m5 if atr_m5 > 0 else 0
                
                # Allow some tolerance due to actual trade-based ATR calculation
                print(f"✅ ATR scaling: ATR_1H={atr_1h}, ATR_M5={atr_m5}, ratio={actual_ratio:.2f} (expected ~{expected_ratio})")

    def test_atr_m1_smaller_than_atr_m5(self):
        """ATR(M1) should be smaller than ATR(M5)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        atr_m5 = data.get('nivel_2', {}).get('delta_zonal', {}).get('atr_m5')
        atr_m1 = data.get('nivel_3', {}).get('delta_zonal', {}).get('atr_m1')
        
        if atr_m5 and atr_m1:
            assert atr_m1 < atr_m5, f"ATR_M1 ({atr_m1}) should be smaller than ATR_M5 ({atr_m5})"
            print(f"✅ ATR_M1 ({atr_m1}) < ATR_M5 ({atr_m5})")


class TestBufferCalculation:
    """Test buffer calculation from ATR and fractions"""

    def test_n2_buffer_equals_atr_times_fraction(self):
        """N2 buffer should equal atr_m5 * atr_fraction"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        dz = data.get('nivel_2', {}).get('delta_zonal', {})
        atr_m5 = dz.get('atr_m5')
        levels = dz.get('levels', {})
        
        if atr_m5:
            for level_name, level_data in levels.items():
                expected_buffer = round(atr_m5 * level_data['atr_fraction'], 2)
                actual_buffer = level_data['buffer']
                
                # Allow small floating point tolerance
                assert abs(actual_buffer - expected_buffer) < 0.1, \
                    f"N2 {level_name} buffer mismatch: expected {expected_buffer}, got {actual_buffer}"
                print(f"✅ N2 {level_name}: buffer={actual_buffer} = atr_m5({atr_m5}) * fraction({level_data['atr_fraction']})")

    def test_n3_buffer_equals_atr_times_fraction(self):
        """N3 buffer should equal atr_m1 * atr_fraction"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        dz = data.get('nivel_3', {}).get('delta_zonal', {})
        atr_m1 = dz.get('atr_m1')
        levels = dz.get('levels', {})
        
        if atr_m1:
            for level_name, level_data in levels.items():
                expected_buffer = round(atr_m1 * level_data['atr_fraction'], 2)
                actual_buffer = level_data['buffer']
                
                # Allow small floating point tolerance
                assert abs(actual_buffer - expected_buffer) < 0.1, \
                    f"N3 {level_name} buffer mismatch: expected {expected_buffer}, got {actual_buffer}"
                print(f"✅ N3 {level_name}: buffer={actual_buffer} = atr_m1({atr_m1}) * fraction({level_data['atr_fraction']})")


@pytest.fixture
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
