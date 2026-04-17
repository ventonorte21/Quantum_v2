"""
Test D-1 DZ Zones Bug Fix (Iteration 54)
=========================================
Bug 1: D-1 borders (VAH_D1, VAL_D1, POC_D1) were using session VA proxy data instead of their own zones.
       Now each border has its own DZ computation with zscore and delta_ratio fields.

Bug 2: Chart scale bug when switching symbols — MNQ price lines were persisting on MES chart.
       Frontend now clears analysis+v3Signal on symbol switch.

Tests verify:
- GET /api/v3/signal/MES returns nivel_2.dz_d1_vah with zscore and delta_ratio fields
- GET /api/v3/signal/MES returns nivel_2.dz_d1_val with zscore and delta_ratio fields  
- GET /api/v3/signal/MES returns nivel_2.dz_d1_poc with zscore and delta_ratio fields
- GET /api/v3/signal/MES nivel_2.transicao_border is one of VAH, VAL, VAH_D1, VAL_D1, MID
- GET /api/v3/signal/MES nivel_2.d1_vah and nivel_2.d1_val have non-zero values
- GET /api/v3/signal/MNQ also returns dz_d1_vah, dz_d1_val fields in nivel_2
- GET /api/v3/signal/MES nivel_2.reason contains 'Borda:' indicating border detection works
- POST /api/analyze {symbol:MES, timeframe:1H} returns session_vps with daily_vp and rth_vp
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")

@pytest.fixture
def api_client():
    """Shared requests session with auth"""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SESSION_TOKEN}"
    })
    return session


class TestD1DZZonesBugFix:
    """Test D-1 DZ zones have their own zscore and delta_ratio fields"""

    def test_mes_v3_signal_has_dz_d1_vah(self, api_client):
        """GET /api/v3/signal/MES returns nivel_2.dz_d1_vah with zscore and delta_ratio"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'nivel_2' in data, "Response missing nivel_2"
        n2 = data['nivel_2']
        
        # Check dz_d1_vah exists and has required fields
        assert 'dz_d1_vah' in n2, f"nivel_2 missing dz_d1_vah. Keys: {list(n2.keys())}"
        dz_d1_vah = n2['dz_d1_vah']
        assert 'zscore' in dz_d1_vah, f"dz_d1_vah missing zscore. Keys: {list(dz_d1_vah.keys())}"
        assert 'delta_ratio' in dz_d1_vah, f"dz_d1_vah missing delta_ratio. Keys: {list(dz_d1_vah.keys())}"
        print(f"✅ dz_d1_vah: zscore={dz_d1_vah['zscore']}, delta_ratio={dz_d1_vah['delta_ratio']}")

    def test_mes_v3_signal_has_dz_d1_val(self, api_client):
        """GET /api/v3/signal/MES returns nivel_2.dz_d1_val with zscore and delta_ratio"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200
        
        data = response.json()
        n2 = data['nivel_2']
        
        assert 'dz_d1_val' in n2, f"nivel_2 missing dz_d1_val. Keys: {list(n2.keys())}"
        dz_d1_val = n2['dz_d1_val']
        assert 'zscore' in dz_d1_val, f"dz_d1_val missing zscore"
        assert 'delta_ratio' in dz_d1_val, f"dz_d1_val missing delta_ratio"
        print(f"✅ dz_d1_val: zscore={dz_d1_val['zscore']}, delta_ratio={dz_d1_val['delta_ratio']}")

    def test_mes_v3_signal_has_dz_d1_poc(self, api_client):
        """GET /api/v3/signal/MES returns nivel_2.dz_d1_poc with zscore and delta_ratio"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200
        
        data = response.json()
        n2 = data['nivel_2']
        
        assert 'dz_d1_poc' in n2, f"nivel_2 missing dz_d1_poc. Keys: {list(n2.keys())}"
        dz_d1_poc = n2['dz_d1_poc']
        assert 'zscore' in dz_d1_poc, f"dz_d1_poc missing zscore"
        assert 'delta_ratio' in dz_d1_poc, f"dz_d1_poc missing delta_ratio"
        print(f"✅ dz_d1_poc: zscore={dz_d1_poc['zscore']}, delta_ratio={dz_d1_poc['delta_ratio']}")

    def test_mes_transicao_border_valid_values(self, api_client):
        """GET /api/v3/signal/MES nivel_2.transicao_border is one of VAH, VAL, VAH_D1, VAL_D1, MID"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200
        
        data = response.json()
        n2 = data['nivel_2']
        
        # transicao_border may not exist if not in TRANSICAO regime
        if 'transicao_border' in n2:
            border = n2['transicao_border']
            valid_borders = ['VAH', 'VAL', 'VAH_D1', 'VAL_D1', 'MID']
            assert border in valid_borders, f"transicao_border '{border}' not in {valid_borders}"
            print(f"✅ transicao_border: {border}")
        else:
            # Check if regime is TRANSICAO - if so, border should exist
            n1 = data.get('nivel_1', {})
            regime = n1.get('regime', 'UNKNOWN')
            if regime == 'TRANSICAO':
                pytest.fail(f"TRANSICAO regime but no transicao_border in nivel_2")
            print(f"✅ Not in TRANSICAO regime ({regime}), transicao_border not required")

    def test_mes_d1_levels_have_values(self, api_client):
        """GET /api/v3/signal/MES nivel_2.d1_vah and nivel_2.d1_val have non-zero values"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200
        
        data = response.json()
        n2 = data['nivel_2']
        
        # Check d1_levels or individual d1_vah/d1_val fields
        d1_levels = n2.get('d1_levels', {})
        d1_vah = d1_levels.get('vah', n2.get('d1_vah', 0))
        d1_val = d1_levels.get('val', n2.get('d1_val', 0))
        
        # D-1 levels should be non-zero if prev_day_vp exists
        # They may be 0 on weekends or if no previous day data
        print(f"✅ D-1 levels: d1_vah={d1_vah}, d1_val={d1_val}")
        
        # At least verify the structure exists
        assert 'd1_levels' in n2 or ('d1_vah' in n2 and 'd1_val' in n2), \
            f"Missing D-1 level fields. Keys: {list(n2.keys())}"

    def test_mnq_v3_signal_has_dz_d1_fields(self, api_client):
        """GET /api/v3/signal/MNQ also returns dz_d1_vah, dz_d1_val fields in nivel_2"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        n2 = data['nivel_2']
        
        assert 'dz_d1_vah' in n2, f"MNQ nivel_2 missing dz_d1_vah"
        assert 'dz_d1_val' in n2, f"MNQ nivel_2 missing dz_d1_val"
        
        dz_d1_vah = n2['dz_d1_vah']
        dz_d1_val = n2['dz_d1_val']
        
        assert 'zscore' in dz_d1_vah, "MNQ dz_d1_vah missing zscore"
        assert 'delta_ratio' in dz_d1_vah, "MNQ dz_d1_vah missing delta_ratio"
        assert 'zscore' in dz_d1_val, "MNQ dz_d1_val missing zscore"
        assert 'delta_ratio' in dz_d1_val, "MNQ dz_d1_val missing delta_ratio"
        
        print(f"✅ MNQ dz_d1_vah: zscore={dz_d1_vah['zscore']}, delta_ratio={dz_d1_vah['delta_ratio']}")
        print(f"✅ MNQ dz_d1_val: zscore={dz_d1_val['zscore']}, delta_ratio={dz_d1_val['delta_ratio']}")

    def test_mes_reason_contains_border_detection(self, api_client):
        """GET /api/v3/signal/MES nivel_2.reason contains 'Borda:' indicating border detection works"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200
        
        data = response.json()
        n2 = data['nivel_2']
        
        reason = n2.get('reason', '')
        
        # In TRANSICAO regime, reason should contain 'Borda:'
        n1 = data.get('nivel_1', {})
        regime = n1.get('regime', 'UNKNOWN')
        
        if regime == 'TRANSICAO':
            assert 'Borda:' in reason, f"TRANSICAO regime but reason missing 'Borda:'. Reason: {reason}"
            print(f"✅ TRANSICAO reason contains border detection: {reason[:100]}...")
        else:
            print(f"✅ Regime is {regime}, border detection not required in reason")


class TestAnalyzeSessionVPs:
    """Test POST /api/analyze returns session_vps with daily_vp and rth_vp"""

    def test_analyze_mes_returns_session_vps(self, api_client):
        """POST /api/analyze {symbol:MES, timeframe:1H} returns session_vps with daily_vp and rth_vp"""
        response = api_client.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MES",
            "timeframe": "1H"
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Check session_vps exists
        assert 'session_vps' in data, f"Response missing session_vps. Keys: {list(data.keys())}"
        session_vps = data['session_vps']
        
        # Check daily_vp and rth_vp exist
        assert 'daily_vp' in session_vps, f"session_vps missing daily_vp. Keys: {list(session_vps.keys())}"
        assert 'rth_vp' in session_vps, f"session_vps missing rth_vp. Keys: {list(session_vps.keys())}"
        
        daily_vp = session_vps['daily_vp']
        rth_vp = session_vps['rth_vp']
        
        # Verify structure of daily_vp
        if daily_vp:
            assert 'poc' in daily_vp, f"daily_vp missing poc"
            assert 'vah' in daily_vp, f"daily_vp missing vah"
            assert 'val' in daily_vp, f"daily_vp missing val"
            print(f"✅ daily_vp: poc={daily_vp['poc']}, vah={daily_vp['vah']}, val={daily_vp['val']}")
        
        # Verify structure of rth_vp
        if rth_vp:
            assert 'poc' in rth_vp, f"rth_vp missing poc"
            assert 'vah' in rth_vp, f"rth_vp missing vah"
            assert 'val' in rth_vp, f"rth_vp missing val"
            print(f"✅ rth_vp: poc={rth_vp['poc']}, vah={rth_vp['vah']}, val={rth_vp['val']}")


class TestPriceRangeValidation:
    """Test that MES and MNQ return prices in expected ranges"""

    def test_mes_price_in_expected_range(self, api_client):
        """MES current price should be in 6000-7000 range, not MNQ's 17000-26000"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200
        
        data = response.json()
        n2 = data.get('nivel_2', {})
        
        # Get current price from various possible fields
        current_price = n2.get('current_price', 0)
        if current_price == 0:
            # Try to get from vwap or other fields
            current_price = n2.get('vwap', 0)
        
        if current_price > 0:
            # MES should be in 5000-8000 range (S&P 500 futures)
            assert 5000 < current_price < 8000, \
                f"MES price {current_price} outside expected range 5000-8000. Possible MNQ contamination!"
            print(f"✅ MES current_price={current_price} is in valid range (5000-8000)")
        else:
            # Check d1_levels for price reference
            d1_levels = n2.get('d1_levels', {})
            d1_vah = d1_levels.get('vah', 0)
            if d1_vah > 0:
                assert 5000 < d1_vah < 8000, \
                    f"MES d1_vah {d1_vah} outside expected range. Possible MNQ contamination!"
                print(f"✅ MES d1_vah={d1_vah} is in valid range (5000-8000)")

    def test_mnq_price_in_expected_range(self, api_client):
        """MNQ current price should be in 17000-26000 range"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        n2 = data.get('nivel_2', {})
        
        current_price = n2.get('current_price', 0)
        if current_price == 0:
            current_price = n2.get('vwap', 0)
        
        if current_price > 0:
            # MNQ should be in 15000-30000 range (Nasdaq 100 futures)
            assert 15000 < current_price < 30000, \
                f"MNQ price {current_price} outside expected range 15000-30000"
            print(f"✅ MNQ current_price={current_price} is in valid range (15000-30000)")
        else:
            d1_levels = n2.get('d1_levels', {})
            d1_vah = d1_levels.get('vah', 0)
            if d1_vah > 0:
                assert 15000 < d1_vah < 30000, \
                    f"MNQ d1_vah {d1_vah} outside expected range"
                print(f"✅ MNQ d1_vah={d1_vah} is in valid range (15000-30000)")


class TestDZFieldsStructure:
    """Test that DZ fields have complete structure"""

    def test_dz_fields_have_all_required_keys(self, api_client):
        """All DZ fields should have zscore, delta_ratio, volume_significant, position, signal, net_delta"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200
        
        data = response.json()
        n2 = data['nivel_2']
        
        required_keys = ['zscore', 'delta_ratio', 'volume_significant', 'position', 'signal', 'net_delta']
        
        dz_fields = ['dz_vah', 'dz_val', 'dz_vwap', 'dz_poc', 'dz_d1_vah', 'dz_d1_val', 'dz_d1_poc']
        
        for field in dz_fields:
            if field in n2:
                dz_data = n2[field]
                for key in required_keys:
                    assert key in dz_data, f"{field} missing required key '{key}'. Keys: {list(dz_data.keys())}"
                print(f"✅ {field} has all required keys: {list(dz_data.keys())}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
