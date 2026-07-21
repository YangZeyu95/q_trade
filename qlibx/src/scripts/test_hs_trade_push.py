"""交易推送 SDK 适配器和推送落盘竞态测试。"""

import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hs_trade_push
from tqqq_trading_bot import TradingStrategy


def _strategy_config():
    return {
        "TQQQ": {
            "buy_point": 80,
            "sell_point": 100,
            "buy_total": 1000,
            "sell_total": 500,
            "buy_day_interval": 1,
            "sell_day_interval": 1,
            "buy_price_interval": 2,
            "max_position": 50,
            "fear_greed_buy": -50,
            "fear_greed_sell": 50,
        }
    }


class _FakeOpenAPI:
    events = []

    def __init__(self, **kwargs):
        self.callback = None
        self.alive = False
        self.kwargs = kwargs
        self.subscribed = False

    def add_notify_callback(self, callback):
        self.callback = callback
        self.events.append("callback")

    def start(self):
        self.events.append("start")
        self.alive = True

    def is_alive(self):
        return self.alive

    def trade_subscribe(self):
        self.events.append("subscribe")
        self.subscribed = True
        return {"ok": True, "err": "", "data": {"success": True}}

    def trade_unsubscribe(self):
        self.events.append("unsubscribe")
        self.subscribed = False
        return {"ok": True, "err": "", "data": {"success": True}}

    def stop(self):
        self.events.append("stop")
        self.alive = False


class TestTradePushListener(unittest.TestCase):

    def setUp(self):
        _FakeOpenAPI.events = []

    def test_subscribes_after_callback_registration(self):
        callback = Mock()
        with patch.object(hs_trade_push, "OpenAPI", _FakeOpenAPI):
            listener = hs_trade_push.TradePushListener(callback)
            self.assertTrue(listener.start())
            self.assertEqual(_FakeOpenAPI.events[:3], ["callback", "start", "subscribe"])
            listener.stop()
            self.assertIn("unsubscribe", _FakeOpenAPI.events)
            self.assertIn("stop", _FakeOpenAPI.events)

    def test_forwards_only_trade_deliver_messages(self):
        callback = Mock()
        payload = SimpleNamespace(recordNo="R1", entrustStatus="8")
        notify = SimpleNamespace(notifyMsgType=hs_trade_push.TradeStockDeliverMsgType)
        with patch.object(hs_trade_push, "OpenAPI", _FakeOpenAPI), \
             patch.object(hs_trade_push, "parse_payload", return_value=payload):
            listener = hs_trade_push.TradePushListener(callback)
            listener._on_notify(notify)
            callback.assert_called_once_with(payload)

            callback.reset_mock()
            listener._on_notify(SimpleNamespace(notifyMsgType=999))
            callback.assert_not_called()

    def test_constructor_rejects_invalid_callback_and_ports(self):
        with self.assertRaises(TypeError):
            hs_trade_push.TradePushListener(None)
        with self.assertRaises(ValueError):
            hs_trade_push.TradePushListener(Mock(), http_port="bad")
        with self.assertRaises(ValueError):
            hs_trade_push.TradePushListener(Mock(), tcp_port="70000")

    def test_start_fails_closed_when_tcp_or_subscription_is_unavailable(self):
        class DisconnectedOpenAPI(_FakeOpenAPI):
            def start(self):
                self.alive = False

        with patch.object(hs_trade_push, "OpenAPI", DisconnectedOpenAPI):
            listener = hs_trade_push.TradePushListener(Mock())
            self.assertFalse(listener.start())

        class RefusingOpenAPI(_FakeOpenAPI):
            def trade_subscribe(self):
                return {"ok": True, "data": {"success": False}}

        with patch.object(hs_trade_push, "OpenAPI", RefusingOpenAPI):
            listener = hs_trade_push.TradePushListener(Mock())
            self.assertFalse(listener.start())

    def test_callback_parse_or_consumer_failure_does_not_escape_sdk_thread(self):
        notify = SimpleNamespace(
            notifyMsgType=hs_trade_push.TradeStockDeliverMsgType
        )
        with patch.object(hs_trade_push, "OpenAPI", _FakeOpenAPI), \
                patch.object(hs_trade_push, "parse_payload", side_effect=ValueError("bad payload")):
            listener = hs_trade_push.TradePushListener(Mock())
            listener._on_notify(notify)

        consumer = Mock(side_effect=RuntimeError("disk full"))
        with patch.object(hs_trade_push, "OpenAPI", _FakeOpenAPI), \
                patch.object(hs_trade_push, "parse_payload", return_value=object()):
            listener = hs_trade_push.TradePushListener(consumer)
            listener._on_notify(notify)
        consumer.assert_called_once()

    def test_success_response_validation_is_strict(self):
        valid = {"ok": True, "data": {"success": True}}
        self.assertTrue(hs_trade_push.TradePushListener._is_success(valid))
        self.assertTrue(hs_trade_push.TradePushListener._is_success({"ok": True}))
        self.assertFalse(hs_trade_push.TradePushListener._is_success(None))
        self.assertFalse(hs_trade_push.TradePushListener._is_success({"ok": False}))
        self.assertFalse(hs_trade_push.TradePushListener._is_success({
            "ok": True, "data": {"success": False},
        }))


class TestTradePushPersistence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        strategy_file = os.path.join(self.tmpdir, "strategy.json")
        with open(strategy_file, "w", encoding="utf-8") as f:
            json.dump(_strategy_config(), f)
        self.strategy = TradingStrategy(
            Mock(),
            strategy_file=strategy_file,
            risk_config_file=os.path.join(self.tmpdir, "risk.json"),
        )
        self.strategy.trades_dir = os.path.join(self.tmpdir, "trades")
        os.makedirs(self.strategy.trades_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @staticmethod
    def _payload(order_id, status="8"):
        return SimpleNamespace(
            recordNo=order_id,
            entrustNo="",
            entrustStatus=status,
            stockCode="TQQQ",
            businessAmount="10",
            leftAmount="0",
        )

    def test_push_updates_existing_order(self):
        self.strategy.record_trade("TQQQ", "buy", 10, 80, 1000, {"orderId": "R1"})
        self.assertTrue(self.strategy.handle_trade_push(self._payload("R1")))

        path = os.path.join(self.strategy.trades_dir, "tqqq_trading.csv")
        with open(path, "r", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["status"], "Filled")
        self.assertEqual(row["filled_quantity"], "10")
        self.assertEqual(row["remaining_quantity"], "0")

    def test_push_before_record_trade_is_applied_after_order_returns(self):
        self.assertFalse(self.strategy.handle_trade_push(self._payload("R2")))
        self.strategy.record_trade("TQQQ", "buy", 10, 80, 1000, {"orderId": "R2"})

        path = os.path.join(self.strategy.trades_dir, "tqqq_trading.csv")
        with open(path, "r", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["status"], "Filled")
        self.assertEqual(row["filled_quantity"], "10")

    def test_entrust_number_can_match_record_number(self):
        self.strategy.record_trade("TQQQ", "buy", 10, 80, 1000, {"orderId": "R3"})
        payload = self._payload("different-record", status="6")
        payload.recordNo = ""
        payload.entrustNo = "R3"
        self.assertTrue(self.strategy.handle_trade_push(payload))

        path = os.path.join(self.strategy.trades_dir, "tqqq_trading.csv")
        with open(path, "r", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["status"], "Canceled")


if __name__ == "__main__":
    unittest.main()
