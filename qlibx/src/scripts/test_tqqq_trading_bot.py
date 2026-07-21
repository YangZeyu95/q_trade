"""
Comprehensive TDD tests for TQQQ Trading Bot (TradingStrategy class).
Covers: load_stock_strategies, get_stock_strategy, record_trade,
        _get_last_trade_info, update_pending_orders, is_trading_time,
        is_near_close, get_signal_indicator, check_buy_conditions,
        check_sell_conditions, execute_strategy.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, call
import json
import os
import sys
import csv
import shutil
import tempfile
import threading
from datetime import datetime, date, timedelta
import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tqqq_trading_bot import TradingStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_strategy(**overrides):
    """Return a complete, valid strategy dict with sensible defaults."""
    base = {
        "name": "Test",
        "buy_point": 80.0,
        "sell_point": 100.0,
        "buy_total": 1000,
        "sell_total": 500,
        "buy_limit_price": 0.0,
        "sell_limit_price": 0.0,
        "buy_day_interval": 1,
        "sell_day_interval": 1,
        "buy_price_interval": 2.0,
        "max_position": 50.0,
        "fear_greed_buy": -50.0,
        "fear_greed_sell": 50.0,
    }
    base.update(overrides)
    return base


def _write_strategy_file(path, strategies: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(strategies, f)


def _write_trade_csv(path, rows, header=None):
    """Write a trade CSV file.  *rows* is a list of lists."""
    if header is None:
        header = ["timestamp", "symbol", "action", "quantity", "price",
                   "volume", "order_id", "status"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


class _BaseTradingTest(unittest.TestCase):
    """Common setUp/tearDown for tests that need a temp directory."""

    def setUp(self):
        self.mock_api = Mock()
        self.tmpdir = tempfile.mkdtemp()
        self.strategy_file = os.path.join(self.tmpdir, "strategy.json")
        self.risk_config_file = os.path.join(self.tmpdir, "risk.json")
        _write_strategy_file(self.strategy_file, {"TQQQ": _make_strategy()})
        self.ts = TradingStrategy(
            self.mock_api,
            strategy_file=self.strategy_file,
            risk_config_file=self.risk_config_file,
        )
        # Override trades_dir to use temp directory
        self.ts.trades_dir = os.path.join(self.tmpdir, "trades")
        os.makedirs(self.ts.trades_dir, exist_ok=True)
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": 100000.0,
            "buyPower": 100000.0,
        }
        self.mock_api.get_total_portfolio_value.return_value = 0.0

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===================================================================
# 1. load_stock_strategies
# ===================================================================

class TestLoadStrategies(_BaseTradingTest):

    def test_valid_file(self):
        strats = self.ts.load_stock_strategies()
        self.assertIn("TQQQ", strats)
        self.assertEqual(strats["TQQQ"]["buy_point"], 80.0)

    def test_missing_file(self):
        ts = TradingStrategy(self.mock_api, strategy_file="/nonexistent_path.json")
        self.assertEqual(ts.stock_strategies, {})

    def test_corrupt_json(self):
        with open(self.strategy_file, "w") as f:
            f.write("{bad json!!!")
        strats = self.ts.load_stock_strategies()
        self.assertEqual(strats, {})

    def test_incomplete_fields_filtered(self):
        """Stocks missing required fields are skipped."""
        data = {
            "GOOD": _make_strategy(),
            "BAD": {"name": "bad", "buy_point": 100.0},  # missing many fields
        }
        _write_strategy_file(self.strategy_file, data)
        strats = self.ts.load_stock_strategies()
        self.assertIn("GOOD", strats)
        self.assertNotIn("BAD", strats)

    def test_multiple_stocks(self):
        data = {
            "TQQQ": _make_strategy(buy_point=80.0),
            "AAPL": _make_strategy(buy_point=200.0),
            "QQQ": _make_strategy(buy_point=400.0),
        }
        _write_strategy_file(self.strategy_file, data)
        strats = self.ts.load_stock_strategies()
        self.assertEqual(len(strats), 3)

    def test_invalid_numeric_config_isolated_without_discarding_good_symbols(self):
        data = {
            "GOOD": _make_strategy(),
            "NAN": _make_strategy(max_position=float("nan")),
            "NEGATIVE": _make_strategy(buy_total=-1),
            "INVERTED": _make_strategy(fear_greed_buy=70, fear_greed_sell=-70),
            "NOT_OBJECT": [1, 2, 3],
        }
        _write_strategy_file(self.strategy_file, data)

        strats = self.ts.load_stock_strategies()

        self.assertEqual(list(strats), ["GOOD"])

    def test_numeric_strings_are_normalized_and_symbol_is_uppercase(self):
        config = _make_strategy()
        config.update({"buy_total": "1000", "max_position": "25.5"})
        _write_strategy_file(self.strategy_file, {" tqqq ": config})

        strategy = self.ts.load_stock_strategies()["TQQQ"]

        self.assertEqual(strategy["buy_total"], 1000)
        self.assertIsInstance(strategy["buy_total"], int)
        self.assertEqual(strategy["max_position"], 25.5)


class TestRiskConfig(_BaseTradingTest):

    def test_valid_risk_config_is_reloaded(self):
        with open(self.risk_config_file, "w", encoding="utf-8") as f:
            json.dump({
                "max_total_leverage": 1.25,
                "min_maintenance_margin_ratio": 0.45,
            }, f)

        self.ts.load_risk_config()

        self.assertEqual(self.ts.max_total_leverage, 1.25)
        self.assertEqual(self.ts.min_maintenance_margin_ratio, 0.45)

    def test_invalid_or_non_finite_risk_config_falls_back_to_defaults(self):
        invalid_configs = [
            {"max_total_leverage": 0, "min_maintenance_margin_ratio": 0.3},
            {"max_total_leverage": float("nan"), "min_maintenance_margin_ratio": 0.3},
            {"max_total_leverage": 2, "min_maintenance_margin_ratio": 1.1},
        ]
        for config in invalid_configs:
            with self.subTest(config=config):
                with open(self.risk_config_file, "w", encoding="utf-8") as f:
                    json.dump(config, f)
                self.ts.max_total_leverage = 9
                self.ts.min_maintenance_margin_ratio = 0.9
                self.ts.load_risk_config()
                self.assertEqual(self.ts.max_total_leverage, 2.0)
                self.assertEqual(self.ts.min_maintenance_margin_ratio, 0.30)


# ===================================================================
# 2. get_stock_strategy
# ===================================================================

class TestGetStockStrategy(_BaseTradingTest):

    def test_found(self):
        s = self.ts.get_stock_strategy("TQQQ")
        self.assertIsNotNone(s)
        self.assertEqual(s["buy_point"], 80.0)

    def test_not_found(self):
        s = self.ts.get_stock_strategy("NONEXISTENT")
        self.assertIsNone(s)


# ===================================================================
# 3. record_trade
# ===================================================================

class TestRecordTrade(_BaseTradingTest):

    def test_new_file_creates_header(self):
        self.ts.record_trade("TQQQ", "buy", 10, 85.0, 1000000, {"orderId": "abc"})
        filepath = os.path.join(self.ts.trades_dir, "tqqq_trading.csv")
        self.assertTrue(os.path.isfile(filepath))
        with open(filepath, "r") as f:
            lines = f.readlines()
        self.assertIn("timestamp", lines[0])  # header
        self.assertEqual(len(lines), 2)  # header + 1 record

    def test_append_to_existing(self):
        self.ts.record_trade("TQQQ", "buy", 10, 85.0, 1000000, "order1")
        self.ts.record_trade("TQQQ", "sell", 5, 90.0, 2000000, "order2")
        filepath = os.path.join(self.ts.trades_dir, "tqqq_trading.csv")
        with open(filepath, "r") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 3)  # header + 2 records

    def test_dict_order_result(self):
        self.ts.record_trade("TQQQ", "buy", 10, 85.0, 1000, {"orderId": "X123"})
        filepath = os.path.join(self.ts.trades_dir, "tqqq_trading.csv")
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["order_id"], "X123")

    def test_string_order_result(self):
        self.ts.record_trade("TQQQ", "buy", 10, 85.0, 1000, "simple_id")
        filepath = os.path.join(self.ts.trades_dir, "tqqq_trading.csv")
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["order_id"], "simple_id")

    def test_none_order_result(self):
        self.ts.record_trade("TQQQ", "buy", 10, 85.0, 1000, None)
        filepath = os.path.join(self.ts.trades_dir, "tqqq_trading.csv")
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["order_id"], "")

    def test_submit_order_uses_shared_path_and_nested_order_id(self):
        self.mock_api.place_order.return_value = {"data": {"data": "NESTED-1"}}

        result = self.ts.submit_order(
            symbol="TQQQ",
            action="buy",
            quantity=3,
            price="70.01",
            volume=1234,
            record_price=70.0,
        )

        self.assertEqual(result, {"data": {"data": "NESTED-1"}})
        self.mock_api.place_order.assert_called_once_with(
            exchangeType="P",
            stock_code="TQQQ",
            entrustAmount=3,
            entrustPrice="70.01",
            entrustBs="1",
            entrustType="3",
        )
        filepath = os.path.join(self.ts.trades_dir, "tqqq_trading.csv")
        with open(filepath, "r", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["order_id"], "NESTED-1")
        self.assertEqual(row["price"], "70.0")

    def test_submit_order_rejects_unknown_action_before_gateway_call(self):
        with self.assertRaises(ValueError):
            self.ts.submit_order("TQQQ", "hold", 3, "70.01")
        self.mock_api.place_order.assert_not_called()

    def test_trade_push_cannot_be_lost_between_early_lookup_and_initial_write(self):
        record_entered = threading.Event()
        push_finished = threading.Event()
        real_datetime = datetime

        def controlled_now(tz=None):
            if threading.current_thread().name == "record-thread":
                record_entered.set()
                # Before the fix, the push can finish while no CSV row exists.
                # With the fix it waits on the trade-log lock and updates after write.
                push_finished.wait(0.1)
            return real_datetime.now(tz)

        payload = type("Payload", (), {
            "recordNo": "RACE-1",
            "entrustNo": "",
            "entrustStatus": "8",
            "stockCode": "TQQQ",
            "businessAmount": "3",
            "leftAmount": "0",
        })()

        with patch("tqqq_trading_bot.datetime") as mocked_datetime:
            mocked_datetime.now.side_effect = controlled_now
            record_thread = threading.Thread(
                target=self.ts.record_trade,
                args=("TQQQ", "buy", 3, 70.0, 1000, {"orderId": "RACE-1"}),
                name="record-thread",
            )
            record_thread.start()
            self.assertTrue(record_entered.wait(1))

            def send_push():
                self.ts.handle_trade_push(payload)
                push_finished.set()

            push_thread = threading.Thread(target=send_push, name="push-thread")
            push_thread.start()
            record_thread.join(2)
            push_thread.join(2)

        self.assertFalse(record_thread.is_alive())
        self.assertFalse(push_thread.is_alive())
        path = os.path.join(self.ts.trades_dir, "tqqq_trading.csv")
        with open(path, "r", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["status"], "Filled")
        self.assertNotIn("RACE-1", self.ts._early_trade_pushes)


# ===================================================================
# 4. _get_last_trade_info / get_last_buy_date / get_last_buy_price / etc.
# ===================================================================

class TestGetLastTradeInfo(_BaseTradingTest):

    def _csv_path(self, symbol="TQQQ"):
        return os.path.join(self.ts.trades_dir, f"{symbol.lower()}_trading.csv")

    def test_no_file_returns_none(self):
        self.assertIsNone(self.ts.get_last_buy_date("TQQQ"))
        self.assertIsNone(self.ts.get_last_buy_price("TQQQ"))
        self.assertIsNone(self.ts.get_last_sell_date("TQQQ"))
        self.assertIsNone(self.ts.get_last_sell_price("TQQQ"))

    def test_buy_price_from_csv(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00-05:00", "TQQQ", "buy", 10, 80.0, 1000, "o1", "Filled"],
            ["2026-01-02T10:00:00-05:00", "TQQQ", "buy", 5, 85.0, 2000, "o2", "Filled"],
        ])
        self.assertEqual(self.ts.get_last_buy_price("TQQQ"), 85.0)

    def test_buy_date_from_csv(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00-05:00", "TQQQ", "buy", 10, 80.0, 1000, "o1", "Filled"],
            ["2026-01-05T10:00:00-05:00", "TQQQ", "buy", 5, 85.0, 2000, "o2", "Filled"],
        ])
        self.assertEqual(self.ts.get_last_buy_date("TQQQ"), date(2026, 1, 5))

    def test_sell_price_from_csv(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00-05:00", "TQQQ", "buy", 10, 80.0, 1000, "o1", "Filled"],
            ["2026-01-03T10:00:00-05:00", "TQQQ", "sell", 5, 95.0, 2000, "o2", "Filled"],
        ])
        self.assertEqual(self.ts.get_last_sell_price("TQQQ"), 95.0)

    def test_sell_date_from_csv(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-03T10:00:00-05:00", "TQQQ", "sell", 5, 95.0, 2000, "o2", "Filled"],
        ])
        self.assertEqual(self.ts.get_last_sell_date("TQQQ"), date(2026, 1, 3))

    def test_ignores_wrong_action(self):
        """get_last_buy_price ignores sell rows."""
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00-05:00", "TQQQ", "sell", 5, 95.0, 2000, "o1", "Filled"],
        ])
        self.assertIsNone(self.ts.get_last_buy_price("TQQQ"))

    def test_hs_snapshot_keeps_latest_buy_and_sell_separate(self):
        self.ts._hs_trade_history_available = True
        self.ts._hs_trade_history_complete = True
        self.ts._hs_trade_history = {
            "TQQQ": {
                "buy": {"date": "2026-07-08", "price": 80.5},
                "sell": {"date": "2026-07-19", "price": 95.25},
            }
        }

        self.assertEqual(self.ts.get_last_buy_date("US.TQQQ"), date(2026, 7, 8))
        self.assertEqual(self.ts.get_last_buy_price("TQQQ"), 80.5)
        self.assertEqual(self.ts.get_last_sell_date("TQQQ"), date(2026, 7, 19))
        self.assertEqual(self.ts.get_last_sell_price("TQQQ"), 95.25)

    def test_authoritative_hs_snapshot_does_not_fall_back_to_local_other_side(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00-05:00", "TQQQ", "buy", 10, 80.0, 1000, "o1", "Filled"],
        ])
        self.ts._hs_trade_history_available = True
        self.ts._hs_trade_history_complete = True
        self.ts._hs_trade_history = {
            "TQQQ": {"sell": {"date": "2026-07-19", "price": 95.25}}
        }

        self.assertIsNone(self.ts.get_last_buy_date("TQQQ"))
        self.assertIsNone(self.ts.get_last_buy_price("TQQQ"))
        self.assertEqual(self.ts.get_last_sell_price("TQQQ"), 95.25)

    def test_missing_side_is_unknown_until_hs_backfill_completes(self):
        self.ts._hs_trade_history_available = True
        self.ts._hs_trade_history_complete = False
        self.ts._hs_trade_history = {}

        with self.assertRaisesRegex(RuntimeError, "历史仍在回填"):
            self.ts.get_last_buy_date("TQQQ")

    def test_malformed_lines_skipped(self):
        """Lines with insufficient columns are skipped gracefully."""
        path = self._csv_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write("timestamp,symbol,action,quantity,price,volume,order_id,status\n")
            f.write("bad_line\n")
            f.write("2026-01-02T10:00:00-05:00,TQQQ,buy,10,82.0,1000,o1,Filled\n")
        self.assertEqual(self.ts.get_last_buy_price("TQQQ"), 82.0)

    def test_empty_file_returns_none(self):
        path = self._csv_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write("timestamp,symbol,action,quantity,price,volume,order_id,status\n")
        self.assertIsNone(self.ts.get_last_buy_price("TQQQ"))

    def test_rejected_latest_order_is_not_used_as_trade_history(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00-05:00", "TQQQ", "buy", 10, 80.0, 1000, "o1", "Filled"],
            ["2026-01-02T10:00:00-05:00", "TQQQ", "buy", 10, 85.0, 1000, "o2", "Rejected"],
        ])
        self.assertEqual(self.ts.get_last_buy_price("TQQQ"), 80.0)

    def test_unfilled_order_is_not_used_as_trade_history(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00-05:00", "TQQQ", "buy", 10, 80.0, 1000, "o1", "Filled"],
            ["2026-01-02T10:00:00-05:00", "TQQQ", "buy", 10, 85.0, 1000, "o2", "Submitted"],
        ])
        self.assertEqual(self.ts.get_last_buy_price("TQQQ"), 80.0)
        self.assertEqual(self.ts.get_last_buy_date("TQQQ"), date(2026, 1, 1))

    def test_partial_fill_uses_execution_price_from_csv(self):
        header = [
            "timestamp", "symbol", "action", "quantity", "price", "volume",
            "order_id", "status", "business_price",
        ]
        _write_trade_csv(self._csv_path(), [[
            "2026-01-02T10:00:00-05:00", "TQQQ", "buy", 10, 85.0,
            1000, "o2", "Partial", 84.5,
        ]], header=header)
        self.assertEqual(self.ts.get_last_buy_price("TQQQ"), 84.5)

    def test_non_finite_latest_execution_price_is_skipped(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00-05:00", "TQQQ", "buy", 10, 80.0, 1000, "o1", "Filled"],
            ["2026-01-02T10:00:00-05:00", "TQQQ", "buy", 10, "nan", 1000, "o2", "Filled"],
        ])
        self.assertEqual(self.ts.get_last_buy_price("TQQQ"), 80.0)


# ===================================================================
# 5. update_pending_orders
# ===================================================================

class TestUpdatePendingOrders(_BaseTradingTest):

    def _csv_path(self, symbol="TQQQ"):
        return os.path.join(self.ts.trades_dir, f"{symbol.lower()}_trading.csv")

    def test_updates_submitted_to_filled(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00", "TQQQ", "buy", 10, 80.0, 1000, "ORD1", "Submitted"],
        ])
        self.mock_api.get_order_details.return_value = {
            "orderStatusName": "Filled"
        }
        self.ts.update_pending_orders()

        with open(self._csv_path(), "r") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["status"], "Filled")

    def test_no_pending_orders(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00", "TQQQ", "buy", 10, 80.0, 1000, "ORD1", "Filled"],
        ])
        self.ts.update_pending_orders()
        self.mock_api.get_order_details.assert_not_called()

    def test_api_failure_does_not_crash(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00", "TQQQ", "buy", 10, 80.0, 1000, "ORD1", "Submitted"],
        ])
        self.mock_api.get_order_details.side_effect = Exception("API down")
        # Should not raise
        self.ts.update_pending_orders()

    def test_missing_trades_dir(self):
        shutil.rmtree(self.ts.trades_dir)
        # Should not raise
        self.ts.update_pending_orders()

    def test_partial_status_also_updated(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00", "TQQQ", "buy", 10, 80.0, 1000, "ORD1", "Partial"],
        ])
        self.mock_api.get_order_details.return_value = {"orderStatusName": "Filled"}
        self.ts.update_pending_orders()
        with open(self._csv_path(), "r") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["status"], "Filled")

    def test_no_change_when_status_same(self):
        """File should not be rewritten when status hasn't changed."""
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00", "TQQQ", "buy", 10, 80.0, 1000, "ORD1", "Submitted"],
        ])
        self.mock_api.get_order_details.return_value = {"orderStatusName": "Submitted"}
        mtime_before = os.path.getmtime(self._csv_path())
        self.ts.update_pending_orders()
        mtime_after = os.path.getmtime(self._csv_path())
        self.assertEqual(mtime_before, mtime_after)

    def test_pending_order_helpers_use_remaining_buy_exposure(self):
        header = [
            "timestamp", "symbol", "action", "quantity", "price", "volume",
            "order_id", "status", "stock_name", "filled_quantity",
            "remaining_quantity", "business_price", "entrust_price",
            "entrust_quantity", "record_no", "entrust_no", "status_updated_at",
        ]
        _write_trade_csv(
            self._csv_path(),
            [[
                "2026-01-01T10:00:00", "TQQQ", "buy", 10, 80.0, 1000,
                "ORD1", "Partial", "", "8", "2", "80", "81", "10", "", "", "",
            ]],
            header=header,
        )

        pending = self.ts._get_pending_orders()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["quantity"], 2.0)
        self.assertEqual(pending[0]["notional"], 162.0)
        self.assertTrue(self.ts._has_pending_order("tqqq"))
        self.assertEqual(self.ts._get_pending_buy_exposure(), (162.0, {"TQQQ": 162.0}))

    def test_terminal_order_does_not_count_as_pending(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00", "TQQQ", "buy", 10, 80.0, 1000, "ORD1", "Canceled"],
        ])
        self.assertEqual(self.ts._get_pending_orders(), [])


# ===================================================================
# 6. is_trading_time / is_near_close
# ===================================================================

class TestTradingTime(_BaseTradingTest):

    def _make_et_datetime(self, year, month, day, hour, minute):
        et = pytz.timezone("America/New_York")
        return et.localize(datetime(year, month, day, hour, minute, 0))

    def test_weekday_in_hours(self):
        # Thursday 10:00 AM ET → trading time
        mock_now = self._make_et_datetime(2026, 3, 5, 10, 0)  # Thursday
        with patch("tqqq_trading_bot.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            self.assertTrue(self.ts.is_trading_time())

    def test_weekend(self):
        mock_now = self._make_et_datetime(2026, 3, 7, 10, 0)  # Saturday
        with patch("tqqq_trading_bot.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            self.assertFalse(self.ts.is_trading_time())

    def test_before_market_open(self):
        mock_now = self._make_et_datetime(2026, 3, 5, 9, 0)  # 9:00 AM, before 9:30
        with patch("tqqq_trading_bot.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            self.assertFalse(self.ts.is_trading_time())

    def test_after_market_close(self):
        mock_now = self._make_et_datetime(2026, 3, 5, 16, 30)  # 4:30 PM
        with patch("tqqq_trading_bot.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            self.assertFalse(self.ts.is_trading_time())

    def test_at_market_open_boundary(self):
        mock_now = self._make_et_datetime(2026, 3, 5, 9, 30)  # Exactly 9:30
        with patch("tqqq_trading_bot.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            self.assertTrue(self.ts.is_trading_time())

    def test_at_market_close_boundary(self):
        mock_now = self._make_et_datetime(2026, 3, 5, 16, 0)  # Exactly 16:00
        with patch("tqqq_trading_bot.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            self.assertFalse(self.ts.is_trading_time())


class TestSharedMarketSnapshot(_BaseTradingTest):

    def test_valid_snapshot_normalizes_quotes_positions_and_risk_metrics(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "complete": True,
            "stale": False,
            "realtime": {
                "US.TQQQ": {
                    "regularLastPrice": "70.5",
                    "lastPrice": "71",
                    "volume": "1234",
                    "signal": "-60",
                    "signal_available": True,
                },
                "AAPL": {"lastPrice": 200, "signal_available": False},
            },
            "holdings": [
                {"stockCode": "TQQQ.US", "enableAmount": "12"},
                {"stockCode": "BAD", "enableAmount": "nan"},
            ],
            "account": {
                "total_asset": "100000",
                "buying_power": "50000",
                "market_value": "25000",
            },
            "trade_history_available": True,
            "trade_history_complete": True,
            "trade_history": {
                "TQQQ": {
                    "buy": {"date": "2026-07-08", "price": 68.0},
                    "sell": {"date": "2026-07-18", "price": 72.0},
                }
            },
            "active_orders_available": True,
            "active_orders": [
                {
                    "order_id": "OPEN-1", "symbol": "MSTU", "action": "buy",
                    "status": "已报", "quantity": 10, "price": 1.5,
                }
            ],
        }

        with patch("tqqq_trading_bot.requests.get", return_value=response) as get:
            snapshot = self.ts.get_shared_market_snapshot()

        get.assert_called_once_with(
            "http://localhost:8000/api/market/snapshot?force=true", timeout=30
        )
        self.assertEqual(snapshot["quotes"]["TQQQ"]["lastPrice"], "70.5")
        self.assertEqual(snapshot["signals"], {"TQQQ": "-60"})
        self.assertEqual(snapshot["positions"], {"TQQQ": 12, "BAD": 0})
        self.assertEqual(snapshot["risk_metrics"]["gross_market_value"], 25000.0)
        self.assertTrue(snapshot["trade_history_available"])
        self.assertTrue(snapshot["trade_history_complete"])
        self.assertEqual(snapshot["trade_history"]["TQQQ"]["buy"]["price"], 68.0)
        self.assertTrue(snapshot["active_orders_available"])
        self.assertEqual(snapshot["active_orders"][0]["order_id"], "OPEN-1")

    def test_snapshot_without_current_entrust_is_rejected(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "complete": True,
            "stale": False,
            "realtime": {},
            "holdings": [],
            "account": {
                "total_asset": "100000", "buying_power": "50000",
                "market_value": "0",
            },
        }

        with patch("tqqq_trading_bot.requests.get", return_value=response):
            self.assertIsNone(self.ts.get_shared_market_snapshot())

    def test_stale_or_incomplete_snapshot_is_rejected(self):
        for payload in (
            {"complete": False, "stale": False},
            {"complete": True, "stale": True},
            ["not", "a", "mapping"],
        ):
            with self.subTest(payload=payload):
                response = Mock(status_code=200)
                response.json.return_value = payload
                with patch("tqqq_trading_bot.requests.get", return_value=response):
                    self.assertIsNone(self.ts.get_shared_market_snapshot())

    def test_malformed_active_order_snapshot_is_rejected(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "complete": True,
            "stale": False,
            "realtime": {},
            "holdings": [],
            "account": {
                "total_asset": 100000,
                "buying_power": 100000,
                "market_value": 0,
            },
            "trade_history": {},
            "trade_history_available": True,
            "trade_history_complete": True,
            "active_orders_available": True,
            "active_orders": [{"order_id": "", "symbol": "TQQQ", "action": "buy"}],
        }

        with patch("tqqq_trading_bot.requests.get", return_value=response):
            self.assertIsNone(self.ts.get_shared_market_snapshot())

    def test_invalid_account_snapshot_fails_closed_for_buy_risk(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "complete": True,
            "stale": False,
            "realtime": {},
            "holdings": [],
            "account": {
                "total_asset": "nan",
                "buying_power": 1000,
                "market_value": -1,
            },
            "active_orders_available": True,
            "active_orders": [],
        }
        with patch("tqqq_trading_bot.requests.get", return_value=response):
            snapshot = self.ts.get_shared_market_snapshot()
        self.assertIsNone(snapshot["risk_metrics"])

    def test_http_failure_or_exception_returns_none(self):
        response = Mock(status_code=503)
        with patch("tqqq_trading_bot.requests.get", return_value=response):
            self.assertIsNone(self.ts.get_shared_market_snapshot())
        with patch(
            "tqqq_trading_bot.requests.get", side_effect=TimeoutError("backend down")
        ):
            self.assertIsNone(self.ts.get_shared_market_snapshot())

class TestIsNearClose(_BaseTradingTest):

    def _make_et_datetime(self, year, month, day, hour, minute):
        et = pytz.timezone("America/New_York")
        return et.localize(datetime(year, month, day, hour, minute, 0))

    def test_5_min_before_close(self):
        mock_now = self._make_et_datetime(2026, 3, 5, 15, 55)
        with patch("tqqq_trading_bot.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = mock_now
            self.assertTrue(self.ts.is_near_close(minutes_before=10))

    def test_30_min_before_close_not_near(self):
        mock_now = self._make_et_datetime(2026, 3, 5, 15, 30)
        with patch("tqqq_trading_bot.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = mock_now
            self.assertFalse(self.ts.is_near_close(minutes_before=10))

    def test_half_day_uses_one_pm_close(self):
        near_close = self._make_et_datetime(2026, 11, 27, 12, 55)
        after_close = self._make_et_datetime(2026, 11, 27, 13, 5)
        with patch("tqqq_trading_bot.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = near_close
            self.assertTrue(self.ts.is_near_close(minutes_before=10))
            mock_dt.now.return_value = after_close
            self.assertFalse(self.ts.is_near_close(minutes_before=10))

    def test_after_close_not_near(self):
        mock_now = self._make_et_datetime(2026, 3, 5, 16, 5)
        with patch("tqqq_trading_bot.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = mock_now
            self.assertFalse(self.ts.is_near_close(minutes_before=10))


# ===================================================================
# 7. get_signal_indicator
# ===================================================================

class TestSignalIndicator(_BaseTradingTest):

    @patch("tqqq_trading_bot.requests.get")
    def test_success(self, mock_get):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"TQQQ": {"signal": -42.5}}
        mock_get.return_value = mock_resp
        self.assertEqual(self.ts.get_signal_indicator("TQQQ"), -42.5)

    @patch("tqqq_trading_bot.requests.get")
    def test_http_error(self, mock_get):
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp
        self.assertIsNone(self.ts.get_signal_indicator("TQQQ"))

    @patch("tqqq_trading_bot.requests.get")
    def test_timeout(self, mock_get):
        mock_get.side_effect = Exception("Timeout")
        self.assertIsNone(self.ts.get_signal_indicator("TQQQ"))

    @patch("tqqq_trading_bot.requests.get")
    def test_symbol_not_in_response(self, mock_get):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"AAPL": {"signal": 10.0}}
        mock_get.return_value = mock_resp
        self.assertIsNone(self.ts.get_signal_indicator("TQQQ"))


# ===================================================================
# 8. check_buy_conditions
# ===================================================================

class TestCheckBuyConditions(_BaseTradingTest):

    def _csv_path(self):
        return os.path.join(self.ts.trades_dir, "tqqq_trading.csv")

    def test_first_buy_no_history(self):
        """No trade history → should be allowed."""
        strat = _make_strategy(buy_day_interval=1, buy_price_interval=2.0)
        self.assertTrue(self.ts.check_buy_conditions("TQQQ", strat, 75.0))

    def test_day_interval_not_met(self):
        today = datetime.now(self.ts.et_tz).date()
        _write_trade_csv(self._csv_path(), [
            [f"{today.isoformat()}T10:00:00-05:00", "TQQQ", "buy", 10, 80.0, 1000, "o1", "Filled"],
        ])
        strat = _make_strategy(buy_day_interval=3, buy_price_interval=0)
        self.assertFalse(self.ts.check_buy_conditions("TQQQ", strat, 75.0))

    def test_day_interval_met(self):
        old_date = datetime.now(self.ts.et_tz).date() - timedelta(days=5)
        _write_trade_csv(self._csv_path(), [
            [f"{old_date.isoformat()}T10:00:00-05:00", "TQQQ", "buy", 10, 80.0, 1000, "o1", "Filled"],
        ])
        strat = _make_strategy(buy_day_interval=3, buy_price_interval=0)
        self.assertTrue(self.ts.check_buy_conditions("TQQQ", strat, 75.0))

    def test_price_interval_not_met(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00-05:00", "TQQQ", "buy", 10, 100.0, 1000, "o1", "Filled"],
        ])
        strat = _make_strategy(buy_day_interval=0, buy_price_interval=5.0)
        # Price diff = |99 - 100| / 100 = 1% < 5%
        self.assertFalse(self.ts.check_buy_conditions("TQQQ", strat, 99.0))

    def test_price_interval_met(self):
        _write_trade_csv(self._csv_path(), [
            ["2026-01-01T10:00:00-05:00", "TQQQ", "buy", 10, 100.0, 1000, "o1", "Filled"],
        ])
        strat = _make_strategy(buy_day_interval=0, buy_price_interval=5.0)
        # Price diff = |90 - 100| / 100 = 10% > 5%
        self.assertTrue(self.ts.check_buy_conditions("TQQQ", strat, 90.0))

    def test_zero_interval_no_restriction(self):
        strat = _make_strategy(buy_day_interval=0, buy_price_interval=0)
        self.assertTrue(self.ts.check_buy_conditions("TQQQ", strat, 75.0))


# ===================================================================
# 9. check_sell_conditions
# ===================================================================

class TestCheckSellConditions(_BaseTradingTest):

    def _csv_path(self):
        return os.path.join(self.ts.trades_dir, "tqqq_trading.csv")

    def test_no_history(self):
        strat = _make_strategy(sell_day_interval=1)
        self.assertTrue(self.ts.check_sell_conditions("TQQQ", strat, 110.0))

    def test_day_interval_not_met(self):
        today = datetime.now(self.ts.et_tz).date()
        _write_trade_csv(self._csv_path(), [
            [f"{today.isoformat()}T10:00:00-05:00", "TQQQ", "sell", 5, 100.0, 1000, "o1", "Filled"],
        ])
        strat = _make_strategy(sell_day_interval=3)
        self.assertFalse(self.ts.check_sell_conditions("TQQQ", strat, 110.0))

    def test_day_interval_met(self):
        old_date = datetime.now(self.ts.et_tz).date() - timedelta(days=5)
        _write_trade_csv(self._csv_path(), [
            [f"{old_date.isoformat()}T10:00:00-05:00", "TQQQ", "sell", 5, 100.0, 1000, "o1", "Filled"],
        ])
        strat = _make_strategy(sell_day_interval=3)
        self.assertTrue(self.ts.check_sell_conditions("TQQQ", strat, 110.0))

    def test_zero_interval_no_restriction(self):
        strat = _make_strategy(sell_day_interval=0)
        self.assertTrue(self.ts.check_sell_conditions("TQQQ", strat, 110.0))


# ===================================================================
# 10. execute_strategy — integration-level tests
# ===================================================================

class TestExecuteStrategy(_BaseTradingTest):
    """Higher-level tests for the full execute_strategy flow.

    is_trading_time and is_near_close are mocked to True by default.
    """

    def setUp(self):
        super().setUp()
        self.ts.is_trading_time = Mock(return_value=True)
        self.ts.is_near_close = Mock(return_value=True)
        # Defaults for API
        self.mock_api.get_stock_position_qty.return_value = 0
        self.mock_api.get_total_portfolio_value.return_value = 100000.0

    # ---- Gating conditions -------------------------------------------------

    def test_not_trading_time(self):
        self.ts.is_trading_time.return_value = False
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 50.0, "volume": 1000}
        self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()
        self.mock_api.get_realtime_quote.assert_not_called()

    def test_not_near_close(self):
        self.ts.is_near_close.return_value = False
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 50.0, "volume": 1000}
        self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_zero_price_skipped(self):
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 0, "volume": 1000}
        self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_negative_price_skipped(self):
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": -5.0, "volume": 1000}
        self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_non_finite_price_skipped(self):
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": float("nan"), "volume": 1000}
        self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_no_strategy_found(self):
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 50.0, "volume": 1000}
        self.ts.execute_strategy("UNKNOWN")
        self.mock_api.place_order.assert_not_called()

    def test_existing_pending_order_blocks_same_symbol(self):
        _write_trade_csv(os.path.join(self.ts.trades_dir, "tqqq_trading.csv"), [
            ["2026-01-01T10:00:00", "TQQQ", "buy", 10, 70.0, 1000, "PENDING", "Submitted"],
        ])
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.get_realtime_quote.assert_not_called()
        self.mock_api.place_order.assert_not_called()

    def test_hs_active_order_blocks_same_symbol_without_local_record(self):
        snapshot = {
            "quotes": {"TQQQ": {"lastPrice": 70.0, "volume": 1000}},
            "signals": {"TQQQ": -60.0},
            "positions": {"TQQQ": 0},
            "risk_metrics": {
                "equity": 100000.0,
                "buying_power": 100000.0,
                "gross_market_value": 0.0,
            },
            "trade_history_available": True,
            "trade_history_complete": True,
            "trade_history": {},
            "active_orders_available": True,
            "active_orders": [{
                "order_id": "HS-OPEN-1", "symbol": "US.TQQQ", "action": "buy",
                "status": "已报", "quantity": 10, "price": 70,
            }],
        }

        self.ts.execute_strategy("TQQQ", snapshot=snapshot)

        self.mock_api.get_realtime_quote.assert_not_called()
        self.mock_api.place_order.assert_not_called()

    def test_hs_order_overrides_same_local_order_for_pending_exposure(self):
        _write_trade_csv(os.path.join(self.ts.trades_dir, "tqqq_trading.csv"), [
            ["2026-01-01T10:00:00", "TQQQ", "buy", 10, 70.0, 1000, "SAME-1", "Submitted"],
        ])
        self.ts._hs_active_orders_available = True
        self.ts._hs_active_orders = [{
            "order_id": "SAME-1", "symbol": "TQQQ", "action": "buy",
            "status": "部分成交", "quantity": 4, "price": 70,
        }]

        total, by_symbol = self.ts._get_pending_buy_exposure()

        self.assertEqual(total, 280.0)
        self.assertEqual(by_symbol, {"TQQQ": 280.0})

    def test_hs_active_order_for_other_symbol_counts_toward_total_exposure(self):
        self.ts._hs_active_orders_available = True
        self.ts._hs_active_orders = [{
            "order_id": "HS-AAPL-1", "symbol": "US.AAPL", "action": "buy",
            "status": "已报", "quantity": 5, "price": 200,
        }]

        total, by_symbol = self.ts._get_pending_buy_exposure()

        self.assertEqual(total, 1000.0)
        self.assertEqual(by_symbol, {"AAPL": 1000.0})

    def test_terminal_order_does_not_block_same_symbol(self):
        _write_trade_csv(os.path.join(self.ts.trades_dir, "tqqq_trading.csv"), [
            ["2026-01-01T10:00:00", "TQQQ", "buy", 10, 70.0, 1000, "DONE", "Canceled"],
        ])
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.place_order.return_value = {"orderId": "BUY-DONE"}
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0), \
             patch.object(self.ts, "check_buy_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_called_once()

    def test_quote_api_exception(self):
        self.mock_api.get_realtime_quote.side_effect = Exception("API down")
        self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_none_quote(self):
        self.mock_api.get_realtime_quote.return_value = None
        self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    # ---- Buy flow -----------------------------------------------------------

    def test_buy_order_placed(self):
        """Price below buy_point AND signal fearful → buy."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.place_order.return_value = {"orderId": "BUY1"}
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0), \
             patch.object(self.ts, "check_buy_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_called_once()
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustBs"], "1")  # Buy side

    def test_buy_blocked_by_signal(self):
        """Price below buy_point BUT signal neutral → no buy."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        with patch.object(self.ts, "get_signal_indicator", return_value=0.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_buy_total_too_small(self):
        """buy_total / price < 1 → quantity 0 → skip."""
        data = {"TQQQ": _make_strategy(buy_total=10, buy_point=80.0)}
        _write_strategy_file(self.strategy_file, data)
        self.ts.stock_strategies = self.ts.load_stock_strategies()
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_buy_blocked_by_max_position(self):
        """Position already at max → no buy."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 100  # 100 * 70 = 7000
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": 10000.0,
            "buyPower": 100000.0,
        }  # 70% > 50% max
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_buy_position_limit_uses_account_equity(self):
        """Cash and margin capacity must not inflate the position limit."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 100  # $7,000
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": 100000.0,
            "buyPower": 100000.0,
        }
        self.mock_api.place_order.return_value = {"orderId": "EQ1"}
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0), \
             patch.object(self.ts, "check_buy_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_called_once()

    def test_buy_blocked_by_projected_position_limit(self):
        """The new order itself must fit below the equity-based limit."""
        data = {"TQQQ": _make_strategy(buy_total=1000, max_position=10.0)}
        _write_strategy_file(self.strategy_file, data)
        self.ts.stock_strategies = self.ts.load_stock_strategies()
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 130  # $9,100
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": 100000.0,
            "buyPower": 100000.0,
        }
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_buy_blocked_by_buying_power(self):
        """Borrowing capacity is still limited by the account's buying power."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": 100000.0,
            "buyPower": 100.0,
        }
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_buy_blocked_by_non_finite_account_risk_data(self):
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": float("nan"),
            "buyPower": 100000.0,
        }
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_buy_blocked_by_total_leverage(self):
        """The projected gross exposure must stay below total leverage."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": 100000.0,
            "buyPower": 100000.0,
        }
        self.mock_api.get_total_portfolio_value.return_value = 199500.0
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_buy_blocked_by_pending_buy_total_leverage(self):
        """Outstanding buy orders count toward projected gross exposure."""
        _write_trade_csv(os.path.join(self.ts.trades_dir, "aapl_trading.csv"), [
            ["2026-01-01T10:00:00", "AAPL", "buy", 100, 200.0, 1000, "PENDING-A", "Submitted"],
        ])
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.get_total_portfolio_value.return_value = 180000.0
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": 100000.0,
            "buyPower": 100000.0,
        }
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_buy_blocked_by_pending_buy_buying_power(self):
        """Outstanding buy orders count toward the available buying-power check."""
        _write_trade_csv(os.path.join(self.ts.trades_dir, "aapl_trading.csv"), [
            ["2026-01-01T10:00:00", "AAPL", "buy", 5, 100.0, 1000, "PENDING-A", "Submitted"],
        ])
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": 100000.0,
            "buyPower": 1000.0,
        }
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_buy_blocked_by_maintenance_margin_ratio(self):
        """The projected equity-to-exposure ratio must stay above the floor."""
        self.ts.max_total_leverage = 10.0
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": 100000.0,
            "buyPower": 100000.0,
        }
        self.mock_api.get_total_portfolio_value.return_value = 350000.0
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_buy_entrust_price_is_last_price_plus_one_cent(self):
        """No limit price → use lastPrice + $0.01."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.place_order.return_value = {"orderId": "B1"}
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0), \
             patch.object(self.ts, "check_buy_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustPrice"], "70.01")

    def test_buy_uses_limit_price_when_set(self):
        """buy_limit_price > 0 → use that limit price."""
        data = {"TQQQ": _make_strategy(buy_limit_price=65.0)}
        _write_strategy_file(self.strategy_file, data)
        self.ts.stock_strategies = self.ts.load_stock_strategies()
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.place_order.return_value = {"orderId": "B2"}
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0), \
             patch.object(self.ts, "check_buy_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustPrice"], "65.0")

    # ---- Sell flow ----------------------------------------------------------

    def test_sell_by_price_and_signal(self):
        """Price above sell_point AND signal greedy → sell."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 110.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 20
        self.mock_api.place_order.return_value = {"orderId": "S1"}
        with patch.object(self.ts, "get_signal_indicator", return_value=60.0), \
             patch.object(self.ts, "check_sell_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_called_once()
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustBs"], "2")  # Sell side

    def test_sell_blocked_when_only_signal_met(self):
        """Signal greedy but price below sell_point → no sell."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 90.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 10
        with patch.object(self.ts, "get_signal_indicator", return_value=60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_sell_blocked_when_only_price_met(self):
        """Price above sell_point but signal not greedy → no sell."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 110.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 10
        with patch.object(self.ts, "get_signal_indicator", return_value=0.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_sell_total_zero_skips_sell(self):
        """sell_total = 0 must not trigger a dangerous full-position sell."""
        data = {"TQQQ": _make_strategy(sell_total=0)}
        _write_strategy_file(self.strategy_file, data)
        self.ts.stock_strategies = self.ts.load_stock_strategies()
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 110.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 25
        with patch.object(self.ts, "get_signal_indicator", return_value=60.0), \
             patch.object(self.ts, "check_sell_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_sell_quantity_capped_at_position(self):
        """sell_total / price > position_qty → sell only position_qty."""
        data = {"TQQQ": _make_strategy(sell_total=10000, sell_point=100.0)}
        _write_strategy_file(self.strategy_file, data)
        self.ts.stock_strategies = self.ts.load_stock_strategies()
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 110.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 5  # Only 5 shares
        self.mock_api.place_order.return_value = {"orderId": "S4"}
        with patch.object(self.ts, "get_signal_indicator", return_value=60.0), \
             patch.object(self.ts, "check_sell_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustAmount"], 5)

    def test_sell_no_position(self):
        """Sell condition met but position_qty = 0 → no order."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 110.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 0
        with patch.object(self.ts, "get_signal_indicator", return_value=60.0):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_sell_entrust_price_is_last_price_minus_one_cent(self):
        """No limit price → use lastPrice - $0.01."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 110.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 10
        self.mock_api.place_order.return_value = {"orderId": "S5"}
        with patch.object(self.ts, "get_signal_indicator", return_value=60.0), \
             patch.object(self.ts, "check_sell_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustPrice"], "109.99")

    def test_sell_uses_limit_price_when_set(self):
        """sell_limit_price > 0 → use that limit price."""
        data = {"TQQQ": _make_strategy(sell_limit_price=115.0)}
        _write_strategy_file(self.strategy_file, data)
        self.ts.stock_strategies = self.ts.load_stock_strategies()
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 110.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 10
        self.mock_api.place_order.return_value = {"orderId": "S6"}
        with patch.object(self.ts, "get_signal_indicator", return_value=60.0), \
             patch.object(self.ts, "check_sell_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustPrice"], "115.0")

    # ---- Dry run ------------------------------------------------------------

    def test_dry_run_buy(self):
        self.ts.dry_run = True
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0), \
             patch.object(self.ts, "check_buy_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_dry_run_sell(self):
        self.ts.dry_run = True
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 110.0, "volume": 1000}
        self.mock_api.get_stock_position_qty.return_value = 10
        with patch.object(self.ts, "get_signal_indicator", return_value=60.0), \
             patch.object(self.ts, "check_sell_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    # ---- Signal parsing edge cases ------------------------------------------

    def test_non_numeric_signal_defaults_to_zero(self):
        """Non-numeric signal should default to 0 and not crash."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        with patch.object(self.ts, "get_signal_indicator", return_value="INVALID"):
            self.ts.execute_strategy("TQQQ")
        # Should not crash; signal_value becomes 0, price 70 < buy_point 80
        # but signal 0 > fear_greed_buy -50 → buy condition not met → no order
        self.mock_api.place_order.assert_not_called()

    def test_non_finite_signal_defaults_to_zero(self):
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        with patch.object(self.ts, "get_signal_indicator", return_value=float("nan")):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    def test_unavailable_signal_blocks_all_trading(self):
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        with patch.object(self.ts, "get_signal_indicator", return_value=None):
            self.ts.execute_strategy("TQQQ")
        self.mock_api.place_order.assert_not_called()

    # ---- Multi-stock isolation ----------------------------------------------

    def test_exception_in_one_stock_does_not_break_others(self):
        """API error for TQQQ should not prevent AAPL execution."""
        data = {
            "TQQQ": _make_strategy(),
            "AAPL": _make_strategy(buy_point=200.0),
        }
        _write_strategy_file(self.strategy_file, data)
        self.ts.stock_strategies = self.ts.load_stock_strategies()

        self.mock_api.get_realtime_quote.side_effect = [
            Exception("TQQQ API down"),
            {"lastPrice": 190.0, "volume": 1000},
        ]
        self.mock_api.place_order.return_value = {"orderId": "A1"}

        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0), \
             patch.object(self.ts, "check_buy_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")  # Should not raise
            self.ts.execute_strategy("AAPL")  # Should proceed

        # AAPL buy should have gone through
        self.assertEqual(self.mock_api.place_order.call_count, 1)

    # ---- Trade recording on order success -----------------------------------

    def test_buy_order_recorded(self):
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 5000}
        self.mock_api.place_order.return_value = {"orderId": "R1"}
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0), \
             patch.object(self.ts, "check_buy_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        filepath = os.path.join(self.ts.trades_dir, "tqqq_trading.csv")
        self.assertTrue(os.path.isfile(filepath))
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["action"], "buy")
        self.assertEqual(row["order_id"], "R1")

    def test_sell_order_recorded(self):
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 110.0, "volume": 5000}
        self.mock_api.get_stock_position_qty.return_value = 10
        self.mock_api.place_order.return_value = {"orderId": "R2"}
        with patch.object(self.ts, "get_signal_indicator", return_value=60.0), \
             patch.object(self.ts, "check_sell_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        filepath = os.path.join(self.ts.trades_dir, "tqqq_trading.csv")
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["action"], "sell")

    def test_order_not_recorded_when_place_order_returns_none(self):
        """If place_order returns None, no trade should be recorded."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": 70.0, "volume": 1000}
        self.mock_api.place_order.return_value = None
        with patch.object(self.ts, "get_signal_indicator", return_value=-60.0), \
             patch.object(self.ts, "check_buy_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ")
        filepath = os.path.join(self.ts.trades_dir, "tqqq_trading.csv")
        self.assertFalse(os.path.isfile(filepath))

    def test_shared_snapshot_reuses_data_and_only_confirms_candidate_price(self):
        self.mock_api.get_realtime_quote.return_value = {
            "lastPrice": 70.0,
            "volume": 5000,
        }
        self.mock_api.place_order.return_value = {"orderId": "SNAP-1"}
        snapshot = {
            "quotes": {"TQQQ": {"lastPrice": 70.0, "volume": 4000}},
            "signals": {"TQQQ": -60.0},
            "positions": {"TQQQ": 0},
            "risk_metrics": {
                "equity": 100000.0,
                "buying_power": 100000.0,
                "gross_market_value": 0.0,
            },
        }

        with patch.object(self.ts, "check_buy_conditions", return_value=True):
            self.ts.execute_strategy("TQQQ", snapshot=snapshot)

        self.mock_api.get_realtime_quote.assert_called_once_with("TQQQ", 20002)
        self.mock_api.get_stock_position_qty.assert_not_called()
        self.mock_api.get_account_funds.assert_not_called()
        self.mock_api.get_total_portfolio_value.assert_not_called()
        self.mock_api.place_order.assert_called_once()


if __name__ == "__main__":
    unittest.main()
