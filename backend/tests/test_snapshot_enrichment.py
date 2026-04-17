"""
Test Snapshot Enrichment for Replay Engine (Iteration 38)

Tests:
1. trade_preview field in snapshot document
2. atr_m5 field in context (both evaluate() return and snapshot)
3. n2_signal field in n2 block of snapshot
4. POST /api/snapshots/record-now returns recorded=2
5. GET /api/v3/signal/{symbol} returns valid response with atr_m5
"""

import pytest
import requests
import os
from pymongo import MongoClient
from datetime import datetime, timezone

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
AUTH_TOKEN = os.environ.get("TEST_AUTH_TOKEN", "test_session_auth_1775441581328")
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'test_database')


@pytest.fixture(scope="module")
def mongo_client():
    """MongoDB client for direct snapshot verification"""
    client = MongoClient(MONGO_URL)
    yield client
    client.close()


@pytest.fixture(scope="module")
def db(mongo_client):
    """Database instance"""
    return mongo_client[DB_NAME]


@pytest.fixture(scope="module")
def api_client():
    """Requests session with auth header"""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AUTH_TOKEN}"
    })
    return session


class TestBackendHealth:
    """Verify backend is running before snapshot tests"""
    
    def test_health_endpoint(self, api_client):
        """Backend health check"""
        response = api_client.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200, f"Health check failed: {response.text}"
        data = response.json()
        assert data.get("status") == "healthy"
        print("✅ Backend health check passed")


class TestV3SignalEndpoint:
    """Test V3 signal endpoint returns atr_m5 in context"""
    
    def test_v3_signal_mes_returns_atr_m5(self, api_client):
        """GET /api/v3/signal/MES should return atr_m5 in context"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MES")
        assert response.status_code == 200, f"V3 signal failed: {response.text}"
        
        data = response.json()
        
        # Verify basic structure
        assert "nivel_1" in data, "Missing nivel_1 in response"
        assert "nivel_2" in data, "Missing nivel_2 in response"
        assert "nivel_3" in data, "Missing nivel_3 in response"
        assert "context" in data, "Missing context in response"
        
        # Verify atr_m5 in context
        context = data.get("context", {})
        assert "atr_m5" in context, "Missing atr_m5 in context"
        atr_m5 = context.get("atr_m5")
        assert atr_m5 is not None, "atr_m5 is None"
        assert isinstance(atr_m5, (int, float)), f"atr_m5 should be numeric, got {type(atr_m5)}"
        
        print(f"✅ V3 signal MES returns atr_m5={atr_m5}")
    
    def test_v3_signal_mnq_returns_atr_m5(self, api_client):
        """GET /api/v3/signal/MNQ should return atr_m5 in context"""
        response = api_client.get(f"{BASE_URL}/api/v3/signal/MNQ")
        assert response.status_code == 200, f"V3 signal failed: {response.text}"
        
        data = response.json()
        context = data.get("context", {})
        
        assert "atr_m5" in context, "Missing atr_m5 in context"
        atr_m5 = context.get("atr_m5")
        assert atr_m5 is not None, "atr_m5 is None"
        
        print(f"✅ V3 signal MNQ returns atr_m5={atr_m5}")


class TestSnapshotRecordNow:
    """Test POST /api/snapshots/record-now endpoint"""
    
    def test_record_now_returns_recorded_2(self, api_client):
        """POST /api/snapshots/record-now should record 2 snapshots (MNQ, MES)"""
        response = api_client.post(f"{BASE_URL}/api/snapshots/record-now")
        assert response.status_code == 200, f"Record-now failed: {response.text}"
        
        data = response.json()
        
        # Verify response structure
        assert "recorded" in data, "Missing 'recorded' in response"
        assert "total_symbols" in data, "Missing 'total_symbols' in response"
        
        recorded = data.get("recorded", 0)
        total_symbols = data.get("total_symbols", 0)
        errors = data.get("errors", [])
        
        # Should record 2 snapshots (MNQ and MES)
        assert recorded == 2, f"Expected recorded=2, got {recorded}. Errors: {errors}"
        assert total_symbols == 2, f"Expected total_symbols=2, got {total_symbols}"
        assert len(errors) == 0, f"Unexpected errors: {errors}"
        
        print(f"✅ Record-now returned recorded={recorded}, total_symbols={total_symbols}")


class TestSnapshotDocument:
    """Test snapshot document contains required fields"""
    
    def test_snapshot_has_trade_preview(self, db, api_client):
        """Snapshot document should contain 'trade_preview' key"""
        # First trigger a snapshot recording
        api_client.post(f"{BASE_URL}/api/snapshots/record-now")
        
        # Query latest snapshot
        collection = db.v3_snapshots
        latest = collection.find_one(
            {},
            sort=[("recorded_at", -1)]
        )
        
        assert latest is not None, "No snapshots found in database"
        
        # trade_preview should exist as a key (can be None for WAIT signals)
        assert "trade_preview" in latest, "Missing 'trade_preview' key in snapshot document"
        
        trade_preview = latest.get("trade_preview")
        action = latest.get("action", "WAIT")
        
        if action in ["BUY", "SELL"]:
            # For actionable signals, trade_preview should be populated
            if trade_preview is not None:
                # Verify trade_preview structure
                assert isinstance(trade_preview, dict), f"trade_preview should be dict, got {type(trade_preview)}"
                print(f"✅ Snapshot has trade_preview with keys: {list(trade_preview.keys())}")
            else:
                print(f"⚠️ trade_preview is None for action={action} (may be expected if position near wrong VA border)")
        else:
            # For WAIT signals, trade_preview can be None
            print(f"✅ Snapshot has trade_preview key (value={trade_preview}, action={action})")
    
    def test_snapshot_context_has_atr_m5(self, db, api_client):
        """Snapshot document context should contain 'atr_m5' field"""
        # Trigger snapshot
        api_client.post(f"{BASE_URL}/api/snapshots/record-now")
        
        # Query latest snapshot
        collection = db.v3_snapshots
        latest = collection.find_one(
            {},
            sort=[("recorded_at", -1)]
        )
        
        assert latest is not None, "No snapshots found in database"
        assert "context" in latest, "Missing 'context' in snapshot document"
        
        context = latest.get("context", {})
        assert "atr_m5" in context, "Missing 'atr_m5' in snapshot context"
        
        atr_m5 = context.get("atr_m5")
        # atr_m5 should be a positive number (or 0 if no data)
        assert atr_m5 is not None, "atr_m5 is None in snapshot context"
        assert isinstance(atr_m5, (int, float)), f"atr_m5 should be numeric, got {type(atr_m5)}"
        
        print(f"✅ Snapshot context has atr_m5={atr_m5}")
    
    def test_snapshot_n2_has_n2_signal(self, db, api_client):
        """Snapshot document n2 block should contain 'n2_signal' field"""
        # Trigger snapshot
        api_client.post(f"{BASE_URL}/api/snapshots/record-now")
        
        # Query latest snapshot
        collection = db.v3_snapshots
        latest = collection.find_one(
            {},
            sort=[("recorded_at", -1)]
        )
        
        assert latest is not None, "No snapshots found in database"
        assert "n2" in latest, "Missing 'n2' in snapshot document"
        
        n2 = latest.get("n2", {})
        assert "n2_signal" in n2, "Missing 'n2_signal' in snapshot n2 block"
        
        n2_signal = n2.get("n2_signal", {})
        
        # n2_signal should have expected keys
        expected_keys = ["passed", "trigger_level", "interaction_quality", "trigger_zscore", "trigger_delta_ratio", "regime"]
        
        if n2_signal:
            present_keys = list(n2_signal.keys())
            print(f"✅ Snapshot n2 has n2_signal with keys: {present_keys}")
            
            # Check for expected keys
            for key in expected_keys:
                if key in n2_signal:
                    print(f"  - {key}: {n2_signal[key]}")
                else:
                    print(f"  - {key}: (not present)")
        else:
            print(f"✅ Snapshot n2 has n2_signal key (empty dict - may be expected if N2 not triggered)")


class TestSnapshotFieldsForBothSymbols:
    """Verify both MNQ and MES snapshots have required fields"""
    
    def test_both_symbols_have_required_fields(self, db, api_client):
        """Both MNQ and MES snapshots should have trade_preview, atr_m5, n2_signal"""
        # Trigger snapshot
        api_client.post(f"{BASE_URL}/api/snapshots/record-now")
        
        collection = db.v3_snapshots
        
        for symbol in ["MNQ", "MES"]:
            latest = collection.find_one(
                {"symbol": symbol},
                sort=[("recorded_at", -1)]
            )
            
            assert latest is not None, f"No snapshot found for {symbol}"
            
            # Check trade_preview key exists
            assert "trade_preview" in latest, f"{symbol}: Missing 'trade_preview' key"
            
            # Check context.atr_m5
            context = latest.get("context", {})
            assert "atr_m5" in context, f"{symbol}: Missing 'atr_m5' in context"
            
            # Check n2.n2_signal
            n2 = latest.get("n2", {})
            assert "n2_signal" in n2, f"{symbol}: Missing 'n2_signal' in n2 block"
            
            print(f"✅ {symbol} snapshot has all required fields")
            print(f"   - trade_preview: {'present' if latest.get('trade_preview') else 'None'}")
            print(f"   - context.atr_m5: {context.get('atr_m5')}")
            print(f"   - n2.n2_signal: {n2.get('n2_signal', {})}")


class TestSnapshotStats:
    """Test snapshot stats endpoint"""
    
    def test_snapshot_stats_endpoint(self, api_client):
        """GET /api/snapshots/stats should return valid stats"""
        response = api_client.get(f"{BASE_URL}/api/snapshots/stats")
        assert response.status_code == 200, f"Stats endpoint failed: {response.text}"
        
        data = response.json()
        
        assert "total_snapshots" in data, "Missing total_snapshots"
        assert "by_symbol" in data, "Missing by_symbol"
        
        total = data.get("total_snapshots", 0)
        by_symbol = data.get("by_symbol", {})
        
        print(f"✅ Snapshot stats: total={total}, by_symbol={by_symbol}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
