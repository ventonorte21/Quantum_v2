"""
Treasury Yields API Tests
Tests for US10Y, US30Y yields and spread functionality
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestTreasuryEndpoint:
    """Tests for GET /api/treasury endpoint"""
    
    def test_treasury_endpoint_returns_200(self):
        """Test that treasury endpoint returns 200 status"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("✅ GET /api/treasury returns 200")
    
    def test_treasury_has_required_fields(self):
        """Test that treasury response has all required fields"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        data = response.json()
        
        assert "treasury" in data, "Response should have 'treasury' key"
        treasury = data["treasury"]
        
        # Required fields
        required_fields = ["us10y", "us30y", "spread", "curve_state", "interpretation"]
        for field in required_fields:
            assert field in treasury, f"Treasury should have '{field}' field"
        
        print(f"✅ Treasury has all required fields: {required_fields}")
    
    def test_treasury_us10y_positive(self):
        """Test that US10Y yield is positive (real data)"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        us10y = treasury.get("us10y", 0)
        assert us10y > 0, f"US10Y should be positive, got {us10y}"
        print(f"✅ US10Y yield is positive: {us10y}%")
    
    def test_treasury_us30y_positive(self):
        """Test that US30Y yield is positive (real data)"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        us30y = treasury.get("us30y", 0)
        assert us30y > 0, f"US30Y should be positive, got {us30y}"
        print(f"✅ US30Y yield is positive: {us30y}%")
    
    def test_treasury_spread_calculation(self):
        """Test that spread = US30Y - US10Y"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        us10y = treasury.get("us10y", 0)
        us30y = treasury.get("us30y", 0)
        spread = treasury.get("spread", 0)
        
        expected_spread = round(us30y - us10y, 4)
        assert abs(spread - expected_spread) < 0.001, f"Spread should be {expected_spread}, got {spread}"
        print(f"✅ Spread calculation correct: {us30y} - {us10y} = {spread}")
    
    def test_treasury_curve_state_valid(self):
        """Test that curve_state is one of valid values"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        curve_state = treasury.get("curve_state")
        valid_states = ["INVERTED", "FLAT_INVERTED", "FLAT", "NORMAL", "STEEP", "UNKNOWN"]
        assert curve_state in valid_states, f"curve_state should be one of {valid_states}, got {curve_state}"
        print(f"✅ Curve state is valid: {curve_state}")
    
    def test_treasury_has_change_fields(self):
        """Test that treasury has change tracking fields"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        change_fields = ["us10y_change", "us30y_change", "spread_change"]
        for field in change_fields:
            assert field in treasury, f"Treasury should have '{field}' field"
        
        print(f"✅ Treasury has change fields: {change_fields}")
    
    def test_treasury_has_month_stats(self):
        """Test that treasury has monthly statistics"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        # Check for month range fields
        assert "us10y_month_range" in treasury, "Should have us10y_month_range"
        assert "us30y_month_range" in treasury, "Should have us30y_month_range"
        assert "spread_month_mean" in treasury, "Should have spread_month_mean"
        
        print("✅ Treasury has monthly statistics")


class TestAnalyzeEndpointTreasury:
    """Tests for POST /api/analyze treasury data"""
    
    def test_analyze_mnq_includes_treasury(self):
        """Test that POST /api/analyze with MNQ includes treasury data"""
        response = requests.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "treasury" in data, "Analyze response should include 'treasury'"
        treasury = data["treasury"]
        
        # Verify required fields
        assert "us10y" in treasury, "Treasury should have us10y"
        assert "us30y" in treasury, "Treasury should have us30y"
        assert "spread" in treasury, "Treasury should have spread"
        assert "curve_state" in treasury, "Treasury should have curve_state"
        
        print(f"✅ POST /api/analyze MNQ includes treasury: us10y={treasury['us10y']}, us30y={treasury['us30y']}, spread={treasury['spread']}, curve_state={treasury['curve_state']}")
    
    def test_analyze_mes_includes_treasury(self):
        """Test that POST /api/analyze with MES includes treasury data"""
        response = requests.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MES",
            "timeframe": "4H"
        })
        assert response.status_code == 200
        data = response.json()
        
        assert "treasury" in data, "Analyze response should include 'treasury'"
        treasury = data["treasury"]
        
        assert treasury.get("us10y", 0) > 0, "US10Y should be positive"
        assert treasury.get("us30y", 0) > 0, "US30Y should be positive"
        
        print(f"✅ POST /api/analyze MES includes treasury with positive yields")


class TestVolatilityEndpointTreasury:
    """Tests for GET /api/volatility/{symbol} treasury data"""
    
    def test_volatility_mes_includes_treasury(self):
        """Test that GET /api/volatility/MES includes treasury data"""
        response = requests.get(f"{BASE_URL}/api/volatility/MES")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        assert "treasury" in data, "Volatility response should include 'treasury'"
        treasury = data["treasury"]
        
        assert "us10y" in treasury, "Treasury should have us10y"
        assert "us30y" in treasury, "Treasury should have us30y"
        assert "spread" in treasury, "Treasury should have spread"
        assert "curve_state" in treasury, "Treasury should have curve_state"
        
        print(f"✅ GET /api/volatility/MES includes treasury data")
    
    def test_volatility_mnq_includes_treasury(self):
        """Test that GET /api/volatility/MNQ includes treasury data"""
        response = requests.get(f"{BASE_URL}/api/volatility/MNQ")
        assert response.status_code == 200
        data = response.json()
        
        assert "treasury" in data, "Volatility response should include 'treasury'"
        print(f"✅ GET /api/volatility/MNQ includes treasury data")


class TestTreasuryDataIntegrity:
    """Tests for treasury data integrity and consistency"""
    
    def test_treasury_signal_valid(self):
        """Test that treasury signal is valid"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        signal = treasury.get("signal")
        valid_signals = ["CONFIRM", "CAUTION", "NEUTRAL"]
        assert signal in valid_signals, f"Signal should be one of {valid_signals}, got {signal}"
        print(f"✅ Treasury signal is valid: {signal}")
    
    def test_treasury_score_in_range(self):
        """Test that treasury score is between 0 and 1"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        score = treasury.get("score", 0)
        assert 0 <= score <= 1, f"Score should be between 0 and 1, got {score}"
        print(f"✅ Treasury score in valid range: {score}")
    
    def test_treasury_source_is_yahoo(self):
        """Test that treasury data source is yahoo_finance"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        source = treasury.get("source")
        assert source in ["yahoo_finance", "error"], f"Source should be yahoo_finance or error, got {source}"
        print(f"✅ Treasury source: {source}")
    
    def test_treasury_has_timestamp(self):
        """Test that treasury data has timestamp"""
        response = requests.get(f"{BASE_URL}/api/treasury")
        assert response.status_code == 200
        treasury = response.json()["treasury"]
        
        assert "timestamp" in treasury, "Treasury should have timestamp"
        print(f"✅ Treasury has timestamp: {treasury['timestamp']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
