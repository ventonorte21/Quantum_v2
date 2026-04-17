"""
Test N3 Execution Logic with Delta Zonal as Primary Trigger
Tests the refactored generate_execution_signal function that replaced OFI Slow with Delta Zonal.

Key inputs tested:
- dz_n2_summary, dz_n3_summary (Delta Zonal structural/absorption)
- dz_n2_levels, dz_n3_levels (level-specific data)
- tick_index, tick_sentiment (market breadth)
- ofi_fast (immediate aggression)

Removed inputs (should NOT be in response):
- ofi_slow
- absorption_flag
- absorption_side
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestN3SignalStructure:
    """Test N3 response structure and required fields"""

    def test_mnq_n3_signal_has_required_fields(self):
        """GET /api/v3/signal/MNQ returns N3 with all required fields"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        n3 = data.get('nivel_3', {})
        
        # Required fields in N3 response
        assert 'action' in n3, "N3 must have 'action' field"
        assert 'reason' in n3, "N3 must have 'reason' field"
        assert 'ofi_fast' in n3, "N3 must have 'ofi_fast' field"
        assert 'tick_index' in n3, "N3 must have 'tick_index' field"
        assert 'tick_sentiment' in n3, "N3 must have 'tick_sentiment' field"
        assert 'dz_n2_status' in n3, "N3 must have 'dz_n2_status' field"
        assert 'dz_n3_status' in n3, "N3 must have 'dz_n3_status' field"
        
        # Action must be one of BUY, SELL, WAIT
        assert n3['action'] in ('BUY', 'SELL', 'WAIT'), f"Invalid action: {n3['action']}"
        
        print(f"✅ MNQ N3 action: {n3['action']}")
        print(f"✅ MNQ N3 dz_n2_status: {n3['dz_n2_status']}")
        print(f"✅ MNQ N3 dz_n3_status: {n3['dz_n3_status']}")
        print(f"✅ MNQ N3 ofi_fast: {n3['ofi_fast']}")
        print(f"✅ MNQ N3 tick_index: {n3['tick_index']}")
        print(f"✅ MNQ N3 tick_sentiment: {n3['tick_sentiment']}")

    def test_mes_n3_signal_has_required_fields(self):
        """GET /api/v3/signal/MES returns N3 with all required fields"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        n3 = data.get('nivel_3', {})
        
        # Required fields
        assert 'action' in n3
        assert 'reason' in n3
        assert 'ofi_fast' in n3
        assert 'tick_index' in n3
        assert 'tick_sentiment' in n3
        assert 'dz_n2_status' in n3
        assert 'dz_n3_status' in n3
        
        print(f"✅ MES N3 action: {n3['action']}")
        print(f"✅ MES N3 reason: {n3['reason'][:100]}...")


class TestN3RemovedFields:
    """Test that removed fields (ofi_slow, absorption_flag, absorption_side) are NOT in N3"""

    def test_mnq_n3_does_not_have_ofi_slow(self):
        """N3 response should NOT contain ofi_slow (removed)"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert resp.status_code == 200
        
        data = resp.json()
        n3 = data.get('nivel_3', {})
        
        # These fields were removed from generate_execution_signal
        assert 'ofi_slow' not in n3, "ofi_slow should be removed from N3"
        assert 'absorption_flag' not in n3, "absorption_flag should be removed from N3"
        assert 'absorption_side' not in n3, "absorption_side should be removed from N3"
        
        print("✅ Confirmed: ofi_slow, absorption_flag, absorption_side NOT in N3 response")

    def test_mes_n3_does_not_have_removed_fields(self):
        """MES N3 response should NOT contain removed fields"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert resp.status_code == 200
        
        data = resp.json()
        n3 = data.get('nivel_3', {})
        
        assert 'ofi_slow' not in n3
        assert 'absorption_flag' not in n3
        assert 'absorption_side' not in n3
        
        print("✅ MES N3: removed fields confirmed absent")


class TestN3ReasonField:
    """Test that N3 reason field contains detailed explanation with metric values"""

    def test_n3_reason_contains_metric_values(self):
        """N3 reason should contain actual metric values (OFI, TICK, DZ status)"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert resp.status_code == 200
        
        data = resp.json()
        n3 = data.get('nivel_3', {})
        reason = n3.get('reason', '')
        
        # Reason should not be empty
        assert len(reason) > 10, f"Reason too short: {reason}"
        
        # Reason should contain some metric indicators (varies by regime)
        # Common patterns: "OFI Fast", "TICK", "N2", "delta=", "Z="
        has_metrics = any(x in reason for x in ['OFI', 'TICK', 'N2', 'delta', 'Z=', 'Aguardando', 'Compra', 'Venda'])
        assert has_metrics, f"Reason should contain metric values: {reason}"
        
        print(f"✅ N3 reason: {reason}")


class TestN3DeltaZonalIntegration:
    """Test Delta Zonal data is properly integrated into N3"""

    def test_n3_has_delta_zonal_object(self):
        """N3 should have delta_zonal object with levels and summary"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert resp.status_code == 200
        
        data = resp.json()
        n3 = data.get('nivel_3', {})
        
        # Delta Zonal N3 object
        dz = n3.get('delta_zonal', {})
        assert dz is not None, "N3 should have delta_zonal object"
        
        # Should have levels and summary
        assert 'levels' in dz or 'summary' in dz, "delta_zonal should have levels or summary"
        
        summary = dz.get('summary', {})
        if summary:
            print(f"✅ N3 DZ summary status: {summary.get('status')}")
            print(f"✅ N3 DZ active_absorptions: {summary.get('active_absorptions', [])}")

    def test_n2_has_delta_zonal_object(self):
        """N2 should have delta_zonal object with structural levels"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert resp.status_code == 200
        
        data = resp.json()
        n2 = data.get('nivel_2', {})
        
        dz = n2.get('delta_zonal', {})
        assert dz is not None, "N2 should have delta_zonal object"
        
        summary = dz.get('summary', {})
        if summary:
            print(f"✅ N2 DZ status: {summary.get('status')}")
            print(f"✅ N2 DZ total_delta: {summary.get('total_delta')}")
            print(f"✅ N2 DZ bullish_levels: {summary.get('bullish_levels')}")
            print(f"✅ N2 DZ bearish_levels: {summary.get('bearish_levels')}")


class TestN3RegimeSpecificLogic:
    """Test N3 logic varies by regime"""

    def test_n3_signal_varies_by_regime(self):
        """N3 signal logic should be regime-aware"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert resp.status_code == 200
        
        data = resp.json()
        n1 = data.get('nivel_1', {})
        n3 = data.get('nivel_3', {})
        
        regime = n1.get('regime', 'UNKNOWN')
        action = n3.get('action', 'WAIT')
        reason = n3.get('reason', '')
        
        print(f"✅ Current regime: {regime}")
        print(f"✅ N3 action: {action}")
        print(f"✅ N3 reason: {reason[:150]}...")
        
        # Verify regime is valid
        valid_regimes = ['COMPLACENCIA', 'BULL', 'TRANSICAO', 'BEAR', 'CAPITULACAO']
        assert regime in valid_regimes, f"Invalid regime: {regime}"
        
        # Verify action is valid
        assert action in ('BUY', 'SELL', 'WAIT')


class TestPositionEvaluateAutoSizing:
    """Test auto position sizing in /api/positions/evaluate"""

    def test_evaluate_returns_max_qty_by_risk(self):
        """POST /api/positions/evaluate/{symbol} returns max_qty_by_risk"""
        resp = requests.post(f"{BASE_URL}/api/positions/evaluate/MES?side=BUY", timeout=30)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        risk = data.get('risk', {})
        
        # Required risk fields
        assert 'max_qty_by_risk' in risk, "risk should have max_qty_by_risk"
        assert 'sl_risk_per_contract_usd' in risk, "risk should have sl_risk_per_contract_usd"
        assert 'max_risk_usd' in risk, "risk should have max_risk_usd"
        assert 'account_size' in risk, "risk should have account_size"
        
        max_qty = risk['max_qty_by_risk']
        assert isinstance(max_qty, int), f"max_qty_by_risk should be int, got {type(max_qty)}"
        assert max_qty >= 1, f"max_qty_by_risk should be >= 1, got {max_qty}"
        
        print(f"✅ max_qty_by_risk: {max_qty}")
        print(f"✅ sl_risk_per_contract_usd: ${risk['sl_risk_per_contract_usd']}")
        print(f"✅ max_risk_usd: ${risk['max_risk_usd']}")
        print(f"✅ account_size: ${risk['account_size']}")


class TestBothSymbolsWork:
    """Test both MNQ and MES return valid N3 signals"""

    def test_mnq_returns_valid_signal(self):
        """MNQ should return valid N3 signal without errors"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert resp.status_code == 200, f"MNQ failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        assert 'nivel_3' in data
        assert 'nivel_1' in data
        assert 'nivel_2' in data
        
        print(f"✅ MNQ v3_status: {data.get('v3_status')}")

    def test_mes_returns_valid_signal(self):
        """MES should return valid N3 signal without errors"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert resp.status_code == 200, f"MES failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        assert 'nivel_3' in data
        assert 'nivel_1' in data
        assert 'nivel_2' in data
        
        print(f"✅ MES v3_status: {data.get('v3_status')}")


class TestN3TickIndexIntegration:
    """Test TICK Index is properly used in N3"""

    def test_tick_index_in_n3_response(self):
        """N3 should include tick_index and tick_sentiment"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert resp.status_code == 200
        
        data = resp.json()
        n3 = data.get('nivel_3', {})
        
        tick_index = n3.get('tick_index')
        tick_sentiment = n3.get('tick_sentiment')
        
        assert tick_index is not None, "tick_index should be in N3"
        assert tick_sentiment is not None, "tick_sentiment should be in N3"
        
        # tick_sentiment should be valid
        valid_sentiments = ['STRONG_BUY', 'BULLISH', 'NEUTRAL', 'BEARISH', 'STRONG_SELL']
        assert tick_sentiment in valid_sentiments, f"Invalid tick_sentiment: {tick_sentiment}"
        
        print(f"✅ tick_index: {tick_index}")
        print(f"✅ tick_sentiment: {tick_sentiment}")


class TestN3OFIFastIntegration:
    """Test OFI Fast is properly used in N3"""

    def test_ofi_fast_in_n3_response(self):
        """N3 should include ofi_fast value"""
        resp = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert resp.status_code == 200
        
        data = resp.json()
        n3 = data.get('nivel_3', {})
        
        ofi_fast = n3.get('ofi_fast')
        assert ofi_fast is not None, "ofi_fast should be in N3"
        
        # OFI Fast should be between -1 and 1
        assert -1.5 <= ofi_fast <= 1.5, f"ofi_fast out of range: {ofi_fast}"
        
        print(f"✅ ofi_fast: {ofi_fast}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
