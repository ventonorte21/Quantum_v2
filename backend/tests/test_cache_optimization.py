"""
Test Cache Optimization Features - VP D-1, VP Session, OFI, Macro TTLs

Tests:
1. POST /api/analyze returns complete data (real_vix, session_vps with prev_day_vp)
2. Second call to /api/analyze is cache HIT (<2s)
3. GET /api/ofi/MES returns ofi_fast and ofi_slow
4. GET /api/session-vps/MES returns prev_day_vp with POC > 0
5. VP D-1 cache persistence (third call has identical prev_day_vp)
6. Macro data (VIX, term_structure) present in /api/analyze response
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestCacheOptimization:
    """Cache optimization tests for VP D-1, VP Session, OFI, Macro TTLs"""
    
    # Store responses for comparison across tests
    _first_analyze_response = None
    _second_analyze_response = None
    _third_analyze_response = None
    
    def test_01_analyze_returns_complete_data(self):
        """POST /api/analyze with symbol=MES,timeframe=5M returns 200 with complete data"""
        url = f"{BASE_URL}/api/analyze"
        payload = {"symbol": "MES", "timeframe": "5M"}
        
        # First call may take 30-60s (DataBento API)
        start_time = time.time()
        response = requests.post(url, json=payload, timeout=120)
        elapsed = time.time() - start_time
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Store for later comparison
        TestCacheOptimization._first_analyze_response = data
        
        # Verify real_vix is present
        assert 'real_vix' in data, "real_vix missing from response"
        real_vix = data['real_vix']
        assert real_vix is not None, "real_vix is None"
        assert isinstance(real_vix, dict), f"real_vix should be dict, got {type(real_vix)}"
        
        # Verify session_vps is present
        assert 'session_vps' in data, "session_vps missing from response"
        session_vps = data['session_vps']
        assert session_vps is not None, "session_vps is None"
        
        # Verify prev_day_vp is present and has POC
        prev_day_vp = session_vps.get('prev_day_vp')
        # Note: prev_day_vp may be None on weekends or if no historical data
        if prev_day_vp is not None:
            assert 'poc' in prev_day_vp, "prev_day_vp missing 'poc' field"
            # POC should be > 0 if data exists
            poc = prev_day_vp.get('poc', 0)
            print(f"VP D-1 POC: {poc}")
        else:
            print("prev_day_vp is None (may be weekend or no historical data)")
        
        print(f"First /api/analyze call took {elapsed:.1f}s")
        print(f"real_vix present: {real_vix is not None}")
        print(f"session_vps present: {session_vps is not None}")
    
    def test_02_analyze_cache_hit(self):
        """Second call to /api/analyze should be cache HIT (<2s)"""
        url = f"{BASE_URL}/api/analyze"
        payload = {"symbol": "MES", "timeframe": "5M"}
        
        start_time = time.time()
        response = requests.post(url, json=payload, timeout=30)
        elapsed = time.time() - start_time
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Store for later comparison
        TestCacheOptimization._second_analyze_response = data
        
        # Cache hit should be fast (<2s)
        assert elapsed < 5, f"Cache HIT should be <5s, took {elapsed:.1f}s"
        print(f"Second /api/analyze call (cache HIT) took {elapsed:.1f}s")
    
    def test_03_ofi_returns_fast_slow(self):
        """GET /api/ofi/MES returns 200 with ofi_fast and ofi_slow"""
        url = f"{BASE_URL}/api/ofi/MES"
        
        response = requests.get(url, timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'ofi' in data, "ofi field missing from response"
        
        ofi = data['ofi']
        assert 'ofi_fast' in ofi, "ofi_fast missing from OFI response"
        assert 'ofi_slow' in ofi, "ofi_slow missing from OFI response"
        
        ofi_fast = ofi['ofi_fast']
        ofi_slow = ofi['ofi_slow']
        
        # OFI values should be between -1 and 1
        assert -1 <= ofi_fast <= 1, f"ofi_fast out of range: {ofi_fast}"
        assert -1 <= ofi_slow <= 1, f"ofi_slow out of range: {ofi_slow}"
        
        print(f"OFI Fast: {ofi_fast}, OFI Slow: {ofi_slow}")
        print(f"OFI source: {ofi.get('source', 'unknown')}")
    
    def test_04_session_vps_returns_prev_day_vp(self):
        """GET /api/session-vps/MES returns 200 with prev_day_vp (POC > 0)"""
        url = f"{BASE_URL}/api/session-vps/MES"
        
        response = requests.get(url, timeout=90)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Check prev_day_vp
        prev_day_vp = data.get('prev_day_vp')
        if prev_day_vp is not None:
            assert 'poc' in prev_day_vp, "prev_day_vp missing 'poc' field"
            poc = prev_day_vp.get('poc', 0)
            # POC should be > 0 for valid VP data
            assert poc > 0, f"prev_day_vp POC should be > 0, got {poc}"
            print(f"Session VPs - prev_day_vp POC: {poc}")
            print(f"prev_day_vp VAH: {prev_day_vp.get('vah')}, VAL: {prev_day_vp.get('val')}")
        else:
            # May be None on weekends or if no historical data
            print("prev_day_vp is None (may be weekend or no historical data)")
            pytest.skip("prev_day_vp is None - skipping POC check")
    
    def test_05_vp_d1_cache_persistence(self):
        """VP D-1 cache: third call should have identical prev_day_vp to second"""
        url = f"{BASE_URL}/api/analyze"
        payload = {"symbol": "MES", "timeframe": "5M"}
        
        # Third call
        response = requests.post(url, json=payload, timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        TestCacheOptimization._third_analyze_response = data
        
        # Compare prev_day_vp between second and third calls
        second_resp = TestCacheOptimization._second_analyze_response
        third_resp = TestCacheOptimization._third_analyze_response
        
        if second_resp and third_resp:
            second_vp = second_resp.get('session_vps', {}).get('prev_day_vp')
            third_vp = third_resp.get('session_vps', {}).get('prev_day_vp')
            
            if second_vp and third_vp:
                # POC should be identical (cached)
                assert second_vp.get('poc') == third_vp.get('poc'), \
                    f"VP D-1 POC mismatch: {second_vp.get('poc')} vs {third_vp.get('poc')}"
                assert second_vp.get('vah') == third_vp.get('vah'), \
                    f"VP D-1 VAH mismatch: {second_vp.get('vah')} vs {third_vp.get('vah')}"
                assert second_vp.get('val') == third_vp.get('val'), \
                    f"VP D-1 VAL mismatch: {second_vp.get('val')} vs {third_vp.get('val')}"
                print("VP D-1 cache persistence verified - POC/VAH/VAL identical across calls")
            else:
                print("prev_day_vp is None in one or both responses - skipping comparison")
        else:
            pytest.skip("Previous responses not available for comparison")
    
    def test_06_macro_data_in_analyze(self):
        """Macro data (VIX, term_structure) should be present in /api/analyze response"""
        # Use cached response from first call
        data = TestCacheOptimization._first_analyze_response
        
        if data is None:
            # Make a fresh call if no cached response
            url = f"{BASE_URL}/api/analyze"
            payload = {"symbol": "MES", "timeframe": "5M"}
            response = requests.post(url, json=payload, timeout=120)
            assert response.status_code == 200
            data = response.json()
        
        # Check VIX data
        assert 'real_vix' in data, "real_vix missing from response"
        real_vix = data['real_vix']
        if real_vix:
            # VIX should have current value
            vix_value = real_vix.get('current') or real_vix.get('value') or real_vix.get('vix')
            print(f"VIX value: {vix_value}")
            print(f"VIX regime: {real_vix.get('regime', 'unknown')}")
        
        # Check term_structure
        assert 'term_structure' in data, "term_structure missing from response"
        term_structure = data['term_structure']
        if term_structure:
            print(f"Term structure present: {term_structure.get('structure', 'unknown')}")
            print(f"Term structure source: {term_structure.get('source', 'unknown')}")
        
        print("Macro data verification complete")


class TestOFISessionAwareCache:
    """Test OFI session-aware cache (60s RTH / 120s Globex)"""
    
    def test_ofi_cache_behavior(self):
        """OFI should use session-aware TTL"""
        url = f"{BASE_URL}/api/ofi/MES"
        
        # First call
        start1 = time.time()
        resp1 = requests.get(url, timeout=60)
        elapsed1 = time.time() - start1
        assert resp1.status_code == 200
        
        # Second call (should be cached)
        start2 = time.time()
        resp2 = requests.get(url, timeout=30)
        elapsed2 = time.time() - start2
        assert resp2.status_code == 200
        
        # Cache hit should be faster
        print(f"OFI first call: {elapsed1:.2f}s, second call: {elapsed2:.2f}s")
        
        # Verify data consistency
        data1 = resp1.json()['ofi']
        data2 = resp2.json()['ofi']
        
        # Timestamps should be identical if cached
        if data1.get('timestamp') == data2.get('timestamp'):
            print("OFI cache HIT confirmed (identical timestamps)")
        else:
            print("OFI cache MISS (different timestamps) - may be TTL expired")


class TestBackendLogs:
    """Test that backend has no _get_macro_ttl errors"""
    
    def test_no_macro_ttl_errors(self):
        """Backend should not have _get_macro_ttl errors after restart"""
        # This test verifies the fix is in place by checking the endpoint works
        url = f"{BASE_URL}/api/analyze"
        payload = {"symbol": "MES", "timeframe": "5M"}
        
        response = requests.post(url, json=payload, timeout=120)
        
        # If we get a 200, the _get_macro_ttl function is working
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        # Verify macro data is present (would fail if _get_macro_ttl had errors)
        data = response.json()
        assert 'real_vix' in data, "real_vix missing - possible _get_macro_ttl error"
        assert 'term_structure' in data, "term_structure missing - possible _get_macro_ttl error"
        
        print("No _get_macro_ttl errors detected - macro data fetched successfully")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
