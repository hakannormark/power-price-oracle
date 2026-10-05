"""Tests for large-scale utility BESS module."""

import unittest
from src.bess.utility import (
    build_utility_bess_payload,
    SVK_REQUIREMENTS,
    HISTORICAL_PRICES,
    PRICE_SCENARIOS,
    ZONE_PROFILES,
    PRESETS,
)


class TestUtilityBess(unittest.TestCase):
    def test_payload_structure(self):
        payload = build_utility_bess_payload()
        self.assertIn("version", payload)
        self.assertIn("requirements", payload)
        self.assertIn("historical_prices", payload)
        self.assertIn("scenarios", payload)
        self.assertIn("zones", payload)
        self.assertIn("presets", payload)
        self.assertIn("timeline", payload)
        self.assertEqual(payload["fx_rate"], 11.30)

    def test_svk_requirements(self):
        # 150 MW BESS vs requirements
        self.assertEqual(SVK_REQUIREMENTS["fcr_d_up"]["2026"], 542)
        self.assertEqual(SVK_REQUIREMENTS["fcr_d_down"]["2026"], 524)
        self.assertEqual(SVK_REQUIREMENTS["fcr_n"]["2026"], 226)
        self.assertEqual(SVK_REQUIREMENTS["afrr_up"]["2026"], 100)
        self.assertEqual(SVK_REQUIREMENTS["afrr_up"]["2030"], 235)
        self.assertEqual(SVK_REQUIREMENTS["mfrr_cap_up"]["2026"], 1400)
        self.assertEqual(SVK_REQUIREMENTS["ffr"]["2026"], 113)

        # Endurance requirements
        self.assertAlmostEqual(SVK_REQUIREMENTS["fcr_d_up"]["endurance_hours"], 0.333, places=2)
        self.assertEqual(SVK_REQUIREMENTS["fcr_n"]["endurance_hours"], 1.0)
        self.assertEqual(SVK_REQUIREMENTS["mfrr_cap_up"]["endurance_hours"], 1.0)

    def test_historical_prices(self):
        # 2025 actual outcomes
        self.assertAlmostEqual(HISTORICAL_PRICES["fcr_d_up"]["2025"], 6.06, places=2)
        self.assertAlmostEqual(HISTORICAL_PRICES["fcr_d_down"]["2025"], 5.91, places=2)
        self.assertAlmostEqual(HISTORICAL_PRICES["fcr_n"]["2025"], 26.85, places=2)
        self.assertAlmostEqual(HISTORICAL_PRICES["mfrr_cap_up"]["2025"], 35.27, places=2)

    def test_zone_profiles(self):
        for z in ["SE1", "SE2", "SE3", "SE4"]:
            self.assertIn(z, ZONE_PROFILES)
            prof = ZONE_PROFILES[z]
            self.assertGreater(prof["mean_spot_sek"], 100)
            self.assertGreater(prof["base_spread_2h_sek"], 150)
            self.assertGreater(prof["peak_price_2h_sek"], prof["trough_price_2h_sek"])

        # Check that SE4 has higher spread and spot than SE2
        self.assertGreater(ZONE_PROFILES["SE4"]["base_spread_2h_sek"], ZONE_PROFILES["SE2"]["base_spread_2h_sek"])
        self.assertGreater(ZONE_PROFILES["SE4"]["mean_spot_sek"], ZONE_PROFILES["SE2"]["mean_spot_sek"])

    def test_bo_preset(self):
        bo = next(p for p in PRESETS if p["id"] == "bo_se2_150mw")
        self.assertEqual(bo["power_mw"], 150.0)
        self.assertEqual(bo["capacity_mwh"], 300.0)
        self.assertEqual(bo["zone"], "SE2")
        self.assertEqual(bo["capex_per_kwh"], 2800.0)
        self.assertEqual(bo["power_capex_share"], 0.40)
        self.assertEqual(bo["cycle_cost_sek_mwh"], 80.0)
        self.assertEqual(bo["bsp_share"], 0.03)
        self.assertEqual(bo["bsp_floor_sek"], 3000000.0)


if __name__ == "__main__":
    unittest.main()
