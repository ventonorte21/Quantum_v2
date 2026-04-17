"""
Test ETF→Futures Conversion for Gamma Levels (Call Wall, Put Wall, ZGL)

This test verifies:
1. GET /api/v3/signal/{symbol} returns context with call_wall, put_wall, zgl in futures prices (>10000 for MNQ, >3000 for MES)
2. context.gamma.call_wall equals context.call_wall (both at root and nested)
3. POST /api/analyze returns real_gamma with futures_call_wall, futures_put_wall, futures_zgl, futures_ratio
4. futures_ratio > 30 for MNQ (QQQ ~$585 → MNQ ~$24000, ratio ~41x)
5. real_gamma.spot_price < 700 (ETF price) and futures_call_wall > 10000 (futures price)
6. N1 score_breakdown has Gamma/ZGL with pts 0-3 using SENTIMENT/ZGL_SIGNAL format
7. N1 macro_score <= 13 and regime in valid set
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestETFFuturesConversion:
    """Test ETF→Futures conversion for Gamma levels"""

    def test_v3_signal_mnq_context_has_futures_gamma_levels(self):
        """GET /api/v3/signal/MNQ — context must have call_wall, put_wall, zgl > 10000 (futures price)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        context = data.get('context', {})
        
        # Verify call_wall, put_wall, zgl exist at root level
        assert 'call_wall' in context, "context.call_wall missing"
        assert 'put_wall' in context, "context.put_wall missing"
        assert 'zgl' in context, "context.zgl missing"
        
        call_wall = context.get('call_wall', 0) or 0
        put_wall = context.get('put_wall', 0) or 0
        zgl = context.get('zgl', 0) or 0
        
        # MNQ futures price is ~24000, so gamma levels should be > 10000 (not ETF ~$590)
        print(f"MNQ context.call_wall: {call_wall}")
        print(f"MNQ context.put_wall: {put_wall}")
        print(f"MNQ context.zgl: {zgl}")
        
        # At least one of them should be > 10000 if conversion is working
        # (some may be 0 if no gamma data available)
        if call_wall > 0:
            assert call_wall > 10000, f"call_wall {call_wall} should be > 10000 (futures price, not ETF)"
        if put_wall > 0:
            assert put_wall > 10000, f"put_wall {put_wall} should be > 10000 (futures price, not ETF)"
        if zgl > 0:
            assert zgl > 10000, f"zgl {zgl} should be > 10000 (futures price, not ETF)"
        
        # At least one gamma level should be present
        assert call_wall > 0 or put_wall > 0 or zgl > 0, "At least one gamma level should be present"

    def test_v3_signal_mnq_context_gamma_nested_equals_root(self):
        """GET /api/v3/signal/MNQ — context.gamma.call_wall must equal context.call_wall"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=60)
        assert response.status_code == 200
        
        data = response.json()
        context = data.get('context', {})
        gamma_nested = context.get('gamma', {})
        
        # Verify nested gamma object exists
        assert 'gamma' in context, "context.gamma nested object missing"
        
        # Verify values match between root and nested
        root_call_wall = context.get('call_wall', 0)
        root_put_wall = context.get('put_wall', 0)
        root_zgl = context.get('zgl', 0)
        
        nested_call_wall = gamma_nested.get('call_wall', 0)
        nested_put_wall = gamma_nested.get('put_wall', 0)
        nested_zgl = gamma_nested.get('zgl', 0)
        
        print(f"Root: call_wall={root_call_wall}, put_wall={root_put_wall}, zgl={root_zgl}")
        print(f"Nested: call_wall={nested_call_wall}, put_wall={nested_put_wall}, zgl={nested_zgl}")
        
        assert root_call_wall == nested_call_wall, f"context.call_wall ({root_call_wall}) != context.gamma.call_wall ({nested_call_wall})"
        assert root_put_wall == nested_put_wall, f"context.put_wall ({root_put_wall}) != context.gamma.put_wall ({nested_put_wall})"
        assert root_zgl == nested_zgl, f"context.zgl ({root_zgl}) != context.gamma.zgl ({nested_zgl})"

    def test_v3_signal_mes_context_has_futures_gamma_levels(self):
        """GET /api/v3/signal/MES — context must have call_wall, put_wall, zgl > 3000 (futures price SPY→ES)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        context = data.get('context', {})
        
        call_wall = context.get('call_wall', 0) or 0
        put_wall = context.get('put_wall', 0) or 0
        zgl = context.get('zgl', 0) or 0
        
        # MES futures price is ~6100, so gamma levels should be > 3000 (not ETF ~$590)
        print(f"MES context.call_wall: {call_wall}")
        print(f"MES context.put_wall: {put_wall}")
        print(f"MES context.zgl: {zgl}")
        
        if call_wall > 0:
            assert call_wall > 3000, f"call_wall {call_wall} should be > 3000 (futures price, not ETF)"
        if put_wall > 0:
            assert put_wall > 3000, f"put_wall {put_wall} should be > 3000 (futures price, not ETF)"
        if zgl > 0:
            assert zgl > 3000, f"zgl {zgl} should be > 3000 (futures price, not ETF)"

    def test_analyze_mnq_real_gamma_has_futures_fields(self):
        """POST /api/analyze {symbol:MNQ,timeframe:5m} — real_gamma must have futures_* fields"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "5M"},
            timeout=60
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        real_gamma = data.get('real_gamma', {})
        
        # Verify futures_* fields exist
        assert 'futures_call_wall' in real_gamma, "real_gamma.futures_call_wall missing"
        assert 'futures_put_wall' in real_gamma, "real_gamma.futures_put_wall missing"
        assert 'futures_zgl' in real_gamma, "real_gamma.futures_zgl missing"
        assert 'futures_ratio' in real_gamma, "real_gamma.futures_ratio missing"
        
        print(f"real_gamma.futures_call_wall: {real_gamma.get('futures_call_wall')}")
        print(f"real_gamma.futures_put_wall: {real_gamma.get('futures_put_wall')}")
        print(f"real_gamma.futures_zgl: {real_gamma.get('futures_zgl')}")
        print(f"real_gamma.futures_ratio: {real_gamma.get('futures_ratio')}")

    def test_analyze_mnq_futures_ratio_greater_than_30(self):
        """POST /api/analyze — real_gamma.futures_ratio must be > 30 for MNQ (QQQ ~$585 → MNQ ~$24000)"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"},
            timeout=60
        )
        assert response.status_code == 200
        
        data = response.json()
        real_gamma = data.get('real_gamma', {})
        futures_ratio = real_gamma.get('futures_ratio', 0) or 0
        
        print(f"MNQ futures_ratio: {futures_ratio}")
        
        # QQQ ~$585, MNQ ~$24000, ratio should be ~41x
        # Allow some tolerance: > 30 is reasonable
        assert futures_ratio > 30, f"futures_ratio {futures_ratio} should be > 30 for MNQ (QQQ→MNQ conversion)"

    def test_analyze_mnq_spot_price_vs_futures_levels(self):
        """POST /api/analyze — spot_price < 700 (ETF) and futures_call_wall > 10000 (futures)"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"},
            timeout=60
        )
        assert response.status_code == 200
        
        data = response.json()
        real_gamma = data.get('real_gamma', {})
        
        spot_price = real_gamma.get('spot_price', 0) or 0
        futures_call_wall = real_gamma.get('futures_call_wall', 0) or 0
        futures_put_wall = real_gamma.get('futures_put_wall', 0) or 0
        futures_zgl = real_gamma.get('futures_zgl', 0) or 0
        
        print(f"spot_price (ETF): {spot_price}")
        print(f"futures_call_wall: {futures_call_wall}")
        print(f"futures_put_wall: {futures_put_wall}")
        print(f"futures_zgl: {futures_zgl}")
        
        # spot_price is ETF price (QQQ ~$585), should be < 700
        assert spot_price < 700, f"spot_price {spot_price} should be < 700 (ETF price)"
        assert spot_price > 100, f"spot_price {spot_price} should be > 100 (sanity check)"
        
        # futures_* levels should be > 10000 (MNQ futures price range)
        if futures_call_wall:
            assert futures_call_wall > 10000, f"futures_call_wall {futures_call_wall} should be > 10000"
        if futures_put_wall:
            assert futures_put_wall > 10000, f"futures_put_wall {futures_put_wall} should be > 10000"
        if futures_zgl:
            assert futures_zgl > 10000, f"futures_zgl {futures_zgl} should be > 10000"

    def test_v3_signal_n1_gamma_scoring_format(self):
        """N1 score_breakdown must have Gamma/ZGL with pts 0-3 using SENTIMENT/ZGL_SIGNAL format"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=60)
        assert response.status_code == 200
        
        data = response.json()
        n1 = data.get('nivel_1', {})
        score_breakdown = n1.get('score_breakdown', [])
        
        # Find Gamma/ZGL entry
        gamma_entry = None
        for entry in score_breakdown:
            if entry.get('name') == 'Gamma/ZGL':
                gamma_entry = entry
                break
        
        assert gamma_entry is not None, "Gamma/ZGL entry not found in score_breakdown"
        
        # Verify format: value should be "SENTIMENT/ZGL_SIGNAL"
        value = gamma_entry.get('value', '')
        pts = gamma_entry.get('pts', -1)
        max_pts = gamma_entry.get('max', 0)
        
        print(f"Gamma/ZGL value: {value}")
        print(f"Gamma/ZGL pts: {pts}/{max_pts}")
        
        # Value should contain "/" separator (e.g., "POSITIVE/BELOW_ZGL")
        assert '/' in str(value), f"Gamma/ZGL value '{value}' should be in SENTIMENT/ZGL_SIGNAL format"
        
        # pts should be 0-3
        assert 0 <= pts <= 3, f"Gamma/ZGL pts {pts} should be 0-3"
        assert max_pts == 3, f"Gamma/ZGL max should be 3, got {max_pts}"
        
        # Validate sentiment and zgl_signal values
        parts = str(value).split('/')
        assert len(parts) == 2, f"Expected 2 parts in '{value}'"
        sentiment, zgl_signal = parts
        assert sentiment in ['POSITIVE', 'NEGATIVE'], f"Invalid sentiment: {sentiment}"
        assert zgl_signal in ['ABOVE_ZGL', 'BELOW_ZGL', 'NEUTRAL'], f"Invalid zgl_signal: {zgl_signal}"

    def test_v3_signal_n1_macro_score_and_regime(self):
        """N1 macro_score <= 13 and regime in valid set"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=60)
        assert response.status_code == 200
        
        data = response.json()
        n1 = data.get('nivel_1', {})
        
        macro_score = n1.get('macro_score', 0)
        max_score = n1.get('max_score', 13)
        regime = n1.get('regime', '')
        
        print(f"macro_score: {macro_score}/{max_score}")
        print(f"regime: {regime}")
        
        # macro_score should be <= 13
        assert macro_score <= 13, f"macro_score {macro_score} should be <= 13"
        assert macro_score >= 0, f"macro_score {macro_score} should be >= 0"
        
        # regime should be one of 5 valid values
        valid_regimes = ['COMPLACENCIA', 'BULL', 'TRANSICAO', 'BEAR', 'CAPITULACAO']
        assert regime in valid_regimes, f"regime '{regime}' not in {valid_regimes}"

    def test_analyze_mes_futures_ratio_greater_than_8(self):
        """POST /api/analyze MES — futures_ratio > 8 (SPY ~$590 → MES ~$6100, ratio ~10x)"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MES", "timeframe": "1H"},
            timeout=60
        )
        assert response.status_code == 200
        
        data = response.json()
        real_gamma = data.get('real_gamma', {})
        futures_ratio = real_gamma.get('futures_ratio', 0) or 0
        
        print(f"MES futures_ratio: {futures_ratio}")
        
        # SPY ~$590, MES ~$6100, ratio should be ~10x
        assert futures_ratio > 8, f"MES futures_ratio {futures_ratio} should be > 8"

    def test_v3_signal_context_current_price_is_futures(self):
        """Verify context.current_price is futures price (not ETF)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=60)
        assert response.status_code == 200
        
        data = response.json()
        context = data.get('context', {})
        current_price = context.get('current_price', 0)
        
        print(f"MNQ context.current_price: {current_price}")
        
        # MNQ futures price should be > 10000 (not ETF ~$590)
        assert current_price > 10000, f"current_price {current_price} should be > 10000 (futures price)"


class TestPositionEvaluateUsesConvertedGamma:
    """Test that position evaluation endpoints use converted gamma levels"""

    def test_positions_evaluate_uses_futures_gamma(self):
        """POST /api/positions/evaluate/MNQ should use futures gamma levels"""
        # POST endpoint requires a position to exist - skip if no position
        response = requests.post(f"{BASE_URL}/api/positions/evaluate/MNQ", timeout=60)
        # This endpoint may return 404 if no position exists, which is OK
        if response.status_code == 404:
            pytest.skip("No active position for MNQ - skipping")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # If there's context data, verify gamma levels are in futures range
        context = data.get('context', {})
        if context:
            call_wall = context.get('call_wall', 0) or 0
            if call_wall > 0:
                assert call_wall > 10000, f"Position evaluate call_wall {call_wall} should be > 10000"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
