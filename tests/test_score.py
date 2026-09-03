"""Scoring must ignore forecasts of hours whose price was already published."""

import unittest
from datetime import datetime, timedelta

from src.evaluate.score import evaluate, scored_rows
from src.timeutil import TZ, horizon_hours, iso


def local(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def forecast_row(issued, target, model_id="weather_scaled", p50=50.0, spread=10.0):
    return {
        "issued_at": iso(issued),
        "model_id": model_id,
        "zone": "SE3",
        "ts": iso(target),
        "horizon_h": horizon_hours(issued, target),
        "p10": p50 - spread,
        "p50": p50,
        "p90": p50 + spread,
        "resolution": "PT60M",
        "run_id": "test",
    }


def actual_row(target, price=55.0):
    return {
        "ts": iso(target),
        "zone": "SE3",
        "price_eur_mwh": price,
        "resolution": "PT60M",
        "published_at": iso(target),
    }


class ScoringFilterTests(unittest.TestCase):
    def setUp(self):
        self.now = local(2026, 9, 10, 9)

    def test_a_forecast_issued_before_the_auction_is_scored(self):
        issued = local(2026, 9, 4, 11)
        target = local(2026, 9, 5, 18)
        rows = scored_rows([forecast_row(issued, target)], [actual_row(target)], self.now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]["bucket"], "24-48h")

    def test_a_copy_of_the_published_auction_is_not_scored(self):
        issued = local(2026, 9, 4, 13, 30)  # after 12:45, tomorrow is fact
        target = local(2026, 9, 5, 18)
        rows = scored_rows([forecast_row(issued, target)], [actual_row(target)], self.now)
        self.assertEqual(len(rows), 0)

    def test_hours_without_an_outcome_are_not_scored(self):
        issued = local(2026, 9, 4, 11)
        target = local(2026, 9, 5, 18)
        rows = scored_rows([forecast_row(issued, target)], [], self.now)
        self.assertEqual(len(rows), 0)

    def test_beyond_the_horizon_is_dropped(self):
        issued = local(2026, 9, 1, 11)
        target = issued + timedelta(hours=200)
        rows = scored_rows([forecast_row(issued, target)], [actual_row(target)], self.now)
        self.assertEqual(len(rows), 0)


class MetricsTests(unittest.TestCase):
    def test_metrics_and_skill_are_computed_per_bucket(self):
        now = local(2026, 9, 10, 9)
        issued = local(2026, 9, 4, 11)
        forecasts, actuals = [], []
        # 24 h to 47 h ahead of issue, so every point lands in the same bucket.
        for offset in range(24):
            target = issued + timedelta(hours=24 + offset)
            actuals.append(actual_row(target, 55.0))
            # The reference is 10 EUR off, the challenger 5 EUR off.
            forecasts.append(forecast_row(issued, target, "seasonal_naive", 45.0))
            forecasts.append(forecast_row(issued, target, "weather_scaled", 50.0))

        result = evaluate(forecasts, actuals, ["seasonal_naive", "weather_scaled"], now=now)
        bucket = result["zones"]["SE3"]["weather_scaled"]["24-48h"]

        self.assertGreaterEqual(bucket["n"], 24)
        self.assertTrue(bucket["enough_data"])
        self.assertAlmostEqual(bucket["mae"], 5.0, places=3)
        self.assertAlmostEqual(bucket["bias"], -5.0, places=3)
        self.assertAlmostEqual(bucket["skill_vs_naive"], 0.5, places=3)
        # 55 is outside 40-60? No: p10=40, p90=60 -> inside.
        self.assertAlmostEqual(bucket["coverage80"], 1.0, places=3)

    def test_no_data_yields_an_empty_but_valid_payload(self):
        result = evaluate([], [], ["seasonal_naive"], now=local(2026, 9, 10))
        self.assertEqual(result["scored_points"], 0)
        self.assertEqual(result["zones"]["SE3"], {})
        self.assertIn("0-24h", result["table"]["SE3"])
        self.assertIsNone(result["table"]["SE3"]["0-24h"]["seasonal_naive"])


if __name__ == "__main__":
    unittest.main()
