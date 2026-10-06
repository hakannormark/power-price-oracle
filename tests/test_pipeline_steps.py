"""Small pieces of the pipeline that broke once and should not break silently again."""

import unittest
from datetime import datetime

from src import pipeline
from src.schedule import is_due
from src.timeutil import TZ


class FxRateTests(unittest.TestCase):
    def test_the_ecb_record_gives_its_rate(self):
        fx = {"pair": "EUR/SEK", "rate": 11.2525, "date": "2026-10-05", "source": "ECB"}
        self.assertEqual(pipeline.fx_rate_of(fx), 11.2525)

    def test_a_missing_or_broken_record_falls_back(self):
        for fx in (None, {}, {"rate": None}, {"rate": "n/a"}, {"rate": 0}):
            self.assertEqual(pipeline.fx_rate_of(fx), pipeline.FALLBACK_FX_RATE)

    def test_the_battery_and_map_steps_receive_a_number(self):
        """The bug: the whole record was passed where a rate was expected."""
        import inspect

        source = inspect.getsource(pipeline.run)
        self.assertNotIn("fx_rate=fx,", source)
        self.assertEqual(source.count("fx_rate=fx_rate"), 2)


class OneRecordedRunPerSlotTests(unittest.TestCase):
    def test_a_second_run_in_the_slot_is_not_due(self):
        first = datetime(2026, 10, 6, 10, 20, tzinfo=TZ)   # the 10:15 slot
        push = datetime(2026, 10, 6, 11, 40, tzinfo=TZ)    # a push run, same slot
        self.assertFalse(is_due(first, push))

    def test_the_first_run_of_the_next_slot_is_due(self):
        first = datetime(2026, 10, 6, 10, 20, tzinfo=TZ)
        after_auction = datetime(2026, 10, 6, 13, 35, tzinfo=TZ)  # the 13:30 slot
        self.assertTrue(is_due(first, after_auction))


if __name__ == "__main__":
    unittest.main()
