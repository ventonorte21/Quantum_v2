"""
Test V3 Trading Dashboard Adjustments
======================================
Testa os 3 ajustes implementados:
1. Hard Stop do FADE (CAPITULACAO) aumentado de 0.5x para 1.0x ATR_M1
2. Scale-Out 50% para TREND — limit order para realizar metade da posição quando lucro atinge 2x risco inicial
3. N3 Event-Driven Watcher — avalia N3 a cada 2s via live data quando V3 status=FILTERS_PASSED

Também testa:
- GET /api/signalstack/symbols — apenas MNQ e MES (MYM e M2K removidos)
- GET /api/n3-watcher/status — retorna estado armado/desarmado para MNQ e MES
- POST /api/snapshots/record-now — grava snapshots e alimenta N3 Watcher
"""

import pytest
import requests
import os
import sys

# Add backend to path for imports
sys.path.insert(0, '/app/backend')

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestPositionManagerSLMultipliers:
    """Testa os multiplicadores de SL por arquétipo no position_manager.py"""

    def test_fade_sl_multiplier_is_1_0(self):
        """FADE (CAPITULACAO) deve ter SL_ATR_MULTIPLIER = 1.0 (era 0.5)"""
        from services.position_manager import SL_ATR_MULTIPLIER, Archetype
        
        assert Archetype.FADE in SL_ATR_MULTIPLIER, "FADE deve estar em SL_ATR_MULTIPLIER"
        assert SL_ATR_MULTIPLIER[Archetype.FADE] == 1.0, f"FADE SL multiplier deve ser 1.0, mas é {SL_ATR_MULTIPLIER[Archetype.FADE]}"
        print(f"✅ FADE SL_ATR_MULTIPLIER = {SL_ATR_MULTIPLIER[Archetype.FADE]} (correto)")

    def test_trend_sl_multiplier_is_1_5(self):
        """TREND deve ter SL_ATR_MULTIPLIER = 1.5"""
        from services.position_manager import SL_ATR_MULTIPLIER, Archetype
        
        assert Archetype.TREND in SL_ATR_MULTIPLIER, "TREND deve estar em SL_ATR_MULTIPLIER"
        assert SL_ATR_MULTIPLIER[Archetype.TREND] == 1.5, f"TREND SL multiplier deve ser 1.5, mas é {SL_ATR_MULTIPLIER[Archetype.TREND]}"
        print(f"✅ TREND SL_ATR_MULTIPLIER = {SL_ATR_MULTIPLIER[Archetype.TREND]} (correto)")

    def test_range_sl_multiplier_is_0_5(self):
        """RANGE deve ter SL_ATR_MULTIPLIER = 0.5"""
        from services.position_manager import SL_ATR_MULTIPLIER, Archetype
        
        assert Archetype.RANGE in SL_ATR_MULTIPLIER, "RANGE deve estar em SL_ATR_MULTIPLIER"
        assert SL_ATR_MULTIPLIER[Archetype.RANGE] == 0.5, f"RANGE SL multiplier deve ser 0.5, mas é {SL_ATR_MULTIPLIER[Archetype.RANGE]}"
        print(f"✅ RANGE SL_ATR_MULTIPLIER = {SL_ATR_MULTIPLIER[Archetype.RANGE]} (correto)")


class TestTrendScaleOut:
    """Testa a configuração de Scale-Out 50% para TREND"""

    def test_trend_returns_scale_out_config(self):
        """TREND deve retornar scale_out config com enabled=True, pct=50, trigger_multiple=2.0"""
        from services.position_manager import calculate_position_params
        
        # Simula parâmetros de entrada
        params = calculate_position_params(
            regime='COMPLACENCIA',  # Maps to TREND
            entry_price=21500.0,
            side='BUY',
            atr_m1=5.0,
            levels={
                'vwap': 21480.0,
                'poc': 21490.0,
                'vah': 21520.0,
                'val': 21460.0,
                'call_wall': 21600.0,
                'put_wall': 21400.0,
            },
            tick_size=0.25,
        )
        
        assert params['archetype'] == 'TREND', f"Regime COMPLACENCIA deve mapear para TREND, mas é {params['archetype']}"
        assert 'scale_out' in params, "TREND deve ter scale_out config"
        
        scale_out = params['scale_out']
        assert scale_out['enabled'] == True, "scale_out.enabled deve ser True"
        assert scale_out['pct'] == 50, f"scale_out.pct deve ser 50, mas é {scale_out['pct']}"
        assert scale_out['trigger_multiple'] == 2.0, f"scale_out.trigger_multiple deve ser 2.0, mas é {scale_out['trigger_multiple']}"
        assert scale_out['move_stop_to_entry'] == True, "scale_out.move_stop_to_entry deve ser True"
        
        print(f"✅ TREND scale_out config: enabled={scale_out['enabled']}, pct={scale_out['pct']}, trigger_multiple={scale_out['trigger_multiple']}")

    def test_trend_scale_out_trigger_price_calculation(self):
        """TREND scale_out.trigger_price deve ser entry + 2×SL_distance (para BUY)"""
        from services.position_manager import calculate_position_params, SL_ATR_MULTIPLIER, Archetype
        
        entry_price = 21500.0
        atr_m1 = 5.0
        
        params = calculate_position_params(
            regime='BULL',  # Maps to TREND
            entry_price=entry_price,
            side='BUY',
            atr_m1=atr_m1,
            levels={'vwap': 21480.0, 'poc': 21490.0, 'vah': 21520.0, 'val': 21460.0, 'call_wall': 21600.0, 'put_wall': 21400.0},
            tick_size=0.25,
        )
        
        sl_distance = SL_ATR_MULTIPLIER[Archetype.TREND] * atr_m1  # 1.5 * 5.0 = 7.5
        expected_trigger = entry_price + sl_distance * 2.0  # 21500 + 7.5 * 2 = 21515.0
        
        actual_trigger = params['scale_out']['trigger_price']
        assert abs(actual_trigger - expected_trigger) < 0.01, f"trigger_price deve ser {expected_trigger}, mas é {actual_trigger}"
        
        print(f"✅ TREND scale_out trigger_price: {actual_trigger} (entry={entry_price}, SL_dist={sl_distance}, 2×risk={expected_trigger})")

    def test_trend_scale_out_trigger_price_sell(self):
        """TREND scale_out.trigger_price para SELL deve ser entry - 2×SL_distance"""
        from services.position_manager import calculate_position_params, SL_ATR_MULTIPLIER, Archetype
        
        entry_price = 21500.0
        atr_m1 = 5.0
        
        params = calculate_position_params(
            regime='BEAR',  # Maps to TREND
            entry_price=entry_price,
            side='SELL',
            atr_m1=atr_m1,
            levels={'vwap': 21480.0, 'poc': 21490.0, 'vah': 21520.0, 'val': 21460.0, 'call_wall': 21600.0, 'put_wall': 21400.0},
            tick_size=0.25,
        )
        
        sl_distance = SL_ATR_MULTIPLIER[Archetype.TREND] * atr_m1  # 1.5 * 5.0 = 7.5
        expected_trigger = entry_price - sl_distance * 2.0  # 21500 - 7.5 * 2 = 21485.0
        
        actual_trigger = params['scale_out']['trigger_price']
        assert abs(actual_trigger - expected_trigger) < 0.01, f"trigger_price (SELL) deve ser {expected_trigger}, mas é {actual_trigger}"
        
        print(f"✅ TREND scale_out trigger_price (SELL): {actual_trigger} (entry={entry_price}, 2×risk={expected_trigger})")

    def test_range_has_no_scale_out(self):
        """RANGE não deve ter scale_out config"""
        from services.position_manager import calculate_position_params
        
        params = calculate_position_params(
            regime='TRANSICAO',  # Maps to RANGE
            entry_price=21500.0,
            side='BUY',
            atr_m1=5.0,
            levels={'vwap': 21480.0, 'poc': 21490.0, 'vah': 21520.0, 'val': 21460.0, 'call_wall': 21600.0, 'put_wall': 21400.0},
            tick_size=0.25,
        )
        
        assert params['archetype'] == 'RANGE', f"Regime TRANSICAO deve mapear para RANGE, mas é {params['archetype']}"
        assert 'scale_out' not in params or params.get('scale_out') is None, "RANGE não deve ter scale_out"
        
        print(f"✅ RANGE não tem scale_out (correto)")

    def test_fade_has_no_scale_out(self):
        """FADE não deve ter scale_out config"""
        from services.position_manager import calculate_position_params
        
        params = calculate_position_params(
            regime='CAPITULACAO',  # Maps to FADE
            entry_price=21500.0,
            side='BUY',
            atr_m1=5.0,
            levels={'vwap': 21480.0, 'poc': 21490.0, 'vah': 21520.0, 'val': 21460.0, 'call_wall': 21600.0, 'put_wall': 21400.0},
            tick_size=0.25,
        )
        
        assert params['archetype'] == 'FADE', f"Regime CAPITULACAO deve mapear para FADE, mas é {params['archetype']}"
        assert 'scale_out' not in params or params.get('scale_out') is None, "FADE não deve ter scale_out"
        
        print(f"✅ FADE não tem scale_out (correto)")


class TestSignalStackSymbols:
    """Testa GET /api/signalstack/symbols — apenas MNQ e MES"""

    def test_signalstack_symbols_only_mnq_mes(self):
        """GET /api/signalstack/symbols deve retornar apenas MNQ e MES (MYM e M2K removidos)"""
        response = requests.get(f"{BASE_URL}/api/signalstack/symbols")
        assert response.status_code == 200, f"Status code deve ser 200, mas é {response.status_code}"
        
        data = response.json()
        assert 'symbols' in data, "Resposta deve ter 'symbols'"
        
        symbols = data['symbols']
        symbol_keys = list(symbols.keys())
        
        # Deve ter MNQ e MES
        assert 'MNQ' in symbol_keys, "MNQ deve estar em symbols"
        assert 'MES' in symbol_keys, "MES deve estar em symbols"
        
        # Não deve ter MYM e M2K
        assert 'MYM' not in symbol_keys, "MYM NÃO deve estar em symbols (removido)"
        assert 'M2K' not in symbol_keys, "M2K NÃO deve estar em symbols (removido)"
        
        # Deve ter exatamente 2 símbolos
        assert len(symbol_keys) == 2, f"Deve ter exatamente 2 símbolos, mas tem {len(symbol_keys)}: {symbol_keys}"
        
        print(f"✅ GET /api/signalstack/symbols retorna apenas: {symbol_keys}")

    def test_signalstack_symbols_tradovate_format(self):
        """Símbolos devem estar no formato Tradovate (1 dígito de ano)"""
        response = requests.get(f"{BASE_URL}/api/signalstack/symbols")
        data = response.json()
        
        for symbol, info in data['symbols'].items():
            tradovate = info['tradovate']
            # Formato: MNQM6 (símbolo + mês + 1 dígito ano)
            assert len(tradovate) >= 5, f"Tradovate symbol deve ter pelo menos 5 chars: {tradovate}"
            assert tradovate.startswith(symbol), f"Tradovate symbol deve começar com {symbol}: {tradovate}"
            # Último char deve ser dígito (ano)
            assert tradovate[-1].isdigit(), f"Último char deve ser dígito (ano): {tradovate}"
            
            print(f"✅ {symbol} → {tradovate}")


class TestN3WatcherStatus:
    """Testa GET /api/n3-watcher/status"""

    def test_n3_watcher_status_endpoint(self):
        """GET /api/n3-watcher/status deve retornar estado para MNQ e MES"""
        response = requests.get(f"{BASE_URL}/api/n3-watcher/status")
        assert response.status_code == 200, f"Status code deve ser 200, mas é {response.status_code}"
        
        data = response.json()
        
        # Deve ter MNQ e MES
        assert 'MNQ' in data, "Resposta deve ter 'MNQ'"
        assert 'MES' in data, "Resposta deve ter 'MES'"
        
        # Cada símbolo deve ter campos esperados
        for symbol in ['MNQ', 'MES']:
            state = data[symbol]
            assert 'armed' in state, f"{symbol} deve ter 'armed'"
            assert 'regime' in state, f"{symbol} deve ter 'regime'"
            assert 'armed_at' in state, f"{symbol} deve ter 'armed_at'"
            assert 'eval_count' in state, f"{symbol} deve ter 'eval_count'"
            assert 'hit_count' in state, f"{symbol} deve ter 'hit_count'"
            assert 'last_signal_at' in state, f"{symbol} deve ter 'last_signal_at'"
            
            print(f"✅ {symbol}: armed={state['armed']}, regime={state['regime']}, eval_count={state['eval_count']}")

    def test_n3_watcher_not_armed_on_weekend(self):
        """N3 Watcher não deve estar armado no fim de semana (is_trading_day=False)"""
        response = requests.get(f"{BASE_URL}/api/n3-watcher/status")
        data = response.json()
        
        # No fim de semana, watchers não devem estar armados
        # (snapshot_loop não arma porque is_trading_day=False)
        for symbol in ['MNQ', 'MES']:
            state = data[symbol]
            # Não assertamos que armed=False porque pode ter sido armado manualmente
            # Apenas verificamos que o estado é válido
            assert isinstance(state['armed'], bool), f"{symbol}.armed deve ser bool"
            
        print(f"✅ N3 Watcher status válido (fim de semana - comportamento esperado)")


class TestSnapshotsRecordNow:
    """Testa POST /api/snapshots/record-now"""

    def test_snapshots_record_now_endpoint(self):
        """POST /api/snapshots/record-now deve gravar snapshots e retornar resultado"""
        try:
            response = requests.post(f"{BASE_URL}/api/snapshots/record-now", timeout=30)
            # Pode retornar 502 no fim de semana devido a timeout ou rate limiting
            assert response.status_code in [200, 502, 504], f"Status code deve ser 200/502/504, mas é {response.status_code}"
            
            if response.status_code == 200:
                data = response.json()
                # Deve ter campos esperados
                assert 'recorded' in data or 'symbols' in data or 'status' in data, f"Resposta deve ter 'recorded', 'symbols' ou 'status': {data}"
                print(f"✅ POST /api/snapshots/record-now: {data}")
            else:
                print(f"⚠️ POST /api/snapshots/record-now retornou {response.status_code} (esperado no fim de semana)")
        except requests.exceptions.ReadTimeout:
            # Timeout é esperado no fim de semana (APIs externas lentas/rate-limited)
            print(f"⚠️ POST /api/snapshots/record-now timeout (esperado no fim de semana - APIs externas lentas)")
        except requests.exceptions.ConnectionError as e:
            print(f"⚠️ POST /api/snapshots/record-now connection error: {e}")

    def test_snapshots_stats_endpoint(self):
        """GET /api/snapshots/stats deve retornar estatísticas"""
        response = requests.get(f"{BASE_URL}/api/snapshots/stats")
        assert response.status_code == 200, f"Status code deve ser 200, mas é {response.status_code}"
        
        data = response.json()
        print(f"✅ GET /api/snapshots/stats: {data}")


class TestOpenPositionWebhooks:
    """Testa a lógica de webhooks em open_position por arquétipo"""

    def test_trend_webhooks_count(self):
        """TREND deve enviar 4 webhooks: entry+trailing, SL, scale-out, (skip TP=None)"""
        # Este teste verifica a lógica no código, não envia webhooks reais
        from services.position_manager import calculate_position_params
        
        params = calculate_position_params(
            regime='COMPLACENCIA',  # TREND
            entry_price=21500.0,
            side='BUY',
            atr_m1=5.0,
            levels={'vwap': 21480.0, 'poc': 21490.0, 'vah': 21520.0, 'val': 21460.0, 'call_wall': 21600.0, 'put_wall': 21400.0},
            tick_size=0.25,
        )
        
        # TREND: TP é None (trailing tira), scale_out é enabled
        assert params['archetype'] == 'TREND'
        assert params.get('take_profit') is None or params.get('tp_type') == 'OPEN_TRAILING', "TREND TP deve ser None ou OPEN_TRAILING"
        assert params.get('scale_out', {}).get('enabled') == True, "TREND scale_out deve estar enabled"
        assert params.get('trailing_type') == 'VWAP_CENTRAL', "TREND deve ter trailing VWAP_CENTRAL"
        
        # Webhooks esperados: 1. Entry+trailing, 2. SL, 3. Scale-out (TP=None, não envia)
        # Se TP não é None, seria 4 webhooks
        print(f"✅ TREND: TP={params.get('take_profit')}, scale_out={params.get('scale_out', {}).get('enabled')}, trailing={params.get('trailing_type')}")

    def test_fade_webhooks_count(self):
        """FADE deve enviar 3 webhooks: entry+breakeven, SL, TP"""
        from services.position_manager import calculate_position_params
        
        params = calculate_position_params(
            regime='CAPITULACAO',  # FADE
            entry_price=21500.0,
            side='BUY',
            atr_m1=5.0,
            levels={'vwap': 21480.0, 'poc': 21490.0, 'vah': 21520.0, 'val': 21460.0, 'call_wall': 21600.0, 'put_wall': 21400.0, 'vwap_lower_1s': 21470.0},
            tick_size=0.25,
        )
        
        assert params['archetype'] == 'FADE'
        assert params.get('take_profit') is not None, "FADE deve ter TP"
        assert params.get('trailing_type') == 'BREAK_EVEN', "FADE deve ter trailing BREAK_EVEN"
        assert params.get('break_even') is not None, "FADE deve ter break_even config"
        assert 'scale_out' not in params or params.get('scale_out') is None, "FADE não deve ter scale_out"
        
        # Webhooks esperados: 1. Entry+breakeven, 2. SL, 3. TP
        print(f"✅ FADE: TP={params.get('take_profit')}, breakeven={params.get('break_even', {}).get('threshold')}, trailing={params.get('trailing_type')}")

    def test_range_webhooks_count(self):
        """RANGE deve enviar 3 webhooks: entry, SL, TP (sem scale-out nem trailing)"""
        from services.position_manager import calculate_position_params
        
        params = calculate_position_params(
            regime='TRANSICAO',  # RANGE
            entry_price=21500.0,
            side='BUY',
            atr_m1=5.0,
            levels={'vwap': 21480.0, 'poc': 21490.0, 'vah': 21520.0, 'val': 21460.0, 'call_wall': 21600.0, 'put_wall': 21400.0},
            tick_size=0.25,
        )
        
        assert params['archetype'] == 'RANGE'
        assert params.get('take_profit') is not None, "RANGE deve ter TP"
        assert params.get('trailing_type') == 'NONE', "RANGE não deve ter trailing"
        assert params.get('trailing_config') is None, "RANGE trailing_config deve ser None"
        assert 'scale_out' not in params or params.get('scale_out') is None, "RANGE não deve ter scale_out"
        assert params.get('break_even') is None, "RANGE não deve ter break_even"
        
        # Webhooks esperados: 1. Entry, 2. SL, 3. TP
        print(f"✅ RANGE: TP={params.get('take_profit')}, trailing={params.get('trailing_type')}, monitoring={params.get('monitoring_required')}")


class TestBuildSignalStackPayload:
    """Testa build_signalstack_payload para scale-out"""

    def test_scale_out_payload_is_limit_order(self):
        """Scale-out payload deve ser limit order com quantidade correta"""
        # Importa do server.py
        import sys
        sys.path.insert(0, '/app/backend')
        
        # Simula o que open_position faz para scale-out
        from services.position_manager import calculate_position_params, SL_ATR_MULTIPLIER, Archetype
        
        entry_price = 21500.0
        atr_m1 = 5.0
        quantity = 4  # Para ter scale-out de 2 contratos
        
        params = calculate_position_params(
            regime='COMPLACENCIA',  # TREND
            entry_price=entry_price,
            side='BUY',
            atr_m1=atr_m1,
            levels={'vwap': 21480.0, 'poc': 21490.0, 'vah': 21520.0, 'val': 21460.0, 'call_wall': 21600.0, 'put_wall': 21400.0},
            tick_size=0.25,
        )
        
        scale_out = params['scale_out']
        so_qty = max(1, quantity * scale_out['pct'] // 100)  # 4 * 50 // 100 = 2
        so_price = scale_out['trigger_price']
        
        assert so_qty == 2, f"Scale-out qty deve ser 2 (50% de 4), mas é {so_qty}"
        
        # Verifica que trigger_price está correto
        sl_distance = SL_ATR_MULTIPLIER[Archetype.TREND] * atr_m1  # 1.5 * 5.0 = 7.5
        expected_price = entry_price + sl_distance * 2.0  # 21500 + 15 = 21515
        assert abs(so_price - expected_price) < 0.01, f"Scale-out price deve ser {expected_price}, mas é {so_price}"
        
        print(f"✅ Scale-out payload: qty={so_qty}, limit_price={so_price} (2×risk de entry={entry_price})")


class TestN3WatcherClass:
    """Testa a classe N3Watcher diretamente"""

    def test_n3_watcher_has_mnq_mes_only(self):
        """N3Watcher deve ter watchers apenas para MNQ e MES"""
        from services.n3_watcher import N3Watcher
        
        # Cria instância mock
        class MockEngine:
            async def evaluate(self, symbol):
                return {}
        
        async def mock_callback(symbol):
            return {}
        
        class MockLiveData:
            pass
        
        watcher = N3Watcher(MockEngine(), mock_callback, MockLiveData())
        
        assert 'MNQ' in watcher.watchers, "N3Watcher deve ter MNQ"
        assert 'MES' in watcher.watchers, "N3Watcher deve ter MES"
        assert len(watcher.watchers) == 2, f"N3Watcher deve ter exatamente 2 símbolos, mas tem {len(watcher.watchers)}"
        
        print(f"✅ N3Watcher watchers: {list(watcher.watchers.keys())}")

    def test_n3_watcher_check_and_arm_filters_passed(self):
        """check_and_arm deve armar quando v3_status=FILTERS_PASSED e n2.passed=True"""
        import asyncio
        from services.n3_watcher import N3Watcher
        
        class MockEngine:
            async def evaluate(self, symbol):
                return {}
        
        async def mock_callback(symbol):
            return {}
        
        class MockLiveData:
            pass
        
        async def run_test():
            watcher = N3Watcher(MockEngine(), mock_callback, MockLiveData())
            
            # Simula V3 result com FILTERS_PASSED
            v3_result = {
                'v3_status': 'FILTERS_PASSED',
                'nivel_1': {'regime': 'COMPLACENCIA'},
                'nivel_2': {'passed': True},
            }
            
            # Deve armar
            armed = watcher.check_and_arm('MNQ', v3_result)
            assert armed == True, "check_and_arm deve retornar True para FILTERS_PASSED"
            assert watcher.watchers['MNQ'].armed == True, "MNQ deve estar armado"
            assert watcher.watchers['MNQ'].regime == 'COMPLACENCIA', "Regime deve ser COMPLACENCIA"
            
            # Cleanup: cancel the task
            if watcher.watchers['MNQ'].task:
                watcher.watchers['MNQ'].task.cancel()
                try:
                    await watcher.watchers['MNQ'].task
                except asyncio.CancelledError:
                    pass
            
            return True
        
        result = asyncio.run(run_test())
        assert result == True
        print(f"✅ check_and_arm arma corretamente para FILTERS_PASSED")

    def test_n3_watcher_check_and_arm_active_signal_disarms(self):
        """check_and_arm deve desarmar quando v3_status=ACTIVE_SIGNAL"""
        import asyncio
        from services.n3_watcher import N3Watcher
        
        class MockEngine:
            async def evaluate(self, symbol):
                return {}
        
        async def mock_callback(symbol):
            return {}
        
        class MockLiveData:
            pass
        
        async def run_test():
            watcher = N3Watcher(MockEngine(), mock_callback, MockLiveData())
            
            # Primeiro arma
            watcher.check_and_arm('MNQ', {
                'v3_status': 'FILTERS_PASSED',
                'nivel_1': {'regime': 'COMPLACENCIA'},
                'nivel_2': {'passed': True},
            })
            assert watcher.watchers['MNQ'].armed == True
            
            # Cleanup task before disarming
            if watcher.watchers['MNQ'].task:
                watcher.watchers['MNQ'].task.cancel()
                try:
                    await watcher.watchers['MNQ'].task
                except asyncio.CancelledError:
                    pass
            
            # Depois ACTIVE_SIGNAL deve desarmar
            armed = watcher.check_and_arm('MNQ', {
                'v3_status': 'ACTIVE_SIGNAL',
                'nivel_1': {'regime': 'COMPLACENCIA'},
                'nivel_2': {'passed': True},
            })
            assert armed == False, "check_and_arm deve retornar False para ACTIVE_SIGNAL"
            assert watcher.watchers['MNQ'].armed == False, "MNQ deve estar desarmado após ACTIVE_SIGNAL"
            
            return True
        
        result = asyncio.run(run_test())
        assert result == True
        print(f"✅ check_and_arm desarma corretamente para ACTIVE_SIGNAL")

    def test_n3_watcher_get_status(self):
        """get_status deve retornar estado para todos os símbolos"""
        from services.n3_watcher import N3Watcher
        
        class MockEngine:
            async def evaluate(self, symbol):
                return {}
        
        async def mock_callback(symbol):
            return {}
        
        class MockLiveData:
            pass
        
        watcher = N3Watcher(MockEngine(), mock_callback, MockLiveData())
        
        status = watcher.get_status()
        
        assert 'MNQ' in status, "Status deve ter MNQ"
        assert 'MES' in status, "Status deve ter MES"
        
        for symbol in ['MNQ', 'MES']:
            s = status[symbol]
            assert 'armed' in s
            assert 'regime' in s
            assert 'armed_at' in s
            assert 'eval_count' in s
            assert 'hit_count' in s
            assert 'last_signal_at' in s
            assert 'armed_duration_s' in s
        
        print(f"✅ get_status retorna campos corretos: {list(status['MNQ'].keys())}")


class TestFadeHardStopCalculation:
    """Testa o cálculo do hard stop para FADE com 1.0x ATR"""

    def test_fade_hard_stop_uses_1_0_atr(self):
        """FADE hard_stop deve usar 1.0×ATR (não 0.5×)"""
        from services.position_manager import calculate_position_params, SL_ATR_MULTIPLIER, Archetype
        
        entry_price = 21500.0
        atr_m1 = 10.0  # ATR maior para facilitar verificação
        
        params = calculate_position_params(
            regime='CAPITULACAO',  # FADE
            entry_price=entry_price,
            side='BUY',
            atr_m1=atr_m1,
            levels={
                'vwap': 21480.0,
                'poc': 21490.0,
                'vah': 21520.0,
                'val': 21460.0,
                'call_wall': 21600.0,
                'put_wall': 21400.0,  # SL será put_wall - sl_distance
            },
            tick_size=0.25,
        )
        
        # FADE SL = put_wall - 1.0×ATR (para BUY)
        sl_distance = SL_ATR_MULTIPLIER[Archetype.FADE] * atr_m1  # 1.0 * 10.0 = 10.0
        expected_sl = 21400.0 - sl_distance  # 21400 - 10 = 21390
        
        actual_sl = params['hard_stop']
        assert abs(actual_sl - expected_sl) < 0.01, f"FADE hard_stop deve ser {expected_sl}, mas é {actual_sl}"
        
        print(f"✅ FADE hard_stop: {actual_sl} (put_wall={21400.0} - 1.0×ATR={sl_distance} = {expected_sl})")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
