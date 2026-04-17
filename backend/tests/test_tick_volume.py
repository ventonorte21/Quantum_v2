"""
Test TICK Index and Volume Profile features
- TICK Index: Calculated from DataBento trade data (side field A=downtick, B=uptick)
- Volume Profile: POC, VAH, VAL calculated from DataBento OHLCV data
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestTickIndex:
    """TICK Index endpoint tests"""
    
    def test_tick_index_mnq_returns_200(self):
        """GET /api/tick-index/MNQ returns 200 status"""
        response = requests.get(f"{BASE_URL}/api/tick-index/MNQ")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✅ GET /api/tick-index/MNQ returns 200")
    
    def test_tick_index_mnq_has_required_fields(self):
        """GET /api/tick-index/MNQ returns tick_index, uptick, downtick, total_trades"""
        response = requests.get(f"{BASE_URL}/api/tick-index/MNQ")
        assert response.status_code == 200
        data = response.json()
        
        # Check top-level structure
        assert 'symbol' in data, "Missing 'symbol' field"
        assert 'tick_index' in data, "Missing 'tick_index' field"
        assert data['symbol'] == 'MNQ', f"Expected symbol MNQ, got {data['symbol']}"
        
        # Check tick_index object fields
        tick = data['tick_index']
        required_fields = ['tick_index', 'uptick', 'downtick', 'total_trades', 'ratio', 'sentiment', 'signal', 'score', 'source']
        for field in required_fields:
            assert field in tick, f"Missing field '{field}' in tick_index"
        
        print(f"✅ TICK Index MNQ has all required fields: tick_index={tick['tick_index']}, uptick={tick['uptick']}, downtick={tick['downtick']}, total_trades={tick['total_trades']}")
    
    def test_tick_index_mes_returns_valid_data(self):
        """GET /api/tick-index/MES returns valid data"""
        response = requests.get(f"{BASE_URL}/api/tick-index/MES")
        assert response.status_code == 200
        data = response.json()
        
        assert data['symbol'] == 'MES'
        tick = data['tick_index']
        assert 'tick_index' in tick
        assert 'uptick' in tick
        assert 'downtick' in tick
        assert 'total_trades' in tick
        
        # Validate data types
        assert isinstance(tick['tick_index'], (int, float)), "tick_index should be numeric"
        assert isinstance(tick['uptick'], (int, float)), "uptick should be numeric"
        assert isinstance(tick['downtick'], (int, float)), "downtick should be numeric"
        assert isinstance(tick['total_trades'], (int, float)), "total_trades should be numeric"
        
        print(f"✅ TICK Index MES valid: tick_index={tick['tick_index']}, source={tick['source']}")
    
    def test_tick_index_invalid_symbol_returns_404(self):
        """GET /api/tick-index/INVALID returns 404"""
        response = requests.get(f"{BASE_URL}/api/tick-index/INVALID")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ GET /api/tick-index/INVALID returns 404")
    
    def test_tick_index_sentiment_is_valid(self):
        """TICK Index sentiment is one of valid values"""
        response = requests.get(f"{BASE_URL}/api/tick-index/MNQ")
        assert response.status_code == 200
        tick = response.json()['tick_index']
        
        valid_sentiments = ['STRONG_BUY', 'BULLISH', 'NEUTRAL', 'BEARISH', 'STRONG_SELL']
        assert tick['sentiment'] in valid_sentiments, f"Invalid sentiment: {tick['sentiment']}"
        print(f"✅ TICK Index sentiment is valid: {tick['sentiment']}")
    
    def test_tick_index_score_in_range(self):
        """TICK Index score is between 0 and 1"""
        response = requests.get(f"{BASE_URL}/api/tick-index/MNQ")
        assert response.status_code == 200
        tick = response.json()['tick_index']
        
        assert 0 <= tick['score'] <= 1, f"Score out of range: {tick['score']}"
        print(f"✅ TICK Index score in valid range: {tick['score']}")
    
    def test_tick_index_source_is_valid(self):
        """TICK Index source is either databento_trades or simulated"""
        response = requests.get(f"{BASE_URL}/api/tick-index/MNQ")
        assert response.status_code == 200
        tick = response.json()['tick_index']
        
        valid_sources = ['databento_trades', 'simulated']
        assert tick['source'] in valid_sources, f"Invalid source: {tick['source']}"
        print(f"✅ TICK Index source is valid: {tick['source']}")


class TestVolumeProfile:
    """Volume Profile endpoint tests"""
    
    def test_volume_profile_mnq_returns_200(self):
        """GET /api/volume-profile/MNQ returns 200 status"""
        response = requests.get(f"{BASE_URL}/api/volume-profile/MNQ?timeframe=1H")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✅ GET /api/volume-profile/MNQ?timeframe=1H returns 200")
    
    def test_volume_profile_mnq_has_required_fields(self):
        """GET /api/volume-profile/MNQ returns poc, vah, val, profile"""
        response = requests.get(f"{BASE_URL}/api/volume-profile/MNQ?timeframe=1H")
        assert response.status_code == 200
        data = response.json()
        
        required_fields = ['poc', 'vah', 'val', 'profile', 'symbol', 'timeframe', 'current_price', 'position', 'signal', 'score', 'source']
        for field in required_fields:
            assert field in data, f"Missing field '{field}' in volume profile"
        
        print(f"✅ Volume Profile MNQ has all required fields: POC={data['poc']}, VAH={data['vah']}, VAL={data['val']}")
    
    def test_volume_profile_mes_returns_valid_data(self):
        """GET /api/volume-profile/MES returns valid data"""
        response = requests.get(f"{BASE_URL}/api/volume-profile/MES?timeframe=1H")
        assert response.status_code == 200
        data = response.json()
        
        assert data['symbol'] == 'MES'
        assert 'poc' in data
        assert 'vah' in data
        assert 'val' in data
        
        # Validate data types
        assert isinstance(data['poc'], (int, float)), "POC should be numeric"
        assert isinstance(data['vah'], (int, float)), "VAH should be numeric"
        assert isinstance(data['val'], (int, float)), "VAL should be numeric"
        
        print(f"✅ Volume Profile MES valid: POC={data['poc']}, VAH={data['vah']}, VAL={data['val']}")
    
    def test_volume_profile_invalid_symbol_returns_404(self):
        """GET /api/volume-profile/INVALID returns 404"""
        response = requests.get(f"{BASE_URL}/api/volume-profile/INVALID?timeframe=1H")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ GET /api/volume-profile/INVALID returns 404")
    
    def test_volume_profile_val_poc_vah_ordering(self):
        """Volume Profile: VAL < POC < VAH (logical ordering)"""
        response = requests.get(f"{BASE_URL}/api/volume-profile/MNQ?timeframe=1H")
        assert response.status_code == 200
        data = response.json()
        
        val = data['val']
        poc = data['poc']
        vah = data['vah']
        
        # VAL should be <= POC <= VAH
        assert val <= poc, f"VAL ({val}) should be <= POC ({poc})"
        assert poc <= vah, f"POC ({poc}) should be <= VAH ({vah})"
        print(f"✅ Volume Profile ordering correct: VAL ({val}) <= POC ({poc}) <= VAH ({vah})")
    
    def test_volume_profile_value_area_pct_approximately_70(self):
        """Volume Profile: value_area_volume_pct is approximately 70%"""
        response = requests.get(f"{BASE_URL}/api/volume-profile/MNQ?timeframe=1H")
        assert response.status_code == 200
        data = response.json()
        
        if 'value_area_volume_pct' in data:
            va_pct = data['value_area_volume_pct']
            # Should be around 70% (allow 60-80% range for edge cases)
            assert 60 <= va_pct <= 85, f"Value area pct ({va_pct}) should be approximately 70%"
            print(f"✅ Volume Profile value_area_volume_pct is approximately 70%: {va_pct}%")
        else:
            print("⚠️ value_area_volume_pct field not present (may be optional)")
    
    def test_volume_profile_position_is_valid(self):
        """Volume Profile position is one of valid values"""
        response = requests.get(f"{BASE_URL}/api/volume-profile/MNQ?timeframe=1H")
        assert response.status_code == 200
        data = response.json()
        
        valid_positions = ['ABOVE_VA', 'BELOW_VA', 'INSIDE_VA']
        assert data['position'] in valid_positions, f"Invalid position: {data['position']}"
        print(f"✅ Volume Profile position is valid: {data['position']}")
    
    def test_volume_profile_has_histogram_data(self):
        """Volume Profile has profile histogram data"""
        response = requests.get(f"{BASE_URL}/api/volume-profile/MNQ?timeframe=1H")
        assert response.status_code == 200
        data = response.json()
        
        assert 'profile' in data, "Missing 'profile' field"
        assert isinstance(data['profile'], list), "profile should be a list"
        
        if len(data['profile']) > 0:
            # Check first item has expected fields
            item = data['profile'][0]
            assert 'price' in item, "Profile item missing 'price'"
            assert 'volume' in item, "Profile item missing 'volume'"
            print(f"✅ Volume Profile has histogram data with {len(data['profile'])} price levels")
        else:
            print("⚠️ Volume Profile histogram is empty")


class TestAnalyzeEndpoint:
    """Test /api/analyze endpoint includes tick_index and volume_profile"""
    
    def test_analyze_mnq_returns_tick_index(self):
        """POST /api/analyze with symbol MNQ returns tick_index object"""
        response = requests.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert 'tick_index' in data, "Missing 'tick_index' in analyze response"
        tick = data['tick_index']
        
        # Verify tick_index has required fields
        assert 'tick_index' in tick, "tick_index object missing 'tick_index' field"
        assert 'uptick' in tick, "tick_index object missing 'uptick' field"
        assert 'downtick' in tick, "tick_index object missing 'downtick' field"
        
        print(f"✅ POST /api/analyze MNQ returns tick_index: {tick['tick_index']}")
    
    def test_analyze_mnq_returns_volume_profile(self):
        """POST /api/analyze with symbol MNQ returns volume_profile object"""
        response = requests.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200
        data = response.json()
        
        assert 'volume_profile' in data, "Missing 'volume_profile' in analyze response"
        vp = data['volume_profile']
        
        if vp is not None:
            # Verify volume_profile has required fields
            assert 'poc' in vp, "volume_profile missing 'poc' field"
            assert 'vah' in vp, "volume_profile missing 'vah' field"
            assert 'val' in vp, "volume_profile missing 'val' field"
            print(f"✅ POST /api/analyze MNQ returns volume_profile: POC={vp['poc']}, VAH={vp['vah']}, VAL={vp['val']}")
        else:
            print("⚠️ volume_profile is None (may be expected if no data)")
    
    def test_analyze_mes_returns_both_features(self):
        """POST /api/analyze with symbol MES returns both tick_index and volume_profile"""
        response = requests.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MES",
            "timeframe": "1H"
        })
        assert response.status_code == 200
        data = response.json()
        
        assert 'tick_index' in data, "Missing 'tick_index' in analyze response"
        assert 'volume_profile' in data, "Missing 'volume_profile' in analyze response"
        
        print(f"✅ POST /api/analyze MES returns both tick_index and volume_profile")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
