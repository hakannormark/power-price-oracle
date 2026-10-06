"""The forecast log is partitioned by day of issue; older weekly files still read."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from src import store
from src.timeutil import TZ, iso


def local(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def row(issued, target, model_id="shrunk_scaled"):
    return {
        "issued_at": iso(issued),
        "model_id": model_id,
        "zone": "SE3",
        "ts": iso(target),
        "horizon_h": 30,
        "p10": 1.0,
        "p50": 2.0,
        "p90": 3.0,
        "resolution": "PT60M",
        "run_id": "test",
    }


class ForecastLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.dir = base / "forecasts"
        self.legacy = base / "forecasts.jsonl"
        self._patches = [
            patch.object(store, "FORECASTS_DIR", self.dir),
            patch.object(store, "LEGACY_FORECASTS_PATH", self.legacy),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def files(self):
        return sorted(p.name for p in self.dir.glob("*.jsonl"))

    def test_rows_land_in_their_day_of_issue(self):
        issued = local(2026, 10, 8, 10, 15)
        store.append_forecasts([row(issued, issued + timedelta(hours=30))])
        self.assertEqual(self.files(), ["2026-10-08.jsonl"])

    def test_the_day_follows_stockholm_time(self):
        issued = local(2026, 10, 12, 0, 30)  # Monday locally, still Sunday in UTC
        store.append_forecasts([row(issued, issued + timedelta(hours=30))])
        self.assertEqual(self.files(), ["2026-10-12.jsonl"])

    def test_a_day_only_rewrites_its_own_file(self):
        a, b = local(2026, 10, 8, 6, 30), local(2026, 10, 9, 6, 30)
        store.append_forecasts([row(a, a + timedelta(hours=30))])
        before = (self.dir / "2026-10-08.jsonl").read_bytes()
        store.append_forecasts([row(b, b + timedelta(hours=30))])
        self.assertEqual((self.dir / "2026-10-08.jsonl").read_bytes(), before)
        self.assertEqual(self.files(), ["2026-10-08.jsonl", "2026-10-09.jsonl"])

    def test_weekly_files_from_before_the_change_are_still_read(self):
        old = local(2026, 9, 10, 10, 15)  # week 37, written under the weekly layout
        store.write_jsonl(self.dir / "2026-W37.jsonl", [row(old, old + timedelta(hours=30))])
        new = local(2026, 10, 8, 10, 15)
        store.append_forecasts([row(new, new + timedelta(hours=30))])
        self.assertEqual(len(store.load_forecasts()), 2)
        self.assertEqual(len(store.load_forecasts(since=local(2026, 10, 1))), 1)

    def test_files_that_cannot_reach_the_window_are_not_read(self):
        old = local(2026, 6, 1, 10)
        recent = local(2026, 9, 10, 10)
        store.write_jsonl(self.dir / "2026-W23.jsonl", [row(old, old + timedelta(hours=30))])
        store.append_forecasts([row(old + timedelta(days=30), old + timedelta(days=31))])
        store.append_forecasts([row(recent, recent + timedelta(hours=30))])

        with patch.object(store, "read_jsonl", wraps=store.read_jsonl) as reader:
            rows = store.load_forecasts(since=local(2026, 9, 1))
        self.assertEqual(len(rows), 1)
        read = {Path(call.args[0]).name for call in reader.call_args_list}
        self.assertNotIn("2026-W23.jsonl", read)
        self.assertNotIn("2026-07-01.jsonl", read)

    def test_the_legacy_log_is_split_and_removed(self):
        a, b = local(2026, 9, 4, 10), local(2026, 9, 9, 10)
        store.write_jsonl(self.legacy, [row(a, a + timedelta(hours=30)), row(b, b + timedelta(hours=30))])

        self.assertEqual(len(store.load_forecasts()), 2)
        self.assertFalse(self.legacy.exists())
        self.assertEqual(self.files(), ["2026-W36.jsonl", "2026-W37.jsonl"])

    def test_an_interrupted_migration_does_not_duplicate(self):
        a = local(2026, 9, 9, 10)
        rows = [row(a, a + timedelta(hours=30))]
        store.write_jsonl(self.legacy, rows)
        store.load_forecasts()
        store.write_jsonl(self.legacy, rows)  # as if unlink had not happened
        self.assertEqual(len(store.load_forecasts()), 1)


if __name__ == "__main__":
    unittest.main()
