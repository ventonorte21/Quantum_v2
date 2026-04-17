"""
Export Endpoints Tests
======================
Tests for CSV and XLSX export functionality in Replay Engine and Trade Journal.
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")

# Known run IDs from context
TEST_RUN_ID = "ae1236e9-fcf"  # Has 14 trades


class TestReplayExport:
    """Tests for /api/replay/export/{run_id} endpoint"""

    @pytest.fixture
    def auth_headers(self):
        return {
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "Cookie": f"session_token={SESSION_TOKEN}"
        }

    def test_replay_export_csv(self, auth_headers):
        """Test CSV export for replay run"""
        response = requests.get(
            f"{BASE_URL}/api/replay/export/{TEST_RUN_ID}?format=csv",
            headers=auth_headers,
            timeout=30
        )
        
        # Status code assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Content-Type assertion
        content_type = response.headers.get("Content-Type", "")
        assert "text/csv" in content_type, f"Expected text/csv, got {content_type}"
        
        # Content-Disposition assertion
        content_disp = response.headers.get("Content-Disposition", "")
        assert "attachment" in content_disp, f"Expected attachment, got {content_disp}"
        assert f"replay_{TEST_RUN_ID}.csv" in content_disp, f"Expected filename with run_id, got {content_disp}"
        
        # Data assertion - CSV should have header and data rows
        content = response.text
        lines = content.strip().split('\n')
        assert len(lines) >= 1, "CSV should have at least header row"
        
        # Check header contains expected columns
        header = lines[0].lower()
        expected_cols = ["id", "symbol", "side", "entry_price", "exit_price", "net_pnl"]
        for col in expected_cols:
            assert col in header, f"Expected column '{col}' in header: {header}"
        
        print(f"✅ Replay CSV export: {len(lines)} lines, Content-Type: {content_type}")

    def test_replay_export_xlsx(self, auth_headers):
        """Test XLSX export for replay run"""
        response = requests.get(
            f"{BASE_URL}/api/replay/export/{TEST_RUN_ID}?format=xlsx",
            headers=auth_headers,
            timeout=30
        )
        
        # Status code assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Content-Type assertion
        content_type = response.headers.get("Content-Type", "")
        expected_xlsx_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert expected_xlsx_type in content_type, f"Expected {expected_xlsx_type}, got {content_type}"
        
        # Content-Disposition assertion
        content_disp = response.headers.get("Content-Disposition", "")
        assert "attachment" in content_disp, f"Expected attachment, got {content_disp}"
        assert f"replay_{TEST_RUN_ID}.xlsx" in content_disp, f"Expected filename with run_id, got {content_disp}"
        
        # Data assertion - XLSX should have content
        content_length = len(response.content)
        assert content_length > 100, f"XLSX file too small: {content_length} bytes"
        
        # Verify it's a valid XLSX (starts with PK - ZIP signature)
        assert response.content[:2] == b'PK', "XLSX should be a valid ZIP file (starts with PK)"
        
        print(f"✅ Replay XLSX export: {content_length} bytes, Content-Type: {content_type}")

    def test_replay_export_invalid_run_id(self, auth_headers):
        """Test export with invalid run ID returns 404"""
        response = requests.get(
            f"{BASE_URL}/api/replay/export/invalid-run-id-12345?format=csv",
            headers=auth_headers,
            timeout=30
        )
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ Replay export with invalid run_id returns 404")


class TestJournalExport:
    """Tests for /api/fills/export endpoint"""

    @pytest.fixture
    def auth_headers(self):
        return {
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "Cookie": f"session_token={SESSION_TOKEN}"
        }

    def test_journal_export_csv(self, auth_headers):
        """Test CSV export for trade journal"""
        response = requests.get(
            f"{BASE_URL}/api/fills/export?format=csv",
            headers=auth_headers,
            timeout=30
        )
        
        # Status code assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Content-Type assertion
        content_type = response.headers.get("Content-Type", "")
        assert "text/csv" in content_type, f"Expected text/csv, got {content_type}"
        
        # Content-Disposition assertion
        content_disp = response.headers.get("Content-Disposition", "")
        assert "attachment" in content_disp, f"Expected attachment, got {content_disp}"
        assert "trade_journal_" in content_disp, f"Expected trade_journal_ in filename, got {content_disp}"
        assert ".csv" in content_disp, f"Expected .csv extension, got {content_disp}"
        
        # Data assertion - CSV should have header and data rows
        content = response.text
        lines = content.strip().split('\n')
        assert len(lines) >= 1, "CSV should have at least header row"
        
        # Check header contains expected columns
        header = lines[0].lower()
        expected_cols = ["id", "symbol", "side", "state", "entry_price", "realized_pnl"]
        for col in expected_cols:
            assert col in header, f"Expected column '{col}' in header: {header}"
        
        print(f"✅ Journal CSV export: {len(lines)} lines, Content-Type: {content_type}")

    def test_journal_export_xlsx(self, auth_headers):
        """Test XLSX export for trade journal"""
        response = requests.get(
            f"{BASE_URL}/api/fills/export?format=xlsx",
            headers=auth_headers,
            timeout=30
        )
        
        # Status code assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Content-Type assertion
        content_type = response.headers.get("Content-Type", "")
        expected_xlsx_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert expected_xlsx_type in content_type, f"Expected {expected_xlsx_type}, got {content_type}"
        
        # Content-Disposition assertion
        content_disp = response.headers.get("Content-Disposition", "")
        assert "attachment" in content_disp, f"Expected attachment, got {content_disp}"
        assert "trade_journal_" in content_disp, f"Expected trade_journal_ in filename, got {content_disp}"
        assert ".xlsx" in content_disp, f"Expected .xlsx extension, got {content_disp}"
        
        # Data assertion - XLSX should have content
        content_length = len(response.content)
        assert content_length > 100, f"XLSX file too small: {content_length} bytes"
        
        # Verify it's a valid XLSX (starts with PK - ZIP signature)
        assert response.content[:2] == b'PK', "XLSX should be a valid ZIP file (starts with PK)"
        
        print(f"✅ Journal XLSX export: {content_length} bytes, Content-Type: {content_type}")

    def test_journal_export_csv_with_symbol_filter(self, auth_headers):
        """Test CSV export with symbol filter"""
        response = requests.get(
            f"{BASE_URL}/api/fills/export?format=csv&symbol=MNQ",
            headers=auth_headers,
            timeout=30
        )
        
        # Status code assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Content-Type assertion
        content_type = response.headers.get("Content-Type", "")
        assert "text/csv" in content_type, f"Expected text/csv, got {content_type}"
        
        # Data assertion - CSV should have header and data rows
        content = response.text
        lines = content.strip().split('\n')
        assert len(lines) >= 1, "CSV should have at least header row"
        
        # If there are data rows, verify they contain MNQ
        if len(lines) > 1:
            for line in lines[1:]:
                assert "MNQ" in line, f"Expected MNQ in filtered data: {line}"
        
        print(f"✅ Journal CSV export with symbol=MNQ filter: {len(lines)} lines")

    def test_journal_export_xlsx_with_status_filter(self, auth_headers):
        """Test XLSX export with status filter"""
        response = requests.get(
            f"{BASE_URL}/api/fills/export?format=xlsx&status=CLOSED",
            headers=auth_headers,
            timeout=30
        )
        
        # Status code assertion
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Content-Type assertion
        content_type = response.headers.get("Content-Type", "")
        expected_xlsx_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert expected_xlsx_type in content_type, f"Expected {expected_xlsx_type}, got {content_type}"
        
        # Data assertion - XLSX should have content
        content_length = len(response.content)
        assert content_length > 100, f"XLSX file too small: {content_length} bytes"
        
        print(f"✅ Journal XLSX export with status=CLOSED filter: {content_length} bytes")


class TestExportEndpointsAvailability:
    """Basic availability tests for export endpoints"""

    @pytest.fixture
    def auth_headers(self):
        return {
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "Cookie": f"session_token={SESSION_TOKEN}"
        }

    def test_replay_runs_list(self, auth_headers):
        """Verify replay runs are available for export"""
        response = requests.get(
            f"{BASE_URL}/api/replay/runs?limit=5",
            headers=auth_headers,
            timeout=30
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "runs" in data, "Expected 'runs' in response"
        
        runs = data["runs"]
        print(f"✅ Found {len(runs)} replay runs available for export")
        
        if runs:
            # Verify the test run exists
            run_ids = [r.get("run_id", "") for r in runs]
            print(f"   Run IDs: {run_ids[:5]}")

    def test_journal_positions_available(self, auth_headers):
        """Verify journal positions are available for export"""
        response = requests.get(
            f"{BASE_URL}/api/fills/journal?limit=5",
            headers=auth_headers,
            timeout=30
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "positions" in data, "Expected 'positions' in response"
        
        positions = data["positions"]
        print(f"✅ Found {len(positions)} positions available for export")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
