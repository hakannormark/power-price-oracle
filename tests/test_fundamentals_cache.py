"""ENTSO-E fundamentals: a failed fetch fills in from the last good one, never from a stale one."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.fetch import entsoe_fundamentals as ef
from src.timeutil import TZ

NOW = datetime(2026, 9, 10, 10, 15, tzinfo=TZ)


def frame(zones=("SE3", "SE4"), load=1000.0, hours=6, drop=None):
    rows = []
    for zone in zones:
        for h in range(hours):
            row = {
                "ts": pd.Timestamp(NOW.replace(minute=0) + timedelta(hours=h)),
                "zone": zone,
                "load_forecast_mw": load,
                "wind_forecast_mw": 300.0,
                "solar_forecast_mw": 50.0,
            }
            if drop and zone == drop[0]:
                row[drop[1]] = float("nan")
            rows.append(row)
    return pd.DataFrame(rows, columns=ef.EMPTY_COLUMNS)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = patch.object(ef, "FUNDAMENTALS_CACHE_PATH", Path(self._tmp.name) / "latest.jsonl")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_a_missing_series_is_filled_from_the_last_good_run(self):
        ef.with_cache(frame(), {"ok": True}, NOW - timedelta(hours=8))
        combined, status = ef.with_cache(
            frame(drop=("SE4", "load_forecast_mw")), {"ok": True, "error": "SE4 load: HTTPError"}, NOW
        )
        se4 = combined[combined["zone"] == "SE4"]
        self.assertFalse(se4["load_forecast_mw"].isna().any())
        self.assertEqual(status["cache_filled"], 6)
        self.assertIn("error", status)  # the failure itself stays visible

    def test_a_missing_zone_is_filled_too(self):
        ef.with_cache(frame(), {"ok": True}, NOW - timedelta(hours=8))
        combined, _ = ef.with_cache(frame(zones=("SE3",)), {"ok": True}, NOW)
        self.assertEqual(set(combined["zone"]), {"SE3", "SE4"})

    def test_a_total_failure_runs_on_the_cache(self):
        ef.with_cache(frame(), {"ok": True}, NOW - timedelta(hours=8))
        combined, status = ef.with_cache(pd.DataFrame(columns=ef.EMPTY_COLUMNS), {"ok": False, "error": "503"}, NOW)
        self.assertTrue(status["ok"])
        self.assertEqual(len(combined), 12)

    def test_a_stale_cache_is_not_used(self):
        ef.with_cache(frame(), {"ok": True}, NOW - timedelta(hours=60))
        combined, status = ef.with_cache(pd.DataFrame(columns=ef.EMPTY_COLUMNS), {"ok": False}, NOW)
        self.assertTrue(combined.empty)
        self.assertNotIn("cache_filled", status)

    def test_fresh_values_win(self):
        ef.with_cache(frame(load=1000.0), {"ok": True}, NOW - timedelta(hours=8))
        combined, status = ef.with_cache(frame(load=1500.0), {"ok": True}, NOW)
        self.assertTrue((combined["load_forecast_mw"] == 1500.0).all())
        self.assertNotIn("cache_filled", status)

    def test_timestamps_merge_with_the_feature_frame(self):
        combined, _ = ef.with_cache(frame(), {"ok": True}, NOW)
        target = pd.DataFrame({"ts": frame()["ts"], "zone": frame()["zone"]})
        merged = target.merge(combined, on=["ts", "zone"], how="left")
        self.assertFalse(merged["load_forecast_mw"].isna().any())


if __name__ == "__main__":
    unittest.main()
