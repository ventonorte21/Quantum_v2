"""
Test VWAP (Volume Weighted Average Price) Feature - Iteration 14
Tests:
- GET /api/vwap/{symbol}?timeframe=1H - VWAP endpoint with bands
- POST /api/analyze - includes vwap field
- Regression: OFI and Treasury endpoints still working
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestVWAPEndpoint:
    """Test VWAP endpoint /api/vwap/{symbol}"""
    
    def test_vwap_mnq_returns_200(self):
        """GET /api/vwap/MNQ?timeframe=1H should return 200"""
        response = requests.get(f"{BASE_URL}/api/vwap/MNQ?timeframe=1H", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print(f"✅ GET /api/vwap/MNQ returns 200")
    
    def test_vwap_mnq_has_required_fields(self):
        """VWAP response should have vwap, upper_1, lower_1, upper_2, lower_2, std, position, distance_pct, distance_std, signal, interpretation"""
        response = requests.get(f"{BASE_URL}/api/vwap/MNQ?timeframe=1H", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        required_fields = ['vwap', 'upper_1', 'lower_1', 'upper_2', 'lower_2', 'std', 
                          'position', 'distance_pct', 'distance_std', 'signal', 'interpretation']
        
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
            print(f"  ✓ Field '{field}' present: {data[field]}")
        
        print(f"✅ VWAP response has all required fields")
    
    def test_vwap_mnq_values_valid(self):
        """VWAP values should be valid numbers"""
        response = requests.get(f"{BASE_URL}/api/vwap/MNQ?timeframe=1H", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        # VWAP should be a positive number
        assert isinstance(data['vwap'], (int, float)), f"vwap should be numeric, got {type(data['vwap'])}"
        assert data['vwap'] > 0, f"vwap should be positive, got {data['vwap']}"
        
        # Bands should be ordered: lower_2 < lower_1 < vwap < upper_1 < upper_2
        if data['vwap'] > 0 and data['std'] > 0:
            assert data['lower_2'] < data['lower_1'] < data['vwap'] < data['upper_1'] < data['upper_2'], \
                f"Bands not properly ordered: {data['lower_2']} < {data['lower_1']} < {data['vwap']} < {data['upper_1']} < {data['upper_2']}"
        
        print(f"✅ VWAP values valid: vwap={data['vwap']}, std={data['std']}")
    
    def test_vwap_position_valid(self):
        """Position should be one of the valid values"""
        response = requests.get(f"{BASE_URL}/api/vwap/MNQ?timeframe=1H", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        valid_positions = ['ABOVE_VWAP', 'BELOW_VWAP', 'ABOVE_1STD', 'BELOW_1STD', 'ABOVE_2STD', 'BELOW_2STD']
        assert data['position'] in valid_positions, f"Invalid position: {data['position']}, expected one of {valid_positions}"
        
        print(f"✅ VWAP position valid: {data['position']}")
    
    def test_vwap_signal_valid(self):
        """Signal should be CONFIRM or CAUTION"""
        response = requests.get(f"{BASE_URL}/api/vwap/MNQ?timeframe=1H", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        valid_signals = ['CONFIRM', 'CAUTION']
        assert data['signal'] in valid_signals, f"Invalid signal: {data['signal']}, expected one of {valid_signals}"
        
        print(f"✅ VWAP signal valid: {data['signal']}")
    
    def test_vwap_mes_returns_200(self):
        """GET /api/vwap/MES?timeframe=1H should return 200"""
        response = requests.get(f"{BASE_URL}/api/vwap/MES?timeframe=1H", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert 'vwap' in data
        print(f"✅ GET /api/vwap/MES returns 200 with vwap={data['vwap']}")
    
    def test_vwap_invalid_symbol_returns_404(self):
        """GET /api/vwap/INVALID should return 404"""
        response = requests.get(f"{BASE_URL}/api/vwap/INVALID?timeframe=1H", timeout=30)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print(f"✅ GET /api/vwap/INVALID returns 404")


class TestAnalyzeIncludesVWAP:
    """Test that POST /api/analyze includes vwap field"""
    
    def test_analyze_includes_vwap(self):
        """POST /api/analyze should include vwap field"""
        response = requests.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        }, timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert 'vwap' in data, "Missing 'vwap' field in analyze response"
        print(f"✅ POST /api/analyze includes 'vwap' field")
    
    def test_analyze_vwap_has_all_fields(self):
        """VWAP in analyze response should have all required fields"""
        response = requests.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        }, timeout=60)
        assert response.status_code == 200
        data = response.json()
        
        vwap = data.get('vwap')
        assert vwap is not None, "vwap field is None"
        
        # Check if vwap has data (may be empty if no candles)
        if vwap.get('vwap', 0) > 0:
            required_fields = ['vwap', 'upper_1', 'lower_1', 'upper_2', 'lower_2', 
                              'position', 'distance_pct', 'distance_std', 'signal', 'interpretation']
            for field in required_fields:
                assert field in vwap, f"Missing field in vwap: {field}"
            print(f"✅ VWAP in analyze has all required fields")
            print(f"   vwap={vwap['vwap']}, position={vwap['position']}, signal={vwap['signal']}")
        else:
            print(f"⚠️ VWAP data empty (possibly weekend/no candles): {vwap}")


class TestRegressionOFI:
    """Regression tests for OFI endpoint"""
    
    def test_ofi_mnq_returns_200(self):
        """GET /api/ofi/MNQ should return 200"""
        response = requests.get(f"{BASE_URL}/api/ofi/MNQ", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # OFI endpoint returns nested structure: {symbol, ofi: {...}}
        ofi = data.get('ofi', data)  # Handle both nested and flat response
        
        assert 'ofi_fast' in ofi, f"Missing ofi_fast in {ofi.keys()}"
        assert 'ofi_slow' in ofi, f"Missing ofi_slow in {ofi.keys()}"
        assert 'signal' in ofi, f"Missing signal in {ofi.keys()}"
        
        print(f"✅ OFI regression: /api/ofi/MNQ returns 200 with ofi_fast={ofi['ofi_fast']}, ofi_slow={ofi['ofi_slow']}")


class TestRegressionTreasury:
    """Regression tests for Treasury endpoint"""
    
    def test_treasury_returns_200(self):
        """GET /api/treasury should return 200"""
        response = requests.get(f"{BASE_URL}/api/treasury", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Treasury endpoint returns nested structure: {treasury: {...}}
        treasury = data.get('treasury', data)  # Handle both nested and flat response
        
        assert 'us2y' in treasury, f"Missing us2y in {treasury.keys()}"
        assert 'us10y' in treasury, f"Missing us10y in {treasury.keys()}"
        assert 'spread_bps' in treasury, f"Missing spread_bps in {treasury.keys()}"
        assert 'curve_state' in treasury, f"Missing curve_state in {treasury.keys()}"
        
        print(f"✅ Treasury regression: /api/treasury returns 200 with us2y={treasury['us2y']}, us10y={treasury['us10y']}, spread_bps={treasury['spread_bps']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
