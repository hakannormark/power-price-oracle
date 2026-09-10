"""Long-term forecast: nothing a model sees may postdate its issue."""

import math
import unittest
from datetime import datetime, timedelta

import pandas as pd

from src.longterm import data as d
from src.longterm.backtest import Context, choose_default, issue_dates, walk_forward
from src.longterm.models import (
    DAMPED,
    FUNDAMENTAL,
    PERSISTENCE,
    damped_prediction,
    fit_damping,
)
from src.timeutil import TZ, iso


def local(y, m, day, h=0):
    return datetime(y, m, day, h, tzinfo=TZ)


def hourly(start, end, price):
    rows = []
    t = start
    while t < end:
        for index, zone in enumerate(("SE1", "SE2", "SE3", "SE4")):
            rows.append({"ts": iso(t), "zone": zone, "price_eur_mwh": price(t, index)})
        t += timedelta(hours=1)
    return rows


def seasonal_price(t, zone_index):
    return 50 + 25 * math.cos((t.month - 1) / 12 * 2 * math.pi) + 8 * zone_index + (t.hour % 24) * 0.5


class IssueTests(unittest.TestCase):
    def test_issues_are_monday_mornings(self):
        issues = issue_dates(local(2024, 1, 3).date(), local(2024, 3, 1))
        self.assertTrue(all(i.weekday() == 0 and i.hour == 6 for i in issues))
        self.assertEqual(issues[0].date().isoformat(), "2024-01-08")

    def test_horizon_one_is_the_next_calendar_month(self):
        months = d.target_months(local(2026, 9, 28, 6), (1, 2, 3))
        self.assertEqual([str(m) for m in months], ["2026-10", "2026-11", "2026-12"])


class CutoffTests(unittest.TestCase):
    def test_the_recent_mean_stops_before_the_issue_day(self):
        rows = hourly(local(2024, 1, 1), local(2024, 3, 10), lambda t, z: 10.0 if t < local(2024, 3, 1) else 1000.0)
        daily = d.daily_prices(rows)
        self.assertAlmostEqual(d.recent_mean(daily, local(2024, 3, 1, 6))["SE3"], 10.0)

    def test_a_reservoir_reading_is_unknown_until_published(self):
        rows = []
        day = local(2022, 9, 5)
        while day < local(2024, 9, 30):
            stored = 5000.0 if day >= local(2024, 9, 20) else 100.0
            rows += [{"ts": iso(day), "zone": z, "stored_mwh": stored} for z in ("SE1", "SE2", "SE3", "SE4")]
            day += timedelta(days=7)
        national = d.national_reservoirs(rows)
        # The 5000 reading is three days old at issue: not yet published.
        self.assertAlmostEqual(d.hydro_anomaly(national, local(2024, 9, 23, 6)), 0.0)

    def test_an_outage_published_later_is_invisible(self):
        row = {
            "kind": "production", "nuclear": True, "unit": "Forsmark Block1", "message_id": "m1",
            "version": 1, "published_at": iso(local(2024, 5, 20)), "unavailable_mw": 1000.0,
            "event_start": iso(local(2024, 6, 1)), "event_stop": iso(local(2024, 7, 1)),
            "outdated": False, "from_area": None, "to_area": None,
        }
        before = d.nuclear_intervals([row], local(2024, 5, 13))
        after = d.nuclear_intervals([row], local(2024, 5, 27))
        self.assertEqual(d.nuclear_out_mw(before, local(2024, 6, 1), local(2024, 7, 1)), 0.0)
        self.assertAlmostEqual(d.nuclear_out_mw(after, local(2024, 6, 1), local(2024, 7, 1)), 1000.0)

    def test_overlapping_messages_about_one_reactor_count_once(self):
        make = lambda mid, mw: (  # noqa: E731
            "Ringhals 4", local(2024, 6, 1), local(2024, 7, 1), mw)
        self.assertAlmostEqual(
            d.nuclear_out_mw([make("a", 1100.0), make("b", 1100.0)], local(2024, 6, 1), local(2024, 7, 1)),
            1100.0,
        )


class LeakageTests(unittest.TestCase):
    """Two histories identical up to July 2024 must give identical forecasts up to then."""

    @classmethod
    def setUpClass(cls):
        start, split, end = local(2022, 10, 1), local(2024, 7, 1), local(2024, 12, 1)
        calm = hourly(start, end, seasonal_price)
        shock = hourly(start, end, lambda t, z: seasonal_price(t, z) * (3.0 if t >= split else 1.0))
        issues = issue_dates(local(2023, 1, 2).date(), local(2024, 6, 25))
        cls.calm = walk_forward(Context(calm, [], [], []), issues)
        cls.shock = walk_forward(Context(shock, [], [], []), issues)

    def test_forecasts_do_not_see_later_outcomes(self):
        for model in (PERSISTENCE, DAMPED, FUNDAMENTAL):
            a = self.calm[model].astype(float).fillna(-1).round(6).tolist()
            b = self.shock[model].astype(float).fillna(-1).round(6).tolist()
            self.assertEqual(a, b, model)

    def test_the_fitted_models_did_engage(self):
        self.assertTrue(self.calm[FUNDAMENTAL].notna().any())
        self.assertTrue((self.calm["damping"].map(tuple) != (0.0, 0.0)).any())


class DampingTests(unittest.TestCase):
    def test_too_little_history_keeps_persistence(self):
        self.assertEqual(fit_damping([]), (0.0, 0.0))

    def test_it_finds_the_share_of_the_seasonal_step_that_held(self):
        rows = [
            {"recent": 50.0, "seasonal_diff": step, "year_mean": 50.0, "actual": 50.0 + 0.5 * step}
            for step in range(-40, 40)
        ]
        self.assertEqual(fit_damping(rows), (0.5, 0.0))
        self.assertAlmostEqual(damped_prediction(rows[0], (0.5, 0.0)), rows[0]["actual"])


class DefaultTests(unittest.TestCase):
    def summary(self, damped):
        cell = lambda mae: {"1": {"mae": mae}, "2": {"mae": mae}}  # noqa: E731
        return {"zones": {"ALL": {PERSISTENCE: cell(20.0), DAMPED: cell(damped), FUNDAMENTAL: cell(30.0)}}}

    def test_a_marginal_win_keeps_the_reference(self):
        self.assertEqual(choose_default(self.summary(19.8)), PERSISTENCE)

    def test_a_clear_win_is_chosen(self):
        self.assertEqual(choose_default(self.summary(19.0)), DAMPED)


if __name__ == "__main__":
    unittest.main()
