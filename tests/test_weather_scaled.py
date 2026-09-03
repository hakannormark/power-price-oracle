"""The weather scaling formula and the quantile ordering guarantees."""

import unittest

from src.models.base import order_quantiles
from src.models.weather_scaled import SCALE_MAX, SCALE_MIN, compute_scale


class ComputeScaleTests(unittest.TestCase):
    def neutral(self, zone="SE3", **overrides):
        args = {
            "wind_index_local": 1.0,
            "wind_index_north": 1.0,
            "wind_index_south": 1.0,
            "temp_anomaly_local": 0.0,
            "solar_index_local": 0.0,
        }
        args.update(overrides)
        return compute_scale(zone, **args)

    def test_normal_weather_leaves_the_level_alone(self):
        self.assertAlmostEqual(self.neutral(), 1.0, places=6)

    def test_wind_above_normal_lowers_the_price(self):
        self.assertLess(self.neutral(wind_index_local=1.5), 1.0)

    def test_calm_weather_raises_the_price(self):
        self.assertGreater(self.neutral(wind_index_local=0.5), 1.0)

    def test_cold_raises_the_price_by_about_15_percent_per_ten_degrees(self):
        self.assertAlmostEqual(self.neutral(temp_anomaly_local=-10.0), 0.85, places=6)
        self.assertAlmostEqual(self.neutral(temp_anomaly_local=10.0), 1.15, places=6)

    def test_midday_sun_dips_the_price(self):
        self.assertLess(self.neutral(solar_index_local=1.0), 1.0)

    def test_northern_zones_lean_harder_on_their_own_wind(self):
        north = self.neutral(zone="SE1", wind_index_local=1.5)
        middle = self.neutral(zone="SE3", wind_index_local=1.5)
        self.assertLess(north, middle)

    def test_northern_zones_do_not_double_count_northern_wind(self):
        # For SE1 the local wind is the northern wind; applying both would
        # exaggerate the same signal twice.
        self.assertAlmostEqual(self.neutral(zone="SE1", wind_index_north=1.5), 1.0, places=6)
        self.assertLess(self.neutral(zone="SE3", wind_index_north=1.5), 1.0)

    def test_se4_reacts_to_southern_wind_and_sun(self):
        self.assertLess(
            self.neutral(zone="SE4", wind_index_south=1.5),
            self.neutral(zone="SE3", wind_index_south=1.5),
        )
        self.assertLess(
            self.neutral(zone="SE4", solar_index_local=1.0),
            self.neutral(zone="SE3", solar_index_local=1.0),
        )

    def test_scale_is_clamped(self):
        self.assertGreaterEqual(self.neutral(wind_index_local=99.0), SCALE_MIN)
        self.assertLessEqual(self.neutral(temp_anomaly_local=-500.0), SCALE_MAX)

    def test_missing_weather_is_neutral_not_fatal(self):
        self.assertAlmostEqual(
            compute_scale("SE3", None, None, None, None, None), 1.0, places=6
        )

    def test_residual_load_replaces_the_wind_term_when_available(self):
        with_fundamentals = compute_scale("SE3", 1.5, 1.0, 1.0, 0.0, 0.0, residual_index=1.2)
        self.assertGreater(with_fundamentals, 1.0)  # tight residual load beats windy proxy


class QuantileOrderTests(unittest.TestCase):
    def test_ordering_is_enforced(self):
        p10, p50, p90 = order_quantiles(90.0, 50.0, 10.0)
        self.assertLessEqual(p10, p50)
        self.assertLessEqual(p50, p90)

    def test_negative_prices_are_allowed_above_the_floor(self):
        p10, p50, p90 = order_quantiles(-30.0, -10.0, 5.0)
        self.assertEqual(p50, -10.0)
        self.assertEqual(p10, -30.0)
        self.assertEqual(p90, 5.0)

    def test_absurdly_negative_values_are_floored(self):
        p10, p50, _ = order_quantiles(-900.0, -800.0, 0.0)
        self.assertGreaterEqual(p10, -50.0)
        self.assertGreaterEqual(p50, -50.0)


if __name__ == "__main__":
    unittest.main()
