"""Write site/data/*.json — everything the frontend fetches, relative to the page.

The site never reads the JSONL state or the repo-root API; it gets its own
pre-joined documents so a page load is two requests, not twenty.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from ..config import (
    CURRENCY,
    HORIZON_HOURS,
    REPO_URL,
    RESOLUTION,
    SITE_DATA_DIR,
    SITE_SUBTITLE_SV,
    SITE_TITLE_SV,
    ZONES,
    ensure_dirs,
    ore_per_kwh,
)
from ..models.registry import DEFAULT_MODEL_ID, REFERENCE_MODEL_ID, describe_models
from ..store import r3
from ..timeutil import iso, parse_iso
from .api import current_point

log = logging.getLogger(__name__)

SPARK_HOURS = 24


def _write(name: str, payload: Any) -> None:
    ensure_dirs()
    target = SITE_DATA_DIR / name
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def _today_hours(forecast: dict, now: datetime) -> list[dict]:
    """Timestamped hours around now, so the browser can pick the current one.

    The "price right now" used to be resolved here, at generation time, and then
    sat frozen until the next run — up to eight hours between the evening and
    morning runs. The clock the reader cares about is theirs, not the runner's.
    """
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now + timedelta(hours=36)
    rate = (forecast.get("fx") or {}).get("rate")
    rows = []
    for entry in forecast["series"]:
        ts = parse_iso(entry["ts"])
        if not start <= ts <= end:
            continue
        model = entry["models"].get(DEFAULT_MODEL_ID) or {}
        value = entry["actual"] if entry["actual"] is not None else model.get("p50")
        if value is None:
            continue
        rows.append(
            {
                "ts": entry["ts"],
                "eur_mwh": r3(value),
                "ore_kwh": r3(ore_per_kwh(value, rate)) if rate else None,
                "source": entry["source"],
            }
        )
    return rows


def _spark(forecast: dict, now: datetime) -> list[float | None]:
    """Next 24 h of the default model's p50, for the tile sparkline."""
    end = now + timedelta(hours=SPARK_HOURS)
    values: list[float | None] = []
    for entry in forecast["series"]:
        ts = parse_iso(entry["ts"])
        if not now <= ts < end:
            continue
        if entry["actual"] is not None:
            values.append(entry["actual"])
        else:
            values.append((entry["models"].get(DEFAULT_MODEL_ID) or {}).get("p50"))
    return values


def _next_hours(forecast: dict, now: datetime) -> list[dict]:
    """Every remaining hour of the forecast window: value, band, and fact-or-not."""
    rate = (forecast.get("fx") or {}).get("rate")
    start = now.replace(minute=0, second=0, microsecond=0)
    end = now + timedelta(hours=HORIZON_HOURS)
    rows: list[dict] = []
    for entry in forecast["series"]:
        ts = parse_iso(entry["ts"])
        if not start <= ts <= end:
            continue
        model = entry["models"].get(DEFAULT_MODEL_ID) or {}
        value = entry["actual"] if entry["actual"] is not None else model.get("p50")
        rows.append(
            {
                "ts": entry["ts"],
                "source": entry["source"],
                "eur_mwh": r3(value),
                "ore_kwh": r3(ore_per_kwh(value, rate)) if value is not None and rate else None,
                "p10": model.get("p10"),
                "p90": model.get("p90"),
            }
        )
    return rows


CHARGE_HOURS = 3  # a car charge, and the block the cheapest-window search uses


def day_plan(forecast: dict, now: datetime, quarters: list[dict] | None = None) -> list[dict]:
    """One row per day for the week ahead: how expensive, and when it is cheapest.

    Built for the question a household actually asks — charge the car tonight or
    wait until Tuesday — which the hourly table answers only by reading 168 rows.
    Each day carries its cheapest contiguous three-hour window, because that is
    the shape of the decision, not the single cheapest hour.
    """
    rate = (forecast.get("fx") or {}).get("rate")
    start = now.replace(minute=0, second=0, microsecond=0)

    hours: list[tuple[datetime, float, str]] = []
    for entry in forecast["series"]:
        ts = parse_iso(entry["ts"])
        if ts < start:
            continue
        model = entry["models"].get(DEFAULT_MODEL_ID) or {}
        value = entry["actual"] if entry["actual"] is not None else model.get("p50")
        if value is not None:
            hours.append((ts, float(value), entry["source"]))
    if not hours:
        return []

    by_day: dict[str, list[tuple[datetime, float, str]]] = {}
    for ts, value, source in hours:
        by_day.setdefault(ts.date().isoformat(), []).append((ts, value, source))

    all_means = []
    days = []
    for date, rows in sorted(by_day.items()):
        values = [v for _, v, _ in rows]
        mean = sum(values) / len(values)
        all_means.append(mean)

        # Cheapest contiguous block, only where the day is complete enough that
        # the answer is not an artefact of a half-covered evening.
        best_from, best_mean = None, None
        if len(rows) >= CHARGE_HOURS:
            for i in range(len(rows) - CHARGE_HOURS + 1):
                window = rows[i : i + CHARGE_HOURS]
                if (window[-1][0] - window[0][0]).total_seconds() != (CHARGE_HOURS - 1) * 3600:
                    continue
                block = sum(v for _, v, _ in window) / CHARGE_HOURS
                if best_mean is None or block < best_mean:
                    best_from, best_mean = window[0][0], block

        settled = all(s != "forecast" for _, _, s in rows)
        days.append(
            {
                "date": date,
                "hours": len(rows),
                "complete": len(rows) >= 20,
                "settled": settled,
                "mean_eur_mwh": r3(mean),
                "mean_ore_kwh": r3(ore_per_kwh(mean, rate)) if rate else None,
                "min_eur_mwh": r3(min(values)),
                "max_eur_mwh": r3(max(values)),
                "min_ore_kwh": r3(ore_per_kwh(min(values), rate)) if rate else None,
                "max_ore_kwh": r3(ore_per_kwh(max(values), rate)) if rate else None,
                "cheapest_from": iso(best_from) if best_from else None,
                "cheapest_mean_ore_kwh": (
                    r3(ore_per_kwh(best_mean, rate)) if best_mean is not None and rate else None
                ),
            }
        )

    # Rank only days we have most of, so a stub evening cannot win on three hours.
    ranked = sorted(
        (d for d in days if d["complete"]), key=lambda d: d["mean_eur_mwh"]
    )
    for position, day in enumerate(ranked):
        day["rank"] = position + 1
    return days


def write_overview(
    forecasts: dict[str, dict],
    accuracy: dict,
    status: dict,
    blurb_sv: str,
    now: datetime,
) -> dict:
    tiles = []
    rate = (status.get("fx") or {}).get("rate")
    for zone, forecast in forecasts.items():
        drivers = forecast.get("drivers", {})
        tiles.append(
            {
                "zone": zone,
                "name": ZONES[zone]["name"],
                "current": current_point(forecast, now, rate),
                "hours": _today_hours(forecast, now),
                "spark": _spark(forecast, now),
                "regime": drivers.get("regime"),
                "regime_label_sv": drivers.get("regime_label_sv"),
                "headline_sv": drivers.get("headline_sv"),
            }
        )

    snapshot = {
        zone: accuracy["table"].get(zone, {})
        for zone in ZONES
    }

    payload = {
        "generated_at": status["generated_at"],
        "run_id": status["run_id"],
        "degraded": status["degraded"],
        "demo": status.get("demo", False),
        "unit": CURRENCY,
        "resolution": RESOLUTION,
        "default_model": DEFAULT_MODEL_ID,
        "reference_model": REFERENCE_MODEL_ID,
        "fx": status.get("fx"),
        "title_sv": SITE_TITLE_SV,
        "subtitle_sv": SITE_SUBTITLE_SV,
        "blurb_sv": blurb_sv,
        "repo_url": REPO_URL,
        "zones": tiles,
        "accuracy_snapshot": snapshot,
        "accuracy_window_days": accuracy["window_days"],
        "sources": status["sources"],
        "next_expected_update_utc": status["next_expected_update_utc"],
        "models": describe_models(),
    }
    _write("overview.json", payload)
    return payload


def _quarter_series(quarters: list[dict], zone: str, forecast: dict, now: datetime) -> list[dict]:
    """The auction's own 15-minute prices, for the window it has published.

    Only where they are fact. The models forecast hourly and cannot resolve
    quarter structure days out; publishing an interpolated quarter would be
    inventing detail the forecast does not have.
    """
    rate = (forecast.get("fx") or {}).get("rate")
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    rows = []
    for row in quarters:
        if row["zone"] != zone:
            continue
        ts = parse_iso(row["ts"])
        if ts < start:
            continue
        value = float(row["price_eur_mwh"])
        rows.append(
            {
                "ts": row["ts"],
                "resolution": row.get("resolution", "PT15M"),
                "eur_mwh": r3(value),
                "ore_kwh": r3(ore_per_kwh(value, rate)) if rate else None,
            }
        )
    rows.sort(key=lambda r: r["ts"])
    return rows


def write_zone(
    zone: str,
    forecast: dict,
    history: dict,
    accuracy_slice: dict,
    now: datetime,
    quarters: list[dict] | None = None,
) -> dict:
    payload = {
        **{k: v for k, v in forecast.items() if k != "series"},
        "series": forecast["series"],
        "next_hours": _next_hours(forecast, now),
        "quarters": _quarter_series(quarters or [], zone, forecast, now),
        "day_plan": day_plan(forecast, now, quarters),
        "history": history["points"],
        "history_lead_times_h": history["lead_times_h"],
        "history_default_lead_h": history["default_lead_time_h"],
        "accuracy": accuracy_slice,
    }
    _write(f"{zone.lower()}.json", payload)
    return payload


def write_accuracy(accuracy: dict) -> None:
    _write("accuracy.json", accuracy)


def write_models() -> None:
    _write(
        "models.json",
        {"default_model": DEFAULT_MODEL_ID, "reference_model": REFERENCE_MODEL_ID, "models": describe_models()},
    )


def write_status(status: dict) -> None:
    _write("status.json", status)
