"""
Test VWAP and VP Session Reset - Globex vs NY
Tests the surgical reset of VWAP and VP sessions:
- VWAP Globex starts at 18:00 ET
- VWAP NY starts at 09:30 ET
- VP Globex starts at 18:00 ET
- VP NY (rth_vp) starts at 09:30 ET
- Position sizing: Globex = 50% of base lot
- ONH/ONL = max/min prices from Globex session (18:00-09:30 ET)
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
AUTH_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")

@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {AUTH_TOKEN}"}


class TestSessionVWAPsEndpoint:
    """Tests for GET /api/session-vwaps/MNQ"""
    
    def test_session_vwaps_returns_globex_and_session_ny_keys(self, auth_headers):
        """Verify response contains 'globex' and 'session_ny' keys (not 'intraday')"""
        response = requests.get(f"{BASE_URL}/api/session-vwaps/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        # Must have globex and session_ny keys
        assert "globex" in data, "Missing 'globex' key in session-vwaps response"
        assert "session_ny" in data, "Missing 'session_ny' key in session-vwaps response"
        
        # Must NOT have 'intraday' key (renamed to 'globex')
        assert "intraday" not in data, "Found deprecated 'intraday' key - should be 'globex'"
    
    def test_globex_vwap_has_real_data(self, auth_headers):
        """Verify globex VWAP has vwap > 0, candle_count > 0, label contains 'Globex'"""
        response = requests.get(f"{BASE_URL}/api/session-vwaps/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        globex = data.get("globex")
        assert globex is not None, "globex VWAP is None"
        
        # Verify real data
        assert globex.get("vwap", 0) > 0, f"globex vwap should be > 0, got {globex.get('vwap')}"
        assert globex.get("candle_count", 0) > 0, f"globex candle_count should be > 0, got {globex.get('candle_count')}"
        assert "Globex" in globex.get("label", ""), f"globex label should contain 'Globex', got {globex.get('label')}"
        
        # Verify bands exist
        assert globex.get("upper_1", 0) > 0, "globex upper_1 band missing"
        assert globex.get("lower_1", 0) > 0, "globex lower_1 band missing"
        assert globex.get("upper_3", 0) > 0, "globex upper_3 band missing"
        assert globex.get("lower_3", 0) > 0, "globex lower_3 band missing"
    
    def test_session_ny_vwap_has_correct_label(self, auth_headers):
        """Verify session_ny VWAP has label containing 'NY' or 'RTH'"""
        response = requests.get(f"{BASE_URL}/api/session-vwaps/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        session_ny = data.get("session_ny")
        assert session_ny is not None, "session_ny VWAP is None"
        
        label = session_ny.get("label", "")
        assert "NY" in label or "RTH" in label, f"session_ny label should contain 'NY' or 'RTH', got {label}"


class TestV3SignalEndpoint:
    """Tests for GET /api/v3/signal/MNQ during Globex session"""
    
    def test_context_session_type_is_globex(self, auth_headers):
        """Verify context.session_type = 'globex' during Globex session (~22:30 ET)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        context = data.get("context", {})
        assert context.get("session_type") == "globex", f"Expected session_type='globex', got {context.get('session_type')}"
    
    def test_context_vwap_source_is_globex(self, auth_headers):
        """Verify context.vwap_source = 'globex' (not 'unavailable')"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        context = data.get("context", {})
        vwap_source = context.get("vwap_source")
        assert vwap_source == "globex", f"Expected vwap_source='globex', got {vwap_source}"
        assert vwap_source != "unavailable", "vwap_source should not be 'unavailable'"
    
    def test_context_vwap_has_real_value(self, auth_headers):
        """Verify context.vwap > 0 with real VWAP Globex value"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        context = data.get("context", {})
        vwap = context.get("vwap", 0)
        assert vwap > 0, f"Expected vwap > 0, got {vwap}"
        # MNQ VWAP should be in reasonable range (20000-30000)
        assert 20000 < vwap < 30000, f"VWAP value {vwap} seems unreasonable for MNQ"
    
    def test_context_vwap_bands_all_positive(self, auth_headers):
        """Verify context.vwap_bands with upper_1, lower_1, upper_3, lower_3 all > 0"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        context = data.get("context", {})
        vwap_bands = context.get("vwap_bands", {})
        
        assert vwap_bands.get("upper_1", 0) > 0, f"upper_1 should be > 0, got {vwap_bands.get('upper_1')}"
        assert vwap_bands.get("lower_1", 0) > 0, f"lower_1 should be > 0, got {vwap_bands.get('lower_1')}"
        assert vwap_bands.get("upper_3", 0) > 0, f"upper_3 should be > 0, got {vwap_bands.get('upper_3')}"
        assert vwap_bands.get("lower_3", 0) > 0, f"lower_3 should be > 0, got {vwap_bands.get('lower_3')}"
    
    def test_context_vp_source_is_globex(self, auth_headers):
        """Verify context.vp_source = 'globex' (not 'd-1')"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        context = data.get("context", {})
        vp_source = context.get("vp_source")
        assert vp_source == "globex", f"Expected vp_source='globex', got {vp_source}"
    
    def test_nivel_1_session_type_is_globex(self, auth_headers):
        """Verify nivel_1.session_type = 'globex'"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        nivel_1 = data.get("nivel_1", {})
        assert nivel_1.get("session_type") == "globex", f"Expected nivel_1.session_type='globex', got {nivel_1.get('session_type')}"
    
    def test_nivel_1_session_lot_warning_contains_globex(self, auth_headers):
        """Verify nivel_1.session_lot_warning contains 'Globex' and lot reduction info"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        nivel_1 = data.get("nivel_1", {})
        warning = nivel_1.get("session_lot_warning", "")
        
        assert "Globex" in warning, f"session_lot_warning should contain 'Globex', got {warning}"
        # Should mention lot reduction (50% or similar)
        assert "50%" in warning or "lote" in warning.lower(), f"session_lot_warning should mention lot reduction, got {warning}"
    
    def test_lot_pct_is_halved_during_globex(self, auth_headers):
        """Verify lot_pct is halved vs REGIME_CONFIG base (e.g., TRANSICAO=50% → 25%)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        nivel_1 = data.get("nivel_1", {})
        lot_pct = nivel_1.get("lot_pct", 0)
        regime = nivel_1.get("regime", "")
        
        # During Globex, lot should be halved
        # TRANSICAO base = 50%, so Globex = 25%
        # TENDENCIA base = 100%, so Globex = 50%
        # CRISE base = 25%, so Globex = 12.5% (rounded to 12 or 13)
        if regime == "TRANSICAO":
            assert lot_pct == 25, f"TRANSICAO during Globex should have lot_pct=25, got {lot_pct}"
        elif regime == "TENDENCIA":
            assert lot_pct == 50, f"TENDENCIA during Globex should have lot_pct=50, got {lot_pct}"
        elif regime == "CRISE":
            assert lot_pct in [12, 13], f"CRISE during Globex should have lot_pct=12 or 13, got {lot_pct}"
        else:
            # Just verify it's reasonable (halved from base)
            assert 0 < lot_pct <= 50, f"lot_pct during Globex should be <= 50, got {lot_pct}"


class TestAnalyzeEndpoint:
    """Tests for POST /api/analyze with session_vps"""
    
    def test_session_vps_has_required_keys(self, auth_headers):
        """Verify session_vps has daily_vp, rth_vp, prev_day_vp keys"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200
        data = response.json()
        
        session_vps = data.get("session_vps", {})
        assert "daily_vp" in session_vps, "Missing 'daily_vp' in session_vps"
        assert "rth_vp" in session_vps, "Missing 'rth_vp' in session_vps"
        assert "prev_day_vp" in session_vps, "Missing 'prev_day_vp' in session_vps"
    
    def test_rth_vp_has_correct_session_and_start(self, auth_headers):
        """Verify rth_vp has session='rth' and session_start containing 09:30 ET (13:30 UTC)"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200
        data = response.json()
        
        rth_vp = data.get("session_vps", {}).get("rth_vp", {})
        assert rth_vp.get("session") == "rth", f"rth_vp.session should be 'rth', got {rth_vp.get('session')}"
        
        session_start = rth_vp.get("session_start", "")
        # 09:30 ET = 13:30 UTC (during EDT) or 14:30 UTC (during EST)
        assert "13:30" in session_start or "14:30" in session_start, \
            f"rth_vp.session_start should contain 13:30 or 14:30 UTC, got {session_start}"
    
    def test_daily_vp_starts_from_overnight(self, auth_headers):
        """Verify daily_vp starts from overnight_start (18:00 ET = 22:00 UTC or 23:00 UTC)"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200
        data = response.json()
        
        daily_vp = data.get("session_vps", {}).get("daily_vp", {})
        session_start = daily_vp.get("session_start", "")
        
        # 18:00 ET = 22:00 UTC (during EDT) or 23:00 UTC (during EST)
        assert "22:00" in session_start or "23:00" in session_start, \
            f"daily_vp.session_start should contain 22:00 or 23:00 UTC, got {session_start}"
    
    def test_session_vwaps_globex_has_candle_count(self, auth_headers):
        """Verify session_vwaps.globex has candle_count > 0"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert response.status_code == 200
        data = response.json()
        
        session_vwaps = data.get("session_vwaps", {})
        globex = session_vwaps.get("globex", {})
        
        candle_count = globex.get("candle_count", 0)
        assert candle_count > 0, f"session_vwaps.globex.candle_count should be > 0, got {candle_count}"


class TestDataIntegrity:
    """Additional data integrity tests"""
    
    def test_vwap_bands_are_symmetric(self, auth_headers):
        """Verify VWAP bands are symmetric around VWAP"""
        response = requests.get(f"{BASE_URL}/api/session-vwaps/MNQ", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        
        globex = data.get("globex", {})
        vwap = globex.get("vwap", 0)
        upper_1 = globex.get("upper_1", 0)
        lower_1 = globex.get("lower_1", 0)
        
        if vwap > 0:
            upper_dist = upper_1 - vwap
            lower_dist = vwap - lower_1
            # Should be approximately equal (within 1%)
            assert abs(upper_dist - lower_dist) < vwap * 0.01, \
                f"VWAP bands not symmetric: upper_dist={upper_dist}, lower_dist={lower_dist}"
    
    def test_session_times_are_consistent(self, auth_headers):
        """Verify session times are consistent across endpoints"""
        # Get session-vwaps
        vwaps_response = requests.get(f"{BASE_URL}/api/session-vwaps/MNQ", headers=auth_headers)
        assert vwaps_response.status_code == 200
        vwaps_data = vwaps_response.json()
        
        sessions = vwaps_data.get("sessions", {})
        overnight_start = sessions.get("overnight_start", "")
        
        # Get analyze
        analyze_response = requests.post(
            f"{BASE_URL}/api/analyze",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"symbol": "MNQ", "timeframe": "1H"}
        )
        assert analyze_response.status_code == 200
        analyze_data = analyze_response.json()
        
        daily_vp_start = analyze_data.get("session_vps", {}).get("daily_vp", {}).get("session_start", "")
        
        # Both should reference the same overnight start time
        assert overnight_start == daily_vp_start, \
            f"Session times inconsistent: overnight_start={overnight_start}, daily_vp_start={daily_vp_start}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
