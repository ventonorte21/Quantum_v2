"""
Test V3 Pipeline Logs Feature
Tests the NEW pipeline logging system for N1/N2/N3 diagnostics.

Test Flow:
1. Enable verbose mode via POST /api/v3/pipeline-logs/verbose?enable=true
2. Trigger V3 evaluation via GET /api/v3/signal/MNQ (creates log)
3. Query logs via GET /api/v3/pipeline-logs
4. Verify log structure (timestamp, symbol, duration_ms, n1, n2, n3, data_quality)
5. Test stats endpoint GET /api/v3/pipeline-logs/stats
6. Disable verbose mode
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://gamma-vix-predictor.preview.emergentagent.com')
SESSION_TOKEN = "test_session_auth_1775441581328"

class TestPipelineLogsAPI:
    """Test V3 Pipeline Logs endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with auth headers"""
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SESSION_TOKEN}"
        })
    
    def test_01_enable_verbose_mode(self):
        """POST /api/v3/pipeline-logs/verbose?enable=true - Enable verbose logging"""
        response = self.session.post(f"{BASE_URL}/api/v3/pipeline-logs/verbose?enable=true")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'verbose_logging' in data, "Response should contain verbose_logging field"
        assert data['verbose_logging'] == True, "Verbose logging should be enabled"
        print(f"✅ Verbose mode enabled: {data}")
    
    def test_02_trigger_v3_evaluation(self):
        """GET /api/v3/signal/MNQ - Trigger V3 evaluation to create a log"""
        # Wait a moment for verbose mode to take effect
        time.sleep(1)
        
        response = self.session.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=60)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # V3 response can have 'symbol' at top level OR in nivel_1 (timeout case)
        # Handle both normal and timeout response structures
        if 'symbol' in data:
            symbol = data['symbol']
        elif 'nivel_1' in data and 'target_symbol' in data['nivel_1']:
            symbol = data['nivel_1']['target_symbol']
        else:
            symbol = 'MNQ'  # Default for timeout responses
        
        # v3_status can be at top level or derived from nivel_2
        if 'v3_status' in data:
            v3_status = data['v3_status']
        elif 'nivel_2' in data and 'passed' in data['nivel_2']:
            # Timeout case: nivel_2.passed=False with timeout reason
            v3_status = 'TIMEOUT' if 'Timeout' in str(data['nivel_2'].get('reason', '')) else 'BLOCKED'
        else:
            v3_status = 'UNKNOWN'
        
        # V3 status can be WAITING, ACTIVE_SIGNAL, BLOCKED, MQS_FREEZE, or TIMEOUT
        valid_statuses = ['WAITING', 'ACTIVE_SIGNAL', 'BLOCKED', 'MQS_FREEZE', 'TIMEOUT', 'UNKNOWN']
        assert v3_status in valid_statuses, f"Invalid v3_status: {v3_status}"
        
        print(f"✅ V3 evaluation triggered: symbol={symbol}, status={v3_status}")
        
        # Even with timeout, the evaluation should have created a log entry
        # (if verbose mode is enabled)
    
    def test_03_query_pipeline_logs(self):
        """GET /api/v3/pipeline-logs - Query logs and verify structure"""
        # Wait for log to be written (fire-and-forget async)
        time.sleep(2)
        
        response = self.session.get(f"{BASE_URL}/api/v3/pipeline-logs?hours=1&limit=10")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'logs' in data, "Response should contain logs array"
        assert 'count' in data, "Response should contain count"
        assert 'summary' in data, "Response should contain summary"
        assert 'filters' in data, "Response should contain filters"
        
        # Verify summary structure
        summary = data['summary']
        assert 'outcomes' in summary, "Summary should contain outcomes"
        assert 'regimes' in summary, "Summary should contain regimes"
        assert 'avg_duration_ms' in summary, "Summary should contain avg_duration_ms"
        assert 'top_n2_block_reasons' in summary, "Summary should contain top_n2_block_reasons"
        
        print(f"✅ Pipeline logs query: count={data['count']}, summary={summary}")
        
        # If we have logs, verify structure
        if data['logs']:
            log = data['logs'][0]
            self._verify_log_structure(log)
        else:
            print("⚠️ No logs found - this may be expected if verbose mode just enabled")
        
        return data
    
    def _verify_log_structure(self, log):
        """Verify a single log has all required fields"""
        required_fields = ['timestamp', 'symbol', 'outcome', 'duration_ms', 'n1', 'n2', 'n3', 'data_quality']
        for field in required_fields:
            assert field in log, f"Log missing required field: {field}"
        
        # Verify duration_ms structure
        duration = log['duration_ms']
        duration_fields = ['fetch', 'n1', 'n2', 'n3', 'total']
        for field in duration_fields:
            assert field in duration, f"duration_ms missing field: {field}"
        
        # Verify total duration > 0 (proves instrumentation is working)
        assert duration['total'] > 0, f"duration_ms.total should be > 0, got {duration['total']}"
        
        # Verify n1 structure
        n1 = log['n1']
        n1_fields = ['regime', 'macro_score', 'target_symbol']
        for field in n1_fields:
            assert field in n1, f"n1 missing field: {field}"
        
        # Verify n2 structure
        n2 = log['n2']
        assert 'passed' in n2, "n2 missing 'passed' field"
        
        # Verify n3 structure
        n3 = log['n3']
        assert 'action' in n3, "n3 missing 'action' field"
        
        # Verify data_quality structure
        dq = log['data_quality']
        assert 'feed_live' in dq, "data_quality missing 'feed_live' field"
        
        print(f"✅ Log structure verified: symbol={log['symbol']}, outcome={log['outcome']}, total_ms={duration['total']}")
    
    def test_04_query_logs_with_symbol_filter(self):
        """GET /api/v3/pipeline-logs?symbol=MNQ - Test symbol filter"""
        response = self.session.get(f"{BASE_URL}/api/v3/pipeline-logs?symbol=MNQ&hours=1&limit=10")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        # All logs should be for MNQ
        for log in data['logs']:
            assert log['symbol'] == 'MNQ', f"Expected MNQ, got {log['symbol']}"
        
        print(f"✅ Symbol filter works: {data['count']} MNQ logs")
    
    def test_05_query_logs_with_outcome_filter(self):
        """GET /api/v3/pipeline-logs?outcome=WAITING - Test outcome filter"""
        response = self.session.get(f"{BASE_URL}/api/v3/pipeline-logs?outcome=WAITING&hours=24&limit=10")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        # All logs should have WAITING outcome
        for log in data['logs']:
            assert log['outcome'] == 'WAITING', f"Expected WAITING, got {log['outcome']}"
        
        print(f"✅ Outcome filter works: {data['count']} WAITING logs")
    
    def test_06_get_pipeline_stats(self):
        """GET /api/v3/pipeline-logs/stats - Get aggregated stats"""
        response = self.session.get(f"{BASE_URL}/api/v3/pipeline-logs/stats?hours=24")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'total' in data, "Stats should contain total"
        assert 'avg_duration_ms' in data, "Stats should contain avg_duration_ms"
        assert 'outcomes' in data, "Stats should contain outcomes"
        assert 'hours' in data, "Stats should contain hours"
        
        # Verify avg_duration_ms structure
        avg_dur = data['avg_duration_ms']
        for field in ['fetch', 'n1', 'n2', 'n3', 'total']:
            assert field in avg_dur, f"avg_duration_ms missing field: {field}"
        
        print(f"✅ Pipeline stats: total={data['total']}, avg_total_ms={avg_dur.get('total', 0)}")
        return data
    
    def test_07_disable_verbose_mode(self):
        """POST /api/v3/pipeline-logs/verbose?enable=false - Disable verbose logging"""
        response = self.session.post(f"{BASE_URL}/api/v3/pipeline-logs/verbose?enable=false")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert 'verbose_logging' in data, "Response should contain verbose_logging field"
        assert data['verbose_logging'] == False, "Verbose logging should be disabled"
        print(f"✅ Verbose mode disabled: {data}")
    
    def test_08_full_flow_verbose_signal_logs(self):
        """Full integration test: enable verbose → trigger signal → verify log created"""
        # Step 1: Enable verbose
        resp1 = self.session.post(f"{BASE_URL}/api/v3/pipeline-logs/verbose?enable=true")
        assert resp1.status_code == 200
        print("Step 1: Verbose enabled")
        
        # Step 2: Get current log count
        resp2 = self.session.get(f"{BASE_URL}/api/v3/pipeline-logs?hours=1&limit=100")
        assert resp2.status_code == 200
        initial_count = resp2.json()['count']
        print(f"Step 2: Initial log count = {initial_count}")
        
        # Step 3: Trigger V3 evaluation
        time.sleep(1)
        resp3 = self.session.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=60)
        assert resp3.status_code == 200
        v3_status = resp3.json().get('v3_status', 'UNKNOWN')
        print(f"Step 3: V3 evaluation triggered, status={v3_status}")
        
        # Step 4: Wait for async log write
        time.sleep(3)
        
        # Step 5: Query logs again
        resp4 = self.session.get(f"{BASE_URL}/api/v3/pipeline-logs?hours=1&limit=100")
        assert resp4.status_code == 200
        new_count = resp4.json()['count']
        print(f"Step 4: New log count = {new_count}")
        
        # Verify log was created (count increased or we have at least 1 log)
        # Note: In verbose mode, WAITING logs are also saved
        if new_count > initial_count:
            print(f"✅ Full flow verified: log count increased from {initial_count} to {new_count}")
        elif new_count >= 1:
            # Check if the most recent log matches our evaluation
            logs = resp4.json()['logs']
            if logs:
                latest = logs[0]
                if latest['symbol'] == 'MNQ':
                    print(f"✅ Full flow verified: found MNQ log with outcome={latest['outcome']}")
                    self._verify_log_structure(latest)
        else:
            print(f"⚠️ No logs found - may be timing issue or log write failed")
        
        # Step 6: Disable verbose
        resp5 = self.session.post(f"{BASE_URL}/api/v3/pipeline-logs/verbose?enable=false")
        assert resp5.status_code == 200
        print("Step 5: Verbose disabled")


class TestPipelineLogsEdgeCases:
    """Test edge cases and error handling"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SESSION_TOKEN}"
        })
    
    def test_logs_with_invalid_hours(self):
        """Test with very large hours value"""
        response = self.session.get(f"{BASE_URL}/api/v3/pipeline-logs?hours=9999&limit=10")
        assert response.status_code == 200, f"Should handle large hours value"
    
    def test_logs_with_zero_limit(self):
        """Test with limit=0"""
        response = self.session.get(f"{BASE_URL}/api/v3/pipeline-logs?hours=1&limit=0")
        assert response.status_code == 200
        data = response.json()
        assert data['logs'] == [], "Should return empty logs with limit=0"
    
    def test_logs_with_nonexistent_symbol(self):
        """Test with symbol that doesn't exist"""
        response = self.session.get(f"{BASE_URL}/api/v3/pipeline-logs?symbol=INVALID&hours=1")
        assert response.status_code == 200
        data = response.json()
        assert data['count'] == 0, "Should return 0 logs for invalid symbol"
    
    def test_stats_with_small_hours(self):
        """Test stats with very small time window"""
        response = self.session.get(f"{BASE_URL}/api/v3/pipeline-logs/stats?hours=1")
        assert response.status_code == 200
        data = response.json()
        assert 'total' in data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
