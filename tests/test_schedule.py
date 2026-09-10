"""Schedule slots are Stockholm wall-clock times, whatever the UTC offset."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.config import AUCTION_CUTOFF_HOUR, AUCTION_CUTOFF_MINUTE
from src.schedule import SLOTS_LOCAL, is_due, last_run_from_status, next_slot, slot_start
from src.timeutil import TZ, UTC


def local(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


class SlotTests(unittest.TestCase):
    def test_the_current_slot_is_the_latest_one_passed(self):
        self.assertEqual(slot_start(local(2026, 9, 10, 11)), local(2026, 9, 10, 10, 15))

    def test_before_the_first_slot_belongs_to_yesterday_evening(self):
        self.assertEqual(slot_start(local(2026, 9, 10, 5)), local(2026, 9, 9, 18))

    def test_the_next_slot_after_the_evening_is_tomorrow_morning(self):
        self.assertEqual(next_slot(local(2026, 9, 10, 18, 5)), local(2026, 9, 11, 6, 30))

    def test_the_post_auction_slot_moves_in_utc_with_dst(self):
        # A fixed UTC cron cannot do this: 13:30 is 11:30 UTC in summer and 12:30 in winter.
        summer = slot_start(datetime(2026, 7, 1, 12, 0, tzinfo=UTC))
        winter = slot_start(datetime(2026, 1, 15, 13, 0, tzinfo=UTC))
        self.assertEqual(summer, datetime(2026, 7, 1, 11, 30, tzinfo=UTC))
        self.assertEqual(winter, datetime(2026, 1, 15, 12, 30, tzinfo=UTC))

    def test_slots_straddle_the_auction(self):
        cutoff = (AUCTION_CUTOFF_HOUR, AUCTION_CUTOFF_MINUTE)
        self.assertGreaterEqual(sum(slot < cutoff for slot in SLOTS_LOCAL), 2)
        self.assertTrue(any(slot > cutoff for slot in SLOTS_LOCAL))


class DueTests(unittest.TestCase):
    def test_never_run_is_due(self):
        self.assertTrue(is_due(None, local(2026, 9, 10, 11)))

    def test_a_run_inside_the_current_slot_is_enough(self):
        self.assertFalse(is_due(local(2026, 9, 10, 10, 20), local(2026, 9, 10, 11)))

    def test_a_run_before_the_current_slot_is_not(self):
        self.assertTrue(is_due(local(2026, 9, 10, 7), local(2026, 9, 10, 11)))

    def test_missed_slots_are_caught_up_by_one_run(self):
        last = local(2026, 9, 9, 19)
        self.assertTrue(is_due(last, local(2026, 9, 10, 14)))
        self.assertFalse(is_due(local(2026, 9, 10, 14, 5), local(2026, 9, 10, 14, 40)))


class StatusFileTests(unittest.TestCase):
    def test_generated_at_is_read_from_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(json.dumps({"generated_at": "2026-09-09T20:56:43+02:00"}))
            self.assertEqual(last_run_from_status(path), local(2026, 9, 9, 20, 56).replace(second=43))

    def test_a_missing_status_means_never_run(self):
        self.assertIsNone(last_run_from_status("/nonexistent/status.json"))


if __name__ == "__main__":
    unittest.main()
