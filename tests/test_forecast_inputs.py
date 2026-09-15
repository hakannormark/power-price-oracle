"""The inputs a forecast was made from must survive the run that made it.

Before these stores existed the fundamentals cache held two days and was
overwritten every run, and the weather the pipeline believed at issue time was
never written down at all — so every weather coefficient could only be scored
against ERA5 reanalysis, which is a perfect hindcast.
"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd

from src import store
from src.fetch import entsoe_fundamentals, open_meteo
from src.fetch_weather_archive import merge_rows, refresh_start
from src.timeutil import TZ, iso


def local(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=TZ)


def observation(ts, seen_at, value, lead_h, zone="SE3", series="load_forecast_mw"):
    return {
        "ts": iso(ts),
        "zone": zone,
        "series": series,
        "value": value,
        "seen_at": iso(seen_at),
        "lead_h": lead_h,
    }


class FundamentalsHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(store, "FUNDAMENTALS_HISTORY_DIR", Path(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_new_hour_is_stored_with_both_ends_equal(self):
        target = local(2026, 9, 20, 18)
        added = store.upsert_fundamentals_history(
            [observation(target, local(2026, 9, 18, 6), 10500.0, 60)]
        )
        self.assertEqual(added, 1)
        rows = store.load_fundamentals_history()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value_first"], 10500.0)
        self.assertEqual(rows[0]["value_last"], 10500.0)
        self.assertEqual(rows[0]["first_lead_h"], 60)
        self.assertEqual(rows[0]["observations"], 1)

    def test_a_later_sighting_updates_only_the_last_end(self):
        target = local(2026, 9, 20, 18)
        store.upsert_fundamentals_history(
            [observation(target, local(2026, 9, 18, 6), 10500.0, 60)]
        )
        added = store.upsert_fundamentals_history(
            [observation(target, local(2026, 9, 19, 18), 11200.0, 24)]
        )
        self.assertEqual(added, 0)  # the same hour, seen again
        row = store.load_fundamentals_history()[0]
        self.assertEqual(row["value_first"], 10500.0)  # the early forecast is preserved
        self.assertEqual(row["value_last"], 11200.0)
        self.assertEqual(row["first_lead_h"], 60)
        self.assertEqual(row["last_lead_h"], 24)
        self.assertEqual(row["observations"], 2)

    def test_an_out_of_order_sighting_still_becomes_the_first(self):
        """A backfill or a replayed run may arrive after a later observation."""
        target = local(2026, 9, 20, 18)
        store.upsert_fundamentals_history(
            [observation(target, local(2026, 9, 19, 18), 11200.0, 24)]
        )
        store.upsert_fundamentals_history(
            [observation(target, local(2026, 9, 17, 6), 9900.0, 84)]
        )
        row = store.load_fundamentals_history()[0]
        self.assertEqual(row["value_first"], 9900.0)
        self.assertEqual(row["first_lead_h"], 84)
        self.assertEqual(row["value_last"], 11200.0)

    def test_series_and_zones_do_not_collide(self):
        target = local(2026, 9, 20, 18)
        seen = local(2026, 9, 19, 6)
        store.upsert_fundamentals_history(
            [
                observation(target, seen, 10500.0, 36),
                observation(target, seen, 2200.0, 36, series="wind_forecast_mw"),
                observation(target, seen, 900.0, 36, zone="SE4"),
            ]
        )
        self.assertEqual(len(store.load_fundamentals_history()), 3)

    def test_weeks_are_partitioned(self):
        """Per ISO week, so folding an hour rewrites a small file and not a year."""
        seen = local(2026, 9, 18)
        store.upsert_fundamentals_history(
            [
                observation(local(2026, 9, 20, 20), seen, 1.0, 68),   # Sunday, week 38
                observation(local(2026, 9, 21, 10), seen, 2.0, 82),   # Monday, week 39
            ]
        )
        files = sorted(p.name for p in Path(self.tmp.name).glob("*.jsonl"))
        self.assertEqual(files, ["2026-W38.jsonl", "2026-W39.jsonl"])
        self.assertEqual(len(store.load_fundamentals_history()), 2)

    def test_a_week_straddling_new_year_stays_one_file(self):
        """31 December 2026 and 1 January 2027 are both ISO week 53 of 2026."""
        seen = local(2026, 12, 30)
        store.upsert_fundamentals_history(
            [
                observation(local(2026, 12, 31, 20), seen, 1.0, 20),
                observation(local(2027, 1, 1, 10), seen, 2.0, 34),
            ]
        )
        files = sorted(p.name for p in Path(self.tmp.name).glob("*.jsonl"))
        self.assertEqual(files, ["2026-W53.jsonl"])
        self.assertEqual(len(store.load_fundamentals_history()), 2)

    def test_loading_since_skips_earlier_hours(self):
        seen = local(2026, 9, 1)
        store.upsert_fundamentals_history(
            [
                observation(local(2026, 9, 5, 10), seen, 1.0, 100),
                observation(local(2026, 9, 25, 10), seen, 2.0, 580),
            ]
        )
        rows = store.load_fundamentals_history(since=local(2026, 9, 20))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value_first"], 2.0)


class HistoryRowShapeTests(unittest.TestCase):
    """Only hours still in the future are forecasts; the rest are observations."""

    def test_fundamentals_rows_drop_hours_already_past(self):
        now = local(2026, 9, 15, 12)
        frame = pd.DataFrame(
            [
                {"ts": pd.Timestamp(local(2026, 9, 15, 6)), "zone": "SE3",
                 "load_forecast_mw": 9000.0, "wind_forecast_mw": 1000.0, "solar_forecast_mw": 50.0},
                {"ts": pd.Timestamp(local(2026, 9, 16, 18)), "zone": "SE3",
                 "load_forecast_mw": 11000.0, "wind_forecast_mw": 2000.0, "solar_forecast_mw": 0.0},
            ]
        )
        rows = entsoe_fundamentals.history_rows(frame, now)
        self.assertTrue(rows)
        self.assertTrue(all(r["ts"].startswith("2026-09-16") for r in rows), rows)
        self.assertEqual({r["lead_h"] for r in rows}, {30})

    def test_fundamentals_rows_are_empty_without_a_fresh_fetch(self):
        self.assertEqual(entsoe_fundamentals.history_rows(None, local(2026, 9, 15)), [])
        self.assertEqual(
            entsoe_fundamentals.history_rows(pd.DataFrame(), local(2026, 9, 15)), []
        )

    def test_weather_rows_carry_the_lead_time_and_skip_the_past(self):
        now = local(2026, 9, 15, 12)
        weather = pd.DataFrame(
            [
                {"ts": pd.Timestamp(local(2026, 9, 15, 6)), "point": "SE3",
                 "temp": 15.0, "wind": 4.0, "solar": 200.0, "precip": 0.0},
                {"ts": pd.Timestamp(local(2026, 9, 17, 12)), "point": "SE3",
                 "temp": 12.0, "wind": 9.0, "solar": 150.0, "precip": 1.5},
            ]
        )
        rows = open_meteo.forecast_log_rows(weather, now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lead_h"], 48)
        self.assertEqual(rows[0]["wind"], 9.0)

    def test_weather_rows_keep_missing_values_as_null(self):
        now = local(2026, 9, 15, 12)
        weather = pd.DataFrame(
            [
                {"ts": pd.Timestamp(local(2026, 9, 16, 12)), "point": "SE1",
                 "temp": None, "wind": 3.0, "solar": float("nan"), "precip": 0.0}
            ]
        )
        rows = open_meteo.forecast_log_rows(weather, now)
        self.assertIsNone(rows[0]["temp"])
        self.assertIsNone(rows[0]["solar"])


class WeatherForecastLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(store, "WEATHER_FORECAST_LOG_DIR", Path(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def _row(self, ts, seen_at, wind, lead_h, point="SE3"):
        return {
            "ts": iso(ts),
            "point": point,
            "temp": 10.0,
            "wind": wind,
            "solar": 100.0,
            "precip": 0.0,
            "seen_at": iso(seen_at),
            "lead_h": lead_h,
        }

    def test_the_forecast_we_first_made_survives_being_revised(self):
        target = local(2026, 9, 20, 18)
        store.upsert_weather_forecast_log(
            [self._row(target, local(2026, 9, 14, 6), 12.0, 156)]
        )
        store.upsert_weather_forecast_log(
            [self._row(target, local(2026, 9, 19, 18), 4.0, 24)]
        )
        row = store.load_weather_forecast_log()[0]
        # The windy long-range forecast and the calm short-range one, both kept:
        # the gap between them is what a horizon-dependent band is fitted on.
        self.assertEqual(row["wind_first"], 12.0)
        self.assertEqual(row["wind_last"], 4.0)
        self.assertEqual(row["first_lead_h"], 156)
        self.assertEqual(row["last_lead_h"], 24)

    def test_points_do_not_collide(self):
        target = local(2026, 9, 20, 18)
        seen = local(2026, 9, 19, 6)
        store.upsert_weather_forecast_log(
            [self._row(target, seen, 4.0, 36), self._row(target, seen, 9.0, 36, point="DE_NORTH")]
        )
        rows = {r["point"]: r for r in store.load_weather_forecast_log()}
        self.assertEqual(set(rows), {"SE3", "DE_NORTH"})
        self.assertEqual(rows["DE_NORTH"]["wind_last"], 9.0)


class ArchiveRefreshTests(unittest.TestCase):
    def test_fresh_values_replace_the_overlapping_tail(self):
        existing = [
            {"ts": "2026-09-01T00:00", "point": "SE3", "temp": 15.0},
            {"ts": "2026-09-02T00:00", "point": "SE3", "temp": 16.0},
        ]
        fresh = [
            {"ts": "2026-09-02T00:00", "point": "SE3", "temp": 16.5},  # revised
            {"ts": "2026-09-03T00:00", "point": "SE3", "temp": 17.0},  # new
        ]
        merged = merge_rows(existing, fresh)
        self.assertEqual([r["ts"] for r in merged], [
            "2026-09-01T00:00", "2026-09-02T00:00", "2026-09-03T00:00"
        ])
        self.assertEqual(merged[1]["temp"], 16.5)
        self.assertEqual(merged[0]["temp"], 15.0)

    def test_a_refresh_starts_just_behind_the_stored_tail(self):
        now = local(2026, 9, 15, 12)
        with mock.patch(
            "src.fetch_weather_archive.last_stored_ts", return_value="2026-08-29T23:00"
        ):
            start = refresh_start("SE3", now, years=4)
        # Three days of overlap, because ERA5 revises what it published recently.
        self.assertEqual(start.date().isoformat(), "2026-08-26")

    def test_an_empty_archive_falls_back_to_the_full_window(self):
        now = local(2026, 9, 15, 12)
        with mock.patch("src.fetch_weather_archive.last_stored_ts", return_value=None):
            start = refresh_start("SE3", now, years=1)
        self.assertLess(start, now - timedelta(days=360))


if __name__ == "__main__":
    unittest.main()
