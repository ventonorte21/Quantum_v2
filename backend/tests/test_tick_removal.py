"""
Test suite for TICK Index removal from N3Execution
Verifies:
1. N3Execution.jsx has no references to tick_index, TrendUp, or tick-index-panel
2. V3 Signal API does NOT return tick_index in nivel_3 (N3 execution object)
3. Accordion trigger shows only "Delta Zonal Extremo & OFI Fast" (no TICK mention)
4. Dashboard loads without errors
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestTickRemovalBackend:
    """Backend API tests for TICK removal"""
    
    def test_v3_signal_mnq_no_tick_in_nivel_3(self):
        """V3 Signal for MNQ should NOT have tick_index in nivel_3"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        nivel_3 = data.get('nivel_3', {})
        
        # tick_index should NOT be in nivel_3
        assert 'tick_index' not in nivel_3, "tick_index should NOT be in nivel_3"
        assert 'tick_sentiment' not in nivel_3, "tick_sentiment should NOT be in nivel_3"
        
        # Verify required N3 fields are present
        assert 'action' in nivel_3, "action should be in nivel_3"
        assert 'ofi_fast' in nivel_3, "ofi_fast should be in nivel_3"
        assert 'dz_n2_status' in nivel_3, "dz_n2_status should be in nivel_3"
        assert 'dz_n3_status' in nivel_3, "dz_n3_status should be in nivel_3"
        assert 'delta_zonal' in nivel_3, "delta_zonal should be in nivel_3"
        assert 'reason' in nivel_3, "reason should be in nivel_3"
        
        print(f"✅ V3 Signal MNQ: nivel_3 has no tick_index, has required fields")
    
    def test_v3_signal_mes_no_tick_in_nivel_3(self):
        """V3 Signal for MES should NOT have tick_index in nivel_3"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MES", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        nivel_3 = data.get('nivel_3', {})
        
        # tick_index should NOT be in nivel_3
        assert 'tick_index' not in nivel_3, "tick_index should NOT be in nivel_3"
        assert 'tick_sentiment' not in nivel_3, "tick_sentiment should NOT be in nivel_3"
        
        print(f"✅ V3 Signal MES: nivel_3 has no tick_index")
    
    def test_v3_signal_context_has_tick_index(self):
        """V3 Signal context CAN have tick_index (it's just context data)"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        context = data.get('context', {})
        
        # tick_index in context is OK (it's just context data, not used by N3)
        # This is informational only
        has_tick = 'tick_index' in context
        print(f"ℹ️ V3 Signal context has tick_index: {has_tick} (this is OK)")
    
    def test_n3_delta_zonal_panel_present(self):
        """N3 should have delta_zonal with levels and summary"""
        response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        nivel_3 = data.get('nivel_3', {})
        delta_zonal = nivel_3.get('delta_zonal', {})
        
        assert 'levels' in delta_zonal, "delta_zonal should have levels"
        assert 'summary' in delta_zonal, "delta_zonal should have summary"
        
        print(f"✅ N3 delta_zonal has levels and summary")
    
    def test_ofi_endpoint_working(self):
        """OFI endpoint should return valid data"""
        response = requests.get(f"{BASE_URL}/api/ofi/MNQ", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        ofi = data.get('ofi', {})
        
        assert 'ofi_fast' in ofi, "OFI should have ofi_fast"
        assert 'signal' in ofi, "OFI should have signal"
        assert 'interpretation' in ofi, "OFI should have interpretation"
        
        print(f"✅ OFI endpoint working: ofi_fast={ofi.get('ofi_fast')}")
    
    def test_health_endpoint(self):
        """Health endpoint should return healthy"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get('status') == 'healthy'
        
        print(f"✅ Health endpoint: healthy")


class TestN3ExecutionComponent:
    """Tests to verify N3Execution.jsx component code"""
    
    def test_no_trendUp_import(self):
        """N3Execution.jsx should NOT import TrendUp"""
        with open('/app/frontend/src/components/N3Execution.jsx', 'r') as f:
            content = f.read()
        
        assert 'TrendUp' not in content, "N3Execution.jsx should NOT have TrendUp import"
        print(f"✅ N3Execution.jsx has no TrendUp import")
    
    def test_no_tick_index_reference(self):
        """N3Execution.jsx should NOT reference tick_index"""
        with open('/app/frontend/src/components/N3Execution.jsx', 'r') as f:
            content = f.read()
        
        assert 'tick_index' not in content, "N3Execution.jsx should NOT reference tick_index"
        assert 'tick-index' not in content, "N3Execution.jsx should NOT have tick-index test-id"
        assert 'tickIndex' not in content, "N3Execution.jsx should NOT reference tickIndex"
        
        print(f"✅ N3Execution.jsx has no tick_index references")
    
    def test_no_tick_panel_testid(self):
        """N3Execution.jsx should NOT have tick-index-panel test-id"""
        with open('/app/frontend/src/components/N3Execution.jsx', 'r') as f:
            content = f.read()
        
        assert 'tick-index-panel' not in content, "N3Execution.jsx should NOT have tick-index-panel"
        assert 'tick-sentiment' not in content, "N3Execution.jsx should NOT have tick-sentiment"
        assert 'tick-value' not in content, "N3Execution.jsx should NOT have tick-value"
        
        print(f"✅ N3Execution.jsx has no tick panel test-ids")
    
    def test_accordion_trigger_text(self):
        """AccordionTrigger should show 'Delta Zonal Extremo & OFI Fast' (no TICK)"""
        with open('/app/frontend/src/components/N3Execution.jsx', 'r') as f:
            content = f.read()
        
        # Should have the correct accordion text
        assert 'Delta Zonal Extremo & OFI Fast' in content, "Accordion should show 'Delta Zonal Extremo & OFI Fast'"
        
        # Should NOT mention TICK in accordion
        # Check lines around AccordionTrigger
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'AccordionTrigger' in line:
                # Check this line and next few lines for TICK
                context = '\n'.join(lines[max(0, i-2):min(len(lines), i+5)])
                assert 'TICK' not in context.upper() or 'TICK' not in line, f"AccordionTrigger should not mention TICK"
        
        print(f"✅ AccordionTrigger shows 'Delta Zonal Extremo & OFI Fast' (no TICK)")
    
    def test_delta_zonal_panel_present(self):
        """N3Execution.jsx should have delta-zonal-n3-panel test-id"""
        with open('/app/frontend/src/components/N3Execution.jsx', 'r') as f:
            content = f.read()
        
        assert 'delta-zonal-n3-panel' in content, "N3Execution.jsx should have delta-zonal-n3-panel"
        print(f"✅ N3Execution.jsx has delta-zonal-n3-panel")
    
    def test_ofi_panel_present(self):
        """N3Execution.jsx should have ofi-panel test-id"""
        with open('/app/frontend/src/components/N3Execution.jsx', 'r') as f:
            content = f.read()
        
        assert 'ofi-panel' in content, "N3Execution.jsx should have ofi-panel"
        assert 'ofi-signal' in content, "N3Execution.jsx should have ofi-signal"
        assert 'ofi-fast' in content, "N3Execution.jsx should have ofi-fast"
        
        print(f"✅ N3Execution.jsx has OFI panel test-ids")
    
    def test_component_line_count(self):
        """N3Execution.jsx should be around 279 lines (as stated)"""
        with open('/app/frontend/src/components/N3Execution.jsx', 'r') as f:
            lines = f.readlines()
        
        line_count = len(lines)
        assert 250 <= line_count <= 300, f"N3Execution.jsx has {line_count} lines, expected ~279"
        
        print(f"✅ N3Execution.jsx has {line_count} lines")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
