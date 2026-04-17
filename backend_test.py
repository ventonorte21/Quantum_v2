import requests
import sys
import json
from datetime import datetime, timedelta

class TradingDashboardTester:
    def __init__(self, base_url="https://gamma-vix-predictor.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def log_test(self, name, passed, details=""):
        """Log test result"""
        self.tests_run += 1
        if passed:
            self.tests_passed += 1
            print(f"✅ {name} - PASSED")
        else:
            print(f"❌ {name} - FAILED: {details}")
        
        self.test_results.append({
            "test": name,
            "passed": passed,
            "details": details
        })

    def test_health_endpoint(self):
        """Test /api/health endpoint"""
        try:
            response = requests.get(f"{self.api_url}/health", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if "status" in data and data["status"] == "healthy":
                    self.log_test("Health Endpoint", True)
                    return True
                else:
                    self.log_test("Health Endpoint", False, f"Invalid response format: {data}")
            else:
                self.log_test("Health Endpoint", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("Health Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_symbols_endpoint(self):
        """Test /api/symbols endpoint"""
        try:
            response = requests.get(f"{self.api_url}/symbols", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if "symbols" in data:
                    symbols = data["symbols"]
                    expected_symbols = ['MNQ', 'MES', 'MYM', 'M2K']
                    
                    if all(symbol in symbols for symbol in expected_symbols):
                        self.log_test("Symbols Endpoint", True, f"Found all symbols: {list(symbols.keys())}")
                        return True
                    else:
                        missing = [s for s in expected_symbols if s not in symbols]
                        self.log_test("Symbols Endpoint", False, f"Missing symbols: {missing}")
                else:
                    self.log_test("Symbols Endpoint", False, f"No 'symbols' key in response: {data}")
            else:
                self.log_test("Symbols Endpoint", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("Symbols Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_analyze_endpoint(self):
        """Test /api/analyze endpoint"""
        try:
            payload = {
                "symbol": "MNQ",
                "timeframe": "1H"
            }
            response = requests.post(f"{self.api_url}/analyze", json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                required_keys = ["symbol", "analysis", "timestamp"]
                
                if all(key in data for key in required_keys):
                    # Check if analysis contains timeframe data
                    analysis = data.get("analysis", {})
                    if analysis and isinstance(analysis, dict):
                        # Check for confluence and confidence scores in signals
                        for tf, tf_data in analysis.items():
                            signal = tf_data.get("signal", {})
                            if "confluence_score" in signal and "confidence_score" in signal:
                                self.log_test("Analyze Endpoint", True, f"Analysis complete for {tf}")
                                return True
                        
                        self.log_test("Analyze Endpoint", False, "Missing confluence/confidence scores")
                    else:
                        self.log_test("Analyze Endpoint", False, "Empty or invalid analysis data")
                else:
                    missing = [k for k in required_keys if k not in data]
                    self.log_test("Analyze Endpoint", False, f"Missing keys: {missing}")
            else:
                self.log_test("Analyze Endpoint", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("Analyze Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_signals_endpoint(self):
        """Test /api/signals/{symbol} endpoint"""
        try:
            symbol = "MNQ"
            response = requests.get(f"{self.api_url}/signals/{symbol}", timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if "symbol" in data and "signals" in data:
                    signals = data["signals"]
                    if signals and isinstance(signals, dict):
                        # Check if signals contain timeframe data
                        for tf, signal_data in signals.items():
                            if "signal_type" in signal_data and "confluence_score" in signal_data:
                                self.log_test("Signals Endpoint", True, f"Signals retrieved for {symbol}")
                                return True
                        
                        self.log_test("Signals Endpoint", False, "Invalid signal format")
                    else:
                        self.log_test("Signals Endpoint", False, "Empty signals data")
                else:
                    self.log_test("Signals Endpoint", False, f"Missing required keys in response: {data}")
            else:
                self.log_test("Signals Endpoint", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("Signals Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_backtest_endpoint(self):
        """Test /api/backtest endpoint"""
        try:
            end_date = datetime.now().isoformat()
            start_date = (datetime.now() - timedelta(days=30)).isoformat()
            
            payload = {
                "symbol": "MNQ",
                "start_date": start_date,
                "end_date": end_date,
                "initial_capital": 100000,
                "risk_per_trade": 0.02
            }
            
            response = requests.post(f"{self.api_url}/backtest", json=payload, timeout=60)
            
            if response.status_code == 200:
                data = response.json()
                required_keys = ["total_trades", "winning_trades", "losing_trades", "win_rate", "total_return", "max_drawdown", "sharpe_ratio"]
                
                if all(key in data for key in required_keys):
                    self.log_test("Backtest Endpoint", True, f"Backtest completed: {data['total_trades']} trades, {data['win_rate']}% win rate")
                    return True
                else:
                    missing = [k for k in required_keys if k not in data]
                    self.log_test("Backtest Endpoint", False, f"Missing keys: {missing}")
            else:
                self.log_test("Backtest Endpoint", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("Backtest Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_market_data_endpoint(self):
        """Test /api/market-data endpoint"""
        try:
            payload = {
                "symbol": "MNQ",
                "timeframe": "1H"
            }
            response = requests.post(f"{self.api_url}/market-data", json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if "data" in data and "count" in data:
                    market_data = data["data"]
                    if isinstance(market_data, list) and len(market_data) > 0:
                        # Check if data has OHLCV structure
                        sample = market_data[0]
                        required_fields = ["open", "high", "low", "close", "volume"]
                        if all(field in sample for field in required_fields):
                            self.log_test("Market Data Endpoint", True, f"Retrieved {len(market_data)} data points")
                            return True
                        else:
                            missing = [f for f in required_fields if f not in sample]
                            self.log_test("Market Data Endpoint", False, f"Missing OHLCV fields: {missing}")
                    else:
                        self.log_test("Market Data Endpoint", False, "Empty or invalid market data")
                else:
                    self.log_test("Market Data Endpoint", False, f"Missing required keys: {list(data.keys())}")
            else:
                self.log_test("Market Data Endpoint", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("Market Data Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_signalstack_symbols_endpoint(self):
        """Test /api/signalstack/symbols endpoint"""
        try:
            response = requests.get(f"{self.api_url}/signalstack/symbols", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if "symbols" in data:
                    symbols = data["symbols"]
                    expected_symbols = ['MNQ', 'MES', 'MYM', 'M2K']
                    
                    if all(symbol in symbols for symbol in expected_symbols):
                        # Check if each symbol has tradovate mapping
                        for symbol in expected_symbols:
                            if "tradovate" not in symbols[symbol]:
                                self.log_test("SignalStack Symbols", False, f"Missing tradovate mapping for {symbol}")
                                return False
                        
                        self.log_test("SignalStack Symbols", True, f"All symbols have Tradovate mappings")
                        return True
                    else:
                        missing = [s for s in expected_symbols if s not in symbols]
                        self.log_test("SignalStack Symbols", False, f"Missing symbols: {missing}")
                else:
                    self.log_test("SignalStack Symbols", False, f"No 'symbols' key in response: {data}")
            else:
                self.log_test("SignalStack Symbols", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("SignalStack Symbols", False, f"Exception: {str(e)}")
        return False

    def test_signalstack_config_endpoint(self):
        """Test /api/signalstack/config GET endpoint"""
        try:
            response = requests.get(f"{self.api_url}/signalstack/config", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if "configs" in data:
                    self.log_test("SignalStack Config GET", True, f"Retrieved {len(data['configs'])} configs")
                    return True
                else:
                    self.log_test("SignalStack Config GET", False, f"No 'configs' key in response: {data}")
            else:
                self.log_test("SignalStack Config GET", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("SignalStack Config GET", False, f"Exception: {str(e)}")
        return False

    def test_signalstack_send_order_endpoint(self):
        """Test /api/signalstack/send-order endpoint"""
        try:
            # Test order payload
            payload = {
                "webhook_url": "https://app.signalstack.com/hook/a65Cvk39pE3HdZiutAi9rP",
                "order": {
                    "symbol": "MNQH25",  # Example Tradovate symbol
                    "action": "buy",
                    "quantity": 1,
                    "order_type": "market"
                }
            }
            
            response = requests.post(f"{self.api_url}/signalstack/send-order", json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                required_keys = ["id", "symbol", "action", "quantity", "status", "sent_at"]
                
                if all(key in data for key in required_keys):
                    # Order may fail due to market hours, but API should work
                    status = data.get("status")
                    if status in ["success", "error"]:
                        self.log_test("SignalStack Send Order", True, f"Order sent with status: {status}")
                        return True
                    else:
                        self.log_test("SignalStack Send Order", False, f"Invalid status: {status}")
                else:
                    missing = [k for k in required_keys if k not in data]
                    self.log_test("SignalStack Send Order", False, f"Missing keys: {missing}")
            else:
                self.log_test("SignalStack Send Order", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("SignalStack Send Order", False, f"Exception: {str(e)}")
        return False

    def test_signalstack_orders_endpoint(self):
        """Test /api/signalstack/orders endpoint"""
        try:
            response = requests.get(f"{self.api_url}/signalstack/orders?limit=10", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if "orders" in data and "count" in data:
                    orders = data["orders"]
                    if isinstance(orders, list):
                        self.log_test("SignalStack Orders", True, f"Retrieved {len(orders)} orders")
                        return True
                    else:
                        self.log_test("SignalStack Orders", False, "Orders is not a list")
                else:
                    self.log_test("SignalStack Orders", False, f"Missing required keys: {list(data.keys())}")
            else:
                self.log_test("SignalStack Orders", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("SignalStack Orders", False, f"Exception: {str(e)}")
        return False

    def test_signalstack_orders_stats_endpoint(self):
        """Test /api/signalstack/orders/stats endpoint"""
        try:
            response = requests.get(f"{self.api_url}/signalstack/orders/stats", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                required_keys = ["total_orders", "orders_last_24h", "by_status"]
                
                if all(key in data for key in required_keys):
                    self.log_test("SignalStack Orders Stats", True, f"Stats: {data['total_orders']} total orders")
                    return True
                else:
                    missing = [k for k in required_keys if k not in data]
                    self.log_test("SignalStack Orders Stats", False, f"Missing keys: {missing}")
            else:
                self.log_test("SignalStack Orders Stats", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("SignalStack Orders Stats", False, f"Exception: {str(e)}")
        return False

    def test_autotrading_config_get_endpoint(self):
        """Test /api/autotrading/config GET endpoint"""
        try:
            response = requests.get(f"{self.api_url}/autotrading/config", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if "config" in data:
                    config = data["config"]
                    required_keys = ["enabled", "paper_trading", "symbols", "min_confluence_score", "min_confidence_score"]
                    
                    if all(key in config for key in required_keys):
                        self.log_test("Auto Trading Config GET", True, f"Config retrieved with {len(config['symbols'])} symbols")
                        return True
                    else:
                        missing = [k for k in required_keys if k not in config]
                        self.log_test("Auto Trading Config GET", False, f"Missing config keys: {missing}")
                else:
                    self.log_test("Auto Trading Config GET", False, f"No 'config' key in response: {data}")
            else:
                self.log_test("Auto Trading Config GET", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("Auto Trading Config GET", False, f"Exception: {str(e)}")
        return False

    def test_autotrading_config_post_endpoint(self):
        """Test /api/autotrading/config POST endpoint"""
        try:
            # Test configuration payload
            payload = {
                "enabled": False,
                "paper_trading": True,
                "symbols": ["MNQ", "MES"],
                "min_confluence_score": 70.0,
                "min_confidence_score": 60.0,
                "require_mtf_alignment": True,
                "max_vix": 30.0,
                "min_vix": 10.0,
                "min_volume_ratio": 0.8,
                "default_quantity": 1,
                "max_daily_trades": 10,
                "webhook_url": "https://app.signalstack.com/hook/a65Cvk39pE3HdZiutAi9rP"
            }
            
            response = requests.post(f"{self.api_url}/autotrading/config", json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if "status" in data and data["status"] == "success":
                    self.log_test("Auto Trading Config POST", True, "Configuration saved successfully")
                    return True
                else:
                    self.log_test("Auto Trading Config POST", False, f"Invalid response: {data}")
            else:
                self.log_test("Auto Trading Config POST", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("Auto Trading Config POST", False, f"Exception: {str(e)}")
        return False

    def test_autotrading_evaluate_endpoint(self):
        """Test /api/autotrading/evaluate endpoint"""
        try:
            symbol = "MNQ"
            response = requests.post(f"{self.api_url}/autotrading/evaluate?symbol={symbol}", timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                required_keys = ["symbol", "signal", "conditions", "config_enabled", "paper_trading"]
                
                if all(key in data for key in required_keys):
                    signal = data.get("signal", {})
                    conditions = data.get("conditions", {})
                    
                    # Check if signal has required fields
                    signal_keys = ["action", "confluence_score", "confidence_score", "all_conditions_met"]
                    if all(key in signal for key in signal_keys):
                        self.log_test("Auto Trading Evaluate", True, f"Evaluation complete for {symbol}: {signal['action']}")
                        return True
                    else:
                        missing = [k for k in signal_keys if k not in signal]
                        self.log_test("Auto Trading Evaluate", False, f"Missing signal keys: {missing}")
                else:
                    missing = [k for k in required_keys if k not in data]
                    self.log_test("Auto Trading Evaluate", False, f"Missing keys: {missing}")
            else:
                self.log_test("Auto Trading Evaluate", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("Auto Trading Evaluate", False, f"Exception: {str(e)}")
        return False

    def test_autotrading_state_endpoint(self):
        """Test /api/autotrading/state endpoint"""
        try:
            response = requests.get(f"{self.api_url}/autotrading/state", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if "state" in data:
                    state = data["state"]
                    required_keys = ["is_running", "open_positions", "daily_trades", "daily_pnl"]
                    
                    if all(key in state for key in required_keys):
                        self.log_test("Auto Trading State", True, f"State retrieved: {state['daily_trades']} daily trades")
                        return True
                    else:
                        missing = [k for k in required_keys if k not in state]
                        self.log_test("Auto Trading State", False, f"Missing state keys: {missing}")
                else:
                    self.log_test("Auto Trading State", False, f"No 'state' key in response: {data}")
            else:
                self.log_test("Auto Trading State", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("Auto Trading State", False, f"Exception: {str(e)}")
        return False

    def test_autotrading_signals_endpoint(self):
        """Test /api/autotrading/signals endpoint"""
        try:
            response = requests.get(f"{self.api_url}/autotrading/signals?limit=10", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if "signals" in data and "count" in data:
                    signals = data["signals"]
                    if isinstance(signals, list):
                        self.log_test("Auto Trading Signals", True, f"Retrieved {len(signals)} signals")
                        return True
                    else:
                        self.log_test("Auto Trading Signals", False, "Signals is not a list")
                else:
                    self.log_test("Auto Trading Signals", False, f"Missing required keys: {list(data.keys())}")
            else:
                self.log_test("Auto Trading Signals", False, f"Status code: {response.status_code}")
        except Exception as e:
            self.log_test("Auto Trading Signals", False, f"Exception: {str(e)}")
        return False

    def test_vix_endpoint(self):
        """Test /api/vix endpoint for real VIX data"""
        try:
            response = requests.get(f"{self.api_url}/vix", timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if "vix" in data:
                    vix_data = data["vix"]
                    required_keys = ["value", "regime", "source", "timestamp"]
                    
                    if all(key in vix_data for key in required_keys):
                        # Check if source is yahoo_finance
                        if vix_data["source"] == "yahoo_finance":
                            # Check if value is a reasonable VIX value
                            vix_value = vix_data["value"]
                            if isinstance(vix_value, (int, float)) and 5 <= vix_value <= 100:
                                # Check if regime is valid
                                valid_regimes = ["LOW_VOL", "NORMAL", "ELEVATED", "HIGH_VOL", "EXTREME_FEAR"]
                                if vix_data["regime"] in valid_regimes:
                                    self.log_test("VIX Endpoint", True, f"VIX: {vix_value}, Regime: {vix_data['regime']}, Source: {vix_data['source']}")
                                    return True
                                else:
                                    self.log_test("VIX Endpoint", False, f"Invalid regime: {vix_data['regime']}")
                            else:
                                self.log_test("VIX Endpoint", False, f"Invalid VIX value: {vix_value}")
                        else:
                            self.log_test("VIX Endpoint", False, f"Expected yahoo_finance source, got: {vix_data['source']}")
                    else:
                        missing = [k for k in required_keys if k not in vix_data]
                        self.log_test("VIX Endpoint", False, f"Missing VIX keys: {missing}")
                else:
                    self.log_test("VIX Endpoint", False, f"No 'vix' key in response: {data}")
            else:
                self.log_test("VIX Endpoint", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("VIX Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_gamma_endpoint(self):
        """Test /api/gamma/{symbol} endpoint for real GAMMA data"""
        try:
            symbol = "MNQ"
            response = requests.get(f"{self.api_url}/gamma/{symbol}", timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if "symbol" in data and "gamma" in data:
                    gamma_data = data["gamma"]
                    required_keys = ["net_gex", "call_gex", "put_gex", "key_levels"]
                    
                    if all(key in gamma_data for key in required_keys):
                        # Check if GEX values are numeric
                        net_gex = gamma_data["net_gex"]
                        call_gex = gamma_data["call_gex"]
                        put_gex = gamma_data["put_gex"]
                        
                        if all(isinstance(val, (int, float)) for val in [net_gex, call_gex, put_gex]):
                            # Check if key_levels is a dict
                            key_levels = gamma_data["key_levels"]
                            if isinstance(key_levels, dict) and len(key_levels) > 0:
                                # Check if has source information
                                source = gamma_data.get("source", "unknown")
                                self.log_test("GAMMA Endpoint", True, f"Symbol: {symbol}, Net GEX: {net_gex}, Source: {source}")
                                return True
                            else:
                                self.log_test("GAMMA Endpoint", False, "Invalid or empty key_levels")
                        else:
                            self.log_test("GAMMA Endpoint", False, "Non-numeric GEX values")
                    else:
                        missing = [k for k in required_keys if k not in gamma_data]
                        self.log_test("GAMMA Endpoint", False, f"Missing GAMMA keys: {missing}")
                else:
                    self.log_test("GAMMA Endpoint", False, f"Missing symbol or gamma in response: {list(data.keys())}")
            else:
                self.log_test("GAMMA Endpoint", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("GAMMA Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_vxn_endpoint(self):
        """Test /api/vxn endpoint for real VXN data"""
        try:
            response = requests.get(f"{self.api_url}/vxn", timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if "vxn" in data:
                    vxn_data = data["vxn"]
                    required_keys = ["value", "regime", "index", "source", "timestamp"]
                    
                    if all(key in vxn_data for key in required_keys):
                        # Check if source is yahoo_finance
                        if vxn_data["source"] == "yahoo_finance":
                            # Check if index is NASDAQ-100
                            if vxn_data["index"] == "NASDAQ-100":
                                # Check if value is a reasonable VXN value
                                vxn_value = vxn_data["value"]
                                if isinstance(vxn_value, (int, float)) and 5 <= vxn_value <= 150:
                                    # Check if regime is valid
                                    valid_regimes = ["LOW_VOL", "NORMAL", "ELEVATED", "HIGH_VOL", "EXTREME_FEAR"]
                                    if vxn_data["regime"] in valid_regimes:
                                        self.log_test("VXN Endpoint", True, f"VXN: {vxn_value}, Regime: {vxn_data['regime']}, Index: {vxn_data['index']}, Source: {vxn_data['source']}")
                                        return True
                                    else:
                                        self.log_test("VXN Endpoint", False, f"Invalid regime: {vxn_data['regime']}")
                                else:
                                    self.log_test("VXN Endpoint", False, f"Invalid VXN value: {vxn_value}")
                            else:
                                self.log_test("VXN Endpoint", False, f"Expected NASDAQ-100 index, got: {vxn_data['index']}")
                        else:
                            self.log_test("VXN Endpoint", False, f"Expected yahoo_finance source, got: {vxn_data['source']}")
                    else:
                        missing = [k for k in required_keys if k not in vxn_data]
                        self.log_test("VXN Endpoint", False, f"Missing VXN keys: {missing}")
                else:
                    self.log_test("VXN Endpoint", False, f"No 'vxn' key in response: {data}")
            else:
                self.log_test("VXN Endpoint", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("VXN Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_volatility_endpoint(self):
        """Test /api/volatility/{symbol} endpoint for combined VIX, VXN and GAMMA data"""
        try:
            symbol = "MNQ"
            response = requests.get(f"{self.api_url}/volatility/{symbol}", timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                required_keys = ["symbol", "vix", "vxn", "gamma", "timestamp"]
                
                if all(key in data for key in required_keys):
                    vix_data = data["vix"]
                    vxn_data = data["vxn"]
                    gamma_data = data["gamma"]
                    
                    # Check VIX data structure
                    vix_keys = ["value", "regime", "source"]
                    if all(key in vix_data for key in vix_keys):
                        # Check VXN data structure
                        vxn_keys = ["value", "regime", "index", "source"]
                        if all(key in vxn_data for key in vxn_keys):
                            # Check GAMMA data structure
                            gamma_keys = ["net_gex", "call_gex", "put_gex", "key_levels"]
                            if all(key in gamma_data for key in gamma_keys):
                                self.log_test("Volatility Endpoint", True, f"Combined data for {symbol}: VIX={vix_data['value']}, VXN={vxn_data['value']}, Net GEX={gamma_data['net_gex']}")
                                return True
                            else:
                                missing = [k for k in gamma_keys if k not in gamma_data]
                                self.log_test("Volatility Endpoint", False, f"Missing GAMMA keys: {missing}")
                        else:
                            missing = [k for k in vxn_keys if k not in vxn_data]
                            self.log_test("Volatility Endpoint", False, f"Missing VXN keys: {missing}")
                    else:
                        missing = [k for k in vix_keys if k not in vix_data]
                        self.log_test("Volatility Endpoint", False, f"Missing VIX keys: {missing}")
                else:
                    missing = [k for k in required_keys if k not in data]
                    self.log_test("Volatility Endpoint", False, f"Missing keys: {missing}")
            else:
                self.log_test("Volatility Endpoint", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("Volatility Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_term_structure_endpoint(self):
        """Test /api/term-structure endpoint for VIX/VIX3M term structure data"""
        try:
            response = requests.get(f"{self.api_url}/term-structure", timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if "term_structure" in data:
                    term_structure = data["term_structure"]
                    required_keys = ["vix", "vix3m", "ratio", "state", "signal", "market_implication"]
                    
                    if all(key in term_structure for key in required_keys):
                        vix = term_structure["vix"]
                        vix3m = term_structure["vix3m"]
                        ratio = term_structure["ratio"]
                        state = term_structure["state"]
                        signal = term_structure["signal"]
                        
                        # Check if values are numeric and reasonable
                        if isinstance(vix, (int, float)) and isinstance(vix3m, (int, float)) and isinstance(ratio, (int, float)):
                            if 5 <= vix <= 100 and 5 <= vix3m <= 100 and 0.5 <= ratio <= 2.0:
                                # Check if state is valid
                                valid_states = ["STEEP_CONTANGO", "CONTANGO", "FLAT", "BACKWARDATION", "STRONG_BACKWARDATION"]
                                if state in valid_states:
                                    # Check if signal is valid
                                    valid_signals = ["CONFIRM", "CAUTION", "EXTREME_CAUTION", "NEUTRAL"]
                                    if signal in valid_signals:
                                        # Verify state logic
                                        if ratio < 0.85 and state == "STEEP_CONTANGO":
                                            state_correct = True
                                        elif 0.85 <= ratio < 0.95 and state == "CONTANGO":
                                            state_correct = True
                                        elif 0.95 <= ratio < 1.0 and state == "FLAT":
                                            state_correct = True
                                        elif 1.0 <= ratio < 1.1 and state == "BACKWARDATION":
                                            state_correct = True
                                        elif ratio >= 1.1 and state == "STRONG_BACKWARDATION":
                                            state_correct = True
                                        else:
                                            state_correct = False
                                        
                                        if state_correct:
                                            self.log_test("Term Structure Endpoint", True, f"VIX: {vix}, VIX3M: {vix3m}, Ratio: {ratio:.3f}, State: {state}, Signal: {signal}")
                                            return True
                                        else:
                                            self.log_test("Term Structure Endpoint", False, f"State logic incorrect: ratio={ratio:.3f} should not be {state}")
                                    else:
                                        self.log_test("Term Structure Endpoint", False, f"Invalid signal: {signal}")
                                else:
                                    self.log_test("Term Structure Endpoint", False, f"Invalid state: {state}")
                            else:
                                self.log_test("Term Structure Endpoint", False, f"Invalid values: VIX={vix}, VIX3M={vix3m}, Ratio={ratio}")
                        else:
                            self.log_test("Term Structure Endpoint", False, "Non-numeric VIX/VIX3M/ratio values")
                    else:
                        missing = [k for k in required_keys if k not in term_structure]
                        self.log_test("Term Structure Endpoint", False, f"Missing term structure keys: {missing}")
                else:
                    self.log_test("Term Structure Endpoint", False, f"No 'term_structure' key in response: {data}")
            else:
                self.log_test("Term Structure Endpoint", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("Term Structure Endpoint", False, f"Exception: {str(e)}")
        return False

    def test_analyze_with_real_vix_vxn_gamma(self):
        """Test /api/analyze endpoint includes real_vix, real_vxn and real_gamma in response"""
        try:
            payload = {
                "symbol": "MNQ",
                "timeframe": "1H"
            }
            response = requests.post(f"{self.api_url}/analyze", json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                required_keys = ["symbol", "analysis", "real_vix", "real_vxn", "real_gamma", "term_structure", "timestamp"]
                
                if all(key in data for key in required_keys):
                    real_vix = data["real_vix"]
                    real_vxn = data["real_vxn"]
                    real_gamma = data["real_gamma"]
                    term_structure = data["term_structure"]
                    
                    # Check real_vix structure
                    vix_keys = ["value", "regime", "source"]
                    if all(key in vix_keys for key in vix_keys):
                        # Check real_vxn structure
                        vxn_keys = ["value", "regime", "index", "source"]
                        if all(key in real_vxn for key in vxn_keys):
                            # Check real_gamma structure
                            gamma_keys = ["net_gex", "call_gex", "put_gex", "key_levels"]
                            if all(key in real_gamma for key in gamma_keys):
                                # Check term_structure structure
                                ts_keys = ["vix", "vix3m", "ratio", "state", "signal", "market_implication"]
                                if all(key in term_structure for key in ts_keys):
                                    # Verify sources and index
                                    if real_vix["source"] == "yahoo_finance" and real_vxn["source"] == "yahoo_finance":
                                        if real_vxn["index"] == "NASDAQ-100":
                                            self.log_test("Analyze with Real VIX/VXN/GAMMA/TermStructure", True, f"Analysis includes real VIX={real_vix['value']}, VXN={real_vxn['value']} (NASDAQ-100), GAMMA={real_gamma['net_gex']}, and Term Structure={term_structure['state']}")
                                            return True
                                        else:
                                            self.log_test("Analyze with Real VIX/VXN/GAMMA/TermStructure", False, f"VXN index not NASDAQ-100: {real_vxn['index']}")
                                    else:
                                        self.log_test("Analyze with Real VIX/VXN/GAMMA/TermStructure", False, f"Sources not yahoo_finance: VIX={real_vix['source']}, VXN={real_vxn['source']}")
                                else:
                                    missing = [k for k in ts_keys if k not in term_structure]
                                    self.log_test("Analyze with Real VIX/VXN/GAMMA/TermStructure", False, f"Missing term_structure keys: {missing}")
                            else:
                                missing = [k for k in gamma_keys if k not in real_gamma]
                                self.log_test("Analyze with Real VIX/VXN/GAMMA/TermStructure", False, f"Missing real_gamma keys: {missing}")
                        else:
                            missing = [k for k in vxn_keys if k not in real_vxn]
                            self.log_test("Analyze with Real VIX/VXN/GAMMA/TermStructure", False, f"Missing real_vxn keys: {missing}")
                    else:
                        missing = [k for k in vix_keys if k not in real_vix]
                        self.log_test("Analyze with Real VIX/VXN/GAMMA/TermStructure", False, f"Missing real_vix keys: {missing}")
                else:
                    missing = [k for k in required_keys if k not in data]
                    self.log_test("Analyze with Real VIX/VXN/GAMMA/TermStructure", False, f"Missing keys: {missing}")
            else:
                self.log_test("Analyze with Real VIX/VXN/GAMMA/TermStructure", False, f"Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            self.log_test("Analyze with Real VIX/VXN/GAMMA/TermStructure", False, f"Exception: {str(e)}")
        return False

    def run_all_tests(self):
        """Run all backend tests"""
        print("🚀 Starting Trading Dashboard Backend Tests")
        print(f"📡 Testing API at: {self.api_url}")
        print("=" * 60)
        
        # Test core endpoints
        self.test_health_endpoint()
        self.test_symbols_endpoint()
        self.test_market_data_endpoint()
        self.test_analyze_endpoint()
        self.test_signals_endpoint()
        self.test_backtest_endpoint()
        
        # Test NEW VIX, VXN and GAMMA endpoints
        print("\n📊 Testing Real VIX, VXN & GAMMA Integration:")
        self.test_vix_endpoint()
        self.test_vxn_endpoint()
        self.test_gamma_endpoint()
        self.test_volatility_endpoint()
        self.test_term_structure_endpoint()
        self.test_analyze_with_real_vix_vxn_gamma()
        
        # Test SignalStack endpoints
        print("\n🔗 Testing SignalStack Integration:")
        self.test_signalstack_symbols_endpoint()
        self.test_signalstack_config_endpoint()
        self.test_signalstack_send_order_endpoint()
        self.test_signalstack_orders_endpoint()
        self.test_signalstack_orders_stats_endpoint()
        
        # Test Auto Trading endpoints
        print("\n⚡ Testing Auto Trading Integration:")
        self.test_autotrading_config_get_endpoint()
        self.test_autotrading_config_post_endpoint()
        self.test_autotrading_evaluate_endpoint()
        self.test_autotrading_state_endpoint()
        self.test_autotrading_signals_endpoint()
        
        print("=" * 60)
        print(f"📊 Test Results: {self.tests_passed}/{self.tests_run} tests passed")
        
        if self.tests_passed == self.tests_run:
            print("🎉 All backend tests PASSED!")
            return True
        else:
            print("⚠️  Some backend tests FAILED!")
            failed_tests = [r for r in self.test_results if not r["passed"]]
            print("\nFailed Tests:")
            for test in failed_tests:
                print(f"  - {test['test']}: {test['details']}")
            return False

def main():
    tester = TradingDashboardTester()
    success = tester.run_all_tests()
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())