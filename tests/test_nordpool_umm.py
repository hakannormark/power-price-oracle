"""Outage handling: direction, deduplication, publication cutoff, ranking."""

import unittest
from datetime import datetime, timedelta

from src.explain.drivers import rank_outages
from src.fetch.nordpool_umm import (
    current_outages,
    dedupe_events,
    hourly_outages,
    known_at,
    latest_versions,
    to_rows,
)
from src.timeutil import TZ


def local(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=TZ)


def message(mid, version, published, units, kind="productionUnits", outdated=False):
    return {
        "messageId": mid,
        "version": version,
        "publicationDate": published,
        "isOutdated": outdated,
        "unavailabilityReason": "Foreseen maintenance",
        kind: units,
    }


PRODUCTION = message(
    "m1", 1, "2026-09-01T06:00:00Z",
    [{
        "name": "Forsmark Block1", "fuelType": 14, "areaName": "SE3",
        "installedCapacity": 1098,
        "timePeriods": [{"unavailableCapacity": 1098,
                         "eventStart": "2026-09-06T10:00:00Z",
                         "eventStop": "2026-09-08T10:00:00Z"}],
    }],
)

CORRIDOR = message(
    "m2", 1, "2026-09-01T06:00:00Z",
    [{
        "name": "SE3 → SE4", "inAreaName": "SE3", "outAreaName": "SE4",
        "installedCapacity": 6200,
        "timePeriods": [{"unavailableCapacity": 2900,
                         "eventStart": "2026-09-06T10:00:00Z",
                         "eventStop": "2026-09-08T10:00:00Z"}],
    }],
    kind="transmissionUnits",
)


class FlattenTests(unittest.TestCase):
    def test_production_unit_is_tagged_with_zone_and_fuel(self):
        row = to_rows([PRODUCTION])[0]
        self.assertEqual(row["zone"], "SE3")
        self.assertEqual(row["fuel"], "kärnkraft")
        self.assertTrue(row["nuclear"])
        self.assertEqual(row["unavailable_mw"], 1098)

    def test_corridor_keeps_both_ends(self):
        row = to_rows([CORRIDOR])[0]
        self.assertEqual(row["kind"], "transmission")
        self.assertEqual(row["from_area"], "SE3")
        self.assertEqual(row["to_area"], "SE4")

    def test_units_outside_sweden_are_dropped(self):
        foreign = message(
            "m9", 1, "2026-09-01T06:00:00Z",
            [{"name": "Kvilldal", "fuelType": 12, "areaName": "NO2",
              "installedCapacity": 1240, "timePeriods": [
                  {"unavailableCapacity": 100, "eventStart": "2026-09-06T10:00:00Z",
                   "eventStop": "2026-09-07T10:00:00Z"}]}],
        )
        self.assertEqual(to_rows([foreign]), [])


class VersionTests(unittest.TestCase):
    def test_a_later_version_supersedes(self):
        v2 = dict(PRODUCTION, version=2, publicationDate="2026-09-02T06:00:00Z")
        rows = latest_versions(to_rows([PRODUCTION, v2]))
        self.assertEqual([r["version"] for r in rows], [2])

    def test_nothing_published_after_the_cutoff_is_visible(self):
        late = message("m3", 1, "2026-09-05T06:00:00Z", PRODUCTION["productionUnits"])
        rows = to_rows([PRODUCTION, late])
        visible = known_at(rows, local(2026, 9, 3))
        self.assertEqual([r["message_id"] for r in visible], ["m1"])

    def test_the_same_outage_under_two_messages_counts_once(self):
        twin = message("m4", 1, "2026-09-01T07:00:00Z", CORRIDOR["transmissionUnits"],
                       kind="transmissionUnits")
        rows = to_rows([CORRIDOR, twin])
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(dedupe_events(rows)), 1)


class HourlyTests(unittest.TestCase):
    def setUp(self):
        self.rows = to_rows([PRODUCTION, CORRIDOR])
        self.frame = hourly_outages(
            self.rows, local(2026, 9, 6), local(2026, 9, 9), as_of=local(2026, 9, 5)
        )

    def test_production_lands_in_its_own_zone(self):
        se3 = self.frame[self.frame.zone == "SE3"]
        self.assertEqual(se3["production_out_mw"].max(), 1098)
        self.assertEqual(se3["nuclear_out_mw"].max(), 1098)

    def test_a_corridor_is_export_for_the_origin_and_import_for_the_destination(self):
        se3 = self.frame[self.frame.zone == "SE3"]
        se4 = self.frame[self.frame.zone == "SE4"]
        self.assertEqual(se3["export_lost_mw"].max(), 2900)
        self.assertEqual(se3["import_lost_mw"].max(), 0)
        self.assertEqual(se4["import_lost_mw"].max(), 2900)
        self.assertEqual(se4["export_lost_mw"].max(), 0)

    def test_hours_outside_the_event_are_zero(self):
        se3 = self.frame[(self.frame.zone == "SE3") & (self.frame.ts >= "2026-09-08T13:00")]
        self.assertEqual(se3["production_out_mw"].max(), 0)

    def test_a_forecast_cannot_see_an_outage_announced_later(self):
        blind = hourly_outages(
            self.rows, local(2026, 9, 6), local(2026, 9, 9), as_of=local(2026, 8, 30)
        )
        self.assertEqual(blind["production_out_mw"].max(), 0)


class RankingTests(unittest.TestCase):
    def test_a_reactor_outranks_a_larger_corridor(self):
        rows = to_rows([PRODUCTION, CORRIDOR])
        items = current_outages(rows, local(2026, 9, 5))["SE3"]["items"]
        self.assertGreater(len(items), 1)
        self.assertTrue(rank_outages(items)[0]["nuclear"])


if __name__ == "__main__":
    unittest.main()
