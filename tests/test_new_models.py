"""Tests for relative_scaled and lightgbm_v1 models."""

import unittest
from datetime import datetime, timedelta

import pandas as pd

from src.config import ZONES
from src.features.build import build_features
from src.models.lightgbm_v1 import LightGbmV1
from src.models.registry import BASE_MODELS, describe_models, get_model, model_ids
from src.models.relative_scaled import RelativeScaled, compute_relative_scale
from src.timeutil import TZ, now_local, start_of_day


class RelativeScaledModelTests(unittest.TestCase):
    def test_registered_in_base_models(self):
        self.assertIn("relative_scaled", model_ids())
        model = get_model("relative_scaled")
        self.assertIsInstance(model, RelativeScaled)
        self.assertTrue(model.quantiles)
        self.assertFalse(model.derived)

    def test_wind_increase_reduces_scale(self):
        class MockRow:
            def __init__(self, wind, temp=10.0, solar=0.0):
                self.wind_index_local = wind
                self.wind_index_north = wind
                self.wind_index_south = wind
                self.temp_local = temp
                self.temp_anomaly_local = 0.0
                self.solar_index_local = solar

        ref = MockRow(wind=1.0)
        target_windy = MockRow(wind=1.8)
        target_calm = MockRow(wind=0.5)

        scale_windy = compute_relative_scale("SE3", target_windy, ref)
        scale_calm = compute_relative_scale("SE3", target_calm, ref)

        self.assertLess(scale_windy, 1.0)
        self.assertGreater(scale_calm, 1.0)

    def test_cold_temperature_increases_scale(self):
        class MockRow:
            def __init__(self, temp):
                self.wind_index_local = 1.0
                self.wind_index_north = 1.0
                self.wind_index_south = 1.0
                self.temp_local = temp
                self.temp_anomaly_local = 0.0
                self.solar_index_local = 0.0

        ref = MockRow(temp=15.0)
        target_freezing = MockRow(temp=-10.0)

        scale = compute_relative_scale("SE3", target_freezing, ref)
        self.assertGreater(scale, 1.0)

    def test_predict_produces_ordered_quantiles(self):
        now = now_local()
        # Build minimal features
        actuals = [
            {"zone": z, "ts": (now - timedelta(hours=h)).isoformat(), "price_eur_mwh": 50.0}
            for z in ZONES
            for h in range(1, 400)
        ]
        features = build_features(actuals, pd.DataFrame(), pd.DataFrame(), None, now)
        model = RelativeScaled()
        points = model.predict(features, now)

        self.assertGreater(len(points), 0)
        for p in points:
            self.assertLessEqual(p.p10, p.p50)
            self.assertLessEqual(p.p50, p.p90)


class LightGbmModelTests(unittest.TestCase):
    def test_registered_in_base_models(self):
        self.assertIn("lightgbm_v1", model_ids())
        model = get_model("lightgbm_v1")
        self.assertIsInstance(model, LightGbmV1)
        self.assertTrue(model.quantiles)
        self.assertFalse(model.derived)

    def test_predict_produces_valid_quantiles(self):
        now = now_local()
        actuals = [
            {"zone": z, "ts": (now - timedelta(hours=h)).isoformat(), "price_eur_mwh": 45.0}
            for z in ZONES
            for h in range(1, 400)
        ]
        features = build_features(actuals, pd.DataFrame(), pd.DataFrame(), None, now)
        model = LightGbmV1()
        points = model.predict(features, now)

        self.assertGreater(len(points), 0)
        zones_found = {p.zone for p in points}
        self.assertEqual(zones_found, set(ZONES.keys()))

        for p in points:
            self.assertLessEqual(p.p10, p.p50)
            self.assertLessEqual(p.p50, p.p90)
            self.assertGreaterEqual(p.p10, -50.0)

    def test_models_json_payload_includes_new_models(self):
        descriptions = describe_models()
        ids = [d["id"] for d in descriptions]
        self.assertIn("relative_scaled", ids)
        self.assertIn("lightgbm_v1", ids)


if __name__ == "__main__":
    unittest.main()
