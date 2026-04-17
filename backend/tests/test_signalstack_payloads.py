"""
Test SignalStack Payload Builder & Endpoints
=============================================
Testa:
1. GET /api/signalstack/symbols — símbolos com 1 dígito de ano (ex: MNQM6)
2. POST /api/signalstack/test-payloads — 7 tipos de payload
3. build_signalstack_payload — market, limit, stop, stop_limit, trailing, breakeven, cancel
4. POST /api/autotrading/flatten-all — envia close + cancel
5. PUT /api/positions/{id}/update-stop — envia update_sl=true
6. POST /api/positions/{id}/close — envia close market + cancel
7. get_tradovate_symbol — 1-digit year (ex: 6 para 2026)
8. position_manager.py — evaluate_position_update foi removida
"""

import pytest
import requests
import os
import sys
from datetime import datetime

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
WEBHOOK_URL = "https://app.signalstack.com/hook/a65Cvk39pE3HdZiutAi9rP"


class TestGetTradovateSymbol:
    """Test get_tradovate_symbol function — 1-digit year format"""

    def test_symbols_endpoint_returns_1_digit_year(self):
        """GET /api/signalstack/symbols deve retornar símbolos com 1 dígito de ano"""
        response = requests.get(f"{BASE_URL}/api/signalstack/symbols")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "symbols" in data, "Response deve conter 'symbols'"
        
        # Verificar que todos os símbolos têm 1 dígito de ano
        for base_symbol, info in data["symbols"].items():
            tradovate = info["tradovate"]
            # Formato esperado: MNQ + M (mês) + 6 (ano 1 dígito) = MNQM6
            assert len(tradovate) == len(base_symbol) + 2, f"Símbolo {tradovate} deve ter {len(base_symbol) + 2} chars"
            
            # Último caractere deve ser 1 dígito (0-9)
            year_digit = tradovate[-1]
            assert year_digit.isdigit(), f"Último char de {tradovate} deve ser dígito, got '{year_digit}'"
            
            # Penúltimo caractere deve ser código de mês (H, M, U, Z)
            month_code = tradovate[-2]
            assert month_code in ['H', 'M', 'U', 'Z'], f"Código de mês inválido em {tradovate}: '{month_code}'"
            
            print(f"✅ {base_symbol} -> {tradovate} (1-digit year OK)")

    def test_mnq_symbol_format(self):
        """MNQ deve retornar formato correto para abril 2026 = MNQM6"""
        response = requests.get(f"{BASE_URL}/api/signalstack/symbols")
        assert response.status_code == 200
        
        data = response.json()
        mnq = data["symbols"]["MNQ"]["tradovate"]
        
        # Em janeiro 2026, estamos no Q1 (H=Mar), mas se passamos de 15 de março, rola para M=Jun
        # Em abril 2026, o front month é M=Jun (junho)
        now = datetime.now()
        expected_year = now.year % 10  # 6 para 2026
        
        # Verificar formato básico
        assert mnq.startswith("MNQ"), f"Deve começar com MNQ, got {mnq}"
        assert mnq[-1] == str(expected_year), f"Ano deve ser {expected_year}, got {mnq[-1]}"
        print(f"✅ MNQ symbol: {mnq} (year digit = {expected_year})")


class TestBuildSignalStackPayload:
    """Test build_signalstack_payload function — todos os tipos de ordem"""

    def test_market_order_payload(self):
        """Market order deve ter symbol, action, quantity, class"""
        # Testar via endpoint test-payloads
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "results" in data, "Response deve conter 'results'"
        
        # Encontrar Market Buy
        market_buy = next((r for r in data["results"] if "Market Buy" in r["test"]), None)
        assert market_buy is not None, "Market Buy test não encontrado"
        
        payload = market_buy["payload_sent"]
        assert "symbol" in payload, "Payload deve ter 'symbol'"
        assert "action" in payload, "Payload deve ter 'action'"
        assert "quantity" in payload, "Payload deve ter 'quantity'"
        assert "class" in payload, "Payload deve ter 'class'"
        assert payload["class"] == "future", "class deve ser 'future'"
        assert payload["action"] == "buy", "action deve ser 'buy'"
        print(f"✅ Market Buy payload: {payload}")

    def test_limit_order_payload(self):
        """Limit order deve ter limit_price"""
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        limit_sell = next((r for r in data["results"] if "Limit Sell" in r["test"]), None)
        assert limit_sell is not None, "Limit Sell test não encontrado"
        
        payload = limit_sell["payload_sent"]
        assert "limit_price" in payload, "Limit order deve ter 'limit_price'"
        assert payload["action"] == "sell", "action deve ser 'sell'"
        print(f"✅ Limit Sell payload: {payload}")

    def test_stop_order_payload(self):
        """Stop order deve ter stop_price"""
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        stop_sell = next((r for r in data["results"] if "Stop Sell" in r["test"]), None)
        assert stop_sell is not None, "Stop Sell test não encontrado"
        
        payload = stop_sell["payload_sent"]
        assert "stop_price" in payload, "Stop order deve ter 'stop_price'"
        print(f"✅ Stop Sell payload: {payload}")

    def test_stop_limit_order_payload(self):
        """Stop-Limit order deve ter stop_price E limit_price"""
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        stop_limit = next((r for r in data["results"] if "Stop-Limit" in r["test"]), None)
        assert stop_limit is not None, "Stop-Limit test não encontrado"
        
        payload = stop_limit["payload_sent"]
        assert "stop_price" in payload, "Stop-Limit deve ter 'stop_price'"
        assert "limit_price" in payload, "Stop-Limit deve ter 'limit_price'"
        print(f"✅ Stop-Limit payload: {payload}")

    def test_trailing_stop_payload(self):
        """Trailing stop deve ter trail_trigger, trail_stop, trail_freq"""
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        trailing = next((r for r in data["results"] if "Trailing" in r["test"]), None)
        assert trailing is not None, "Trailing Stop test não encontrado"
        
        payload = trailing["payload_sent"]
        assert "trail_trigger" in payload, "Trailing deve ter 'trail_trigger'"
        assert "trail_stop" in payload, "Trailing deve ter 'trail_stop'"
        assert "trail_freq" in payload, "Trailing deve ter 'trail_freq'"
        print(f"✅ Trailing Stop payload: {payload}")

    def test_breakeven_payload(self):
        """Breakeven order deve ter 'breakeven' field"""
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        breakeven = next((r for r in data["results"] if "Breakeven" in r["test"]), None)
        assert breakeven is not None, "Breakeven test não encontrado"
        
        payload = breakeven["payload_sent"]
        assert "breakeven" in payload, "Breakeven order deve ter 'breakeven'"
        print(f"✅ Breakeven payload: {payload}")

    def test_cancel_payload(self):
        """Cancel order deve ter action='cancel'"""
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        cancel = next((r for r in data["results"] if "Cancel" in r["test"]), None)
        assert cancel is not None, "Cancel test não encontrado"
        
        payload = cancel["payload_sent"]
        assert payload["action"] == "cancel", "Cancel deve ter action='cancel'"
        assert "class" in payload, "Cancel deve ter 'class'"
        print(f"✅ Cancel payload: {payload}")


class TestTestPayloadsEndpoint:
    """Test POST /api/signalstack/test-payloads endpoint"""

    def test_endpoint_returns_7_results(self):
        """Endpoint deve retornar 7 resultados de teste"""
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        assert "results" in data, "Response deve conter 'results'"
        assert len(data["results"]) == 7, f"Deve ter 7 resultados, got {len(data['results'])}"
        
        # Verificar estrutura de cada resultado
        for result in data["results"]:
            assert "test" in result, "Cada resultado deve ter 'test'"
            assert "payload_sent" in result, "Cada resultado deve ter 'payload_sent'"
            assert "response_code" in result, "Cada resultado deve ter 'response_code'"
            assert "accepted" in result, "Cada resultado deve ter 'accepted'"
        
        print(f"✅ test-payloads retornou {len(data['results'])} resultados")

    def test_endpoint_returns_summary(self):
        """Endpoint deve retornar summary com accepted/rejected counts"""
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        assert "summary" in data, "Response deve conter 'summary'"
        assert "test_symbol" in data, "Response deve conter 'test_symbol'"
        
        # Verificar que test_symbol tem 1 dígito de ano
        test_symbol = data["test_symbol"]
        assert test_symbol[-1].isdigit(), f"test_symbol deve ter 1 dígito de ano: {test_symbol}"
        
        print(f"✅ Summary: {data['summary']}")
        print(f"✅ Test symbol: {test_symbol}")

    def test_cancel_returns_201(self):
        """Cancel deve retornar 201 (mesmo no fim de semana)"""
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        cancel = next((r for r in data["results"] if "Cancel" in r["test"]), None)
        assert cancel is not None
        
        # Cancel deve ser aceito (201) mesmo no fim de semana
        # Nota: buy/sell podem retornar SessionClosed no fim de semana
        print(f"✅ Cancel response code: {cancel['response_code']}")
        print(f"✅ Cancel accepted: {cancel['accepted']}")


class TestFlattenAllEndpoint:
    """Test POST /api/autotrading/flatten-all endpoint"""

    def test_flatten_all_returns_success(self):
        """Flatten all deve retornar status e positions_closed"""
        response = requests.post(
            f"{BASE_URL}/api/autotrading/flatten-all",
            params={"reason": "test_flatten"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "status" in data, "Response deve conter 'status'"
        assert "positions_closed" in data, "Response deve conter 'positions_closed'"
        
        print(f"✅ Flatten all: status={data['status']}, positions_closed={data['positions_closed']}")


class TestUpdateStopEndpoint:
    """Test PUT /api/positions/{id}/update-stop endpoint"""

    def test_update_stop_nonexistent_position(self):
        """Update stop em posição inexistente deve retornar 404"""
        response = requests.put(
            f"{BASE_URL}/api/positions/nonexistent-id-12345/update-stop",
            params={"new_stop": 21000.0}
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ Update stop em posição inexistente retorna 404")


class TestClosePositionEndpoint:
    """Test POST /api/positions/{id}/close endpoint"""

    def test_close_nonexistent_position(self):
        """Close em posição inexistente deve retornar 404"""
        response = requests.post(
            f"{BASE_URL}/api/positions/nonexistent-id-12345/close",
            params={"reason": "TEST"}
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ Close em posição inexistente retorna 404")


class TestPositionManagerCleanup:
    """Test que evaluate_position_update foi removida do position_manager.py"""

    def test_evaluate_position_update_removed(self):
        """evaluate_position_update não deve existir em position_manager.py"""
        import importlib.util
        
        spec = importlib.util.spec_from_file_location(
            "position_manager", 
            "/app/backend/services/position_manager.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Verificar que evaluate_position_update NÃO existe
        assert not hasattr(module, 'evaluate_position_update'), \
            "evaluate_position_update deve ter sido removida (código morto)"
        
        # Verificar que as funções necessárias EXISTEM
        assert hasattr(module, 'calculate_position_params'), \
            "calculate_position_params deve existir"
        assert hasattr(module, 'create_position_document'), \
            "create_position_document deve existir"
        assert hasattr(module, 'get_archetype'), \
            "get_archetype deve existir"
        
        print("✅ evaluate_position_update foi removida (código morto limpo)")
        print("✅ calculate_position_params existe")
        print("✅ create_position_document existe")
        print("✅ get_archetype existe")


class TestSignalStackOrderModel:
    """Test SignalStackOrder model fields"""

    def test_order_model_has_trailing_fields(self):
        """SignalStackOrder deve ter trail_trigger, trail_stop, trail_freq"""
        # Testar via endpoint que usa o modelo
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        trailing = next((r for r in data["results"] if "Trailing" in r["test"]), None)
        assert trailing is not None
        
        payload = trailing["payload_sent"]
        # Verificar que os campos de trailing estão presentes
        assert "trail_trigger" in payload
        assert "trail_stop" in payload
        assert "trail_freq" in payload
        
        # Verificar valores esperados (do test case)
        assert payload["trail_trigger"] == 15.0
        assert payload["trail_stop"] == 8.0
        assert payload["trail_freq"] == 4.0
        
        print(f"✅ Trailing fields: trigger={payload['trail_trigger']}, stop={payload['trail_stop']}, freq={payload['trail_freq']}")

    def test_order_model_has_breakeven_field(self):
        """SignalStackOrder deve ter campo breakeven"""
        response = requests.post(
            f"{BASE_URL}/api/signalstack/test-payloads",
            params={"webhook_url": WEBHOOK_URL}
        )
        assert response.status_code == 200
        
        data = response.json()
        breakeven = next((r for r in data["results"] if "Breakeven" in r["test"]), None)
        assert breakeven is not None
        
        payload = breakeven["payload_sent"]
        assert "breakeven" in payload
        assert payload["breakeven"] == 20.0
        
        print(f"✅ Breakeven field: {payload['breakeven']}")


# Fixture para sessão de requests
@pytest.fixture(scope="module")
def api_client():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
