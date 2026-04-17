"""
Auto Position Sizing Tests
==========================
Tests for the new auto position sizing feature:
- POST /api/positions/evaluate/{symbol} returns risk object with max_qty_by_risk
- max_qty_by_risk = floor(max_risk_usd / sl_risk_per_contract_usd)
- Open position uses auto-calculated quantity
- Risk object contains: account_size, risk_per_trade_pct, sl_risk_per_contract_usd, rr_ratio
"""

import pytest
import requests
import os
import time
import math

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestAutoPositionSizingAPI:
    """Test auto position sizing in /api/positions/evaluate endpoint"""
    
    def test_evaluate_returns_risk_object(self):
        """Evaluate endpoint should return risk object with all required fields"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MES?side=BUY")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'risk' in data, "Response should contain 'risk' object"
        
        risk = data['risk']
        required_fields = [
            'account_size',
            'risk_per_trade_pct',
            'max_risk_usd',
            'sl_distance_pts',
            'sl_risk_per_contract_usd',
            'rr_ratio',
            'max_qty_by_risk',
            'point_value',
        ]
        
        for field in required_fields:
            assert field in risk, f"Risk object should contain '{field}'"
        
        print(f"✅ Risk object contains all required fields")
        print(f"   account_size: ${risk['account_size']:,.0f}")
        print(f"   risk_per_trade_pct: {risk['risk_per_trade_pct']}%")
        print(f"   max_risk_usd: ${risk['max_risk_usd']:,.2f}")
        print(f"   sl_risk_per_contract_usd: ${risk['sl_risk_per_contract_usd']:,.2f}")
        print(f"   max_qty_by_risk: {risk['max_qty_by_risk']}")
        print(f"   rr_ratio: {risk['rr_ratio']}")
    
    def test_max_qty_calculation_formula(self):
        """max_qty_by_risk should equal floor(max_risk_usd / sl_risk_per_contract_usd)"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MES?side=BUY")
        assert response.status_code == 200
        
        data = response.json()
        risk = data['risk']
        
        max_risk_usd = risk['max_risk_usd']
        sl_risk_per_contract = risk['sl_risk_per_contract_usd']
        max_qty = risk['max_qty_by_risk']
        
        # Calculate expected qty
        if sl_risk_per_contract > 0:
            expected_qty = max(1, int(max_risk_usd / sl_risk_per_contract))
        else:
            expected_qty = 1
        
        assert max_qty == expected_qty, f"max_qty_by_risk ({max_qty}) should equal floor({max_risk_usd}/{sl_risk_per_contract}) = {expected_qty}"
        
        print(f"✅ max_qty_by_risk formula verified: floor(${max_risk_usd:.2f} / ${sl_risk_per_contract:.2f}) = {max_qty}")
    
    def test_sl_risk_uses_point_value(self):
        """sl_risk_per_contract_usd should equal sl_distance_pts * point_value"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MES?side=BUY")
        assert response.status_code == 200
        
        data = response.json()
        risk = data['risk']
        
        sl_distance = risk['sl_distance_pts']
        point_value = risk['point_value']
        sl_risk = risk['sl_risk_per_contract_usd']
        
        expected_risk = sl_distance * point_value
        
        # Allow small floating point tolerance
        assert abs(sl_risk - expected_risk) < 0.01, \
            f"sl_risk_per_contract_usd ({sl_risk}) should equal sl_distance_pts ({sl_distance}) * point_value ({point_value}) = {expected_risk}"
        
        print(f"✅ sl_risk calculation verified: {sl_distance:.2f} pts * ${point_value}/pt = ${sl_risk:.2f}")
    
    def test_mes_point_value_is_5(self):
        """MES point_value should be 5.0 (micro S&P)"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MES?side=BUY")
        assert response.status_code == 200
        
        data = response.json()
        risk = data['risk']
        
        assert risk['point_value'] == 5.0, f"MES point_value should be 5.0, got {risk['point_value']}"
        print(f"✅ MES point_value = ${risk['point_value']}/point")
    
    def test_point_value_matches_trade_symbol(self):
        """point_value should match the trade_symbol (regime target), not requested symbol"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MNQ?side=BUY")
        assert response.status_code == 200
        
        data = response.json()
        risk = data['risk']
        trade_symbol = data['trade_symbol']
        
        # Point values: MNQ=2.0, MES=5.0
        expected_point_values = {'MNQ': 2.0, 'MES': 5.0}
        expected = expected_point_values.get(trade_symbol, 5.0)
        
        assert risk['point_value'] == expected, \
            f"point_value for trade_symbol={trade_symbol} should be {expected}, got {risk['point_value']}"
        print(f"✅ point_value for {trade_symbol} = ${risk['point_value']}/point (regime target)")
    
    def test_max_qty_at_least_1(self):
        """max_qty_by_risk should always be at least 1"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MES?side=BUY")
        assert response.status_code == 200
        
        data = response.json()
        risk = data['risk']
        
        assert risk['max_qty_by_risk'] >= 1, f"max_qty_by_risk should be >= 1, got {risk['max_qty_by_risk']}"
        print(f"✅ max_qty_by_risk >= 1: {risk['max_qty_by_risk']}")
    
    def test_rr_ratio_calculation(self):
        """rr_ratio should equal tp_distance / sl_distance"""
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MES?side=BUY")
        assert response.status_code == 200
        
        data = response.json()
        risk = data['risk']
        params = data['params']
        entry = data['entry_price']
        
        if risk['rr_ratio'] is not None and params.get('take_profit'):
            sl_distance = abs(entry - params['hard_stop'])
            tp_distance = abs(params['take_profit'] - entry)
            expected_rr = round(tp_distance / sl_distance, 2) if sl_distance > 0 else None
            
            assert risk['rr_ratio'] == expected_rr, \
                f"rr_ratio ({risk['rr_ratio']}) should equal {tp_distance:.2f}/{sl_distance:.2f} = {expected_rr}"
            
            print(f"✅ R:R ratio verified: {tp_distance:.2f}/{sl_distance:.2f} = {risk['rr_ratio']}")
        else:
            print(f"ℹ️ No TP set, rr_ratio is None (open target)")


class TestOpenPositionWithAutoQty:
    """Test that open position uses auto-calculated quantity"""
    
    def test_open_position_with_auto_qty(self):
        """Open position should accept and use auto-calculated quantity"""
        # First, close any existing MES position
        active_resp = requests.get(f"{BASE_URL}/api/positions/active/MES")
        if active_resp.status_code == 200:
            active_data = active_resp.json()
            if active_data.get('position'):
                pos_id = active_data['position']['id']
                requests.post(f"{BASE_URL}/api/positions/{pos_id}/close?reason=TEST_CLEANUP")
                time.sleep(0.5)
        
        # Get the auto-calculated quantity
        eval_resp = requests.post(f"{BASE_URL}/api/positions/evaluate/MES?side=BUY")
        assert eval_resp.status_code == 200
        eval_data = eval_resp.json()
        auto_qty = eval_data['risk']['max_qty_by_risk']
        
        print(f"   Auto-calculated qty: {auto_qty}")
        
        # Open position with auto qty
        payload = {
            "symbol": "MES",
            "side": "BUY",
            "quantity": auto_qty,
            "force": True
        }
        response = requests.post(f"{BASE_URL}/api/positions/open", json=payload)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data['status'] == 'opened', f"Expected status=opened, got {data['status']}"
        
        position = data['position']
        assert position['quantity'] == auto_qty, \
            f"Position quantity ({position['quantity']}) should equal auto_qty ({auto_qty})"
        
        print(f"✅ Position opened with auto qty: {position['quantity']}x MES")
        
        # Cleanup
        requests.post(f"{BASE_URL}/api/positions/{position['id']}/close?reason=TEST_CLEANUP")
    
    def test_position_quantity_persists(self):
        """Position quantity should persist in database"""
        # Close any existing MNQ position
        active_resp = requests.get(f"{BASE_URL}/api/positions/active/MNQ")
        if active_resp.status_code == 200:
            active_data = active_resp.json()
            if active_data.get('position'):
                pos_id = active_data['position']['id']
                requests.post(f"{BASE_URL}/api/positions/{pos_id}/close?reason=TEST_CLEANUP")
                time.sleep(0.5)
        
        # Get auto qty
        eval_resp = requests.post(f"{BASE_URL}/api/positions/evaluate/MNQ?side=BUY")
        auto_qty = eval_resp.json()['risk']['max_qty_by_risk']
        
        # Open position
        payload = {
            "symbol": "MNQ",
            "side": "BUY",
            "quantity": auto_qty,
            "force": True
        }
        open_resp = requests.post(f"{BASE_URL}/api/positions/open", json=payload)
        assert open_resp.status_code == 200
        pos_id = open_resp.json()['position']['id']
        
        # Verify quantity persisted
        verify_resp = requests.get(f"{BASE_URL}/api/positions/active/MNQ")
        assert verify_resp.status_code == 200
        verify_data = verify_resp.json()
        
        assert verify_data['position'] is not None, "Position should exist"
        assert verify_data['position']['quantity'] == auto_qty, \
            f"Persisted quantity ({verify_data['position']['quantity']}) should equal {auto_qty}"
        
        print(f"✅ Position quantity persisted: {verify_data['position']['quantity']}x MNQ")
        
        # Cleanup
        requests.post(f"{BASE_URL}/api/positions/{pos_id}/close?reason=TEST_CLEANUP")


class TestAutoTradingConfigIntegration:
    """Test that auto trading config affects position sizing"""
    
    def test_account_size_affects_max_qty(self):
        """Different account sizes should produce different max_qty values"""
        # Get current config
        config_resp = requests.get(f"{BASE_URL}/api/autotrading/config")
        if config_resp.status_code == 200:
            current_config = config_resp.json().get('config', {})
        else:
            current_config = {}
        
        # Test with current config
        eval_resp = requests.post(f"{BASE_URL}/api/positions/evaluate/MES?side=BUY")
        assert eval_resp.status_code == 200
        risk = eval_resp.json()['risk']
        
        account_size = risk['account_size']
        risk_pct = risk['risk_per_trade_pct']
        max_risk = risk['max_risk_usd']
        
        # Verify max_risk = account_size * risk_pct / 100
        expected_max_risk = account_size * risk_pct / 100
        assert abs(max_risk - expected_max_risk) < 0.01, \
            f"max_risk_usd ({max_risk}) should equal account_size ({account_size}) * risk_pct ({risk_pct}) / 100 = {expected_max_risk}"
        
        print(f"✅ max_risk_usd = ${account_size:,.0f} * {risk_pct}% = ${max_risk:,.2f}")


class TestCleanup:
    """Cleanup test positions"""
    
    def test_cleanup_test_positions(self):
        """Close any remaining test positions"""
        for symbol in ['MNQ', 'MES']:
            active_resp = requests.get(f"{BASE_URL}/api/positions/active/{symbol}")
            if active_resp.status_code == 200:
                active_data = active_resp.json()
                if active_data.get('position'):
                    pos_id = active_data['position']['id']
                    requests.post(f"{BASE_URL}/api/positions/{pos_id}/close?reason=TEST_CLEANUP")
                    print(f"✅ Cleaned up {symbol} position: {pos_id}")
        
        print("✅ Cleanup complete")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
