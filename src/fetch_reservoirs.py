"""Fetch and store the hydro reservoir history.

    python -m src.fetch_reservoirs [--years 4]

Separate from the scheduled pipeline because the series is weekly and published
with a lag: refetching four years costs four requests and is worth doing rarely,
not three times a day.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta

from .config import ensure_dirs
from .fetch.entsoe_supply import fetch_reservoirs, reservoir_features
from .store import load_reservoirs, upsert_reservoirs
from .timeutil import now_local

log = logging.getLogger("reservoirs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch hydro reservoir levels")
    parser.add_argument("--years", type=int, default=4)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    ensure_dirs()
    now = now_local()
    rows, status = fetch_reservoirs(now - timedelta(days=365 * args.years), now)
    if not status["ok"]:
        print(f"reservoirs: FAILED — {status.get('error')}")
        return 1

    added = upsert_reservoirs(rows)
    stored = load_reservoirs()
    print(f"reservoirs: fetched {len(rows)}, {added} new, {len(stored)} stored")

    features = reservoir_features(stored, now)
    for zone, group in features.groupby("zone"):
        last = group.iloc[-1]
        print(
            f"  {zone}  {last['ts']:%Y-%m-%d}  fyllnad {last['fill_ratio']:.1%}"
            f"  mot normal vecka {last['week_anomaly']:+.1%}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
