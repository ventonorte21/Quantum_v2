"""
Trading System Review Tests (Iteration 58)
==========================================
Tests for the unified trading system: Replay, SignalStack, Journal, and Fills.

Key validations:
1. Replay: max_concurrent_positions=1 prevents overlap (no 2 trades open at same time)
2. Replay: All trades have duration_minutes > 0 (no 0-second trades)
3. SignalStack: GET /signalstack/orders returns both 'orders' and 'v3_trades'
4. Journal: GET /fills/journal returns positions with PnL
5. Stats: GET /fills/stats returns breakdown by regime and symbol
"""

import pytest
import requests
import os
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestReplayEngine:
    """Test Replay Engine with multi-symbol config and overlap validation."""

    @pytest.fixture(scope="class")
    def replay_result(self):
        """Run replay once and cache result for all tests in this class."""
        config = {
            "symbols": ["MES", "MNQ"],
            "signal_mode": "DYNAMIC",
            "max_concurrent_positions": 1,  # V3 rule: 1 active position at a time
            "min_snapshots_between_trades": 5,
        }
        response = requests.post(
            f"{BASE_URL}/api/replay/run",
            json={"config": config},
            timeout=90  # Replay processes ~2900 snapshots, needs time
        )
        assert response.status_code == 200, f"Replay failed: {response.text}"
        return response.json()

    def test_replay_returns_trades(self, replay_result):
        """Verify replay returns trades array."""
        assert "trades" in replay_result, "Response missing 'trades' key"
        trades = replay_result["trades"]
        print(f"✅ Replay returned {len(trades)} trades")
        # May have 0 trades if no signals pass DYNAMIC filters - that's OK
        assert isinstance(trades, list), "trades should be a list"

    def test_replay_returns_metrics(self, replay_result):
        """Verify replay returns metrics object."""
        assert "metrics" in replay_result, "Response missing 'metrics' key"
        metrics = replay_result["metrics"]
        assert "total_trades" in metrics, "metrics missing total_trades"
        assert "win_rate" in metrics, "metrics missing win_rate"
        print(f"✅ Metrics: {metrics.get('total_trades')} trades, {metrics.get('win_rate')}% win rate")

    def test_no_trade_overlap(self, replay_result):
        """
        CRITICAL: Verify no two trades are open at the same time.
        
        V3 Engine rule: max_concurrent_positions=1 means only 1 trade can be
        active at any moment, regardless of symbol.
        
        Overlap detection: if entry2 < exit1 AND entry1 < exit2, there's overlap.
        """
        trades = replay_result.get("trades", [])
        if len(trades) < 2:
            print(f"⚠️ Only {len(trades)} trades - cannot test overlap")
            pytest.skip("Need at least 2 trades to test overlap")

        overlaps = []
        for i, t1 in enumerate(trades):
            for j, t2 in enumerate(trades):
                if i >= j:
                    continue  # Skip self and already-compared pairs

                # Parse times
                entry1 = t1.get("entry_time", "")
                exit1 = t1.get("exit_time", "")
                entry2 = t2.get("entry_time", "")
                exit2 = t2.get("exit_time", "")

                if not all([entry1, exit1, entry2, exit2]):
                    continue

                # Overlap: entry2 < exit1 AND entry1 < exit2
                if entry2 < exit1 and entry1 < exit2:
                    overlaps.append({
                        "trade1": {"id": t1.get("id"), "symbol": t1.get("symbol"), "entry": entry1, "exit": exit1},
                        "trade2": {"id": t2.get("id"), "symbol": t2.get("symbol"), "entry": entry2, "exit": exit2},
                    })

        if overlaps:
            print(f"❌ Found {len(overlaps)} overlapping trade pairs:")
            for o in overlaps[:5]:  # Show first 5
                print(f"   Trade {o['trade1']['id']} ({o['trade1']['symbol']}): {o['trade1']['entry']} → {o['trade1']['exit']}")
                print(f"   Trade {o['trade2']['id']} ({o['trade2']['symbol']}): {o['trade2']['entry']} → {o['trade2']['exit']}")

        assert len(overlaps) == 0, f"Found {len(overlaps)} overlapping trades (V3 rule violation)"
        print(f"✅ No overlapping trades found among {len(trades)} trades")

    def test_no_zero_duration_trades(self, replay_result):
        """
        Verify all trades have duration_minutes > 0.
        
        Bug fix: Previously, snapshots from different symbols could update
        the wrong position, causing instant exits (duration=0).
        """
        trades = replay_result.get("trades", [])
        if not trades:
            print("⚠️ No trades to check duration")
            pytest.skip("No trades returned")

        zero_duration = [t for t in trades if t.get("duration_minutes", 0) <= 0]

        if zero_duration:
            print(f"❌ Found {len(zero_duration)} trades with duration <= 0:")
            for t in zero_duration[:5]:
                print(f"   Trade {t.get('id')} ({t.get('symbol')}): duration={t.get('duration_minutes')} min")

        assert len(zero_duration) == 0, f"Found {len(zero_duration)} trades with duration <= 0"
        print(f"✅ All {len(trades)} trades have duration > 0")

    def test_replay_config_preserved(self, replay_result):
        """Verify the config is preserved in the result."""
        config = replay_result.get("config", {})
        assert config.get("max_concurrent_positions") == 1, "max_concurrent_positions should be 1"
        assert "MES" in config.get("symbols", []), "MES should be in symbols"
        assert "MNQ" in config.get("symbols", []), "MNQ should be in symbols"
        print(f"✅ Config preserved: symbols={config.get('symbols')}, max_concurrent={config.get('max_concurrent_positions')}")


class TestSignalStackOrders:
    """Test SignalStack orders endpoint with V3 paper trades merge."""

    def test_signalstack_orders_endpoint(self):
        """GET /api/signalstack/orders should return both orders and v3_trades."""
        response = requests.get(f"{BASE_URL}/api/signalstack/orders", timeout=30)
        assert response.status_code == 200, f"SignalStack orders failed: {response.text}"
        
        data = response.json()
        
        # Must have both keys
        assert "orders" in data, "Response missing 'orders' key"
        assert "v3_trades" in data, "Response missing 'v3_trades' key"
        assert "count" in data, "Response missing 'count' key"
        assert "v3_count" in data, "Response missing 'v3_count' key"
        
        print(f"✅ SignalStack orders: {data['count']} SS orders, {data['v3_count']} V3 paper trades")

    def test_signalstack_orders_structure(self):
        """Verify v3_trades have expected fields."""
        response = requests.get(f"{BASE_URL}/api/signalstack/orders", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        v3_trades = data.get("v3_trades", [])
        
        if v3_trades:
            trade = v3_trades[0]
            expected_fields = ["symbol", "action", "quantity", "status", "source"]
            for field in expected_fields:
                assert field in trade, f"v3_trade missing '{field}' field"
            print(f"✅ V3 trade structure valid: {list(trade.keys())}")
        else:
            print("⚠️ No V3 trades found (may be empty if no paper trades exist)")

    def test_signalstack_orders_with_symbol_filter(self):
        """Test symbol filter parameter."""
        response = requests.get(f"{BASE_URL}/api/signalstack/orders?symbol=MES", timeout=30)
        assert response.status_code == 200, f"Symbol filter failed: {response.text}"
        
        data = response.json()
        assert "orders" in data
        assert "v3_trades" in data
        print(f"✅ Symbol filter works: {data['count']} orders, {data['v3_count']} v3_trades for MES")


class TestFillsJournal:
    """Test Fills/Journal endpoints."""

    def test_journal_endpoint(self):
        """GET /api/fills/journal should return positions with PnL."""
        response = requests.get(f"{BASE_URL}/api/fills/journal", timeout=30)
        assert response.status_code == 200, f"Journal failed: {response.text}"
        
        data = response.json()
        assert "positions" in data, "Response missing 'positions' key"
        assert "count" in data, "Response missing 'count' key"
        
        positions = data["positions"]
        assert isinstance(positions, list), "positions should be a list"
        print(f"✅ Journal returned {data['count']} positions")

    def test_journal_pnl_calculation(self):
        """Verify closed positions have realized_pnl calculated."""
        response = requests.get(f"{BASE_URL}/api/fills/journal?status=CLOSED", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        positions = data.get("positions", [])
        
        if positions:
            # Check that closed positions have realized_pnl
            for pos in positions[:5]:
                if pos.get("state") == "CLOSED":
                    assert "realized_pnl" in pos, f"Closed position missing realized_pnl: {pos.get('id')}"
            print(f"✅ Closed positions have realized_pnl field")
        else:
            print("⚠️ No closed positions found")

    def test_journal_with_filters(self):
        """Test journal with symbol and days filters."""
        response = requests.get(f"{BASE_URL}/api/fills/journal?symbol=MNQ&days=30", timeout=30)
        assert response.status_code == 200, f"Journal filter failed: {response.text}"
        
        data = response.json()
        assert "positions" in data
        print(f"✅ Journal filter works: {data['count']} MNQ positions in last 30 days")


class TestFillsStats:
    """Test Fills stats endpoint."""

    def test_stats_endpoint(self):
        """GET /api/fills/stats should return aggregated statistics."""
        response = requests.get(f"{BASE_URL}/api/fills/stats", timeout=30)
        assert response.status_code == 200, f"Stats failed: {response.text}"
        
        data = response.json()
        
        # Required fields
        required = ["period_days", "total_trades", "total_pnl", "win_rate", "by_regime", "by_symbol"]
        for field in required:
            assert field in data, f"Stats missing '{field}' field"
        
        print(f"✅ Stats: {data['total_trades']} trades, ${data['total_pnl']} PnL, {data['win_rate']}% win rate")

    def test_stats_by_regime(self):
        """Verify stats include breakdown by regime."""
        response = requests.get(f"{BASE_URL}/api/fills/stats", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        by_regime = data.get("by_regime", {})
        
        assert isinstance(by_regime, dict), "by_regime should be a dict"
        print(f"✅ Stats by regime: {list(by_regime.keys())}")

    def test_stats_by_symbol(self):
        """Verify stats include breakdown by symbol."""
        response = requests.get(f"{BASE_URL}/api/fills/stats", timeout=30)
        assert response.status_code == 200
        
        data = response.json()
        by_symbol = data.get("by_symbol", {})
        
        assert isinstance(by_symbol, dict), "by_symbol should be a dict"
        print(f"✅ Stats by symbol: {list(by_symbol.keys())}")

    def test_stats_with_days_param(self):
        """Test stats with custom days parameter."""
        response = requests.get(f"{BASE_URL}/api/fills/stats?days=7", timeout=30)
        assert response.status_code == 200, f"Stats days param failed: {response.text}"
        
        data = response.json()
        assert data.get("period_days") == 7, "period_days should match param"
        print(f"✅ Stats with days=7: {data['total_trades']} trades")


class TestBackendCompilation:
    """Verify backend compiles and runs without errors."""

    def test_health_endpoint(self):
        """Basic health check to verify server is running."""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200, f"Health check failed: {response.text}"
        print("✅ Backend health check passed")

    def test_replay_defaults_endpoint(self):
        """Verify replay defaults endpoint works (tests replay_engine import)."""
        response = requests.get(f"{BASE_URL}/api/replay/defaults", timeout=10)
        assert response.status_code == 200, f"Replay defaults failed: {response.text}"
        
        data = response.json()
        assert "config" in data, "Response missing 'config' key"
        assert data["config"].get("max_concurrent_positions") == 1, "Default max_concurrent should be 1"
        print(f"✅ Replay defaults: max_concurrent_positions={data['config'].get('max_concurrent_positions')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
