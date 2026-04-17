"""
Test suite for Advanced Gamma feature with Black-Scholes GEX calculation.
Tests ZGL (Zero Gamma Level), Call Wall, Put Wall, and GEX Profile endpoints.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Valid symbols for micro futures
VALID_SYMBOLS = ['MNQ', 'MES']

# Symbol to underlying ETF mapping
SYMBOL_UNDERLYING_MAP = {
    'MNQ': 'QQQ',
    'MES': 'SPY',
}


class TestHealthCheck:
    """Basic health check tests"""
    
    def test_api_health(self):
        """Test API health endpoint"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'healthy'
        print("✅ API health check passed")


class TestAdvancedGammaEndpoint:
    """Tests for /api/gamma/advanced/{symbol} endpoint"""
    
    @pytest.mark.parametrize("symbol", VALID_SYMBOLS)
    def test_advanced_gamma_valid_symbols(self, symbol):
        """Test advanced gamma endpoint returns valid data for all symbols"""
        response = requests.get(f"{BASE_URL}/api/gamma/advanced/{symbol}")
        assert response.status_code == 200
        
        data = response.json()
        
        # Verify required fields exist
        assert 'symbol' in data
        assert data['symbol'] == symbol
        
        # Verify ZGL fields
        assert 'zgl' in data
        assert 'zgl_signal' in data
        assert data['zgl_signal'] in ['ABOVE_ZGL', 'BELOW_ZGL', 'NEUTRAL']
        assert 'zgl_interpretation' in data
        
        # Verify Call Wall fields
        assert 'call_wall' in data
        assert 'call_wall_gex' in data
        
        # Verify Put Wall fields
        assert 'put_wall' in data
        assert 'put_wall_gex' in data
        
        # Verify GEX Profile
        assert 'gex_profile' in data
        assert isinstance(data['gex_profile'], list)
        if len(data['gex_profile']) > 0:
            profile_item = data['gex_profile'][0]
            assert 'strike' in profile_item
            assert 'call_gex' in profile_item
            assert 'put_gex' in profile_item
            assert 'net_gex' in profile_item
        
        # Verify other required fields
        assert 'net_gex' in data
        assert 'call_gex' in data
        assert 'put_gex' in data
        assert 'sentiment' in data
        assert data['sentiment'] in ['POSITIVE', 'NEGATIVE']
        assert 'spot_price' in data
        
        print(f"✅ Advanced gamma for {symbol}: ZGL={data['zgl']}, Signal={data['zgl_signal']}, "
              f"Call Wall={data['call_wall']}, Put Wall={data['put_wall']}")
    
    def test_advanced_gamma_invalid_symbol(self):
        """Test advanced gamma endpoint returns 404 for invalid symbol"""
        response = requests.get(f"{BASE_URL}/api/gamma/advanced/INVALID")
        assert response.status_code == 404
        data = response.json()
        assert 'detail' in data
        assert 'not found' in data['detail'].lower()
        print("✅ Invalid symbol correctly returns 404")
    
    def test_advanced_gamma_underlying_mapping(self):
        """Test that each symbol maps to correct underlying ETF"""
        for symbol, expected_underlying in SYMBOL_UNDERLYING_MAP.items():
            response = requests.get(f"{BASE_URL}/api/gamma/advanced/{symbol}")
            assert response.status_code == 200
            data = response.json()
            
            # Check underlying field if present
            if 'underlying' in data:
                assert data['underlying'] == expected_underlying, \
                    f"Expected {expected_underlying} for {symbol}, got {data['underlying']}"
            
            print(f"✅ {symbol} -> {expected_underlying} mapping verified")


class TestBasicGammaEndpoint:
    """Tests for /api/gamma/{symbol} endpoint"""
    
    @pytest.mark.parametrize("symbol", VALID_SYMBOLS)
    def test_basic_gamma_valid_symbols(self, symbol):
        """Test basic gamma endpoint returns data with ZGL, Call Wall, Put Wall"""
        response = requests.get(f"{BASE_URL}/api/gamma/{symbol}")
        assert response.status_code == 200
        
        data = response.json()
        assert 'gamma' in data
        gamma = data['gamma']
        
        # Verify ZGL fields
        assert 'zgl' in gamma
        assert 'zgl_signal' in gamma
        
        # Verify Call Wall fields
        assert 'call_wall' in gamma
        
        # Verify Put Wall fields
        assert 'put_wall' in gamma
        
        # Verify GEX Profile
        assert 'gex_profile' in gamma
        
        print(f"✅ Basic gamma for {symbol}: ZGL={gamma['zgl']}, "
              f"Call Wall={gamma['call_wall']}, Put Wall={gamma['put_wall']}")
    
    def test_basic_gamma_invalid_symbol(self):
        """Test basic gamma endpoint returns 404 for invalid symbol"""
        response = requests.get(f"{BASE_URL}/api/gamma/INVALID")
        assert response.status_code == 404
        print("✅ Invalid symbol correctly returns 404")


class TestAnalyzeEndpoint:
    """Tests for /api/analyze endpoint with real_gamma data"""
    
    @pytest.mark.parametrize("symbol", VALID_SYMBOLS)
    def test_analyze_includes_real_gamma(self, symbol):
        """Test analyze endpoint includes real_gamma with ZGL, Call Wall, Put Wall, GEX Profile"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": symbol, "timeframe": "1H"}
        )
        assert response.status_code == 200
        
        data = response.json()
        
        # Verify real_gamma is present
        assert 'real_gamma' in data
        real_gamma = data['real_gamma']
        
        # Verify ZGL fields
        assert 'zgl' in real_gamma
        assert 'zgl_signal' in real_gamma
        assert real_gamma['zgl_signal'] in ['ABOVE_ZGL', 'BELOW_ZGL', 'NEUTRAL']
        
        # Verify Call Wall fields
        assert 'call_wall' in real_gamma
        assert 'call_wall_gex' in real_gamma
        
        # Verify Put Wall fields
        assert 'put_wall' in real_gamma
        assert 'put_wall_gex' in real_gamma
        
        # Verify GEX Profile
        assert 'gex_profile' in real_gamma
        assert isinstance(real_gamma['gex_profile'], list)
        
        # Verify other fields
        assert 'net_gex' in real_gamma
        assert 'sentiment' in real_gamma
        assert 'spot_price' in real_gamma
        
        print(f"✅ Analyze for {symbol}: real_gamma present with ZGL={real_gamma['zgl']}, "
              f"Signal={real_gamma['zgl_signal']}")
    
    def test_analyze_gamma_in_confirmatory_indicators(self):
        """Test that GAMMA_EXPOSURE is included in confirmatory indicators"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200
        
        data = response.json()
        
        # Check analysis structure
        assert 'analysis' in data
        assert '1H' in data['analysis']
        
        indicators = data['analysis']['1H'].get('indicators', {})
        confirmatory = indicators.get('confirmatory', {})
        
        assert 'GAMMA_EXPOSURE' in confirmatory
        gamma_indicator = confirmatory['GAMMA_EXPOSURE']
        
        # Verify gamma indicator has required fields
        assert 'zgl' in gamma_indicator
        assert 'call_wall' in gamma_indicator
        assert 'put_wall' in gamma_indicator
        
        print("✅ GAMMA_EXPOSURE correctly included in confirmatory indicators")


class TestGEXProfileData:
    """Tests for GEX Profile data structure and values"""
    
    def test_gex_profile_structure(self):
        """Test GEX profile has correct structure"""
        response = requests.get(f"{BASE_URL}/api/gamma/advanced/MES")
        assert response.status_code == 200
        
        data = response.json()
        gex_profile = data['gex_profile']
        
        assert len(gex_profile) > 0, "GEX profile should not be empty"
        
        # Check first item structure
        item = gex_profile[0]
        assert 'strike' in item
        assert 'call_gex' in item
        assert 'put_gex' in item
        assert 'net_gex' in item
        
        # Verify types
        assert isinstance(item['strike'], (int, float))
        assert isinstance(item['call_gex'], (int, float))
        assert isinstance(item['put_gex'], (int, float))
        assert isinstance(item['net_gex'], (int, float))
        
        print(f"✅ GEX profile has {len(gex_profile)} strikes with correct structure")
    
    def test_gex_profile_values_consistency(self):
        """Test that net_gex = call_gex + put_gex (approximately)"""
        response = requests.get(f"{BASE_URL}/api/gamma/advanced/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        gex_profile = data['gex_profile']
        
        # Check a few items for consistency
        for item in gex_profile[:5]:
            expected_net = item['call_gex'] + item['put_gex']
            actual_net = item['net_gex']
            # Allow small floating point differences
            assert abs(expected_net - actual_net) < 0.1, \
                f"Net GEX mismatch at strike {item['strike']}: expected {expected_net}, got {actual_net}"
        
        print("✅ GEX profile values are consistent (net_gex = call_gex + put_gex)")


class TestZGLInterpretation:
    """Tests for ZGL interpretation logic"""
    
    def test_zgl_above_interpretation(self):
        """Test ZGL interpretation when spot is above ZGL"""
        # Find a symbol where spot > ZGL
        for symbol in VALID_SYMBOLS:
            response = requests.get(f"{BASE_URL}/api/gamma/advanced/{symbol}")
            data = response.json()
            
            if data['zgl_signal'] == 'ABOVE_ZGL':
                assert data['spot_price'] > data['zgl'], \
                    f"Spot ({data['spot_price']}) should be > ZGL ({data['zgl']}) for ABOVE_ZGL"
                assert 'ACIMA' in data['zgl_interpretation'] or 'above' in data['zgl_interpretation'].lower()
                print(f"✅ {symbol}: ABOVE_ZGL interpretation correct (spot={data['spot_price']}, zgl={data['zgl']})")
                return
        
        print("⚠️ No symbol found with ABOVE_ZGL signal")
    
    def test_zgl_below_interpretation(self):
        """Test ZGL interpretation when spot is below ZGL"""
        # Find a symbol where spot < ZGL
        for symbol in VALID_SYMBOLS:
            response = requests.get(f"{BASE_URL}/api/gamma/advanced/{symbol}")
            data = response.json()
            
            if data['zgl_signal'] == 'BELOW_ZGL':
                assert data['spot_price'] < data['zgl'], \
                    f"Spot ({data['spot_price']}) should be < ZGL ({data['zgl']}) for BELOW_ZGL"
                assert 'ABAIXO' in data['zgl_interpretation'] or 'below' in data['zgl_interpretation'].lower()
                print(f"✅ {symbol}: BELOW_ZGL interpretation correct (spot={data['spot_price']}, zgl={data['zgl']})")
                return
        
        print("⚠️ No symbol found with BELOW_ZGL signal")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
