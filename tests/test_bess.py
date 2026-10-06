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


class BessSolarTests(unittest.TestCase):
    """The sun is the zone's own, measured hour by hour."""

    def _year(self, year=2025):
        start = datetime(year, 1, 1, 0, tzinfo=TZ)
        return [start + timedelta(hours=i) for i in range(8760)]

    def test_yield_rises_southwards_and_is_plausible(self):
        from src.bess import solar

        ts = self._year()
        yields = {z: float(solar.production_per_kwp(z, ts).sum()) for z in ("SE1", "SE2", "SE3", "SE4")}
        self.assertLess(yields["SE1"], yields["SE2"])
        self.assertLess(yields["SE2"], yields["SE3"])
        self.assertLess(yields["SE3"], yields["SE4"])
        # PVGIS 5.2 (EU Joint Research Centre, satellite-based, 14 % system loss)
        # for the same four points, 35 degrees facing south, fetched 2026-10-07.
        # An independent method and data set; the two should agree within a tenth.
        pvgis = {"SE1": 975, "SE2": 903, "SE3": 987, "SE4": 1053}
        mean = {}
        for zone in pvgis:
            years = [float(solar.production_per_kwp(zone, self._year(y)).sum()) for y in range(2015, 2026)]
            mean[zone] = sum(years) / len(years)
            self.assertAlmostEqual(mean[zone] / pvgis[zone], 1.0, delta=0.10, msg=zone)

    def test_east_west_roof_against_pvgis(self):
        from src.bess import solar

        # Mean of PVGIS facing east and facing west, same settings.
        pvgis = {"SE1": 730, "SE2": 682, "SE3": 756, "SE4": 815}
        for zone, ref in pvgis.items():
            years = [float(solar.production_per_kwp(zone, self._year(y), "eastwest").sum()) for y in range(2015, 2026)]
            self.assertAlmostEqual(sum(years) / len(years) / ref, 1.0, delta=0.10, msg=zone)

    def test_heating_follows_the_zone_temperature(self):
        from src.bess import solar
        from src.bess.profiles import HEATING_BASE_C, REFERENCE_DEGREE_DAYS

        ts = self._year()
        north = generate_household_profiles(ts, zone="SE1")
        south = generate_household_profiles(ts, zone="SE4")
        flat = generate_household_profiles(ts, zone="SE1", heating_share=0.0)
        winter = lambda df: df["load_kwh"].to_numpy()[: 59 * 24].sum() / df["load_kwh"].sum()
        self.assertGreater(winter(north), winter(south))
        self.assertGreater(winter(south), winter(flat))
        # The reference climate is Malmö's: its degree days are close to the constant.
        years = [float((HEATING_BASE_C - solar.temperature("SE4", self._year(y))).clip(min=0).sum() / 24) for y in range(2015, 2026)]
        self.assertAlmostEqual(sum(years) / len(years) / REFERENCE_DEGREE_DAYS, 1.0, delta=0.08)

    def test_profile_uses_the_zone(self):
        ts = self._year()
        north = generate_household_profiles(ts, zone="SE1")
        south = generate_household_profiles(ts, zone="SE4")
        self.assertLess(north["pv_kwh"].sum(), south["pv_kwh"].sum())
        # The same house either way.
        self.assertAlmostEqual(north["load_kwh"].sum(), south["load_kwh"].sum(), delta=1.0)
        self.assertGreaterEqual(north["pv_kwh"].min(), 0.0)
        # Measured weather has overcast days; a clear-sky curve does not.
        june = south[south["month"] == 6] if "month" in south else south.iloc[151 * 24:181 * 24]
        daily = june["pv_kwh"].to_numpy()[: 30 * 24].reshape(30, 24).sum(axis=1)
        self.assertLess(daily.min(), 0.5 * daily.max())

    def test_extreme_years_use_the_valuation_they_are_given(self):
        ts = self._year(2026)
        profile_df = generate_household_profiles(ts)
        offer = get_offer("solis_dyness_15")
        disp = simulate_battery_dispatch(profile_df, np.array([0.85] * 8760), offer, strategy="mixed")
        plain = compute_lifecycle(offer, disp, scenario="nordic_frequency")
        given = compute_lifecycle(offer, disp, scenario="nordic_frequency", extreme=(5000.0, 9000.0))
        by_year = {c.year: c for c in given.annual_cash_flows}
        base = {c.year: c for c in plain.annual_cash_flows}
        for year in (5,):
            c = by_year[year]
            self.assertAlmostEqual(c.solar_savings, 5000.0 * c.capacity_retention, delta=1.0)
            self.assertAlmostEqual(c.arbitrage_profit, 9000.0 * c.capacity_retention, delta=1.0)
        # Normal years are untouched.
        self.assertEqual(by_year[1].total_revenue, base[1].total_revenue)
        self.assertEqual(by_year[2].arbitrage_profit, base[2].arbitrage_profit)
        # The weak year takes its own valuation too.
        weak = compute_lifecycle(offer, disp, scenario="nordic_frequency", extreme=(5000.0, 9000.0), weak=(700.0, 100.0))
        c = {x.year: x for x in weak.annual_cash_flows}[10]
        self.assertAlmostEqual(c.solar_savings, 700.0 * c.capacity_retention, delta=1.0)
        self.assertAlmostEqual(c.arbitrage_profit, 100.0 * c.capacity_retention, delta=1.0)
        # Pay for ancillary services is not scaled in extreme years: it only
        # follows capacity fade and the stated market erosion.
        fcr = {x.year: x.ancillary_revenue / x.capacity_retention for x in weak.annual_cash_flows}
        self.assertAlmostEqual(fcr[5] / fcr[4], 0.95, delta=0.002)
        self.assertAlmostEqual(fcr[10] / fcr[9], 0.95, delta=0.002)
        # One extreme year of each kind in the fifteen, and no more.
        kinds = [round(x.extreme_multiplier, 3) != 1.0 for x in weak.annual_cash_flows]
        self.assertEqual([i + 1 for i, k in enumerate(kinds) if k], [5, 10])


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
