"""Pull historical day-ahead prices into data/actuals.jsonl.

    python -m src.backfill              # last 90 days, all zones
    python -m src.backfill --days 365
    python -m src.backfill --if-needed  # no-op when history is already deep enough

Only touches actuals. It never writes forecasts, so it is safe to re-run.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta

from .config import BACKFILL_DAYS, BACKFILL_MIN_ROWS, ZONES, ensure_dirs
from .fetch import entsoe_prices
from .store import load_actuals, upsert_actuals
from .timeutil import iso, now_local, start_of_day

log = logging.getLogger("backfill")

# ENTSO-E rejects very long windows for A44; walk the range in month-sized chunks.
CHUNK_DAYS = 30


def needed(min_rows: int = BACKFILL_MIN_ROWS) -> bool:
    rows = len(load_actuals())
    if rows >= min_rows:
        log.info("Actuals hold %s rows (>= %s) — no backfill needed", rows, min_rows)
        return False
    log.info("Actuals hold %s rows (< %s) — backfill needed", rows, min_rows)
    return True


def run(days: int = BACKFILL_DAYS, zones: list[str] | None = None) -> int:
    ensure_dirs()
    now = now_local()
    end = start_of_day(now) + timedelta(days=2)
    start = start_of_day(now) - timedelta(days=days)

    all_rows: list[dict] = []
    failures: list[str] = []

    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), end)
        rows, status = entsoe_prices.fetch_prices(chunk_start, chunk_end, zones)
        log.info(
            "%s -> %s: %s rows%s",
            iso(chunk_start)[:10],
            iso(chunk_end)[:10],
            len(rows),
            "" if status["ok"] else f" ({status.get('error')})",
        )
        all_rows.extend(rows)
        if not status["ok"]:
            failures.append(status.get("error", "unknown"))
        chunk_start = chunk_end

    added = upsert_actuals(all_rows) if all_rows else 0
    total = len(load_actuals())
    print(f"backfill: fetched {len(all_rows)} rows, {added} new, {total} stored")
    if failures:
        print(f"backfill: {len(failures)} chunk(s) failed — {failures[0]}")
    return 0 if all_rows or not failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill official day-ahead prices")
    parser.add_argument("--days", type=int, default=BACKFILL_DAYS)
    parser.add_argument("--zones", nargs="*", choices=list(ZONES), default=None)
    parser.add_argument(
        "--if-needed",
        action="store_true",
        help="Exit without fetching when actuals already hold enough rows",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.if_needed and not needed():
        print("backfill: skipped, history already deep enough")
        return 0
    if not entsoe_prices.token_available():
        print("backfill: ENTSOE_TOKEN is not set — nothing to fetch")
        return 0
    return run(days=args.days, zones=args.zones)


if __name__ == "__main__":
    sys.exit(main())
