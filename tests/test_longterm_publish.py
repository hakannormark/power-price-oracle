"""The long-term log: one forecast a day, scored only once its month has ended."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.longterm import publish
from src.timeutil import TZ

PAYLOAD = {
    "zones": {
        "SE3": {
            "months": [
                {
                    "month": "2026-10",
                    "horizon": 1,
                    "models": {
                        "lt_damped": {"p50": 80.0, "p10": 60.0, "p90": 110.0},
                        "lt_market": {"p50": 100.0},
                    },
                }
            ]
        }
    }
}


class LogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "forecasts.jsonl"
        self._patch = patch.object(publish, "LONGTERM_FORECASTS_PATH", self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def rows(self):
        return list(publish.read_jsonl(self.path))

    def test_one_forecast_per_day(self):
        self.assertEqual(publish.record(PAYLOAD, datetime(2026, 9, 10, 6, 30, tzinfo=TZ)), 2)
        self.assertEqual(publish.record(PAYLOAD, datetime(2026, 9, 10, 13, 30, tzinfo=TZ)), 0)
        self.assertEqual(publish.record(PAYLOAD, datetime(2026, 9, 11, 6, 30, tzinfo=TZ)), 2)
        self.assertEqual(len(self.rows()), 4)

    def test_a_month_is_scored_only_once_it_has_an_outcome(self):
        publish.record(PAYLOAD, datetime(2026, 9, 10, 6, 30, tzinfo=TZ))
        pending = publish.score(self.rows(), pd.DataFrame(columns=["SE3"]))
        self.assertEqual(pending["scored"], 0)
        self.assertEqual(pending["first_scoreable_month_label"], "oktober 2026")

        done = pd.DataFrame({"SE3": [90.0]}, index=pd.PeriodIndex(["2026-10"], freq="M"))
        scored = publish.score(self.rows(), done)
        self.assertEqual(scored["scored"], 2)
        self.assertEqual(scored["models"]["lt_damped"]["1"]["mae"], 10.0)
        self.assertEqual(scored["models"]["lt_market"]["1"]["mae"], 10.0)


if __name__ == "__main__":
    unittest.main()
