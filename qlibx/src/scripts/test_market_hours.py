import unittest
from datetime import datetime, timezone
import os
import sys

import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market_hours import get_market_status


class TestMarketHours(unittest.TestCase):
    def test_regular_session_boundaries(self):
        et = pytz.timezone("America/New_York")
        self.assertFalse(get_market_status(et.localize(datetime(2026, 3, 5, 9, 29)))['is_open'])
        self.assertTrue(get_market_status(et.localize(datetime(2026, 3, 5, 9, 30)))['is_open'])
        self.assertFalse(get_market_status(et.localize(datetime(2026, 3, 5, 16, 0)))['is_open'])
        self.assertFalse(get_market_status(et.localize(datetime(2026, 3, 5, 16, 1)))['is_open'])

    def test_weekend_is_closed(self):
        et = pytz.timezone("America/New_York")
        status = get_market_status(et.localize(datetime(2026, 3, 7, 12, 0)))
        self.assertFalse(status['is_open'])
        self.assertEqual(status['reason'], 'weekend')

    def test_daylight_saving_transition_is_applied(self):
        before_dst = get_market_status(datetime(2026, 3, 6, 15, 0, tzinfo=timezone.utc))
        after_dst = get_market_status(datetime(2026, 3, 9, 14, 0, tzinfo=timezone.utc))
        self.assertEqual(before_dst['local_time'][-6:], '-05:00')
        self.assertEqual(after_dst['local_time'][-6:], '-04:00')
        self.assertTrue(after_dst['is_open'])

    def test_extended_hours_are_explicitly_disabled(self):
        status = get_market_status()
        self.assertFalse(status['extended_hours_enabled'])

    def test_observed_market_holidays_are_closed(self):
        et = pytz.timezone("America/New_York")
        independence_day = get_market_status(
            et.localize(datetime(2026, 7, 3, 12, 0))
        )
        thanksgiving = get_market_status(
            et.localize(datetime(2026, 11, 26, 12, 0))
        )
        self.assertEqual(independence_day['reason'], 'holiday')
        self.assertEqual(independence_day['holiday'], 'independence_day')
        self.assertFalse(thanksgiving['is_open'])

    def test_good_friday_and_cross_year_new_year_observation(self):
        et = pytz.timezone("America/New_York")
        self.assertEqual(
            get_market_status(et.localize(datetime(2026, 4, 3, 12, 0)))['holiday'],
            'good_friday',
        )
        self.assertEqual(
            get_market_status(et.localize(datetime(2021, 12, 31, 12, 0)))['holiday'],
            'new_year',
        )

    def test_standard_half_day_closes_at_one_pm(self):
        et = pytz.timezone("America/New_York")
        before_close = get_market_status(
            et.localize(datetime(2026, 11, 27, 12, 59))
        )
        at_close = get_market_status(
            et.localize(datetime(2026, 11, 27, 13, 0))
        )
        christmas_eve = get_market_status(
            et.localize(datetime(2026, 12, 24, 14, 0))
        )
        self.assertTrue(before_close['is_open'])
        self.assertTrue(before_close['early_close'])
        self.assertFalse(at_close['is_open'])
        self.assertFalse(christmas_eve['is_open'])


if __name__ == '__main__':
    unittest.main()
