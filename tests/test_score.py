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


class OneRunPerSlotTests(unittest.TestCase):
    def setUp(self):
        self.now = local(2026, 9, 10, 9)
        self.target = local(2026, 9, 6, 18)

    def test_a_second_run_in_the_same_slot_is_not_scored(self):
        first = forecast_row(local(2026, 9, 4, 10, 20), self.target, p50=50.0)
        again = forecast_row(local(2026, 9, 4, 11, 40), self.target, p50=90.0)
        rows = scored_rows([first, again], [actual_row(self.target)], self.now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]["p50"], 50.0)

    def test_runs_in_different_slots_both_count(self):
        morning = forecast_row(local(2026, 9, 4, 6, 40), self.target)
        later = forecast_row(local(2026, 9, 4, 10, 20), self.target)
        rows = scored_rows([morning, later], [actual_row(self.target)], self.now)
        self.assertEqual(len(rows), 2)


class ComparableHoursTests(unittest.TestCase):
    """0-24h can only hold the small hours of tomorrow; the fair table cuts every
    bucket to the delivery hours all of them forecast."""

    def setUp(self):
        self.now = local(2026, 9, 20, 9)
        forecasts, actuals, self.covered = [], [], []
        # Three delivery days, and the hours a forecast issued at 10:00 the
        # morning before still reaches inside 24 hours: 00 through 09.
        for day in (12, 13, 14):
            for hour in range(10):
                target = local(2026, 9, day, hour)
                actuals.append(actual_row(target, 55.0))
                self.covered.append(target)
                # One issue per horizon bucket, each a morning further back.
                for days_before in range(1, 8):
                    issued = local(2026, 9, day - days_before, 10)
                    forecasts.append(forecast_row(issued, target, "seasonal_naive", 45.0))
        # An evening hour no one-day-ahead forecast can reach: not comparable.
        evening = local(2026, 9, 13, 19)
        actuals.append(actual_row(evening, 200.0))
        forecasts.append(forecast_row(local(2026, 9, 12, 10), evening, "seasonal_naive", 45.0))

        self.result = evaluate(forecasts, actuals, ["seasonal_naive"], now=self.now)

    def test_only_hours_every_horizon_reached_are_counted(self):
        self.assertEqual(self.result["comparable"]["hours"], len(self.covered))

    def test_every_bucket_is_filled_and_equal_on_those_hours(self):
        table = self.result["comparable"]["table"]["SE3"]
        values = [table[bucket]["seasonal_naive"] for bucket in table]
        self.assertEqual(len(values), 7)
        self.assertTrue(all(v is not None for v in values), values)
        # Same hours, same forecast, so the horizon curve is flat by construction.
        self.assertEqual(len(set(values)), 1)

    def test_the_full_table_still_holds_the_extra_hour(self):
        full = self.result["table"]["SE3"]["24-48h"]["seasonal_naive"]
        fair = self.result["comparable"]["table"]["SE3"]["24-48h"]["seasonal_naive"]
        self.assertGreater(full, fair)  # the 200 EUR/MWh evening drags it up


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
        # The measured period, not the window: the first and last scored hour.
        self.assertTrue(result["scored_from"].startswith("2026-09-05T11:00"))
        self.assertTrue(result["scored_to"].startswith("2026-09-06T10:00"))
        # 55 is outside 40-60? No: p10=40, p90=60 -> inside.
        self.assertAlmostEqual(bucket["coverage80"], 1.0, places=3)

    def test_skill_is_measured_on_the_hours_both_models_forecast(self):
        now = local(2026, 9, 10, 9)
        early, late = local(2026, 9, 2, 11), local(2026, 9, 4, 11)
        forecasts, actuals = [], []
        for offset in range(24):
            t_early = early + timedelta(hours=24 + offset)
            t_late = late + timedelta(hours=24 + offset)
            actuals += [actual_row(t_early, 55.0), actual_row(t_late, 55.0)]
            # A bad day for the reference, before the challenger existed.
            forecasts.append(forecast_row(early, t_early, "seasonal_naive", 15.0))
            forecasts.append(forecast_row(late, t_late, "seasonal_naive", 45.0))
            forecasts.append(forecast_row(late, t_late, "weather_scaled", 50.0))

        result = evaluate(forecasts, actuals, ["seasonal_naive", "weather_scaled"], now=now)
        # Against the reference's whole record (MAE 25) this would read 0.8.
        skill = result["zones"]["SE3"]["weather_scaled"]["24-48h"]["skill_vs_naive"]
        self.assertAlmostEqual(skill, 0.5, places=3)

    def test_no_data_yields_an_empty_but_valid_payload(self):
        result = evaluate([], [], ["seasonal_naive"], now=local(2026, 9, 10))
        self.assertEqual(result["scored_points"], 0)
        self.assertIsNone(result["scored_from"])
        self.assertEqual(result["zones"]["SE3"], {})
        self.assertIn("0-24h", result["table"]["SE3"])
        self.assertIsNone(result["table"]["SE3"]["0-24h"]["seasonal_naive"])


if __name__ == "__main__":
    unittest.main()
