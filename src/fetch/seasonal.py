"""Seasonal weather outlook: ECMWF SEAS5 monthly anomalies, through Open-Meteo.

Temperature drives winter demand, and precipitation in the north is next
season's hydro. The outlook is shown beside the long-term forecast as
context, and deliberately not used by any model: there is no free archive of
past seasonal forecasts to backtest a coefficient against, and a term that
cannot be tested is not added (see CONTRIBUTING.md).

No key is needed. Anomalies are against the model's own climatology.
"""

from __future__ import annotations

import logging

from ..config import SEASONAL_API_URL, ZONES
from .http import get

log = logging.getLogger(__name__)

VARIABLES = ("temperature_2m_anomaly", "precipitation_anomaly")


def parse_outlook(payload: dict) -> list[dict]:
    """One row per month: 'YYYY-MM', temperature anomaly (K), precipitation anomaly (mm)."""
    monthly = payload.get("monthly") or {}
    months = monthly.get("time") or []
    temps = monthly.get("temperature_2m_anomaly") or [None] * len(months)
    rain = monthly.get("precipitation_anomaly") or [None] * len(months)
    return [
        {
            "month": month[:7],
            "temp_anomaly_c": None if t is None else round(float(t), 1),
            "precip_anomaly_mm": None if p is None else round(float(p), 1),
        }
        for month, t, p in zip(months, temps, rain)
    ]


def fetch_outlook() -> tuple[dict[str, list[dict]], dict]:
    """Monthly anomalies at each zone's reference point. Never raises."""
    out: dict[str, list[dict]] = {}
    errors: list[str] = []
    for zone, meta in ZONES.items():
        try:
            payload = get(
                SEASONAL_API_URL,
                params={
                    "latitude": meta["lat"],
                    "longitude": meta["lon"],
                    "monthly": ",".join(VARIABLES),
                },
                retries=2,
            ).json()
            if payload.get("error"):
                raise ValueError(payload.get("reason", "error"))
            out[zone] = parse_outlook(payload)
        except Exception as exc:  # noqa: BLE001 - degrade per zone
            errors.append(f"{zone}: {type(exc).__name__}")
    status: dict = {"ok": bool(out), "zones": len(out)}
    if errors:
        status["error"] = "; ".join(errors)[:200]
    return out, status
