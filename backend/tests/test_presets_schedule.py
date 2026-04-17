"""
Walk-Forward Optimizer Phase B Tests
=====================================
Tests for:
1. Presets CRUD (POST/GET/PUT/DELETE /api/replay/presets)
2. Schedule CRUD (POST/GET/DELETE /api/replay/schedule)
3. Schedule History (GET /api/replay/schedule/history)
"""

import pytest
import requests
import os
import uuid

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


class TestPresetsCRUD:
    """Preset endpoints tests"""
    
    def test_create_preset_success(self, api_client):
        """POST /api/replay/presets creates preset with name, config, description"""
        unique_name = f"TEST_Preset_{uuid.uuid4().hex[:8]}"
        payload = {
            "name": unique_name,
            "config": {
                "zscore_min": {"TRANSICAO": 1.2},
                "delta_ratio_min": {"TRANSICAO": 0.25},
                "initial_capital": 30000
            },
            "description": "Test preset for automated testing"
        }
        
        response = api_client.post(f"{BASE_URL}/api/replay/presets", json=payload)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "preset_id" in data, "Response should contain preset_id"
        assert data["name"] == unique_name
        assert data["config"]["initial_capital"] == 30000
        assert "created_at" in data
        assert "updated_at" in data
        
        # Cleanup
        api_client.delete(f"{BASE_URL}/api/replay/presets/{data['preset_id']}")
        print(f"✅ POST /api/replay/presets - Created preset '{unique_name}' with preset_id={data['preset_id']}")
    
    def test_create_preset_duplicate_name_returns_409(self, api_client):
        """POST /api/replay/presets with duplicate name returns 409"""
        unique_name = f"TEST_Dup_{uuid.uuid4().hex[:8]}"
        payload = {"name": unique_name, "config": {"initial_capital": 25000}, "description": ""}
        
        # Create first preset
        resp1 = api_client.post(f"{BASE_URL}/api/replay/presets", json=payload)
        assert resp1.status_code == 200
        preset_id = resp1.json()["preset_id"]
        
        # Try to create duplicate
        resp2 = api_client.post(f"{BASE_URL}/api/replay/presets", json=payload)
        assert resp2.status_code == 409, f"Expected 409 for duplicate, got {resp2.status_code}"
        
        # Cleanup
        api_client.delete(f"{BASE_URL}/api/replay/presets/{preset_id}")
        print(f"✅ POST /api/replay/presets with duplicate name returns 409")
    
    def test_list_presets(self, api_client):
        """GET /api/replay/presets lists all saved presets"""
        response = api_client.get(f"{BASE_URL}/api/replay/presets")
        assert response.status_code == 200
        
        data = response.json()
        assert "presets" in data
        assert isinstance(data["presets"], list)
        print(f"✅ GET /api/replay/presets - Found {len(data['presets'])} presets")
    
    def test_get_preset_by_id(self, api_client):
        """GET /api/replay/presets/{preset_id} returns specific preset"""
        # Create a preset first
        unique_name = f"TEST_Get_{uuid.uuid4().hex[:8]}"
        create_resp = api_client.post(f"{BASE_URL}/api/replay/presets", json={
            "name": unique_name,
            "config": {"initial_capital": 35000},
            "description": "Test get by ID"
        })
        assert create_resp.status_code == 200
        preset_id = create_resp.json()["preset_id"]
        
        # Get by ID
        get_resp = api_client.get(f"{BASE_URL}/api/replay/presets/{preset_id}")
        assert get_resp.status_code == 200
        
        data = get_resp.json()
        assert data["preset_id"] == preset_id
        assert data["name"] == unique_name
        assert data["config"]["initial_capital"] == 35000
        
        # Cleanup
        api_client.delete(f"{BASE_URL}/api/replay/presets/{preset_id}")
        print(f"✅ GET /api/replay/presets/{preset_id} - Retrieved preset successfully")
    
    def test_get_preset_not_found(self, api_client):
        """GET /api/replay/presets/{preset_id} returns 404 for non-existent"""
        response = api_client.get(f"{BASE_URL}/api/replay/presets/nonexistent123")
        assert response.status_code == 404
        print(f"✅ GET /api/replay/presets/nonexistent returns 404")
    
    def test_update_preset(self, api_client):
        """PUT /api/replay/presets/{preset_id} updates preset"""
        # Create preset
        unique_name = f"TEST_Upd_{uuid.uuid4().hex[:8]}"
        create_resp = api_client.post(f"{BASE_URL}/api/replay/presets", json={
            "name": unique_name,
            "config": {"initial_capital": 25000},
            "description": "Original"
        })
        assert create_resp.status_code == 200
        preset_id = create_resp.json()["preset_id"]
        
        # Update
        update_resp = api_client.put(f"{BASE_URL}/api/replay/presets/{preset_id}", json={
            "name": f"{unique_name}_Updated",
            "config": {"initial_capital": 50000},
            "description": "Updated description"
        })
        assert update_resp.status_code == 200
        
        data = update_resp.json()
        assert data["name"] == f"{unique_name}_Updated"
        assert data["config"]["initial_capital"] == 50000
        assert data["description"] == "Updated description"
        
        # Verify with GET
        get_resp = api_client.get(f"{BASE_URL}/api/replay/presets/{preset_id}")
        assert get_resp.json()["config"]["initial_capital"] == 50000
        
        # Cleanup
        api_client.delete(f"{BASE_URL}/api/replay/presets/{preset_id}")
        print(f"✅ PUT /api/replay/presets/{preset_id} - Updated preset successfully")
    
    def test_delete_preset(self, api_client):
        """DELETE /api/replay/presets/{preset_id} deletes preset"""
        # Create preset
        unique_name = f"TEST_Del_{uuid.uuid4().hex[:8]}"
        create_resp = api_client.post(f"{BASE_URL}/api/replay/presets", json={
            "name": unique_name,
            "config": {},
            "description": ""
        })
        assert create_resp.status_code == 200
        preset_id = create_resp.json()["preset_id"]
        
        # Delete
        del_resp = api_client.delete(f"{BASE_URL}/api/replay/presets/{preset_id}")
        assert del_resp.status_code == 200
        
        data = del_resp.json()
        assert data["deleted"] == True
        assert data["preset_id"] == preset_id
        
        # Verify deleted
        get_resp = api_client.get(f"{BASE_URL}/api/replay/presets/{preset_id}")
        assert get_resp.status_code == 404
        
        print(f"✅ DELETE /api/replay/presets/{preset_id} - Deleted preset successfully")
    
    def test_delete_preset_not_found(self, api_client):
        """DELETE /api/replay/presets/{preset_id} returns 404 for non-existent"""
        response = api_client.delete(f"{BASE_URL}/api/replay/presets/nonexistent456")
        assert response.status_code == 404
        print(f"✅ DELETE /api/replay/presets/nonexistent returns 404")


class TestScheduleCRUD:
    """Schedule endpoints tests"""
    
    def test_create_schedule(self, api_client):
        """POST /api/replay/schedule creates/updates schedule and returns next_run_at"""
        payload = {
            "enabled": True,
            "frequency": "weekly",
            "day_of_week": 6,  # Sunday
            "hour_utc": 6,
            "grid_config": {
                "zscore_min": {"min": 0.5, "max": 1.5, "steps": 3},
                "delta_ratio_min": {"min": 0.10, "max": 0.30, "steps": 3},
                "ofi_threshold": {"min": 0.1, "max": 0.4, "steps": 2},
                "sl_atr_mult": {"min": 0.5, "max": 1.5, "steps": 2},
                "regime": "TRANSICAO"
            },
            "objective": "sharpe",
            "auto_apply": False,
            "improvement_threshold_pct": 10.0
        }
        
        response = api_client.post(f"{BASE_URL}/api/replay/schedule", json=payload)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["active"] == True
        assert data["frequency"] == "weekly"
        assert data["day_of_week"] == 6
        assert data["hour_utc"] == 6
        assert data["objective"] == "sharpe"
        assert data["auto_apply"] == False
        assert "next_run_at" in data, "Response should contain next_run_at"
        assert "created_at" in data
        
        print(f"✅ POST /api/replay/schedule - Created schedule, next_run_at={data['next_run_at']}")
    
    def test_create_schedule_daily(self, api_client):
        """POST /api/replay/schedule with frequency=daily"""
        payload = {
            "enabled": True,
            "frequency": "daily",
            "hour_utc": 8,
            "grid_config": {
                "zscore_min": {"min": 0.5, "max": 1.0, "steps": 2},
                "delta_ratio_min": {"min": 0.10, "max": 0.20, "steps": 2},
                "ofi_threshold": {"min": 0.1, "max": 0.2, "steps": 2},
                "sl_atr_mult": {"min": 0.5, "max": 1.0, "steps": 2},
                "regime": "TRANSICAO"
            },
            "objective": "sortino",
            "auto_apply": True,
            "improvement_threshold_pct": 15.0
        }
        
        response = api_client.post(f"{BASE_URL}/api/replay/schedule", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["frequency"] == "daily"
        assert data["auto_apply"] == True
        assert data["improvement_threshold_pct"] == 15.0
        
        print(f"✅ POST /api/replay/schedule with frequency=daily - Created successfully")
    
    def test_get_schedule(self, api_client):
        """GET /api/replay/schedule returns active schedule configuration"""
        # First create a schedule
        api_client.post(f"{BASE_URL}/api/replay/schedule", json={
            "enabled": True,
            "frequency": "weekly",
            "day_of_week": 5,
            "hour_utc": 10,
            "grid_config": {"zscore_min": {"min": 0.5, "max": 1.0, "steps": 2}},
            "objective": "profit_factor",
            "auto_apply": False,
            "improvement_threshold_pct": 10.0
        })
        
        response = api_client.get(f"{BASE_URL}/api/replay/schedule")
        assert response.status_code == 200
        
        data = response.json()
        # Should have schedule data or message about no schedule
        if data.get("active"):
            assert "frequency" in data
            assert "next_run_at" in data
            print(f"✅ GET /api/replay/schedule - Active schedule: {data['frequency']}, next_run={data['next_run_at']}")
        else:
            print(f"✅ GET /api/replay/schedule - No active schedule (enabled=False)")
    
    def test_disable_schedule(self, api_client):
        """DELETE /api/replay/schedule disables the active schedule"""
        # First create a schedule
        api_client.post(f"{BASE_URL}/api/replay/schedule", json={
            "enabled": True,
            "frequency": "weekly",
            "day_of_week": 6,
            "hour_utc": 6,
            "grid_config": {"zscore_min": {"min": 0.5, "max": 1.0, "steps": 2}},
            "objective": "sharpe",
            "auto_apply": False,
            "improvement_threshold_pct": 10.0
        })
        
        # Disable
        response = api_client.delete(f"{BASE_URL}/api/replay/schedule")
        assert response.status_code == 200
        
        data = response.json()
        assert data["enabled"] == False
        
        # Verify disabled
        get_resp = api_client.get(f"{BASE_URL}/api/replay/schedule")
        get_data = get_resp.json()
        # Either no active schedule or enabled=False
        assert get_data.get("enabled") == False or get_data.get("active") == False or "No schedule" in get_data.get("message", "")
        
        print(f"✅ DELETE /api/replay/schedule - Schedule disabled successfully")
    
    def test_get_schedule_history(self, api_client):
        """GET /api/replay/schedule/history returns execution history"""
        response = api_client.get(f"{BASE_URL}/api/replay/schedule/history")
        assert response.status_code == 200
        
        data = response.json()
        assert "history" in data
        assert isinstance(data["history"], list)
        assert "total" in data
        
        if len(data["history"]) > 0:
            entry = data["history"][0]
            # Check expected fields in history entry
            expected_fields = ["run_id", "executed_at", "objective", "status"]
            for field in expected_fields:
                if field in entry:
                    print(f"  - History entry has {field}: {entry[field]}")
        
        print(f"✅ GET /api/replay/schedule/history - Found {data['total']} history entries")
    
    def test_schedule_with_auto_apply(self, api_client):
        """POST /api/replay/schedule with auto_apply=True and threshold"""
        payload = {
            "enabled": True,
            "frequency": "weekly",
            "day_of_week": 6,
            "hour_utc": 6,
            "grid_config": {
                "zscore_min": {"min": 0.5, "max": 1.5, "steps": 3},
                "delta_ratio_min": {"min": 0.10, "max": 0.30, "steps": 3},
                "ofi_threshold": {"min": 0.1, "max": 0.4, "steps": 2},
                "sl_atr_mult": {"min": 0.5, "max": 1.5, "steps": 2},
                "regime": "TRANSICAO"
            },
            "objective": "sharpe",
            "auto_apply": True,
            "improvement_threshold_pct": 20.0
        }
        
        response = api_client.post(f"{BASE_URL}/api/replay/schedule", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["auto_apply"] == True
        assert data["improvement_threshold_pct"] == 20.0
        
        print(f"✅ POST /api/replay/schedule with auto_apply=True, threshold=20%")


class TestResetDefaults:
    """Test GET /api/replay/defaults for Reset functionality"""
    
    def test_get_defaults(self, api_client):
        """GET /api/replay/defaults returns default configuration"""
        response = api_client.get(f"{BASE_URL}/api/replay/defaults")
        assert response.status_code == 200
        
        data = response.json()
        assert "config" in data
        
        config = data["config"]
        # Check some expected default fields
        expected_fields = ["initial_capital", "contracts_per_signal", "sl_atr_mult"]
        for field in expected_fields:
            assert field in config, f"Default config should have {field}"
        
        print(f"✅ GET /api/replay/defaults - Returns default config with {len(config)} fields")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
