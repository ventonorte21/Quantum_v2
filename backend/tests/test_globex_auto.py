"""
Test Globex Auto Feature - Iteration 55
========================================
Tests for the new Globex Auto trading session feature:
- GET /api/trading-calendar/status returns globex_auto, nyse_auto, session_type, exchange_status
- POST /api/autotrading/config with globex_auto_enabled toggle
- GET /api/autotrading/config returns globex_auto_enabled and globex_flatten_before_ny_minutes
"""

import pytest
import requests
import os
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")


class TestGlobexAutoBackend:
    """Backend API tests for Globex Auto feature"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session"""
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SESSION_TOKEN}"
        }

    # ========== GET /api/trading-calendar/status Tests ==========

    def test_trading_status_returns_globex_auto_field(self):
        """GET /api/trading-calendar/status returns globex_auto field with ok, enabled, reason"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/status", headers=self.headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'globex_auto' in data, "Response missing 'globex_auto' field"
        
        globex_auto = data['globex_auto']
        assert 'ok' in globex_auto, "globex_auto missing 'ok' field"
        assert 'enabled' in globex_auto, "globex_auto missing 'enabled' field"
        assert 'reason' in globex_auto, "globex_auto missing 'reason' field"
        
        assert isinstance(globex_auto['ok'], bool), "globex_auto.ok should be boolean"
        assert isinstance(globex_auto['enabled'], bool), "globex_auto.enabled should be boolean"
        assert isinstance(globex_auto['reason'], str), "globex_auto.reason should be string"
        
        print(f"✅ globex_auto field present: ok={globex_auto['ok']}, enabled={globex_auto['enabled']}, reason='{globex_auto['reason']}'")

    def test_trading_status_returns_nyse_auto_field(self):
        """GET /api/trading-calendar/status returns nyse_auto field with ok, reason"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/status", headers=self.headers)
        assert response.status_code == 200
        
        data = response.json()
        assert 'nyse_auto' in data, "Response missing 'nyse_auto' field"
        
        nyse_auto = data['nyse_auto']
        assert 'ok' in nyse_auto, "nyse_auto missing 'ok' field"
        assert 'reason' in nyse_auto, "nyse_auto missing 'reason' field"
        
        assert isinstance(nyse_auto['ok'], bool), "nyse_auto.ok should be boolean"
        assert isinstance(nyse_auto['reason'], str), "nyse_auto.reason should be string"
        
        print(f"✅ nyse_auto field present: ok={nyse_auto['ok']}, reason='{nyse_auto['reason']}'")

    def test_trading_status_returns_session_type_field(self):
        """GET /api/trading-calendar/status returns session_type field"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/status", headers=self.headers)
        assert response.status_code == 200
        
        data = response.json()
        assert 'session_type' in data, "Response missing 'session_type' field"
        
        session_type = data['session_type']
        assert session_type in ['nyse', 'globex', 'closed'], f"session_type should be 'nyse', 'globex', or 'closed', got '{session_type}'"
        
        print(f"✅ session_type field present: '{session_type}'")

    def test_trading_status_returns_exchange_status_field(self):
        """GET /api/trading-calendar/status returns exchange_status field"""
        response = requests.get(f"{BASE_URL}/api/trading-calendar/status", headers=self.headers)
        assert response.status_code == 200
        
        data = response.json()
        assert 'exchange_status' in data, "Response missing 'exchange_status' field"
        
        exchange_status = data['exchange_status']
        assert isinstance(exchange_status, str), "exchange_status should be string"
        assert exchange_status in ['NYSE', 'Globex', 'Fechado'], f"exchange_status should be 'NYSE', 'Globex', or 'Fechado', got '{exchange_status}'"
        
        print(f"✅ exchange_status field present: '{exchange_status}'")

    # ========== POST /api/autotrading/config Tests ==========

    def test_enable_globex_auto_via_config(self):
        """POST /api/autotrading/config with {globex_auto_enabled: true} enables Globex Auto"""
        # First get current config
        get_response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=self.headers)
        assert get_response.status_code == 200
        current_config = get_response.json().get('config', {})
        
        # Enable globex_auto
        current_config['globex_auto_enabled'] = True
        
        post_response = requests.post(f"{BASE_URL}/api/autotrading/config", json=current_config, headers=self.headers)
        assert post_response.status_code == 200, f"Expected 200, got {post_response.status_code}: {post_response.text}"
        
        # Verify it was saved
        verify_response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=self.headers)
        assert verify_response.status_code == 200
        saved_config = verify_response.json().get('config', {})
        
        assert saved_config.get('globex_auto_enabled') == True, "globex_auto_enabled should be True after enabling"
        
        print("✅ Globex Auto enabled successfully via POST /api/autotrading/config")

    def test_trading_status_with_globex_enabled(self):
        """After enabling Globex Auto, GET /api/trading-calendar/status returns correct session info"""
        # Enable globex_auto first
        get_response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=self.headers)
        current_config = get_response.json().get('config', {})
        current_config['globex_auto_enabled'] = True
        requests.post(f"{BASE_URL}/api/autotrading/config", json=current_config, headers=self.headers)
        
        # Check trading status
        status_response = requests.get(f"{BASE_URL}/api/trading-calendar/status", headers=self.headers)
        assert status_response.status_code == 200
        
        data = status_response.json()
        
        # Verify globex_auto.enabled is True
        assert data['globex_auto']['enabled'] == True, "globex_auto.enabled should be True"
        
        # At ~23:00 ET (Globex session), if globex_auto is enabled and hours are ok, can_trade should be True
        # Note: This depends on current time. During Globex hours (18:00-09:25 ET), globex_auto.ok should be True
        globex_ok = data['globex_auto']['ok']
        nyse_ok = data['nyse_auto']['ok']
        
        print(f"✅ Trading status with Globex enabled: globex_ok={globex_ok}, nyse_ok={nyse_ok}")
        print(f"   can_trade={data['can_trade']}, status={data['status']}, session_type={data['session_type']}")
        
        # If we're in Globex hours and Globex is enabled, session_type should be 'globex' (not 'nyse')
        if globex_ok and not nyse_ok:
            assert data['session_type'] == 'globex', f"session_type should be 'globex' during Globex hours, got '{data['session_type']}'"
            print("✅ session_type correctly shows 'globex' during Globex hours")

    def test_disable_globex_auto_via_config(self):
        """POST /api/autotrading/config with {globex_auto_enabled: false} disables Globex Auto"""
        # Get current config
        get_response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=self.headers)
        current_config = get_response.json().get('config', {})
        
        # Disable globex_auto
        current_config['globex_auto_enabled'] = False
        
        post_response = requests.post(f"{BASE_URL}/api/autotrading/config", json=current_config, headers=self.headers)
        assert post_response.status_code == 200
        
        # Verify it was saved
        verify_response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=self.headers)
        saved_config = verify_response.json().get('config', {})
        
        assert saved_config.get('globex_auto_enabled') == False, "globex_auto_enabled should be False after disabling"
        
        print("✅ Globex Auto disabled successfully via POST /api/autotrading/config")

    def test_trading_status_with_globex_disabled(self):
        """After disabling Globex Auto, can_trade should be false during non-NYSE hours"""
        # Disable globex_auto
        get_response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=self.headers)
        current_config = get_response.json().get('config', {})
        current_config['globex_auto_enabled'] = False
        requests.post(f"{BASE_URL}/api/autotrading/config", json=current_config, headers=self.headers)
        
        # Check trading status
        status_response = requests.get(f"{BASE_URL}/api/trading-calendar/status", headers=self.headers)
        data = status_response.json()
        
        # Verify globex_auto.enabled is False
        assert data['globex_auto']['enabled'] == False, "globex_auto.enabled should be False"
        
        # If NYSE is closed and Globex is disabled, can_trade should be False
        nyse_ok = data['nyse_auto']['ok']
        if not nyse_ok:
            assert data['can_trade'] == False, "can_trade should be False when NYSE closed and Globex disabled"
            print("✅ can_trade correctly False when NYSE closed and Globex disabled")
        else:
            print(f"✅ NYSE is currently open (nyse_ok={nyse_ok}), can_trade={data['can_trade']}")

    # ========== GET /api/autotrading/config Tests ==========

    def test_autotrading_config_returns_globex_fields(self):
        """GET /api/autotrading/config returns globex_auto_enabled and globex_flatten_before_ny_minutes"""
        response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=self.headers)
        assert response.status_code == 200
        
        config = response.json().get('config', {})
        
        assert 'globex_auto_enabled' in config, "Config missing 'globex_auto_enabled' field"
        assert 'globex_flatten_before_ny_minutes' in config, "Config missing 'globex_flatten_before_ny_minutes' field"
        
        assert isinstance(config['globex_auto_enabled'], bool), "globex_auto_enabled should be boolean"
        assert isinstance(config['globex_flatten_before_ny_minutes'], int), "globex_flatten_before_ny_minutes should be int"
        
        print(f"✅ Config has Globex fields: globex_auto_enabled={config['globex_auto_enabled']}, globex_flatten_before_ny_minutes={config['globex_flatten_before_ny_minutes']}")

    def test_globex_flatten_minutes_configurable(self):
        """globex_flatten_before_ny_minutes can be configured via POST"""
        # Get current config
        get_response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=self.headers)
        current_config = get_response.json().get('config', {})
        
        # Set custom flatten minutes
        current_config['globex_flatten_before_ny_minutes'] = 10
        
        post_response = requests.post(f"{BASE_URL}/api/autotrading/config", json=current_config, headers=self.headers)
        assert post_response.status_code == 200
        
        # Verify
        verify_response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=self.headers)
        saved_config = verify_response.json().get('config', {})
        
        assert saved_config.get('globex_flatten_before_ny_minutes') == 10, "globex_flatten_before_ny_minutes should be 10"
        
        # Reset to default
        current_config['globex_flatten_before_ny_minutes'] = 5
        requests.post(f"{BASE_URL}/api/autotrading/config", json=current_config, headers=self.headers)
        
        print("✅ globex_flatten_before_ny_minutes is configurable")


class TestGlobexAutoCleanup:
    """Cleanup: Ensure globex_auto is disabled after tests"""

    def test_cleanup_disable_globex_auto(self):
        """Cleanup: Disable globex_auto_enabled after all tests"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SESSION_TOKEN}"
        }
        
        get_response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=headers)
        current_config = get_response.json().get('config', {})
        
        current_config['globex_auto_enabled'] = False
        current_config['globex_flatten_before_ny_minutes'] = 5  # Reset to default
        
        post_response = requests.post(f"{BASE_URL}/api/autotrading/config", json=current_config, headers=headers)
        assert post_response.status_code == 200
        
        # Verify
        verify_response = requests.get(f"{BASE_URL}/api/autotrading/config", headers=headers)
        saved_config = verify_response.json().get('config', {})
        
        assert saved_config.get('globex_auto_enabled') == False, "Cleanup failed: globex_auto_enabled should be False"
        
        print("✅ Cleanup complete: globex_auto_enabled set to False")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
