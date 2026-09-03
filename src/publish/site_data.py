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
from ..timeutil import parse_iso
from .api import current_point

log = logging.getLogger(__name__)

SPARK_HOURS = 24


def _write(name: str, payload: Any) -> None:
    ensure_dirs()
    target = SITE_DATA_DIR / name
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


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
                "ore_kwh": r3(ore_per_kwh(value)) if value is not None else None,
                "p10": model.get("p10"),
                "p90": model.get("p90"),
            }
        )
    return rows


def write_overview(
    forecasts: dict[str, dict],
    accuracy: dict,
    status: dict,
    blurb_sv: str,
    now: datetime,
) -> dict:
    tiles = []
    for zone, forecast in forecasts.items():
        drivers = forecast.get("drivers", {})
        tiles.append(
            {
                "zone": zone,
                "name": ZONES[zone]["name"],
                "current": current_point(forecast, now),
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


def write_zone(
    zone: str,
    forecast: dict,
    history: dict,
    accuracy_slice: dict,
    now: datetime,
) -> dict:
    payload = {
        **{k: v for k, v in forecast.items() if k != "series"},
        "series": forecast["series"],
        "next_hours": _next_hours(forecast, now),
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
