"""
Test suite for Code Quality Refactoring verification.
Verifies that backend APIs still work correctly after Python linter fixes.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestCodeQualityRefactor:
    """Tests to verify backend APIs work after code quality fixes"""
    
    def test_health_endpoint(self):
        """GET /api/health should return 200"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert data.get('status') == 'healthy'
        print(f"✅ Health check passed: {data}")
    
    def test_v3_signal_mnq(self):
        """GET /api/v3/signal/MNQ should return V3 signal data"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        # Verify structure
        assert 'nivel_1' in data
        assert 'nivel_2' in data
        assert 'nivel_3' in data
        assert 'v3_status' in data
        print(f"✅ V3 Signal MNQ: status={data.get('v3_status')}, regime={data.get('nivel_1', {}).get('regime')}")
    
    def test_v3_signal_mes(self):
        """GET /api/v3/signal/MES should return V3 signal data"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert response.status_code == 200
        data = response.json()
        assert 'nivel_1' in data
        assert 'v3_status' in data
        print(f"✅ V3 Signal MES: status={data.get('v3_status')}, regime={data.get('nivel_1', {}).get('regime')}")
    
    def test_feed_health(self):
        """GET /api/feed/health should return feed status"""
        response = requests.get(f"{BASE_URL}/api/feed/health", timeout=10)
        assert response.status_code == 200
        data = response.json()
        # Should have MNQ and MES
        assert 'MNQ' in data or 'MES' in data
        print(f"✅ Feed health: {list(data.keys())}")
    
    def test_trading_calendar_status(self):
        """GET /api/trading-calendar/status should return trading status"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/status", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert 'status' in data or 'session_type' in data or 'is_trading_hours' in data
        print(f"✅ Trading calendar: {data.get('status', data.get('session_type', 'OK'))}")
    
    def test_autotrading_config(self):
        """GET /api/autotrading/config should return config"""
        response = requests.get(f"{BASE_URL}/api/autotrading/config", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert 'config' in data
        print(f"✅ AutoTrading config: enabled={data.get('config', {}).get('enabled')}")
    
    def test_signalstack_symbols(self):
        """GET /api/signalstack/symbols should return symbol mapping"""
        response = requests.get(f"{BASE_URL}/api/signalstack/symbols", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert 'symbols' in data
        print(f"✅ SignalStack symbols: {list(data.get('symbols', {}).keys())}")
    
    def test_positions_active(self):
        """GET /api/positions/active/MNQ should return positions"""
        response = requests.get(f"{BASE_URL}/api/positions/active/MNQ", timeout=10)
        assert response.status_code == 200
        data = response.json()
        # Can be empty or have positions
        assert isinstance(data, (dict, list))
        print(f"✅ Active positions MNQ: {type(data).__name__}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
