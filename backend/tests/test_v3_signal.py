"""
V3 Institutional Trading Architecture Phase 2 Tests
Tests for 3-level signal engine (Nível 1: Macro Regime Detection, Nível 2: Price Filters, Nível 3: Execution Signals)
with 5 market regimes (COMPLACENCIA, BULL, TRANSICAO, BEAR, CAPITULACAO)
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestV3SignalEndpoint:
    """Tests for GET /api/v3/signal/{symbol}"""
    
    def test_v3_signal_mnq_returns_valid_json(self):
        """V3 signal for MNQ returns valid JSON with all required keys"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        required_keys = ['v3_status', 'nivel_1', 'nivel_2', 'nivel_3', 'context', 'symbol', 'timestamp']
        for key in required_keys:
            assert key in data, f"Missing required key: {key}"
        
        assert data['symbol'] == 'MNQ'
        print(f"✅ V3 signal MNQ has all required keys: {required_keys}")
    
    def test_v3_signal_nivel_1_structure(self):
        """Nível 1 contains regime, macro_score, max_score, target_symbol, tactic, lot_pct, score_breakdown"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        n1 = data.get('nivel_1', {})
        
        # Check required fields
        assert 'regime' in n1, "nivel_1 missing 'regime'"
        assert 'macro_score' in n1, "nivel_1 missing 'macro_score'"
        assert 'max_score' in n1, "nivel_1 missing 'max_score'"
        assert 'target_symbol' in n1, "nivel_1 missing 'target_symbol'"
        assert 'tactic' in n1, "nivel_1 missing 'tactic'"
        assert 'lot_pct' in n1, "nivel_1 missing 'lot_pct'"
        assert 'score_breakdown' in n1, "nivel_1 missing 'score_breakdown'"
        
        # Validate regime is one of 5 valid values
        valid_regimes = ['COMPLACENCIA', 'BULL', 'TRANSICAO', 'BEAR', 'CAPITULACAO']
        assert n1['regime'] in valid_regimes, f"Invalid regime: {n1['regime']}"
        
        # Validate max_score is 13
        assert n1['max_score'] == 13, f"Expected max_score=13, got {n1['max_score']}"
        
        # Validate target_symbol is MNQ or MES
        assert n1['target_symbol'] in ['MNQ', 'MES'], f"Invalid target_symbol: {n1['target_symbol']}"
        
        print(f"✅ Nível 1: regime={n1['regime']}, score={n1['macro_score']}/{n1['max_score']}, target={n1['target_symbol']}, tactic={n1['tactic']}, lot={n1['lot_pct']}%")
    
    def test_v3_signal_nivel_2_structure(self):
        """Nível 2 contains passed (boolean), reason (string), price_vs_vah"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        n2 = data.get('nivel_2', {})
        
        # Check required fields
        assert 'passed' in n2, "nivel_2 missing 'passed'"
        assert 'reason' in n2, "nivel_2 missing 'reason'"
        assert 'price_vs_vah' in n2, "nivel_2 missing 'price_vs_vah'"
        
        # Validate types
        assert isinstance(n2['passed'], bool), f"passed should be bool, got {type(n2['passed'])}"
        assert isinstance(n2['reason'], str), f"reason should be str, got {type(n2['reason'])}"
        
        # Validate price_vs_vah is one of valid values
        valid_positions = ['ABOVE', 'BELOW', 'INSIDE_VA']
        assert n2['price_vs_vah'] in valid_positions, f"Invalid price_vs_vah: {n2['price_vs_vah']}"
        
        print(f"✅ Nível 2: passed={n2['passed']}, price_vs_vah={n2['price_vs_vah']}, reason={n2['reason'][:50]}...")
    
    def test_v3_signal_nivel_3_structure(self):
        """Nível 3 contains action, ofi_fast, ofi_slow, absorption_flag"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        n3 = data.get('nivel_3', {})
        
        # Check required fields
        assert 'action' in n3, "nivel_3 missing 'action'"
        assert 'ofi_fast' in n3, "nivel_3 missing 'ofi_fast'"
        assert 'ofi_slow' in n3, "nivel_3 missing 'ofi_slow'"
        assert 'absorption_flag' in n3, "nivel_3 missing 'absorption_flag'"
        
        # Validate action is one of valid values
        valid_actions = ['BUY', 'SELL', 'WAIT']
        assert n3['action'] in valid_actions, f"Invalid action: {n3['action']}"
        
        # Validate types
        assert isinstance(n3['ofi_fast'], (int, float)), f"ofi_fast should be numeric, got {type(n3['ofi_fast'])}"
        assert isinstance(n3['ofi_slow'], (int, float)), f"ofi_slow should be numeric, got {type(n3['ofi_slow'])}"
        assert isinstance(n3['absorption_flag'], bool), f"absorption_flag should be bool, got {type(n3['absorption_flag'])}"
        
        print(f"✅ Nível 3: action={n3['action']}, ofi_fast={n3['ofi_fast']:.4f}, ofi_slow={n3['ofi_slow']:.4f}, absorption={n3['absorption_flag']}")
    
    def test_v3_status_valid_values(self):
        """v3_status is one of ACTIVE_SIGNAL, FILTERS_PASSED, WAITING"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        valid_statuses = ['ACTIVE_SIGNAL', 'FILTERS_PASSED', 'WAITING']
        assert data['v3_status'] in valid_statuses, f"Invalid v3_status: {data['v3_status']}"
        
        print(f"✅ v3_status={data['v3_status']}")
    
    def test_v3_signal_mes_returns_valid_data(self):
        """V3 signal for MES returns valid data (different symbol test)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        assert data['symbol'] == 'MES'
        assert 'nivel_1' in data
        assert 'nivel_2' in data
        assert 'nivel_3' in data
        
        print(f"✅ V3 signal MES: regime={data['nivel_1']['regime']}, status={data['v3_status']}")
    
    def test_v3_signal_context_vwap_bands(self):
        """Context contains vwap_bands with upper_3 and lower_3 (±3σ)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        context = data.get('context', {})
        vwap_bands = context.get('vwap_bands', {})
        
        # Check ±3σ bands exist
        assert 'upper_3' in vwap_bands, "vwap_bands missing 'upper_3'"
        assert 'lower_3' in vwap_bands, "vwap_bands missing 'lower_3'"
        assert 'upper_1' in vwap_bands, "vwap_bands missing 'upper_1'"
        assert 'upper_2' in vwap_bands, "vwap_bands missing 'upper_2'"
        assert 'lower_1' in vwap_bands, "vwap_bands missing 'lower_1'"
        assert 'lower_2' in vwap_bands, "vwap_bands missing 'lower_2'"
        
        # Validate band ordering: lower_3 < lower_2 < lower_1 < upper_1 < upper_2 < upper_3
        assert vwap_bands['lower_3'] < vwap_bands['lower_2'], "lower_3 should be < lower_2"
        assert vwap_bands['lower_2'] < vwap_bands['lower_1'], "lower_2 should be < lower_1"
        assert vwap_bands['upper_1'] < vwap_bands['upper_2'], "upper_1 should be < upper_2"
        assert vwap_bands['upper_2'] < vwap_bands['upper_3'], "upper_2 should be < upper_3"
        
        print(f"✅ VWAP bands: lower_3={vwap_bands['lower_3']}, upper_3={vwap_bands['upper_3']}")


class TestV3RegimesEndpoint:
    """Tests for GET /api/v3/regimes"""
    
    def test_v3_regimes_returns_5_configs(self):
        """V3 regimes returns 5 regime configurations with score_components"""
        response = requests.get(f"{BASE_URL}/api/v3/regimes", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        regimes = data.get('regimes', {})
        
        # Check all 5 regimes exist
        expected_regimes = ['COMPLACENCIA', 'BULL', 'TRANSICAO', 'BEAR', 'CAPITULACAO']
        for regime in expected_regimes:
            assert regime in regimes, f"Missing regime: {regime}"
        
        assert len(regimes) == 5, f"Expected 5 regimes, got {len(regimes)}"
        
        # Check score_components
        assert 'score_components' in data, "Missing score_components"
        assert 'score_max' in data, "Missing score_max"
        assert data['score_max'] == 13, f"Expected score_max=13, got {data['score_max']}"
        
        print(f"✅ V3 regimes: {list(regimes.keys())}, score_max={data['score_max']}")
    
    def test_v3_regimes_config_structure(self):
        """Each regime config has symbol, tactic, lot_pct, direction"""
        response = requests.get(f"{BASE_URL}/api/v3/regimes", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        regimes = data.get('regimes', {})
        
        for regime_name, config in regimes.items():
            assert 'symbol' in config, f"{regime_name} missing 'symbol'"
            assert 'tactic' in config, f"{regime_name} missing 'tactic'"
            assert 'lot_pct' in config, f"{regime_name} missing 'lot_pct'"
            assert 'direction' in config, f"{regime_name} missing 'direction'"
            
            # Validate symbol is MNQ or MES
            assert config['symbol'] in ['MNQ', 'MES'], f"{regime_name} invalid symbol: {config['symbol']}"
            
            print(f"  {regime_name}: {config['symbol']}, {config['tactic']}, {config['lot_pct']}%, {config['direction']}")
        
        print(f"✅ All 5 regime configs have valid structure")


class TestV3WarmCache:
    """Tests for warm cache performance"""
    
    def test_warm_cache_second_call_fast(self):
        """Second call to /api/v3/signal/MNQ completes in < 2s"""
        # First call (may be cold)
        start1 = time.time()
        response1 = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        time1 = time.time() - start1
        assert response1.status_code == 200
        
        # Second call (should be warm)
        start2 = time.time()
        response2 = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        time2 = time.time() - start2
        assert response2.status_code == 200
        
        print(f"First call: {time1:.2f}s, Second call: {time2:.2f}s")
        
        # Second call should be under 2 seconds
        assert time2 < 2.0, f"Warm cache call took {time2:.2f}s, expected < 2s"
        
        print(f"✅ Warm cache working: second call {time2:.2f}s < 2s")


class TestAnalyzeVWAPBands:
    """Tests for ±3σ VWAP bands in /api/analyze"""
    
    def test_analyze_includes_vwap_3sigma_bands(self):
        """POST /api/analyze with MNQ returns vwap.upper_3 and vwap.lower_3"""
        response = requests.post(
            f"{BASE_URL}/api/analyze",
            json={"symbol": "MNQ", "timeframe": "1H"},
            timeout=30
        )
        assert response.status_code == 200
        
        data = response.json()
        vwap = data.get('vwap', {})
        
        # Check ±3σ bands exist
        assert 'upper_3' in vwap, "vwap missing 'upper_3'"
        assert 'lower_3' in vwap, "vwap missing 'lower_3'"
        
        # Validate they are numeric
        assert isinstance(vwap['upper_3'], (int, float)), f"upper_3 should be numeric"
        assert isinstance(vwap['lower_3'], (int, float)), f"lower_3 should be numeric"
        
        # Validate ordering
        assert vwap['lower_3'] < vwap['lower_2'] < vwap['lower_1'] < vwap['vwap'], "Lower bands should be ordered"
        assert vwap['vwap'] < vwap['upper_1'] < vwap['upper_2'] < vwap['upper_3'], "Upper bands should be ordered"
        
        print(f"✅ /api/analyze VWAP bands: lower_3={vwap['lower_3']}, upper_3={vwap['upper_3']}")


class TestV3InvalidSymbol:
    """Tests for invalid symbol handling"""
    
    def test_v3_signal_invalid_symbol_returns_404(self):
        """V3 signal for invalid symbol returns 404"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/INVALID", timeout=10)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        
        print(f"✅ Invalid symbol returns 404")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
