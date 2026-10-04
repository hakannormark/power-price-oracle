"""Tests for the BESS & home battery valuation module."""

import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from src.bess.dispatch import simulate_battery_dispatch
from src.bess.lifecycle import compute_lifecycle
from src.bess.offers import OFFERS, get_offer
from src.bess.profiles import generate_household_profiles
from src.bess.publish import build_bess_payload
from src.timeutil import TZ


class BessOffersTests(unittest.TestCase):
    def test_six_offers_defined(self):
        self.assertEqual(len(OFFERS), 6)
        ids = [o.id for o in OFFERS]
        self.assertIn("solis_dyness_15", ids)
        self.assertIn("sigenergy_18", ids)
        self.assertIn("svea_solar_10", ids)
        self.assertIn("greenely_polarium_13_8", ids)
        self.assertIn("elteknik_saj_15", ids)
        self.assertIn("evify_saj_15", ids)

    def test_deduction_capping_one_vs_two_owners(self):
        # Solis gross 53 500 kr: under 103k -> same for 1 and 2 owners
        solis = get_offer("solis_dyness_15")
        self.assertAlmostEqual(solis.net_price(1), solis.net_price(2), delta=1.0)
        self.assertEqual(solis.deduction_lost(1), 0.0)

        # Elteknik gross 113 200 kr: spränger 50k tak vid 1 ägare
        elteknik = get_offer("elteknik_saj_15")
        self.assertGreater(elteknik.net_price(1), elteknik.net_price(2))
        self.assertGreater(elteknik.deduction_lost(1), 4_000.0)

    def test_c_rate_power_limits(self):
        saj = get_offer("elteknik_saj_15")
        self.assertEqual(saj.c_rate, 0.50)
        self.assertEqual(saj.battery_max_power_kw, 7.5)


class BessProfileTests(unittest.TestCase):
    def test_profile_generation(self):
        now = datetime(2026, 1, 1, 0, tzinfo=TZ)
        timestamps = [now + timedelta(hours=i) for i in range(8760)]
        df = generate_household_profiles(timestamps, annual_load_kwh=8000.0, annual_pv_kwh=7000.0)
        self.assertEqual(len(df), 8760)
        self.assertAlmostEqual(df["load_kwh"].sum(), 8000.0, delta=50.0)
        self.assertAlmostEqual(df["pv_kwh"].sum(), 7000.0, delta=50.0)
        # Solar should be 0 at night
        night_hours = df[df["hour"].isin([0, 1, 2, 22, 23])]
        self.assertEqual(night_hours["pv_kwh"].max(), 0.0)


class BessDispatchTests(unittest.TestCase):
    def test_dispatch_respects_soc_limits(self):
        now = datetime(2026, 6, 1, 0, tzinfo=TZ)
        timestamps = [now + timedelta(hours=i) for i in range(168)]  # 1 week
        profile_df = generate_household_profiles(timestamps)
        spot = np.array([0.70] * 168)
        solis = get_offer("solis_dyness_15")

        res = simulate_battery_dispatch(profile_df, spot, solis, strategy="mixed")
        self.assertGreater(res.total_annual_value_sek, 0.0)
        self.assertGreater(res.solar_savings_sek, 0.0)
        soc = res.hourly_df["soc"].to_numpy()
        self.assertTrue(np.all(soc >= 0.0))
        self.assertTrue(np.all(soc <= solis.usable_kwh + 0.01))


class BessLifecycleTests(unittest.TestCase):
    def test_lifecycle_payback_and_npv(self):
        now = datetime(2026, 1, 1, 0, tzinfo=TZ)
        timestamps = [now + timedelta(hours=i) for i in range(8760)]
        profile_df = generate_household_profiles(timestamps)
        spot = np.array([0.85] * 8760)
        solis = get_offer("solis_dyness_15")

        disp = simulate_battery_dispatch(profile_df, spot, solis, strategy="mixed")
        lc = compute_lifecycle(solis, disp, num_owners=2, scenario="nordic_frequency")
        self.assertIsNotNone(lc.payback_years)
        self.assertLess(lc.payback_years, 8.0)
        self.assertGreater(lc.npv_15y, 0.0)


class BessPublishTests(unittest.TestCase):
    def test_build_bess_payload(self):
        now = datetime(2026, 10, 4, 10, tzinfo=TZ)
        actuals = [
            {"zone": "SE4", "ts": (now - timedelta(hours=i)).isoformat(), "price_eur_mwh": 75.0}
            for i in range(500)
        ]
        payload = build_bess_payload(actuals, fx_rate=11.33, zone="SE4", now=now)
        self.assertIn("offers", payload)
        self.assertIn("advice_sv", payload)
        self.assertEqual(len(payload["offers"]), 6)


if __name__ == "__main__":
    unittest.main()
