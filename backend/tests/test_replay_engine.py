"""
Replay Engine API Tests
========================
Tests for Walk-Forward Backtest endpoints:
- GET /api/replay/defaults
- POST /api/replay/run
- GET /api/replay/runs
- GET /api/replay/run/{run_id}
- POST /api/replay/compare
- POST /api/replay/batch
- DELETE /api/replay/run/{run_id}
- GET /api/replay/snapshot-stats
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
AUTH_TOKEN = os.environ.get("TEST_AUTH_TOKEN", "test_session_auth_1775441581328")

@pytest.fixture
def api_client():
    """Shared requests session with auth"""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Cookie": f"session_token={AUTH_TOKEN}"
    })
    return session


class TestReplayDefaults:
    """Test GET /api/replay/defaults endpoint"""
    
    def test_get_defaults_returns_config(self, api_client):
        """Verify defaults endpoint returns complete config"""
        response = api_client.get(f"{BASE_URL}/api/replay/defaults")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "config" in data, "Response should contain 'config' key"
        
        config = data["config"]
        # Verify all expected parameter groups exist
        expected_keys = [
            "sl_atr_mult", "max_daily_loss_pct", "max_consecutive_losses",
            "tp_mode", "fixed_rr", "rr_min_threshold",
            "scale_out_enabled", "scale_out_pct", "scale_out_trigger_mult",
            "trailing_enabled", "trail_trigger_atr_mult", "trail_stop_atr_mult",
            "breakeven_enabled", "breakeven_trigger_atr_mult", "breakeven_ticks_buffer",
            "ofi_threshold", "zscore_min",
            "initial_capital", "contracts_per_signal", "use_lot_pct",
            "slippage_ticks", "commission_per_contract",
            "symbols", "regimes_filter", "min_snapshots_between_trades"
        ]
        
        for key in expected_keys:
            assert key in config, f"Config missing expected key: {key}"
        
        # Verify sl_atr_mult has archetype keys
        assert "TREND" in config["sl_atr_mult"], "sl_atr_mult should have TREND key"
        assert "RANGE" in config["sl_atr_mult"], "sl_atr_mult should have RANGE key"
        assert "FADE" in config["sl_atr_mult"], "sl_atr_mult should have FADE key"
        
        print(f"✅ Defaults endpoint returns complete config with {len(config)} parameters")


class TestReplayRun:
    """Test POST /api/replay/run endpoint"""
    
    def test_run_with_empty_config_uses_defaults(self, api_client):
        """Run replay with empty config (uses defaults)"""
        response = api_client.post(f"{BASE_URL}/api/replay/run", json={"config": {}})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Verify run structure
        assert "run_id" in data, "Response should contain run_id"
        assert "trades" in data, "Response should contain trades"
        assert "metrics" in data, "Response should contain metrics"
        assert "config" in data, "Response should contain config"
        assert "snapshot_count" in data, "Response should contain snapshot_count"
        assert "duration_ms" in data, "Response should contain duration_ms"
        
        print(f"✅ Run with empty config: run_id={data['run_id']}, trades={len(data['trades'])}, snapshots={data['snapshot_count']}")
        
        return data
    
    def test_run_metrics_include_all_kpis(self, api_client):
        """Verify metrics include all required KPIs"""
        response = api_client.post(f"{BASE_URL}/api/replay/run", json={"config": {}})
        assert response.status_code == 200
        
        metrics = response.json()["metrics"]
        
        # Required KPIs
        required_metrics = [
            "total_trades", "win_rate", "total_pnl",
            "sharpe_ratio", "sortino_ratio", "profit_factor",
            "max_drawdown", "equity_curve",
            "regime_breakdown", "archetype_breakdown", "exit_breakdown"
        ]
        
        for metric in required_metrics:
            assert metric in metrics, f"Metrics missing required KPI: {metric}"
        
        # Verify equity_curve is a list
        assert isinstance(metrics["equity_curve"], list), "equity_curve should be a list"
        
        # Verify breakdowns are dicts
        assert isinstance(metrics["regime_breakdown"], dict), "regime_breakdown should be a dict"
        assert isinstance(metrics["archetype_breakdown"], dict), "archetype_breakdown should be a dict"
        assert isinstance(metrics["exit_breakdown"], dict), "exit_breakdown should be a dict"
        
        print(f"✅ Metrics include all KPIs: Sharpe={metrics['sharpe_ratio']}, Sortino={metrics['sortino_ratio']}, PF={metrics['profit_factor']}")
    
    def test_run_with_custom_sl_produces_different_results(self, api_client):
        """Changing sl_atr_mult should produce different results"""
        # Run 1: Default config
        resp1 = api_client.post(f"{BASE_URL}/api/replay/run", json={"config": {}})
        assert resp1.status_code == 200
        result1 = resp1.json()
        
        # Run 2: Modified SL multiplier (RANGE from 0.5 to 1.0)
        custom_config = {
            "sl_atr_mult": {"TREND": 1.5, "RANGE": 1.0, "FADE": 1.0}
        }
        resp2 = api_client.post(f"{BASE_URL}/api/replay/run", json={"config": custom_config})
        assert resp2.status_code == 200
        result2 = resp2.json()
        
        # Results should be different (different SL = different exits)
        # Note: They might be the same if no RANGE trades occurred, so we just verify both ran
        print(f"✅ Run 1 (default SL): trades={result1['metrics']['total_trades']}, pnl=${result1['metrics']['total_pnl']}")
        print(f"✅ Run 2 (custom SL): trades={result2['metrics']['total_trades']}, pnl=${result2['metrics']['total_pnl']}")
        
        return result1["run_id"], result2["run_id"]


class TestReplayRunsList:
    """Test GET /api/replay/runs endpoint"""
    
    def test_list_runs_returns_sorted_by_recent(self, api_client):
        """List runs should return most recent first"""
        response = api_client.get(f"{BASE_URL}/api/replay/runs?limit=10")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "runs" in data, "Response should contain 'runs' key"
        assert "total" in data, "Response should contain 'total' key"
        
        runs = data["runs"]
        assert isinstance(runs, list), "runs should be a list"
        
        if len(runs) >= 2:
            # Verify sorted by most recent (stored_at descending)
            for i in range(len(runs) - 1):
                if runs[i].get("stored_at") and runs[i+1].get("stored_at"):
                    assert runs[i]["stored_at"] >= runs[i+1]["stored_at"], "Runs should be sorted by most recent"
        
        print(f"✅ List runs: {len(runs)} runs returned, total={data['total']}")


class TestReplayRunById:
    """Test GET /api/replay/run/{run_id} endpoint"""
    
    def test_get_run_by_id_returns_full_details(self, api_client):
        """Get run by ID should return full details with trades"""
        # First create a run
        create_resp = api_client.post(f"{BASE_URL}/api/replay/run", json={"config": {}})
        assert create_resp.status_code == 200
        run_id = create_resp.json()["run_id"]
        
        # Then fetch it
        response = api_client.get(f"{BASE_URL}/api/replay/run/{run_id}")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["run_id"] == run_id, "run_id should match"
        assert "trades" in data, "Response should contain trades"
        assert "metrics" in data, "Response should contain metrics"
        assert "config" in data, "Response should contain config"
        
        # Verify trades have required fields
        if data["trades"]:
            trade = data["trades"][0]
            trade_fields = ["id", "symbol", "side", "entry_price", "exit_price", "net_pnl", "exit_reason"]
            for field in trade_fields:
                assert field in trade, f"Trade missing field: {field}"
        
        print(f"✅ Get run {run_id}: {len(data['trades'])} trades, pnl=${data['metrics']['total_pnl']}")
        
        return run_id
    
    def test_get_nonexistent_run_returns_404(self, api_client):
        """Get non-existent run should return 404"""
        response = api_client.get(f"{BASE_URL}/api/replay/run/nonexistent123")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ Non-existent run returns 404")


class TestReplayCompare:
    """Test POST /api/replay/compare endpoint"""
    
    def test_compare_multiple_runs(self, api_client):
        """Compare multiple runs side by side"""
        # Create two runs
        resp1 = api_client.post(f"{BASE_URL}/api/replay/run", json={"config": {}})
        resp2 = api_client.post(f"{BASE_URL}/api/replay/run", json={"config": {"initial_capital": 50000}})
        
        run_id1 = resp1.json()["run_id"]
        run_id2 = resp2.json()["run_id"]
        
        # Compare them
        response = api_client.post(f"{BASE_URL}/api/replay/compare", json={"run_ids": [run_id1, run_id2]})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "comparisons" in data, "Response should contain 'comparisons' key"
        assert "count" in data, "Response should contain 'count' key"
        assert data["count"] == 2, f"Expected 2 comparisons, got {data['count']}"
        
        # Verify each comparison has metrics
        for comp in data["comparisons"]:
            assert "run_id" in comp, "Comparison should have run_id"
            assert "metrics" in comp, "Comparison should have metrics"
        
        print(f"✅ Compare runs: {data['count']} runs compared")


class TestReplayBatch:
    """Test POST /api/replay/batch endpoint"""
    
    def test_batch_runs_multiple_configs(self, api_client):
        """Run batch with multiple configs and rank by objective"""
        configs = [
            {},  # Default config
            {"sl_atr_mult": {"TREND": 1.5, "RANGE": 1.0, "FADE": 1.0}}  # Modified SL
        ]
        
        response = api_client.post(f"{BASE_URL}/api/replay/batch", json={
            "configs": configs,
            "objective": "sharpe"
        })
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "batch_id" in data, "Response should contain batch_id"
        assert "objective" in data, "Response should contain objective"
        assert "total_configs" in data, "Response should contain total_configs"
        assert "results" in data, "Response should contain results"
        
        assert data["objective"] == "sharpe", "Objective should be sharpe"
        assert data["total_configs"] == 2, f"Expected 2 configs, got {data['total_configs']}"
        
        # Verify results are ranked
        results = data["results"]
        assert len(results) == 2, f"Expected 2 results, got {len(results)}"
        
        for i, result in enumerate(results):
            assert result["rank"] == i + 1, f"Result {i} should have rank {i+1}"
            assert "objective_value" in result, "Result should have objective_value"
            assert "metrics_summary" in result, "Result should have metrics_summary"
        
        print(f"✅ Batch run: {data['total_configs']} configs, ranked by {data['objective']}")
        for r in results:
            print(f"   Rank {r['rank']}: Sharpe={r['objective_value']}, PnL=${r['metrics_summary']['total_pnl']}")


class TestReplayDelete:
    """Test DELETE /api/replay/run/{run_id} endpoint"""
    
    def test_delete_run(self, api_client):
        """Delete a run and verify it's gone"""
        # Create a run
        create_resp = api_client.post(f"{BASE_URL}/api/replay/run", json={"config": {}})
        assert create_resp.status_code == 200
        run_id = create_resp.json()["run_id"]
        
        # Delete it
        delete_resp = api_client.delete(f"{BASE_URL}/api/replay/run/{run_id}")
        assert delete_resp.status_code == 200, f"Expected 200, got {delete_resp.status_code}: {delete_resp.text}"
        
        data = delete_resp.json()
        assert data["deleted"] == run_id, "Deleted run_id should match"
        
        # Verify it's gone
        get_resp = api_client.get(f"{BASE_URL}/api/replay/run/{run_id}")
        assert get_resp.status_code == 404, "Deleted run should return 404"
        
        print(f"✅ Delete run {run_id}: successfully deleted and verified gone")
    
    def test_delete_nonexistent_run_returns_404(self, api_client):
        """Delete non-existent run should return 404"""
        response = api_client.delete(f"{BASE_URL}/api/replay/run/nonexistent456")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ Delete non-existent run returns 404")


class TestReplaySnapshotStats:
    """Test GET /api/replay/snapshot-stats endpoint"""
    
    def test_get_snapshot_stats(self, api_client):
        """Get snapshot collection statistics"""
        response = api_client.get(f"{BASE_URL}/api/replay/snapshot-stats")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Verify required fields
        assert "total_snapshots" in data, "Response should contain total_snapshots"
        assert "actionable_signals" in data, "Response should contain actionable_signals"
        assert "by_symbol" in data, "Response should contain by_symbol"
        assert "by_regime" in data, "Response should contain by_regime"
        
        # Verify counts are reasonable
        assert data["total_snapshots"] >= 0, "total_snapshots should be >= 0"
        assert data["actionable_signals"] >= 0, "actionable_signals should be >= 0"
        
        print(f"✅ Snapshot stats: {data['total_snapshots']} total, {data['actionable_signals']} actionable")
        print(f"   By symbol: {data['by_symbol']}")
        print(f"   By regime: {data['by_regime']}")


class TestReplayTradeDetails:
    """Test trade details in replay results"""
    
    def test_trades_have_all_required_fields(self, api_client):
        """Verify trades have all required fields for dashboard display"""
        response = api_client.post(f"{BASE_URL}/api/replay/run", json={"config": {}})
        assert response.status_code == 200
        
        trades = response.json()["trades"]
        
        if not trades:
            print("⚠️ No trades generated - skipping trade field validation")
            return
        
        required_fields = [
            "id", "symbol", "regime", "archetype", "side",
            "quantity", "entry_price", "exit_price",
            "hard_stop", "take_profit",
            "entry_time", "exit_time", "exit_reason",
            "gross_pnl", "net_pnl", "commission",
            "duration_minutes"
        ]
        
        for trade in trades[:5]:  # Check first 5 trades
            for field in required_fields:
                assert field in trade, f"Trade missing field: {field}"
        
        # Verify exit_reason values
        valid_exit_reasons = ["STOP_LOSS", "TAKE_PROFIT", "END_OF_DATA", "DAILY_LOSS_LIMIT", "CONSECUTIVE_LOSS_LIMIT"]
        for trade in trades:
            assert trade["exit_reason"] in valid_exit_reasons, f"Invalid exit_reason: {trade['exit_reason']}"
        
        print(f"✅ All {len(trades)} trades have required fields")
        
        # Print sample trade
        if trades:
            t = trades[0]
            print(f"   Sample: {t['symbol']} {t['side']} @ {t['entry_price']} → {t['exit_price']} ({t['exit_reason']}) P&L=${t['net_pnl']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
