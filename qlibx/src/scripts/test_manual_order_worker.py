"""Tests for the one-shot manual order process."""

import os
import sys
import csv
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import manual_order_worker as worker
from huasheng_api import HuashengGatewayAPI as RealHuashengGatewayAPI


def _args(**overrides):
    values = {
        "mode": "live",
        "symbol": "TQQQ",
        "side": "buy",
        "quantity": 2,
        "limit_price": "70.01",
        "auto_cancel_seconds": 10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _fake_strategy():
    strategy = MagicMock()
    strategy.exchange_type = "P"
    strategy.data_type = 20002
    strategy.max_total_leverage = 2.0
    strategy.min_maintenance_margin_ratio = 0.30
    strategy.is_trading_time.return_value = True
    strategy._get_account_risk_metrics.return_value = {
        "equity": 100000.0,
        "gross_market_value": 0.0,
        "buying_power": 100000.0,
    }
    strategy.api.get_realtime_quote.return_value = {
        "lastPrice": 70.0,
        "volume": 1000,
    }
    strategy.api.get_stock_position_qty.return_value = 0
    return strategy


class TestManualOrderWorker(unittest.TestCase):

    def test_order_status_reads_matching_csv_and_handles_missing_file(self):
        strategy = SimpleNamespace(trades_dir=tempfile.mkdtemp())
        self.addCleanup(
            lambda: shutil.rmtree(strategy.trades_dir, ignore_errors=True)
        )
        self.assertIsNone(worker._order_status(strategy, "TQQQ", "O1"))
        path = os.path.join(strategy.trades_dir, "tqqq_trading.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["order_id", "status"])
            writer.writeheader()
            writer.writerow({"order_id": "O1", "status": "Partial"})
        self.assertEqual(worker._order_status(strategy, "TQQQ", "O1"), "Partial")
        self.assertIsNone(worker._order_status(strategy, "TQQQ", "OTHER"))

    def test_dry_run_never_starts_listener_or_submits(self):
        with patch.object(worker, "HuashengGatewayAPI") as api_class, \
                patch.object(worker, "TradingStrategy") as strategy_class, \
                patch.object(worker, "TradePushListener") as listener_class:
            self.assertEqual(worker.run(_args(mode="dry_run")), 0)

        api_class.assert_called_once_with()
        strategy_class.assert_called_once()
        listener_class.assert_not_called()

    def test_live_order_records_nested_order_id_and_waits_for_fill(self):
        api = Mock()
        api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        api.get_stock_position_qty.return_value = 0
        strategy = _fake_strategy()
        strategy.api = api
        strategy.submit_order.return_value = {"data": {"data": "ORDER-1"}}
        listener = Mock()
        listener.start.return_value = True

        with patch.object(worker, "HuashengGatewayAPI", return_value=api), \
                patch.object(worker, "TradingStrategy", return_value=strategy), \
                patch.object(worker, "TradePushListener", return_value=listener), \
                patch.object(worker, "_order_status", return_value="Filled") as status:
            worker.HuashengGatewayAPI.extract_order_id.side_effect = (
                RealHuashengGatewayAPI.extract_order_id
            )
            result = worker.run(_args())

        self.assertEqual(result, 0)
        listener.start.assert_called_once_with()
        listener.stop.assert_called_once_with()
        strategy.submit_order.assert_called_once_with(
            symbol="TQQQ",
            action="buy",
            quantity=2,
            price="70.01",
            volume=1000,
            entrust_type="3",
        )
        api.cancel_order.assert_not_called()
        self.assertEqual(status.call_count, 2)
        status.assert_any_call(strategy, "TQQQ", "ORDER-1")

    def test_gateway_validation_listener_and_submit_failures_are_nonzero(self):
        api = Mock()
        strategy = _fake_strategy()
        strategy.api = api

        api.check_connection.return_value = False
        with patch.object(worker, "HuashengGatewayAPI", return_value=api), \
                patch.object(worker, "TradingStrategy", return_value=strategy):
            self.assertEqual(worker.run(_args()), 3)

        api.check_connection.return_value = True
        api.get_realtime_quote.return_value = None
        with patch.object(worker, "HuashengGatewayAPI", return_value=api), \
                patch.object(worker, "TradingStrategy", return_value=strategy):
            self.assertEqual(worker.run(_args()), 2)

        api.get_realtime_quote.return_value = {"lastPrice": 70, "volume": 100}
        api.get_stock_position_qty.return_value = 0
        listener = Mock()
        listener.start.return_value = False
        with patch.object(worker, "HuashengGatewayAPI", return_value=api), \
                patch.object(worker, "TradingStrategy", return_value=strategy), \
                patch.object(worker, "TradePushListener", return_value=listener):
            self.assertEqual(worker.run(_args()), 3)
        listener.stop.assert_called_once()

        listener.reset_mock()
        listener.start.return_value = True
        strategy.submit_order.return_value = None
        with patch.object(worker, "HuashengGatewayAPI", return_value=api), \
                patch.object(worker, "TradingStrategy", return_value=strategy), \
                patch.object(worker, "TradePushListener", return_value=listener):
            self.assertEqual(worker.run(_args()), 4)
        listener.stop.assert_called_once()

    def test_cancel_request_failure_returns_nonzero_when_order_is_still_active(self):
        api = Mock()
        api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        api.get_stock_position_qty.return_value = 0
        api.cancel_order.return_value = None
        strategy = _fake_strategy()
        strategy.api = api
        strategy.submit_order.return_value = {"orderId": "ORDER-CANCEL-FAIL"}
        listener = Mock()
        listener.start.return_value = True

        with patch.object(worker, "HuashengGatewayAPI", return_value=api), \
                patch.object(worker, "TradingStrategy", return_value=strategy), \
                patch.object(worker, "TradePushListener", return_value=listener), \
                patch.object(worker, "_order_status", return_value="Submitted"), \
                patch.object(worker.time, "monotonic", side_effect=[0, 11]):
            worker.HuashengGatewayAPI.extract_order_id.side_effect = (
                RealHuashengGatewayAPI.extract_order_id
            )
            self.assertEqual(worker.run(_args()), 5)

        listener.stop.assert_called_once()

    def test_pending_order_is_automatically_cancelled(self):
        api = Mock()
        api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        api.get_stock_position_qty.return_value = 0
        api.cancel_order.return_value = {"success": True}
        strategy = _fake_strategy()
        strategy.api = api
        strategy.submit_order.return_value = {"orderId": "ORDER-2"}
        listener = Mock()
        listener.start.return_value = True

        with patch.object(worker, "HuashengGatewayAPI", return_value=api), \
                patch.object(worker, "TradingStrategy", return_value=strategy), \
                patch.object(worker, "TradePushListener", return_value=listener), \
                patch.object(worker, "_order_status", side_effect=["Submitted", "Submitted", "Canceled"]), \
                patch.object(worker.time, "monotonic", side_effect=[0, 11, 11, 11]):
            worker.HuashengGatewayAPI.extract_order_id.side_effect = (
                RealHuashengGatewayAPI.extract_order_id
            )
            result = worker.run(_args())

        self.assertEqual(result, 0)
        api.cancel_order.assert_called_once_with(
            order_id="ORDER-2",
            stock_code="TQQQ",
            exchange_type="P",
            entrust_amount=2,
            entrust_price="70.01",
            entrust_type="3",
        )
        strategy.update_pending_orders.assert_called_once_with()
        listener.stop.assert_called_once_with()

    def test_lowercase_terminal_status_does_not_trigger_cancel(self):
        api = Mock()
        api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        api.get_stock_position_qty.return_value = 0
        strategy = _fake_strategy()
        strategy.api = api
        strategy.submit_order.return_value = {"orderId": "ORDER-LOWER"}
        listener = Mock()
        listener.start.return_value = True

        with patch.object(worker, "HuashengGatewayAPI", return_value=api), \
                patch.object(worker, "TradingStrategy", return_value=strategy), \
                patch.object(worker, "TradePushListener", return_value=listener), \
                patch.object(worker, "_order_status", return_value="filled"), \
                patch.object(worker.time, "monotonic", return_value=0):
            worker.HuashengGatewayAPI.extract_order_id.side_effect = (
                RealHuashengGatewayAPI.extract_order_id
            )
            result = worker.run(_args())

        self.assertEqual(result, 0)
        api.cancel_order.assert_not_called()

    def test_unconfirmed_cancel_returns_failure_for_manual_review(self):
        api = Mock()
        api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        api.get_stock_position_qty.return_value = 0
        api.cancel_order.return_value = {"success": True}
        strategy = _fake_strategy()
        strategy.api = api
        strategy.submit_order.return_value = {"orderId": "ORDER-STUCK"}
        listener = Mock()
        listener.start.return_value = True

        with patch.object(worker, "HuashengGatewayAPI", return_value=api), \
                patch.object(worker, "TradingStrategy", return_value=strategy), \
                patch.object(worker, "TradePushListener", return_value=listener), \
                patch.object(worker, "_order_status", return_value="Submitted"), \
                patch.object(worker.time, "monotonic", side_effect=[0, 11, 20, 36]):
            worker.HuashengGatewayAPI.extract_order_id.side_effect = (
                RealHuashengGatewayAPI.extract_order_id
            )
            result = worker.run(_args())

        self.assertEqual(result, 6)
        api.cancel_order.assert_called_once()
        listener.stop.assert_called_once_with()

    def test_sell_quantity_cannot_exceed_available_position(self):
        api = Mock()
        api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        api.get_stock_position_qty.return_value = 1
        strategy = _fake_strategy()
        strategy.api = api

        self.assertIn(
            "超过可卖数量",
            worker._validate_manual_order(strategy, api, "TQQQ", "sell", 2, 70.0),
        )

    def test_validation_fails_closed_when_market_or_position_reads_raise(self):
        strategy = _fake_strategy()
        api = strategy.api
        api.get_realtime_quote.side_effect = RuntimeError("quote down")
        self.assertIn(
            "无法获取 TQQQ 实时行情",
            worker._validate_manual_order(strategy, api, "TQQQ", "buy", 2, 70.0),
        )

        api.get_realtime_quote.side_effect = None
        api.get_stock_position_qty.side_effect = RuntimeError("position down")
        self.assertIn(
            "无法获取 TQQQ 可卖持仓",
            worker._validate_manual_order(strategy, api, "TQQQ", "sell", 2, 70.0),
        )
        self.assertIn(
            "无法获取 TQQQ 当前持仓",
            worker._validate_manual_order(strategy, api, "TQQQ", "buy", 2, 70.0),
        )

    def test_buy_validation_checks_each_account_risk_limit(self):
        strategy = _fake_strategy()
        api = strategy.api

        strategy._get_account_risk_metrics.return_value = None
        self.assertIn(
            "无法获取账户风控数据",
            worker._validate_manual_order(strategy, api, "TQQQ", "buy", 1, 20.0),
        )

        strategy._get_account_risk_metrics.return_value = {
            "equity": 100.0, "gross_market_value": 190.0, "buying_power": 100.0,
        }
        self.assertIn(
            "总杠杆",
            worker._validate_manual_order(strategy, api, "TQQQ", "buy", 1, 20.0),
        )

        strategy.max_total_leverage = 10.0
        strategy.min_maintenance_margin_ratio = 0.5
        self.assertIn(
            "保证金比例",
            worker._validate_manual_order(strategy, api, "TQQQ", "buy", 1, 20.0),
        )

        strategy.min_maintenance_margin_ratio = 0.1
        strategy._get_account_risk_metrics.return_value["buying_power"] = 10.0
        self.assertIn(
            "可用购买力",
            worker._validate_manual_order(strategy, api, "TQQQ", "buy", 1, 20.0),
        )

    def test_manual_validation_does_not_apply_strategy_market_clock(self):
        api = Mock()
        api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        api.get_stock_position_qty.return_value = 0
        strategy = _fake_strategy()
        strategy.api = api
        strategy.is_trading_time.return_value = False

        self.assertIsNone(
            worker._validate_manual_order(strategy, api, "TQQQ", "buy", 2, 70.0)
        )
        strategy.is_trading_time.assert_not_called()


if __name__ == "__main__":
    unittest.main()
