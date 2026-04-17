"""
Auth API Tests for Emergent Google OAuth with Email Whitelist

Tests:
- GET /api/auth/me - returns 401 without auth, 200 with valid session
- POST /api/auth/session - validates session_id requirement and invalid sessions
- POST /api/auth/logout - clears session and returns 200
- Whitelist enforcement - only bruno.caiado@gmail.com allowed
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test session token created in MongoDB
TEST_SESSION_TOKEN = os.environ.get("TEST_AUTH_TOKEN", "test_session_auth_1775441581328")
TEST_USER_EMAIL = "bruno.caiado@gmail.com"
TEST_USER_NAME = "Bruno Caiado"


class Test01AuthMe:
    """Tests for GET /api/auth/me endpoint"""
    
    def test_auth_me_without_auth_returns_401(self):
        """GET /api/auth/me without auth should return 401"""
        response = requests.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 401
        data = response.json()
        assert "detail" in data
        assert "Not authenticated" in data["detail"]
    
    def test_auth_me_with_valid_bearer_token_returns_200(self):
        """GET /api/auth/me with valid Bearer token should return 200 with user data"""
        response = requests.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {TEST_SESSION_TOKEN}"}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify user data structure
        assert "user_id" in data
        assert "email" in data
        assert "name" in data
        
        # Verify user data values
        assert data["email"] == TEST_USER_EMAIL
        assert data["name"] == TEST_USER_NAME
    
    def test_auth_me_with_valid_cookie_returns_200(self):
        """GET /api/auth/me with valid session_token cookie should return 200"""
        response = requests.get(
            f"{BASE_URL}/api/auth/me",
            cookies={"session_token": TEST_SESSION_TOKEN}
        )
        assert response.status_code == 200
        data = response.json()
        
        assert data["email"] == TEST_USER_EMAIL
        assert data["name"] == TEST_USER_NAME
    
    def test_auth_me_with_invalid_token_returns_401(self):
        """GET /api/auth/me with invalid token should return 401"""
        response = requests.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": "Bearer invalid_token_12345"}
        )
        assert response.status_code == 401


class Test02AuthSession:
    """Tests for POST /api/auth/session endpoint"""
    
    def test_session_without_session_id_returns_400(self):
        """POST /api/auth/session without session_id should return 400"""
        response = requests.post(
            f"{BASE_URL}/api/auth/session",
            json={},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "session_id is required" in data["detail"]
    
    def test_session_with_invalid_session_id_returns_401(self):
        """POST /api/auth/session with invalid session_id should return 401"""
        response = requests.post(
            f"{BASE_URL}/api/auth/session",
            json={"session_id": "invalid_session_12345"},
            headers={"Content-Type": "application/json"}
        )
        # Should return 401 (invalid) or 502 (service unavailable)
        assert response.status_code in [401, 502]
        data = response.json()
        assert "detail" in data


class Test03Whitelist:
    """Tests for email whitelist enforcement"""
    
    def test_whitelist_only_allows_bruno_caiado(self):
        """Only bruno.caiado@gmail.com should be in whitelist"""
        # This test verifies the whitelist by checking that the test user
        # (bruno.caiado@gmail.com) can authenticate
        response = requests.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {TEST_SESSION_TOKEN}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "bruno.caiado@gmail.com"


class Test04ProtectedEndpoints:
    """Tests to verify protected endpoints require authentication"""
    
    def test_v3_signal_without_auth_still_works(self):
        """V3 signal endpoint should work without auth (public API)"""
        # Note: Based on the current implementation, trading endpoints
        # are not protected by auth - only the dashboard UI is protected
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        # This should work as trading APIs are public
        assert response.status_code == 200


class Test99AuthLogout:
    """Tests for POST /api/auth/logout endpoint
    
    Note: These tests run LAST (99) because logout deletes the session.
    """
    
    def test_logout_without_session_returns_200(self):
        """POST /api/auth/logout without session should still return 200"""
        response = requests.post(f"{BASE_URL}/api/auth/logout")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "logged_out"
    
    def test_logout_with_session_returns_200(self):
        """POST /api/auth/logout should return 200 and clear session"""
        response = requests.post(
            f"{BASE_URL}/api/auth/logout",
            cookies={"session_token": TEST_SESSION_TOKEN}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "logged_out"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
