"""
Feed Health Monitor Tests
=========================
Tests for the Feed Health Monitor feature that distinguishes between:
- Technical failures (DEAD/STALE - red/orange pulsing)
- Expected market closures (CLOSED - amber fixed)
- Active feed (LIVE - green pulsing)

CME Globex Schedule:
- Open: Sunday 18:00 ET → Friday 17:00 ET
- Daily Halt: 17:00-18:00 ET (Mon-Thu)
- Weekend: Friday 17:00 ET → Sunday 18:00 ET
"""

import pytest
import requests
import os
from datetime import datetime, timezone, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")

@pytest.fixture
def api_client():
    """Shared requests session with auth"""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SESSION_TOKEN}",
        "Cookie": f"session_token={SESSION_TOKEN}"
    })
    return session


class TestFeedHealthEndpoints:
    """Test /api/feed/health endpoints"""
    
    def test_get_feed_health_current_state(self, api_client):
        """GET /api/feed/health returns current state for MNQ and MES"""
        response = api_client.get(f"{BASE_URL}/api/feed/health")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Should have MNQ and MES keys
        assert 'MNQ' in data or 'MES' in data, f"Expected MNQ or MES in response: {data}"
        
        # Check structure for each symbol
        for symbol in ['MNQ', 'MES']:
            if symbol in data:
                state_data = data[symbol]
                assert 'state' in state_data, f"Missing 'state' for {symbol}"
                assert 'reason' in state_data, f"Missing 'reason' for {symbol}"
                assert 'market' in state_data, f"Missing 'market' for {symbol}"
                
                # State should be one of the 4 valid states
                valid_states = ['LIVE', 'CLOSED', 'STALE', 'DEAD']
                assert state_data['state'] in valid_states, f"Invalid state: {state_data['state']}"
                
                # Market info should have 'open' and 'reason'
                market = state_data['market']
                assert 'open' in market, f"Missing 'open' in market for {symbol}"
                assert 'reason' in market, f"Missing 'reason' in market for {symbol}"
                
                print(f"✅ {symbol}: state={state_data['state']}, reason={state_data['reason']}")
    
    def test_get_feed_health_report_mnq(self, api_client):
        """GET /api/feed/health/report/MNQ returns report with expected fields"""
        response = api_client.get(f"{BASE_URL}/api/feed/health/report/MNQ?hours=1")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Required fields in report
        required_fields = ['symbol', 'period_hours', 'total_checks', 'states', 'uptime_pct', 'incidents', 'timeline']
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
        
        assert data['symbol'] == 'MNQ', f"Expected symbol MNQ, got {data['symbol']}"
        assert data['period_hours'] == 1, f"Expected period_hours 1, got {data['period_hours']}"
        
        # uptime_pct should be a number between 0 and 100
        assert isinstance(data['uptime_pct'], (int, float)), f"uptime_pct should be numeric"
        assert 0 <= data['uptime_pct'] <= 100, f"uptime_pct out of range: {data['uptime_pct']}"
        
        # states should be a dict
        assert isinstance(data['states'], dict), f"states should be a dict"
        
        # incidents should be a list
        assert isinstance(data['incidents'], list), f"incidents should be a list"
        
        # timeline should be a list
        assert isinstance(data['timeline'], list), f"timeline should be a list"
        
        print(f"✅ MNQ Report: total_checks={data['total_checks']}, uptime_pct={data['uptime_pct']}%")
        print(f"   States: {data['states']}")
        print(f"   Incidents: {len(data['incidents'])}")
    
    def test_get_feed_health_report_mes(self, api_client):
        """GET /api/feed/health/report/MES returns report"""
        response = api_client.get(f"{BASE_URL}/api/feed/health/report/MES?hours=24")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data['symbol'] == 'MES', f"Expected symbol MES, got {data['symbol']}"
        assert data['period_hours'] == 24, f"Expected period_hours 24, got {data['period_hours']}"
        
        print(f"✅ MES Report: total_checks={data['total_checks']}, uptime_pct={data['uptime_pct']}%")


class TestCMEScheduleLogic:
    """Test CME Globex schedule detection"""
    
    def test_feed_health_during_maintenance_window(self, api_client):
        """During 17:xx ET (Mon-Thu), state should be CLOSED with maintenance reason"""
        response = api_client.get(f"{BASE_URL}/api/feed/health")
        assert response.status_code == 200
        
        data = response.json()
        
        # Get current ET time to check if we're in maintenance window
        now_utc = datetime.now(timezone.utc)
        # Approximate ET offset (EDT = UTC-4, EST = UTC-5)
        month = now_utc.month
        et_offset = timedelta(hours=-4) if 4 <= month <= 10 else timedelta(hours=-5)
        now_et = now_utc + et_offset
        
        weekday = now_et.weekday()  # 0=Mon, 6=Sun
        hour = now_et.hour
        
        print(f"Current ET time: {now_et.strftime('%A %H:%M')} (weekday={weekday}, hour={hour})")
        
        # Check if we're in maintenance window (17:xx ET, Mon-Thu)
        if weekday in (0, 1, 2, 3) and hour == 17:
            # Should be CLOSED with maintenance reason
            for symbol in ['MNQ', 'MES']:
                if symbol in data:
                    state_data = data[symbol]
                    assert state_data['state'] == 'CLOSED', f"Expected CLOSED during maintenance, got {state_data['state']}"
                    assert 'manutencao' in state_data['reason'].lower() or 'maintenance' in state_data['reason'].lower(), \
                        f"Expected maintenance reason, got: {state_data['reason']}"
                    print(f"✅ {symbol}: Correctly shows CLOSED during maintenance window")
        else:
            print(f"⏭️ Not in maintenance window (17:xx ET Mon-Thu), skipping maintenance check")
    
    def test_feed_health_weekend_detection(self, api_client):
        """On weekends, state should be CLOSED with weekend reason"""
        response = api_client.get(f"{BASE_URL}/api/feed/health")
        assert response.status_code == 200
        
        data = response.json()
        
        # Get current ET time
        now_utc = datetime.now(timezone.utc)
        month = now_utc.month
        et_offset = timedelta(hours=-4) if 4 <= month <= 10 else timedelta(hours=-5)
        now_et = now_utc + et_offset
        
        weekday = now_et.weekday()
        hour = now_et.hour
        
        # Check if we're on weekend (Sat, or Sun before 18:00, or Fri after 17:00)
        is_weekend = (
            weekday == 5 or  # Saturday
            (weekday == 6 and hour < 18) or  # Sunday before 18:00
            (weekday == 4 and hour >= 17)  # Friday after 17:00
        )
        
        if is_weekend:
            for symbol in ['MNQ', 'MES']:
                if symbol in data:
                    state_data = data[symbol]
                    assert state_data['state'] == 'CLOSED', f"Expected CLOSED on weekend, got {state_data['state']}"
                    print(f"✅ {symbol}: Correctly shows CLOSED on weekend")
        else:
            print(f"⏭️ Not on weekend, skipping weekend check")


class TestFeedHealthInSignalEndpoint:
    """Test feed_health is included in /api/v3/signal response"""
    
    def test_signal_includes_feed_health(self, api_client):
        """GET /api/v3/signal/MNQ should include data_quality.feed_health"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Check data_quality exists
        assert 'data_quality' in data, f"Missing 'data_quality' in signal response"
        
        dq = data['data_quality']
        
        # Check feed_health exists in data_quality
        assert 'feed_health' in dq, f"Missing 'feed_health' in data_quality: {dq.keys()}"
        
        feed_health = dq['feed_health']
        
        # If feed_health has data, check structure
        if feed_health:
            # Should have state and reason at minimum
            if 'state' in feed_health:
                valid_states = ['LIVE', 'CLOSED', 'STALE', 'DEAD']
                assert feed_health['state'] in valid_states, f"Invalid feed_health state: {feed_health['state']}"
                print(f"✅ Signal includes feed_health: state={feed_health.get('state')}, reason={feed_health.get('reason', 'N/A')}")
            else:
                print(f"✅ Signal includes feed_health (empty or initializing): {feed_health}")
        else:
            print(f"✅ Signal includes feed_health (empty - monitor may be initializing)")


class TestFeedHealthMonitorPersistence:
    """Test that feed health data is being persisted to MongoDB"""
    
    def test_report_has_data_after_server_running(self, api_client):
        """Report should have data if server has been running for a while"""
        # Get 1-hour report
        response = api_client.get(f"{BASE_URL}/api/feed/health/report/MNQ?hours=1")
        assert response.status_code == 200
        
        data = response.json()
        
        # If server has been running, we should have some checks
        # Note: This may be 0 if server just started
        total_checks = data.get('total_checks', 0)
        
        if total_checks > 0:
            print(f"✅ MongoDB persistence working: {total_checks} checks in last hour")
            
            # Verify timeline has data
            timeline = data.get('timeline', [])
            assert len(timeline) > 0, "Expected timeline data when total_checks > 0"
            
            # Verify states distribution
            states = data.get('states', {})
            assert len(states) > 0, "Expected states distribution when total_checks > 0"
            
            # Verify market_open_checks is present
            assert 'market_open_checks' in data, "Missing market_open_checks in report"
        else:
            print(f"⚠️ No checks in last hour - server may have just started or monitor not running")
            print(f"   This is expected if the server was recently restarted")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
