"""lightgbm_v4: its features carry no price level, and it serves like v3."""

import unittest
from datetime import date, datetime

import numpy as np

from src.features.build import build_features
from src.models import lgbm3_features as F3
from src.models import lgbm4_features as F
from src.models import lightgbm_v4
from src.models.lightgbm_v4 import LightGbmV4
from src.timeutil import TZ
from tests.test_lightgbm_v3 import NOW, inputs

N = 24 * 60


def arrays(level: float):
    rng = np.random.default_rng(7)
    hours = np.arange(N)
    shape = 1 + 0.3 * np.sin(2 * np.pi * hours / 24) + rng.normal(0, 0.05, N)
    prices = {z: level * (1 + 0.2 * i) * shape for i, z in enumerate(F3.ZONE_ORDER)}
    weather = {
        p: {v: rng.normal(5, 2, (N, 1)) for v in ("temp", "wind", "solar")} for p in F3.POINTS
    }
    fuels = {"ttf": np.full(N, 40.0), "eua": np.full(N, 70.0)}
    return prices, weather, fuels


def matrix(level: float, lead: int = 3):
    prices, weather, fuels = arrays(level)
    target = np.arange(24 * 40, 24 * 50)
    hour0 = F3.hour_index(datetime(2026, 1, 1, tzinfo=TZ))
    return F.build_matrix(
        "SE4", target, np.full(len(target), lead), prices, weather, fuels, hour0, set[date]()
    )


class LevelBlindTests(unittest.TestCase):
    def test_price_features_do_not_change_when_every_price_is_tripled(self):
        x1, _, s1 = matrix(60.0)
        x3, _, s3 = matrix(180.0)
        for name in F.FEATURE_NAMES:
            if name.startswith("r_") and name != "r_gas_gap":
                i = F.FEATURE_NAMES.index(name)
                np.testing.assert_allclose(x1[:, i], x3[:, i], rtol=1e-9, atol=1e-12, err_msg=name)
        np.testing.assert_allclose(s3, 3 * s1)

    def test_no_raw_price_level_is_a_feature(self):
        for raw in ("shrunk", "p_last", "p_lag168", "p_lag336", "day_mean", "week_mean", "ttf", "eua"):
            self.assertNotIn(raw, F.FEATURE_NAMES)

    def test_the_scale_has_a_floor(self):
        _, _, s = matrix(1.0)
        self.assertTrue((s >= F.SCALE_FLOOR).all())

    def test_lead_one_reads_yesterday_and_lead_two_does_not(self):
        # r_last is the same hour on the last known day: one day back at lead 1, two at lead 2.
        x1, shrunk1, s1 = matrix(60.0, lead=1)
        x2, shrunk2, s2 = matrix(60.0, lead=2)
        i = F.FEATURE_NAMES.index("r_last")
        self.assertFalse(np.allclose(x1[:, i] * s1 + shrunk1, x2[:, i] * s2 + shrunk2))


@unittest.skipUnless((lightgbm_v4.ARTIFACTS_DIR / "lightgbm_v4_long_q50.txt").exists(), "lightgbm_v4 is not trained")
class ServingTests(unittest.TestCase):
    def predict(self, now=NOW, published_through_days=0):
        actuals, weather, regional = inputs(now, published_through_days)
        frame = build_features(actuals, weather, regional, None, now)
        return LightGbmV4().predict(frame, now)

    def test_every_zone_gets_a_week_of_ordered_quantiles(self):
        points = self.predict()
        self.assertEqual({p.zone for p in points}, {"SE1", "SE2", "SE3", "SE4"})
        self.assertGreaterEqual(len([p for p in points if p.zone == "SE3"]), 167)
        for p in points:
            self.assertLessEqual(p.p10, p.p50)
            self.assertLessEqual(p.p50, p.p90)
            self.assertTrue(np.isfinite([p.p10, p.p50, p.p90]).all())

    def test_a_run_after_the_auction_also_forecasts(self):
        after = datetime(2026, 10, 6, 13, 35, tzinfo=TZ)
        points = self.predict(after, published_through_days=1)
        self.assertEqual({p.zone for p in points}, {"SE1", "SE2", "SE3", "SE4"})

    def test_missing_artifacts_fall_back_instead_of_failing(self):
        actuals, weather, regional = inputs()
        frame = build_features(actuals, weather, regional, None, NOW)
        original = lightgbm_v4.ARTIFACTS_DIR
        lightgbm_v4.ARTIFACTS_DIR = original / "nowhere"
        try:
            points = LightGbmV4().predict(frame, NOW)
        finally:
            lightgbm_v4.ARTIFACTS_DIR = original
        self.assertTrue(points)


if __name__ == "__main__":
    unittest.main()
