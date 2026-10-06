"""Definition epochs and the promotion table."""

import unittest
from datetime import datetime, timedelta

from src.evaluate import score
from src.timeutil import TZ, iso


def local(day, hour=0):
    return datetime(2026, 10, 1, tzinfo=TZ) + timedelta(days=day, hours=hour)


def rows(model_id, issue_day, errors_by_day):
    """One pre-auction forecast a day; `errors_by_day[d]` is the miss on every hour of day d."""
    out = []
    for d, err in errors_by_day.items():
        issued = local(issue_day + d, 6) + timedelta(minutes=30)
        for hour in range(24):
            ts = local(issue_day + d + 2, hour)
            out.append({
                "issued_at": iso(issued), "model_id": model_id, "zone": "SE3", "ts": iso(ts),
                "horizon_h": int((ts - issued).total_seconds() // 3600),
                "p10": 40 + err - 10, "p50": 50 + err, "p90": 40 + err + 30,
            })
    return out


def actuals(days):
    return [
        {"zone": "SE3", "ts": iso(local(d, h)), "price_eur_mwh": 50.0}
        for d in range(days) for h in range(24)
    ]


NOW = local(40)


class DefinitionEpochTests(unittest.TestCase):
    def test_forecasts_from_an_earlier_definition_are_not_scored(self):
        forecasts = rows("hybrid", 0, {d: 5 for d in range(10)})
        everything = score.scored_rows(forecasts, actuals(14), NOW)
        later = score.scored_rows(forecasts, actuals(14), NOW, {"hybrid": iso(local(6))})
        self.assertEqual(len(everything), 240)
        self.assertEqual(len(later), 96)  # issued on days 6-9 only
        self.assertTrue((later["issued_at"] >= score.pd.Timestamp(iso(local(6))).tz_convert("UTC")).all())

    def test_other_models_are_untouched(self):
        forecasts = rows("hybrid", 0, {d: 5 for d in range(4)}) + rows("naive", 0, {d: 9 for d in range(4)})
        frame = score.scored_rows(forecasts, actuals(8), NOW, {"hybrid": iso(local(30))})
        self.assertEqual(set(frame["model_id"]), {"naive"})


class PromotionTests(unittest.TestCase):
    def table(self, candidate_errors, default_errors):
        days = len(default_errors)
        forecasts = rows("cand", 0, candidate_errors) + rows("default", 0, default_errors)
        frame = score.scored_rows(forecasts, actuals(days + 4), NOW)
        return score.promotion_table(frame, ["cand", "default"], "default")["candidates"]["cand"]

    def test_too_few_days_is_too_early_however_good(self):
        result = self.table({d: 1 for d in range(10)}, {d: 20 for d in range(10)})
        self.assertEqual(result["verdict"], "too_early")
        self.assertEqual(result["overall"]["days"], 10)

    def test_clearly_better_over_enough_days_is_eligible(self):
        result = self.table({d: 2 + d % 3 for d in range(25)}, {d: 12 + d % 5 for d in range(25)})
        self.assertEqual(result["verdict"], "eligible")
        self.assertLess(result["overall"]["hi"], 0)
        self.assertAlmostEqual(result["overall"]["diff"], result["overall"]["mae"] - result["overall"]["mae_default"], places=2)

    def test_clearly_worse_is_called_worse(self):
        result = self.table({d: 15 + d % 4 for d in range(25)}, {d: 3 + d % 2 for d in range(25)})
        self.assertEqual(result["verdict"], "worse")

    def test_a_difference_carried_by_one_day_is_not_shown(self):
        # Better on average only because of a single day; the interval must straddle zero.
        cand = {d: 10 for d in range(25)}
        default = {d: 9 for d in range(25)}
        default[3] = 60
        result = self.table(cand, default)
        self.assertLess(result["overall"]["diff"], 0)
        self.assertGreater(result["overall"]["hi"], 0)
        self.assertEqual(result["verdict"], "not_shown")

    def test_the_interval_is_reproducible(self):
        a = self.table({d: 4 + d % 3 for d in range(25)}, {d: 6 + d % 4 for d in range(25)})
        b = self.table({d: 4 + d % 3 for d in range(25)}, {d: 6 + d % 4 for d in range(25)})
        self.assertEqual(a["overall"], b["overall"])


if __name__ == "__main__":
    unittest.main()
