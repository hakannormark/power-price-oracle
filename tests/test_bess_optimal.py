"""The home battery is valued by the bill it removes, once."""

import unittest

import numpy as np

from src.bess import optimal as O
from src.bess.dispatch import REALISATION, household_prices, simulate_battery_dispatch
from src.bess.offers import get_offer
from src.bess.profiles import generate_household_profiles
from datetime import datetime, timedelta
from src.timeutil import TZ

DAY = 24


def flat(n, value):
    return np.full(n, float(value))


class OptimalScheduleTests(unittest.TestCase):
    def test_flat_prices_leave_the_battery_idle(self):
        n = 10 * DAY
        load, pv = flat(n, 1.0), flat(n, 0.0)
        s = O.optimal_schedule(load, pv, flat(n, 2.0), flat(n, 0.5), 10.0, 5.0, 0.9)
        self.assertAlmostEqual(s.cost, O.bill_without_battery(load, pv, flat(n, 2.0), flat(n, 0.5)))
        self.assertEqual(s.charged.sum(), 0.0)

    def test_surplus_solar_is_stored_and_used_at_night(self):
        n = 6 * DAY
        hours = np.arange(n) % 24
        pv = np.where((hours >= 10) & (hours < 14), 3.0, 0.0)   # 12 kWh at midday
        load = flat(n, 0.5)                                      # 12 kWh a day, 2 of it under the sun
        imp, exp = flat(n, 2.0), flat(n, 0.5)
        base = O.bill_without_battery(load, pv, imp, exp)
        s = O.optimal_schedule(load, pv, imp, exp, 10.0, 5.0, 0.9)
        # Each stored kWh is worth what it saves on import less what it would have sold for.
        delivered = s.discharged.sum()
        self.assertGreater(delivered, 30.0)
        # The ceiling is every delivered kWh saving the import price. The schedule
        # moves on a grid of storage levels, so a steady 0.5 kWh load is met in
        # steps a little under or over it and a few per cent is lost.
        ceiling = delivered * 2.0 - s.charged.sum() * 0.5
        self.assertLessEqual(base - s.cost, ceiling + 1e-6)
        self.assertGreater(base - s.cost, 0.9 * ceiling)
        self.assertLess(base - s.cost, delivered * 2.0)  # never the full import price

    def test_energy_is_conserved_and_bounds_hold(self):
        rng = np.random.default_rng(3)
        n = 14 * DAY
        load, pv = rng.random(n) * 2, np.clip(rng.normal(1, 1.5, n), 0, None)
        imp, exp = 1 + rng.random(n), 0.2 + 0.5 * rng.random(n)
        s = O.optimal_schedule(load, pv, imp, exp, 8.0, 3.0, 0.9)
        np.testing.assert_allclose(s.grid_import - s.grid_export, load - pv + s.charged - s.discharged, atol=1e-9)
        self.assertTrue((s.soc >= -1e-9).all() and (s.soc <= 8.0 + 1e-9).all())
        self.assertTrue((s.charged <= 3.0 + 1e-9).all() and (s.discharged <= 3.0 + 1e-9).all())
        eta = np.sqrt(0.9)
        prev = np.r_[0.0, s.soc[:-1]]
        np.testing.assert_allclose(s.soc - prev, s.charged * eta - s.discharged / eta, atol=1e-9)
        self.assertLessEqual(s.cost, O.bill_without_battery(load, pv, imp, exp) + 1e-9)

    def test_grid_charging_can_be_forbidden(self):
        n = 8 * DAY
        hours = np.arange(n) % 24
        load, pv = flat(n, 1.0), flat(n, 0.0)
        imp = np.where(hours < 6, 0.5, 3.0)
        solar_only = O.optimal_schedule(load, pv, imp, flat(n, 0.1), 10.0, 5.0, 0.9, allow_grid_charging=False)
        free = O.optimal_schedule(load, pv, imp, flat(n, 0.1), 10.0, 5.0, 0.9)
        self.assertEqual(solar_only.charged.sum(), 0.0)   # no sun, nothing to store
        self.assertGreater(free.charged.sum(), 0.0)
        self.assertLess(free.cost, solar_only.cost)

    def test_no_battery_is_the_plain_bill(self):
        n = DAY
        s = O.optimal_schedule(flat(n, 1), flat(n, 0), flat(n, 2), flat(n, 1), 0.0, 5.0, 0.9)
        self.assertAlmostEqual(s.cost, 48.0)


class ValuationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start = datetime(2025, 1, 1, tzinfo=TZ)
        cls.profile = generate_household_profiles([start + timedelta(hours=i) for i in range(8760)])
        rng = np.random.default_rng(7)
        hours = np.arange(8760) % 24
        cls.spot = np.clip(0.6 + 0.5 * np.isin(hours, (7, 8, 18, 19, 20)) - 0.3 * np.isin(hours, (2, 3, 12, 13)) + rng.normal(0, 0.1, 8760), -0.1, None)
        cls.offer = get_offer("solis_dyness_15")

    def test_cycles_are_the_ones_the_schedule_actually_runs(self):
        res = simulate_battery_dispatch(self.profile, self.spot, self.offer, strategy="energy_only")
        h = res.hourly_df
        charged = float((h["charge_solar"] + h["charge_grid"]).sum())
        self.assertAlmostEqual(res.equivalent_cycles, charged / self.offer.capacity_kwh, delta=0.1)
        self.assertAlmostEqual(res.battery_throughput_kwh, charged, delta=0.5)
        # Solar and arbitrage are parts of that one total, not two separate cycle counts.
        self.assertAlmostEqual(res.solar_self_consumed_kwh + res.arbitrage_charged_kwh, charged, delta=0.5)

    def test_stored_solar_is_worth_less_than_the_import_price(self):
        res = simulate_battery_dispatch(self.profile, self.spot, self.offer, strategy="energy_only")
        import_price, _ = household_prices(self.spot)
        per_kwh = res.solar_savings_sek / max(res.solar_self_consumed_kwh, 1.0)
        self.assertLess(per_kwh, float(import_price.mean()))
        self.assertGreater(per_kwh, 0.2)

    def test_the_parts_add_up_to_the_bill_removed(self):
        res = simulate_battery_dispatch(self.profile, self.spot, self.offer, strategy="energy_only")
        import_price, export_price = household_prices(self.spot)
        load = self.profile["load_kwh"].to_numpy()
        pv = self.profile["pv_kwh"].to_numpy()
        base = O.bill_without_battery(load, pv, import_price, export_price)
        h = res.hourly_df
        with_battery = float((h["grid_import"] * import_price - h["grid_export"] * export_price).sum())
        self.assertAlmostEqual(res.solar_savings_sek + res.arbitrage_profit_sek, (base - with_battery) * REALISATION, delta=2.0)
        self.assertEqual(res.ancillary_revenue_sek, 0.0)

    def test_keeping_room_for_frequency_response_costs_energy_value(self):
        energy = simulate_battery_dispatch(self.profile, self.spot, self.offer, strategy="energy_only")
        mixed = simulate_battery_dispatch(self.profile, self.spot, self.offer, strategy="mixed")
        self.assertLess(mixed.solar_savings_sek + mixed.arbitrage_profit_sek, energy.solar_savings_sek + energy.arbitrage_profit_sek)
        self.assertGreater(mixed.ancillary_revenue_sek, 0.0)

    def test_import_price_carries_vat_on_the_spot_part_and_export_does_not(self):
        imp, exp = household_prices(np.array([1.0]))
        self.assertAlmostEqual(imp[0], 1.25 + 0.535 + 0.28 + 0.04)
        self.assertAlmostEqual(exp[0], 1.06)


if __name__ == "__main__":
    unittest.main()
