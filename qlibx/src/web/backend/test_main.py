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

import main
from main import app

class TestBackend(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        main._gateway_logged_in = False
        main._gateway_login_at = None
        main._gateway_last_heartbeat_at = None
        main._gateway_last_error = None
        main._gateway_disconnect_started_at = None
        main._gateway_disconnect_started_monotonic = None
        main._gateway_probe_enabled = False
        main._bot_process = None
        main._bot_exit_code = None
        main._manual_order_process = None
        main._manual_order_request = None
        main._manual_order_started_at = None
        main._manual_order_exit_code = None
        main._market_snapshot = None
        main._market_snapshot_at = 0.0
        main._market_snapshot_last_attempt_at = 0.0
        main._trade_history_api = None
        main.SZDT_CACHE.clear()
        self.sample_strategy = {
            "name": "Apple Inc.",
            "buy_point": 150.0,
            "sell_point": 200.0,
            "buy_total": 1000,
            "sell_total": 500,
            "buy_limit_price": 0.0,
            "sell_limit_price": 0.0,
            "buy_day_interval": 1,
            "sell_day_interval": 1,
            "buy_price_interval": 2.0,
            "max_position": 100.0,
            "fear_greed_buy": -50.0,
            "fear_greed_sell": 50.0,
            "lever": "3",
            "emo_area": "us"
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
        self.assertEqual(response.status_code, 422)

    def test_update_strategy_rejects_zero_sell_total(self):
        bad_strategy = self.sample_strategy.copy()
        bad_strategy["sell_total"] = 0
        response = self.client.post("/api/strategies/AAPL", json=bad_strategy)
        self.assertEqual(response.status_code, 422)

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

    @patch('main.api.get_realtime_quotes')
    @patch('main.api.get_account_funds')
    @patch('main.api.get_position')
    @patch('main.load_strategies')
    def test_get_realtime_data(self, mock_load, mock_pos, mock_funds, mock_quotes):
        mock_load.return_value = {"TQQQ": self.sample_strategy}
        mock_pos.return_value = {
            "positionList": [
                {"stockCode": "TQQQ", "marketValue": "5000.0", "enableAmount": "100"}
            ]
        }
        mock_funds.return_value = {
            "assetBalance": "10000.0",
            "enableBalance": "5000.0",
            "buyPower": "5000.0",
        }
        mock_quotes.return_value = {"TQQQ": {"lastPrice": 50.0, "volume": 1000}}
        
        response = self.client.get("/api/realtime")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("TQQQ", data)
        self.assertEqual(data["TQQQ"]["quantity"], 100)
        self.assertEqual(data["TQQQ"]["value"], 5000.0)
        self.assertEqual(data["TQQQ"]["weight"], 50.0)
        self.assertIn("signal", data["TQQQ"])
        mock_quotes.assert_called_once_with(["TQQQ"], data_type=20002)

    @patch('main.get_hs_trade_history_api')
    @patch('main.get_cached_signal', return_value=-20.0)
    @patch('main.api.get_realtime_quotes')
    @patch('main.api.get_account_funds')
    @patch('main.api.get_position')
    @patch('main.load_strategies')
    def test_market_snapshot_is_shared_across_endpoints(
        self, mock_load, mock_pos, mock_funds, mock_quotes, mock_signal,
        mock_history_api,
    ):
        main._gateway_logged_in = True
        mock_load.return_value = {"TQQQ": self.sample_strategy}
        mock_pos.return_value = {
            "positionList": [
                {"stockCode": "TQQQ", "marketValue": "5000", "enableAmount": "100"}
            ]
        }
        mock_funds.return_value = {
            "assetBalance": "10000",
            "enableBalance": "5000",
            "buyPower": "5000",
        }
        mock_quotes.return_value = {"TQQQ": {"lastPrice": 50.0, "volume": 1000}}
        query_order = []
        history_result = {
            "TQQQ": {
                "buy": {"date": "2026-07-18", "price": 49.5, "source": "HS SDK"},
                "sell": {"date": "2026-07-19", "price": 51.0, "source": "HS SDK"},
            }
        }
        mock_history_api.return_value.get_latest_by_symbol.side_effect = (
            lambda **kwargs: query_order.append("history") or history_result
        )
        mock_history_api.return_value.history_complete = True
        active_result = [
            {
                "order_id": "OPEN-1", "symbol": "MSTU", "action": "buy",
                "status": "已报", "quantity": 10.0, "price": 1.5,
                "notional": 15.0, "source": "HS SDK current entrust",
            }
        ]
        mock_history_api.return_value.get_active_orders.side_effect = (
            lambda **kwargs: query_order.append("active") or active_result
        )

        snapshot = self.client.get("/api/market/snapshot")
        holdings = self.client.get("/api/holdings")
        account = self.client.get("/api/account")

        self.assertEqual(snapshot.status_code, 200)
        self.assertFalse(snapshot.json()["stale"])
        self.assertTrue(snapshot.json()["trade_history_available"])
        self.assertTrue(snapshot.json()["trade_history_complete"])
        self.assertEqual(snapshot.json()["trade_history"]["TQQQ"]["buy"]["price"], 49.5)
        self.assertTrue(snapshot.json()["active_orders_available"])
        self.assertEqual(snapshot.json()["active_orders"][0]["order_id"], "OPEN-1")
        self.assertEqual(len(holdings.json()), 1)
        self.assertEqual(account.json()["market_value"], 5000.0)
        mock_pos.assert_called_once_with(exchange_type="P")
        mock_funds.assert_called_once_with(exchange_type="P")
        mock_quotes.assert_called_once_with(["TQQQ"], data_type=20002)
        mock_signal.assert_called_once()
        mock_history_api.return_value.get_latest_by_symbol.assert_called_once_with(
            exchange_type="P"
        )
        mock_history_api.return_value.get_active_orders.assert_called_once_with(
            exchange_type="P"
        )
        # Query active entrusts first. If an order fills between the two calls,
        # the snapshot then contains either the old active order or the new fill.
        self.assertEqual(query_order, ["active", "history"])

    @patch('main.get_hs_trade_history_api')
    @patch('main.get_cached_signal', return_value=-20.0)
    @patch('main.api.get_realtime_quotes', return_value={"TQQQ": {"lastPrice": 50}})
    @patch('main.api.get_account_funds', return_value={"assetBalance": "10000"})
    @patch('main.api.get_position', return_value={"positionList": []})
    @patch('main.load_strategies', return_value={"TQQQ": {}})
    def test_current_entrust_failure_makes_snapshot_incomplete(
        self, mock_load, mock_pos, mock_funds, mock_quotes, mock_signal,
        mock_history_api,
    ):
        main._gateway_logged_in = True
        mock_history_api.return_value.get_latest_by_symbol.return_value = {}
        mock_history_api.return_value.history_complete = True
        mock_history_api.return_value.get_active_orders.side_effect = RuntimeError("down")

        snapshot = main._build_market_snapshot()

        self.assertFalse(snapshot["complete"])
        self.assertFalse(snapshot["active_orders_available"])

    @patch('main.api.get_realtime_quotes', return_value={})
    @patch('main.api.get_account_funds', return_value=None)
    @patch('main.api.get_position', return_value=None)
    @patch('main.load_strategies', return_value={"TQQQ": {}})
    def test_incomplete_snapshot_has_gateway_retry_backoff(
        self, mock_load, mock_pos, mock_funds, mock_quotes
    ):
        first = self.client.get("/api/market/snapshot")
        second = self.client.get("/api/market/snapshot")

        self.assertTrue(first.json()["stale"])
        self.assertTrue(second.json()["stale"])
        self.assertEqual(second.json()["account"]["total_asset"], 0.0)
        self.assertEqual(second.json()["account"]["risk_status"], "unavailable")
        mock_pos.assert_called_once_with(exchange_type="P")
        mock_funds.assert_called_once_with(exchange_type="P")
        mock_quotes.assert_called_once_with(["TQQQ"], data_type=20002)

    @patch('main._build_market_snapshot')
    def test_forced_snapshot_respects_failed_refresh_cooldown(self, mock_build):
        main._market_snapshot = {
            "holdings": [{"stockCode": "TQQQ"}],
            "account": {"total_asset": 10000},
            "realtime": {"TQQQ": {"signal": -20}},
            "complete": True,
        }
        mock_build.return_value = {"complete": False}

        first = main.refresh_market_snapshot(force=True)
        second = main.refresh_market_snapshot(force=True)

        self.assertFalse(first["complete"])
        self.assertFalse(second["complete"])
        self.assertEqual(second["holdings"][0]["stockCode"], "TQQQ")
        mock_build.assert_called_once_with()

    @patch('main._build_market_snapshot')
    def test_trading_snapshot_force_bypasses_successful_cache(self, mock_build):
        mock_build.side_effect = [
            {"complete": True, "generation": 1},
            {"complete": True, "generation": 2},
        ]

        first = self.client.get("/api/market/snapshot")
        cached = self.client.get("/api/market/snapshot")
        forced = self.client.get("/api/market/snapshot?force=true")

        self.assertEqual(first.json()["generation"], 1)
        self.assertEqual(cached.json()["generation"], 1)
        self.assertEqual(forced.json()["generation"], 2)
        self.assertEqual(mock_build.call_count, 2)

    @patch('main._build_market_snapshot', return_value={"complete": False})
    def test_failed_forced_snapshot_does_not_reuse_old_order_data(self, mock_build):
        main._market_snapshot = {"complete": True, "generation": "old"}
        main._market_snapshot_at = main.time.time()

        response = self.client.get("/api/market/snapshot?force=true")

        self.assertFalse(response.json()["complete"])
        self.assertTrue(response.json()["stale"])
        self.assertNotIn("generation", response.json())
        mock_build.assert_called_once_with()

    @patch('main.api.get_account_funds')
    @patch('main.api.get_position')
    def test_get_full_holdings(self, mock_pos, mock_funds):
        mock_pos.return_value = {
            "positionList": [
                {"stockCode": "TQQQ", "marketValue": "6000.0", "incomeBalance": "100.0"},
                {"stockCode": "AAPL", "marketValue": "4000.0", "incomeBalance": "-50.0"}
            ]
        }
        mock_funds.return_value = {
            "assetBalance": "20000.0",
            "enableBalance": "10000.0",
            "buyPower": "10000.0",
        }
        response = self.client.get("/api/holdings")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        # Same denominator as max_position: position value / account equity.
        self.assertEqual(data[0]["weight"], 30.0)
        self.assertEqual(data[1]["weight"], 20.0)

    def test_get_indicator(self):
        response = self.client.get("/api/indicator")
        self.assertEqual(response.status_code, 200)
        self.assertIn("value", response.json())

    def test_default_signal_cache_is_half_hour(self):
        self.assertEqual(main.DEFAULT_SIGNAL_CACHE_TTL_SECONDS, 1800)

    def test_market_status_endpoint_reports_regular_session_contract(self):
        response = self.client.get("/api/market/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn(data["session"], {"regular", "closed"})
        self.assertIn(data["reason"], {"weekend", "before_open", "after_close", "regular_session"})
        self.assertEqual(data["timezone"], "America/New_York")
        self.assertFalse(data["extended_hours_enabled"])

    @patch('main.api.check_connection', return_value=True)
    @patch('main.api.log_in', return_value=True)
    def test_login_endpoint_updates_gateway_state(self, mock_login, mock_check):
        response = self.client.post("/api/auth/login", json={"password": "secret"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["logged_in"])
        self.assertTrue(main._gateway_logged_in)
        mock_login.assert_called_once_with(password="secret")
        mock_check.assert_called_once_with(exchange_type="P")

    @patch('main.api.log_in', return_value=False)
    def test_login_failure_does_not_start_trading(self, mock_login):
        response = self.client.post("/api/auth/login", json={"password": "bad"})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(main._gateway_logged_in)
        mock_login.assert_called_once_with(password="bad")

    @patch('main.api.check_connection', return_value=True)
    def test_auth_health_can_recover_logged_out_state(self, mock_check):
        response = self.client.get("/api/auth/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["logged_in"])
        self.assertEqual(response.json()["connection_state"], "connected")
        mock_check.assert_called_once_with(exchange_type="P")

    @patch('main.api.check_connection', return_value=False)
    def test_heartbeat_failure_clears_state_and_blocks_engine_start(self, mock_check):
        main._gateway_logged_in = True
        main._gateway_login_at = "2026-07-19T10:00:00"
        main._bot_process = None

        self.assertFalse(main._run_gateway_heartbeat_once())
        self.assertFalse(main._gateway_logged_in)
        self.assertEqual(main.get_gateway_connection_state(), "disconnected")
        self.assertIsNotNone(main._gateway_last_error)

        response = self.client.post("/api/trading/start", json={"mode": "dry_run"})
        self.assertEqual(response.status_code, 401)
        mock_check.assert_called_once_with(exchange_type="P")

    @patch('main.api.check_connection', side_effect=[False, True])
    def test_heartbeat_keeps_probing_after_transient_disconnect(self, mock_check):
        main._gateway_logged_in = True
        main._gateway_probe_enabled = True

        self.assertFalse(main._run_gateway_heartbeat_once())
        self.assertFalse(main._gateway_logged_in)
        self.assertTrue(main._gateway_probe_enabled)

        self.assertTrue(main._run_gateway_heartbeat_once())
        self.assertTrue(main._gateway_logged_in)
        self.assertEqual(main.get_gateway_connection_state(), "connected")
        self.assertEqual(mock_check.call_count, 2)

    @patch('main.api.check_connection', return_value=False)
    def test_heartbeat_grace_defers_engine_stop_until_timeout(self, mock_check):
        fake_process = MagicMock()
        fake_process.poll.return_value = None
        fake_process.returncode = -15
        main._gateway_logged_in = True
        main._gateway_probe_enabled = True
        main._bot_process = fake_process

        with patch.object(main, "GATEWAY_DISCONNECT_GRACE_SECONDS", 60), \
                patch('main.time.monotonic', side_effect=[100.0, 161.0]):
            self.assertFalse(main._run_gateway_heartbeat_once())
            fake_process.terminate.assert_not_called()

            self.assertFalse(main._run_gateway_heartbeat_once())
            fake_process.terminate.assert_called_once()

        self.assertFalse(main._gateway_logged_in)
        self.assertEqual(mock_check.call_count, 2)

    @patch('main.api.check_connection', return_value=False)
    def test_heartbeat_failure_stops_running_engine(self, mock_check):
        fake_process = MagicMock()
        fake_process.poll.return_value = None
        fake_process.returncode = 0
        main._gateway_logged_in = True
        main._bot_process = fake_process

        with patch.object(main, "GATEWAY_DISCONNECT_GRACE_SECONDS", 0):
            self.assertFalse(main._run_gateway_heartbeat_once())
        fake_process.terminate.assert_called_once()
        fake_process.wait.assert_called_once_with(timeout=5)
        self.assertFalse(main._gateway_logged_in)
        mock_check.assert_called_once_with(exchange_type="P")

    def test_risk_config_round_trip_and_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "trading_config.json")
            with patch.object(main, "TRADING_CONFIG_FILE", config_path):
                response = self.client.get("/api/trading/config")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["max_total_leverage"], 2.0)

                response = self.client.put("/api/trading/config", json={
                    "max_total_leverage": 2.1,
                    "min_maintenance_margin_ratio": 0.35,
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["config"]["min_maintenance_margin_ratio"], 0.35)

                response = self.client.get("/api/trading/config")
                self.assertEqual(response.json()["max_total_leverage"], 2.1)

                response = self.client.put("/api/trading/config", json={
                    "max_total_leverage": 1.5,
                    "min_maintenance_margin_ratio": 1.1,
                })
                self.assertEqual(response.status_code, 422)

    def test_szdt_key_uses_file_override_then_environment_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "trading_config.json")
            with patch.object(main, "TRADING_CONFIG_FILE", config_path), \
                    patch.dict(os.environ, {"SZDT_AUTH_KEY": "env-key"}, clear=False):
                response = self.client.get("/api/trading/config")
                self.assertEqual(response.json()["szdt_auth_key_source"], "environment")
                self.assertNotIn("szdt_auth_key", response.json())
                self.assertEqual(main.get_effective_szdt_auth_key(), "env-key")

                response = self.client.put("/api/trading/config", json={
                    "max_total_leverage": 2.0,
                    "min_maintenance_margin_ratio": 0.30,
                    "szdt_auth_key": "file-key",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["config"]["szdt_auth_key_source"], "file")
                self.assertEqual(main.get_effective_szdt_auth_key(), "file-key")
                self.assertNotIn("szdt_auth_key", response.json()["config"])

                # Updating risk values without touching the key preserves it.
                self.client.put("/api/trading/config", json={
                    "max_total_leverage": 1.5,
                    "min_maintenance_margin_ratio": 0.35,
                })
                self.assertEqual(main.get_effective_szdt_auth_key(), "file-key")

                # An explicit empty key removes the file override and reveals env fallback.
                response = self.client.put("/api/trading/config", json={
                    "max_total_leverage": 1.5,
                    "min_maintenance_margin_ratio": 0.35,
                    "szdt_auth_key": "",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["config"]["szdt_auth_key_source"], "environment")
                self.assertEqual(main.get_effective_szdt_auth_key(), "env-key")

    def test_start_requires_gateway_login(self):
        main._gateway_logged_in = False
        response = self.client.post("/api/trading/start", json={"mode": "dry_run"})
        self.assertEqual(response.status_code, 401)

    def test_manual_order_dry_run_starts_one_shot_worker(self):
        fake_process = MagicMock()
        fake_process.pid = 4321
        fake_process.poll.return_value = None

        with patch('main.subprocess.Popen', return_value=fake_process) as mock_popen:
            response = self.client.post("/api/orders/manual", json={
                "symbol": "tqqq",
                "side": "buy",
                "quantity": 2,
                "limit_price": 70.01,
                "mode": "dry_run",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(response.json()["manual_order"]["state"], "running")
        command = mock_popen.call_args.args[0]
        self.assertIn("manual_order_worker.py", command[1])
        self.assertEqual(command[command.index("--symbol") + 1], "TQQQ")
        self.assertEqual(command[command.index("--quantity") + 1], "2")

    def test_manual_order_requires_confirmation_for_live_mode(self):
        response = self.client.post("/api/orders/manual", json={
            "symbol": "TQQQ",
            "side": "buy",
            "quantity": 1,
            "limit_price": 70,
            "mode": "live",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("explicit confirmation", response.json()["detail"])

    def test_manual_order_rejects_non_finite_limit_price(self):
        response = self.client.post("/api/orders/manual", json={
            "symbol": "TQQQ",
            "side": "sell",
            "quantity": 1,
            "limit_price": "inf",
            "mode": "dry_run",
        })
        self.assertEqual(response.status_code, 422)

    @patch('main.get_market_status', return_value={"is_open": True})
    def test_manual_live_order_requires_gateway_connection(self, mock_market):
        response = self.client.post("/api/orders/manual", json={
            "symbol": "TQQQ",
            "side": "buy",
            "quantity": 1,
            "limit_price": 70,
            "mode": "live",
            "confirm_live": True,
        })
        self.assertEqual(response.status_code, 401)
        mock_market.assert_not_called()

    @patch('main.get_market_status', return_value={"is_open": False})
    @patch('main.subprocess.Popen')
    def test_manual_live_order_is_not_blocked_by_local_market_clock(self, mock_popen, mock_market):
        fake_process = MagicMock()
        fake_process.pid = 4323
        fake_process.poll.return_value = None
        mock_popen.return_value = fake_process
        main._gateway_logged_in = True
        response = self.client.post("/api/orders/manual", json={
            "symbol": "TQQQ",
            "side": "buy",
            "quantity": 1,
            "limit_price": 70,
            "mode": "live",
            "confirm_live": True,
        })
        self.assertEqual(response.status_code, 200)
        mock_market.assert_not_called()
        self.assertEqual(response.json()["manual_order"]["state"], "running")

    def test_manual_order_is_blocked_while_automatic_engine_runs(self):
        fake_bot = MagicMock()
        fake_bot.poll.return_value = None
        main._bot_process = fake_bot

        response = self.client.post("/api/orders/manual", json={
            "symbol": "TQQQ",
            "side": "buy",
            "quantity": 1,
            "limit_price": 70,
            "mode": "dry_run",
        })
        self.assertEqual(response.status_code, 409)
        self.assertIn("自动交易引擎", response.json()["detail"])

    def test_manual_order_prevents_duplicate_workers(self):
        fake_process = MagicMock()
        fake_process.pid = 4322
        fake_process.poll.return_value = None
        main._manual_order_process = fake_process

        response = self.client.post("/api/orders/manual", json={
            "symbol": "TQQQ",
            "side": "buy",
            "quantity": 1,
            "limit_price": 70,
            "mode": "dry_run",
        })
        self.assertEqual(response.status_code, 409)
        self.assertIn("已有手动订单", response.json()["detail"])

    @patch('main.api.check_connection', return_value=False)
    def test_heartbeat_failure_stops_manual_order_worker(self, mock_check):
        fake_process = MagicMock()
        fake_process.poll.return_value = None
        fake_process.returncode = -15
        main._gateway_logged_in = True
        main._manual_order_process = fake_process

        with patch.object(main, "GATEWAY_DISCONNECT_GRACE_SECONDS", 0):
            self.assertFalse(main._run_gateway_heartbeat_once())
        fake_process.terminate.assert_called_once()
        fake_process.wait.assert_called_once_with(timeout=5)
        self.assertFalse(main._gateway_logged_in)
        mock_check.assert_called_once_with(exchange_type="P")

    @patch('main.probe_signal_service', return_value=(False, "SZDT_AUTH_KEY 未配置"))
    def test_start_is_blocked_when_signal_service_is_unavailable(self, mock_probe):
        main._gateway_logged_in = True
        response = self.client.post("/api/trading/start", json={"mode": "dry_run"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("贪恐指数服务不可用", response.json()["detail"])
        mock_probe.assert_called_once_with()

    @patch('main.probe_signal_service', return_value=(True, None))
    @patch('main.subprocess.Popen')
    def test_start_and_stop_engine_lifecycle(self, mock_popen, mock_probe):
        fake_process = MagicMock()
        fake_process.pid = 1234
        fake_process.returncode = 0
        fake_process.poll.side_effect = [None, None, None, None, 0, 0]
        mock_popen.return_value = fake_process

        old_process = main._bot_process
        old_logged_in = main._gateway_logged_in
        try:
            main._bot_process = None
            main._gateway_logged_in = True

            response = self.client.post("/api/trading/start", json={"mode": "dry_run"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["state"], "running")
            mock_popen.assert_called_once()
            self.assertNotIn("--live", mock_popen.call_args.args[0])

            response = self.client.post("/api/trading/stop")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["state"], "stopped")
            fake_process.terminate.assert_called_once()
            fake_process.wait.assert_called_once_with(timeout=5)
        finally:
            main._bot_process = old_process
            main._gateway_logged_in = old_logged_in

    @patch('main.read_trading_logs', return_value=['line 1\n', 'line 2\n'])
    def test_trading_logs_endpoint(self, mock_logs):
        response = self.client.get("/api/trading/logs?limit=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"lines": ['line 1\n', 'line 2\n']})
        mock_logs.assert_called_once_with(2)

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

    @patch('os.listdir')
    @patch('os.path.exists')
    def test_get_realtime_orders_includes_status_and_fill_fields(self, mock_exists, mock_listdir):
        mock_exists.return_value = True
        mock_listdir.return_value = ["aapl_trading.csv"]
        csv_content = (
            "timestamp,symbol,action,quantity,price,volume,order_id,status,"
            "filled_quantity,remaining_quantity,business_price,status_updated_at\n"
            "2026-03-07T10:00:00,AAPL,buy,10,150.0,1000000,O1,Partial,5,5,150.01,"
            "2026-03-07T10:00:01\n"
        )

        with patch('main.open', mock_open(read_data=csv_content)):
            response = self.client.get("/api/orders/realtime")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["active_count"], 1)
        self.assertEqual(data["orders"][0]["order_id"], "O1")
        self.assertEqual(data["orders"][0]["filled_quantity"], "5")
        self.assertTrue(data["orders"][0]["is_active"])

    @patch('os.listdir')
    @patch('os.path.exists')
    def test_get_realtime_orders_normalizes_and_conservatively_handles_status(self, mock_exists, mock_listdir):
        mock_exists.return_value = True
        mock_listdir.return_value = ["aapl_trading.csv"]
        csv_content = (
            "timestamp,symbol,action,quantity,price,volume,order_id,status\n"
            "2026-03-07T10:00:00,AAPL,buy,10,150,100,O1, filled \n"
            "2026-03-07T10:01:00,AAPL,buy,10,150,100,O2,GatewayNewState\n"
            "2026-03-07T10:02:00,AAPL,buy,10,150,100,,Submitted\n"
        )

        with patch('main.open', mock_open(read_data=csv_content)):
            response = self.client.get("/api/orders/realtime")

        self.assertEqual(response.status_code, 200)
        by_id = {row["order_id"]: row for row in response.json()["orders"]}
        self.assertFalse(by_id["O1"]["is_active"])
        self.assertTrue(by_id["O2"]["is_active"])
        self.assertFalse(by_id[""]["is_active"])
        self.assertEqual(response.json()["active_count"], 1)

    @patch('main.get_hs_option_api')
    def test_get_option_chain_uses_hs_sdk_adapter(self, mock_get_option_api):
        mock_get_option_api.return_value.get_chain.return_value = {
            "symbol": "AAPL",
            "market_price": 151.0,
            "expiration": "2026/07/24",
            "expirations": ["2026/07/24"],
            "calls": [],
            "puts": [],
            "source": "HS SDK",
            "data_type": 20003,
        }

        response = self.client.get(
            "/api/options/chain/aapl",
            params={"expiration": "2026/07/24"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "HS SDK")
        mock_get_option_api.return_value.get_chain.assert_called_once_with(
            "AAPL", "2026/07/24"
        )

    def test_get_option_chain_validates_symbol_and_expiration(self):
        self.assertEqual(
            self.client.get("/api/options/chain/AAPL%20BAD").status_code,
            422,
        )
        response = self.client.get(
            "/api/options/chain/AAPL",
            params={"expiration": "2026-07-24"},
        )
        self.assertEqual(response.status_code, 422)

if __name__ == "__main__":
    unittest.main()
