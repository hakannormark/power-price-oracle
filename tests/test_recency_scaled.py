"""The recency level: the one input that differs between tomorrow and next week."""

import unittest
from types import SimpleNamespace

import pandas as pd

from src.models.recency_scaled import RECENT_WEIGHT, RecencyScaled, recency_level
from src.models.shrunk_scaled import shrunk_level

EMPTY = pd.Series(dtype="float64")


def row(lag24=None, lag168=60.0, lag336=60.0, lag504=60.0, lag672=60.0, zone="SE3"):
    return SimpleNamespace(
        zone=zone,
        price_lag_24h=lag24,
        price_lag_168h=lag168,
        price_lag_336h=lag336,
        price_lag_504h=lag504,
        price_lag_672h=lag672,
    )


class RecencyLevelTests(unittest.TestCase):
    def test_yesterday_dominates_when_it_is_known(self):
        level = recency_level(row(lag24=20.0), EMPTY, 40.0)
        self.assertAlmostEqual(level, RECENT_WEIGHT * 20.0 + (1 - RECENT_WEIGHT) * 60.0, places=6)

    def test_an_unknown_yesterday_leaves_the_weekly_level_alone(self):
        # Beyond about a day and a half the lag does not exist, and the model
        # must not pretend to know more about next Friday than it did.
        far = row(lag24=None)
        self.assertAlmostEqual(
            recency_level(far, EMPTY, 40.0), shrunk_level(far, EMPTY, 40.0), places=6
        )

    def test_a_nan_lag_is_treated_as_unknown(self):
        far = row(lag24=float("nan"))
        self.assertAlmostEqual(
            recency_level(far, EMPTY, 40.0), shrunk_level(far, EMPTY, 40.0), places=6
        )

    def test_a_negative_yesterday_pulls_the_level_far_down(self):
        # It does not force the level negative — a weekly level of 60 still
        # carries 30 % of the weight — but it must dominate the move.
        far = row(lag24=None)
        near = row(lag24=-15.0)
        self.assertLess(recency_level(near, EMPTY, 40.0), recency_level(far, EMPTY, 40.0))
        self.assertLess(recency_level(near, EMPTY, 40.0), 20.0)


class RegistryTests(unittest.TestCase):
    def test_it_is_the_default_and_outside_the_ensemble(self):
        from src.models.ensemble import WEIGHTS
        from src.models.registry import DEFAULT_MODEL_ID, model_ids

        self.assertEqual(DEFAULT_MODEL_ID, RecencyScaled.id)
        self.assertIn(RecencyScaled.id, model_ids())
        self.assertNotIn(RecencyScaled.id, WEIGHTS)


if __name__ == "__main__":
    unittest.main()
