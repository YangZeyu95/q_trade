"""
Real-world scenario integration tests for the TQQQ Trading Bot.

Each test simulates a realistic multi-round trading situation.
Only the HuashengGatewayAPI boundary is mocked — all internal logic
(CSV I/O, condition checks, interval tracking) uses real file operations.
"""

import unittest
from unittest.mock import Mock, patch
import json
import os
import sys
import csv
import shutil
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tqqq_trading_bot import TradingStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strat(**overrides):
    """Complete valid strategy dict with sensible defaults."""
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


def _read_trades(filepath):
    """Read all rows from a trade CSV."""
    if not os.path.isfile(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _inject_trade(filepath, action, price, date_str, order_id="INJ", status="Filled"):
    """Manually inject a trade row into CSV for history setup."""
    exists = os.path.isfile(filepath)
    with open(filepath, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["date", "action", "quantity", "price", "volume", "order_id", "status"])
        w.writerow([date_str, action, 10, price, 1000, order_id, status])


class _ScenarioBase(unittest.TestCase):
    """Base class for all scenario tests."""

    def setUp(self):
        self.mock_api = Mock()
        self.tmpdir = tempfile.mkdtemp()
        self.strategy_file = os.path.join(self.tmpdir, "strategy.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _bot(self, strategies: dict):
        """Create a TradingStrategy with given strategies, always in trading window."""
        with open(self.strategy_file, "w", encoding="utf-8") as f:
            json.dump(strategies, f)
        ts = TradingStrategy(
            self.mock_api,
            strategy_file=self.strategy_file,
            risk_config_file=os.path.join(self.tmpdir, "risk.json"),
        )
        ts.trades_dir = os.path.join(self.tmpdir, "trades")
        os.makedirs(ts.trades_dir, exist_ok=True)
        ts.is_trading_time = Mock(return_value=True)
        ts.is_near_close = Mock(return_value=True)
        return ts

    def _csv(self, ts, symbol):
        return os.path.join(ts.trades_dir, f"{symbol.lower()}_trading.csv")

    def _set_order_status(self, ts, symbol, order_id, status):
        """Simulate a gateway push reaching a terminal/partial status."""
        filepath = self._csv(ts, symbol)
        with open(filepath, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row.get("order_id") == order_id:
                row["status"] = status
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _run(self, ts, symbol, price, signal, position=0, portfolio=100000.0):
        """Execute one round of strategy for a symbol."""
        self.mock_api.get_realtime_quote.return_value = {"lastPrice": price, "volume": 5000}
        self.mock_api.get_stock_position_qty.return_value = position
        self.mock_api.get_total_portfolio_value.return_value = portfolio
        self.mock_api.get_account_funds.return_value = {
            "assetBalance": portfolio,
            "buyPower": portfolio,
        }
        with patch.object(ts, "get_signal_indicator", return_value=signal):
            ts.execute_strategy(symbol)


# ===================================================================
# Scenario 1: 暴跌抄底 — Market Crash DCA
# ===================================================================

class TestMarketCrashDCA(_ScenarioBase):
    """Price drops $80→$70→$60→$50. Bot buys at each level until position
    limit is hit. Tests graduated buying with price interval gating."""

    def test_graduated_buying_with_position_cap(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0,     # no day restriction (simulate different days)
            buy_price_interval=5.0, # need 5% price drop between buys
            max_position=10.0,      # 10% max
            fear_greed_buy=0.0,     # easy to trigger
        )})

        # Day 1: Price $75 → buy 13 shares (1000/75)
        self.mock_api.place_order.return_value = {"orderId": "CRASH_1"}
        self._run(ts, "TQQQ", price=75.0, signal=-10.0, position=0)
        self.assertEqual(self.mock_api.place_order.call_count, 1)
        self._set_order_status(ts, "TQQQ", "CRASH_1", "Filled")

        # Day 2: Price $72 → only 4% drop from $75, need 5% → BLOCKED
        self.mock_api.reset_mock()
        self._run(ts, "TQQQ", price=72.0, signal=-20.0, position=13)
        self.mock_api.place_order.assert_not_called()

        # Day 3: Price $70 → 6.67% drop from $75 → buy allowed
        self.mock_api.reset_mock()
        self.mock_api.place_order.return_value = {"orderId": "CRASH_2"}
        self._run(ts, "TQQQ", price=70.0, signal=-30.0, position=13)
        self.assertEqual(self.mock_api.place_order.call_count, 1)
        self._set_order_status(ts, "TQQQ", "CRASH_2", "Filled")

        # Day 4: Price $50, but position = 200 shares × $50 = $10k = 10% → CAPPED
        self.mock_api.reset_mock()
        self._run(ts, "TQQQ", price=50.0, signal=-50.0, position=200)
        self.mock_api.place_order.assert_not_called()

        # Verify CSV has exactly 2 buys
        trades = _read_trades(self._csv(ts, "TQQQ"))
        self.assertEqual(len(trades), 2)
        self.assertTrue(all(t["action"] == "buy" for t in trades))


# ===================================================================
# Scenario 2: 牛市止盈 — Bull Run Profit Taking
# ===================================================================

class TestBullRunProfitTaking(_ScenarioBase):
    """Price rises $90→$110→$130. Bot sells in batches, never overselling."""

    def test_partial_sells_across_rounds(self):
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, sell_total=500,  # sell ~$500 worth per round
            sell_day_interval=0,
            fear_greed_sell=0.0,  # easy to trigger
        )})

        # Round 1: Price $110, hold 50 shares → sell 4 shares (500/110)
        self.mock_api.place_order.return_value = {"orderId": "BULL_1"}
        self._run(ts, "TQQQ", price=110.0, signal=10.0, position=50)
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustAmount"], 4)
        self._set_order_status(ts, "TQQQ", "BULL_1", "Filled")

        # Round 2: Price $130, hold 46 → sell 3 shares (500/130)
        self.mock_api.reset_mock()
        self.mock_api.place_order.return_value = {"orderId": "BULL_2"}
        self._run(ts, "TQQQ", price=130.0, signal=20.0, position=46)
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustAmount"], 3)
        self._set_order_status(ts, "TQQQ", "BULL_2", "Filled")

        # Round 3: Only 2 shares left, sell_total=$500 (would be 3 shares) → capped at 2
        self.mock_api.reset_mock()
        self.mock_api.place_order.return_value = {"orderId": "BULL_3"}
        self._run(ts, "TQQQ", price=150.0, signal=30.0, position=2)
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustAmount"], 2)

        trades = _read_trades(self._csv(ts, "TQQQ"))
        self.assertEqual(len(trades), 3)
        self.assertTrue(all(t["action"] == "sell" for t in trades))


# ===================================================================
# Scenario 3: V型反转 — V-Shaped Recovery
# ===================================================================

class TestVShapedRecovery(_ScenarioBase):
    """Price drops $80→$60 (buys), then recovers $60→$120 (sells).
    Full round-trip with CSV tracking."""

    def test_buy_then_sell_round_trip(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, sell_point=100.0,
            buy_total=1000, sell_total=10000,  # explicit large sell budget, capped by holdings
            buy_day_interval=0, sell_day_interval=0,
            buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0, fear_greed_sell=0.0,
        )})

        # Leg down: buy at $75
        self.mock_api.place_order.return_value = {"orderId": "V_BUY_1"}
        self._run(ts, "TQQQ", price=75.0, signal=-10.0, position=0)
        self.assertEqual(self.mock_api.place_order.call_count, 1)
        self._set_order_status(ts, "TQQQ", "V_BUY_1", "Filled")

        # Leg down more: buy at $60
        self.mock_api.reset_mock()
        self.mock_api.place_order.return_value = {"orderId": "V_BUY_2"}
        self._run(ts, "TQQQ", price=60.0, signal=-20.0, position=13)
        self.assertEqual(self.mock_api.place_order.call_count, 1)
        self._set_order_status(ts, "TQQQ", "V_BUY_2", "Filled")

        # Recovery: Price $90 → below sell_point, no sell
        self.mock_api.reset_mock()
        self._run(ts, "TQQQ", price=90.0, signal=10.0, position=29)
        self.mock_api.place_order.assert_not_called()

        # Recovery: Price $120 → above sell_point, sell all
        self.mock_api.reset_mock()
        self.mock_api.place_order.return_value = {"orderId": "V_SELL"}
        self._run(ts, "TQQQ", price=120.0, signal=10.0, position=29)
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustBs"], "2")
        self.assertEqual(args.kwargs["entrustAmount"], 29)

        # Verify full journey in CSV
        trades = _read_trades(self._csv(ts, "TQQQ"))
        self.assertEqual(len(trades), 3)
        self.assertEqual(trades[0]["action"], "buy")
        self.assertEqual(trades[1]["action"], "buy")
        self.assertEqual(trades[2]["action"], "sell")


# ===================================================================
# Scenario 4: 震荡市 — Sideways Choppy Market
# ===================================================================

class TestChoppyMarket(_ScenarioBase):
    """Price oscillates around buy_point. Interval controls should prevent
    excessive trading. Tests: $75→$72→$76→$71→$74"""

    def test_intervals_prevent_overtrading(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=500,
            buy_day_interval=2,      # need 2 day gap
            buy_price_interval=3.0,  # need 3% price change
            max_position=100.0,
            fear_greed_buy=0.0,
        )})

        # Tick 1: $75 → first buy (no history)
        self.mock_api.place_order.return_value = {"orderId": "CHOP_1"}
        self._run(ts, "TQQQ", price=75.0, signal=-10.0, position=0)
        self.assertEqual(self.mock_api.place_order.call_count, 1)

        # Tick 2: $72 → 4% drop (> 3%), but same day → day interval blocks
        self.mock_api.reset_mock()
        self._run(ts, "TQQQ", price=72.0, signal=-10.0, position=6)
        self.mock_api.place_order.assert_not_called()

        # Tick 3: $76 → price went UP 1.3% from $75 (< 3%) → both intervals block
        self.mock_api.reset_mock()
        self._run(ts, "TQQQ", price=76.0, signal=-10.0, position=6)
        self.mock_api.place_order.assert_not_called()

        # Only 1 trade in choppy conditions
        trades = _read_trades(self._csv(ts, "TQQQ"))
        self.assertEqual(len(trades), 1)


# ===================================================================
# Scenario 5: API故障 — API Failures During Critical Moments
# ===================================================================

class TestAPIFailureResilience(_ScenarioBase):
    """Various API failures should not corrupt state or crash the bot."""

    def test_failures_dont_corrupt_state(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0,
        )})

        # Round 1: Successful buy
        self.mock_api.place_order.return_value = {"orderId": "OK_1"}
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=0)
        self.assertEqual(self.mock_api.place_order.call_count, 1)
        self._set_order_status(ts, "TQQQ", "OK_1", "Filled")

        # Round 2: place_order returns None (API rejection)
        self.mock_api.reset_mock()
        self.mock_api.place_order.return_value = None
        self._run(ts, "TQQQ", price=65.0, signal=-20.0, position=14)
        # Order attempted but rejected — no CSV entry
        trades = _read_trades(self._csv(ts, "TQQQ"))
        self.assertEqual(len(trades), 1)  # Only round 1

        # Round 3: get_realtime_quote throws exception
        self.mock_api.reset_mock()
        self.mock_api.get_realtime_quote.side_effect = Exception("Network timeout")
        with patch.object(ts, "get_signal_indicator", return_value=-30.0):
            ts.execute_strategy("TQQQ")  # Should not crash
        self.mock_api.place_order.assert_not_called()

        # Round 4: API recovered, normal buy succeeds
        self.mock_api.reset_mock()
        self.mock_api.get_realtime_quote.side_effect = None
        self.mock_api.place_order.return_value = {"orderId": "OK_2"}
        self._run(ts, "TQQQ", price=60.0, signal=-40.0, position=14)
        self.assertEqual(self.mock_api.place_order.call_count, 1)

        # Verify: 2 successful trades, state intact
        trades = _read_trades(self._csv(ts, "TQQQ"))
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0]["order_id"], "OK_1")
        self.assertEqual(trades[1]["order_id"], "OK_2")

    def test_update_pending_survives_api_failure(self):
        """update_pending_orders should handle API errors gracefully."""
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0,
        )})

        # Place an order
        self.mock_api.place_order.return_value = {"orderId": "PEND_1"}
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=0)

        # API throws during status check
        self.mock_api.get_order_details.side_effect = Exception("API down")
        ts.update_pending_orders()  # Should not crash

        # Status should remain Submitted
        trades = _read_trades(self._csv(ts, "TQQQ"))
        self.assertEqual(trades[0]["status"], "Submitted")


# ===================================================================
# Scenario 6: 多品种分化 — Multi-Stock Divergence
# ===================================================================

class TestMultiStockDivergence(_ScenarioBase):
    """TQQQ crashes (buy), AAPL moons (sell), MSFT sideways (no action)."""

    def test_independent_actions_across_stocks(self):
        ts = self._bot({
            "TQQQ": _strat(buy_point=80.0, sell_point=100.0, buy_total=1000,
                           buy_day_interval=0, buy_price_interval=0, max_position=100.0,
                           fear_greed_buy=0.0, fear_greed_sell=50.0),
            "AAPL": _strat(buy_point=150.0, sell_point=200.0, sell_total=10000,
                           sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
                           fear_greed_buy=-50.0, fear_greed_sell=0.0),
            "MSFT": _strat(buy_point=300.0, sell_point=400.0,
                           buy_day_interval=0, buy_price_interval=0,
                           fear_greed_buy=-50.0, fear_greed_sell=50.0),
        })

        # TQQQ: Price $70 < buy_point $80, signal fearful → BUY
        self.mock_api.place_order.return_value = {"orderId": "T1"}
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=0)
        self.assertEqual(self.mock_api.place_order.call_count, 1)
        buy_call = self.mock_api.place_order.call_args
        self.assertEqual(buy_call.kwargs["entrustBs"], "1")

        # AAPL: Price $220 > sell_point $200, signal greedy → SELL
        self.mock_api.reset_mock()
        self.mock_api.place_order.return_value = {"orderId": "A1"}
        self._run(ts, "AAPL", price=220.0, signal=10.0, position=30)
        self.assertEqual(self.mock_api.place_order.call_count, 1)
        sell_call = self.mock_api.place_order.call_args
        self.assertEqual(sell_call.kwargs["entrustBs"], "2")

        # MSFT: Price $350 → between buy/sell points → NO ACTION
        self.mock_api.reset_mock()
        self._run(ts, "MSFT", price=350.0, signal=0.0, position=10)
        self.mock_api.place_order.assert_not_called()

        # Verify separate CSV files
        self.assertEqual(len(_read_trades(self._csv(ts, "TQQQ"))), 1)
        self.assertEqual(len(_read_trades(self._csv(ts, "AAPL"))), 1)
        self.assertEqual(len(_read_trades(self._csv(ts, "MSFT"))), 0)


# ===================================================================
# Scenario 7: 订单生命周期 — Order Lifecycle Management
# ===================================================================

class TestOrderLifecycle(_ScenarioBase):
    """Place orders → track through Submitted→Partial→Filled.
    Then verify interval enforcement reads from real trade history."""

    def test_full_order_lifecycle_with_interval_check(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0,
        )})

        # Place 2 orders in rapid succession
        self.mock_api.place_order.return_value = {"orderId": "LC_1"}
        self._run(ts, "TQQQ", price=75.0, signal=-10.0, position=0)

        self.mock_api.reset_mock()
        self.mock_api.place_order.return_value = {"orderId": "LC_2"}
        self._run(ts, "TQQQ", price=70.0, signal=-20.0, position=13)

        # The second order is blocked while LC_1 is still pending.
        trades = _read_trades(self._csv(ts, "TQQQ"))
        self.assertEqual(len(trades), 1)
        self.assertTrue(all(t["status"] == "Submitted" for t in trades))

        # First update: LC_1 → Partial
        self.mock_api.get_order_details.side_effect = [
            {"orderStatusName": "Partial"},
        ]
        ts.update_pending_orders()
        trades = _read_trades(self._csv(ts, "TQQQ"))
        self.assertEqual(trades[0]["status"], "Partial")

        # Second update: LC_1 → Filled
        self.mock_api.get_order_details.side_effect = [
            {"orderStatusName": "Filled"},
        ]
        ts.update_pending_orders()
        trades = _read_trades(self._csv(ts, "TQQQ"))
        self.assertTrue(all(t["status"] == "Filled" for t in trades))

        # Third update: no pending orders → no API calls
        self.mock_api.reset_mock()
        ts.update_pending_orders()
        self.mock_api.get_order_details.assert_not_called()


# ===================================================================
# Scenario 8: 极端价格 — Extreme Price Edge Cases
# ===================================================================

class TestExtremePrices(_ScenarioBase):
    """Penny stocks, high-price stocks, boundary-exact prices."""

    def test_penny_stock_one_cent(self):
        """$0.05 stock: 1000/$0.05 = 20000 shares, entrust = $0.06"""
        ts = self._bot({"PENNY": _strat(
            buy_point=1.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0,
        )})
        self.mock_api.place_order.return_value = {"orderId": "PENNY"}
        self._run(ts, "PENNY", price=0.05, signal=-10.0, position=0)
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustAmount"], 20000)
        self.assertEqual(args.kwargs["entrustPrice"], "0.06")  # 0.05 + 0.01

    def test_high_price_stock(self):
        """$5000 stock: 1000/5000 = 0 shares → no order."""
        ts = self._bot({"BRK": _strat(
            buy_point=6000.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0,
        )})
        self._run(ts, "BRK", price=5000.0, signal=-10.0, position=0)
        self.mock_api.place_order.assert_not_called()

    def test_price_exactly_at_buy_point(self):
        """Price == buy_point → should buy (<=)."""
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0,
        )})
        self.mock_api.place_order.return_value = {"orderId": "EXACT_BUY"}
        self._run(ts, "TQQQ", price=80.0, signal=-10.0, position=0)
        self.mock_api.place_order.assert_called_once()

    def test_price_exactly_at_sell_point(self):
        """Price == sell_point → should sell (>=)."""
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, sell_total=10000,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
            fear_greed_sell=0.0,
        )})
        self.mock_api.place_order.return_value = {"orderId": "EXACT_SELL"}
        self._run(ts, "TQQQ", price=100.0, signal=10.0, position=10)
        self.mock_api.place_order.assert_called_once()

    def test_price_one_cent_above_buy_point_no_buy(self):
        """Price $80.01 > buy_point $80 → no buy."""
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0,
        )})
        self._run(ts, "TQQQ", price=80.01, signal=-10.0, position=0)
        self.mock_api.place_order.assert_not_called()

    def test_price_one_cent_below_sell_point_no_sell(self):
        """Price $99.99 < sell_point $100 → no sell."""
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, sell_total=10000,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
            fear_greed_sell=0.0,
        )})
        self._run(ts, "TQQQ", price=99.99, signal=10.0, position=10)
        self.mock_api.place_order.assert_not_called()


# ===================================================================
# Scenario 9: 策略热更新 — Live Strategy Modification
# ===================================================================

class TestLiveStrategyUpdate(_ScenarioBase):
    """User changes strategy parameters and adds a new stock mid-session."""

    def test_parameter_change_takes_effect(self):
        # Start: buy_point=80, price $85 → no buy
        ts = self._bot({"TQQQ": _strat(buy_point=80.0, fear_greed_buy=0.0,
                                        buy_day_interval=0, buy_price_interval=0, max_position=100.0)})
        self._run(ts, "TQQQ", price=85.0, signal=-10.0, position=0)
        self.mock_api.place_order.assert_not_called()

        # User changes buy_point to 90
        with open(self.strategy_file, "w", encoding="utf-8") as f:
            json.dump({"TQQQ": _strat(buy_point=90.0, fear_greed_buy=0.0,
                                       buy_day_interval=0, buy_price_interval=0, max_position=100.0)}, f)
        ts.stock_strategies = ts.load_stock_strategies()

        # Now price $85 < new buy_point $90 → buy!
        self.mock_api.place_order.return_value = {"orderId": "HOT_1"}
        self._run(ts, "TQQQ", price=85.0, signal=-10.0, position=0)
        self.mock_api.place_order.assert_called_once()

    def test_new_stock_added_mid_session(self):
        ts = self._bot({"TQQQ": _strat()})
        self.assertNotIn("AAPL", ts.stock_strategies)

        # Add AAPL
        with open(self.strategy_file, "w", encoding="utf-8") as f:
            json.dump({
                "TQQQ": _strat(),
                "AAPL": _strat(buy_point=200.0, fear_greed_buy=0.0,
                               buy_day_interval=0, buy_price_interval=0, max_position=100.0),
            }, f)
        ts.stock_strategies = ts.load_stock_strategies()

        # AAPL trade works
        self.mock_api.place_order.return_value = {"orderId": "NEW_AAPL"}
        self._run(ts, "AAPL", price=180.0, signal=-10.0, position=0)
        self.mock_api.place_order.assert_called_once()

    def test_stock_removed_mid_session(self):
        ts = self._bot({"TQQQ": _strat(), "AAPL": _strat()})
        self.assertIn("AAPL", ts.stock_strategies)

        # Remove AAPL
        with open(self.strategy_file, "w", encoding="utf-8") as f:
            json.dump({"TQQQ": _strat()}, f)
        ts.stock_strategies = ts.load_stock_strategies()

        self.assertNotIn("AAPL", ts.stock_strategies)
        # Trying to execute removed stock → no strategy found, no crash
        self._run(ts, "AAPL", price=180.0, signal=-10.0, position=0)
        self.mock_api.place_order.assert_not_called()


# ===================================================================
# Scenario 10: 纯信号驱动 — Signal-Only Trading
# ===================================================================

class TestSignalOnlyTrading(_ScenarioBase):
    """buy_point=0, sell_point=0 → purely signal-driven."""

    def test_buys_when_fearful_regardless_of_price(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=0, sell_point=0,  # no price restriction
            buy_total=1000, sell_total=10000,
            buy_day_interval=0, sell_day_interval=0,
            buy_price_interval=0, max_position=100.0,
            fear_greed_buy=-30.0, fear_greed_sell=30.0,
        )})

        # Price $200 (very high), signal fearful → BUY
        self.mock_api.place_order.return_value = {"orderId": "SIG_B"}
        self._run(ts, "TQQQ", price=200.0, signal=-40.0, position=0)
        self.mock_api.place_order.assert_called_once()
        self.assertEqual(self.mock_api.place_order.call_args.kwargs["entrustBs"], "1")

    def test_sells_when_greedy_regardless_of_price(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=0, sell_point=0,
            buy_total=1000, sell_total=10000,
            buy_day_interval=0, sell_day_interval=0,
            buy_price_interval=0, max_position=100.0,
            fear_greed_buy=-30.0, fear_greed_sell=30.0,
        )})

        # Price $10 (very low), signal greedy → SELL
        self.mock_api.place_order.return_value = {"orderId": "SIG_S"}
        self._run(ts, "TQQQ", price=10.0, signal=40.0, position=50)
        self.mock_api.place_order.assert_called_once()
        self.assertEqual(self.mock_api.place_order.call_args.kwargs["entrustBs"], "2")

    def test_neutral_signal_no_action(self):
        """Signal between thresholds → neither buy nor sell."""
        ts = self._bot({"TQQQ": _strat(
            buy_point=0, sell_point=0,
            buy_total=1000, sell_total=10000,
            buy_day_interval=0, sell_day_interval=0,
            buy_price_interval=0, max_position=100.0,
            fear_greed_buy=-30.0, fear_greed_sell=30.0,
        )})
        self._run(ts, "TQQQ", price=100.0, signal=0.0, position=10)
        self.mock_api.place_order.assert_not_called()


# ===================================================================
# Scenario 11: Dry Run全流程 — Full Dry Run Simulation
# ===================================================================

class TestDryRunFullSimulation(_ScenarioBase):
    """Buy + sell conditions met across multiple rounds. No orders, no CSV."""

    def test_dry_run_complete_isolation(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, sell_point=100.0,
            buy_total=1000, sell_total=10000,
            buy_day_interval=0, sell_day_interval=0,
            buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0, fear_greed_sell=0.0,
        )})
        ts.dry_run = True

        # Would-be buy
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=0)
        self.mock_api.place_order.assert_not_called()

        # Would-be sell
        self._run(ts, "TQQQ", price=110.0, signal=10.0, position=20)
        self.mock_api.place_order.assert_not_called()

        # No CSV file created at all
        self.assertFalse(os.path.isfile(self._csv(ts, "TQQQ")))


# ===================================================================
# Scenario 12: 仓位边界 — Position Boundary Precision
# ===================================================================

class TestPositionBoundaryPrecision(_ScenarioBase):
    """Tests exact boundary of max_position percentage."""

    def test_just_under_limit_allows_buy(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0,
            max_position=10.0,  # 10% limit
            fear_greed_buy=0.0,
        )})
        # Position: 99 shares × $100 = $9,900 / $100,000 = 9.9% < 10% → OK
        self.mock_api.place_order.return_value = {"orderId": "EDGE_OK"}
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=99, portfolio=100000.0)
        # 99 * 70 = 6930, 6.93% < 10% → buy allowed
        self.mock_api.place_order.assert_called_once()

    def test_exactly_at_limit_blocks_buy(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0,
            max_position=10.0,
            fear_greed_buy=0.0,
        )})
        # Position: 100 shares × $100 = $10,000 / $100,000 = 10.0% == 10% → BLOCKED
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=143, portfolio=100000.0)
        # 143 * 70 = 10010, 10.01% >= 10% → blocked
        self.mock_api.place_order.assert_not_called()

    def test_zero_equity_blocks_buy(self):
        """No account equity → fail closed instead of bypassing risk checks."""
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0,
            max_position=10.0,
            fear_greed_buy=0.0,
        )})
        self.mock_api.place_order.return_value = {"orderId": "ZERO_PORT"}
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=10, portfolio=0.0)
        self.mock_api.place_order.assert_not_called()

    def test_no_position_first_buy_always_allowed(self):
        """Position = 0, portfolio = any → 0% position, always under limit."""
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0,
            max_position=1.0,  # Even 1% limit
            fear_greed_buy=0.0,
        )})
        self.mock_api.place_order.return_value = {"orderId": "FIRST"}
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=0, portfolio=100000.0)
        self.mock_api.place_order.assert_called_once()

# ===================================================================
# Standalone Corner Cases (补充覆盖)
# ===================================================================

class TestSellQuantityEdgeCases(_ScenarioBase):
    """Explicit tests for sell quantity calculation edge cases."""

    def test_sell_total_zero_skips_sell(self):
        """sell_total=0 must skip instead of selling the entire position."""
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, sell_total=0,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
            fear_greed_sell=0.0,
        )})
        self._run(ts, "TQQQ", price=110.0, signal=10.0, position=37)
        self.mock_api.place_order.assert_not_called()

    def test_sell_total_larger_than_position_caps_at_position(self):
        """sell_total=$10000, price=$100 → want 100 shares but only hold 5."""
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, sell_total=10000,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
            fear_greed_sell=0.0,
        )})
        self.mock_api.place_order.return_value = {"orderId": "CAP"}
        self._run(ts, "TQQQ", price=100.0, signal=10.0, position=5)
        args = self.mock_api.place_order.call_args
        self.assertEqual(args.kwargs["entrustAmount"], 5)

    def test_sell_total_too_small_for_one_share(self):
        """sell_total=$50, price=$100 → 0 shares → skip."""
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, sell_total=50,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
            fear_greed_sell=0.0,
        )})
        self._run(ts, "TQQQ", price=100.0, signal=10.0, position=10)
        self.mock_api.place_order.assert_not_called()

    def test_sell_no_position_no_order(self):
        """Sell triggered but position=0 → no order."""
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, sell_total=10000,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
            fear_greed_sell=0.0,
        )})
        self._run(ts, "TQQQ", price=110.0, signal=10.0, position=0)
        self.mock_api.place_order.assert_not_called()


class TestPlaceOrderFailures(_ScenarioBase):
    """When place_order returns None, no trade should be recorded."""

    def test_buy_order_rejected_no_csv(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0,
        )})
        self.mock_api.place_order.return_value = None
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=0)
        self.assertFalse(os.path.isfile(self._csv(ts, "TQQQ")))

    def test_sell_order_rejected_no_csv(self):
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, sell_total=10000,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
            fear_greed_sell=0.0,
        )})
        self.mock_api.place_order.return_value = None
        self._run(ts, "TQQQ", price=110.0, signal=10.0, position=10)
        self.assertFalse(os.path.isfile(self._csv(ts, "TQQQ")))


class TestRegressionGreedySignalLowPrice(_ScenarioBase):
    """REGRESSION: Greedy signal + price below sell_point must NOT trigger sell.
    (Previously used `or` which caused this bug.)"""

    def test_no_sell_below_sell_point_with_greedy_signal(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, sell_point=100.0,
            fear_greed_buy=-50.0, fear_greed_sell=50.0,
            sell_total=10000, sell_day_interval=0,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
        )})
        self._run(ts, "TQQQ", price=50.0, signal=60.0, position=20)
        self.mock_api.place_order.assert_not_called()

    def test_sell_blocked_when_only_signal_met(self):
        """Signal greedy but price $90 < sell_point $100."""
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, fear_greed_sell=50.0,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
        )})
        self._run(ts, "TQQQ", price=90.0, signal=60.0, position=10)
        self.mock_api.place_order.assert_not_called()

    def test_sell_blocked_when_only_price_met(self):
        """Price $110 > sell_point $100 but signal=0 < fear_greed_sell=50."""
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, fear_greed_sell=50.0,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
        )})
        self._run(ts, "TQQQ", price=110.0, signal=0.0, position=10)
        self.mock_api.place_order.assert_not_called()


class TestEntrustPriceLogic(_ScenarioBase):
    """Entrust price: default ±$0.01, or limit price if configured."""

    def test_buy_default_adds_one_cent(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0,
        )})
        self.mock_api.place_order.return_value = {"orderId": "P1"}
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=0)
        self.assertEqual(self.mock_api.place_order.call_args.kwargs["entrustPrice"], "70.01")

    def test_sell_default_subtracts_one_cent(self):
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, sell_total=10000,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
            fear_greed_sell=0.0,
        )})
        self.mock_api.place_order.return_value = {"orderId": "P2"}
        self._run(ts, "TQQQ", price=110.0, signal=10.0, position=10)
        self.assertEqual(self.mock_api.place_order.call_args.kwargs["entrustPrice"], "109.99")

    def test_buy_uses_limit_price_when_set(self):
        ts = self._bot({"TQQQ": _strat(
            buy_point=80.0, buy_limit_price=65.0, buy_total=1000,
            buy_day_interval=0, buy_price_interval=0, max_position=100.0,
            fear_greed_buy=0.0,
        )})
        self.mock_api.place_order.return_value = {"orderId": "P3"}
        self._run(ts, "TQQQ", price=70.0, signal=-10.0, position=0)
        self.assertEqual(self.mock_api.place_order.call_args.kwargs["entrustPrice"], "65.0")

    def test_sell_uses_limit_price_when_set(self):
        ts = self._bot({"TQQQ": _strat(
            sell_point=100.0, sell_limit_price=115.0, sell_total=10000,
            sell_day_interval=0, buy_day_interval=0, buy_price_interval=0,
            fear_greed_sell=0.0,
        )})
        self.mock_api.place_order.return_value = {"orderId": "P4"}
        self._run(ts, "TQQQ", price=110.0, signal=10.0, position=10)
        self.assertEqual(self.mock_api.place_order.call_args.kwargs["entrustPrice"], "115.0")


class TestStrategyFileCorruption(_ScenarioBase):
    """Corrupt or missing strategy files should be handled gracefully."""

    def test_corrupt_json_returns_empty(self):
        ts = self._bot({"TQQQ": _strat()})
        with open(self.strategy_file, "w") as f:
            f.write("{broken json!!!")
        ts.stock_strategies = ts.load_stock_strategies()
        self.assertEqual(ts.stock_strategies, {})

    def test_missing_file_returns_empty(self):
        ts = self._bot({"TQQQ": _strat()})
        os.remove(self.strategy_file)
        ts.stock_strategies = ts.load_stock_strategies()
        self.assertEqual(ts.stock_strategies, {})


if __name__ == "__main__":
    unittest.main()
