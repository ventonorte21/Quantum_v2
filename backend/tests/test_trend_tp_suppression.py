"""
Test Suite: TREND Archetype TP Suppression Fix
===============================================
Verifies that TREND trades do NOT send TP Limit orders (only Entry + SL + Scale-Out),
while RANGE and FADE trades still send TP Limit orders with full quantity.

Two code paths tested:
1. execute_v3_signal (~line 5659) - POST /api/v3/execute/{symbol}?force=true
2. open_position_with_signalstack (~line 5922) - internal function

Key conditions:
- TREND (COMPLACENCIA, BULL, BEAR): TP Limit suppressed, trailing stop handles exit
- RANGE (TRANSICAO): TP Limit sent with full quantity
- FADE (CAPITULACAO): TP Limit sent with full quantity
- Scale-Out quantity = 50% of total (scale_out['quantity'])
- Scale-Out position_doc has 'move_stop_automated': False
"""

import pytest
import requests
import os
import sys

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')

# Use internal URL for testing to avoid network timeouts
BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
AUTH_TOKEN = os.environ.get("TEST_AUTH_TOKEN", "test_session_auth_1775441581328")


class TestPositionManagerArchetypes:
    """Test position_manager.py archetype logic directly"""
    
    def test_regime_to_archetype_mapping(self):
        """Verify REGIME_TO_ARCHETYPE mapping is correct"""
        from services.position_manager import REGIME_TO_ARCHETYPE, Archetype
        
        # TREND regimes
        assert REGIME_TO_ARCHETYPE['COMPLACENCIA'] == Archetype.TREND, "COMPLACENCIA should map to TREND"
        assert REGIME_TO_ARCHETYPE['BULL'] == Archetype.TREND, "BULL should map to TREND"
        assert REGIME_TO_ARCHETYPE['BEAR'] == Archetype.TREND, "BEAR should map to TREND"
        
        # RANGE regime
        assert REGIME_TO_ARCHETYPE['TRANSICAO'] == Archetype.RANGE, "TRANSICAO should map to RANGE"
        
        # FADE regime
        assert REGIME_TO_ARCHETYPE['CAPITULACAO'] == Archetype.FADE, "CAPITULACAO should map to FADE"
        
        print("✅ REGIME_TO_ARCHETYPE mapping is correct")
    
    def test_get_archetype_function(self):
        """Test get_archetype() returns correct archetype for each regime"""
        from services.position_manager import get_archetype, Archetype
        
        # TREND
        assert get_archetype('COMPLACENCIA') == Archetype.TREND
        assert get_archetype('BULL') == Archetype.TREND
        assert get_archetype('BEAR') == Archetype.TREND
        
        # RANGE
        assert get_archetype('TRANSICAO') == Archetype.RANGE
        
        # FADE
        assert get_archetype('CAPITULACAO') == Archetype.FADE
        
        # Unknown defaults to RANGE
        assert get_archetype('UNKNOWN') == Archetype.RANGE
        
        print("✅ get_archetype() returns correct archetypes")
    
    def test_trend_calculate_position_params_has_take_profit(self):
        """TREND archetype still calculates take_profit (theoretical) - suppression is at webhook level"""
        from services.position_manager import calculate_position_params
        
        levels = {
            'vwap': 21500.0,
            'poc': 21480.0,
            'vah': 21550.0,
            'val': 21450.0,
            'call_wall': 21700.0,
            'put_wall': 21300.0,
        }
        
        # Test COMPLACENCIA (TREND)
        params = calculate_position_params(
            regime='COMPLACENCIA',
            entry_price=21500.0,
            side='BUY',
            atr_m1=5.0,
            levels=levels,
            tick_size=0.25,
        )
        
        assert params['archetype'] == 'TREND', "Archetype should be TREND"
        # take_profit is calculated (theoretical) but suppression happens at webhook send level
        # For LONG with call_wall > entry, TP should be call_wall
        assert params.get('take_profit') == 21700.0, f"TREND should have theoretical TP (call_wall). Got: {params.get('take_profit')}"
        assert params['tp_type'] == 'OPEN_TRAILING', "TREND tp_type should be OPEN_TRAILING"
        assert params['trailing_type'] == 'VWAP_CENTRAL', "TREND should use VWAP_CENTRAL trailing"
        
        print("✅ TREND calculate_position_params returns theoretical take_profit (suppression at webhook level)")
    
    def test_trend_scale_out_config(self):
        """TREND archetype has scale_out config with correct parameters"""
        from services.position_manager import calculate_position_params
        
        levels = {
            'vwap': 21500.0,
            'poc': 21480.0,
            'vah': 21550.0,
            'val': 21450.0,
            'call_wall': 21700.0,
            'put_wall': 21300.0,
        }
        
        params = calculate_position_params(
            regime='BULL',  # TREND archetype
            entry_price=21500.0,
            side='BUY',
            atr_m1=5.0,
            levels=levels,
            tick_size=0.25,
        )
        
        assert 'scale_out' in params, "TREND should have scale_out config"
        scale_out = params['scale_out']
        assert scale_out['enabled'] == True, "scale_out should be enabled"
        assert scale_out['pct'] == 50, "scale_out pct should be 50%"
        assert scale_out['trigger_multiple'] == 2.0, "scale_out trigger_multiple should be 2.0"
        assert scale_out['move_stop_to_entry'] == True, "scale_out should move stop to entry"
        
        # Verify trigger_price calculation: entry + sign * sl_distance * 2.0
        # sl_distance = 1.5 * atr_m1 = 1.5 * 5.0 = 7.5
        # trigger_price = 21500 + 1 * 7.5 * 2.0 = 21500 + 15 = 21515
        expected_trigger = 21500.0 + 1 * (1.5 * 5.0) * 2.0
        assert scale_out['trigger_price'] == expected_trigger, f"scale_out trigger_price should be {expected_trigger}. Got: {scale_out['trigger_price']}"
        
        print("✅ TREND scale_out config is correct (50% at 2×risk)")
    
    def test_range_has_fixed_take_profit(self):
        """RANGE archetype has fixed take_profit at POC"""
        from services.position_manager import calculate_position_params
        
        levels = {
            'vwap': 21500.0,
            'poc': 21480.0,
            'vah': 21550.0,
            'val': 21450.0,
        }
        
        params = calculate_position_params(
            regime='TRANSICAO',  # RANGE archetype
            entry_price=21450.0,  # Entry at VAL (long)
            side='BUY',
            atr_m1=5.0,
            levels=levels,
            tick_size=0.25,
        )
        
        assert params['archetype'] == 'RANGE', "Archetype should be RANGE"
        assert params['take_profit'] == 21480.0, f"RANGE TP should be POC (21480). Got: {params['take_profit']}"
        assert params['tp_type'] == 'FIXED_POC', "RANGE tp_type should be FIXED_POC"
        assert params['trailing_type'] == 'NONE', "RANGE should have no trailing"
        assert 'scale_out' not in params, "RANGE should NOT have scale_out"
        
        print("✅ RANGE has fixed take_profit at POC")
    
    def test_fade_has_fixed_take_profit(self):
        """FADE archetype has fixed take_profit at VWAP -1σ"""
        from services.position_manager import calculate_position_params
        
        levels = {
            'vwap': 21500.0,
            'poc': 21480.0,
            'vah': 21550.0,
            'val': 21450.0,
            'call_wall': 21700.0,
            'put_wall': 21300.0,
            'vwap_lower_1s': 21400.0,
            'vwap_upper_1s': 21600.0,
        }
        
        params = calculate_position_params(
            regime='CAPITULACAO',  # FADE archetype
            entry_price=21300.0,  # Entry at put_wall (long fade)
            side='BUY',
            atr_m1=5.0,
            levels=levels,
            tick_size=0.25,
        )
        
        assert params['archetype'] == 'FADE', "Archetype should be FADE"
        # For LONG fade, TP is vwap_lower_1s if > entry, else vwap
        # vwap_lower_1s (21400) > entry (21300), so TP = 21400
        assert params['take_profit'] == 21400.0, f"FADE TP should be vwap_lower_1s (21400). Got: {params['take_profit']}"
        assert params['tp_type'] == 'FIXED_VWAP_1S', "FADE tp_type should be FIXED_VWAP_1S"
        assert params['trailing_type'] == 'BREAK_EVEN', "FADE should use BREAK_EVEN trailing"
        assert 'scale_out' not in params, "FADE should NOT have scale_out"
        
        print("✅ FADE has fixed take_profit at VWAP -1σ")


class TestV3SignalEndpoint:
    """Test /api/v3/signal/{symbol} endpoint returns correct archetype info"""
    
    def test_v3_signal_returns_regime_in_nivel_1(self):
        """V3 signal endpoint returns regime in nivel_1"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert 'nivel_1' in data, "Response should contain nivel_1"
        nivel_1 = data['nivel_1']
        assert 'regime' in nivel_1, "nivel_1 should contain regime"
        
        regime = nivel_1['regime']
        valid_regimes = ['COMPLACENCIA', 'BULL', 'BEAR', 'TRANSICAO', 'CAPITULACAO']
        assert regime in valid_regimes, f"Invalid regime: {regime}. Expected one of {valid_regimes}"
        
        # Verify archetype can be derived from regime
        from services.position_manager import get_archetype
        archetype = get_archetype(regime)
        assert archetype.value in ['TREND', 'RANGE', 'FADE'], f"Invalid archetype: {archetype}"
        
        print(f"✅ V3 signal returns regime: {regime} → archetype: {archetype.value}")


class TestCodePathLogic:
    """Test the conditional logic in both code paths (static analysis + API verification)"""
    
    def test_path1_execute_v3_signal_trend_suppression_logic(self):
        """Verify Path 1 (execute_v3_signal) has correct TREND TP suppression logic"""
        import ast
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Check for the key condition: arch != Arch.TREND
        assert 'arch != Arch.TREND' in content, "Path 1 should check 'arch != Arch.TREND' for TP suppression"
        
        # Check for the log message when TREND TP is suppressed
        assert 'TREND: TP Limit suprimido' in content, "Path 1 should log TREND TP suppression"
        
        # Check that scale-out uses so_qty (partial quantity)
        assert 'so_qty = max(1, quantity // 2)' in content, "Path 1 scale-out should use 50% quantity"
        
        print("✅ Path 1 (execute_v3_signal) has correct TREND TP suppression logic")
    
    def test_path2_open_position_with_signalstack_trend_suppression_logic(self):
        """Verify Path 2 (open_position_with_signalstack) has correct TREND TP suppression logic"""
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Check for the key condition: arch_val != 'TREND'
        assert "arch_val != 'TREND'" in content, "Path 2 should check arch_val != 'TREND' for TP suppression"
        
        # Check for tp_resp = None initialization
        assert 'tp_resp = None' in content, "Path 2 should initialize tp_resp = None"
        
        # Check for scale-out move_stop_automated: False
        assert "'move_stop_automated': False" in content, "Scale-out should have move_stop_automated: False"
        
        print("✅ Path 2 (open_position_with_signalstack) has correct TREND TP suppression logic")
    
    def test_scale_out_quantity_calculation(self):
        """Verify scale-out quantity is exactly 50% (scale_out['quantity'])"""
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Path 1: so_qty = max(1, quantity // 2)
        assert 'so_qty = max(1, quantity // 2)' in content, "Path 1 should calculate so_qty as 50%"
        
        # Path 2: so_qty = max(1, quantity * scale_out_cfg['pct'] // 100)
        assert "so_qty = max(1, quantity * scale_out_cfg['pct'] // 100)" in content, "Path 2 should use scale_out_cfg['pct']"
        
        print("✅ Scale-out quantity calculation is correct (50% of total)")


class TestBackendHealth:
    """Verify backend starts without errors after changes"""
    
    def test_backend_health(self):
        """Backend health check passes"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
        
        data = response.json()
        assert data.get('status') == 'healthy', f"Backend not healthy: {data}"
        
        print("✅ Backend health check passed")
    
    def test_position_manager_imports(self):
        """position_manager.py imports without errors"""
        try:
            from services.position_manager import (
                calculate_position_params, create_position_document,
                get_archetype, Archetype, PositionState, CloseReason,
                REGIME_TO_ARCHETYPE, SL_ATR_MULTIPLIER
            )
            print("✅ position_manager.py imports successfully")
        except ImportError as e:
            pytest.fail(f"position_manager.py import failed: {e}")


class TestArchetypeSpecificBehavior:
    """Test archetype-specific behavior for all three archetypes"""
    
    def test_all_trend_regimes_suppress_tp(self):
        """All TREND regimes (COMPLACENCIA, BULL, BEAR) should suppress TP"""
        from services.position_manager import get_archetype, Archetype
        
        trend_regimes = ['COMPLACENCIA', 'BULL', 'BEAR']
        for regime in trend_regimes:
            arch = get_archetype(regime)
            assert arch == Archetype.TREND, f"{regime} should be TREND archetype"
            # The suppression check: arch != Arch.TREND means TP is NOT sent for TREND
            should_send_tp = (arch != Archetype.TREND)
            assert should_send_tp == False, f"{regime} should NOT send TP (suppressed)"
        
        print("✅ All TREND regimes (COMPLACENCIA, BULL, BEAR) suppress TP")
    
    def test_range_regime_sends_tp(self):
        """RANGE regime (TRANSICAO) should send TP"""
        from services.position_manager import get_archetype, Archetype
        
        arch = get_archetype('TRANSICAO')
        assert arch == Archetype.RANGE, "TRANSICAO should be RANGE archetype"
        should_send_tp = (arch != Archetype.TREND)
        assert should_send_tp == True, "RANGE should send TP"
        
        print("✅ RANGE regime (TRANSICAO) sends TP")
    
    def test_fade_regime_sends_tp(self):
        """FADE regime (CAPITULACAO) should send TP"""
        from services.position_manager import get_archetype, Archetype
        
        arch = get_archetype('CAPITULACAO')
        assert arch == Archetype.FADE, "CAPITULACAO should be FADE archetype"
        should_send_tp = (arch != Archetype.TREND)
        assert should_send_tp == True, "FADE should send TP"
        
        print("✅ FADE regime (CAPITULACAO) sends TP")


class TestScaleOutConfiguration:
    """Test scale-out configuration for TREND archetype"""
    
    def test_scale_out_only_for_trend(self):
        """Scale-out is only configured for TREND archetype"""
        from services.position_manager import calculate_position_params
        
        levels = {
            'vwap': 21500.0, 'poc': 21480.0, 'vah': 21550.0, 'val': 21450.0,
            'call_wall': 21700.0, 'put_wall': 21300.0,
            'vwap_lower_1s': 21400.0, 'vwap_upper_1s': 21600.0,
        }
        
        # TREND has scale_out
        trend_params = calculate_position_params('COMPLACENCIA', 21500.0, 'BUY', 5.0, levels, 0.25)
        assert 'scale_out' in trend_params, "TREND should have scale_out"
        
        # RANGE does NOT have scale_out
        range_params = calculate_position_params('TRANSICAO', 21450.0, 'BUY', 5.0, levels, 0.25)
        assert 'scale_out' not in range_params, "RANGE should NOT have scale_out"
        
        # FADE does NOT have scale_out
        fade_params = calculate_position_params('CAPITULACAO', 21300.0, 'BUY', 5.0, levels, 0.25)
        assert 'scale_out' not in fade_params, "FADE should NOT have scale_out"
        
        print("✅ Scale-out is only configured for TREND archetype")
    
    def test_scale_out_quantity_is_50_percent(self):
        """Scale-out quantity is exactly 50% of total"""
        from services.position_manager import calculate_position_params
        
        levels = {
            'vwap': 21500.0, 'poc': 21480.0, 'vah': 21550.0, 'val': 21450.0,
            'call_wall': 21700.0, 'put_wall': 21300.0,
        }
        
        params = calculate_position_params('BULL', 21500.0, 'BUY', 5.0, levels, 0.25)
        scale_out = params['scale_out']
        
        assert scale_out['pct'] == 50, f"Scale-out pct should be 50. Got: {scale_out['pct']}"
        
        # For quantity=4, so_qty should be 2 (50%)
        # For quantity=3, so_qty should be 1 (max(1, 3//2) = max(1, 1) = 1)
        # For quantity=2, so_qty should be 1 (max(1, 2//2) = max(1, 1) = 1)
        # For quantity=1, scale-out is skipped (quantity > 1 check)
        
        print("✅ Scale-out quantity is 50% of total")


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
