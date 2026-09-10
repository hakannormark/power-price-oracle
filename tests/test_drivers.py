"""Driver text: each event once, reactors together, a headline that summarises."""

import unittest
from datetime import datetime

from src.explain.drivers import outage_bullets, outage_headline
from src.timeutil import TZ, iso


def local(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=TZ)


NOW = local(2026, 9, 10, 12)


def reactor(unit, mw, start, stop, zone="SE3", installed=None, local_=True):
    return {
        "kind": "production", "unit": unit, "zone": zone, "from_area": None, "to_area": None,
        "fuel": "kärnkraft", "nuclear": True, "unavailable_mw": mw,
        "installed_mw": installed or mw, "from": iso(start), "to": iso(stop),
        "reason": "Yearly outage.", "messages": 1, "local": local_,
    }


def link(origin, destination, mw, installed, start, stop):
    return {
        "kind": "transmission", "unit": f"{origin} → {destination}", "zone": None,
        "from_area": origin, "to_area": destination, "fuel": None, "nuclear": False,
        "unavailable_mw": mw, "installed_mw": installed, "from": iso(start), "to": iso(stop),
        "reason": "Foreseen maintenance", "messages": 3, "local": True,
    }


SE3_BLOCK = {
    "items": [
        reactor("Ringhals Block4", 1134, local(2026, 9, 2), local(2026, 11, 30)),
        reactor("Forsmark Block2", 591, local(2026, 9, 16, 1), local(2026, 9, 18), installed=1121),
        link("SE3", "SE4", 2900, 6200, local(2026, 9, 9, 6), local(2026, 10, 9)),
        link("SE2", "SE3", 2500, 7700, local(2026, 9, 9, 6), local(2026, 10, 9)),
        link("FI", "SE3", 1000, 1200, local(2026, 9, 9, 6), local(2026, 10, 9)),
    ]
}


class OutageBulletTests(unittest.TestCase):
    def setUp(self):
        self.lines = outage_bullets(SE3_BLOCK, "SE3", NOW)

    def test_reactors_share_one_bullet_and_corridors_get_the_rest(self):
        self.assertEqual(len(self.lines), 3)
        self.assertTrue(self.lines[0].startswith("Kärnkraft ur drift: "))
        self.assertIn("Ringhals 4 (1\u00a0134 MW, fram till 30 nov)", self.lines[0])
        self.assertIn("Forsmark 2 (591 MW, från 16 sep 01:00 till 18 sep)", self.lines[0])

    def test_corridors_name_direction_and_are_not_repeated(self):
        self.assertIn("från SE3 till SE4", self.lines[1])
        self.assertIn("från SE2 till SE3", self.lines[2])
        self.assertEqual(sum("SE3 till SE4" in line for line in self.lines), 1)

    def test_foreign_zones_are_named(self):
        block = {"items": [link("FI", "SE3", 1000, 1200, local(2026, 9, 9), local(2026, 10, 9))]}
        self.assertIn("från Finland till SE3", outage_bullets(block, "SE3", NOW)[0])

    def test_a_neighbours_reactor_says_where_it_is(self):
        block = {"items": [reactor("Forsmark Block1", 1098, local(2026, 9, 6), local(2026, 11, 17),
                                   local_=False)]}
        self.assertTrue(outage_bullets(block, "SE4", NOW)[0].startswith("Kärnkraft ur drift i SE3: "))


class HeadlineTests(unittest.TestCase):
    def test_the_headline_summarises_instead_of_repeating(self):
        headline = outage_headline("SE3", SE3_BLOCK)
        self.assertEqual(headline, "Två kärnkraftsblock är helt eller delvis ur drift — det stramar åt SE3.")
        self.assertNotIn(headline, outage_bullets(SE3_BLOCK, "SE3", NOW))

    def test_a_corridor_headline_names_no_figures(self):
        block = {"items": [link("SE3", "SE4", 2900, 6200, local(2026, 9, 9), local(2026, 10, 9))]}
        self.assertEqual(
            outage_headline("SE4", block),
            "Begränsad överföring från SE3 till SE4 påverkar priset i SE4.",
        )


if __name__ == "__main__":
    unittest.main()
