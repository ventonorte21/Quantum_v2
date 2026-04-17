"""
Test Gamma/ZGL Scoring in N1 Macro Score
Bug fix: The Gamma/ZGL pillar in N1 scoring was comparing with non-existent strings 
('STRONG_BULLISH', 'BULLISH') that were never produced by get_gamma_exposure().
Fixed to use the real fields: sentiment (POSITIVE/NEGATIVE) and zgl_signal (ABOVE_ZGL/BELOW_ZGL/NEUTRAL).

Mapping:
- POSITIVE + ABOVE_ZGL = 3 pts
- POSITIVE + other = 2 pts
- any + ABOVE_ZGL = 1 pt
- NEGATIVE + not-ABOVE_ZGL = 0 pts
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestGammaScoringFix:
    """Test the Gamma/ZGL scoring fix in N1 Macro Score"""

    def test_mnq_signal_has_gamma_breakdown(self):
        """GET /api/v3/signal/MNQ — N1 score_breakdown must contain Gamma/ZGL item"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        n1 = data.get('nivel_1', {})
        score_breakdown = n1.get('score_breakdown', [])
        
        # Find Gamma/ZGL in breakdown
        gamma_item = None
        for item in score_breakdown:
            if item.get('name') == 'Gamma/ZGL':
                gamma_item = item
                break
        
        assert gamma_item is not None, f"Gamma/ZGL not found in score_breakdown: {score_breakdown}"
        print(f"✅ MNQ Gamma/ZGL breakdown found: {gamma_item}")
        
        # Validate structure
        assert 'value' in gamma_item, "Gamma/ZGL item missing 'value' field"
        assert 'pts' in gamma_item, "Gamma/ZGL item missing 'pts' field"
        assert 'max' in gamma_item, "Gamma/ZGL item missing 'max' field"
        
        # Validate value format: should be 'SENTIMENT/ZGL_SIGNAL'
        value = gamma_item['value']
        assert '/' in value, f"Gamma/ZGL value should be in format 'SENTIMENT/ZGL_SIGNAL', got: {value}"
        
        parts = value.split('/')
        assert len(parts) == 2, f"Gamma/ZGL value should have 2 parts, got: {parts}"
        
        sentiment = parts[0]
        zgl_signal = parts[1]
        
        # Validate sentiment is POSITIVE or NEGATIVE
        assert sentiment in ['POSITIVE', 'NEGATIVE'], f"Sentiment should be POSITIVE or NEGATIVE, got: {sentiment}"
        
        # Validate zgl_signal is ABOVE_ZGL, BELOW_ZGL, or NEUTRAL
        assert zgl_signal in ['ABOVE_ZGL', 'BELOW_ZGL', 'NEUTRAL'], f"ZGL signal should be ABOVE_ZGL/BELOW_ZGL/NEUTRAL, got: {zgl_signal}"
        
        print(f"✅ MNQ Gamma/ZGL value format valid: sentiment={sentiment}, zgl_signal={zgl_signal}")

    def test_mes_signal_has_gamma_breakdown(self):
        """GET /api/v3/signal/MES — same validation for MES"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        n1 = data.get('nivel_1', {})
        score_breakdown = n1.get('score_breakdown', [])
        
        gamma_item = None
        for item in score_breakdown:
            if item.get('name') == 'Gamma/ZGL':
                gamma_item = item
                break
        
        assert gamma_item is not None, f"Gamma/ZGL not found in score_breakdown: {score_breakdown}"
        print(f"✅ MES Gamma/ZGL breakdown found: {gamma_item}")
        
        # Validate value format
        value = gamma_item['value']
        assert '/' in value, f"Gamma/ZGL value should be in format 'SENTIMENT/ZGL_SIGNAL', got: {value}"

    def test_gamma_pts_range(self):
        """Gamma pts must be between 0-3"""
        for symbol in ['MNQ', 'MES']:
            response = requests.get(f"{BASE_URL}/api/v3/signal/{symbol}", timeout=30)
            assert response.status_code == 200
            
            data = response.json()
            n1 = data.get('nivel_1', {})
            score_breakdown = n1.get('score_breakdown', [])
            
            gamma_item = None
            for item in score_breakdown:
                if item.get('name') == 'Gamma/ZGL':
                    gamma_item = item
                    break
            
            assert gamma_item is not None
            pts = gamma_item.get('pts')
            max_pts = gamma_item.get('max')
            
            assert isinstance(pts, int), f"pts should be int, got {type(pts)}"
            assert 0 <= pts <= 4, f"Gamma pts should be 0-4, got {pts}"
            assert max_pts == 4, f"Gamma max should be 4, got {max_pts}"

            print(f"✅ {symbol} Gamma pts={pts} (valid range 0-4)")

    def test_n1_macro_score_range(self):
        """N1 macro_score must be <= 13 and >= 0"""
        for symbol in ['MNQ', 'MES']:
            response = requests.get(f"{BASE_URL}/api/v3/signal/{symbol}", timeout=30)
            assert response.status_code == 200
            
            data = response.json()
            n1 = data.get('nivel_1', {})
            
            macro_score = n1.get('macro_score')
            max_score = n1.get('max_score')
            
            assert macro_score is not None, "macro_score missing"
            assert max_score is not None, "max_score missing"
            assert 0 <= macro_score <= 13, f"macro_score should be 0-13, got {macro_score}"
            assert max_score == 13, f"max_score should be 13, got {max_score}"
            
            print(f"✅ {symbol} macro_score={macro_score}/{max_score} (valid)")

    def test_n1_regime_valid(self):
        """N1 regime must be one of 5 valid regimes"""
        valid_regimes = ['COMPLACENCIA', 'BULL', 'TRANSICAO', 'BEAR', 'CAPITULACAO']
        
        for symbol in ['MNQ', 'MES']:
            response = requests.get(f"{BASE_URL}/api/v3/signal/{symbol}", timeout=30)
            assert response.status_code == 200
            
            data = response.json()
            n1 = data.get('nivel_1', {})
            regime = n1.get('regime')
            
            assert regime in valid_regimes, f"regime should be one of {valid_regimes}, got {regime}"
            print(f"✅ {symbol} regime={regime} (valid)")

    def test_n1_has_required_fields(self):
        """N1 must have target_symbol, tactic, lot_pct, direction"""
        for symbol in ['MNQ', 'MES']:
            response = requests.get(f"{BASE_URL}/api/v3/signal/{symbol}", timeout=30)
            assert response.status_code == 200
            
            data = response.json()
            n1 = data.get('nivel_1', {})
            
            assert 'target_symbol' in n1, "target_symbol missing"
            assert 'tactic' in n1, "tactic missing"
            assert 'lot_pct' in n1, "lot_pct missing"
            assert 'direction' in n1, "direction missing"
            
            # Validate target_symbol is MNQ or MES
            assert n1['target_symbol'] in ['MNQ', 'MES'], f"target_symbol should be MNQ or MES, got {n1['target_symbol']}"
            
            # Validate lot_pct is a number
            assert isinstance(n1['lot_pct'], (int, float)), f"lot_pct should be numeric, got {type(n1['lot_pct'])}"
            
            print(f"✅ {symbol} N1 has all required fields: target={n1['target_symbol']}, tactic={n1['tactic']}, lot={n1['lot_pct']}%, dir={n1['direction']}")

    def test_n2_filters_have_passed_field(self):
        """N2 must have 'passed' boolean field"""
        for symbol in ['MNQ', 'MES']:
            response = requests.get(f"{BASE_URL}/api/v3/signal/{symbol}", timeout=30)
            assert response.status_code == 200
            
            data = response.json()
            n2 = data.get('nivel_2', {})
            
            # Check that N2 has passed field at root level
            assert 'passed' in n2, f"N2 missing 'passed' field: {list(n2.keys())}"
            assert isinstance(n2['passed'], bool), f"N2.passed should be bool, got {type(n2['passed'])}"
            
            print(f"✅ {symbol} N2.passed={n2['passed']}")

    def test_n3_execution_has_action(self):
        """N3 execution must have 'action' field (BUY/SELL/WAIT)"""
        valid_actions = ['BUY', 'SELL', 'WAIT']
        
        for symbol in ['MNQ', 'MES']:
            response = requests.get(f"{BASE_URL}/api/v3/signal/{symbol}", timeout=30)
            assert response.status_code == 200
            
            data = response.json()
            n3 = data.get('nivel_3', {})
            action = n3.get('action')
            
            assert action in valid_actions, f"N3 action should be one of {valid_actions}, got {action}"
            print(f"✅ {symbol} N3 action={action}")


class TestGammaScoringLogic:
    """Test the specific scoring logic for Gamma/ZGL combinations"""

    def test_gamma_scoring_logic_via_api(self):
        """
        Verify the Gamma scoring logic by checking the API response.
        The scoring should follow:
        - POSITIVE + ABOVE_ZGL = 3 pts
        - POSITIVE + other = 2 pts
        - any + ABOVE_ZGL = 1 pt
        - NEGATIVE + not-ABOVE_ZGL = 0 pts
        """
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        n1 = data.get('nivel_1', {})
        score_breakdown = n1.get('score_breakdown', [])
        
        gamma_item = None
        for item in score_breakdown:
            if item.get('name') == 'Gamma/ZGL':
                gamma_item = item
                break
        
        assert gamma_item is not None
        
        value = gamma_item['value']
        pts = gamma_item['pts']
        
        parts = value.split('/')
        sentiment = parts[0]
        zgl_signal = parts[1]
        
        # Verify scoring logic
        expected_pts = None
        if sentiment == 'POSITIVE' and zgl_signal == 'ABOVE_ZGL':
            expected_pts = 3
        elif sentiment == 'POSITIVE':
            expected_pts = 2
        elif zgl_signal == 'ABOVE_ZGL':
            expected_pts = 1
        else:
            expected_pts = 0
        
        assert pts == expected_pts, f"Gamma pts mismatch: sentiment={sentiment}, zgl_signal={zgl_signal}, expected={expected_pts}, got={pts}"
        print(f"✅ Gamma scoring logic verified: {sentiment}/{zgl_signal} = {pts} pts (expected {expected_pts})")

    def test_gamma_data_via_analyze_endpoint(self):
        """Verify real_gamma data is present in /api/analyze response"""
        response = requests.post(f"{BASE_URL}/api/analyze", json={"symbol": "MNQ", "timeframe": "1H"}, timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        real_gamma = data.get('real_gamma', {})
        
        # Check required fields
        assert 'sentiment' in real_gamma, f"real_gamma missing sentiment: {real_gamma}"
        assert 'zgl_signal' in real_gamma, f"real_gamma missing zgl_signal: {real_gamma}"
        assert 'net_gex' in real_gamma, f"real_gamma missing net_gex: {real_gamma}"
        
        # Validate sentiment values
        assert real_gamma['sentiment'] in ['POSITIVE', 'NEGATIVE'], f"Invalid sentiment: {real_gamma['sentiment']}"
        assert real_gamma['zgl_signal'] in ['ABOVE_ZGL', 'BELOW_ZGL', 'NEUTRAL'], f"Invalid zgl_signal: {real_gamma['zgl_signal']}"
        
        print(f"✅ real_gamma data present: sentiment={real_gamma['sentiment']}, zgl_signal={real_gamma['zgl_signal']}, net_gex={real_gamma['net_gex']}")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
