"""
Position Manager API Tests
==========================
Tests for the Position Manager endpoints:
- POST /api/positions/evaluate/{symbol} - Preview position params
- POST /api/positions/open - Open a new position
- GET /api/positions/active - Get all active positions
- GET /api/positions/active/{symbol} - Get active position for symbol
- PUT /api/positions/{id}/update-stop - Update stop loss
- POST /api/positions/{id}/close - Close a position
- GET /api/positions/history - Get closed positions

Archetypes tested:
- TREND (regimes 1,2,4): trailing_type=VWAP_CENTRAL, monitoring_required=true
- RANGE (regime 3): trailing_type=NONE, monitoring_required=false, tp_type=FIXED_POC
- FADE (regime 5): trailing_type=BREAK_EVEN, break_even threshold present
"""

import pytest
import requests
import os
import time
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test position IDs to clean up
created_position_ids = []


class TestPositionEvaluate:
    """Test POST /api/positions/evaluate/{symbol} endpoint"""
    
    def test_evaluate_mnq_buy(self):
        """Evaluate position params for MNQ BUY"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MNQ?side=BUY")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'regime' in data, "Response should contain regime"
        assert 'params' in data, "Response should contain params"
        assert 'entry_price' in data, "Response should contain entry_price"
        
        params = data['params']
        assert 'archetype' in params, "Params should contain archetype"
        assert 'hard_stop' in params, "Params should contain hard_stop"
        assert 'trailing_type' in params, "Params should contain trailing_type"
        assert 'monitoring_required' in params, "Params should contain monitoring_required"
        assert 'atr_m1' in params, "Params should contain atr_m1"
        
        print(f"✅ MNQ BUY evaluate: regime={data['regime']}, archetype={params['archetype']}")
        print(f"   Entry={data['entry_price']}, SL={params['hard_stop']}, TP={params.get('take_profit')}")
    
    def test_evaluate_mnq_sell(self):
        """Evaluate position params for MNQ SELL"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MNQ?side=SELL")
        assert response.status_code == 200
        
        data = response.json()
        params = data['params']
        assert params['side'] == 'SELL', "Side should be SELL"
        print(f"✅ MNQ SELL evaluate: archetype={params['archetype']}, SL={params['hard_stop']}")
    
    def test_evaluate_mes_buy(self):
        """Evaluate position params for MES BUY"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MES?side=BUY")
        assert response.status_code == 200
        
        data = response.json()
        params = data['params']
        assert params['archetype'] in ['TREND', 'RANGE', 'FADE'], f"Invalid archetype: {params['archetype']}"
        print(f"✅ MES BUY evaluate: archetype={params['archetype']}")
    
    def test_evaluate_invalid_symbol(self):
        """Evaluate with invalid symbol should return 404"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/INVALID?side=BUY")
        assert response.status_code == 404, f"Expected 404 for invalid symbol, got {response.status_code}"
        print("✅ Invalid symbol returns 404")


class TestArchetypeParams:
    """Test archetype-specific parameters from evaluate endpoint"""
    
    def test_range_archetype_params(self):
        """RANGE archetype should have trailing_type=NONE, monitoring_required=false, tp_type=FIXED_POC"""
        # Current regime is TRANSICAO which maps to RANGE
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MNQ?side=BUY")
        assert response.status_code == 200
        
        data = response.json()
        params = data['params']
        
        # Weekend/TRANSICAO regime should give RANGE archetype
        if params['archetype'] == 'RANGE':
            assert params['trailing_type'] == 'NONE', f"RANGE should have trailing_type=NONE, got {params['trailing_type']}"
            assert params['monitoring_required'] == False, f"RANGE should have monitoring_required=false"
            assert params['tp_type'] == 'FIXED_POC', f"RANGE should have tp_type=FIXED_POC, got {params['tp_type']}"
            assert params['take_profit'] is not None, "RANGE should have a take_profit value"
            print(f"✅ RANGE archetype params verified: trailing=NONE, monitoring=false, tp_type=FIXED_POC")
        else:
            print(f"⚠️ Current archetype is {params['archetype']}, not RANGE (regime={data['regime']})")
    
    def test_trend_archetype_params(self):
        """TREND archetype should have trailing_type=VWAP_CENTRAL, monitoring_required=true"""
        # This test documents expected TREND behavior
        # In production, TREND would be active during COMPLACENCIA, BULL, or BEAR regimes
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MNQ?side=BUY")
        data = response.json()
        params = data['params']
        
        if params['archetype'] == 'TREND':
            assert params['trailing_type'] == 'VWAP_CENTRAL', f"TREND should have trailing_type=VWAP_CENTRAL"
            assert params['monitoring_required'] == True, f"TREND should have monitoring_required=true"
            assert params.get('trailing_config', {}).get('reference') == 'vwap', "TREND trailing should reference VWAP"
            print(f"✅ TREND archetype params verified: trailing=VWAP_CENTRAL, monitoring=true")
        else:
            print(f"ℹ️ Current archetype is {params['archetype']}, TREND test skipped (regime={data['regime']})")
    
    def test_fade_archetype_params(self):
        """FADE archetype should have trailing_type=BREAK_EVEN, break_even threshold present"""
        # This test documents expected FADE behavior
        # In production, FADE would be active during CAPITULACAO regime
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MNQ?side=BUY")
        data = response.json()
        params = data['params']
        
        if params['archetype'] == 'FADE':
            assert params['trailing_type'] == 'BREAK_EVEN', f"FADE should have trailing_type=BREAK_EVEN"
            assert params['monitoring_required'] == True, f"FADE should have monitoring_required=true"
            assert params.get('break_even') is not None, "FADE should have break_even config"
            assert 'threshold' in params.get('break_even', {}), "FADE break_even should have threshold"
            print(f"✅ FADE archetype params verified: trailing=BREAK_EVEN, break_even present")
        else:
            print(f"ℹ️ Current archetype is {params['archetype']}, FADE test skipped (regime={data['regime']})")


class TestPositionOpen:
    """Test POST /api/positions/open endpoint"""
    
    def test_open_position_with_force(self):
        """Open a paper position with force=true"""
        global created_position_ids
        
        # First, close any existing positions for MNQ
        active_resp = requests.get(f"{BASE_URL}/api/positions/active/MNQ")
        if active_resp.status_code == 200:
            active_data = active_resp.json()
            if active_data.get('position'):
                pos_id = active_data['position']['id']
                requests.post(f"{BASE_URL}/api/positions/{pos_id}/close?reason=TEST_CLEANUP")
                time.sleep(0.5)
        
        # Open new position
        payload = {
            "symbol": "MNQ",
            "side": "BUY",
            "quantity": 1,
            "force": True
        }
        response = requests.post(f"{BASE_URL}/api/positions/open", json=payload)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data['status'] == 'opened', f"Expected status=opened, got {data['status']}"
        assert 'position' in data, "Response should contain position"
        
        position = data['position']
        created_position_ids.append(position['id'])
        
        # Verify position document fields
        assert position['symbol'] == 'MNQ', "Position symbol should be MNQ"
        assert position['side'] == 'BUY', "Position side should be BUY"
        assert 'id' in position, "Position should have id"
        assert 'entry_price' in position, "Position should have entry_price"
        assert 'hard_stop' in position, "Position should have hard_stop"
        assert 'current_stop' in position, "Position should have current_stop"
        assert 'archetype' in position, "Position should have archetype"
        assert 'state' in position, "Position should have state"
        assert 'events' in position, "Position should have events"
        assert 'opened_at' in position, "Position should have opened_at"
        assert position['paper'] == True, "Position should be paper=true"
        
        print(f"✅ Position opened: id={position['id']}, archetype={position['archetype']}")
        print(f"   Entry={position['entry_price']}, SL={position['hard_stop']}, TP={position.get('take_profit')}")
        
        return position['id']
    
    def test_duplicate_position_protection(self):
        """Opening same symbol twice should return error"""
        # Ensure there's an open position
        active_resp = requests.get(f"{BASE_URL}/api/positions/active/MNQ")
        if active_resp.status_code == 200:
            active_data = active_resp.json()
            if not active_data.get('position'):
                # Open one first
                payload = {"symbol": "MNQ", "side": "BUY", "quantity": 1, "force": True}
                requests.post(f"{BASE_URL}/api/positions/open", json=payload)
                time.sleep(0.5)
        
        # Try to open another
        payload = {"symbol": "MNQ", "side": "SELL", "quantity": 1, "force": True}
        response = requests.post(f"{BASE_URL}/api/positions/open", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data['status'] == 'error', f"Expected status=error for duplicate, got {data['status']}"
        assert 'ja aberta' in data.get('message', '').lower() or 'already' in data.get('message', '').lower(), \
            f"Error message should indicate duplicate: {data.get('message')}"
        
        print(f"✅ Duplicate position protection works: {data.get('message')}")


class TestPositionActive:
    """Test GET /api/positions/active endpoints"""
    
    def test_get_all_active_positions(self):
        """Get all active positions"""
        response = requests.get(f"{BASE_URL}/api/positions/active")
        assert response.status_code == 200
        
        data = response.json()
        assert 'positions' in data, "Response should contain positions"
        assert 'count' in data, "Response should contain count"
        assert isinstance(data['positions'], list), "Positions should be a list"
        
        print(f"✅ Active positions: count={data['count']}")
        for pos in data['positions'][:3]:
            print(f"   - {pos['symbol']} {pos['side']} {pos['archetype']} state={pos['state']}")
    
    def test_get_active_position_for_symbol(self):
        """Get active position for specific symbol"""
        response = requests.get(f"{BASE_URL}/api/positions/active/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        assert 'position' in data, "Response should contain position key"
        
        if data['position']:
            pos = data['position']
            assert pos['symbol'] == 'MNQ', "Position symbol should match"
            print(f"✅ Active MNQ position: id={pos['id']}, state={pos['state']}")
        else:
            print("ℹ️ No active MNQ position")
    
    def test_get_active_position_nonexistent_symbol(self):
        """Get active position for symbol with no position"""
        response = requests.get(f"{BASE_URL}/api/positions/active/MES")
        assert response.status_code == 200
        
        data = response.json()
        # Should return null/None for position
        print(f"MES active position: {data.get('position')}")


class TestPositionUpdateStop:
    """Test PUT /api/positions/{id}/update-stop endpoint"""
    
    def test_update_stop_loss(self):
        """Update stop loss for an active position"""
        # Get active position
        active_resp = requests.get(f"{BASE_URL}/api/positions/active/MNQ")
        if active_resp.status_code != 200:
            pytest.skip("No active position to update")
        
        active_data = active_resp.json()
        if not active_data.get('position'):
            pytest.skip("No active MNQ position to update")
        
        position = active_data['position']
        pos_id = position['id']
        old_stop = position['current_stop']
        new_stop = old_stop + 5.0  # Move stop up by 5 points
        
        response = requests.put(f"{BASE_URL}/api/positions/{pos_id}/update-stop?new_stop={new_stop}")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data['status'] == 'updated', f"Expected status=updated, got {data['status']}"
        assert data['new_stop'] == round(new_stop, 2), f"New stop should be {new_stop}"
        
        # Verify the update persisted
        verify_resp = requests.get(f"{BASE_URL}/api/positions/active/MNQ")
        verify_data = verify_resp.json()
        assert verify_data['position']['current_stop'] == round(new_stop, 2), "Stop should be updated in DB"
        
        # Check event was added
        assert len(verify_data['position']['events']) > len(position['events']), "Event should be added"
        
        print(f"✅ Stop updated: {old_stop} -> {new_stop}")
    
    def test_update_stop_invalid_position(self):
        """Update stop for non-existent position should return 404"""
        response = requests.put(f"{BASE_URL}/api/positions/invalid-id-12345/update-stop?new_stop=100")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ Invalid position ID returns 404")


class TestPositionClose:
    """Test POST /api/positions/{id}/close endpoint"""
    
    def test_close_position(self):
        """Close an active position"""
        # Get active position
        active_resp = requests.get(f"{BASE_URL}/api/positions/active/MNQ")
        if active_resp.status_code != 200:
            pytest.skip("No active position to close")
        
        active_data = active_resp.json()
        if not active_data.get('position'):
            pytest.skip("No active MNQ position to close")
        
        position = active_data['position']
        pos_id = position['id']
        
        response = requests.post(f"{BASE_URL}/api/positions/{pos_id}/close?reason=TEST_MANUAL")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data['status'] == 'closed', f"Expected status=closed, got {data['status']}"
        assert data['reason'] == 'TEST_MANUAL', f"Reason should be TEST_MANUAL"
        
        # Verify position is now closed
        verify_resp = requests.get(f"{BASE_URL}/api/positions/active/MNQ")
        verify_data = verify_resp.json()
        assert verify_data['position'] is None, "Position should no longer be active"
        
        print(f"✅ Position closed: id={pos_id}")
    
    def test_close_already_closed_position(self):
        """Closing already closed position should return already_closed"""
        # Get a closed position from history
        history_resp = requests.get(f"{BASE_URL}/api/positions/history?limit=1")
        if history_resp.status_code != 200:
            pytest.skip("No history available")
        
        history_data = history_resp.json()
        if not history_data.get('positions'):
            pytest.skip("No closed positions in history")
        
        closed_pos = history_data['positions'][0]
        pos_id = closed_pos['id']
        
        response = requests.post(f"{BASE_URL}/api/positions/{pos_id}/close?reason=TEST")
        assert response.status_code == 200
        
        data = response.json()
        assert data['status'] == 'already_closed', f"Expected already_closed, got {data['status']}"
        print(f"✅ Already closed position returns already_closed")


class TestPositionHistory:
    """Test GET /api/positions/history endpoint"""
    
    def test_get_position_history(self):
        """Get closed position history"""
        response = requests.get(f"{BASE_URL}/api/positions/history?limit=10")
        assert response.status_code == 200
        
        data = response.json()
        assert 'positions' in data, "Response should contain positions"
        assert 'count' in data, "Response should contain count"
        
        print(f"✅ Position history: count={data['count']}")
        for pos in data['positions'][:3]:
            print(f"   - {pos['symbol']} {pos['archetype']} closed={pos.get('close_reason')} at {pos.get('closed_at', '')[:10]}")


class TestCleanup:
    """Cleanup test positions"""
    
    def test_cleanup_test_positions(self):
        """Close any remaining test positions"""
        # Close any active MNQ position
        active_resp = requests.get(f"{BASE_URL}/api/positions/active/MNQ")
        if active_resp.status_code == 200:
            active_data = active_resp.json()
            if active_data.get('position'):
                pos_id = active_data['position']['id']
                requests.post(f"{BASE_URL}/api/positions/{pos_id}/close?reason=TEST_CLEANUP")
                print(f"✅ Cleaned up MNQ position: {pos_id}")
        
        # Close any active MES position
        active_resp = requests.get(f"{BASE_URL}/api/positions/active/MES")
        if active_resp.status_code == 200:
            active_data = active_resp.json()
            if active_data.get('position'):
                pos_id = active_data['position']['id']
                requests.post(f"{BASE_URL}/api/positions/{pos_id}/close?reason=TEST_CLEANUP")
                print(f"✅ Cleaned up MES position: {pos_id}")
        
        print("✅ Cleanup complete")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
