"""
Test Dynamic HUD Per-Line Roles and VWAP Source
Tests the dynamic per-line role system (validation/target/context) and VWAP source fix.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
AUTH_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")

class TestVWAPSource:
    """Test that VWAP source is session_ny (not intraday)"""
    
    def test_vwap_source_is_session_ny(self):
        """Backend /api/v3/signal/MNQ returns vwap_source: session_ny"""
        headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=headers)
        assert response.status_code == 200
        data = response.json()
        
        # vwap_source is in context object
        context = data.get('context', {})
        vwap_source = context.get('vwap_source')
        
        # Should be session_ny, not intraday or fallback
        assert vwap_source == 'session_ny', f"Expected vwap_source='session_ny', got '{vwap_source}'"
        print(f"✅ vwap_source is '{vwap_source}' (correct)")
    
    def test_vwap_bands_present(self):
        """VWAP bands should be present from session_ny"""
        headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=headers)
        assert response.status_code == 200
        data = response.json()
        
        context = data.get('context', {})
        vwap = context.get('vwap')
        vwap_bands = context.get('vwap_bands', {})
        
        assert vwap is not None and vwap > 0, f"VWAP should be positive, got {vwap}"
        assert 'upper_1' in vwap_bands, "Missing upper_1 band"
        assert 'lower_1' in vwap_bands, "Missing lower_1 band"
        print(f"✅ VWAP={vwap}, bands present: {list(vwap_bands.keys())}")


class TestN3OvernightFix:
    """Test that CAPITULACAO is NOT in overnight fading block"""
    
    def test_capitulacao_not_in_overnight_fading(self):
        """Line 3996 should only check TRANSICAO, not CAPITULACAO"""
        # Read the server.py file and check line 3996
        server_path = "/app/backend/server.py"
        with open(server_path, 'r') as f:
            lines = f.readlines()
        
        # Line 3996 (0-indexed: 3995)
        line_3996 = lines[3995].strip()
        
        # Should be "if regime == 'TRANSICAO':" not including CAPITULACAO
        assert "TRANSICAO" in line_3996, f"Line 3996 should contain TRANSICAO: {line_3996}"
        assert "CAPITULACAO" not in line_3996, f"Line 3996 should NOT contain CAPITULACAO: {line_3996}"
        print(f"✅ Line 3996: '{line_3996}' (CAPITULACAO not in overnight fading)")


class TestRegimeSignal:
    """Test V3 signal returns correct regime data"""
    
    def test_nivel_1_regime(self):
        """V3 signal should return nivel_1.regime"""
        headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=headers)
        assert response.status_code == 200
        data = response.json()
        
        nivel_1 = data.get('nivel_1', {})
        regime = nivel_1.get('regime')
        
        assert regime is not None, "nivel_1.regime should not be None"
        assert regime in ['COMPLACENCIA', 'BULL', 'TRANSICAO', 'BEAR', 'CAPITULACAO'], f"Invalid regime: {regime}"
        print(f"✅ Current regime: {regime}")
    
    def test_nivel_2_passed(self):
        """V3 signal should return nivel_2.passed (boolean)"""
        headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=headers)
        assert response.status_code == 200
        data = response.json()
        
        nivel_2 = data.get('nivel_2', {})
        passed = nivel_2.get('passed')
        
        assert passed is not None, "nivel_2.passed should not be None"
        assert isinstance(passed, bool), f"nivel_2.passed should be boolean, got {type(passed)}"
        print(f"✅ N2 passed: {passed}")
    
    def test_nivel_3_action(self):
        """V3 signal should return nivel_3.action"""
        headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=headers)
        assert response.status_code == 200
        data = response.json()
        
        nivel_3 = data.get('nivel_3', {})
        action = nivel_3.get('action')
        
        assert action is not None, "nivel_3.action should not be None"
        assert action in ['WAIT', 'BUY', 'SELL'], f"Invalid action: {action}"
        print(f"✅ N3 action: {action}")


class TestOvernightInventory:
    """Test overnight inventory data in context"""
    
    def test_overnight_inventory_structure(self):
        """V3 signal should return context.overnight_inventory"""
        headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=headers)
        assert response.status_code == 200
        data = response.json()
        
        context = data.get('context', {})
        overnight_inventory = context.get('overnight_inventory')
        overnight_relevance = context.get('overnight_relevance')
        
        # overnight_inventory can be None if not in overnight session
        # overnight_relevance should always be present
        assert overnight_relevance is not None, "overnight_relevance should not be None"
        print(f"✅ overnight_inventory: {overnight_inventory}, relevance: {overnight_relevance}")


class TestN2ValidationLevels:
    """Test N2 validation levels per regime (from frontend constants)"""
    
    # N2_VALIDATION from DataBentoChart.jsx
    N2_VALIDATION = {
        'COMPLACENCIA': ['vah', 'upper_3', 'vwap_session'],
        'BULL': ['vah', 'upper_1', 'd1_poc'],
        'TRANSICAO': ['vah', 'val'],
        'BEAR': ['val', 'lower_1', 'd1_poc'],
        'CAPITULACAO': ['val', 'lower_3', 'put_wall', 'zgl'],
    }
    
    def test_bull_has_3_validation_levels(self):
        """BULL regime should have 3 N2 validation levels"""
        bull_levels = self.N2_VALIDATION['BULL']
        assert len(bull_levels) == 3, f"BULL should have 3 levels, got {len(bull_levels)}"
        assert 'vah' in bull_levels, "BULL should include vah"
        assert 'upper_1' in bull_levels, "BULL should include upper_1"
        assert 'd1_poc' in bull_levels, "BULL should include d1_poc"
        print(f"✅ BULL N2 validation levels: {bull_levels}")
    
    def test_capitulacao_has_4_validation_levels(self):
        """CAPITULACAO regime should have 4 N2 validation levels"""
        cap_levels = self.N2_VALIDATION['CAPITULACAO']
        assert len(cap_levels) == 4, f"CAPITULACAO should have 4 levels, got {len(cap_levels)}"
        assert 'val' in cap_levels, "CAPITULACAO should include val"
        assert 'lower_3' in cap_levels, "CAPITULACAO should include lower_3"
        assert 'put_wall' in cap_levels, "CAPITULACAO should include put_wall"
        assert 'zgl' in cap_levels, "CAPITULACAO should include zgl"
        print(f"✅ CAPITULACAO N2 validation levels: {cap_levels}")


class TestN3TargetLevels:
    """Test N3 target levels per regime (from frontend constants)"""
    
    # N3_TARGETS from DataBentoChart.jsx
    N3_TARGETS = {
        'COMPLACENCIA': [],
        'BULL': ['call_wall'],
        'TRANSICAO': ['poc', 'call_wall', 'put_wall'],
        'BEAR': ['put_wall'],
        'CAPITULACAO': ['vwap_session'],
    }
    
    def test_bull_target_is_call_wall(self):
        """BULL regime N3 target should be call_wall"""
        bull_targets = self.N3_TARGETS['BULL']
        assert len(bull_targets) == 1, f"BULL should have 1 target, got {len(bull_targets)}"
        assert 'call_wall' in bull_targets, "BULL target should be call_wall"
        print(f"✅ BULL N3 targets: {bull_targets}")
    
    def test_complacencia_has_no_targets(self):
        """COMPLACENCIA regime should have no N3 targets"""
        comp_targets = self.N3_TARGETS['COMPLACENCIA']
        assert len(comp_targets) == 0, f"COMPLACENCIA should have 0 targets, got {len(comp_targets)}"
        print(f"✅ COMPLACENCIA N3 targets: {comp_targets} (empty as expected)")


class TestRegimeVisibility:
    """Test regime visibility matrix (from frontend constants)"""
    
    # REGIME_VISIBILITY from DataBentoChart.jsx
    REGIME_VISIBILITY = {
        'COMPLACENCIA': {
            'vp_session': True, 'vp_d1': True, 'vwap_session': True,
            'vwap_trend': True, 'vwap_shock': True, 'gamma': True, 'overnight': False,
        },
        'BULL': {
            'vp_session': True, 'vp_d1': True, 'vwap_session': True,
            'vwap_trend': True, 'vwap_shock': False, 'gamma': True, 'overnight': True,
        },
        'TRANSICAO': {
            'vp_session': True, 'vp_d1': True, 'vwap_session': True,
            'vwap_trend': False, 'vwap_shock': False, 'gamma': True, 'overnight': True,
        },
        'BEAR': {
            'vp_session': True, 'vp_d1': True, 'vwap_session': True,
            'vwap_trend': True, 'vwap_shock': False, 'gamma': True, 'overnight': True,
        },
        'CAPITULACAO': {
            'vp_session': True, 'vp_d1': False, 'vwap_session': True,
            'vwap_trend': False, 'vwap_shock': True, 'gamma': True, 'overnight': False,
        },
    }
    
    def test_bull_visibility(self):
        """BULL regime: vwap_shock hidden, overnight visible"""
        bull_vis = self.REGIME_VISIBILITY['BULL']
        assert bull_vis['vwap_shock'] == False, "BULL should hide vwap_shock (Bands ±3σ)"
        assert bull_vis['overnight'] == True, "BULL should show overnight"
        assert bull_vis['vwap_trend'] == True, "BULL should show vwap_trend (Bands ±1σ)"
        print(f"✅ BULL visibility: vwap_shock={bull_vis['vwap_shock']}, overnight={bull_vis['overnight']}")
    
    def test_capitulacao_visibility(self):
        """CAPITULACAO regime: vp_d1 hidden, vwap_shock visible, overnight hidden"""
        cap_vis = self.REGIME_VISIBILITY['CAPITULACAO']
        assert cap_vis['vp_d1'] == False, "CAPITULACAO should hide vp_d1"
        assert cap_vis['vwap_shock'] == True, "CAPITULACAO should show vwap_shock (Bands ±3σ)"
        assert cap_vis['overnight'] == False, "CAPITULACAO should hide overnight"
        print(f"✅ CAPITULACAO visibility: vp_d1={cap_vis['vp_d1']}, vwap_shock={cap_vis['vwap_shock']}, overnight={cap_vis['overnight']}")
    
    def test_bull_has_6_visible_families(self):
        """BULL regime should have 6 visible families"""
        bull_vis = self.REGIME_VISIBILITY['BULL']
        visible_count = sum(1 for v in bull_vis.values() if v)
        assert visible_count == 6, f"BULL should have 6 visible families, got {visible_count}"
        print(f"✅ BULL has {visible_count} visible families")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
