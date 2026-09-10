"""Day-ahead prices from elprisetjustnu.se — the fallback when ENTSO-E is down.

ENTSO-E is the authoritative source and stays first in line. It is also the
single point of failure the whole product hangs on: it answers 503 often enough
to matter, throttles hard under repeated backfills, and takes an emailed request
and several days to get access to in the first place.

This source needs no key, covers exactly the four Swedish bidding zones, serves
tomorrow once the auction has published it, and reaches years back. It was
verified against our stored ENTSO-E data before being trusted: the mean of each
hour's four quarter-hour entries matched our hourly figure to 0.00 EUR/MWh
across a full day.

Prices are read in EUR, not SEK. The feed carries its own exchange rate, but the
site converts to öre through the ECB daily reference rate, and mixing two rates
would make a published figure impossible to reproduce.

Rows carry `via` so provenance survives into the store. ENTSO-E rows overwrite
fallback rows for the same hour on the next successful run, which is the
precedence we want and needs no special handling — the upsert is keyed on
(zone, ts).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..config import ELPRISET_URL, RESOLUTION, ZONES
from ..store import r3
from ..timeutil import TZ, auction_publication_time, iso, now_local, parse_iso
from .http import get

log = logging.getLogger(__name__)

# The feed is keyed by Swedish bidding zone, which is all we publish anyway.
SUPPORTED = set(ZONES)


def _day_url(day: datetime, zone: str) -> str:
    return ELPRISET_URL.format(year=day.year, month=day.month, day=day.day, zone=zone)


def fetch_day(day: datetime, zone: str) -> list[dict]:
    """One zone, one delivery day, at whatever resolution the feed publishes."""
    payload = get(_day_url(day, zone), retries=2).json()
    rows: list[dict] = []
    for entry in payload:
        start = parse_iso(entry["time_start"])
        end = parse_iso(entry["time_end"])
        minutes = int((end - start).total_seconds() // 60) or 60
        price = entry.get("EUR_per_kWh")
        if price is None:
            continue
        rows.append(
            {
                "ts": iso(start),
                "zone": zone,
                # EUR/kWh in the feed, EUR/MWh everywhere in this project.
                "price_eur_mwh": r3(float(price) * 1000.0),
                "resolution": f"PT{minutes}M",
                "published_at": iso(now_local()),
                "via": "elpriset",
            }
        )
    return rows


def _to_hourly(rows: list[dict]) -> list[dict]:
    """Average sub-hourly entries into the hourly series the models use."""
    buckets: dict[tuple[str, str], list[float]] = {}
    meta: dict[tuple[str, str], dict] = {}
    for row in rows:
        ts = parse_iso(row["ts"]).replace(minute=0, second=0, microsecond=0)
        key = (row["zone"], iso(ts))
        buckets.setdefault(key, []).append(float(row["price_eur_mwh"]))
        meta[key] = row

    hourly = []
    for (zone, ts), values in sorted(buckets.items()):
        hourly.append(
            {
                "ts": ts,
                "zone": zone,
                "price_eur_mwh": r3(sum(values) / len(values)),
                "resolution": RESOLUTION,
                "published_at": meta[(zone, ts)]["published_at"],
                "via": "elpriset",
            }
        )
    return hourly


def fetch_prices(
    start: datetime,
    end: datetime,
    zones: list[str] | None = None,
    native: bool = False,
    now: datetime | None = None,
) -> tuple[list[dict], dict]:
    """Same shape and contract as entsoe_prices.fetch_prices, so it can stand in.

    `end` is exclusive, as in entsoe_prices.scheduled_window. A day the auction
    has not published yet is skipped rather than requested: its 404 is certain,
    and reporting it in every run buried the errors that mattered.

    Never raises: a fallback that fails loudly is worse than one that reports
    what it managed.
    """
    now = now or now_local()
    zones = [z for z in (zones or list(ZONES)) if z in SUPPORTED]
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    days = []
    while day < end:
        if auction_publication_time(day) <= now:
            days.append(day)
        day += timedelta(days=1)

    rows: list[dict] = []
    errors: list[str] = []
    for zone in zones:
        for target in days:
            try:
                rows.extend(fetch_day(target, zone))
            except Exception as exc:  # noqa: BLE001 - degrade per day
                # Unpublished days were skipped above, so this is a real failure.
                errors.append(f"{zone} {target:%Y-%m-%d}: {type(exc).__name__}")

    result = rows if native else _to_hourly(rows)
    status: dict = {"ok": bool(result), "rows": len(result), "via": "elpriset"}
    if errors:
        status["error"] = "; ".join(errors[:4])[:200]
    if result:
        log.info("elprisetjustnu: %s rows for %s zones", len(result), len(zones))
    return result, status
