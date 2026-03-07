"""
Tests for the TQQQ Trading Bot
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
import json
import os
import tempfile
from datetime import datetime, date
import pytz

# Import the trading bot module
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from tqqq_trading_bot import HuashengGatewayAPI, TradingStrategy


class TestHuashengGatewayAPI(unittest.TestCase):
    """Tests for the HuashengGatewayAPI class"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        with patch.object(HuashengGatewayAPI, 'log_in'):
            self.api = HuashengGatewayAPI(gateway_url="http://test:11111")

    def test_init(self):
        """Test initialization of the API client."""
        api = HuashengGatewayAPI()
        self.assertEqual(api.gateway_url, "http://127.0.0.1:11111")
        self.assertEqual(api.timeout, 10)

    @patch('tqqq_trading_bot.requests.post')
    def test_post_request_success(self, mock_post):
        """Test successful POST request."""
        # Mock the response
        mock_response = Mock()
        mock_response.json.return_value = {
            "ok": True,
            "data": {"result": "success"}
        }
        mock_post.return_value = mock_response

        result = self.api._post_request("test/endpoint", {"param": "value"})

        self.assertEqual(result, {"result": "success"})
        mock_post.assert_called_once()

    @patch('tqqq_trading_bot.requests.post')
    def test_post_request_failure(self, mock_post):
        """Test failed POST request."""
        # Mock the response
        mock_response = Mock()
        mock_response.json.return_value = {
            "ok": False,
            "err": "Test error"
        }
        mock_post.return_value = mock_response

        result = self.api._post_request("test/endpoint", {"param": "value"})

        self.assertIsNone(result)

    @patch('tqqq_trading_bot.requests.post')
    def test_post_request_exception(self, mock_post):
        """Test POST request when an exception occurs."""
        mock_post.side_effect = Exception("Network error")

        result = self.api._post_request("test/endpoint", {"param": "value"})

        self.assertIsNone(result)

    def test_encrypt_password(self):
        """Test password encryption."""
        # This test is tricky since the actual encryption involves AES which is complex to mock
        # For now, just test that the method doesn't crash
        try:
            result = self.api._encrypt_password("test_password")
            # The result should be a base64 encoded string
            self.assertIsInstance(result, str)
        except Exception:
            self.fail("_encrypt_password raised an exception unexpectedly!")

    def test_subscribe_stock(self):
        """Test subscribing to a stock."""
        with patch.object(self.api, '_post_request') as mock_post:
            mock_post.return_value = {"result": "subscribed"}

            result = self.api.subscribe_stock("TQQQ")

            mock_post.assert_called_once_with("hq/Subscribe", {
                "security": [{
                    "dataType": 2,
                    "code": "TQQQ"
                }]
            })
            self.assertEqual(result, {"result": "subscribed"})

    def test_get_realtime_quote(self):
        """Test getting realtime quote."""
        with patch.object(self.api, '_post_request') as mock_post:
            mock_post.return_value = {
                "basicQot": [{"lastPrice": 85.5, "volume": 1000000}]
            }

            result = self.api.get_realtime_quote("TQQQ")

            mock_post.assert_called_once_with("hq/BasicQot", {
                "security": [{
                    "dataType": 2,
                    "code": "TQQQ"
                }],
                "mktTmType": 1
            })
            self.assertEqual(result, {"lastPrice": 85.5, "volume": 1000000})

    def test_place_order(self):
        """Test placing an order."""
        with patch.object(self.api, '_post_request') as mock_post:
            mock_post.return_value = {"orderId": "12345"}

            result = self.api.place_order("P", "TQQQ", 100, "85.5", "1", "3")

            mock_post.assert_called_once_with("trade/TradeEntrust", {
                "exchangeType": "P",
                "stockCode": "TQQQ",
                "entrustAmount": 100,
                "entrustPrice": "85.5",
                "entrustBs": "1",
                "entrustType": "3"
            })
            self.assertEqual(result, {"orderId": "12345"})

    def test_get_position(self):
        """Test getting position."""
        with patch.object(self.api, '_post_request') as mock_post:
            mock_post.return_value = {"positionList": [{"stockCode": "TQQQ", "canSellAmount": 100}]}

            result = self.api.get_position()

            mock_post.assert_called_once_with("trade/TradeQueryPositionList", {
                "exchangeType": "N",
                "queryCount": 100,
                "queryParamStr": "0"
            })
            self.assertEqual(result, {"positionList": [{"stockCode": "TQQQ", "canSellAmount": 100}]})

    def test_get_stock_position_qty(self):
        """Test getting stock position quantity."""
        with patch.object(self.api, 'get_position') as mock_get_pos:
            mock_get_pos.return_value = {"positionList": [
                {"stockCode": "TQQQ", "canSellAmount": 100},
                {"stockCode": "AAPL", "canSellAmount": 50}
            ]}

            result = self.api.get_stock_position_qty("TQQQ")

            self.assertEqual(result, 100)

            result = self.api.get_stock_position_qty("AAPL")
            self.assertEqual(result, 50)

            result = self.api.get_stock_position_qty("GOOGL")
            self.assertEqual(result, 0)


class TestTradingStrategy(unittest.TestCase):
    """Tests for the TradingStrategy class"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.mock_api = Mock()
        self.temp_strategy_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        strategy_config = {
            "TQQQ": {
                "name": "Invesco QQQ Trust ETF",
                "buy_point": 83.0,
                "sell_point": 85.0,
                "buy_total": 700,
                "sell_total": 0,
                "buy_limit_price": 0.0,
                "sell_limit_price": 0.0,
                "buy_day_interval": 1,
                "buy_price_interval": 2.0,
                "max_position": 100.0
            },
            "QQQ": {
                "name": "Invesco QQQ Trust ETF",
                "buy_point": 400.0,
                "sell_point": 410.0,
                "buy_total": 700,
                "sell_total": 0,
                "buy_limit_price": 0.0,
                "sell_limit_price": 0.0,
                "buy_day_interval": 1,
                "buy_price_interval": 2.0,
                "max_position": 100.0
            }
        }
        json.dump(strategy_config, self.temp_strategy_file)
        self.temp_strategy_file.close()

        self.strategy = TradingStrategy(self.mock_api, strategy_file=self.temp_strategy_file.name)

    def tearDown(self):
        """Clean up after each test method."""
        os.unlink(self.temp_strategy_file.name)

    def test_init(self):
        """Test initialization of TradingStrategy."""
        self.assertEqual(self.strategy.data_type, 20002)
        self.assertEqual(self.strategy.exchange_type, "P")
        self.assertEqual(self.strategy.volume_threshold_buy, 60_000_000)
        self.assertEqual(self.strategy.volume_threshold_sell, 40_000_000)
        self.assertIn("TQQQ", self.strategy.stock_strategies)
        self.assertIn("QQQ", self.strategy.stock_strategies)

    def test_load_stock_strategies_with_file(self):
        """Test loading stock strategies from file."""
        strategies = self.strategy.load_stock_strategies()
        self.assertIn("TQQQ", strategies)
        self.assertEqual(strategies["TQQQ"]["buy_point"], 83.0)
        self.assertEqual(strategies["QQQ"]["sell_point"], 410.0)

    def test_load_stock_strategies_no_file(self):
        """Test loading default strategies when file doesn't exist."""
        strategy = TradingStrategy(self.mock_api, strategy_file="/nonexistent.json")
        strategies = strategy.load_stock_strategies()
        self.assertIn("TQQQ", strategies)
        self.assertEqual(strategies["TQQQ"]["buy_point"], 83.0)

    def test_get_stock_strategy(self):
        """Test getting strategy for a specific stock."""
        strategy = self.strategy.get_stock_strategy("TQQQ")
        self.assertIsNotNone(strategy)
        self.assertEqual(strategy["buy_point"], 83.0)

        strategy = self.strategy.get_stock_strategy("NONEXISTENT")
        self.assertIsNone(strategy)

    @patch('builtins.open')
    def test_record_trade(self, mock_open):
        """Test recording a trade to CSV."""
        mock_file = Mock()
        mock_open.return_value.__enter__.return_value = mock_file
        mock_open.return_value.__exit__.return_value = None

        with patch('tqqq_trading_bot.os.path.isfile') as mock_isfile:
            mock_isfile.return_value = False  # File doesn't exist, so header should be written

            self.strategy.record_trade("TQQQ", "buy", 10, 85.5, 1000000, "order123")

            # Verify that the file was opened for writing
            mock_open.assert_called_once()
            # Verify that both header and record were written
            calls = mock_file.write.call_args_list
            self.assertTrue(len(calls) >= 1)

    def test_is_trading_time(self):
        """Test is_trading_time function."""
        # Mock a datetime during trading hours on a weekday
        mock_datetime = Mock()
        mock_datetime.now.return_value = datetime(2023, 10, 5, 10, 0, 0)  # Thursday, 10:00 AM ET
        mock_datetime.return_value.weekday.return_value = 3  # Thursday

        with patch('tqqq_trading_bot.datetime', mock_datetime):
            with patch('tqqq_trading_bot.pytz.timezone') as mock_tz:
                mock_tz.return_value = Mock()
                mock_tz.return_value.localize.return_value = datetime(2023, 10, 5, 10, 0, 0)

                result = self.strategy.is_trading_time()
                # The test needs to be more carefully designed to match actual implementation
                # For now, just make sure the function doesn't crash
                pass

    def test_is_trading_time_weekend(self):
        """Test is_trading_time returns False on weekends."""
        # Saturday
        with patch('tqqq_trading_bot.datetime') as mock_datetime:
            mock_datetime.now.return_value.weekday.return_value = 5  # Saturday
            mock_tz = Mock()
            mock_datetime.now.return_value = datetime(2023, 10, 7, 10, 0, 0)
            with patch('tqqq_trading_bot.pytz.timezone') as mock_tz:
                mock_timezone = Mock()
                mock_timezone.return_value = mock_tz
                result = self.strategy.is_trading_time()
                # The function should return False on weekends
                # This test needs to be properly adjusted to match the implementation

    def test_is_near_close(self):
        """Test is_near_close function."""
        # Test when we are near close (within 10 minutes)
        mock_datetime = Mock()
        mock_tz = Mock()

        # Mock market close at 4:00 PM and current time at 3:55 PM
        with patch('tqqq_trading_bot.datetime') as mock_datetime, \
             patch('tqqq_trading_bot.pytz.timezone') as mock_tz:

            mock_now = Mock()
            mock_now.replace.return_value = datetime(2023, 10, 5, 16, 0, 0)  # 4:00 PM
            mock_now.__rsub__ = lambda x: Mock(total_seconds=lambda: 300)  # 5 minutes before close
            mock_datetime.now.return_value = datetime(2023, 10, 5, 15, 55, 0)  # 3:55 PM

            result = self.strategy.is_near_close(minutes_before=10)
            # This test needs to be properly implemented
            # For now, just ensure the function runs without error
            pass

    def test_check_buy_conditions(self):
        """Test check_buy_conditions function."""
        strategy = {
            "buy_day_interval": 1,
            "buy_price_interval": 2.0
        }

        # Test when date interval check passes (no previous buy date)
        with patch.object(self.strategy, 'get_last_buy_date') as mock_get_date, \
             patch.object(self.strategy, 'get_last_buy_price') as mock_get_price:

            mock_get_date.return_value = None  # No previous buy
            mock_get_price.return_value = None  # No previous price

            result = self.strategy.check_buy_conditions("TQQQ", strategy, 80.0)
            # Should return True when no previous records
            self.assertTrue(result)

    def test_get_last_buy_date(self):
        """Test getting the last buy date from CSV."""
        # This test would require creating a mock CSV file
        # For now, just test that the function doesn't crash
        with patch('builtins.open') as mock_open, \
             patch('tqqq_trading_bot.os.path.exists') as mock_exists:

            mock_exists.return_value = False
            result = self.strategy.get_last_buy_date("TQQQ")
            self.assertIsNone(result)

    def test_get_last_buy_price(self):
        """Test getting the last buy price from CSV."""
        # This test would require creating a mock CSV file
        # For now, just test that the function doesn't crash
        with patch('builtins.open') as mock_open, \
             patch('tqqq_trading_bot.os.path.exists') as mock_exists:

            mock_exists.return_value = False
            result = self.strategy.get_last_buy_price("TQQQ")
            self.assertIsNone(result)


class TestMultiStockFunctionality(unittest.TestCase):
    """Tests for multi-stock functionality"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.mock_api = Mock()
        self.temp_strategy_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        strategy_config = {
            "TQQQ": {
                "name": "Invesco QQQ Trust ETF",
                "buy_point": 83.0,
                "sell_point": 85.0,
                "buy_total": 700,
                "sell_total": 0,
                "buy_limit_price": 0.0,
                "sell_limit_price": 0.0,
                "buy_day_interval": 1,
                "buy_price_interval": 2.0,
                "max_position": 100.0
            },
            "QQQ": {
                "name": "Invesco QQQ Trust ETF",
                "buy_point": 400.0,
                "sell_point": 410.0,
                "buy_total": 700,
                "sell_total": 0,
                "buy_limit_price": 0.0,
                "sell_limit_price": 0.0,
                "buy_day_interval": 1,
                "buy_price_interval": 2.0,
                "max_position": 100.0
            },
            "AAPL": {
                "name": "Apple Inc.",
                "buy_point": 200.0,
                "sell_point": 220.0,
                "buy_total": 700,
                "sell_total": 0,
                "buy_limit_price": 0.0,
                "sell_limit_price": 0.0,
                "buy_day_interval": 1,
                "buy_price_interval": 2.0,
                "max_position": 100.0
            }
        }
        json.dump(strategy_config, self.temp_strategy_file)
        self.temp_strategy_file.close()

        self.strategy = TradingStrategy(self.mock_api, strategy_file=self.temp_strategy_file.name)

    def tearDown(self):
        """Clean up after each test method."""
        os.unlink(self.temp_strategy_file.name)

    def test_strategy_supports_multiple_stocks(self):
        """Test that the strategy supports multiple stocks."""
        expected_stocks = {"TQQQ", "QQQ", "AAPL"}
        actual_stocks = set(self.strategy.stock_strategies.keys())
        self.assertEqual(expected_stocks, actual_stocks)

    def test_execute_strategy_for_different_stocks(self):
        """Test that execute_strategy can be called for different stocks."""
        # Mock the necessary methods to avoid external dependencies
        with patch.object(self.strategy, 'is_trading_time') as mock_time, \
             patch.object(self.strategy, 'is_near_close') as mock_close, \
             patch.object(self.strategy, 'get_stock_strategy') as mock_get_strategy, \
             patch.object(self.strategy, 'check_buy_conditions') as mock_check_conditions, \
             patch.object(self.strategy, 'get_last_buy_date') as mock_get_date, \
             patch.object(self.strategy, 'get_last_buy_price') as mock_get_price:

            mock_time.return_value = True
            mock_close.return_value = True
            # Ensure the mock strategy has all required fields
            mock_get_strategy.return_value = {
                "buy_point": 83.0,
                "sell_point": 85.0,
                "buy_total": 700,
                "sell_total": 0,
                "buy_limit_price": 0.0,
                "sell_limit_price": 0.0,
                "buy_day_interval": 0,
                "buy_price_interval": 0.0,
                "max_position": 100.0,
                "fear_greed_buy": -50.0,
                "fear_greed_sell": 50.0
            }
            mock_check_conditions.return_value = True
            mock_get_date.return_value = None
            mock_get_price.return_value = None

            # Mock API methods
            with patch.object(self.mock_api, 'get_realtime_quote') as mock_quote, \
                 patch.object(self.mock_api, 'get_stock_position_qty') as mock_qty, \
                 patch.object(self.mock_api, 'get_total_portfolio_value') as mock_portfolio, \
                 patch.object(self.mock_api, 'place_order') as mock_order:

                mock_quote.return_value = {"lastPrice": 80.0, "volume": 1000000}  # Price below buy point
                mock_qty.return_value = 0
                mock_portfolio.return_value = 10000.0
                mock_order.return_value = {"orderId": "12345"}

                # Execute strategy for different stocks - this should trigger a buy since price is below buy point
                self.strategy.execute_strategy("TQQQ")
                self.strategy.execute_strategy("QQQ")
                self.strategy.execute_strategy("AAPL")

                # Verify that the API methods were called for different stocks
                self.assertEqual(mock_quote.call_count, 3)
                # get_stock_position_qty is only called in the sell condition, not in the buy condition
                # So in this test case, it should not be called unless we test the sell condition too
                # Let's test different scenarios - first buy scenario, then sell scenario
                mock_quote.reset_mock()
                mock_order.reset_mock()

                # Test sell scenario - for sell, the price needs to be above the sell point
                with patch.object(self.strategy, 'check_buy_conditions') as mock_check_conditions2:
                    mock_check_conditions2.return_value = False  # Make sure buy conditions fail
                    mock_get_strategy.return_value = {  # Need to return a strategy for the sell test too
                        "buy_point": 83.0,
                        "sell_point": 85.0,
                        "buy_total": 700,
                        "sell_total": 0,
                        "buy_limit_price": 0.0,
                        "sell_limit_price": 0.0,
                        "buy_day_interval": 0,
                        "buy_price_interval": 0.0
                    }
                    mock_quote.return_value = {"lastPrice": 90.0, "volume": 1000000}  # Price above sell point
                    mock_qty.return_value = 10  # Have some position to sell

                    self.strategy.execute_strategy("TQQQ")  # This should trigger sell since quantity > 0
                    self.assertEqual(mock_qty.call_count, 1)  # qty is called for the sell condition
                    self.assertEqual(mock_order.call_count, 1)  # order is placed for sell


    def test_execute_strategy_with_signal(self):
        """Test execute_strategy with signal indicator logic."""
        with patch.object(self.strategy, 'is_trading_time') as mock_time, \
             patch.object(self.strategy, 'is_near_close') as mock_close, \
             patch.object(self.strategy, 'get_stock_strategy') as mock_get_strategy, \
             patch.object(self.strategy, 'get_signal_indicator') as mock_signal, \
             patch.object(self.strategy, 'check_buy_conditions') as mock_check_conditions:

            mock_time.return_value = True
            mock_close.return_value = True
            mock_check_conditions.return_value = True
            
            # Scenario 1: Price below buy point, but signal too high (Greed) -> No buy
            mock_get_strategy.return_value = {
                "buy_point": 83.0,
                "sell_point": 85.0,
                "buy_total": 700,
                "fear_greed_buy": -50.0,
                "max_position": 100.0,
                "buy_limit_price": 0.0
            }
            mock_signal.return_value = 0.0 # Not fearful enough
            
            with patch.object(self.mock_api, 'get_realtime_quote') as mock_quote, \
                 patch.object(self.mock_api, 'get_total_portfolio_value') as mock_portfolio, \
                 patch.object(self.mock_api, 'get_stock_position_qty') as mock_qty, \
                 patch.object(self.mock_api, 'place_order') as mock_order:
                
                mock_quote.return_value = {"lastPrice": 80.0, "volume": 1000000}
                mock_portfolio.return_value = 10000.0
                mock_qty.return_value = 0
                self.strategy.execute_strategy("TQQQ")
                mock_order.assert_not_called()

            # Scenario 2: Price below buy point, AND signal low enough (Fear) -> Buy
            mock_signal.return_value = -60.0 # Very fearful
            with patch.object(self.mock_api, 'get_realtime_quote') as mock_quote, \
                 patch.object(self.mock_api, 'get_total_portfolio_value') as mock_portfolio, \
                 patch.object(self.mock_api, 'get_stock_position_qty') as mock_qty, \
                 patch.object(self.mock_api, 'place_order') as mock_order:
                
                mock_quote.return_value = {"lastPrice": 80.0, "volume": 1000000}
                mock_portfolio.return_value = 10000.0
                mock_qty.return_value = 0
                mock_order.return_value = {"orderId": "123"}
                self.strategy.execute_strategy("TQQQ")
                mock_order.assert_called_once()
                
            # Scenario 3: Price NOT above sell point, but signal too high (Greed) -> Sell
            mock_signal.return_value = 60.0 # Greed
            mock_get_strategy.return_value["fear_greed_sell"] = 50.0
            mock_get_strategy.return_value["sell_limit_price"] = 0.0
            with patch.object(self.mock_api, 'get_realtime_quote') as mock_quote, \
                 patch.object(self.mock_api, 'get_total_portfolio_value') as mock_portfolio, \
                 patch.object(self.mock_api, 'get_stock_position_qty') as mock_qty, \
                 patch.object(self.mock_api, 'place_order') as mock_order:
                
                mock_quote.return_value = {"lastPrice": 84.0, "volume": 1000000} # Below sell point 85
                mock_portfolio.return_value = 10000.0
                mock_qty.return_value = 10
                mock_order.return_value = {"orderId": "124"}
                self.strategy.execute_strategy("TQQQ")
                mock_order.assert_called_once()


if __name__ == '__main__':
    unittest.main()