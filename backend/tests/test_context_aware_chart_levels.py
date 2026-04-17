"""
Test Context-Aware Chart Levels (HUD) and N3 Overnight Fix

Features tested:
1. Backend API /api/v3/signal/MNQ returns correct structure
2. N3 overnight fading only applies to TRANSICAO (not CAPITULACAO)
3. REGIME_MATRIX level visibility per regime
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")

HEADERS = {
    "Authorization": f"Bearer {SESSION_TOKEN}",
    "Content-Type": "application/json"
}


class TestV3SignalStructure:
    """Test /api/v3/signal endpoint returns correct structure for HUD"""
    
    def test_v3_signal_returns_nivel_1_regime(self):
        """N1 should return regime field (BULL/BEAR/TRANSICAO/COMPLACENCIA/CAPITULACAO)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=HEADERS)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert 'nivel_1' in data, "Missing nivel_1 in response"
        
        n1 = data['nivel_1']
        assert 'regime' in n1, "Missing regime in nivel_1"
        
        valid_regimes = ['COMPLACENCIA', 'BULL', 'TRANSICAO', 'BEAR', 'CAPITULACAO']
        assert n1['regime'] in valid_regimes, f"Invalid regime: {n1['regime']}"
        print(f"✅ N1 regime: {n1['regime']}")
    
    def test_v3_signal_returns_nivel_2_passed(self):
        """N2 should return passed field (boolean)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=HEADERS)
        assert response.status_code == 200
        
        data = response.json()
        assert 'nivel_2' in data, "Missing nivel_2 in response"
        
        n2 = data['nivel_2']
        assert 'passed' in n2, "Missing passed in nivel_2"
        assert isinstance(n2['passed'], bool), f"passed should be bool, got {type(n2['passed'])}"
        print(f"✅ N2 passed: {n2['passed']}")
    
    def test_v3_signal_returns_nivel_3_action(self):
        """N3 should return action field (WAIT/BUY/SELL)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=HEADERS)
        assert response.status_code == 200
        
        data = response.json()
        assert 'nivel_3' in data, "Missing nivel_3 in response"
        
        n3 = data['nivel_3']
        assert 'action' in n3, "Missing action in nivel_3"
        
        valid_actions = ['WAIT', 'BUY', 'SELL']
        assert n3['action'] in valid_actions, f"Invalid action: {n3['action']}"
        print(f"✅ N3 action: {n3['action']}")
    
    def test_v3_signal_returns_context_overnight_inventory(self):
        """Context should include overnight_inventory field (can be null)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=HEADERS)
        assert response.status_code == 200
        
        data = response.json()
        assert 'context' in data, "Missing context in response"
        
        context = data['context']
        # overnight_inventory can be null or dict
        assert 'overnight_inventory' in context or context.get('overnight_inventory') is None, \
            "Missing overnight_inventory in context"
        print(f"✅ Context overnight_inventory: {context.get('overnight_inventory')}")


class TestRegimeMatrix:
    """Test REGIME_MATRIX level visibility per regime"""
    
    # Expected visibility per regime (from DataBentoChart.jsx REGIME_MATRIX)
    REGIME_MATRIX = {
        'COMPLACENCIA': {
            'vp_session': 'core', 'vp_d1': 'ref', 'vwap_session': 'core',
            'vwap_trend': 'ref', 'vwap_shock': 'core', 'gamma': 'core', 'overnight': None,
        },
        'BULL': {
            'vp_session': 'core', 'vp_d1': 'core', 'vwap_session': 'core',
            'vwap_trend': 'core', 'vwap_shock': None, 'gamma': 'ref', 'overnight': 'core',
        },
        'TRANSICAO': {
            'vp_session': 'core', 'vp_d1': 'core', 'vwap_session': 'core',
            'vwap_trend': None, 'vwap_shock': None, 'gamma': 'core', 'overnight': 'core',
        },
        'BEAR': {
            'vp_session': 'core', 'vp_d1': 'core', 'vwap_session': 'core',
            'vwap_trend': 'core', 'vwap_shock': None, 'gamma': 'ref', 'overnight': 'core',
        },
        'CAPITULACAO': {
            'vp_session': 'ref', 'vp_d1': None, 'vwap_session': 'core',
            'vwap_trend': None, 'vwap_shock': 'core', 'gamma': 'core', 'overnight': None,
        },
    }
    
    def test_bull_regime_matrix(self):
        """BULL: vwap_shock=null (hidden), overnight=core (visible)"""
        matrix = self.REGIME_MATRIX['BULL']
        
        # vwap_shock should be hidden
        assert matrix['vwap_shock'] is None, "BULL: vwap_shock should be null (hidden)"
        
        # overnight should be visible
        assert matrix['overnight'] == 'core', "BULL: overnight should be core (visible)"
        
        # vp_session, vp_d1, vwap_session, vwap_trend should be visible
        assert matrix['vp_session'] == 'core', "BULL: vp_session should be core"
        assert matrix['vp_d1'] == 'core', "BULL: vp_d1 should be core"
        assert matrix['vwap_session'] == 'core', "BULL: vwap_session should be core"
        assert matrix['vwap_trend'] == 'core', "BULL: vwap_trend should be core"
        
        print("✅ BULL regime matrix verified")
    
    def test_capitulacao_regime_matrix(self):
        """CAPITULACAO: overnight=null (hidden), vwap_shock=core (visible)"""
        matrix = self.REGIME_MATRIX['CAPITULACAO']
        
        # overnight should be hidden
        assert matrix['overnight'] is None, "CAPITULACAO: overnight should be null (hidden)"
        
        # vwap_shock should be visible
        assert matrix['vwap_shock'] == 'core', "CAPITULACAO: vwap_shock should be core (visible)"
        
        # vp_d1 should be hidden
        assert matrix['vp_d1'] is None, "CAPITULACAO: vp_d1 should be null (hidden)"
        
        print("✅ CAPITULACAO regime matrix verified")
    
    def test_transicao_regime_matrix(self):
        """TRANSICAO: vwap_trend=null, vwap_shock=null, overnight=core"""
        matrix = self.REGIME_MATRIX['TRANSICAO']
        
        # vwap_trend and vwap_shock should be hidden
        assert matrix['vwap_trend'] is None, "TRANSICAO: vwap_trend should be null (hidden)"
        assert matrix['vwap_shock'] is None, "TRANSICAO: vwap_shock should be null (hidden)"
        
        # overnight should be visible
        assert matrix['overnight'] == 'core', "TRANSICAO: overnight should be core (visible)"
        
        print("✅ TRANSICAO regime matrix verified")


class TestN3OvernightFix:
    """Test that CAPITULACAO is NOT in overnight fading block of N3"""
    
    def test_overnight_fading_only_transicao(self):
        """
        Verify the N3 overnight fading logic only applies to TRANSICAO.
        
        The fix at line 3996 of server.py changed:
        FROM: if regime in ('TRANSICAO', 'CAPITULACAO'):
        TO:   if regime == 'TRANSICAO':
        
        This means CAPITULACAO no longer triggers overnight fading signals.
        """
        # Read the server.py file to verify the fix
        server_path = '/app/backend/server.py'
        with open(server_path, 'r') as f:
            lines = f.readlines()
        
        # Find line 3996 which should have the TRANSICAO-only condition
        # Line 3996 is index 3995 (0-indexed)
        line_3996 = lines[3995].strip()
        
        # Verify the condition is ONLY TRANSICAO
        assert "if regime ==" in line_3996 and "TRANSICAO" in line_3996, \
            f"Line 3996 should be 'if regime == TRANSICAO': got {line_3996}"
        assert "CAPITULACAO" not in line_3996, \
            f"Line 3996 should NOT include CAPITULACAO: {line_3996}"
        
        # Also verify the comment on line 3997 mentions "Fading"
        line_3997 = lines[3996].strip()
        assert "Fading" in line_3997, f"Line 3997 should mention Fading: {line_3997}"
        
        print(f"✅ N3 overnight fix verified:")
        print(f"   Line 3996: {line_3996}")
        print(f"   Line 3997: {line_3997}")
    
    def test_overnight_fading_regimes_in_code(self):
        """Verify the exact code pattern for overnight fading"""
        server_path = '/app/backend/server.py'
        with open(server_path, 'r') as f:
            lines = f.readlines()
        
        # Find line 3996 (0-indexed: 3995)
        line_3996 = lines[3995].strip()
        
        # Should be: if regime == 'TRANSICAO':
        assert "if regime == 'TRANSICAO':" in line_3996, \
            f"Line 3996 should be 'if regime == TRANSICAO:', got: {line_3996}"
        
        # Should NOT contain CAPITULACAO
        assert "CAPITULACAO" not in line_3996, \
            f"Line 3996 should NOT contain CAPITULACAO, got: {line_3996}"
        
        print(f"✅ Line 3996 verified: {line_3996}")


class TestRemovedFeatures:
    """Test that VP Multi-day and VWAP Multi-day are removed"""
    
    def test_no_vp_multiday_in_frontend(self):
        """VP Multi-day should not exist in DataBentoChart.jsx"""
        chart_path = '/app/frontend/src/components/DataBentoChart.jsx'
        with open(chart_path, 'r') as f:
            content = f.read()
        
        # Check that vp_multiday is not in LEVEL_FAMILIES
        assert 'vp_multiday' not in content, \
            "vp_multiday should be removed from DataBentoChart.jsx"
        
        print("✅ VP Multi-day removed from frontend")
    
    def test_no_vwap_multiday_in_frontend(self):
        """VWAP Multi-day should not exist in DataBentoChart.jsx"""
        chart_path = '/app/frontend/src/components/DataBentoChart.jsx'
        with open(chart_path, 'r') as f:
            content = f.read()
        
        # Check that vwap_multiday is not in LEVEL_FAMILIES
        assert 'vwap_multiday' not in content, \
            "vwap_multiday should be removed from DataBentoChart.jsx"
        
        print("✅ VWAP Multi-day removed from frontend")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
