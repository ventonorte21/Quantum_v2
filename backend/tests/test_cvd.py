"""
CVD (Cumulative Volume Delta) API Tests
Tests the CVD endpoint and CVD data in analyze response
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestCVDEndpoint:
    """Tests for GET /api/cvd/{symbol} endpoint"""
    
    def test_cvd_mnq_returns_valid_data(self):
        """GET /api/cvd/MNQ returns valid CVD data"""
        response = requests.get(f"{BASE_URL}/api/cvd/MNQ")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert 'symbol' in data, "Response should contain 'symbol'"
        assert data['symbol'] == 'MNQ', f"Expected symbol MNQ, got {data['symbol']}"
        assert 'cvd' in data, "Response should contain 'cvd' object"
        
        cvd = data['cvd']
        # Verify all required fields
        required_fields = ['cvd', 'buy_volume', 'sell_volume', 'cvd_trend', 'sentiment']
        for field in required_fields:
            assert field in cvd, f"CVD data should contain '{field}'"
        
        print(f"✅ MNQ CVD: {cvd['cvd']}, trend: {cvd['cvd_trend']}, sentiment: {cvd['sentiment']}")
    
    def test_cvd_mes_returns_valid_data(self):
        """GET /api/cvd/MES returns valid CVD data"""
        response = requests.get(f"{BASE_URL}/api/cvd/MES")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data['symbol'] == 'MES'
        assert 'cvd' in data
        
        cvd = data['cvd']
        assert 'cvd' in cvd
        assert 'buy_volume' in cvd
        assert 'sell_volume' in cvd
        
        print(f"✅ MES CVD: {cvd['cvd']}, trend: {cvd['cvd_trend']}, sentiment: {cvd['sentiment']}")
    
    def test_cvd_invalid_symbol_returns_404(self):
        """GET /api/cvd/INVALID returns 404"""
        response = requests.get(f"{BASE_URL}/api/cvd/INVALID")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        
        data = response.json()
        assert 'detail' in data, "404 response should contain 'detail'"
        print(f"✅ Invalid symbol correctly returns 404: {data['detail']}")
    
    def test_cvd_volume_math_correct(self):
        """CVD: buy_volume + sell_volume = total_volume"""
        response = requests.get(f"{BASE_URL}/api/cvd/MNQ")
        assert response.status_code == 200
        
        cvd = response.json()['cvd']
        buy_vol = cvd['buy_volume']
        sell_vol = cvd['sell_volume']
        total_vol = cvd['total_volume']
        
        # Allow small floating point tolerance
        assert abs((buy_vol + sell_vol) - total_vol) < 1, \
            f"buy_volume ({buy_vol}) + sell_volume ({sell_vol}) should equal total_volume ({total_vol})"
        
        print(f"✅ Volume math correct: {buy_vol} + {sell_vol} = {total_vol}")
    
    def test_cvd_percentage_math_correct(self):
        """CVD: buy_pct + sell_pct = 100"""
        response = requests.get(f"{BASE_URL}/api/cvd/MNQ")
        assert response.status_code == 200
        
        cvd = response.json()['cvd']
        buy_pct = cvd['buy_pct']
        sell_pct = cvd['sell_pct']
        
        # Allow small floating point tolerance
        assert abs((buy_pct + sell_pct) - 100) < 0.5, \
            f"buy_pct ({buy_pct}) + sell_pct ({sell_pct}) should equal 100"
        
        print(f"✅ Percentage math correct: {buy_pct}% + {sell_pct}% = 100%")
    
    def test_cvd_trend_valid_values(self):
        """CVD trend should be RISING or FALLING"""
        response = requests.get(f"{BASE_URL}/api/cvd/MNQ")
        assert response.status_code == 200
        
        cvd = response.json()['cvd']
        assert cvd['cvd_trend'] in ['RISING', 'FALLING'], \
            f"cvd_trend should be RISING or FALLING, got {cvd['cvd_trend']}"
        
        print(f"✅ CVD trend is valid: {cvd['cvd_trend']}")
    
    def test_cvd_sentiment_valid_values(self):
        """CVD sentiment should be valid value"""
        response = requests.get(f"{BASE_URL}/api/cvd/MNQ")
        assert response.status_code == 200
        
        cvd = response.json()['cvd']
        valid_sentiments = ['STRONG_BUY', 'BULLISH', 'NEUTRAL', 'BEARISH', 'STRONG_SELL']
        assert cvd['sentiment'] in valid_sentiments, \
            f"sentiment should be one of {valid_sentiments}, got {cvd['sentiment']}"
        
        print(f"✅ CVD sentiment is valid: {cvd['sentiment']}")


class TestCVDInAnalyze:
    """Tests for CVD data in POST /api/analyze response"""
    
    def test_analyze_includes_cvd(self):
        """POST /api/analyze with symbol MNQ returns cvd object in response"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert 'cvd' in data, "Analyze response should contain 'cvd' object"
        
        cvd = data['cvd']
        assert 'cvd' in cvd, "CVD object should contain 'cvd' value"
        assert 'buy_volume' in cvd, "CVD object should contain 'buy_volume'"
        assert 'sell_volume' in cvd, "CVD object should contain 'sell_volume'"
        assert 'cvd_trend' in cvd, "CVD object should contain 'cvd_trend'"
        assert 'sentiment' in cvd, "CVD object should contain 'sentiment'"
        
        print(f"✅ Analyze response includes CVD: {cvd['cvd']}, trend: {cvd['cvd_trend']}")
    
    def test_analyze_cvd_has_all_fields(self):
        """CVD in analyze response has all required fields"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MES", "timeframe": "1H"}
        )
        assert response.status_code == 200
        
        cvd = response.json()['cvd']
        required_fields = [
            'cvd', 'buy_volume', 'sell_volume', 'total_volume',
            'buy_pct', 'sell_pct', 'cvd_high', 'cvd_low',
            'cvd_trend', 'total_trades', 'sentiment', 'signal',
            'score', 'lookback_minutes', 'source', 'timestamp'
        ]
        
        for field in required_fields:
            assert field in cvd, f"CVD should contain '{field}'"
        
        print(f"✅ CVD has all {len(required_fields)} required fields")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
