import os
import sys
import unittest
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hs_trade_history import HSTradeHistory, HSTradeHistoryError


class TestHSTradeHistory(unittest.TestCase):
    def setUp(self):
        self.sdk = Mock()
        self.history = HSTradeHistory(open_api=self.sdk)
        self.history.lookback_days = 30
        self.history.window_days = 30
        self.history.windows_per_refresh = 1
        self.now = datetime(2026, 7, 20, 12, tzinfo=ZoneInfo("America/New_York"))

    @staticmethod
    def _response(records):
        return {"ok": True, "err": "", "data": {"data": records}}

    def test_latest_buy_and_sell_are_kept_separately(self):
        self.sdk.query_real_deliver_list.return_value = self._response([
            {
                "stockCode": "US.MSTU", "entrustBs": "2", "businessPrice": "2.10",
                "businessAmount": "100", "businessBalance": "210", "date": "2026-07-20",
                "businessTime": "10:05:00", "entrustId": "SELL-TODAY",
            },
            {
                "stockCode": "MSTU", "entrustBs": "1", "businessPrice": "1.95",
                "businessAmount": "200", "date": "2026-07-20",
                "businessTime": "09:45:00", "entrustId": "BUY-TODAY",
            },
        ])
        self.sdk.query_history_deliver_list.return_value = self._response([
            {
                "stockCode": "MSTU", "entrustBs": "1", "businessPrice": "1.80",
                "businessAmount": "400", "date": "2026-07-08", "businessTime": "--:--:--",
            },
            {
                "stockCode": "MSTU", "entrustBs": "2", "businessPrice": "2.00",
                "businessAmount": "50", "date": "2026-07-09", "businessTime": "--:--:--",
            },
        ])

        result = self.history.get_latest_by_symbol(now=self.now)

        self.assertEqual(result["MSTU"]["buy"]["price"], 1.95)
        self.assertEqual(result["MSTU"]["buy"]["order_id"], "BUY-TODAY")
        self.assertEqual(result["MSTU"]["sell"]["price"], 2.10)
        self.assertEqual(result["MSTU"]["sell"]["order_id"], "SELL-TODAY")
        self.assertEqual(result["MSTU"]["buy"]["source"], "HS SDK")
        self.sdk.query_real_deliver_list.assert_called_once_with(
            exchange_type="P", query_count=99, query_param_str="0"
        )
        history_call = self.sdk.query_history_deliver_list.call_args.kwargs
        self.assertEqual(history_call["end_date"], "20260719")

    def test_sell_only_does_not_create_a_buy_record(self):
        self.sdk.query_real_deliver_list.return_value = self._response([])
        self.sdk.query_history_deliver_list.return_value = self._response([
            {
                "stockCode": "TQQQ", "entrustBs": "2", "businessPrice": "75",
                "businessAmount": "10", "date": "2026-07-10",
            }
        ])

        result = self.history.get_latest_by_symbol(now=self.now)

        self.assertNotIn("buy", result["TQQQ"])
        self.assertEqual(result["TQQQ"]["sell"]["date"], "2026-07-10")

    def test_gateway_failure_is_not_treated_as_empty_history(self):
        self.sdk.query_real_deliver_list.return_value = {
            "ok": False, "err": "not logged in",
        }
        with self.assertRaisesRegex(HSTradeHistoryError, "not logged in"):
            self.history.get_latest_by_symbol(now=self.now)

    def test_malformed_and_unknown_side_records_are_ignored(self):
        self.sdk.query_real_deliver_list.return_value = self._response([
            {"stockCode": "BAD", "entrustBs": "9", "businessPrice": "1", "businessAmount": "1", "date": "2026-07-20"},
            {"stockCode": "BAD", "entrustBs": "1", "businessPrice": "nan", "businessAmount": "1", "date": "2026-07-20"},
        ])
        self.sdk.query_history_deliver_list.return_value = self._response([])
        self.assertEqual(self.history.get_latest_by_symbol(now=self.now), {})

    def test_historical_windows_are_cached_but_today_is_refreshed(self):
        self.sdk.query_real_deliver_list.return_value = self._response([])
        self.sdk.query_history_deliver_list.return_value = self._response([])

        self.history.get_latest_by_symbol(now=self.now)
        self.history.get_latest_by_symbol(now=self.now)

        self.assertEqual(self.sdk.query_history_deliver_list.call_count, 1)
        self.assertEqual(self.sdk.query_real_deliver_list.call_count, 2)
        self.assertTrue(self.history.history_complete)

    def test_long_history_is_filled_one_window_per_refresh(self):
        self.history.lookback_days = 60
        self.history.window_days = 30
        self.sdk.query_real_deliver_list.return_value = self._response([])
        self.sdk.query_history_deliver_list.return_value = self._response([])

        self.history.get_latest_by_symbol(now=self.now)
        self.assertFalse(self.history.history_complete)
        self.assertEqual(self.sdk.query_history_deliver_list.call_count, 1)

        self.history.get_latest_by_symbol(now=self.now)
        self.assertTrue(self.history.history_complete)
        self.assertEqual(self.sdk.query_history_deliver_list.call_count, 2)

        self.history.get_latest_by_symbol(now=self.now)
        self.assertEqual(self.sdk.query_history_deliver_list.call_count, 2)

    def test_active_orders_exclude_terminal_and_keep_remaining_quantity(self):
        self.sdk.query_real_entrust_list.return_value = self._response([
            {
                "stockCode": "US.MSTU", "entrustBs": "1", "entrustId": "OPEN-1",
                "status": "2", "statusDesc": "已报", "entrustAmount": "10",
                "businessAmount": "0", "unBusinessAmount": "10",
                "entrustPrice": "1.50", "date": "2026-07-20", "entrustTime": "10:00:00",
            },
            {
                "stockCode": "MSTU", "entrustBs": "2", "entrustId": "PARTIAL-1",
                "status": "7", "statusDesc": "部分成交", "entrustAmount": "10",
                "businessAmount": "4", "unBusinessAmount": "6", "entrustPrice": "2.00",
            },
            {
                "stockCode": "MSTU", "entrustBs": "1", "entrustId": "DONE-1",
                "status": "8", "statusDesc": "已成交", "entrustAmount": "10",
                "businessAmount": "10", "unBusinessAmount": "0", "entrustPrice": "1.60",
            },
            {
                "stockCode": "MSTU", "entrustBs": "1", "entrustId": "CANCELED-1",
                "status": "6", "statusDesc": "已撤销", "entrustAmount": "10",
                "businessAmount": "0", "unBusinessAmount": "10", "entrustPrice": "1.60",
            },
        ])

        result = self.history.get_active_orders()

        self.assertEqual([order["order_id"] for order in result], ["OPEN-1", "PARTIAL-1"])
        self.assertEqual(result[0]["symbol"], "MSTU")
        self.assertEqual(result[0]["action"], "buy")
        self.assertEqual(result[0]["quantity"], 10.0)
        self.assertEqual(result[0]["notional"], 15.0)
        self.assertEqual(result[1]["action"], "sell")
        self.assertEqual(result[1]["quantity"], 6.0)
        self.sdk.query_real_entrust_list.assert_called_once_with(
            exchange_type="P", query_count=99, query_param_str="0"
        )

    def test_unknown_order_status_is_conservatively_active(self):
        self.sdk.query_real_entrust_list.return_value = self._response([{
            "stockCode": "TQQQ", "entrustBs": "1", "entrustId": "NEW-STATUS",
            "status": "Z", "entrustAmount": "2", "unBusinessAmount": "2",
            "entrustPrice": "70",
        }])

        result = self.history.get_active_orders()

        self.assertEqual(result[0]["order_id"], "NEW-STATUS")

    def test_malformed_active_order_fails_closed(self):
        self.sdk.query_real_entrust_list.return_value = self._response([{
            "entrustBs": "1", "entrustId": "NO-SYMBOL", "status": "2",
        }])
        with self.assertRaisesRegex(HSTradeHistoryError, "无法识别"):
            self.history.get_active_orders()

    def test_current_entrust_gateway_failure_is_not_an_empty_list(self):
        self.sdk.query_real_entrust_list.return_value = {
            "ok": False, "err": "not logged in",
        }
        with self.assertRaisesRegex(HSTradeHistoryError, "not logged in"):
            self.history.get_active_orders()

    def test_pagination_uses_last_record_cursor_and_reads_next_page(self):
        first_page = [{"recordNo": str(index)} for index in range(99)]
        first_page[-1]["queryParamStr"] = "NEXT-99"
        second_page = [{"recordNo": "last"}]
        method = Mock(side_effect=[
            self._response(first_page),
            self._response(second_page),
        ])

        records = self.history._query_pages(method, "测试分页", exchange_type="P")

        self.assertEqual(len(records), 100)
        self.assertEqual(method.call_args_list[1].kwargs["query_param_str"], "NEXT-99")

    def test_full_page_without_usable_cursor_fails_closed(self):
        page = [{"recordNo": str(index)} for index in range(99)]
        method = Mock(return_value=self._response(page))

        with self.assertRaisesRegex(HSTradeHistoryError, "分页游标"):
            self.history._query_pages(method, "测试分页", exchange_type="P")

    def test_active_buy_without_reliable_exposure_fails_closed(self):
        invalid_orders = [
            {
                "stockCode": "TQQQ", "entrustBs": "1", "entrustId": "NO-QTY",
                "status": "2", "entrustPrice": "70",
            },
            {
                "stockCode": "TQQQ", "entrustBs": "1", "entrustId": "NO-PRICE",
                "status": "2", "entrustAmount": "2", "unBusinessAmount": "2",
                "entrustPrice": "0",
            },
        ]
        for order in invalid_orders:
            with self.subTest(order=order):
                self.sdk.query_real_entrust_list.return_value = self._response([order])
                with self.assertRaisesRegex(HSTradeHistoryError, "敞口"):
                    self.history.get_active_orders()

    def test_numeric_zero_remaining_quantity_is_preserved(self):
        self.sdk.query_real_entrust_list.return_value = self._response([{
            "stockCode": "TQQQ", "entrustBs": "1", "entrustId": "ZERO-LEFT",
            "status": "2", "entrustAmount": 2, "businessAmount": 2,
            "unBusinessAmount": 0, "entrustPrice": 70,
        }])

        result = self.history.get_active_orders()

        self.assertEqual(result[0]["quantity"], 0.0)
        self.assertEqual(result[0]["notional"], 0.0)


if __name__ == "__main__":
    unittest.main()
