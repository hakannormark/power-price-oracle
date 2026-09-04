"""Backfill the outage history from Nord Pool.

    python -m src.fetch_umm --years 4

Public and unauthenticated, so this runs anywhere. Walks the publication window
in month-sized slices because the API paginates from a start date and the deep
history is tens of thousands of messages.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta

from .config import ensure_dirs
from .fetch.nordpool_umm import fetch_messages, to_rows
from .store import load_umm, upsert_umm
from .timeutil import now_local

log = logging.getLogger("umm")
SLICE_DAYS = 30


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill Nord Pool outage messages")
    parser.add_argument("--years", type=float, default=4)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    ensure_dirs()
    now = now_local()
    start = now - timedelta(days=int(365 * args.years))

    total_rows = added = 0
    cursor = start
    while cursor < now:
        stop = min(cursor + timedelta(days=SLICE_DAYS), now)
        messages, status = fetch_messages(cursor, stop, max_messages=50000)
        rows = to_rows(messages)
        added += upsert_umm(rows)
        total_rows += len(rows)
        print(f"  {cursor:%Y-%m-%d} → {stop:%Y-%m-%d}: {status.get('messages', 0)} meddelanden, {len(rows)} SE-rader")
        cursor = stop

    print(f"umm: {total_rows} rows flattened, {added} new, {len(load_umm())} stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
