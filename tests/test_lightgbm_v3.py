"""lightgbm_v3 in the pipeline's own shape: a feature frame in, forecast points out."""

import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from src.features.build import RAW_WEATHER_COLUMNS, build_features
from src.models import lightgbm_v3
from src.models.lightgbm_v3 import LightGbmV3
from src.timeutil import TZ, iso

NOW = datetime(2026, 10, 6, 10, 20, tzinfo=TZ)  # before the auction


def inputs(now=NOW, published_through_days=0):
    """Nine weeks of prices up to the end of the last published day, and weather for every point."""
    start = datetime(now.year, now.month, now.day, tzinfo=TZ) - timedelta(days=63)
    end_prices = datetime(now.year, now.month, now.day, tzinfo=TZ) + timedelta(days=1 + published_through_days)
    actuals, t = [], start
    rng = np.random.default_rng(1)
    while t < end_prices:
        for i, zone in enumerate(("SE1", "SE2", "SE3", "SE4")):
            price = 20 + 15 * i + 25 * (t.hour in (7, 8, 18, 19)) + rng.normal(0, 3)
            actuals.append({"ts": iso(t), "zone": zone, "price_eur_mwh": round(float(price), 2)})
        t += timedelta(hours=1)
    hours = pd.date_range(start, start + timedelta(days=63 + 9), freq="h", tz=TZ, inclusive="left")
    frames = []
    for point in ("SE1", "SE2", "SE3", "SE4", "DK2", "DE_NORTH", "NO1"):
        frames.append(pd.DataFrame({
            "ts": hours, "point": point, "temp": 8 + rng.normal(0, 2, len(hours)),
            "wind": 15 + rng.normal(0, 4, len(hours)), "solar": np.clip(rng.normal(80, 60, len(hours)), 0, None),
            "wind_index": 1.0, "temp_anomaly": 0.0, "solar_index": 0.2,
        }))
    weather = pd.concat(frames, ignore_index=True)
    regional = pd.DataFrame({"ts": hours, "wind_index_north": 1.0, "wind_index_south": 1.0})
    return actuals, weather, regional


class FeatureFrameTests(unittest.TestCase):
    def test_raw_weather_is_carried_per_point(self):
        actuals, weather, regional = inputs()
        frame = build_features(actuals, weather, regional, None, NOW)
        for column in RAW_WEATHER_COLUMNS:
            self.assertIn(column, frame.columns)
        row = frame[(frame["zone"] == "SE1")].iloc[100]
        expected = weather[(weather["point"] == "DE_NORTH") & (weather["ts"] == row["ts"])]["wind"].iloc[0]
        self.assertAlmostEqual(row["wx_wind_DE_NORTH"], expected)
        # The same value on every zone's row for that hour.
        same_hour = frame[frame["ts"] == row["ts"]]["wx_wind_DE_NORTH"]
        self.assertEqual(same_hour.nunique(), 1)

    def test_no_weather_leaves_the_columns_empty_not_missing(self):
        actuals, _, _ = inputs()
        frame = build_features(actuals, pd.DataFrame(columns=["ts", "point"]), pd.DataFrame(columns=["ts"]), None, NOW)
        self.assertTrue(frame[RAW_WEATHER_COLUMNS].isna().all().all())


@unittest.skipUnless((lightgbm_v3.ARTIFACTS_DIR / "lightgbm_v3_q50.txt").exists(), "lightgbm_v3 is not trained")
class ServingTests(unittest.TestCase):
    def predict(self, now=NOW, published_through_days=0):
        actuals, weather, regional = inputs(now, published_through_days)
        frame = build_features(actuals, weather, regional, None, now)
        return LightGbmV3().predict(frame, now)

    def test_every_zone_gets_a_week_of_ordered_quantiles(self):
        points = self.predict()
        self.assertEqual({p.zone for p in points}, {"SE1", "SE2", "SE3", "SE4"})
        per_zone = len([p for p in points if p.zone == "SE3"])
        self.assertGreaterEqual(per_zone, 167)
        for p in points:
            self.assertLessEqual(p.p10, p.p50)
            self.assertLessEqual(p.p50, p.p90)
            self.assertTrue(np.isfinite([p.p10, p.p50, p.p90]).all())

    def test_the_band_widens_with_the_lead(self):
        points = [p for p in self.predict() if p.zone == "SE3"]
        day = lambda p: (p.ts.date() - NOW.date()).days  # noqa: E731
        width = lambda d: np.mean([p.p90 - p.p10 for p in points if day(p) == d])  # noqa: E731
        self.assertGreater(width(6), width(1))

    def test_a_run_after_the_auction_also_forecasts(self):
        after = datetime(2026, 10, 6, 13, 35, tzinfo=TZ)
        points = self.predict(after, published_through_days=1)
        self.assertEqual({p.zone for p in points}, {"SE1", "SE2", "SE3", "SE4"})

    def test_missing_artifacts_fall_back_instead_of_failing(self):
        actuals, weather, regional = inputs()
        frame = build_features(actuals, weather, regional, None, NOW)
        model = LightGbmV3()
        original = lightgbm_v3.ARTIFACTS_DIR
        lightgbm_v3.ARTIFACTS_DIR = original / "nowhere"
        try:
            points = model.predict(frame, NOW)
        finally:
            lightgbm_v3.ARTIFACTS_DIR = original
        self.assertTrue(points)


if __name__ == "__main__":
    unittest.main()
