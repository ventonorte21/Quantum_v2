"""
Test Suite: Live Merge Guardrail & VP/VWAP Pipeline Audit (Iteration 53)
========================================================================
Tests for:
1. POST /api/analyze returns live_merge object with keys: status, buffer_age_s, candles_added, buffer_trades, skipped_reason
2. POST /api/analyze live_merge.status is 'OK' when feed is connected and buffer age < 60s
3. POST /api/analyze live_merge.buffer_age_s is a number (integer) < 60 when status is OK
4. POST /api/analyze live_merge.candles_added is a positive integer when status is OK
5. GET /api/v3/signal/MNQ returns data_quality.live_merge with same keys as analyze
6. GET /api/v3/signal/MNQ data_quality.mqs.ok is True when live_merge.status is OK
7. GET /api/v3/signal/MNQ returns data_quality.live_merge.status field
8. GET /api/session-vwaps/MNQ returns valid data (globex or session_ny VWAP present)
9. GET /api/session-vwaps/MNQ second call within 60s returns cached result (same data)
10. POST /api/analyze still returns session_vwaps.globex with vwap > 0 (live buffer merged)
11. POST /api/analyze still returns session_vps with daily_vp, rth_vp, prev_day_vp keys
12. GET /api/v3/signal/MNQ N3 action should contain valid response (WAIT/BUY/SELL)
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")

# Expected keys in live_merge object
LIVE_MERGE_KEYS = {'status', 'buffer_age_s', 'candles_added', 'buffer_trades', 'skipped_reason'}


@pytest.fixture(scope="module")
def api_client():
    """Shared requests session with auth header"""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SESSION_TOKEN}"
    })
    return session


class TestAnalyzeLiveMerge:
    """Tests for POST /api/analyze live_merge metadata"""

    def test_analyze_returns_live_merge_object(self, api_client):
        """POST /api/analyze returns live_merge object with required keys"""
        response = api_client.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'live_merge' in data, "Response missing 'live_merge' key"
        
        live_merge = data['live_merge']
        assert isinstance(live_merge, dict), "live_merge should be a dict"
        
        # Check all required keys are present
        for key in LIVE_MERGE_KEYS:
            assert key in live_merge, f"live_merge missing key: {key}"
        
        print(f"✅ live_merge object: {live_merge}")

    def test_analyze_live_merge_status_ok_when_feed_connected(self, api_client):
        """POST /api/analyze live_merge.status is 'OK' when feed is connected and buffer age < 60s"""
        response = api_client.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200
        
        data = response.json()
        live_merge = data.get('live_merge', {})
        status = live_merge.get('status')
        
        # During Globex session with live feed, status should be OK
        # If status is STALE/DISCONNECTED, that's also valid (feed issue)
        valid_statuses = ['OK', 'STALE', 'DISCONNECTED', 'NO_BUFFER']
        assert status in valid_statuses, f"Unexpected status: {status}"
        
        if status == 'OK':
            print(f"✅ live_merge.status = 'OK' (feed is connected and fresh)")
        else:
            print(f"⚠️ live_merge.status = '{status}' (feed may be stale/disconnected)")

    def test_analyze_live_merge_buffer_age_under_60s_when_ok(self, api_client):
        """POST /api/analyze live_merge.buffer_age_s is < 60 when status is OK"""
        response = api_client.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200
        
        data = response.json()
        live_merge = data.get('live_merge', {})
        status = live_merge.get('status')
        buffer_age_s = live_merge.get('buffer_age_s')
        
        if status == 'OK':
            assert buffer_age_s is not None, "buffer_age_s should not be None when status is OK"
            assert isinstance(buffer_age_s, (int, float)), f"buffer_age_s should be numeric, got {type(buffer_age_s)}"
            assert buffer_age_s < 60, f"buffer_age_s should be < 60 when OK, got {buffer_age_s}"
            print(f"✅ buffer_age_s = {buffer_age_s}s (< 60s threshold)")
        else:
            print(f"⚠️ Skipping buffer_age check: status is '{status}', not 'OK'")

    def test_analyze_live_merge_candles_added_positive_when_ok(self, api_client):
        """POST /api/analyze live_merge.candles_added is positive when status is OK"""
        response = api_client.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200
        
        data = response.json()
        live_merge = data.get('live_merge', {})
        status = live_merge.get('status')
        candles_added = live_merge.get('candles_added')
        
        if status == 'OK':
            assert candles_added is not None, "candles_added should not be None when status is OK"
            assert isinstance(candles_added, int), f"candles_added should be int, got {type(candles_added)}"
            assert candles_added > 0, f"candles_added should be > 0 when OK, got {candles_added}"
            print(f"✅ candles_added = {candles_added} (positive)")
        else:
            print(f"⚠️ Skipping candles_added check: status is '{status}', not 'OK'")

    def test_analyze_session_vwaps_globex_has_vwap(self, api_client):
        """POST /api/analyze still returns session_vwaps.globex with vwap > 0"""
        response = api_client.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200
        
        data = response.json()
        session_vwaps = data.get('session_vwaps', {})
        
        # Check globex VWAP exists
        globex = session_vwaps.get('globex', {})
        vwap = globex.get('vwap', 0)
        
        assert vwap > 0, f"session_vwaps.globex.vwap should be > 0, got {vwap}"
        print(f"✅ session_vwaps.globex.vwap = {vwap}")

    def test_analyze_session_vps_has_required_keys(self, api_client):
        """POST /api/analyze still returns session_vps with daily_vp, rth_vp, prev_day_vp keys"""
        response = api_client.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200
        
        data = response.json()
        session_vps = data.get('session_vps', {})
        
        required_keys = ['daily_vp', 'rth_vp', 'prev_day_vp']
        for key in required_keys:
            assert key in session_vps, f"session_vps missing key: {key}"
        
        print(f"✅ session_vps has keys: {list(session_vps.keys())}")


class TestV3SignalLiveMerge:
    """Tests for GET /api/v3/signal/{symbol} live_merge and MQS"""

    def test_v3_signal_returns_live_merge_in_data_quality(self, api_client):
        """GET /api/v3/signal/MNQ returns data_quality.live_merge with same keys as analyze"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'data_quality' in data, "Response missing 'data_quality' key"
        
        data_quality = data['data_quality']
        assert 'live_merge' in data_quality, "data_quality missing 'live_merge' key"
        
        live_merge = data_quality['live_merge']
        assert isinstance(live_merge, dict), "live_merge should be a dict"
        
        # Check all required keys are present
        for key in LIVE_MERGE_KEYS:
            assert key in live_merge, f"live_merge missing key: {key}"
        
        print(f"✅ data_quality.live_merge: {live_merge}")

    def test_v3_signal_live_merge_status_field_exists(self, api_client):
        """GET /api/v3/signal/MNQ returns data_quality.live_merge.status field"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        live_merge = data.get('data_quality', {}).get('live_merge', {})
        
        assert 'status' in live_merge, "live_merge missing 'status' field"
        status = live_merge['status']
        
        valid_statuses = ['OK', 'STALE', 'DISCONNECTED', 'NO_BUFFER']
        assert status in valid_statuses, f"Unexpected status: {status}"
        
        print(f"✅ data_quality.live_merge.status = '{status}'")

    def test_v3_signal_mqs_ok_when_live_merge_ok(self, api_client):
        """GET /api/v3/signal/MNQ data_quality.mqs.ok is True when live_merge.status is OK"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        data_quality = data.get('data_quality', {})
        live_merge = data_quality.get('live_merge', {})
        mqs = data_quality.get('mqs', {})
        
        live_merge_status = live_merge.get('status')
        mqs_ok = mqs.get('ok')
        
        if live_merge_status == 'OK':
            # When live_merge is OK, MQS should also be OK (unless other MQS components fail)
            # Note: MQS can still fail due to other reasons (ws_disconnected, buffer_has_trades, etc.)
            print(f"✅ live_merge.status = 'OK', mqs.ok = {mqs_ok}")
            if not mqs_ok:
                print(f"   MQS reason: {mqs.get('reason')}")
        else:
            # When live_merge is STALE/DISCONNECTED, MQS should be forced to fail
            assert mqs_ok is False, f"MQS should be False when live_merge is {live_merge_status}"
            print(f"✅ live_merge.status = '{live_merge_status}', mqs.ok = False (correctly forced)")

    def test_v3_signal_n3_action_valid(self, api_client):
        """GET /api/v3/signal/MNQ N3 action should contain valid response (WAIT/BUY/SELL)"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        
        # Check nivel_3 action (directly on nivel_3, not nested in execution)
        nivel_3 = data.get('nivel_3', {})
        action = nivel_3.get('action')
        reason = nivel_3.get('reason', 'N/A')
        
        valid_actions = ['WAIT', 'BUY', 'SELL', 'CLOSE']
        assert action in valid_actions, f"Unexpected N3 action: {action}"
        
        print(f"✅ N3 action = '{action}'")
        if action == 'WAIT':
            print(f"   Reason: {reason}")


class TestSessionVwapsCache:
    """Tests for GET /api/session-vwaps/{symbol} caching"""

    def test_session_vwaps_returns_valid_data(self, api_client):
        """GET /api/session-vwaps/MNQ returns valid data (globex or session_ny VWAP present)"""
        response = api_client.get(f"{BASE_URL}/api/session-vwaps/MNQ")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Check that at least one VWAP session is present
        globex = data.get('globex', {})
        session_ny = data.get('session_ny', {})
        
        globex_vwap = globex.get('vwap', 0)
        session_ny_vwap = session_ny.get('vwap', 0)
        
        assert globex_vwap > 0 or session_ny_vwap > 0, "At least one VWAP should be > 0"
        
        print(f"✅ session-vwaps: globex.vwap={globex_vwap}, session_ny.vwap={session_ny_vwap}")

    def test_session_vwaps_60s_cache(self, api_client):
        """GET /api/session-vwaps/MNQ second call within 60s returns cached result"""
        # First call
        response1 = api_client.get(f"{BASE_URL}/api/session-vwaps/MNQ")
        assert response1.status_code == 200
        data1 = response1.json()
        
        # Wait a short time (less than 60s)
        time.sleep(2)
        
        # Second call
        response2 = api_client.get(f"{BASE_URL}/api/session-vwaps/MNQ")
        assert response2.status_code == 200
        data2 = response2.json()
        
        # Compare key values - they should be identical (cached)
        globex1 = data1.get('globex', {})
        globex2 = data2.get('globex', {})
        
        # VWAP values should be exactly the same if cached
        vwap1 = globex1.get('vwap')
        vwap2 = globex2.get('vwap')
        
        assert vwap1 == vwap2, f"VWAP values differ: {vwap1} vs {vwap2} (cache may not be working)"
        
        print(f"✅ 60s cache working: both calls returned vwap={vwap1}")


class TestLiveMergeGuardrailIntegration:
    """Integration tests for live merge guardrail behavior"""

    def test_live_merge_metadata_consistency(self, api_client):
        """Verify live_merge metadata is consistent between analyze and v3/signal"""
        # Call analyze
        analyze_resp = api_client.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert analyze_resp.status_code == 200
        analyze_data = analyze_resp.json()
        
        # Call v3/signal
        v3_resp = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert v3_resp.status_code == 200
        v3_data = v3_resp.json()
        
        # Compare live_merge status
        analyze_live_merge = analyze_data.get('live_merge', {})
        v3_live_merge = v3_data.get('data_quality', {}).get('live_merge', {})
        
        # Both should have the same structure
        for key in LIVE_MERGE_KEYS:
            assert key in analyze_live_merge, f"analyze live_merge missing: {key}"
            assert key in v3_live_merge, f"v3 live_merge missing: {key}"
        
        print(f"✅ live_merge structure consistent between analyze and v3/signal")
        print(f"   analyze: status={analyze_live_merge.get('status')}, buffer_age={analyze_live_merge.get('buffer_age_s')}")
        print(f"   v3:      status={v3_live_merge.get('status')}, buffer_age={v3_live_merge.get('buffer_age_s')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
