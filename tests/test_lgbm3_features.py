"""lightgbm_v3's feature builder never reads what was unknown when the forecast was issued."""

import unittest
from datetime import datetime

import numpy as np

from src.models import lgbm3_features as F
from src.timeutil import TZ

N = 24 * 60
HOUR0 = F.hour_index(datetime(2026, 6, 1, tzinfo=TZ))  # local midnight, no DST change inside


def world(seed=0):
    rng = np.random.default_rng(seed)
    prices = {z: 40 + 30 * rng.random(N) for z in F.ZONE_ORDER}
    weather = {p: {v: rng.random((N, 8)) * 20 for v in ("temp", "wind", "solar")} for p in F.POINTS}
    fuels = {"ttf": 30 + rng.random(N), "eua": 70 + rng.random(N)}
    return prices, weather, fuels


def matrix(prices, weather, fuels, day, hour, lead, age=None):
    target = np.array([day * 24 + hour])
    x, shrunk = F.build_matrix("SE3", target, np.array([lead]), prices, weather, fuels, HOUR0, set(),
                               None if age is None else np.array([age]))
    return dict(zip(F.FEATURE_NAMES, x[0])), shrunk[0]


class NoLookAheadTests(unittest.TestCase):
    def test_prices_after_the_last_known_day_change_nothing(self):
        for lead in range(1, F.MAX_LEAD + 1):
            prices, weather, fuels = world()
            before, shrunk_before = matrix(prices, weather, fuels, 40, 15, lead, lead)
            first_unknown = (40 - lead + 1) * 24  # midnight after the last known day
            for z in prices:
                prices[z][first_unknown:] = 9999.0
            fuels["ttf"][first_unknown - 24:] = 9999.0   # closes from the last known day on
            fuels["eua"][first_unknown - 24:] = 9999.0
            after, shrunk_after = matrix(prices, weather, fuels, 40, 15, lead, lead)
            for name in F.FEATURE_NAMES:
                self.assertEqual(before[name], after[name], f"lead {lead}: {name} read an unknown value")
            self.assertEqual(shrunk_before, shrunk_after)

    def test_the_last_known_day_is_actually_used(self):
        prices, weather, fuels = world()
        before, _ = matrix(prices, weather, fuels, 40, 15, 3, 3)
        prices["SE3"][(40 - 3) * 24 + 15] += 100.0
        after, _ = matrix(prices, weather, fuels, 40, 15, 3, 3)
        self.assertAlmostEqual(after["p_last"] - before["p_last"], 100.0)
        self.assertGreater(after["day_mean"], before["day_mean"])

    def test_weekly_lag_is_dropped_only_when_it_is_still_unknown(self):
        prices, weather, fuels = world()
        row, _ = matrix(prices, weather, fuels, 40, 15, 7, 7)
        self.assertEqual(row["p_lag168"], prices["SE3"][33 * 24 + 15])  # exactly a week back is the last known day
        row, _ = matrix(prices, weather, fuels, 40, 15, 2, 2)
        self.assertEqual(row["p_lag168"], prices["SE3"][33 * 24 + 15])


class DailyStatsTests(unittest.TestCase):
    def test_a_day_is_a_local_day(self):
        prices = np.arange(N, dtype=float)
        stats = F.daily_stats(prices, HOUR0)
        self.assertEqual(stats["mean"][0], np.mean(np.arange(24)))
        self.assertEqual(stats["mean"][23], stats["mean"][0])
        self.assertEqual(stats["mean"][24], np.mean(np.arange(24, 48)))
        self.assertEqual(stats["max"][30], 47.0)

    def test_a_day_without_all_its_prices_has_no_mean(self):
        prices = np.arange(N, dtype=float)
        prices[30:40] = np.nan
        self.assertTrue(np.isnan(F.daily_stats(prices, HOUR0)["mean"][25]))

    def test_hour_indices_do_not_depend_on_the_index_resolution(self):
        import pandas as pd

        base = pd.Series(pd.date_range("2026-06-01", periods=5, freq="h", tz=TZ))
        for unit in ("s", "ms", "ns"):
            self.assertEqual(list(F.hour_indices(base.dt.as_unit(unit)) - HOUR0), [0, 1, 2, 3, 4])


class WeatherAgeTests(unittest.TestCase):
    def test_the_target_reads_the_forecast_of_the_given_age(self):
        prices, weather, fuels = world()
        t = 40 * 24 + 15
        row, _ = matrix(prices, weather, fuels, 40, 15, 4, 4)
        self.assertAlmostEqual(row["temp"], weather["SE3"]["temp"][t, 4])
        self.assertAlmostEqual(row["wind_de"], weather["DE_NORTH"]["wind"][t, 4])
        self.assertAlmostEqual(row["wind_north"], np.mean([weather[p]["wind"][t, 4] for p in F.NORTH]))
        self.assertAlmostEqual(row["hdd"], max(0.0, 15.0 - weather["SE3"]["temp"][t, 4]))

    def test_the_reference_hour_reads_the_newest_forecast(self):
        prices, weather, fuels = world()
        row, _ = matrix(prices, weather, fuels, 40, 15, 4, 4)
        self.assertAlmostEqual(row["temp_ref"], weather["SE3"]["temp"][36 * 24 + 15, 0])

    def test_serving_with_one_column_of_weather_works(self):
        prices, weather, fuels = world()
        narrow = {p: {v: a[:, :1] for v, a in vars_.items()} for p, vars_ in weather.items()}
        row, _ = matrix(prices, narrow, fuels, 40, 15, 5)
        self.assertAlmostEqual(row["temp"], weather["SE3"]["temp"][40 * 24 + 15, 0])


class CalendarTests(unittest.TestCase):
    def test_calendar_is_local_time(self):
        prices, weather, fuels = world()
        row, _ = matrix(prices, weather, fuels, 40, 15, 1, 1)   # 11 July 2026, a Saturday
        self.assertEqual((row["hour"], row["dow"], row["month"], row["is_weekend"]), (15.0, 5.0, 7.0, 1.0))
        self.assertEqual(row["zone"], 2.0)
        self.assertEqual(row["lead"], 1.0)

    def test_lead_is_clipped_to_what_the_model_was_trained_on(self):
        prices, weather, fuels = world()
        row, _ = matrix(prices, weather, fuels, 40, 15, 0)
        self.assertEqual(row["lead"], 1.0)
        row, _ = matrix(prices, weather, fuels, 40, 15, 12)
        self.assertEqual(row["lead"], 7.0)


if __name__ == "__main__":
    unittest.main()
