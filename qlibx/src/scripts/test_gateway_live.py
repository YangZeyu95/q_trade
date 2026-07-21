"""Opt-in, read-only smoke test for a real Huasheng gateway.

Run explicitly before Live mode:

    RUN_LIVE_GATEWAY_TEST=1 python3 -m unittest scripts/test_gateway_live.py

The default test suite skips this test so ordinary unit tests never depend on
the user's local gateway. It only queries account information and never places
an order.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from huasheng_api import HuashengGatewayAPI


@unittest.skipUnless(
    os.getenv("RUN_LIVE_GATEWAY_TEST") == "1",
    "set RUN_LIVE_GATEWAY_TEST=1 to run the real read-only gateway smoke test",
)
class TestLiveHuashengGateway(unittest.TestCase):
    def test_read_only_connection(self):
        gateway_url = os.getenv("HUASHENG_GATEWAY_URL", "http://127.0.0.1:11111")
        api = HuashengGatewayAPI(gateway_url=gateway_url)
        self.assertTrue(
            api.check_connection(),
            f"Huasheng gateway is unavailable or not logged in: {gateway_url}",
        )


if __name__ == "__main__":
    unittest.main()
