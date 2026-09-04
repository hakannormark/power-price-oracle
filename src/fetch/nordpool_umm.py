"""Urgent Market Messages from Nord Pool: outages, in MW and to the hour.

This is the source ENTSO-E's unavailability endpoint was meant to be. It is
public, needs no token, and answers the question the model could not: which
plant or cable is out, in which bidding zone, how much capacity is gone, and
exactly when. REMIT obliges participants to publish, so it is also the market's
own record rather than a scrape.

    https://ummapi.nordpoolgroup.com/messages

Two facts shape how it is used here.

Messages are versioned and superseded: an outage is extended, shortened or
cancelled by a later version of the same messageId. Only the highest version is
kept.

Every message carries a publicationDate, which is what makes honest scoring
possible. A forecast issued at T may only use what was published before T; a
reactor trip announced on Tuesday must not improve Monday's forecast in a
backtest.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Iterable

from ..config import UMM_API_URL, UMM_PAGE_SIZE, ZONES
from ..store import r3
from ..timeutil import TZ, iso, now_local, parse_iso
from .http import get

log = logging.getLogger(__name__)

# messageType 3 is transmission; production and consumption arrive as 1 and 2.
UNIT_KINDS = ("productionUnits", "transmissionUnits")

# The feed sends fuelType as a bare integer with no code list in the response,
# so this map was derived by reading off the units each code actually carries:
#   14 -> Forsmark Block1 (1098 MW)      -> nuclear
#    4 -> Öresundsverket, FLEMALLE TH1   -> gas
#   11 -> HPP Plavinas, Harrsele         -> hydro
#   12 -> Kvilldal, Stornorrfors         -> hydro
#   18 -> Hollandse Kust, Windpark Fryslan -> wind, offshore
#   19 -> Björkhöjden, Åskälen, Storheia -> wind, onshore
#   17 -> Dublin Waste to Energy         -> waste
#    1 -> Igelsta kraftvärmeverk         -> CHP
# Anything unmapped is reported as unknown rather than guessed at: mislabelling a
# gigawatt of nuclear as solar would put a false sentence in the driver text.
FUEL_NAMES = {
    1: "kraftvärme",
    4: "gas",
    11: "vattenkraft",
    12: "vattenkraft",
    14: "kärnkraft",
    17: "avfall",
    18: "vindkraft",
    19: "vindkraft",
    100: "annat",
}
NUCLEAR_FUEL_CODES = {14}


def _page(params: dict) -> dict:
    return get(UMM_API_URL, params=params, retries=3).json()


def fetch_messages(
    published_from: datetime,
    published_to: datetime | None = None,
    max_messages: int = 20000,
) -> tuple[list[dict], dict]:
    """Every UMM published in the window, newest first, following pagination."""
    params: dict[str, Any] = {
        "limit": UMM_PAGE_SIZE,
        "publicationStartDate": published_from.date().isoformat(),
    }
    if published_to is not None:
        params["publicationEndDate"] = published_to.date().isoformat()

    items: list[dict] = []
    skip = 0
    try:
        while len(items) < max_messages:
            payload = _page({**params, "skip": skip})
            batch = payload.get("items") or []
            if not batch:
                break
            items.extend(batch)
            total = int(payload.get("total") or 0)
            skip += len(batch)
            if skip >= total:
                break
    except Exception as exc:  # noqa: BLE001 - degrade, never block a run
        log.warning("Nord Pool UMM fetch failed after %s messages: %s", len(items), exc)
        return items, {"ok": bool(items), "messages": len(items), "error": str(exc)[:160]}

    log.info("Nord Pool UMM: %s messages since %s", len(items), published_from.date())
    return items, {"ok": True, "messages": len(items)}


def to_rows(messages: Iterable[dict]) -> list[dict]:
    """Flatten messages into one row per unit and time period.

    Keeps publication time and version so a later pass can reconstruct what was
    known at any point, and drops anything outside the Swedish zones.
    """
    rows: list[dict] = []
    for message in messages:
        published = message.get("publicationDate")
        if not published:
            continue
        base = {
            "message_id": message.get("messageId"),
            "version": int(message.get("version") or 1),
            "published_at": iso(parse_iso(published)),
            "reason": (message.get("unavailabilityReason") or "")[:120],
            "remarks": (message.get("remarks") or "")[:200],
            "outdated": bool(message.get("isOutdated")),
        }

        for kind in UNIT_KINDS:
            for unit in message.get(kind) or []:
                if kind == "productionUnits":
                    zone = unit.get("areaName")
                    if zone not in ZONES:
                        continue
                    scope = {
                        "kind": "production",
                        "zone": zone,
                        "unit": (unit.get("name") or "")[:80],
                        "fuel": FUEL_NAMES.get(unit.get("fuelType"), "okänt"),
                        "fuel_code": unit.get("fuelType"),
                        "nuclear": unit.get("fuelType") in NUCLEAR_FUEL_CODES,
                    }
                else:
                    # Corridors are named "<inArea> → <outArea>", so the flow runs
                    # from inArea to outArea. Losing it costs the origin export
                    # capacity and the destination import capacity — opposite
                    # signs for the two zones, which is why direction matters.
                    origin, destination = unit.get("inAreaName"), unit.get("outAreaName")
                    if origin not in ZONES and destination not in ZONES:
                        continue
                    scope = {
                        "kind": "transmission",
                        "zone": None,
                        "from_area": origin,
                        "to_area": destination,
                        "unit": (unit.get("name") or "")[:80],
                    }

                capacity = unit.get("installedCapacity")
                for period in unit.get("timePeriods") or []:
                    start, stop = period.get("eventStart"), period.get("eventStop")
                    if not start or not stop:
                        continue
                    rows.append(
                        {
                            **base,
                            **scope,
                            "installed_mw": r3(capacity) if capacity is not None else None,
                            "unavailable_mw": r3(period.get("unavailableCapacity") or 0),
                            "event_start": iso(parse_iso(start)),
                            "event_stop": iso(parse_iso(stop)),
                        }
                    )
    return rows


def latest_versions(rows: list[dict]) -> list[dict]:
    """Keep only the newest version of each message; later ones supersede."""
    best: dict[str, int] = {}
    for row in rows:
        mid = row["message_id"]
        best[mid] = max(best.get(mid, 0), row["version"])
    return [r for r in rows if r["version"] == best.get(r["message_id"])]


def known_at(rows: list[dict], moment: datetime) -> list[dict]:
    """The outage picture as it stood at `moment` — nothing published later.

    This is the guard that keeps a backtest honest.
    """
    visible = [r for r in rows if parse_iso(r["published_at"]) <= moment]
    return latest_versions(visible)


def dedupe_events(rows: list[dict]) -> list[dict]:
    """Collapse the same physical outage reported under several messages.

    The grid operator republishes a corridor restriction as its own message and
    counterparties repeat it, so summing rows straight from the feed inflates a
    2 900 MW restriction into five figures.
    """
    seen: dict[tuple, dict] = {}
    for row in rows:
        key = (
            row["kind"],
            row.get("unit"),
            row.get("from_area"),
            row.get("to_area"),
            row["event_start"],
            row["event_stop"],
            row.get("unavailable_mw"),
        )
        current = seen.get(key)
        if current is None or row["version"] > current["version"]:
            seen[key] = row
    return list(seen.values())


def hourly_outages(
    rows: list[dict], start: datetime, end: datetime, as_of: datetime | None = None
) -> "pd.DataFrame":
    """Hourly MW unavailable per zone over [start, end).

    Columns per (ts, zone): production out, of which nuclear, and the import and
    export capacity lost on that zone's corridors. Only messages published on or
    before `as_of` are used, so this is safe to call inside a backtest.
    """
    import pandas as pd

    from ..timeutil import hour_range

    live = dedupe_events(known_at(rows, as_of) if as_of else latest_versions(rows))
    hours = hour_range(start, end)
    index = {ts: i for i, ts in enumerate(hours)}
    fields = ("production_out_mw", "nuclear_out_mw", "import_lost_mw", "export_lost_mw")
    grid = {(zone, f): [0.0] * len(hours) for zone in ZONES for f in fields}

    for row in live:
        if row.get("outdated"):
            continue
        mw = float(row.get("unavailable_mw") or 0)
        if mw <= 0:
            continue
        first = parse_iso(row["event_start"]).replace(minute=0, second=0, microsecond=0)
        last = parse_iso(row["event_stop"])

        targets: list[tuple[str, str]] = []
        if row["kind"] == "production":
            zone = row.get("zone")
            if zone in ZONES:
                targets.append((zone, "production_out_mw"))
                if row.get("nuclear"):
                    targets.append((zone, "nuclear_out_mw"))
        else:
            if row.get("from_area") in ZONES:
                targets.append((row["from_area"], "export_lost_mw"))
            if row.get("to_area") in ZONES:
                targets.append((row["to_area"], "import_lost_mw"))
        if not targets:
            continue

        ts = max(first, hours[0]) if hours else first
        while ts < last and ts in index:
            slot = index[ts]
            for key in targets:
                grid[key][slot] += mw
            ts += timedelta(hours=1)

    frame = pd.DataFrame({"ts": hours * len(ZONES), "zone": [z for z in ZONES for _ in hours]})
    for field in fields:
        frame[field] = [v for zone in ZONES for v in grid[(zone, field)]]
    return frame


def current_outages(rows: list[dict], now: datetime | None = None, horizon_h: int = 168) -> dict:
    """What is out during the forecast window, per zone, for the driver text."""
    now = now or now_local()
    window_end = now + timedelta(hours=horizon_h)
    live = dedupe_events(known_at(rows, now))

    out: dict[str, dict] = {zone: {"items": []} for zone in ZONES}
    for row in live:
        if row.get("outdated"):
            continue
        start, stop = parse_iso(row["event_start"]), parse_iso(row["event_stop"])
        if stop <= now or start >= window_end:
            continue
        mw = float(row.get("unavailable_mw") or 0)
        if mw <= 0:
            continue

        if row["kind"] == "production":
            zones = [row["zone"]] if row.get("zone") in ZONES else []
            label = row["unit"]
        else:
            zones = [z for z in (row.get("from_area"), row.get("to_area")) if z in ZONES]
            label = f"{row.get('from_area')} → {row.get('to_area')}"

        for zone in zones:
            out[zone]["items"].append(
                {
                    "kind": row["kind"],
                    "unit": label,
                    "fuel": row.get("fuel"),
                    "nuclear": bool(row.get("nuclear")),
                    "unavailable_mw": r3(mw),
                    "installed_mw": row.get("installed_mw"),
                    "from": row["event_start"],
                    "to": row["event_stop"],
                    "reason": row["reason"],
                }
            )

    # Keep a generous slice sorted by size; the explainer re-ranks by what is
    # worth telling first, and truncating to six here would cut a reactor block
    # that several larger corridor restrictions outrank on megawatts alone.
    for block in out.values():
        block["items"].sort(key=lambda i: -(i["unavailable_mw"] or 0))
        block["items"] = block["items"][:14]
    return out
