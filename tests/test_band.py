"""The calibrated uncertainty band."""

import unittest

from src.models.band import MIN_BAND_BASE, RESIDUAL_QUANTILES, band_for, forecast_day


class ForecastDayTests(unittest.TestCase):
    def test_first_day_covers_the_first_24_hours(self):
        self.assertEqual(forecast_day(0), 1)
        self.assertEqual(forecast_day(23), 1)
        self.assertEqual(forecast_day(24), 2)
        self.assertEqual(forecast_day(167), 7)

    def test_beyond_the_window_holds_at_day_seven(self):
        self.assertEqual(forecast_day(500), 7)

    def test_negative_horizons_do_not_underflow(self):
        self.assertEqual(forecast_day(-5), 1)


class BandTests(unittest.TestCase):
    def test_the_band_widens_with_horizon(self):
        widths = []
        for horizon in (0, 24, 48, 72, 96):
            low, high = band_for(100.0, horizon)
            widths.append(high - low)
        self.assertEqual(widths, sorted(widths))
        self.assertGreater(widths[-1], widths[0])

    def test_the_band_is_asymmetric_upward(self):
        low, high = band_for(100.0, 0)
        self.assertLess(100.0 - low, high - 100.0)

    def test_a_near_zero_level_still_gets_an_absolute_band(self):
        low, high = band_for(1.5, 0)
        self.assertLess(low, 1.5)
        self.assertGreater(high - low, MIN_BAND_BASE)

    def test_a_negative_level_produces_an_ordered_band(self):
        low, high = band_for(-8.0, 48)
        self.assertLess(low, -8.0)
        self.assertGreater(high, -8.0)

    def test_uncertainty_never_shrinks_with_horizon(self):
        lows = [RESIDUAL_QUANTILES[d][0] for d in sorted(RESIDUAL_QUANTILES)]
        highs = [RESIDUAL_QUANTILES[d][1] for d in sorted(RESIDUAL_QUANTILES)]
        self.assertEqual(lows, sorted(lows, reverse=True))
        self.assertEqual(highs, sorted(highs))


if __name__ == "__main__":
    unittest.main()
