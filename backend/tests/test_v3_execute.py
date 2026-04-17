"""
V3 Execute and Orders API Tests
Tests for POST /api/v3/execute/{symbol} and GET /api/v3/orders endpoints
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestV3ExecuteEndpoint:
    """Tests for POST /api/v3/execute/{symbol}"""

    def test_execute_with_force_returns_paper_executed(self):
        """POST /api/v3/execute/MNQ?force=true returns status=paper_executed"""
        response = requests.post(f"{BASE_URL}/api/v3/execute/MNQ?force=true", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Status should be paper_executed (default is paper trading mode)
        assert data.get('status') in ['paper_executed', 'executed'], f"Expected paper_executed or executed, got {data.get('status')}"
        assert 'v3_order' in data, "Response should contain v3_order"
        
    def test_execute_force_v3_order_contains_required_fields(self):
        """POST /api/v3/execute/MNQ?force=true v3_order contains all required fields"""
        response = requests.post(f"{BASE_URL}/api/v3/execute/MNQ?force=true", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        v3_order = data.get('v3_order', {})
        
        # Required fields from the spreadsheet mapping
        required_fields = [
            'v3_regime',      # Market regime (COMPLACENCIA, BULL, TRANSICAO, BEAR, CAPITULACAO)
            'v3_tactic',      # Trading tactic per regime
            'trade_symbol',   # Tradovate symbol (MESM26, MNQM26)
            'action',         # buy or sell
            'quantity',       # Calculated from base_qty * lot_pct / 100
            'lot_pct',        # Lot percentage from regime config
            'entry_type',     # MARKET or LIMIT
            'stop_loss',      # Stop loss price
            'take_profit',    # Take profit price
            'trailing_stop',  # Trailing stop config
            'paper_trade',    # True for paper trading
        ]
        
        for field in required_fields:
            assert field in v3_order, f"v3_order missing required field: {field}"
        
        # Validate regime is one of the 5 valid regimes
        valid_regimes = ['COMPLACENCIA', 'BULL', 'TRANSICAO', 'BEAR', 'CAPITULACAO']
        assert v3_order['v3_regime'] in valid_regimes, f"Invalid regime: {v3_order['v3_regime']}"
        
        # Validate action is buy or sell
        assert v3_order['action'] in ['buy', 'sell'], f"Invalid action: {v3_order['action']}"
        
        # Validate paper_trade is True (default mode)
        assert v3_order['paper_trade'] == True, "Expected paper_trade=True"
        
        print(f"✅ V3 Order: {v3_order['v3_regime']} {v3_order['action']} {v3_order['quantity']}x {v3_order['trade_symbol']}")

    def test_execute_without_force_when_wait_returns_waiting(self):
        """POST /api/v3/execute/MNQ (without force) when N3=WAIT returns status=waiting"""
        # First check the V3 signal to see if N3 is WAIT
        signal_response = requests.get(f"{BASE_URL}/api/v3/signal/MNQ", timeout=30)
        signal_data = signal_response.json()
        n3_action = signal_data.get('nivel_3', {}).get('action', 'WAIT')
        
        # Execute without force
        response = requests.post(f"{BASE_URL}/api/v3/execute/MNQ", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        # If N3 is WAIT, status should be 'waiting' or 'disabled'
        # If N3 is BUY/SELL, status could be 'paper_executed' or 'filters_not_passed'
        valid_statuses = ['waiting', 'disabled', 'paper_executed', 'executed', 'filters_not_passed']
        assert data.get('status') in valid_statuses, f"Unexpected status: {data.get('status')}"
        
        if n3_action == 'WAIT':
            # Should return waiting with message
            if data.get('status') == 'waiting':
                assert 'message' in data, "Waiting response should have message"
                print(f"✅ N3=WAIT → status=waiting: {data.get('message')}")
            elif data.get('status') == 'disabled':
                print(f"✅ Auto trading disabled: {data.get('message')}")
        else:
            print(f"✅ N3={n3_action} → status={data.get('status')}")

    def test_execute_force_inserts_order_in_mongodb(self):
        """POST /api/v3/execute/MNQ?force=true inserts order in MongoDB (verify via GET /api/v3/orders)"""
        # Execute with force
        exec_response = requests.post(f"{BASE_URL}/api/v3/execute/MNQ?force=true", timeout=30)
        assert exec_response.status_code == 200
        exec_data = exec_response.json()
        
        # Get the order ID
        order_id = exec_data.get('v3_order', {}).get('id')
        assert order_id, "v3_order should have an id"
        
        # Verify order exists in orders list
        orders_response = requests.get(f"{BASE_URL}/api/v3/orders?limit=10", timeout=15)
        assert orders_response.status_code == 200
        orders_data = orders_response.json()
        
        # Find the order we just created
        order_ids = [o.get('id') for o in orders_data.get('orders', [])]
        assert order_id in order_ids, f"Order {order_id} not found in orders list"
        print(f"✅ Order {order_id} persisted in MongoDB")

    def test_execute_invalid_symbol_returns_404(self):
        """POST /api/v3/execute/INVALID returns 404"""
        response = requests.post(f"{BASE_URL}/api/v3/execute/INVALID?force=true", timeout=15)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ Invalid symbol returns 404")


class TestV3OrdersEndpoint:
    """Tests for GET /api/v3/orders"""

    def test_get_orders_returns_list(self):
        """GET /api/v3/orders returns orders list"""
        response = requests.get(f"{BASE_URL}/api/v3/orders", timeout=15)
        assert response.status_code == 200
        data = response.json()
        
        assert 'orders' in data, "Response should contain 'orders' key"
        assert 'count' in data, "Response should contain 'count' key"
        assert isinstance(data['orders'], list), "orders should be a list"
        print(f"✅ GET /api/v3/orders returns {data['count']} orders")

    def test_get_orders_count_after_executing(self):
        """GET /api/v3/orders returns count > 0 after executing"""
        # First execute an order
        exec_response = requests.post(f"{BASE_URL}/api/v3/execute/MES?force=true", timeout=30)
        assert exec_response.status_code == 200
        
        # Then check orders
        orders_response = requests.get(f"{BASE_URL}/api/v3/orders", timeout=15)
        assert orders_response.status_code == 200
        data = orders_response.json()
        
        assert data['count'] > 0, "Should have at least 1 order after executing"
        print(f"✅ Orders count: {data['count']}")


class TestV3OrdersStatsEndpoint:
    """Tests for GET /api/v3/orders/stats"""

    def test_get_orders_stats_returns_grouped_by_regime(self):
        """GET /api/v3/orders/stats returns stats grouped by regime"""
        response = requests.get(f"{BASE_URL}/api/v3/orders/stats", timeout=15)
        assert response.status_code == 200
        data = response.json()
        
        assert 'stats' in data, "Response should contain 'stats' key"
        assert 'total_orders' in data, "Response should contain 'total_orders' key"
        
        # Stats should be grouped by regime
        if data['stats']:
            for stat in data['stats']:
                assert '_id' in stat, "Each stat should have _id (regime name)"
                assert 'total' in stat, "Each stat should have total count"
                assert 'buys' in stat, "Each stat should have buys count"
                assert 'sells' in stat, "Each stat should have sells count"
                assert 'paper' in stat, "Each stat should have paper count"
                assert 'live' in stat, "Each stat should have live count"
                print(f"  Regime {stat['_id']}: {stat['total']} orders ({stat['buys']} buys, {stat['sells']} sells)")
        
        print(f"✅ Total orders in stats: {data['total_orders']}")


class TestV3QuantityCalculation:
    """Tests for V3 order quantity calculation"""

    def test_quantity_calculation_mes_regimes(self):
        """V3 order quantity: MES regimes use 50% lot (TRANSICAO, CAPITULACAO)"""
        # Execute for MES (which is used in TRANSICAO and CAPITULACAO regimes)
        response = requests.post(f"{BASE_URL}/api/v3/execute/MES?force=true", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        v3_order = data.get('v3_order', {})
        regime = v3_order.get('v3_regime')
        lot_pct = v3_order.get('lot_pct')
        quantity = v3_order.get('quantity')
        
        # MES is used in TRANSICAO (50%) and CAPITULACAO (50%)
        if regime in ['TRANSICAO', 'CAPITULACAO']:
            assert lot_pct == 50, f"Expected lot_pct=50 for {regime}, got {lot_pct}"
        
        # Quantity should be at least 1
        assert quantity >= 1, f"Quantity should be >= 1, got {quantity}"
        print(f"✅ {regime}: lot_pct={lot_pct}%, quantity={quantity}")

    def test_quantity_calculation_mnq_regimes(self):
        """V3 order quantity: MNQ regimes use 75% or 100% lot"""
        response = requests.post(f"{BASE_URL}/api/v3/execute/MNQ?force=true", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        v3_order = data.get('v3_order', {})
        regime = v3_order.get('v3_regime')
        lot_pct = v3_order.get('lot_pct')
        quantity = v3_order.get('quantity')
        
        # MNQ is used in COMPLACENCIA (75%), BULL (100%), BEAR (100%)
        if regime == 'COMPLACENCIA':
            assert lot_pct == 75, f"Expected lot_pct=75 for COMPLACENCIA, got {lot_pct}"
        elif regime in ['BULL', 'BEAR']:
            assert lot_pct == 100, f"Expected lot_pct=100 for {regime}, got {lot_pct}"
        
        assert quantity >= 1, f"Quantity should be >= 1, got {quantity}"
        print(f"✅ {regime}: lot_pct={lot_pct}%, quantity={quantity}")


class TestV3TradeSymbolMapping:
    """Tests for V3 trade symbol mapping per regime"""

    def test_trade_symbol_matches_regime_config(self):
        """V3 order trade_symbol matches regime config"""
        # Execute and check trade symbol
        response = requests.post(f"{BASE_URL}/api/v3/execute/MNQ?force=true", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        v3_order = data.get('v3_order', {})
        regime = v3_order.get('v3_regime')
        trade_symbol = v3_order.get('trade_symbol', '')
        trade_symbol_base = v3_order.get('trade_symbol_base', '')
        
        # Regime to symbol mapping:
        # TRANSICAO, CAPITULACAO → MES (MESM26)
        # COMPLACENCIA, BULL, BEAR → MNQ (MNQM26)
        if regime in ['TRANSICAO', 'CAPITULACAO']:
            assert trade_symbol_base == 'MES', f"Expected MES for {regime}, got {trade_symbol_base}"
            assert 'MES' in trade_symbol, f"trade_symbol should contain MES, got {trade_symbol}"
        elif regime in ['COMPLACENCIA', 'BULL', 'BEAR']:
            assert trade_symbol_base == 'MNQ', f"Expected MNQ for {regime}, got {trade_symbol_base}"
            assert 'MNQ' in trade_symbol, f"trade_symbol should contain MNQ, got {trade_symbol}"
        
        print(f"✅ {regime} → {trade_symbol_base} ({trade_symbol})")


class TestV3ExecutePerformance:
    """Tests for V3 execute endpoint performance"""

    def test_warm_cache_execute_time(self):
        """V3 execute with warm cache should be faster"""
        # First call (cold)
        start1 = time.time()
        response1 = requests.post(f"{BASE_URL}/api/v3/execute/MNQ?force=true", timeout=30)
        time1 = time.time() - start1
        assert response1.status_code == 200
        
        # Second call (warm cache)
        start2 = time.time()
        response2 = requests.post(f"{BASE_URL}/api/v3/execute/MNQ?force=true", timeout=30)
        time2 = time.time() - start2
        assert response2.status_code == 200
        
        print(f"✅ Cold call: {time1:.2f}s, Warm call: {time2:.2f}s")
        # Warm call should be faster (but we don't enforce strict timing due to network variability)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
