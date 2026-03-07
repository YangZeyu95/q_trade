from fastapi.testclient import TestClient
import os
import sys
import json
import unittest
import csv
import tempfile
from unittest.mock import patch, MagicMock, mock_open

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import app

class TestBackend(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.sample_strategy = {
            "name": "Apple Inc.",
            "buy_point": 150.0,
            "sell_point": 200.0,
            "buy_total": 1000,
            "sell_total": 0,
            "buy_limit_price": 0.0,
            "sell_limit_price": 0.0,
            "buy_day_interval": 1,
            "sell_day_interval": 1,
            "buy_price_interval": 2.0,
            "max_position": 100.0,
            "fear_greed_buy": -50.0,
            "fear_greed_sell": 50.0
        }

    @patch('main.load_strategies')
    def test_get_strategies(self, mock_load):
        mock_load.return_value = {"TQQQ": self.sample_strategy}
        response = self.client.get("/api/strategies")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"TQQQ": self.sample_strategy})

    @patch('main.api.get_stock_name')
    @patch('main.load_strategies')
    @patch('main.save_strategies')
    def test_update_strategy_success(self, mock_save, mock_load, mock_name):
        mock_load.return_value = {}
        mock_name.return_value = "Apple Inc."
        
        response = self.client.post("/api/strategies/AAPL", json=self.sample_strategy)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        mock_save.assert_called_once()
        # Verify symbol normalization
        args, _ = mock_save.call_args
        self.assertIn("AAPL", args[0])

    def test_update_strategy_validation_fail(self):
        # Test negative values
        bad_strategy = self.sample_strategy.copy()
        bad_strategy["buy_point"] = -10.0
        response = self.client.post("/api/strategies/AAPL", json=bad_strategy)
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be negative", response.json()["detail"])

    @patch('main.load_strategies')
    @patch('main.save_strategies')
    def test_delete_strategy(self, mock_save, mock_load):
        mock_load.return_value = {"AAPL": self.sample_strategy}
        response = self.client.delete("/api/strategies/AAPL")
        self.assertEqual(response.status_code, 200)
        mock_save.assert_called_once_with({})

    @patch('main.api.get_stock_name')
    def test_get_stock_info(self, mock_name):
        mock_name.return_value = "Tesla, Inc."
        response = self.client.get("/api/stock_info/TSLA")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"symbol": "TSLA", "name": "Tesla, Inc."})

    @patch('main.api.get_stock_position_qty')
    @patch('main.api.get_realtime_quote')
    @patch('main.api.get_position')
    @patch('main.load_strategies')
    def test_get_realtime_data(self, mock_load, mock_pos, mock_quote, mock_qty):
        mock_load.return_value = {"TQQQ": self.sample_strategy}
        # Total portfolio value calculation mock
        mock_pos.return_value = {
            "positionList": [
                {"stockCode": "TQQQ", "marketValue": "5000.0"}
            ]
        }
        mock_quote.return_value = {"lastPrice": 50.0}
        mock_qty.return_value = 100
        
        response = self.client.get("/api/realtime")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("TQQQ", data)
        self.assertEqual(data["TQQQ"]["quantity"], 100)
        self.assertEqual(data["TQQQ"]["value"], 5000.0)
        self.assertEqual(data["TQQQ"]["weight"], 100.0) # Only one stock
        self.assertIn("signal", data["TQQQ"])

    @patch('main.api.get_position')
    def test_get_full_holdings(self, mock_pos):
        mock_pos.return_value = {
            "positionList": [
                {"stockCode": "TQQQ", "marketValue": "6000.0", "incomeBalance": "100.0"},
                {"stockCode": "AAPL", "marketValue": "4000.0", "incomeBalance": "-50.0"}
            ]
        }
        response = self.client.get("/api/holdings")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        # Check weight calculation: 6000 / (6000+4000) = 60%
        self.assertEqual(data[0]["weight"], 60.0)
        self.assertEqual(data[1]["weight"], 40.0)

    def test_get_indicator(self):
        response = self.client.get("/api/indicator")
        self.assertEqual(response.status_code, 200)
        self.assertIn("value", response.json())

    @patch('os.path.exists')
    def test_get_history(self, mock_exists):
        mock_exists.return_value = True
        csv_content = "timestamp,action,quantity,price,volume\n2026-03-07 10:00:00,buy,10,150.0,1000000"
        
        with patch('main.open', mock_open(read_data=csv_content)):
            response = self.client.get("/api/history/AAPL")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()), 1)
            self.assertEqual(response.json()[0]["action"], "buy")

    @patch('os.listdir')
    @patch('os.path.exists')
    def test_get_all_history(self, mock_exists, mock_listdir):
        mock_exists.return_value = True
        mock_listdir.return_value = ["aapl_trading.csv"]
        csv_content = "timestamp,action,quantity,price,volume\n2026-03-07 10:00:00,buy,10,150.0,1000000"
        
        with patch('main.open', mock_open(read_data=csv_content)):
            response = self.client.get("/api/all_history")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()), 1)
            self.assertEqual(response.json()[0]["symbol"], "AAPL")

if __name__ == "__main__":
    unittest.main()
