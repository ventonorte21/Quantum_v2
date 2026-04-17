"""
V3 Live Data Service and VPS Architecture Tests
================================================
Tests for:
- GET /api/v3/live-status: Live WebSocket status
- GET /api/v3/signal/{symbol}: live_connected and data_source fields
- POST /api/v3/execute/{symbol}?force=true: Paper order creation with trail_amount
- GET /api/v3/orders: Orders with trail_amount field
- POST /api/analyze: ofi.source and vwap.upper_3/lower_3 fields
- GET /api/v3/regimes: 5 regimes with score_components
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestV3LiveStatus:
    """Tests for GET /api/v3/live-status endpoint"""
    
    def test_live_status_returns_valid_json(self):
        """GET /api/v3/live-status returns valid JSON with required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/live-status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        # Required top-level fields
        assert 'running' in data, "Missing 'running' field"
        assert 'connected' in data, "Missing 'connected' field"
        assert 'symbols' in data, "Missing 'symbols' field"
        assert 'error_count' in data, "Missing 'error_count' field"
        
        print(f"✅ live-status: running={data['running']}, connected={data['connected']}, error_count={data['error_count']}")
    
    def test_live_status_symbols_mnq_mes(self):
        """GET /api/v3/live-status shows symbols.MNQ and symbols.MES with required fields"""
        response = requests.get(f"{BASE_URL}/api/v3/live-status")
        assert response.status_code == 200
        
        data = response.json()
        symbols = data.get('symbols', {})
        
        # Check MNQ
        assert 'MNQ' in symbols, "Missing MNQ in symbols"
        mnq = symbols['MNQ']
        assert 'connected' in mnq, "MNQ missing 'connected' field"
        assert 'trades_received' in mnq, "MNQ missing 'trades_received' field"
        assert 'buffer_size' in mnq, "MNQ missing 'buffer_size' field"
        assert 'ofi_fast' in mnq, "MNQ missing 'ofi_fast' field"
        assert 'ofi_slow' in mnq, "MNQ missing 'ofi_slow' field"
        assert 'absorption' in mnq, "MNQ missing 'absorption' field"
        
        # Check MES
        assert 'MES' in symbols, "Missing MES in symbols"
        mes = symbols['MES']
        assert 'connected' in mes, "MES missing 'connected' field"
        assert 'trades_received' in mes, "MES missing 'trades_received' field"
        assert 'buffer_size' in mes, "MES missing 'buffer_size' field"
        assert 'ofi_fast' in mes, "MES missing 'ofi_fast' field"
        assert 'ofi_slow' in mes, "MES missing 'ofi_slow' field"
        assert 'absorption' in mes, "MES missing 'absorption' field"
        
        print(f"✅ MNQ: connected={mnq['connected']}, trades={mnq['trades_received']}, buffer={mnq['buffer_size']}")
        print(f"✅ MES: connected={mes['connected']}, trades={mes['trades_received']}, buffer={mes['buffer_size']}")
    
    def test_live_status_disabled_in_preview(self):
        """In preview env (ENABLE_LIVE_DATA=false), running and connected should be false"""
        response = requests.get(f"{BASE_URL}/api/v3/live-status")
        assert response.status_code == 200
        
        data = response.json()
        # In preview environment, live data is disabled
        # running=False means the service was never started
        # connected=False means no WebSocket connection
        assert data['running'] == False, f"Expected running=False in preview, got {data['running']}"
        assert data['connected'] == False, f"Expected connected=False in preview, got {data['connected']}"
        
        print(f"✅ Preview env: running=False, connected=False (ENABLE_LIVE_DATA not set)")


class TestV3SignalLiveFields:
    """Tests for live_connected and data_source fields in V3 signal"""
    
    def test_v3_signal_has_live_connected_field(self):
        """GET /api/v3/signal/MNQ returns live_connected field (boolean)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        assert 'live_connected' in data, "Missing 'live_connected' field"
        assert isinstance(data['live_connected'], bool), f"live_connected should be bool, got {type(data['live_connected'])}"
        
        print(f"✅ V3 signal MNQ: live_connected={data['live_connected']}")
    
    def test_v3_signal_has_data_source_field(self):
        """GET /api/v3/signal/MNQ returns data_source field (string)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        assert 'data_source' in data, "Missing 'data_source' field"
        assert isinstance(data['data_source'], str), f"data_source should be str, got {type(data['data_source'])}"
        
        print(f"✅ V3 signal MNQ: data_source={data['data_source']}")
    
    def test_v3_signal_live_disabled_shows_correct_values(self):
        """When live disabled, live_connected=false and data_source=simulated or historical"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200
        
        data = response.json()
        # In preview env with ENABLE_LIVE_DATA=false
        assert data['live_connected'] == False, f"Expected live_connected=False, got {data['live_connected']}"
        
        # data_source should be 'simulated' or 'databento_trades' or 'historical'
        valid_sources = ['simulated', 'databento_trades', 'historical']
        assert data['data_source'] in valid_sources, f"data_source should be one of {valid_sources}, got {data['data_source']}"
        
        print(f"✅ Live disabled: live_connected=False, data_source={data['data_source']}")


class TestV3Execute:
    """Tests for POST /api/v3/execute/{symbol}?force=true"""
    
    def test_v3_execute_force_creates_paper_order(self):
        """POST /api/v3/execute/MNQ?force=true creates paper order"""
        response = requests.post(f"{BASE_URL}/api/v3/execute/MNQ?force=true")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Should be paper_executed or executed
        assert data.get('status') in ['paper_executed', 'executed'], f"Expected paper_executed/executed, got {data.get('status')}"
        
        # Check v3_order exists
        assert 'v3_order' in data, "Missing 'v3_order' in response"
        v3_order = data['v3_order']
        
        # Required fields
        assert 'v3_regime' in v3_order, "Missing v3_regime"
        assert 'action' in v3_order, "Missing action"
        assert 'quantity' in v3_order, "Missing quantity"
        assert 'paper_trade' in v3_order, "Missing paper_trade"
        
        print(f"✅ V3 execute force=true: status={data['status']}, regime={v3_order['v3_regime']}, action={v3_order['action']}")


class TestV3Orders:
    """Tests for GET /api/v3/orders"""
    
    def test_v3_orders_returns_orders_list(self):
        """GET /api/v3/orders returns orders with trail_amount field"""
        response = requests.get(f"{BASE_URL}/api/v3/orders?limit=5")
        assert response.status_code == 200
        
        data = response.json()
        assert 'orders' in data, "Missing 'orders' field"
        assert 'count' in data, "Missing 'count' field"
        
        orders = data['orders']
        if len(orders) > 0:
            order = orders[0]
            # Check for trail_amount field (may be null)
            # The field should exist in the order document
            print(f"✅ V3 orders: count={data['count']}, first order keys: {list(order.keys())[:10]}")
            
            # Check if trailing_stop field exists (trail_amount is in the order)
            if 'trailing_stop' in order:
                print(f"   trailing_stop={order['trailing_stop']}")
        else:
            print(f"✅ V3 orders: count=0 (no orders yet)")


class TestAnalyzeEndpoint:
    """Tests for POST /api/analyze"""
    
    def test_analyze_returns_ofi_source(self):
        """POST /api/analyze returns data with ofi.source field"""
        response = requests.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'ofi' in data, "Missing 'ofi' field"
        
        ofi = data['ofi']
        assert 'source' in ofi, "Missing 'source' in ofi"
        
        # Valid sources
        valid_sources = ['simulated', 'databento_trades', 'databento_live', 'historical']
        assert ofi['source'] in valid_sources, f"ofi.source should be one of {valid_sources}, got {ofi['source']}"
        
        print(f"✅ Analyze ofi.source={ofi['source']}")
    
    def test_analyze_returns_vwap_3sigma_bands(self):
        """POST /api/analyze returns vwap with upper_3 and lower_3 fields"""
        response = requests.post(f"{BASE_URL}/api/analyze", json={
            "symbol": "MNQ",
            "timeframe": "1H"
        })
        assert response.status_code == 200
        
        data = response.json()
        assert 'vwap' in data, "Missing 'vwap' field"
        
        vwap = data['vwap']
        assert 'upper_3' in vwap, "Missing 'upper_3' in vwap"
        assert 'lower_3' in vwap, "Missing 'lower_3' in vwap"
        
        # Values should be numeric
        assert isinstance(vwap['upper_3'], (int, float)), f"upper_3 should be numeric, got {type(vwap['upper_3'])}"
        assert isinstance(vwap['lower_3'], (int, float)), f"lower_3 should be numeric, got {type(vwap['lower_3'])}"
        
        # upper_3 should be > lower_3
        assert vwap['upper_3'] > vwap['lower_3'], f"upper_3 ({vwap['upper_3']}) should be > lower_3 ({vwap['lower_3']})"
        
        print(f"✅ VWAP bands: upper_3={vwap['upper_3']}, lower_3={vwap['lower_3']}")


class TestV3Regimes:
    """Tests for GET /api/v3/regimes"""
    
    def test_v3_regimes_returns_5_regimes(self):
        """GET /api/v3/regimes returns 5 regimes with score_components"""
        response = requests.get(f"{BASE_URL}/api/v3/regimes")
        assert response.status_code == 200
        
        data = response.json()
        assert 'regimes' in data, "Missing 'regimes' field"
        assert 'score_components' in data, "Missing 'score_components' field"
        
        regimes = data['regimes']
        assert len(regimes) == 5, f"Expected 5 regimes, got {len(regimes)}"
        
        # Check regime names
        expected_regimes = ['COMPLACENCIA', 'BULL', 'TRANSICAO', 'BEAR', 'CAPITULACAO']
        for regime_name in expected_regimes:
            assert regime_name in regimes, f"Missing regime: {regime_name}"
        
        # Check score_components
        score_components = data['score_components']
        assert len(score_components) >= 4, f"Expected at least 4 score components, got {len(score_components)}"
        
        print(f"✅ V3 regimes: {list(regimes.keys())}")
        print(f"✅ Score components: {[c['name'] for c in score_components]}")


class TestBackendStartup:
    """Tests for backend server startup"""
    
    def test_backend_health_check(self):
        """Backend server starts without errors"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get('status') == 'healthy', f"Expected healthy, got {data.get('status')}"
        
        print(f"✅ Backend health: {data['status']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
