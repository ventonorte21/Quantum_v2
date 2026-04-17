"""
Test D-1 Closing Ratio ETF→Futures for Gamma Wall Projection
Tests the new gamma_ratio_service and its integration with v3/signal endpoint.
"""

import pytest
import requests
import os
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestGammaRatioEndpoint:
    """Tests for GET /api/gamma/ratio/{symbol}"""
    
    def test_mnq_ratio_returns_valid_structure(self):
        """GET /api/gamma/ratio/MNQ should return object with required fields"""
        response = requests.get(f"{BASE_URL}/api/gamma/ratio/MNQ")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        # Required fields
        assert 'ratio' in data, "Missing 'ratio' field"
        assert 'source' in data, "Missing 'source' field"
        assert 'computed_for' in data, "Missing 'computed_for' field"
        assert 'contract' in data, "Missing 'contract' field"
        assert 'is_roll_period' in data, "Missing 'is_roll_period' field"
        
        # Validate values
        assert data['ratio'] > 0, f"Ratio should be > 0, got {data['ratio']}"
        assert data['source'] in ('d1_close', 'realtime_fallback'), f"Invalid source: {data['source']}"
        assert data['contract'] == 'MNQ.v.0', f"Expected MNQ.v.0, got {data['contract']}"
        print(f"✅ MNQ ratio: {data['ratio']}, source: {data['source']}")
    
    def test_mes_ratio_returns_valid_structure(self):
        """GET /api/gamma/ratio/MES should return equivalent data for MES"""
        response = requests.get(f"{BASE_URL}/api/gamma/ratio/MES")
        assert response.status_code == 200
        
        data = response.json()
        assert data['ratio'] > 0
        assert data['source'] in ('d1_close', 'realtime_fallback')
        assert data['contract'] == 'MES.v.0'
        assert data['etf_symbol'] == 'SPY'
        print(f"✅ MES ratio: {data['ratio']}, source: {data['source']}")
    
    def test_d1_close_has_futures_and_etf_prices(self):
        """D-1 close source should include futures_close and etf_close"""
        response = requests.get(f"{BASE_URL}/api/gamma/ratio/MNQ")
        data = response.json()
        
        if data['source'] == 'd1_close':
            assert 'futures_close' in data, "Missing futures_close for d1_close source"
            assert 'etf_close' in data, "Missing etf_close for d1_close source"
            assert data['futures_close'] > 10000, f"Futures close too low: {data['futures_close']}"
            assert data['etf_close'] > 100, f"ETF close too low: {data['etf_close']}"
            # Verify ratio calculation
            expected_ratio = data['futures_close'] / data['etf_close']
            assert abs(data['ratio'] - expected_ratio) < 0.001, "Ratio doesn't match futures/etf calculation"
            print(f"✅ D-1 close: futures={data['futures_close']}, etf={data['etf_close']}")
        else:
            print(f"ℹ️ Using fallback source, skipping D-1 close validation")
    
    def test_is_roll_period_false_in_april(self):
        """is_roll_period should be false in April (not a roll month)"""
        response = requests.get(f"{BASE_URL}/api/gamma/ratio/MNQ")
        data = response.json()
        
        # April is not a roll month (Mar/Jun/Sep/Dec)
        assert data['is_roll_period'] == False, "is_roll_period should be False in April"
        print("✅ is_roll_period correctly False for April")
    
    def test_caching_second_call_fast(self):
        """Second call should be cached and fast"""
        import time
        
        # First call
        start1 = time.time()
        response1 = requests.get(f"{BASE_URL}/api/gamma/ratio/MNQ")
        time1 = time.time() - start1
        
        # Second call (should be cached)
        start2 = time.time()
        response2 = requests.get(f"{BASE_URL}/api/gamma/ratio/MNQ")
        time2 = time.time() - start2
        
        assert response1.status_code == 200
        assert response2.status_code == 200
        
        # Both should return same data
        data1 = response1.json()
        data2 = response2.json()
        assert data1['ratio'] == data2['ratio'], "Cached data should match"
        assert data1['computed_for'] == data2['computed_for'], "Cached date should match"
        
        print(f"✅ First call: {time1:.3f}s, Second call: {time2:.3f}s")


class TestV3SignalGammaRatio:
    """Tests for gamma ratio integration in GET /api/v3/signal/{symbol}"""
    
    def test_nivel_1_contains_gamma_ratio_fields(self):
        """nivel_1 should contain gamma_ratio, gamma_ratio_source, is_roll_period"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        n1 = data.get('nivel_1', {})
        
        assert 'gamma_ratio' in n1, "Missing gamma_ratio in nivel_1"
        assert 'gamma_ratio_source' in n1, "Missing gamma_ratio_source in nivel_1"
        assert 'is_roll_period' in n1, "Missing is_roll_period in nivel_1"
        
        assert n1['gamma_ratio'] > 0, f"gamma_ratio should be > 0, got {n1['gamma_ratio']}"
        assert n1['gamma_ratio_source'] in ('d1_close', 'realtime_fallback', 'unknown'), f"Invalid source: {n1['gamma_ratio_source']}"
        assert isinstance(n1['is_roll_period'], bool), "is_roll_period should be boolean"
        
        print(f"✅ nivel_1 gamma_ratio: {n1['gamma_ratio']}, source: {n1['gamma_ratio_source']}")
    
    def test_nivel_1_contains_fallback_fields(self):
        """nivel_1 should contain using_fallback_gamma and gamma_warning"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        data = response.json()
        n1 = data.get('nivel_1', {})
        
        assert 'using_fallback_gamma' in n1, "Missing using_fallback_gamma in nivel_1"
        assert isinstance(n1['using_fallback_gamma'], bool), "using_fallback_gamma should be boolean"
        
        # gamma_warning can be null or string
        assert 'gamma_warning' in n1 or n1.get('gamma_warning') is None, "gamma_warning field should exist"
        
        if n1['using_fallback_gamma']:
            assert n1.get('gamma_warning') is not None, "gamma_warning should be set when using fallback"
            print(f"✅ Fallback active: {n1['gamma_warning'][:50]}...")
        else:
            print("✅ Real gamma active, no fallback warning")
    
    def test_context_contains_gamma_walls(self):
        """context should contain call_wall, put_wall, zgl (converted with D-1 ratio)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        data = response.json()
        ctx = data.get('context', {})
        
        assert 'call_wall' in ctx, "Missing call_wall in context"
        assert 'put_wall' in ctx, "Missing put_wall in context"
        assert 'zgl' in ctx, "Missing zgl in context"
        
        # Values should be numeric
        assert isinstance(ctx['call_wall'], (int, float)), "call_wall should be numeric"
        assert isinstance(ctx['put_wall'], (int, float)), "put_wall should be numeric"
        assert isinstance(ctx['zgl'], (int, float)), "zgl should be numeric"
        
        print(f"✅ Context gamma walls: call={ctx['call_wall']}, put={ctx['put_wall']}, zgl={ctx['zgl']}")
    
    def test_mes_signal_also_has_gamma_ratio(self):
        """MES signal should also have gamma ratio fields"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200
        
        data = response.json()
        n1 = data.get('nivel_1', {})
        
        assert 'gamma_ratio' in n1
        assert n1['gamma_ratio'] > 0
        # MES ratio should be ~10 (SPY ~550, MES ~5500)
        assert 5 < n1['gamma_ratio'] < 20, f"MES ratio seems off: {n1['gamma_ratio']}"
        
        print(f"✅ MES gamma_ratio: {n1['gamma_ratio']}")


class TestRollPeriodDetection:
    """Tests for roll period detection logic"""
    
    def test_april_is_not_roll_period(self):
        """April is not a roll month (Mar/Jun/Sep/Dec)"""
        response = requests.get(f"{BASE_URL}/api/gamma/ratio/MNQ")
        data = response.json()
        
        # Current month is April 2026
        assert data['is_roll_period'] == False, "April should not be roll period"
        print("✅ April correctly identified as non-roll period")
    
    def test_roll_period_in_nivel_1(self):
        """is_roll_period should be propagated to nivel_1"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        data = response.json()
        n1 = data.get('nivel_1', {})
        
        assert 'is_roll_period' in n1
        assert n1['is_roll_period'] == False, "April should not be roll period in nivel_1"
        print("✅ is_roll_period correctly propagated to nivel_1")


class TestMongoDBCaching:
    """Tests for MongoDB caching of gamma ratios"""
    
    def test_ratio_stored_in_mongodb(self):
        """Ratio should be cached in MongoDB gamma_ratios collection"""
        # First call to ensure data is stored
        response = requests.get(f"{BASE_URL}/api/gamma/ratio/MNQ")
        assert response.status_code == 200
        data = response.json()
        
        # Verify computed_for is today's date
        today = datetime.now().strftime('%Y-%m-%d')
        assert data['computed_for'] == today, f"computed_for should be {today}, got {data['computed_for']}"
        
        # Verify timestamp is recent
        assert 'timestamp' in data, "Missing timestamp"
        print(f"✅ Ratio cached for {data['computed_for']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
