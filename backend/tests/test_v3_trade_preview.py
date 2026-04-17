"""
Test V3 Trade Preview - Asset Isolation Fix
Tests that trade_preview never mixes assets (Entry from one, SL/TP from another).
When target_symbol != symbol, backend returns redirect instead of mixed data.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestV3TradePreviewAssetIsolation:
    """Tests for V3 trade_preview asset isolation fix"""

    def test_mnq_signal_returns_redirect_when_target_is_mes(self):
        """MNQ signal should return redirect:true when regime target is MES (TRANSICAO)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        n1 = data.get('nivel_1', {})
        tp = data.get('trade_preview', {})
        
        # Verify regime is TRANSICAO (which targets MES)
        regime = n1.get('regime')
        target_symbol = n1.get('target_symbol')
        
        print(f"MNQ Signal - Regime: {regime}, Target: {target_symbol}")
        
        # If target != MNQ, trade_preview should have redirect:true
        if target_symbol != 'MNQ':
            assert tp.get('redirect') == True, f"Expected redirect:true when target={target_symbol}, got {tp}"
            assert tp.get('target_symbol') == target_symbol, f"Expected target_symbol={target_symbol} in redirect"
            assert 'message' in tp, "Redirect should have a message"
            assert 'archetype' in tp, "Redirect should have archetype"
            # Should NOT have entry_price, hard_stop, take_profit
            assert 'entry_price' not in tp, "Redirect should NOT have entry_price"
            assert 'hard_stop' not in tp, "Redirect should NOT have hard_stop"
            assert 'take_profit' not in tp, "Redirect should NOT have take_profit"
            print(f"✅ MNQ correctly returns redirect to {target_symbol}")
        else:
            # If target is MNQ, should have normal preview
            assert 'entry_price' in tp, "Normal preview should have entry_price"
            print(f"✅ MNQ returns normal preview (target is MNQ)")

    def test_mes_signal_returns_proper_preview_in_mes_scale(self):
        """MES signal should return trade_preview with all prices in MES scale (5000-7000)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        n1 = data.get('nivel_1', {})
        tp = data.get('trade_preview', {})
        
        regime = n1.get('regime')
        target_symbol = n1.get('target_symbol')
        
        print(f"MES Signal - Regime: {regime}, Target: {target_symbol}")
        
        # MES should be the target for TRANSICAO regime
        if target_symbol == 'MES':
            # Should NOT be a redirect
            assert tp.get('redirect') != True, f"MES panel should NOT redirect when target is MES"
            
            # Should have proper trade preview
            entry_price = tp.get('entry_price')
            hard_stop = tp.get('hard_stop')
            take_profit = tp.get('take_profit')
            
            print(f"MES Trade Preview - Entry: {entry_price}, SL: {hard_stop}, TP: {take_profit}")
            
            # Validate prices are in MES scale (5000-7000), NOT MNQ scale (20000+)
            assert entry_price is not None, "MES preview should have entry_price"
            assert hard_stop is not None, "MES preview should have hard_stop"
            
            # MES prices should be between 5000-7500 (current market ~6600)
            assert 5000 < entry_price < 7500, f"Entry {entry_price} should be in MES scale (5000-7500)"
            assert 5000 < hard_stop < 7500, f"Hard stop {hard_stop} should be in MES scale (5000-7500)"
            
            if take_profit is not None:
                assert 5000 < take_profit < 7500, f"Take profit {take_profit} should be in MES scale (5000-7500)"
            
            # Entry and SL should be close (within ~20 points for MES)
            sl_distance = abs(entry_price - hard_stop)
            assert sl_distance < 50, f"SL distance {sl_distance} seems too large for MES"
            
            print(f"✅ MES preview has correct scale: Entry={entry_price}, SL={hard_stop}, TP={take_profit}")
        else:
            # If target is not MES, should redirect
            assert tp.get('redirect') == True, f"Expected redirect when target={target_symbol}"
            print(f"✅ MES correctly redirects to {target_symbol}")

    def test_trade_preview_never_mixes_assets(self):
        """Verify that trade_preview never has prices from different assets"""
        for symbol in ['MNQ', 'MES']:
            response = requests.get(f"{BASE_URL}/api/v3/signal/{symbol}", timeout=30)
            assert response.status_code == 200
            
            data = response.json()
            n1 = data.get('nivel_1', {})
            tp = data.get('trade_preview', {})
            target_symbol = n1.get('target_symbol')
            
            if tp.get('redirect'):
                # Redirect case - no prices should be present
                assert 'entry_price' not in tp, f"{symbol}: Redirect should not have entry_price"
                assert 'hard_stop' not in tp, f"{symbol}: Redirect should not have hard_stop"
                print(f"✅ {symbol} redirect has no mixed prices")
            else:
                # Normal preview - all prices should be in same scale
                entry = tp.get('entry_price', 0)
                sl = tp.get('hard_stop', 0)
                tp_price = tp.get('take_profit')
                
                # Determine expected scale based on symbol
                if symbol == 'MNQ':
                    # MNQ scale: 18000-25000
                    assert 18000 < entry < 25000, f"MNQ entry {entry} out of scale"
                    assert 18000 < sl < 25000, f"MNQ SL {sl} out of scale"
                    if tp_price:
                        assert 18000 < tp_price < 25000, f"MNQ TP {tp_price} out of scale"
                elif symbol == 'MES':
                    # MES scale: 5000-7500
                    assert 5000 < entry < 7500, f"MES entry {entry} out of scale"
                    assert 5000 < sl < 7500, f"MES SL {sl} out of scale"
                    if tp_price:
                        assert 5000 < tp_price < 7500, f"MES TP {tp_price} out of scale"
                
                print(f"✅ {symbol} preview has consistent scale")

    def test_v3_signal_structure(self):
        """Verify V3 signal has all required fields"""
        for symbol in ['MNQ', 'MES']:
            response = requests.get(f"{BASE_URL}/api/v3/signal/{symbol}", timeout=30)
            assert response.status_code == 200
            
            data = response.json()
            
            # Required top-level fields
            assert 'symbol' in data, f"{symbol}: Missing symbol field"
            assert 'v3_status' in data, f"{symbol}: Missing v3_status field"
            assert 'nivel_1' in data, f"{symbol}: Missing nivel_1 field"
            assert 'nivel_2' in data, f"{symbol}: Missing nivel_2 field"
            assert 'nivel_3' in data, f"{symbol}: Missing nivel_3 field"
            assert 'trade_preview' in data, f"{symbol}: Missing trade_preview field"
            
            # nivel_1 required fields
            n1 = data['nivel_1']
            assert 'regime' in n1, f"{symbol}: Missing regime in nivel_1"
            assert 'target_symbol' in n1, f"{symbol}: Missing target_symbol in nivel_1"
            assert 'tactic' in n1, f"{symbol}: Missing tactic in nivel_1"
            
            print(f"✅ {symbol} V3 signal has all required fields")

    def test_trade_preview_archetype_matches_regime(self):
        """Verify trade_preview archetype matches the regime"""
        regime_archetype_map = {
            'COMPLACENCIA': 'TREND',
            'BULL': 'TREND',
            'TRANSICAO': 'RANGE',
            'BEAR': 'TREND',
            'CAPITULACAO': 'FADE',
        }
        
        for symbol in ['MNQ', 'MES']:
            response = requests.get(f"{BASE_URL}/api/v3/signal/{symbol}", timeout=30)
            assert response.status_code == 200
            
            data = response.json()
            n1 = data.get('nivel_1', {})
            tp = data.get('trade_preview', {})
            
            regime = n1.get('regime')
            archetype = tp.get('archetype')
            expected_archetype = regime_archetype_map.get(regime)
            
            if archetype and expected_archetype:
                assert archetype == expected_archetype, f"{symbol}: Expected archetype {expected_archetype} for regime {regime}, got {archetype}"
                print(f"✅ {symbol} archetype {archetype} matches regime {regime}")


class TestV3SignalEndpointResponses:
    """Test V3 signal endpoint response codes and error handling"""

    def test_valid_symbols_return_200(self):
        """Valid symbols MNQ and MES should return 200"""
        for symbol in ['MNQ', 'MES']:
            response = requests.get(f"{BASE_URL}/api/v3/signal/{symbol}", timeout=30)
            assert response.status_code == 200, f"{symbol} should return 200, got {response.status_code}"
            print(f"✅ GET /api/v3/signal/{symbol} returns 200")

    def test_invalid_symbol_returns_404(self):
        """Invalid symbol should return 404"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/INVALID", timeout=30)
        assert response.status_code == 404, f"Invalid symbol should return 404, got {response.status_code}"
        print("✅ GET /api/v3/signal/INVALID returns 404")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
