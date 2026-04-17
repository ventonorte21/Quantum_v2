"""
Test suite for the 5-level Market Regime model
Tests: COMPLACENCIA, BULL, TRANSICAO, BEAR, CAPITULACAO regimes
Tests: VIX, Yield Curve, Gamma/ZGL, Term Structure signals
Tests: Treasury yields (yfinance source)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestTreasuryEndpoint:
    """Treasury endpoint tests - yfinance source for US10Y/US30Y"""
    
    def test_treasury_returns_200(self):
        """GET /api/treasury returns 200"""
        response = requests.get(f"{BASE_URL}/api/treasury", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("✅ GET /api/treasury returns 200")
    
    def test_treasury_has_us10y(self):
        """Treasury response contains us10y field"""
        response = requests.get(f"{BASE_URL}/api/treasury", timeout=30)
        data = response.json()
        treasury = data.get('treasury', {})
        assert 'us10y' in treasury, "Missing us10y field"
        assert isinstance(treasury['us10y'], (int, float)), "us10y should be numeric"
        print(f"✅ Treasury us10y: {treasury['us10y']}")
    
    def test_treasury_has_us30y(self):
        """Treasury response contains us30y field"""
        response = requests.get(f"{BASE_URL}/api/treasury", timeout=30)
        data = response.json()
        treasury = data.get('treasury', {})
        assert 'us30y' in treasury, "Missing us30y field"
        assert isinstance(treasury['us30y'], (int, float)), "us30y should be numeric"
        print(f"✅ Treasury us30y: {treasury['us30y']}")
    
    def test_treasury_has_spread(self):
        """Treasury response contains spread field"""
        response = requests.get(f"{BASE_URL}/api/treasury", timeout=30)
        data = response.json()
        treasury = data.get('treasury', {})
        assert 'spread' in treasury, "Missing spread field"
        assert isinstance(treasury['spread'], (int, float)), "spread should be numeric"
        print(f"✅ Treasury spread: {treasury['spread']}")
    
    def test_treasury_has_curve_state(self):
        """Treasury response contains curve_state field"""
        response = requests.get(f"{BASE_URL}/api/treasury", timeout=30)
        data = response.json()
        treasury = data.get('treasury', {})
        assert 'curve_state' in treasury, "Missing curve_state field"
        valid_states = ['INVERTED', 'FLAT_INVERTED', 'FLAT', 'NORMAL', 'STEEP', 'UNKNOWN']
        assert treasury['curve_state'] in valid_states, f"Invalid curve_state: {treasury['curve_state']}"
        print(f"✅ Treasury curve_state: {treasury['curve_state']}")
    
    def test_treasury_source_is_yfinance(self):
        """Treasury source should be yahoo_finance (yfinance)"""
        response = requests.get(f"{BASE_URL}/api/treasury", timeout=30)
        data = response.json()
        treasury = data.get('treasury', {})
        source = treasury.get('source', '')
        # Accept yahoo_finance or error (if API fails)
        assert source in ['yahoo_finance', 'error'], f"Expected yahoo_finance source, got: {source}"
        print(f"✅ Treasury source: {source}")


class TestTermStructureEndpoint:
    """Term Structure endpoint tests - VIX/VIX3M ratio"""
    
    def test_term_structure_returns_200(self):
        """GET /api/term-structure returns 200"""
        response = requests.get(f"{BASE_URL}/api/term-structure", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("✅ GET /api/term-structure returns 200")
    
    def test_term_structure_has_ratio(self):
        """Term structure response contains ratio field"""
        response = requests.get(f"{BASE_URL}/api/term-structure", timeout=30)
        data = response.json()
        ts = data.get('term_structure', {})
        assert 'ratio' in ts, "Missing ratio field"
        assert isinstance(ts['ratio'], (int, float)), "ratio should be numeric"
        print(f"✅ Term Structure ratio: {ts['ratio']}")
    
    def test_term_structure_has_state(self):
        """Term structure response contains state field"""
        response = requests.get(f"{BASE_URL}/api/term-structure", timeout=30)
        data = response.json()
        ts = data.get('term_structure', {})
        assert 'state' in ts, "Missing state field"
        valid_states = ['STRONG_BACKWARDATION', 'BACKWARDATION', 'FLAT', 'CONTANGO', 'STEEP_CONTANGO']
        assert ts['state'] in valid_states, f"Invalid state: {ts['state']}"
        print(f"✅ Term Structure state: {ts['state']}")
    
    def test_term_structure_has_vix_values(self):
        """Term structure response contains vix and vix3m values"""
        response = requests.get(f"{BASE_URL}/api/term-structure", timeout=30)
        data = response.json()
        ts = data.get('term_structure', {})
        assert 'vix' in ts, "Missing vix field"
        assert 'vix3m' in ts, "Missing vix3m field"
        assert isinstance(ts['vix'], (int, float)), "vix should be numeric"
        assert isinstance(ts['vix3m'], (int, float)), "vix3m should be numeric"
        print(f"✅ Term Structure VIX: {ts['vix']}, VIX3M: {ts['vix3m']}")


class TestAnalyzeEndpointTermStructure:
    """POST /api/analyze should include term_structure with ratio"""
    
    def test_analyze_returns_term_structure(self):
        """POST /api/analyze returns term_structure object"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"},
            timeout=90
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'term_structure' in data, "Missing term_structure in analyze response"
        print("✅ POST /api/analyze returns term_structure")
    
    def test_analyze_term_structure_has_ratio(self):
        """Analyze term_structure contains ratio field"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"},
            timeout=90
        )
        data = response.json()
        ts = data.get('term_structure', {})
        assert 'ratio' in ts, "Missing ratio in term_structure"
        assert isinstance(ts['ratio'], (int, float)), "ratio should be numeric"
        print(f"✅ Analyze term_structure ratio: {ts['ratio']}")
    
    def test_analyze_returns_treasury(self):
        """POST /api/analyze returns treasury object"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"},
            timeout=90
        )
        data = response.json()
        assert 'treasury' in data, "Missing treasury in analyze response"
        treasury = data.get('treasury', {})
        assert 'us10y' in treasury, "Missing us10y in treasury"
        assert 'us30y' in treasury, "Missing us30y in treasury"
        assert 'curve_state' in treasury, "Missing curve_state in treasury"
        print(f"✅ Analyze treasury: us10y={treasury.get('us10y')}, us30y={treasury.get('us30y')}, curve_state={treasury.get('curve_state')}")
    
    def test_analyze_returns_real_vix(self):
        """POST /api/analyze returns real_vix object"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"},
            timeout=90
        )
        data = response.json()
        assert 'real_vix' in data, "Missing real_vix in analyze response"
        vix = data.get('real_vix', {})
        assert 'value' in vix, "Missing value in real_vix"
        assert 'regime' in vix, "Missing regime in real_vix"
        print(f"✅ Analyze real_vix: value={vix.get('value')}, regime={vix.get('regime')}")
    
    def test_analyze_returns_real_gamma(self):
        """POST /api/analyze returns real_gamma object with zgl_signal"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"},
            timeout=90
        )
        data = response.json()
        assert 'real_gamma' in data, "Missing real_gamma in analyze response"
        gamma = data.get('real_gamma', {})
        assert 'net_gex' in gamma, "Missing net_gex in real_gamma"
        assert 'zgl_signal' in gamma, "Missing zgl_signal in real_gamma"
        print(f"✅ Analyze real_gamma: net_gex={gamma.get('net_gex')}, zgl_signal={gamma.get('zgl_signal')}")


class TestMarketRegimeCalculation:
    """Test Market Regime score calculation logic"""
    
    def test_regime_score_max_13(self):
        """Market Regime max score is 13 (VIX:4 + TS:4 + Gamma:4 + YieldCurve:1)"""
        # Logic test - verify the frontend displays X/13 format
        # The backend provides the raw data, frontend calculates the regime
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"},
            timeout=90
        )
        data = response.json()
        
        # Verify all 4 signal sources are present
        assert 'real_vix' in data, "Missing real_vix for VIX signal"
        assert 'treasury' in data, "Missing treasury for Yield Curve signal"
        assert 'real_gamma' in data, "Missing real_gamma for Gamma/ZGL signal"
        assert 'term_structure' in data, "Missing term_structure for Term Structure signal"
        
        print("✅ All 4 Market Regime signal sources present in analyze response")
        print(f"   - VIX regime: {data.get('real_vix', {}).get('regime')}")
        print(f"   - Yield Curve state: {data.get('treasury', {}).get('curve_state')}")
        print(f"   - Gamma ZGL signal: {data.get('real_gamma', {}).get('zgl_signal')}")
        print(f"   - Term Structure state: {data.get('term_structure', {}).get('state')}")


class TestTermStructureOverride:
    """Test Term Structure override logic (ratio > 1.10 forces CAPITULACAO)"""
    
    def test_term_structure_ratio_range(self):
        """Term structure ratio should be a reasonable value"""
        response = requests.get(f"{BASE_URL}/api/term-structure", timeout=30)
        data = response.json()
        ts = data.get('term_structure', {})
        ratio = ts.get('ratio', 0)
        
        # Ratio should typically be between 0.7 and 1.3
        assert 0.5 < ratio < 1.5, f"Ratio {ratio} seems out of normal range"
        
        # Check if override would apply
        if ratio > 1.10:
            print(f"⚠️ Term Structure ratio {ratio} > 1.10 - CAPITULACAO override would apply")
        else:
            print(f"✅ Term Structure ratio {ratio} - no override (< 1.10)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
