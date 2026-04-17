"""
Auto-Tune Optimizer API Tests
=============================
Tests for grid search optimization with background execution.
Features: POST /optimize, GET /optimize/status, GET /optimize/result, 
POST /optimize/cancel, GET /optimize/history
"""

import pytest
import requests
import time
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_auth_1775441581328")

@pytest.fixture
def api_client():
    """Shared requests session with auth"""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Cookie": f"session_token={SESSION_TOKEN}"
    })
    return session


class TestOptimizerStartAndStatus:
    """Test starting optimization and checking status"""
    
    def test_start_optimization_returns_running_status(self, api_client):
        """POST /api/replay/optimize with grid_config should return optimization_id and status=running"""
        # Small grid for fast test: 2x2x2x2 = 16 combos
        payload = {
            "grid_config": {
                "zscore_min": {"min": 0.5, "max": 1.0, "steps": 2},
                "delta_ratio_min": {"min": 0.10, "max": 0.20, "steps": 2},
                "ofi_threshold": {"min": 0.1, "max": 0.2, "steps": 2},
                "sl_atr_mult": {"min": 0.5, "max": 1.0, "steps": 2},
                "regime": "TRANSICAO"
            },
            "base_config": {},
            "objective": "sharpe"
        }
        
        response = api_client.post(f"{BASE_URL}/api/replay/optimize", json=payload)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "optimization_id" in data, "Response should contain optimization_id"
        assert data["status"] == "running", f"Expected status=running, got {data['status']}"
        assert data["total_combinations"] == 16, f"Expected 16 combos (2x2x2x2), got {data['total_combinations']}"
        
        print(f"✅ Optimization started: {data['optimization_id']} with {data['total_combinations']} combinations")
        
        # Wait for completion
        time.sleep(3)
    
    def test_get_optimization_status_returns_progress(self, api_client):
        """GET /api/replay/optimize/status should return progress (current/total/pct/best_objective)"""
        response = api_client.get(f"{BASE_URL}/api/replay/optimize/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Either idle or has optimization data
        if data.get("status") == "idle":
            print("✅ Status endpoint returns idle when no optimization running")
        else:
            assert "optimization_id" in data, "Should have optimization_id"
            assert "status" in data, "Should have status"
            assert "progress" in data, "Should have progress"
            
            progress = data.get("progress", {})
            assert "current" in progress, "Progress should have current"
            assert "total" in progress, "Progress should have total"
            assert "pct" in progress, "Progress should have pct"
            assert "best_objective" in progress, "Progress should have best_objective"
            
            print(f"✅ Status: {data['status']}, Progress: {progress['current']}/{progress['total']} ({progress['pct']}%)")


class TestOptimizerResult:
    """Test optimization result retrieval"""
    
    def test_run_small_optimization_and_get_result(self, api_client):
        """Run small optimization and verify result structure"""
        # Start a small optimization: 3x3x2x2 = 36 combos
        payload = {
            "grid_config": {
                "zscore_min": {"min": 0.5, "max": 1.5, "steps": 3},
                "delta_ratio_min": {"min": 0.10, "max": 0.30, "steps": 3},
                "ofi_threshold": {"min": 0.1, "max": 0.3, "steps": 2},
                "sl_atr_mult": {"min": 0.5, "max": 1.0, "steps": 2},
                "regime": "TRANSICAO"
            },
            "base_config": {},
            "objective": "sharpe"
        }
        
        start_resp = api_client.post(f"{BASE_URL}/api/replay/optimize", json=payload)
        if start_resp.status_code == 409:
            # Optimization already running, wait and retry
            print("Optimization already running, waiting...")
            time.sleep(10)
            start_resp = api_client.post(f"{BASE_URL}/api/replay/optimize", json=payload)
        
        assert start_resp.status_code == 200, f"Failed to start: {start_resp.text}"
        opt_id = start_resp.json()["optimization_id"]
        print(f"Started optimization {opt_id}")
        
        # Poll until completed (max 60 seconds)
        max_wait = 60
        waited = 0
        while waited < max_wait:
            status_resp = api_client.get(f"{BASE_URL}/api/replay/optimize/status")
            status_data = status_resp.json()
            
            if status_data.get("status") == "completed":
                print(f"✅ Optimization completed in {waited}s")
                break
            elif status_data.get("status") == "cancelled":
                pytest.fail("Optimization was cancelled")
            
            progress = status_data.get("progress", {})
            print(f"  Progress: {progress.get('current', 0)}/{progress.get('total', 0)} ({progress.get('pct', 0)}%)")
            
            time.sleep(2)
            waited += 2
        else:
            pytest.fail(f"Optimization did not complete within {max_wait}s")
        
        # Get result
        result_resp = api_client.get(f"{BASE_URL}/api/replay/optimize/result")
        assert result_resp.status_code == 200, f"Failed to get result: {result_resp.text}"
        
        result = result_resp.json()
        
        # Verify result structure
        assert "optimization_id" in result, "Result should have optimization_id"
        assert result["status"] == "completed", "Result status should be completed"
        assert "best" in result, "Result should have best"
        assert "results" in result, "Result should have results"
        assert "heatmap" in result, "Result should have heatmap"
        
        # Verify best result structure
        best = result["best"]
        assert "combo" in best, "Best should have combo"
        assert "metrics" in best, "Best should have metrics"
        assert "objective_value" in best, "Best should have objective_value"
        
        combo = best["combo"]
        assert "zscore" in combo, "Combo should have zscore"
        assert "delta_ratio" in combo, "Combo should have delta_ratio"
        assert "ofi" in combo, "Combo should have ofi"
        assert "sl_atr" in combo, "Combo should have sl_atr"
        
        print(f"✅ Best result: Z={combo['zscore']}, DR={combo['delta_ratio']}, OFI={combo['ofi']}, SL={combo['sl_atr']}")
        print(f"   Objective value: {best['objective_value']}")
        
        # Verify results are ranked
        results = result["results"]
        assert len(results) > 0, "Should have results"
        for i, r in enumerate(results):
            assert r["rank"] == i + 1, f"Result {i} should have rank {i+1}"
        
        print(f"✅ Results ranked correctly ({len(results)} results)")


class TestOptimizerHeatmap:
    """Test heatmap data structure"""
    
    def test_heatmap_has_all_six_pairs(self, api_client):
        """Result should include 6 heatmap pairs"""
        # First check if there's a completed result
        result_resp = api_client.get(f"{BASE_URL}/api/replay/optimize/result")
        
        if result_resp.status_code == 404:
            pytest.skip("No optimization result available - run test_run_small_optimization_and_get_result first")
        
        assert result_resp.status_code == 200, f"Unexpected status: {result_resp.status_code}"
        result = result_resp.json()
        
        heatmap = result.get("heatmap", {})
        expected_pairs = [
            "zscore_x_delta_ratio",
            "zscore_x_ofi",
            "zscore_x_sl_atr",
            "delta_ratio_x_ofi",
            "delta_ratio_x_sl_atr",
            "ofi_x_sl_atr"
        ]
        
        for pair in expected_pairs:
            assert pair in heatmap, f"Heatmap should have {pair}"
            pair_data = heatmap[pair]
            assert "x_param" in pair_data, f"{pair} should have x_param"
            assert "y_param" in pair_data, f"{pair} should have y_param"
            assert "x_values" in pair_data, f"{pair} should have x_values"
            assert "y_values" in pair_data, f"{pair} should have y_values"
            assert "cells" in pair_data, f"{pair} should have cells"
            
            # Verify cells structure
            cells = pair_data["cells"]
            if len(cells) > 0:
                cell = cells[0]
                assert "x" in cell, "Cell should have x"
                assert "y" in cell, "Cell should have y"
                assert "value" in cell, "Cell should have value"
        
        print(f"✅ All 6 heatmap pairs present: {list(heatmap.keys())}")


class TestOptimizerValidation:
    """Test grid validation and error handling"""
    
    def test_grid_over_1000_combos_rejected(self, api_client):
        """Grid with steps > 1000 combinations should be rejected (400 error)"""
        # 10x10x10x10 = 10000 combos - should be rejected
        payload = {
            "grid_config": {
                "zscore_min": {"min": 0.5, "max": 1.5, "steps": 10},
                "delta_ratio_min": {"min": 0.10, "max": 0.30, "steps": 10},
                "ofi_threshold": {"min": 0.1, "max": 0.4, "steps": 10},
                "sl_atr_mult": {"min": 0.5, "max": 1.5, "steps": 10},
                "regime": "TRANSICAO"
            },
            "base_config": {},
            "objective": "sharpe"
        }
        
        response = api_client.post(f"{BASE_URL}/api/replay/optimize", json=payload)
        assert response.status_code == 400, f"Expected 400 for large grid, got {response.status_code}"
        
        data = response.json()
        assert "detail" in data, "Error response should have detail"
        assert "1000" in data["detail"] or "too large" in data["detail"].lower(), f"Error should mention 1000 limit: {data['detail']}"
        
        print(f"✅ Large grid correctly rejected: {data['detail']}")


class TestOptimizerCancel:
    """Test optimization cancellation"""
    
    def test_cancel_active_optimization(self, api_client):
        """POST /api/replay/optimize/cancel should cancel active optimization"""
        # Start a larger optimization that takes time
        payload = {
            "grid_config": {
                "zscore_min": {"min": 0.5, "max": 1.5, "steps": 5},
                "delta_ratio_min": {"min": 0.10, "max": 0.30, "steps": 5},
                "ofi_threshold": {"min": 0.1, "max": 0.4, "steps": 4},
                "sl_atr_mult": {"min": 0.5, "max": 1.5, "steps": 4},
                "regime": "TRANSICAO"
            },
            "base_config": {},
            "objective": "sharpe"
        }
        
        start_resp = api_client.post(f"{BASE_URL}/api/replay/optimize", json=payload)
        if start_resp.status_code == 409:
            # Already running, try to cancel it
            cancel_resp = api_client.post(f"{BASE_URL}/api/replay/optimize/cancel")
            if cancel_resp.status_code == 200:
                print("✅ Cancelled existing optimization")
                time.sleep(1)
                start_resp = api_client.post(f"{BASE_URL}/api/replay/optimize", json=payload)
        
        if start_resp.status_code != 200:
            pytest.skip(f"Could not start optimization: {start_resp.text}")
        
        # Wait a bit then cancel
        time.sleep(1)
        
        cancel_resp = api_client.post(f"{BASE_URL}/api/replay/optimize/cancel")
        assert cancel_resp.status_code == 200, f"Expected 200, got {cancel_resp.status_code}: {cancel_resp.text}"
        
        data = cancel_resp.json()
        assert data.get("status") == "cancelled", f"Expected status=cancelled, got {data}"
        
        print("✅ Optimization cancelled successfully")
        
        # Verify status shows cancelled
        time.sleep(1)
        status_resp = api_client.get(f"{BASE_URL}/api/replay/optimize/status")
        status_data = status_resp.json()
        assert status_data.get("status") in ["cancelled", "idle"], f"Status should be cancelled or idle, got {status_data.get('status')}"
        
        print(f"✅ Status after cancel: {status_data.get('status')}")
    
    def test_cancel_no_active_optimization_returns_404(self, api_client):
        """POST /api/replay/optimize/cancel with no active optimization should return 404"""
        # First ensure no optimization is running
        status_resp = api_client.get(f"{BASE_URL}/api/replay/optimize/status")
        if status_resp.json().get("status") == "running":
            # Cancel it first
            api_client.post(f"{BASE_URL}/api/replay/optimize/cancel")
            time.sleep(2)
        
        # Now try to cancel when nothing is running
        cancel_resp = api_client.post(f"{BASE_URL}/api/replay/optimize/cancel")
        assert cancel_resp.status_code == 404, f"Expected 404 when no optimization running, got {cancel_resp.status_code}"
        
        print("✅ Cancel correctly returns 404 when no optimization running")


class TestOptimizerHistory:
    """Test optimization history endpoint"""
    
    def test_get_optimization_history(self, api_client):
        """GET /api/replay/optimize/history should list previous optimizations"""
        response = api_client.get(f"{BASE_URL}/api/replay/optimize/history")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "optimizations" in data, "Response should have optimizations"
        assert "total" in data, "Response should have total"
        
        optimizations = data["optimizations"]
        print(f"✅ History endpoint returns {len(optimizations)} optimizations")
        
        if len(optimizations) > 0:
            opt = optimizations[0]
            assert "optimization_id" in opt, "Optimization should have optimization_id"
            assert "status" in opt, "Optimization should have status"
            assert "objective" in opt, "Optimization should have objective"
            print(f"   Latest: {opt['optimization_id']} - {opt['status']} - {opt['objective']}")


class TestOptimizerObjectives:
    """Test different objective functions"""
    
    def test_sortino_objective_ranking(self, api_client):
        """POST /api/replay/optimize with objective=sortino should rank by sortino_ratio"""
        # Small grid for fast test
        payload = {
            "grid_config": {
                "zscore_min": {"min": 0.5, "max": 1.0, "steps": 2},
                "delta_ratio_min": {"min": 0.10, "max": 0.20, "steps": 2},
                "ofi_threshold": {"min": 0.1, "max": 0.2, "steps": 2},
                "sl_atr_mult": {"min": 0.5, "max": 1.0, "steps": 2},
                "regime": "TRANSICAO"
            },
            "base_config": {},
            "objective": "sortino"
        }
        
        start_resp = api_client.post(f"{BASE_URL}/api/replay/optimize", json=payload)
        if start_resp.status_code == 409:
            # Wait for existing to complete
            time.sleep(15)
            start_resp = api_client.post(f"{BASE_URL}/api/replay/optimize", json=payload)
        
        if start_resp.status_code != 200:
            pytest.skip(f"Could not start optimization: {start_resp.text}")
        
        # Wait for completion
        max_wait = 60
        waited = 0
        while waited < max_wait:
            status_resp = api_client.get(f"{BASE_URL}/api/replay/optimize/status")
            if status_resp.json().get("status") == "completed":
                break
            time.sleep(2)
            waited += 2
        
        result_resp = api_client.get(f"{BASE_URL}/api/replay/optimize/result")
        if result_resp.status_code != 200:
            pytest.skip("Could not get result")
        
        result = result_resp.json()
        assert result["objective"] == "sortino", f"Objective should be sortino, got {result['objective']}"
        
        # Verify results are sorted by sortino (descending)
        results = result["results"]
        if len(results) >= 2:
            for i in range(len(results) - 1):
                # objective_value should be sortino_ratio
                assert results[i]["objective_value"] >= results[i+1]["objective_value"], \
                    f"Results should be sorted by sortino descending: {results[i]['objective_value']} < {results[i+1]['objective_value']}"
        
        print(f"✅ Sortino objective ranking correct. Best sortino: {result['best']['objective_value']}")
    
    def test_min_drawdown_objective_ascending_order(self, api_client):
        """Objective min_drawdown should sort from smallest to largest (inverse)"""
        payload = {
            "grid_config": {
                "zscore_min": {"min": 0.5, "max": 1.0, "steps": 2},
                "delta_ratio_min": {"min": 0.10, "max": 0.20, "steps": 2},
                "ofi_threshold": {"min": 0.1, "max": 0.2, "steps": 2},
                "sl_atr_mult": {"min": 0.5, "max": 1.0, "steps": 2},
                "regime": "TRANSICAO"
            },
            "base_config": {},
            "objective": "min_drawdown"
        }
        
        start_resp = api_client.post(f"{BASE_URL}/api/replay/optimize", json=payload)
        if start_resp.status_code == 409:
            time.sleep(15)
            start_resp = api_client.post(f"{BASE_URL}/api/replay/optimize", json=payload)
        
        if start_resp.status_code != 200:
            pytest.skip(f"Could not start optimization: {start_resp.text}")
        
        # Wait for completion
        max_wait = 60
        waited = 0
        while waited < max_wait:
            status_resp = api_client.get(f"{BASE_URL}/api/replay/optimize/status")
            if status_resp.json().get("status") == "completed":
                break
            time.sleep(2)
            waited += 2
        
        result_resp = api_client.get(f"{BASE_URL}/api/replay/optimize/result")
        if result_resp.status_code != 200:
            pytest.skip("Could not get result")
        
        result = result_resp.json()
        assert result["objective"] == "min_drawdown", f"Objective should be min_drawdown"
        
        # Verify results are sorted by drawdown (ascending - smallest first)
        results = result["results"]
        if len(results) >= 2:
            for i in range(len(results) - 1):
                # For min_drawdown, smaller is better, so should be ascending
                assert results[i]["objective_value"] <= results[i+1]["objective_value"], \
                    f"min_drawdown should be sorted ascending: {results[i]['objective_value']} > {results[i+1]['objective_value']}"
        
        print(f"✅ min_drawdown objective correctly sorted ascending. Best (lowest) DD: {result['best']['objective_value']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
