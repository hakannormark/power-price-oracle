"""Day-ahead prices (documentType A44) from the ENTSO-E Transparency Platform.

Implemented against the raw REST endpoint rather than entsoe-py: the payload is a
single well-known XML document, and going direct removes a version-compatibility
risk from the one fetch the whole product depends on. The EIC codes in config are
the source of truth here. entsoe-py is still used for the optional fundamentals.
"""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import ENTSOE_API_URL, RAW_DIR, RESOLUTION, ZONES
from ..store import r3
from ..timeutil import TZ, UTC, iso, now_local, to_local, to_utc
from .http import get

log = logging.getLogger(__name__)

RESOLUTION_MINUTES = {
    "PT15M": 15,
    "PT30M": 30,
    "PT60M": 60,
    "PT1H": 60,
    "P1D": 1440,
}


class EntsoeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawPoint:
    """One price point at the resolution the exchange published it in."""

    ts_utc: datetime
    price_eur_mwh: float
    resolution: str


def token_available() -> bool:
    return bool(os.environ.get("ENTSOE_TOKEN", "").strip())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(element: ET.Element, name: str) -> ET.Element | None:
    for child in element.iter():
        if _local_name(child.tag) == name:
            return child
    return None


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in element if _local_name(c.tag) == name]


def _parse_entsoe_timestamp(value: str) -> datetime:
    """ENTSO-E timestamps look like 2026-09-04T22:00Z."""
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_a44(xml_text: str) -> list[RawPoint]:
    """Parse a Publication_MarketDocument into native-resolution price points."""
    root = ET.fromstring(xml_text)
    root_name = _local_name(root.tag)

    if root_name.startswith("Acknowledgement"):
        reason = _first(root, "text")
        message = reason.text if reason is not None and reason.text else "no data"
        raise EntsoeError(f"ENTSO-E acknowledgement: {message.strip()}")

    points: list[RawPoint] = []
    for series in (el for el in root if _local_name(el.tag) == "TimeSeries"):
        currency = _first(series, "currency_Unit.name")
        if currency is not None and currency.text and currency.text.strip() != "EUR":
            log.warning("Skipping TimeSeries in %s, not EUR", currency.text)
            continue

        for period in _children(series, "Period"):
            interval = _first(period, "timeInterval")
            resolution_el = _first(period, "resolution")
            if interval is None or resolution_el is None:
                continue
            start_el = _first(interval, "start")
            end_el = _first(interval, "end")
            if start_el is None or start_el.text is None:
                continue

            resolution = (resolution_el.text or RESOLUTION).strip()
            minutes = RESOLUTION_MINUTES.get(resolution)
            if minutes is None:
                log.warning("Unknown resolution %s, skipping period", resolution)
                continue

            start = _parse_entsoe_timestamp(start_el.text)
            end = (
                _parse_entsoe_timestamp(end_el.text)
                if end_el is not None and end_el.text
                else None
            )

            by_position: dict[int, float] = {}
            for point in _children(period, "Point"):
                position_el = _first(point, "position")
                amount_el = _first(point, "price.amount")
                if position_el is None or amount_el is None:
                    continue
                if position_el.text is None or amount_el.text is None:
                    continue
                by_position[int(position_el.text)] = float(amount_el.text)

            if not by_position:
                continue

            if end is not None:
                expected = int((end - start).total_seconds() // (minutes * 60))
            else:
                expected = max(by_position)

            # Curve type A03 omits repeated points: the previous value stays valid
            # until the next position appears.
            last_value: float | None = None
            for position in range(1, expected + 1):
                if position in by_position:
                    last_value = by_position[position]
                if last_value is None:
                    continue
                points.append(
                    RawPoint(
                        ts_utc=start + timedelta(minutes=minutes * (position - 1)),
                        price_eur_mwh=last_value,
                        resolution=resolution,
                    )
                )

    return points


def document_created_at(xml_text: str) -> datetime | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    created = _first(root, "createdDateTime")
    if created is None or created.text is None:
        return None
    try:
        return _parse_entsoe_timestamp(created.text)
    except ValueError:
        return None


def to_hourly_rows(
    points: list[RawPoint], zone: str, published_at: datetime
) -> list[dict]:
    """Average sub-hourly points into the hourly rows stored in actuals.jsonl."""
    buckets: dict[datetime, list[float]] = {}
    for point in points:
        hour = point.ts_utc.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour, []).append(point.price_eur_mwh)

    rows: list[dict] = []
    for hour in sorted(buckets):
        values = buckets[hour]
        rows.append(
            {
                "ts": iso(hour.astimezone(TZ)),
                "zone": zone,
                "price_eur_mwh": r3(sum(values) / len(values)),
                "resolution": RESOLUTION,
                "published_at": iso(published_at),
            }
        )
    return rows


def _save_raw(zone: str, points: list[RawPoint]) -> None:
    """Keep the native (possibly 15-minute) curve out of the public API but on disk."""
    sub_hourly = [p for p in points if p.resolution != "PT60M"]
    if not sub_hourly:
        return
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = RAW_DIR / f"prices_native_{zone}.csv"
    with target.open("w", encoding="utf-8") as fh:
        fh.write("ts_local,resolution,price_eur_mwh\n")
        for point in sorted(sub_hourly, key=lambda p: p.ts_utc):
            fh.write(
                f"{iso(point.ts_utc.astimezone(TZ))},{point.resolution},{point.price_eur_mwh}\n"
            )


def fetch_zone(zone: str, start: datetime, end: datetime) -> list[dict]:
    """Fetch one zone's day-ahead prices for [start, end) and return hourly rows."""
    token = os.environ.get("ENTSOE_TOKEN", "").strip()
    if not token:
        raise EntsoeError("ENTSOE_TOKEN is not set")

    eic = ZONES[zone]["eic"]
    params = {
        "documentType": "A44",
        "in_Domain": eic,
        "out_Domain": eic,
        "periodStart": to_utc(start).strftime("%Y%m%d%H%M"),
        "periodEnd": to_utc(end).strftime("%Y%m%d%H%M"),
        "securityToken": token,
    }
    response = get(ENTSOE_API_URL, params=params)
    points = parse_a44(response.text)
    _save_raw(zone, points)
    published_at = document_created_at(response.text) or now_local()
    return to_hourly_rows(points, zone, to_local(published_at))


def fetch_prices(start: datetime, end: datetime, zones: list[str] | None = None) -> tuple[list[dict], dict]:
    """Fetch all zones. Returns (rows, status) and never raises for a single zone."""
    zones = zones or list(ZONES)
    rows: list[dict] = []
    errors: dict[str, str] = {}

    for zone in zones:
        try:
            zone_rows = fetch_zone(zone, start, end)
            rows.extend(zone_rows)
            log.info("ENTSO-E %s: %s hourly rows", zone, len(zone_rows))
        except Exception as exc:  # noqa: BLE001 - degrade per zone
            errors[zone] = str(exc)[:200]
            log.warning("ENTSO-E %s failed: %s", zone, exc)

    status: dict = {"ok": bool(rows) and not errors, "rows": len(rows)}
    if errors:
        status["ok"] = bool(rows)
        status["error"] = "; ".join(f"{z}: {e}" for z, e in errors.items())
    if not rows and not errors:
        status["ok"] = False
        status["error"] = "no rows returned"
    return rows, status


def scheduled_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Last 3 days plus tomorrow — the window every scheduled run refreshes."""
    now = now or now_local()
    start = (now - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end
