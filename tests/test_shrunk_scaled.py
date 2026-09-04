"""The shrunk level: weighting, fallback, and the anomaly it exists to damp."""

import unittest
from types import SimpleNamespace

import pandas as pd

from src.models.shrunk_scaled import LAG_WEIGHT, ShrunkScaled, shrunk_level


def row(lag168, lag336=None, lag504=None, lag672=None, lag24=None, zone="SE3"):
    return SimpleNamespace(
        zone=zone,
        price_lag_24h=lag24,
        price_lag_168h=lag168,
        price_lag_336h=lag336,
        price_lag_504h=lag504,
        price_lag_672h=lag672,
    )


EMPTY_MEANS = pd.Series(dtype="float64")


class ShrunkLevelTests(unittest.TestCase):
    def test_weights_the_lag_against_the_median(self):
        # median(50, 60, 70, 80) = 65
        level = shrunk_level(row(50.0, 60.0, 70.0, 80.0), EMPTY_MEANS, 40.0)
        self.assertAlmostEqual(level, LAG_WEIGHT * 50.0 + (1 - LAG_WEIGHT) * 65.0, places=6)

    def test_a_stable_history_leaves_the_level_alone(self):
        level = shrunk_level(row(60.0, 60.0, 60.0, 60.0), EMPTY_MEANS, 40.0)
        self.assertAlmostEqual(level, 60.0, places=6)

    def test_an_anomalous_week_is_pulled_toward_the_others(self):
        # The case this model exists for: last Saturday collapsed to 2 EUR/MWh
        # while the three before it were normal.
        naive = 2.0
        level = shrunk_level(row(naive, 55.0, 48.0, 61.0), EMPTY_MEANS, 40.0)
        self.assertGreater(level, naive)
        self.assertLess(level, 55.0)

    def test_a_spike_is_damped_in_the_other_direction_too(self):
        level = shrunk_level(row(175.0, 55.0, 48.0, 61.0), EMPTY_MEANS, 40.0)
        self.assertLess(level, 175.0)
        self.assertGreater(level, 61.0)

    def test_too_few_weeks_falls_back_to_the_plain_lag(self):
        # A fresh install has no four-week history; it must still forecast.
        level = shrunk_level(row(70.0, 80.0), EMPTY_MEANS, 40.0)
        self.assertAlmostEqual(level, 70.0, places=6)

    def test_missing_lag_falls_back_through_the_naive_chain(self):
        level = shrunk_level(row(None, lag24=45.0), EMPTY_MEANS, 40.0)
        self.assertAlmostEqual(level, 45.0, places=6)

    def test_nan_lags_are_ignored_not_counted(self):
        level = shrunk_level(
            row(70.0, float("nan"), float("nan"), float("nan")), EMPTY_MEANS, 40.0
        )
        self.assertAlmostEqual(level, 70.0, places=6)


class RegistryTests(unittest.TestCase):
    def test_model_is_registered_but_stays_out_of_the_ensemble(self):
        from src.models.ensemble import WEIGHTS
        from src.models.registry import BASE_MODELS, model_ids

        self.assertIn("shrunk_scaled", model_ids())
        self.assertIn(ShrunkScaled.id, [m.id for m in BASE_MODELS])
        # It competes on the accuracy page before it is allowed to move the
        # number the site publishes.
        self.assertNotIn("shrunk_scaled", WEIGHTS)


if __name__ == "__main__":
    unittest.main()
