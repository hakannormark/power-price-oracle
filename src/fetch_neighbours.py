"""Backfill day-ahead prices for the zones that border Sweden.

    python -m src.fetch_neighbours --days 1460 --stage staged.jsonl
    python -m src.fetch_neighbours --merge staged.jsonl

SE4 tracks Denmark and Germany and SE3 tracks Finland and Norway — it is written
on the method page as a known driver — yet the level estimator has only ever seen
a zone's own history. Same ENTSO-E call as the Swedish zones, different domain.

Stored separately from data/actuals so the published API keeps meaning exactly
the four Swedish bidding zones.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

from .config import NEIGHBOUR_DIR, NEIGHBOUR_ZONES, ensure_dirs
from .fetch import entsoe_prices
from .store import read_jsonl, write_jsonl
from .timeutil import now_local, parse_iso, start_of_day

log = logging.getLogger("neighbours")
CHUNK_DAYS = 30


def fetch(days: int) -> list[dict]:
    now = now_local()
    end = start_of_day(now) + timedelta(days=2)
    start = start_of_day(now) - timedelta(days=days)

    rows: list[dict] = []
    for area, meta in NEIGHBOUR_ZONES.items():
        got = 0
        cursor = start
        while cursor < end:
            stop = min(cursor + timedelta(days=CHUNK_DAYS), end)
            try:
                batch = entsoe_prices.fetch_zone(area, cursor, stop, eic=meta["eic"])
            except Exception as exc:  # noqa: BLE001 - degrade per chunk
                log.warning("%s %s: %s", area, cursor.date(), str(exc)[:70])
                cursor = stop
                continue
            rows.extend(batch)
            got += len(batch)
            cursor = stop
        print(f"  {area}: {got:,} hours")
    return rows


def merge(path: Path) -> int:
    """Upsert staged rows, partitioned by year like the Swedish actuals."""
    ensure_dirs()
    NEIGHBOUR_DIR.mkdir(parents=True, exist_ok=True)
    incoming: dict[int, list[dict]] = {}
    for row in read_jsonl(path):
        incoming.setdefault(parse_iso(row["ts"]).year, []).append(row)

    added = 0
    for year, year_rows in incoming.items():
        target = NEIGHBOUR_DIR / f"{year}.jsonl"
        merged = {(r["zone"], r["ts"]): r for r in read_jsonl(target)}
        for row in year_rows:
            if (row["zone"], row["ts"]) not in merged:
                added += 1
            merged[(row["zone"], row["ts"])] = row
        write_jsonl(target, sorted(merged.values(), key=lambda r: (r["zone"], r["ts"])))
    print(f"neighbours: merged {added} new rows")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill neighbouring zone prices")
    parser.add_argument("--days", type=int, default=1460)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--merge", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.merge:
        return merge(args.merge)
    if not entsoe_prices.token_available():
        print("neighbours: ENTSOE_TOKEN is not set")
        return 1
    rows = fetch(args.days)
    if not rows:
        return 1
    write_jsonl(args.stage or Path("neighbours.jsonl"), rows)
    print(f"neighbours: staged {len(rows):,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
