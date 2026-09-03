"""ENTSO-E A44 parsing, against a trimmed copy of a real Publication_MarketDocument."""

import unittest

from src.fetch.entsoe_prices import EntsoeError, parse_a44, to_hourly_rows
from src.timeutil import TZ, parse_iso
from tests import fixtures_xml


class ParseA44Tests(unittest.TestCase):
    def test_hourly_document_is_parsed(self):
        points = parse_a44(fixtures_xml.HOURLY)
        self.assertEqual(len(points), 4)
        self.assertEqual(points[0].price_eur_mwh, 71.42)
        # 22:00Z on 2 September is midnight local on 3 September (CEST).
        self.assertEqual(points[0].ts_utc.astimezone(TZ).hour, 0)

    def test_omitted_positions_carry_the_previous_price_forward(self):
        points = parse_a44(fixtures_xml.SPARSE)
        self.assertEqual(len(points), 4)
        self.assertEqual([p.price_eur_mwh for p in points], [40.0, 40.0, 55.5, 55.5])

    def test_quarter_hour_points_average_into_the_hour(self):
        points = parse_a44(fixtures_xml.QUARTER_HOURLY)
        self.assertEqual(len(points), 4)
        rows = to_hourly_rows(points, "SE3", parse_iso("2026-09-02T13:01:00+02:00"))
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["price_eur_mwh"], 25.0, places=3)
        self.assertEqual(rows[0]["resolution"], "PT60M")

    def test_acknowledgement_raises(self):
        with self.assertRaises(EntsoeError):
            parse_a44(fixtures_xml.ACKNOWLEDGEMENT)

    def test_rows_carry_the_zone_and_publication_time(self):
        rows = to_hourly_rows(
            parse_a44(fixtures_xml.HOURLY), "SE3", parse_iso("2026-09-02T13:01:00+02:00")
        )
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["zone"] == "SE3" for row in rows))
        self.assertTrue(all(row["published_at"].startswith("2026-09-02T13:01") for row in rows))
        self.assertEqual(rows[0]["ts"], "2026-09-03T00:00:00+02:00")


if __name__ == "__main__":
    unittest.main()
