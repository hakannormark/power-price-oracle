"""Horizon arithmetic and the auction cutoff — the rules the accuracy numbers rest on."""

import unittest
from datetime import datetime

from src.timeutil import (
    TZ,
    auction_publication_time,
    bucket_for_horizon,
    horizon_hours,
    hour_range,
    is_official_known,
)


def local(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


class HorizonTests(unittest.TestCase):
    def test_horizon_floors_partial_hours(self):
        issued = local(2026, 9, 4, 13, 18)
        target = local(2026, 9, 6, 18)
        self.assertEqual(horizon_hours(issued, target), 52)

    def test_horizon_is_negative_for_past_targets(self):
        issued = local(2026, 9, 4, 13)
        self.assertEqual(horizon_hours(issued, local(2026, 9, 4, 10)), -3)

    def test_horizon_counts_absolute_time_across_dst(self):
        # 26 October 2026 is the autumn transition: that day has 25 hours.
        issued = local(2026, 10, 25, 0)
        target = local(2026, 10, 26, 0)
        self.assertEqual(horizon_hours(issued, target), 25)

    def test_bucket_edges(self):
        self.assertEqual(bucket_for_horizon(0), "0-24h")
        self.assertEqual(bucket_for_horizon(23), "0-24h")
        self.assertEqual(bucket_for_horizon(24), "24-48h")
        self.assertEqual(bucket_for_horizon(167), "144-168h")
        self.assertIsNone(bucket_for_horizon(168))
        self.assertIsNone(bucket_for_horizon(-1))

    def test_hour_range_covers_dst_extra_hour(self):
        hours = hour_range(local(2026, 10, 25, 0), local(2026, 10, 26, 0))
        self.assertEqual(len(hours), 25)
        self.assertEqual(len({h.utcoffset() for h in hours}), 2)


class AuctionCutoffTests(unittest.TestCase):
    def test_publication_is_1245_the_day_before_delivery(self):
        published = auction_publication_time(local(2026, 9, 6, 18))
        self.assertEqual(published, local(2026, 9, 5, 12, 45))

    def test_tomorrow_is_a_forecast_before_the_cutoff(self):
        issued = local(2026, 9, 4, 11, 0)
        self.assertFalse(is_official_known(issued, local(2026, 9, 5, 18)))

    def test_tomorrow_is_fact_after_the_cutoff(self):
        issued = local(2026, 9, 4, 13, 20)
        self.assertTrue(is_official_known(issued, local(2026, 9, 5, 18)))

    def test_day_after_tomorrow_stays_a_forecast(self):
        issued = local(2026, 9, 4, 13, 20)
        self.assertFalse(is_official_known(issued, local(2026, 9, 6, 18)))

    def test_today_is_always_already_published(self):
        issued = local(2026, 9, 4, 0, 5)
        self.assertTrue(is_official_known(issued, local(2026, 9, 4, 18)))


if __name__ == "__main__":
    unittest.main()
