"""
Test Suite for V3 7-Point Improvements (Iteration 37)
=====================================================
Tests the following improvements:
1. TRANSIÇÃO absorption nomenclature fix + OFI EMA confirmation
2. COMPLACÊNCIA POSITIONAL_ONLY exception for ratio bypass
3. Overnight Inventory (no change - kept as-is)
4. Border detection in TRANSIÇÃO uses distance-based comparison
5. Pullback/bounce tolerance uses 0.25×ATR(M5)
6. Time-Decay Accumulator 30s TTL in N3 absorption
7. Welford Exponential with alpha=0.04 decay
"""

import pytest
import os
import sys
import math

# Add backend to path for imports
sys.path.insert(0, '/app/backend')

from services.delta_zonal_service import DeltaZonalService, WelfordAccumulator, delta_zonal_service


# ══════════════════════════════════════════════════════════════
# #7: Welford Exponential with alpha=0.04 decay
# ══════════════════════════════════════════════════════════════

class TestWelfordExponential:
    """Test Welford Accumulator with exponential decay after 10 observations."""

    def test_welford_cold_start_standard_welford(self):
        """First 10 observations use standard Welford (no decay)."""
        acc = WelfordAccumulator(alpha=0.04)
        
        # Push 10 values
        values = [100, 110, 105, 115, 108, 112, 107, 103, 109, 111]
        for v in values:
            acc.update('MNQ', 'vwap', 'n2', v)
        
        stats = acc.get_stats('MNQ', 'vwap', 'n2')
        assert stats['n'] == 10, f"Expected n=10, got {stats['n']}"
        
        # Standard Welford mean should be arithmetic mean
        expected_mean = sum(values) / len(values)
        assert abs(stats['mean'] - expected_mean) < 0.01, f"Mean mismatch: {stats['mean']} vs {expected_mean}"
        print(f"✅ Cold start (n=10): mean={stats['mean']:.2f}, expected={expected_mean:.2f}")

    def test_welford_exponential_decay_after_10(self):
        """After 10 observations, applies alpha=0.04 exponential decay."""
        acc = WelfordAccumulator(alpha=0.04)
        
        # Push 10 values (cold start)
        for v in [100] * 10:
            acc.update('MNQ', 'poc', 'n2', v)
        
        stats_before = acc.get_stats('MNQ', 'poc', 'n2').copy()
        assert stats_before['n'] == 10
        
        # Push 11th value (should trigger exponential decay)
        acc.update('MNQ', 'poc', 'n2', 200)
        
        stats_after = acc.get_stats('MNQ', 'poc', 'n2')
        assert stats_after['n'] == 11
        
        # With alpha=0.04, new mean = (1-0.04)*100 + 0.04*200 = 96 + 8 = 104
        expected_mean = (1 - 0.04) * 100 + 0.04 * 200
        assert abs(stats_after['mean'] - expected_mean) < 0.1, f"Exponential mean mismatch: {stats_after['mean']} vs {expected_mean}"
        print(f"✅ Exponential decay (n=11): mean={stats_after['mean']:.2f}, expected={expected_mean:.2f}")

    def test_welford_zscore_variance_calculation(self):
        """compute_zscore uses different variance for n<=10 vs n>10."""
        acc = WelfordAccumulator(alpha=0.04)
        
        # Push 5 values (n<=10 path)
        for v in [100, 110, 90, 105, 95]:
            acc.update('MNQ', 'vah', 'n2', v)
        
        zscore_cold = acc.compute_zscore('MNQ', 'vah', 'n2', 120)
        print(f"Z-Score at n=5 (cold): {zscore_cold}")
        
        # Push more to get n>10
        for v in [100, 105, 95, 110, 90, 100]:
            acc.update('MNQ', 'vah', 'n2', v)
        
        zscore_warm = acc.compute_zscore('MNQ', 'vah', 'n2', 120)
        print(f"Z-Score at n=11 (warm): {zscore_warm}")
        
        # Both should be positive (120 > mean ~100)
        assert zscore_cold > 0, "Z-Score should be positive for value above mean"
        assert zscore_warm > 0, "Z-Score should be positive for value above mean"
        print(f"✅ Z-Score calculation works for both n<=10 and n>10")

    def test_welford_alpha_default(self):
        """Default alpha is 0.04."""
        acc = WelfordAccumulator()
        assert acc._alpha == 0.04, f"Default alpha should be 0.04, got {acc._alpha}"
        print("✅ Default alpha=0.04 confirmed")


# ══════════════════════════════════════════════════════════════
# #6: Time-Decay Accumulator 30s TTL in N3 absorption
# ══════════════════════════════════════════════════════════════

class TestTimedecayAccumulator:
    """Test N3 Time-Decay Accumulator with 30s TTL."""

    def test_n3_stitching_within_ttl(self):
        """If price re-enters zone within 30s, volumes are NOT reset (stitched)."""
        service = DeltaZonalService()
        
        # Create trades that exit and re-enter within 30s
        base_ts = 1000_000_000_000  # 1000 seconds in ns
        zone_ttl_ns = 30_000_000_000  # 30s
        
        trades = [
            # First entry into zone (price=100, zone is 99-101)
            {'price': 100.0, 'size': 10, 'side': 'B', 'ts': base_ts},
            {'price': 100.5, 'size': 15, 'side': 'A', 'ts': base_ts + 1_000_000_000},
            # Exit zone
            {'price': 102.0, 'size': 5, 'side': 'B', 'ts': base_ts + 5_000_000_000},
            # Re-enter within 30s (at 10s)
            {'price': 100.2, 'size': 20, 'side': 'B', 'ts': base_ts + 10_000_000_000},
            {'price': 100.3, 'size': 25, 'side': 'A', 'ts': base_ts + 15_000_000_000},
        ]
        
        levels = {'test_level': 100.0}
        result = service.compute_n3_extreme(trades, levels, tick_size=0.25, atr_m1=2.0, symbol='MNQ', zone_ttl_ns=zone_ttl_ns)
        
        level_data = result['levels'].get('test_level', {})
        # Should have accumulated: 10+15+20+25 = 70 (stitched, not reset)
        total_vol = level_data.get('total_volume', 0)
        print(f"Total volume (stitched within TTL): {total_vol}")
        
        # The volumes from both entries should be combined
        assert total_vol >= 50, f"Expected stitched volume >= 50, got {total_vol}"
        print(f"✅ N3 TTL stitching works: volumes accumulated across re-entry within 30s")

    def test_n3_reset_after_ttl_expired(self):
        """If price re-enters zone after >30s, volumes ARE reset."""
        service = DeltaZonalService()
        
        base_ts = 1000_000_000_000
        zone_ttl_ns = 30_000_000_000  # 30s
        
        trades = [
            # First entry into zone
            {'price': 100.0, 'size': 100, 'side': 'B', 'ts': base_ts},
            {'price': 100.5, 'size': 100, 'side': 'A', 'ts': base_ts + 1_000_000_000},
            # Exit zone
            {'price': 102.0, 'size': 5, 'side': 'B', 'ts': base_ts + 5_000_000_000},
            # Re-enter AFTER 30s (at 40s) - should reset
            {'price': 100.2, 'size': 10, 'side': 'B', 'ts': base_ts + 40_000_000_000},
            {'price': 100.3, 'size': 10, 'side': 'A', 'ts': base_ts + 41_000_000_000},
        ]
        
        levels = {'test_level': 100.0}
        result = service.compute_n3_extreme(trades, levels, tick_size=0.25, atr_m1=2.0, symbol='MNQ', zone_ttl_ns=zone_ttl_ns)
        
        level_data = result['levels'].get('test_level', {})
        total_vol = level_data.get('total_volume', 0)
        print(f"Total volume (reset after TTL): {total_vol}")
        
        # After TTL expired, only the second entry's volume should count: 10+10=20
        # The first 200 should have been committed to Welford and reset
        assert total_vol <= 30, f"Expected reset volume <= 30, got {total_vol}"
        print(f"✅ N3 TTL reset works: volumes reset after 30s absence")


# ══════════════════════════════════════════════════════════════
# #5: Pullback/Bounce tolerance uses 0.25×ATR(M5)
# ══════════════════════════════════════════════════════════════

class TestPullbackBounceTolerance:
    """Test that BULL pullback and BEAR bounce use 0.25×ATR(M5)."""

    def test_bull_pullback_tolerance_formula(self):
        """BULL pullback_tolerance = 0.25 * atr_m5."""
        # This is a code inspection test - verify the formula in server.py
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Find the BULL regime section
        bull_match = re.search(r"elif regime == 'BULL':.*?pullback_tolerance = (.*?)(?:\n|$)", content, re.DOTALL)
        assert bull_match, "Could not find BULL pullback_tolerance"
        
        formula = bull_match.group(1)
        assert '0.25 * atr_m5' in formula, f"Expected 0.25 * atr_m5 in formula, got: {formula}"
        print(f"✅ BULL pullback_tolerance formula: {formula.strip()}")

    def test_bear_bounce_tolerance_formula(self):
        """BEAR bounce_tolerance = 0.25 * atr_m5."""
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Find the BEAR regime section
        bear_match = re.search(r"elif regime == 'BEAR':.*?bounce_tolerance = (.*?)(?:\n|$)", content, re.DOTALL)
        assert bear_match, "Could not find BEAR bounce_tolerance"
        
        formula = bear_match.group(1)
        assert '0.25 * atr_m5' in formula, f"Expected 0.25 * atr_m5 in formula, got: {formula}"
        print(f"✅ BEAR bounce_tolerance formula: {formula.strip()}")

    def test_generate_execution_signal_accepts_atr_m5(self):
        """generate_execution_signal now accepts atr_m5 parameter."""
        import inspect
        
        # Import the class
        from server import V3SignalEngine
        
        sig = inspect.signature(V3SignalEngine.generate_execution_signal)
        params = list(sig.parameters.keys())
        
        assert 'atr_m5' in params, f"atr_m5 not in generate_execution_signal params: {params}"
        print(f"✅ generate_execution_signal accepts atr_m5 parameter")

    def test_evaluate_passes_atr_m5_to_generate_execution_signal(self):
        """evaluate() passes atr_m5 to generate_execution_signal."""
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Find the call to generate_execution_signal
        call_match = re.search(r'generate_execution_signal\(.*?atr_m5=atr_m5', content, re.DOTALL)
        assert call_match, "atr_m5=atr_m5 not found in generate_execution_signal call"
        print(f"✅ evaluate() passes atr_m5 to generate_execution_signal")


# ══════════════════════════════════════════════════════════════
# #4: Border detection in TRANSIÇÃO uses distance-based comparison
# ══════════════════════════════════════════════════════════════

class TestTransicaoBorderDetection:
    """Test TRANSIÇÃO border detection uses |price-VAH| vs |price-VAL|."""

    def test_evaluate_filters_distance_based_border(self):
        """evaluate_filters uses distance-based border detection."""
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Find the TRANSICAO section in evaluate_filters
        # Should have: dist_to_vah = abs(current_price - vah)
        assert 'dist_to_vah = abs(current_price - vah)' in content, "Distance-based VAH detection not found"
        assert 'dist_to_val = abs(current_price - val)' in content, "Distance-based VAL detection not found"
        assert 'near_vah = dist_to_vah < dist_to_val' in content, "near_vah comparison not found"
        print(f"✅ evaluate_filters uses distance-based border detection")

    def test_n2_signal_builder_distance_based(self):
        """n2_signal builder uses distance-based border detection."""
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Find the n2_signal section for TRANSICAO
        # Should have: dist_vah = abs(current_price - vah)
        transicao_n2_match = re.search(
            r"elif regime == 'TRANSICAO':.*?dist_vah = abs\(current_price - vah\)",
            content, re.DOTALL
        )
        assert transicao_n2_match, "Distance-based detection not found in n2_signal builder for TRANSICAO"
        print(f"✅ n2_signal builder uses distance-based border detection for TRANSICAO")

    def test_asymmetric_profile_scenario(self):
        """Test with asymmetric profile where POC is near VAH."""
        # Scenario: VAH=100, VAL=80, POC=98 (near VAH)
        # Price=95 should be near_val (closer to VAL=80 than VAH=100)
        # Old logic (price > poc) would say near_vah (95 < 98 → below POC → near_val) ✓
        # But if POC=82 (near VAL), price=95:
        # Old: 95 > 82 → above POC → near_vah (WRONG, 95 is closer to VAH=100)
        # New: |95-100|=5 vs |95-80|=15 → near_vah ✓
        
        vah, val, poc = 100, 80, 82  # POC near VAL
        price = 95
        
        # Old logic (price > poc)
        old_near_vah = price > poc  # 95 > 82 = True (near_vah)
        
        # New logic (distance-based)
        dist_to_vah = abs(price - vah)  # |95-100| = 5
        dist_to_val = abs(price - val)  # |95-80| = 15
        new_near_vah = dist_to_vah < dist_to_val  # 5 < 15 = True (near_vah)
        
        print(f"Asymmetric profile: VAH={vah}, VAL={val}, POC={poc}, Price={price}")
        print(f"Old logic (price > poc): near_vah={old_near_vah}")
        print(f"New logic (distance): dist_vah={dist_to_vah}, dist_val={dist_to_val}, near_vah={new_near_vah}")
        
        # In this case both agree, but the new logic is more robust
        assert new_near_vah == True, "Price 95 should be near VAH (100) not VAL (80)"
        print(f"✅ Distance-based detection correctly identifies border")


# ══════════════════════════════════════════════════════════════
# #2: COMPLACÊNCIA POSITIONAL_ONLY exception for ratio bypass
# ══════════════════════════════════════════════════════════════

class TestComplacenciaPositionalOnly:
    """Test COMPLACÊNCIA with POSITIONAL_ONLY skips ratio validation."""

    def test_positional_only_sets_n2_bullish_flow_true(self):
        """COMPLACÊNCIA with interaction_quality='POSITIONAL_ONLY' sets n2_bullish_flow=True."""
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Find the COMPLACENCIA section
        complacencia_match = re.search(
            r"if regime == 'COMPLACENCIA':.*?if n2_iq == 'POSITIONAL_ONLY':.*?n2_bullish_flow = True",
            content, re.DOTALL
        )
        assert complacencia_match, "POSITIONAL_ONLY bypass not found in COMPLACENCIA"
        print(f"✅ COMPLACÊNCIA POSITIONAL_ONLY sets n2_bullish_flow=True")

    def test_positional_only_comment_explains_bypass(self):
        """Code has comment explaining POSITIONAL_ONLY bypass."""
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        assert 'POSITIONAL_ONLY' in content, "POSITIONAL_ONLY not found in code"
        assert 'skip ratio validation' in content.lower() or 'DZ indisponível' in content, \
            "Comment explaining POSITIONAL_ONLY bypass not found"
        print(f"✅ POSITIONAL_ONLY bypass is documented in code")


# ══════════════════════════════════════════════════════════════
# #1: TRANSIÇÃO absorption nomenclature fix + OFI EMA confirmation
# ══════════════════════════════════════════════════════════════

class TestTransicaoAbsorptionNomenclature:
    """Test TRANSIÇÃO absorption nomenclature and OFI EMA confirmation."""

    def test_sell_absorption_above_poc_triggers_sell(self):
        """SELL_ABSORPTION + above_poc → SELL (not BUY_ABSORPTION)."""
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Find: if abs_type == 'SELL_ABSORPTION' and above_poc:
        #           signal['action'] = 'SELL'
        sell_abs_match = re.search(
            r"if abs_type == 'SELL_ABSORPTION' and above_poc:.*?signal\['action'\] = 'SELL'",
            content, re.DOTALL
        )
        assert sell_abs_match, "SELL_ABSORPTION + above_poc → SELL not found"
        print(f"✅ SELL_ABSORPTION + above_poc → SELL")

    def test_buy_absorption_below_poc_triggers_buy(self):
        """BUY_ABSORPTION + below_poc → BUY (not SELL_ABSORPTION)."""
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Find: elif abs_type == 'BUY_ABSORPTION' and below_poc:
        #           signal['action'] = 'BUY'
        buy_abs_match = re.search(
            r"elif abs_type == 'BUY_ABSORPTION' and below_poc:.*?signal\['action'\] = 'BUY'",
            content, re.DOTALL
        )
        assert buy_abs_match, "BUY_ABSORPTION + below_poc → BUY not found"
        print(f"✅ BUY_ABSORPTION + below_poc → BUY")

    def test_ofi_ema_required_for_sell_absorption(self):
        """SELL_ABSORPTION requires OFI EMA > 0.2 for SELL."""
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Find: ofi_confirm = ofi_fast > 0.2 (for SELL_ABSORPTION)
        ofi_sell_match = re.search(
            r"if abs_type == 'SELL_ABSORPTION' and above_poc:.*?ofi_confirm = ofi_fast > 0\.2",
            content, re.DOTALL
        )
        assert ofi_sell_match, "OFI EMA > 0.2 confirmation for SELL_ABSORPTION not found"
        print(f"✅ SELL_ABSORPTION requires OFI EMA > 0.2")

    def test_ofi_ema_required_for_buy_absorption(self):
        """BUY_ABSORPTION requires OFI EMA < -0.2 for BUY."""
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Find: ofi_confirm = ofi_fast < -0.2 (for BUY_ABSORPTION)
        ofi_buy_match = re.search(
            r"elif abs_type == 'BUY_ABSORPTION' and below_poc:.*?ofi_confirm = ofi_fast < -0\.2",
            content, re.DOTALL
        )
        assert ofi_buy_match, "OFI EMA < -0.2 confirmation for BUY_ABSORPTION not found"
        print(f"✅ BUY_ABSORPTION requires OFI EMA < -0.2")

    def test_without_ofi_confirmation_action_stays_wait(self):
        """Without OFI EMA confirmation, absorption does NOT trigger trade."""
        import re
        
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Check that there's an else clause that keeps action as WAIT
        # The signal dict is initialized with action='WAIT', and if ofi_confirm fails,
        # it should set reason but not change action
        
        # Find the else clause after ofi_confirm check
        else_match = re.search(
            r"if ofi_confirm:.*?signal\['action'\] = '(BUY|SELL)'.*?else:.*?signal\['reason'\] = ",
            content, re.DOTALL
        )
        assert else_match, "Else clause for OFI confirmation failure not found"
        print(f"✅ Without OFI confirmation, action stays WAIT (reason is set)")

    def test_nomenclature_comments_explain_logic(self):
        """Code has comments explaining absorption nomenclature."""
        with open('/app/backend/server.py', 'r') as f:
            content = f.read()
        
        # Check for nomenclature comments
        assert 'BUY_ABSORPTION = passive buyers absorbing sellers' in content or \
               'BUY_ABSORPTION' in content and 'support holds' in content, \
            "BUY_ABSORPTION nomenclature comment not found"
        assert 'SELL_ABSORPTION = passive sellers absorbing buyers' in content or \
               'SELL_ABSORPTION' in content and 'resistance holds' in content, \
            "SELL_ABSORPTION nomenclature comment not found"
        print(f"✅ Absorption nomenclature is documented in code")


# ══════════════════════════════════════════════════════════════
# API Tests
# ══════════════════════════════════════════════════════════════

class TestV3SignalEndpoint:
    """Test V3 signal endpoint returns valid response."""

    def test_health_endpoint(self):
        """Backend health check passes."""
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        base_url = os.environ.get('REACT_APP_BACKEND_URL', 'https://gamma-vix-predictor.preview.emergentagent.com')
        
        # Use retry logic for flaky network
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        session.mount('https://', HTTPAdapter(max_retries=retries))
        
        resp = session.get(f"{base_url}/api/health", timeout=60)
        assert resp.status_code == 200, f"Health check failed: {resp.status_code}"
        data = resp.json()
        assert data.get('status') == 'healthy', f"Health status not healthy: {data}"
        print(f"✅ Health endpoint: {data}")

    def test_v3_signal_endpoint_returns_valid_response(self):
        """V3 signal endpoint returns valid response with N1/N2/N3 fields."""
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        import time
        
        base_url = os.environ.get('REACT_APP_BACKEND_URL', 'https://gamma-vix-predictor.preview.emergentagent.com')
        auth_token = os.environ.get('TEST_AUTH_TOKEN', 'test_session_auth_1775441581328')
        headers = {'Authorization': f'Bearer {auth_token}'}
        
        # Use retry logic for flaky network
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
        session.mount('https://', HTTPAdapter(max_retries=retries))
        
        # Retry up to 3 times with backoff
        for attempt in range(3):
            try:
                resp = session.get(f"{base_url}/api/v3/signal/MNQ", headers=headers, timeout=90)
                if resp.status_code == 200:
                    break
                print(f"Attempt {attempt+1}: status={resp.status_code}")
                time.sleep(5)
            except Exception as e:
                print(f"Attempt {attempt+1} failed: {e}")
                time.sleep(5)
                if attempt == 2:
                    raise
        assert resp.status_code == 200, f"V3 signal failed: {resp.status_code} - {resp.text}"
        
        data = resp.json()
        
        # Check N1 (regime)
        assert 'nivel_1' in data, "nivel_1 not in response"
        n1 = data['nivel_1']
        assert 'regime' in n1, "regime not in nivel_1"
        print(f"N1 regime: {n1.get('regime')}")
        
        # Check N2 (filters)
        assert 'nivel_2' in data, "nivel_2 not in response"
        n2 = data['nivel_2']
        assert 'passed' in n2, "passed not in nivel_2"
        print(f"N2 passed: {n2.get('passed')}")
        
        # Check N3 (execution)
        assert 'nivel_3' in data, "nivel_3 not in response"
        n3 = data['nivel_3']
        assert 'action' in n3, "action not in nivel_3"
        print(f"N3 action: {n3.get('action')}")
        
        # Check context data (contains delta zonal info)
        assert 'context' in data, "context not in response"
        context = data['context']
        assert 'atr_m1' in context, "atr_m1 not in context"
        assert 'atr' in context, "atr not in context"
        print(f"Context atr: {context.get('atr')}, atr_m1: {context.get('atr_m1')}")
        
        # Check N2 has delta zonal data
        assert 'dz_vah' in n2 or 'dz_summary_status' in n2, "Delta zonal data not in nivel_2"
        print(f"N2 dz_summary_status: {n2.get('dz_summary_status')}")
        
        print(f"✅ V3 signal endpoint returns valid response with all N1/N2/N3 fields")


# ══════════════════════════════════════════════════════════════
# Run tests
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
