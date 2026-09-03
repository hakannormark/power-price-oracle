"""Generate synthetic demo prices so a clone without an ENTSO-E token still renders.

    python -m src.fixtures --days 14

These are NOT market data. They land in data/fixtures/, never in data/actuals.jsonl,
and every hour built from them is tagged source: "demo" in the API and behind a
banner in the UI. Production always uses ENTSO-E.
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import sys
from datetime import timedelta

from .config import FIXTURE_ACTUALS_PATH, RESOLUTION, ZONES
from .store import r3, write_jsonl
from .timeutil import hour_range, iso, now_local, start_of_day

log = logging.getLogger("fixtures")

SEED = 20260904

# Rough long-run level per zone: the north is hydro-heavy and cheaper, SE4 imports
# the continental price level through Denmark and Germany.
ZONE_BASE_EUR_MWH = {"SE1": 28.0, "SE2": 29.0, "SE3": 48.0, "SE4": 62.0}
ZONE_VOLATILITY = {"SE1": 0.18, "SE2": 0.18, "SE3": 0.34, "SE4": 0.45}

MORNING_PEAK_HOUR = 8
EVENING_PEAK_HOUR = 18


def _diurnal(hour: int) -> float:
    """Two peaks and a night trough, as a multiplier around 1.0."""
    morning = 0.22 * math.exp(-((hour - MORNING_PEAK_HOUR) ** 2) / 6.0)
    evening = 0.30 * math.exp(-((hour - EVENING_PEAK_HOUR) ** 2) / 8.0)
    night = -0.22 * math.exp(-((hour - 3) ** 2) / 10.0)
    return 1.0 + morning + evening + night


def generate(days: int = 14) -> list[dict]:
    rng = random.Random(SEED)
    now = now_local()
    start = start_of_day(now) - timedelta(days=days)
    end = start_of_day(now) + timedelta(days=1)
    timestamps = hour_range(start, end)

    rows: list[dict] = []
    for zone in ZONES:
        base = ZONE_BASE_EUR_MWH[zone]
        volatility = ZONE_VOLATILITY[zone]
        level = base
        for ts in timestamps:
            # Slow mean-reverting walk for the daily level.
            level += 0.12 * (base - level) + rng.gauss(0, base * volatility * 0.09)
            shape = _diurnal(ts.hour)
            weekday = 0.90 if ts.weekday() >= 5 else 1.0
            noise = rng.gauss(1.0, 0.06)
            price = level * shape * weekday * noise

            # Sunny midday hours occasionally push SE3/SE4 to or below zero.
            if zone in {"SE3", "SE4"} and 11 <= ts.hour <= 14 and rng.random() < 0.06:
                price = rng.uniform(-8.0, 4.0)

            rows.append(
                {
                    "ts": iso(ts),
                    "zone": zone,
                    "price_eur_mwh": r3(price),
                    "resolution": RESOLUTION,
                    "published_at": iso(start_of_day(ts) - timedelta(hours=11, minutes=15)),
                    "synthetic": True,
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic demo prices")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    rows = generate(args.days)
    FIXTURE_ACTUALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(FIXTURE_ACTUALS_PATH, rows)
    print(f"fixtures: wrote {len(rows)} synthetic rows to {FIXTURE_ACTUALS_PATH}")
    print("These are not market data. Set ENTSOE_TOKEN for real prices.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
