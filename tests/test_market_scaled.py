"""market_scaled: the default model's shape at the futures market's level."""

import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from src.fetch.euronext_futures import parse_block, zone_price_for_day
from src.models.base import ForecastPoint
from src.models.market_scaled import MIN_COVERED_HOURS, MarketScaled
from src.timeutil import TZ

FIXTURE = Path(__file__).parent / "fixtures" / "euronext_power_block.html"


def row(product, tenor, code, delivery, start, end, settlement, trade_date="2026-09-10"):
    return {"trade_date": trade_date, "product": product, "tenor": tenor, "code": code,
            "delivery": delivery, "delivery_start": start, "delivery_end": end, "settlement": settlement}


SNAPSHOT = [
    row("SYS", "week", "NSBW", "Week 38 2026", "2026-09-14", "2026-09-21", 100.0),
    row("SE3", "month", "STBM", "Oct 2026", "2026-10-01", "2026-11-01", -4.0),
]


def hours(start, count, zone="SE3", base=50.0):
    return [
        ForecastPoint(ts=start + timedelta(hours=h), zone=zone,
                      p10=base - 20 + h % 24, p50=base + h % 24, p90=base + 60 + h % 24)
        for h in range(count)
    ]


class ZonePriceTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = parse_block(FIXTURE.read_text(encoding="utf-8"), "2026-09-10")

    def find(self, product, tenor):
        return next(r for r in self.snapshot if r["product"] == product and r["tenor"] == tenor)

    def test_next_week_uses_the_week_contract_and_the_next_quoted_epad(self):
        level = zone_price_for_day(self.snapshot, "SE3", date(2026, 9, 15))
        expected = self.find("SYS", "week")["settlement"] + self.find("SE3", "month")["settlement"]
        self.assertAlmostEqual(level["price"], expected, places=3)
        self.assertEqual(level["system_tenor"], "week")
        self.assertTrue(level["epad_proxy"])

    def test_a_quoted_month_uses_its_own_contracts(self):
        level = zone_price_for_day(self.snapshot, "SE4", date(2026, 10, 20))
        expected = self.find("SYS", "month")["settlement"] + self.find("SE4", "month")["settlement"]
        self.assertAlmostEqual(level["price"], expected, places=3)
        self.assertFalse(level["epad_proxy"])

    def test_the_rest_of_the_current_week_has_no_market_price(self):
        self.assertIsNone(zone_price_for_day(self.snapshot, "SE3", date(2026, 9, 11)))


class CombineTests(unittest.TestCase):
    def model(self, snapshot=SNAPSHOT):
        return MarketScaled(futures_loader=lambda: snapshot)

    def test_a_covered_period_is_moved_to_the_market_price_keeping_its_shape(self):
        base = hours(datetime(2026, 9, 14, tzinfo=TZ), 96)
        out = self.model().combine({"shrunk_scaled": base}, datetime(2026, 9, 10, 10, tzinfo=TZ))
        mean = sum(p.p50 for p in out) / len(out)
        self.assertAlmostEqual(mean, 96.0, places=6)
        # Same shape: every hour moved by the same amount.
        shifts = {round(o.p50 - b.p50, 6) for o, b in zip(out, base)}
        self.assertEqual(len(shifts), 1)

    def test_hours_without_a_market_price_are_left_alone(self):
        base = hours(datetime(2026, 9, 11, tzinfo=TZ), 72) + hours(datetime(2026, 9, 14, tzinfo=TZ), 96)
        out = self.model().combine({"shrunk_scaled": base}, datetime(2026, 9, 10, 10, tzinfo=TZ))
        self.assertEqual([o.p50 for o in out[:72]], [b.p50 for b in base[:72]])

    def test_a_thinly_covered_period_is_not_shifted(self):
        base = hours(datetime(2026, 9, 14, tzinfo=TZ), MIN_COVERED_HOURS - 1)
        out = self.model().combine({"shrunk_scaled": base}, datetime(2026, 9, 10, 10, tzinfo=TZ))
        self.assertEqual([o.p50 for o in out], [b.p50 for b in base])

    def test_no_futures_means_no_forecast(self):
        base = hours(datetime(2026, 9, 14, tzinfo=TZ), 96)
        self.assertEqual(self.model(snapshot=[]).combine({"shrunk_scaled": base}, datetime(2026, 9, 10, tzinfo=TZ)), [])

    def test_a_snapshot_from_after_the_issue_is_not_used(self):
        later = [dict(r, trade_date="2026-09-12") for r in SNAPSHOT]
        base = hours(datetime(2026, 9, 14, tzinfo=TZ), 96)
        self.assertEqual(self.model(snapshot=later).combine({"shrunk_scaled": base}, datetime(2026, 9, 10, 10, tzinfo=TZ)), [])


if __name__ == "__main__":
    unittest.main()
