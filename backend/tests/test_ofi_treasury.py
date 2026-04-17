"""
Test OFI (Order Flow Imbalance) and Treasury 2s10s endpoints
Tests for iteration 13: OFI feature + 2s10s yield curve migration
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestOFIEndpoint:
    """Tests for /api/ofi/{symbol} endpoint"""
    
    def test_ofi_mnq_returns_200(self):
        """GET /api/ofi/MNQ should return 200 with OFI data"""
        response = requests.get(f"{BASE_URL}/api/ofi/MNQ")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "symbol" in data
        assert data["symbol"] == "MNQ"
        assert "ofi" in data
        print("✅ GET /api/ofi/MNQ returns 200")
    
    def test_ofi_mnq_has_required_fields(self):
        """OFI response should have ofi_fast, ofi_slow, absorption_flag, signal, source"""
        response = requests.get(f"{BASE_URL}/api/ofi/MNQ")
        assert response.status_code == 200
        ofi = response.json()["ofi"]
        
        required_fields = ["ofi_fast", "ofi_slow", "absorption_flag", "signal", "source"]
        for field in required_fields:
            assert field in ofi, f"Missing required field: {field}"
        
        # Validate types
        assert isinstance(ofi["ofi_fast"], (int, float)), "ofi_fast should be numeric"
        assert isinstance(ofi["ofi_slow"], (int, float)), "ofi_slow should be numeric"
        assert isinstance(ofi["absorption_flag"], bool), "absorption_flag should be boolean"
        assert isinstance(ofi["signal"], str), "signal should be string"
        assert isinstance(ofi["source"], str), "source should be string"
        print("✅ OFI MNQ has all required fields: ofi_fast, ofi_slow, absorption_flag, signal, source")
    
    def test_ofi_mnq_values_in_range(self):
        """OFI values should be in valid range [-1, +1]"""
        response = requests.get(f"{BASE_URL}/api/ofi/MNQ")
        assert response.status_code == 200
        ofi = response.json()["ofi"]
        
        assert -1 <= ofi["ofi_fast"] <= 1, f"ofi_fast {ofi['ofi_fast']} out of range [-1, 1]"
        assert -1 <= ofi["ofi_slow"] <= 1, f"ofi_slow {ofi['ofi_slow']} out of range [-1, 1]"
        print(f"✅ OFI values in valid range: ofi_fast={ofi['ofi_fast']}, ofi_slow={ofi['ofi_slow']}")
    
    def test_ofi_mes_returns_200(self):
        """GET /api/ofi/MES should return 200 with OFI data"""
        response = requests.get(f"{BASE_URL}/api/ofi/MES")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["symbol"] == "MES"
        assert "ofi" in data
        print("✅ GET /api/ofi/MES returns 200")
    
    def test_ofi_invalid_symbol_returns_404(self):
        """GET /api/ofi/INVALID should return 404"""
        response = requests.get(f"{BASE_URL}/api/ofi/INVALID")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ GET /api/ofi/INVALID returns 404")
    
    def test_ofi_has_interpretation(self):
        """OFI response should have interpretation field"""
        response = requests.get(f"{BASE_URL}/api/ofi/MNQ")
        assert response.status_code == 200
        ofi = response.json()["ofi"]
        assert "interpretation" in ofi, "Missing interpretation field"
        assert isinstance(ofi["interpretation"], str), "interpretation should be string"
        assert len(ofi["interpretation"]) > 0, "interpretation should not be empty"
        print(f"✅ OFI has interpretation: {ofi['interpretation'][:50]}...")


class TestTreasuryEndpoint:
    """Tests for /api/treasury endpoint - 2s10s yield curve"""
    
    def test_treasury_returns_200(self):
        """GET /api/treasury should return 200"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("✅ GET /api/treasury returns 200")
    
    def test_treasury_has_2s10s_fields(self):
        """Treasury should have us2y, us10y, spread_bps, spread_type=2s10s"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        # Check required fields for 2s10s
        assert "us2y" in treasury, "Missing us2y field"
        assert "us10y" in treasury, "Missing us10y field"
        assert "spread_bps" in treasury, "Missing spread_bps field"
        assert "spread_type" in treasury, "Missing spread_type field"
        
        # Validate spread_type is 2s10s
        assert treasury["spread_type"] == "2s10s", f"Expected spread_type=2s10s, got {treasury['spread_type']}"
        print("✅ Treasury has us2y, us10y, spread_bps, spread_type=2s10s")
    
    def test_treasury_has_curve_state(self):
        """Treasury should have curve_state field"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        assert "curve_state" in treasury, "Missing curve_state field"
        valid_states = ["STEEP", "NORMAL", "FLAT", "INVERTED", "DEEP_INVERSION"]
        assert treasury["curve_state"] in valid_states, f"Invalid curve_state: {treasury['curve_state']}"
        print(f"✅ Treasury curve_state: {treasury['curve_state']}")
    
    def test_treasury_has_score_0_to_4(self):
        """Treasury should have score (0-4) and max_score=4"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        assert "score" in treasury, "Missing score field"
        assert "max_score" in treasury, "Missing max_score field"
        assert treasury["max_score"] == 4, f"Expected max_score=4, got {treasury['max_score']}"
        assert 0 <= treasury["score"] <= 4, f"Score {treasury['score']} out of range [0, 4]"
        print(f"✅ Treasury score: {treasury['score']}/{treasury['max_score']}")
    
    def test_treasury_yields_reasonable(self):
        """Treasury yields should be in reasonable range (0-10%)"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        assert 0 < treasury["us2y"] < 10, f"us2y {treasury['us2y']} out of reasonable range"
        assert 0 < treasury["us10y"] < 10, f"us10y {treasury['us10y']} out of reasonable range"
        print(f"✅ Treasury yields reasonable: US2Y={treasury['us2y']}%, US10Y={treasury['us10y']}%")


class TestAnalyzeWithOFI:
    """Tests for POST /api/analyze including OFI field"""
    
    def test_analyze_includes_ofi(self):
        """POST /api/analyze should include ofi field in response"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "ofi" in data, "Missing ofi field in analyze response"
        print("✅ POST /api/analyze includes ofi field")
    
    def test_analyze_ofi_has_required_fields(self):
        """OFI in analyze response should have all required fields"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200
        ofi = response.json()["ofi"]
        
        required_fields = ["ofi_fast", "ofi_slow", "absorption_flag", "signal", "source"]
        for field in required_fields:
            assert field in ofi, f"Missing required field in analyze ofi: {field}"
        print("✅ Analyze OFI has all required fields")
    
    def test_analyze_includes_treasury_2s10s(self):
        """POST /api/analyze should include treasury with 2s10s spread"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200
        data = response.json()
        
        assert "treasury" in data, "Missing treasury field in analyze response"
        treasury = data["treasury"]
        assert treasury["spread_type"] == "2s10s", f"Expected spread_type=2s10s, got {treasury.get('spread_type')}"
        assert "us2y" in treasury, "Missing us2y in treasury"
        assert "us10y" in treasury, "Missing us10y in treasury"
        print("✅ Analyze includes treasury with 2s10s spread")
    
    def test_analyze_includes_cvd_and_tick_index(self):
        """POST /api/analyze should include cvd and tick_index alongside ofi"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200
        data = response.json()
        
        assert "cvd" in data, "Missing cvd field in analyze response"
        assert "tick_index" in data, "Missing tick_index field in analyze response"
        print("✅ Analyze includes cvd and tick_index alongside ofi")


class TestMarketRegimeScoring:
    """Tests for Market Regime max 13 pts scoring"""
    
    def test_analyze_has_all_regime_components(self):
        """Analyze should have all 4 regime components: VIX, Treasury, Gamma, Term Structure"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200
        data = response.json()
        
        assert "real_vix" in data, "Missing real_vix for regime calculation"
        assert "treasury" in data, "Missing treasury for regime calculation"
        assert "real_gamma" in data, "Missing real_gamma for regime calculation"
        assert "term_structure" in data, "Missing term_structure for regime calculation"
        print("✅ Analyze has all 4 regime components")
    
    def test_treasury_score_contributes_0_to_4(self):
        """Treasury score should be 0-4 pts (5 states)"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        # Verify 5-state scoring
        assert treasury["max_score"] == 4, "Treasury max_score should be 4 (5 states: 0,1,2,3,4)"
        assert 0 <= treasury["score"] <= 4, f"Treasury score {treasury['score']} out of range [0, 4]"
        print(f"✅ Treasury contributes {treasury['score']}/4 pts to regime")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
