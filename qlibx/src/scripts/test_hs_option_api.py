import os
import sys
import unittest
from unittest.mock import Mock


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hs.api.hq_constant import DataType, OptionType
from hs_option_api import HSOptionAPIError, HSOptionMarketData


class TestHSOptionMarketData(unittest.TestCase):
    def setUp(self):
        self.sdk = Mock()
        self.market = HSOptionMarketData(open_api=self.sdk)

    def test_get_chain_uses_official_hs_sdk_and_normalizes_greeks(self):
        self.sdk.query_hq_us_option_chain_expire_date.return_value = {
            "ok": True,
            "data": {"expireDate": ["2026/07/24", "2026/07/31"]},
        }

        def option_codes(**kwargs):
            code = "AAPL260724C1500000" if kwargs["option_type"] == OptionType.CALL else "AAPL260724P1500000"
            return {"ok": True, "data": {"optionCode": [code]}}

        self.sdk.query_hq_us_option_chain_code.side_effect = option_codes

        def quotes(**kwargs):
            security = kwargs["security_list"][0]
            if security["dataType"] == DataType.US_OPTION:
                items = []
                for item in kwargs["security_list"]:
                    items.append({
                        "security": item,
                        "lastPrice": 4.25 if "C" in item["code"] else 3.8,
                        "volume": 120,
                        "optionExData": {
                            "strikePrice": "150",
                            "expireDate": "20260724",
                            "openInterest": "900",
                            "iv": "0.31",
                            "delta": "0.52" if "C" in item["code"] else "-0.48",
                            "gamma": "0.04",
                            "theta": "-0.08",
                            "vega": "0.15",
                        },
                    })
                return {"ok": True, "data": {"basicQot": items}}
            return {"ok": True, "data": {"basicQot": [{
                "security": security,
                "name": "Apple Inc.",
                "lastPrice": 151,
                "lastClosePrice": 149,
                "tradeTime": "2026-07-20 10:00:00",
            }]}}

        self.sdk.query_hq_basic_qot.side_effect = quotes
        self.sdk.query_hq_order_book.return_value = {
            "ok": True,
            "data": {
                "orderBookBidList": [{"price": 4.1, "volume": 12}],
                "orderBookAskList": [{"price": 4.4, "volume": 9}],
            },
        }

        result = self.market.get_chain("US.AAPL", "2026/07/24")

        self.assertEqual(result["source"], "HS SDK")
        self.assertEqual(result["data_type"], DataType.US_OPTION)
        self.assertEqual(result["market_price"], 151)
        self.assertEqual(result["calls"][0]["strike"], 150)
        self.assertEqual(result["calls"][0]["implied_volatility"], 0.31)
        self.assertEqual(result["calls"][0]["bid"], 4.1)
        self.assertEqual(result["calls"][0]["ask"], 4.4)
        self.assertEqual(result["calls"][0]["mid"], 4.25)
        self.assertEqual(result["puts"][0]["delta"], -0.48)
        self.sdk.query_hq_us_option_chain_expire_date.assert_called_once_with(stock_code="AAPL")

    def test_invalid_expiration_is_rejected_before_quote_query(self):
        self.sdk.query_hq_us_option_chain_expire_date.return_value = {
            "ok": True,
            "data": {"expireDate": ["2026/07/24"]},
        }
        with self.assertRaisesRegex(HSOptionAPIError, "不在当前期权链"):
            self.market.get_chain("AAPL", "2026/08/01")
        self.sdk.query_hq_us_option_chain_code.assert_not_called()

    def test_gateway_error_is_exposed_as_adapter_error(self):
        self.sdk.query_hq_us_option_chain_expire_date.return_value = {
            "ok": False,
            "err": "market permission denied",
        }
        with self.assertRaisesRegex(HSOptionAPIError, "market permission denied"):
            self.market.get_expirations("AAPL")


if __name__ == "__main__":
    unittest.main()
