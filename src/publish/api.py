"""Write the static API under api/v1/ (and the Pages copy under site/api/v1/)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import (
    API_DIR,
    CURRENCY,
    HISTORY_API_DAYS,
    HORIZON_HOURS,
    RESOLUTION,
    SITE_API_DIR,
    ZONES,
    ensure_dirs,
    ore_per_kwh,
)
from ..models.official import SOURCE_DEMO, SOURCE_FORECAST, SOURCE_OFFICIAL
from ..models.registry import DEFAULT_MODEL_ID, describe_models
from ..store import r3
from ..timeutil import hour_range, iso, now_local, parse_iso, start_of_day, to_utc

log = logging.getLogger(__name__)

# UTC hours the workflow's cron entries fire at.
SCHEDULE_UTC = [(4, 30), (11, 20), (16, 0)]

# History is published at one lead time per forecast day, so the site can ask
# "what did we say N days ahead?" instead of only the day-ahead case.
HISTORY_LEAD_TIMES = [24, 48, 72, 96, 120, 144, 168]
HISTORY_DEFAULT_LEAD = 24
HISTORY_LEAD_TOLERANCE = 12  # hours either side of the nominal lead time


def write_json(relative: str, payload: Any) -> None:
    """Write one document to both the repo-root API and the Pages copy."""
    ensure_dirs()
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False)
    for base in (API_DIR, SITE_API_DIR):
        target = Path(base) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")


def series_window(now: datetime) -> tuple[datetime, datetime]:
    """From the start of yesterday through +7 days."""
    return start_of_day(now) - timedelta(days=1), now + timedelta(hours=HORIZON_HOURS)


def build_series(
    zone: str,
    predictions: dict[str, list],
    actuals_index: dict,
    now: datetime,
    demo: bool = False,
) -> list[dict]:
    """Merge official outcomes and every model's quantiles onto one hourly grid."""
    start, end = series_window(now)
    by_model: dict[str, dict[datetime, Any]] = {
        model_id: {p.ts: p for p in points if p.zone == zone}
        for model_id, points in predictions.items()
    }

    series: list[dict] = []
    for ts in hour_range(start, end):
        actual = actuals_index.get((zone, ts))
        entry: dict[str, Any] = {
            "ts": iso(ts),
            "actual": r3(actual) if actual is not None else None,
            "source": (SOURCE_DEMO if demo else SOURCE_OFFICIAL) if actual is not None else SOURCE_FORECAST,
            "models": {},
        }
        for model_id, points in by_model.items():
            point = points.get(ts)
            if point is None:
                continue
            entry["models"][model_id] = {
                "p10": r3(point.p10),
                "p50": r3(point.p50),
                "p90": r3(point.p90),
            }
        series.append(entry)
    return series


def write_forecast(
    zone: str,
    series: list[dict],
    drivers: dict,
    meta: dict,
) -> dict:
    payload = {
        "zone": zone,
        "zone_name": ZONES[zone]["name"],
        "generated_at": meta["generated_at"],
        "run_id": meta["run_id"],
        "unit": CURRENCY,
        "resolution": RESOLUTION,
        "degraded": meta["degraded"],
        "demo": meta.get("demo", False),
        "default_model": DEFAULT_MODEL_ID,
        "models": meta["models"],
        "fx": meta.get("fx"),
        "series": series,
        "drivers": drivers,
    }
    write_json(f"zones/{zone}/forecast.json", payload)
    return payload


def build_history(
    zone: str,
    actuals: list[dict],
    forecasts: list[dict],
    now: datetime,
    model_id: str = DEFAULT_MODEL_ID,
) -> dict:
    """Last 30 days of outcomes beside the forecasts we issued for them.

    One column per lead time, so "what did we say five days ahead?" is answerable
    from the file without the browser touching the forecast log.
    """
    cutoff = start_of_day(now) - timedelta(days=HISTORY_API_DAYS)

    outcomes: dict[datetime, float] = {}
    for row in actuals:
        if row["zone"] != zone:
            continue
        ts = parse_iso(row["ts"])
        if ts >= cutoff:
            outcomes[ts] = float(row["price_eur_mwh"])

    # Per delivery hour and lead time, keep the forecast issued closest to it.
    best: dict[tuple[datetime, int], tuple[int, float]] = {}
    for row in forecasts:
        if row["zone"] != zone or row["model_id"] != model_id:
            continue
        if row.get("p50") is None:
            continue
        horizon = int(row.get("horizon_h", -1))
        ts = parse_iso(row["ts"])
        if ts < cutoff:
            continue
        for lead in HISTORY_LEAD_TIMES:
            distance = abs(horizon - lead)
            if distance > HISTORY_LEAD_TOLERANCE:
                continue
            current = best.get((ts, lead))
            if current is None or distance < current[0]:
                best[(ts, lead)] = (distance, float(row["p50"]))

    hours = sorted(set(outcomes) | {ts for ts, _ in best})
    points = []
    for ts in hours:
        forecast = {
            str(lead): r3(best[(ts, lead)][1])
            for lead in HISTORY_LEAD_TIMES
            if (ts, lead) in best
        }
        points.append(
            {
                "ts": iso(ts),
                "actual": r3(outcomes.get(ts)),
                "forecast": forecast,
            }
        )

    payload = {
        "zone": zone,
        "zone_name": ZONES[zone]["name"],
        "generated_at": iso(now),
        "unit": CURRENCY,
        "resolution": RESOLUTION,
        "days": HISTORY_API_DAYS,
        "model_id": model_id,
        "lead_times_h": HISTORY_LEAD_TIMES,
        "default_lead_time_h": HISTORY_DEFAULT_LEAD,
        "points": points,
    }
    write_json(f"zones/{zone}/history.json", payload)
    return payload


def write_zones_index(payloads: dict[str, dict], meta: dict) -> dict:
    """Cheap summary document: current price per zone, for tiles and integrations."""
    zones = []
    rate = (meta.get("fx") or {}).get("rate")
    for zone, forecast in payloads.items():
        current = current_point(forecast, meta["now"], rate)
        zones.append(
            {
                "zone": zone,
                "name": ZONES[zone]["name"],
                "lat": ZONES[zone]["lat"],
                "lon": ZONES[zone]["lon"],
                "eic": ZONES[zone]["eic"],
                "current": current,
                "forecast_url": f"zones/{zone}/forecast.json",
                "history_url": f"zones/{zone}/history.json",
                "accuracy_url": f"zones/{zone}/accuracy.json",
            }
        )

    payload = {
        "generated_at": meta["generated_at"],
        "run_id": meta["run_id"],
        "unit": CURRENCY,
        "resolution": RESOLUTION,
        "degraded": meta["degraded"],
        "default_model": DEFAULT_MODEL_ID,
        "fx": meta.get("fx"),
        "zones": zones,
    }
    write_json("zones.json", payload)
    return payload


def current_point(forecast: dict, now: datetime, eur_sek: float | None = None) -> dict | None:
    """The price for the hour we are in: official when published, otherwise ensemble."""
    if eur_sek is None:
        eur_sek = (forecast.get("fx") or {}).get("rate")
    target = iso(now.replace(minute=0, second=0, microsecond=0))
    for entry in forecast["series"]:
        if entry["ts"] != target:
            continue
        value = entry["actual"]
        source = entry["source"]
        if value is None:
            model = entry["models"].get(DEFAULT_MODEL_ID) or {}
            value = model.get("p50")
            source = SOURCE_FORECAST
        if value is None:
            return None
        return {
            "ts": entry["ts"],
            "eur_mwh": r3(value),
            "ore_kwh": r3(ore_per_kwh(value, eur_sek)) if eur_sek else None,
            "source": source,
        }
    return None


def write_models() -> dict:
    payload = {
        "generated_at": iso(now_local()),
        "default_model": DEFAULT_MODEL_ID,
        "models": describe_models(),
    }
    write_json("models.json", payload)
    return payload


def write_accuracy(accuracy: dict, zone_slices: dict[str, dict]) -> None:
    write_json("accuracy.json", accuracy)
    for zone, payload in zone_slices.items():
        write_json(f"zones/{zone}/accuracy.json", payload)


def next_scheduled_update(now: datetime | None = None) -> str:
    now_utc = to_utc(now or now_local())
    candidates = []
    for day_offset in (0, 1):
        day = (now_utc + timedelta(days=day_offset)).replace(minute=0, second=0, microsecond=0)
        for hour, minute in SCHEDULE_UTC:
            candidates.append(day.replace(hour=hour, minute=minute))
    upcoming = sorted(c for c in candidates if c > now_utc)
    return upcoming[0].strftime("%Y-%m-%dT%H:%M:%SZ")


def write_status(
    run_id: str,
    sources: dict,
    degraded: bool,
    now: datetime,
    demo: bool = False,
    fx: dict | None = None,
) -> dict:
    payload = {
        "ok": not degraded,
        "generated_at": iso(now),
        "run_id": run_id,
        "degraded": degraded,
        "demo": demo,
        "fx": fx,
        "sources": sources,
        "next_expected_update_utc": next_scheduled_update(now),
    }
    write_json("status.json", payload)
    return payload
