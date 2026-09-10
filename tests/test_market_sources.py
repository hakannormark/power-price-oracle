"""Futures, fuels and the seasonal outlook: parsing without the network."""

import unittest
from pathlib import Path

import pandas as pd

from src.fetch.euronext_futures import (
    delivery_period,
    implied_month_price,
    latest_snapshot,
    parse_block,
    product_for,
)
from src.fetch.fuels import parse_chart
from src.fetch.seasonal import parse_outlook
from src.longterm.data import gas_plant_cost

FIXTURE = Path(__file__).parent / "fixtures" / "euronext_power_block.html"


class FuturesParseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = parse_block(FIXTURE.read_text(encoding="utf-8"), "2026-09-10")

    def find(self, product, tenor):
        return next(r for r in self.rows if r["product"] == product and r["tenor"] == tenor)

    def test_system_and_every_swedish_zone_are_read(self):
        self.assertTrue({"SYS", "SE1", "SE2", "SE3", "SE4"} <= {r["product"] for r in self.rows})

    def test_other_areas_are_ignored(self):
        self.assertNotIn(None, {r["product"] for r in self.rows})
        self.assertFalse(any(r["code"].startswith(("ARB", "CPB", "OSB")) for r in self.rows))

    def test_a_settlement_and_its_delivery(self):
        sto = self.find("SE3", "month")
        self.assertEqual(sto["code"], "STBM")
        self.assertEqual(sto["settlement"], -3.75)
        self.assertEqual((sto["delivery_start"], sto["delivery_end"]), ("2026-10-01", "2026-11-01"))

    def test_the_area_price_is_system_plus_epad(self):
        implied = implied_month_price(self.rows, "SE3", pd.Period("2026-10", freq="M"))
        expected = self.find("SYS", "month")["settlement"] + self.find("SE3", "month")["settlement"]
        self.assertEqual(implied["tenor"], "month")
        self.assertAlmostEqual(implied["price"], expected, places=3)

    def test_a_month_without_its_own_contract_uses_the_quarter(self):
        implied = implied_month_price(self.rows, "SE4", pd.Period("2026-11", freq="M"))
        self.assertEqual(implied["tenor"], "quarter")
        self.assertEqual(implied["delivery"], "Q4 2026")


class FuturesHelperTests(unittest.TestCase):
    def test_delivery_periods(self):
        self.assertEqual(delivery_period("Month", "Oct 2026"), ("2026-10-01", "2026-11-01"))
        self.assertEqual(delivery_period("Quarter", "Q4 2026"), ("2026-10-01", "2027-01-01"))
        self.assertEqual(delivery_period("Year", "2027"), ("2027-01-01", "2028-01-01"))
        self.assertEqual(delivery_period("Week", "Week 38 2026"), ("2026-09-14", "2026-09-21"))
        self.assertEqual(delivery_period("Day", "12 Sep 2026"), ("2026-09-12", "2026-09-13"))
        self.assertIsNone(delivery_period("Weekend", "WE 38 2026"))

    def test_products(self):
        self.assertEqual(product_for("Nordic System Price Electricity Base Load"), "SYS")
        self.assertEqual(product_for("Nordic EPAD Electricity Base Load - Sweden MAL"), "SE4")
        self.assertIsNone(product_for("Nordic EPAD Electricity Base Load - Denmark CPH"))

    def test_the_latest_snapshot_is_used(self):
        rows = [{"trade_date": "2026-09-09", "x": 1}, {"trade_date": "2026-09-10", "x": 2}]
        self.assertEqual([r["x"] for r in latest_snapshot(rows)], [2])
        self.assertEqual([r["x"] for r in latest_snapshot(rows, "2026-09-09")], [1])


class FuelTests(unittest.TestCase):
    def test_gaps_are_dropped_and_one_close_per_day_is_kept(self):
        payload = {"chart": {"result": [{
            "timestamp": [1757462400, 1757548800, 1757548900],
            "indicators": {"quote": [{"close": [30.5, None, 31.0]}]},
        }]}}
        self.assertEqual(parse_chart(payload), [("2025-09-10", 30.5), ("2025-09-11", 31.0)])

    def test_an_empty_response_is_empty(self):
        self.assertEqual(parse_chart({"chart": {"result": None}}), [])

    def test_gas_plant_cost(self):
        self.assertAlmostEqual(gas_plant_cost(35.0, 70.0), 2 * 35.0 + 0.37 * 70.0)
        self.assertIsNone(gas_plant_cost(None, 70.0))


class OutlookTests(unittest.TestCase):
    def test_months_and_anomalies(self):
        payload = {"monthly": {
            "time": ["2026-10-01", "2026-11-01"],
            "temperature_2m_anomaly": [0.54, -1.2],
            "precipitation_anomaly": [-3.7, 12.0],
        }}
        self.assertEqual(parse_outlook(payload), [
            {"month": "2026-10", "temp_anomaly_c": 0.5, "precip_anomaly_mm": -3.7},
            {"month": "2026-11", "temp_anomaly_c": -1.2, "precip_anomaly_mm": 12.0},
        ])


if __name__ == "__main__":
    unittest.main()
