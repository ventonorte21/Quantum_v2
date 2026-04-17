"""
Trading Calendar & Hours Management Tests
==========================================
Tests for NYSE trading hours, DST, holidays, news blackout, EOD flatten, and flatten-all.
Iteration 29 - Weekend scenario (Saturday).
"""

import pytest
import requests
import os
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestTradingCalendarSession:
    """Tests for GET /api/trading-calendar/session"""

    def test_session_returns_required_fields(self):
        """Session endpoint returns all required fields"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/session")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        # Required fields
        required_fields = [
            'is_trading_day', 'is_weekend', 'is_dst', 'is_early_close',
            'market_open_utc', 'market_close_utc', 'next_session', 'date'
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        print(f"✅ Session endpoint returns all required fields")
        print(f"   is_trading_day={data['is_trading_day']}, is_weekend={data['is_weekend']}")
        print(f"   is_dst={data['is_dst']}, is_early_close={data['is_early_close']}")

    def test_session_weekend_detection(self):
        """On weekend, is_weekend=true and is_trading_day=false"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/session")
        assert response.status_code == 200
        
        data = response.json()
        
        # Today is Saturday (weekend)
        assert data['is_weekend'] == True, f"Expected is_weekend=True, got {data['is_weekend']}"
        assert data['is_trading_day'] == False, f"Expected is_trading_day=False, got {data['is_trading_day']}"
        
        print(f"✅ Weekend correctly detected: is_weekend={data['is_weekend']}, is_trading_day={data['is_trading_day']}")

    def test_session_dst_field(self):
        """Session returns is_dst field (DST-aware)"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/session")
        assert response.status_code == 200
        
        data = response.json()
        
        assert 'is_dst' in data, "Missing is_dst field"
        assert isinstance(data['is_dst'], bool), f"is_dst should be bool, got {type(data['is_dst'])}"
        
        # April 2026 should be DST (EDT)
        print(f"✅ DST field present: is_dst={data['is_dst']}")
        print(f"   tz_offset={data.get('tz_offset', 'N/A')}")

    def test_session_next_session(self):
        """Session returns next_session info"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/session")
        assert response.status_code == 200
        
        data = response.json()
        
        assert 'next_session' in data, "Missing next_session field"
        
        if data['next_session']:
            assert 'date' in data['next_session'], "next_session missing date"
            assert 'open_utc' in data['next_session'], "next_session missing open_utc"
            assert 'close_utc' in data['next_session'], "next_session missing close_utc"
            print(f"✅ Next session: {data['next_session']['date']}")
        else:
            print(f"⚠️ next_session is None (may be expected at end of calendar)")


class TestTradingCalendarStatus:
    """Tests for GET /api/trading-calendar/status"""

    def test_status_returns_required_fields(self):
        """Status endpoint returns all required fields"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        required_fields = ['can_trade', 'status', 'reason', 'session', 'config']
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        print(f"✅ Status endpoint returns all required fields")
        print(f"   can_trade={data['can_trade']}, status={data['status']}")

    def test_status_weekend_closed(self):
        """On weekend, can_trade=false and status=CLOSED"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/status")
        assert response.status_code == 200
        
        data = response.json()
        
        assert data['can_trade'] == False, f"Expected can_trade=False on weekend, got {data['can_trade']}"
        assert data['status'] == 'CLOSED', f"Expected status=CLOSED, got {data['status']}"
        assert 'Fim de semana' in data['reason'] or 'weekend' in data['reason'].lower(), \
            f"Reason should mention weekend, got: {data['reason']}"
        
        print(f"✅ Weekend status correct: can_trade={data['can_trade']}, status={data['status']}")
        print(f"   reason={data['reason']}")

    def test_status_config_fields(self):
        """Status returns config with auto_hours_mode, news_blackout_enabled, eod_flatten_enabled"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/status")
        assert response.status_code == 200
        
        data = response.json()
        
        assert 'config' in data, "Missing config field"
        config = data['config']
        
        required_config_fields = [
            'auto_hours_mode', 'news_blackout_enabled', 'eod_flatten_enabled', 'pre_close_flatten_minutes'
        ]
        for field in required_config_fields:
            assert field in config, f"Missing config field: {field}"
        
        print(f"✅ Config fields present:")
        print(f"   auto_hours_mode={config['auto_hours_mode']}")
        print(f"   news_blackout_enabled={config['news_blackout_enabled']}")
        print(f"   eod_flatten_enabled={config['eod_flatten_enabled']}")
        print(f"   pre_close_flatten_minutes={config['pre_close_flatten_minutes']}")


class TestTradingCalendarNews:
    """Tests for GET /api/trading-calendar/news"""

    def test_news_returns_required_fields(self):
        """News endpoint returns events array, is_blackout, config"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/news")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        required_fields = ['events', 'is_blackout', 'config']
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        assert isinstance(data['events'], list), f"events should be list, got {type(data['events'])}"
        assert isinstance(data['is_blackout'], bool), f"is_blackout should be bool, got {type(data['is_blackout'])}"
        
        print(f"✅ News endpoint returns required fields")
        print(f"   events count={len(data['events'])}, is_blackout={data['is_blackout']}")

    def test_news_config_fields(self):
        """News config contains blackout settings"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/news")
        assert response.status_code == 200
        
        data = response.json()
        config = data.get('config', {})
        
        assert 'news_blackout_enabled' in config, "Missing news_blackout_enabled"
        assert 'minutes_before' in config, "Missing minutes_before"
        assert 'minutes_after' in config, "Missing minutes_after"
        
        print(f"✅ News config fields present:")
        print(f"   news_blackout_enabled={config['news_blackout_enabled']}")
        print(f"   minutes_before={config['minutes_before']}, minutes_after={config['minutes_after']}")


class TestFlattenAll:
    """Tests for POST /api/autotrading/flatten-all"""

    def test_flatten_all_returns_required_fields(self):
        """Flatten-all endpoint returns positions_closed and status"""
        response = requests.post(f"{BASE_URL}/api/autotrading/flatten-all?reason=test_manual")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        assert 'positions_closed' in data, "Missing positions_closed field"
        assert 'status' in data, "Missing status field"
        assert data['status'] == 'flattened', f"Expected status=flattened, got {data['status']}"
        
        print(f"✅ Flatten-all returns required fields")
        print(f"   positions_closed={data['positions_closed']}, status={data['status']}")

    def test_flatten_all_with_reason(self):
        """Flatten-all accepts reason parameter"""
        response = requests.post(f"{BASE_URL}/api/autotrading/flatten-all?reason=manual_test")
        assert response.status_code == 200
        
        data = response.json()
        assert data['status'] == 'flattened'
        
        print(f"✅ Flatten-all with reason=manual_test works")


class TestAutoTradingConfig:
    """Tests for GET /api/autotrading/config"""

    def test_config_returns_new_fields(self):
        """Config contains new trading hours and news blackout fields"""
        response = requests.get(f"{BASE_URL}/api/autotrading/config")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        config = data.get('config', {})
        
        # New fields from iteration 29
        new_fields = [
            'auto_hours_mode',
            'news_blackout_enabled',
            'news_blackout_minutes_before',
            'news_blackout_minutes_after',
            'pre_close_flatten_minutes',
            'eod_flatten_enabled'
        ]
        
        for field in new_fields:
            assert field in config, f"Missing new config field: {field}"
        
        print(f"✅ Config contains all new fields:")
        print(f"   auto_hours_mode={config['auto_hours_mode']}")
        print(f"   news_blackout_enabled={config['news_blackout_enabled']}")
        print(f"   news_blackout_minutes_before={config['news_blackout_minutes_before']}")
        print(f"   news_blackout_minutes_after={config['news_blackout_minutes_after']}")
        print(f"   pre_close_flatten_minutes={config['pre_close_flatten_minutes']}")
        print(f"   eod_flatten_enabled={config['eod_flatten_enabled']}")


class TestAutoTradingExecuteWeekend:
    """Tests for POST /api/autotrading/execute on weekend"""

    def test_execute_returns_outside_hours_on_weekend(self):
        """Execute endpoint returns status=outside_hours on weekend"""
        response = requests.post(f"{BASE_URL}/api/autotrading/execute?symbol=MES")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        # On weekend, should return outside_hours (unless force=true)
        # Note: If auto trading is disabled, it returns 'disabled' first
        valid_statuses = ['outside_hours', 'disabled']
        assert data['status'] in valid_statuses, \
            f"Expected status in {valid_statuses}, got {data['status']}"
        
        if data['status'] == 'outside_hours':
            assert 'Fim de semana' in data.get('message', '') or 'weekend' in data.get('message', '').lower(), \
                f"Message should mention weekend, got: {data.get('message')}"
            print(f"✅ Execute returns outside_hours on weekend")
            print(f"   message={data.get('message')}")
        else:
            print(f"⚠️ Execute returns {data['status']} (auto trading may be disabled)")
            print(f"   message={data.get('message')}")


class TestAutoTradingState:
    """Tests for GET /api/autotrading/state"""

    def test_state_returns_required_fields(self):
        """State endpoint returns required fields"""
        response = requests.get(f"{BASE_URL}/api/autotrading/state")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        state = data.get('state', {})
        
        required_fields = ['is_running', 'open_positions', 'daily_trades', 'daily_pnl']
        for field in required_fields:
            assert field in state, f"Missing state field: {field}"
        
        print(f"✅ State returns required fields")
        print(f"   is_running={state['is_running']}, daily_trades={state['daily_trades']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
