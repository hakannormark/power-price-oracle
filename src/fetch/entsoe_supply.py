"""Supply-side state from ENTSO-E: hydro reservoirs, and generation outages.

Weather explains how price varies within a week. What sets the level a week or a
season at a time is the supply side — how much water is in the reservoirs and how
much thermal capacity is actually available. v1 had neither, and took its level
from "last week", a one-sample proxy for exactly this state.

Reservoir levels are published weekly per bidding zone in MWh, with roughly a
two-week publication lag. They are a slow variable, which is what makes them
usable: the level moves far less between publication and delivery than the price
does.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import pandas as pd

from ..config import ZONES
from ..store import r3
from ..timeutil import TZ, iso, now_local
from .entsoe_fundamentals import _resolve_area

log = logging.getLogger(__name__)

# Reservoir readings are stamped at the start of the week they describe.
RESERVOIR_COLUMNS = ["ts", "zone", "stored_mwh"]


def _client():
    from entsoe import EntsoePandasClient

    return EntsoePandasClient(api_key=os.environ["ENTSOE_TOKEN"])


def fetch_reservoirs(
    start: datetime | None = None, end: datetime | None = None
) -> tuple[list[dict], dict]:
    """Weekly stored hydro energy per bidding zone, in MWh."""
    if not os.environ.get("ENTSOE_TOKEN", "").strip():
        return [], {"ok": False, "error": "ENTSOE_TOKEN is not set"}

    now = now_local()
    start = start or now - timedelta(days=365 * 4)
    end = end or now

    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001
        return [], {"ok": False, "error": f"entsoe-py unavailable: {exc}"[:160]}

    rows: list[dict] = []
    errors: list[str] = []
    for zone in ZONES:
        area = _resolve_area(zone)
        if area is None:
            errors.append(f"{zone}: no area alias")
            continue
        try:
            series = client.query_aggregate_water_reservoirs_and_hydro_storage(
                area, start=pd.Timestamp(start), end=pd.Timestamp(end)
            )
        except Exception as exc:  # noqa: BLE001 - degrade per zone
            errors.append(f"{zone}: {type(exc).__name__}")
            continue
        if series is None or len(series) == 0:
            errors.append(f"{zone}: empty")
            continue

        index = pd.to_datetime(series.index, utc=True).tz_convert(TZ)
        for ts, value in zip(index, series.to_numpy()):
            if pd.isna(value):
                continue
            rows.append(
                {
                    "ts": iso(ts.to_pydatetime()),
                    "zone": zone,
                    "stored_mwh": r3(float(value)),
                }
            )

    status: dict = {"ok": bool(rows), "rows": len(rows)}
    if errors:
        status["error"] = "; ".join(errors)[:200]
    if rows:
        log.info("ENTSO-E reservoirs: %s weekly readings", len(rows))
    return rows, status


def reservoir_features(reservoirs: list[dict], now: datetime | None = None) -> pd.DataFrame:
    """Turn weekly stored MWh into the two numbers a model can actually use.

    Absolute storage is meaningless across zones — SE1 holds 13 TWh and SE4 holds
    0.19 TWh — and meaningless across seasons, since reservoirs always fill in
    summer and drain in winter. What carries information is where the level sits
    relative to the same week of previous years, and which way it is moving.

        fill_ratio      stored / that zone's observed maximum
        week_anomaly    fill_ratio minus the mean fill_ratio for that ISO week
        change_4w       fill_ratio now minus fill_ratio four weeks ago
    """
    if not reservoirs:
        return pd.DataFrame(columns=["zone", "ts", "fill_ratio", "week_anomaly", "change_4w"])

    frame = pd.DataFrame(reservoirs)
    frame["ts"] = pd.to_datetime(frame["ts"], format="ISO8601", utc=True).dt.tz_convert(TZ)
    frame = frame.sort_values(["zone", "ts"]).drop_duplicates(["zone", "ts"], keep="last")

    frame["fill_ratio"] = frame.groupby("zone")["stored_mwh"].transform(lambda s: s / s.max())
    frame["iso_week"] = frame["ts"].dt.isocalendar().week.astype(int)

    seasonal = frame.groupby(["zone", "iso_week"])["fill_ratio"].transform("mean")
    frame["week_anomaly"] = frame["fill_ratio"] - seasonal
    frame["change_4w"] = frame.groupby("zone")["fill_ratio"].diff(4)

    return frame[["zone", "ts", "stored_mwh", "fill_ratio", "week_anomaly", "change_4w"]]


def latest_reservoir_state(reservoirs: list[dict], now: datetime | None = None) -> dict[str, dict]:
    """The most recent reading per zone, for the drivers block and the API."""
    features = reservoir_features(reservoirs, now)
    if features.empty:
        return {}
    out: dict[str, dict] = {}
    for zone, group in features.groupby("zone"):
        row = group.iloc[-1]
        out[str(zone)] = {
            "ts": iso(row["ts"].to_pydatetime()),
            "stored_mwh": r3(row["stored_mwh"]),
            "fill_ratio": r3(row["fill_ratio"]),
            "week_anomaly": r3(row["week_anomaly"]) if pd.notna(row["week_anomaly"]) else None,
            "change_4w": r3(row["change_4w"]) if pd.notna(row["change_4w"]) else None,
        }
    return out
