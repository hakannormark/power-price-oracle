"""The fallback price source: shape, units, and standing in for ENTSO-E."""

import unittest
from datetime import datetime
from unittest.mock import patch

from src.fetch import elpriset
from src.timeutil import TZ

SAMPLE = [
    {"SEK_per_kWh": 1.86937, "EUR_per_kWh": 0.16806, "EXR": 11.123238,
     "time_start": "2026-09-07T00:00:00+02:00", "time_end": "2026-09-07T00:15:00+02:00"},
    {"SEK_per_kWh": 1.80000, "EUR_per_kWh": 0.16000, "EXR": 11.123238,
     "time_start": "2026-09-07T00:15:00+02:00", "time_end": "2026-09-07T00:30:00+02:00"},
    {"SEK_per_kWh": 1.70000, "EUR_per_kWh": 0.15000, "EXR": 11.123238,
     "time_start": "2026-09-07T00:30:00+02:00", "time_end": "2026-09-07T00:45:00+02:00"},
    {"SEK_per_kWh": 1.60000, "EUR_per_kWh": 0.14000, "EXR": 11.123238,
     "time_start": "2026-09-07T00:45:00+02:00", "time_end": "2026-09-07T01:00:00+02:00"},
]


class Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class UnitTests(unittest.TestCase):
    def test_eur_per_kwh_becomes_eur_per_mwh(self):
        with patch.object(elpriset, "get", return_value=Response(SAMPLE)):
            rows = elpriset.fetch_day(datetime(2026, 9, 7, tzinfo=TZ), "SE3")
        self.assertAlmostEqual(rows[0]["price_eur_mwh"], 168.06, places=2)

    def test_resolution_comes_from_the_interval(self):
        with patch.object(elpriset, "get", return_value=Response(SAMPLE)):
            rows = elpriset.fetch_day(datetime(2026, 9, 7, tzinfo=TZ), "SE3")
        self.assertEqual(rows[0]["resolution"], "PT15M")

    def test_provenance_is_recorded(self):
        with patch.object(elpriset, "get", return_value=Response(SAMPLE)):
            rows = elpriset.fetch_day(datetime(2026, 9, 7, tzinfo=TZ), "SE3")
        self.assertTrue(all(r["via"] == "elpriset" for r in rows))

    def test_quarters_average_into_the_hour(self):
        # The feed's own exchange rate is ignored on purpose; the site converts
        # through the ECB rate and mixing two would make a figure unreproducible.
        with patch.object(elpriset, "get", return_value=Response(SAMPLE)):
            rows, status = elpriset.fetch_prices(
                datetime(2026, 9, 7, tzinfo=TZ), datetime(2026, 9, 8, tzinfo=TZ), ["SE3"]
            )
        self.assertTrue(status["ok"])
        self.assertEqual(len(rows), 1)
        # (168.06 + 160 + 150 + 140) / 4
        self.assertAlmostEqual(rows[0]["price_eur_mwh"], 154.515, places=2)
        self.assertEqual(rows[0]["resolution"], "PT60M")

    def test_native_keeps_every_quarter(self):
        with patch.object(elpriset, "get", return_value=Response(SAMPLE)):
            rows, _ = elpriset.fetch_prices(
                datetime(2026, 9, 7, tzinfo=TZ), datetime(2026, 9, 8, tzinfo=TZ),
                ["SE3"], native=True,
            )
        self.assertEqual(len(rows), 4)


class WindowTests(unittest.TestCase):
    def requested_days(self, start, end, now):
        urls = []

        def fake_get(url, **_):
            urls.append(url)
            return Response(SAMPLE)

        with patch.object(elpriset, "get", side_effect=fake_get):
            elpriset.fetch_prices(start, end, ["SE3"], now=now)
        return [url.rsplit("/", 1)[1][:5] for url in urls]

    def test_end_is_exclusive(self):
        days = self.requested_days(
            datetime(2026, 9, 7, tzinfo=TZ), datetime(2026, 9, 9, tzinfo=TZ),
            datetime(2026, 9, 9, 15, tzinfo=TZ),
        )
        self.assertEqual(days, ["09-07", "09-08"])

    def test_tomorrow_is_not_requested_before_the_auction(self):
        days = self.requested_days(
            datetime(2026, 9, 7, tzinfo=TZ), datetime(2026, 9, 10, tzinfo=TZ),
            datetime(2026, 9, 8, 10, tzinfo=TZ),
        )
        self.assertEqual(days, ["09-07", "09-08"])

    def test_tomorrow_is_requested_once_published(self):
        days = self.requested_days(
            datetime(2026, 9, 7, tzinfo=TZ), datetime(2026, 9, 10, tzinfo=TZ),
            datetime(2026, 9, 8, 13, tzinfo=TZ),
        )
        self.assertEqual(days, ["09-07", "09-08", "09-09"])


class FailureTests(unittest.TestCase):
    def test_a_failed_day_does_not_raise(self):
        # A fallback that fails loudly is worse than one that reports what it got.
        with patch.object(elpriset, "get", side_effect=RuntimeError("503")):
            rows, status = elpriset.fetch_prices(
                datetime(2026, 9, 7, tzinfo=TZ), datetime(2026, 9, 8, tzinfo=TZ), ["SE3"]
            )
        self.assertEqual(rows, [])
        self.assertFalse(status["ok"])
        self.assertIn("error", status)

    def test_zones_outside_sweden_are_ignored(self):
        rows, status = elpriset.fetch_prices(
            datetime(2026, 9, 7, tzinfo=TZ), datetime(2026, 9, 8, tzinfo=TZ), ["DE_LU"]
        )
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
