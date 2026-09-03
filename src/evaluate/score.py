"""Score issued forecasts against official outcomes, bucketed by horizon.

The one rule that makes these numbers honest: an hour whose day-ahead price was
already published when the forecast was issued is not scored. Copying the
exchange is not skill.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import pandas as pd

from ..config import (
    CURRENCY,
    EVAL_WINDOW_DAYS,
    HORIZON_HOURS,
    MIN_SAMPLES_FOR_CORR,
    MIN_SAMPLES_FOR_STATS,
    ZONES,
)
from ..store import r3
from ..timeutil import auction_publication_time, iso, now_local, to_utc
from .horizon import BUCKET_LABELS, bucket_for_horizon

log = logging.getLogger(__name__)

MAPE_MIN_ABS_PRICE = 5.0


def _to_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["issued_at", "model_id", "zone", "ts", "horizon_h", "p10", "p50", "p90"]
        )
    frame = pd.DataFrame(rows)
    frame["issued_at"] = pd.to_datetime(frame["issued_at"], format="ISO8601", utc=True)
    frame["ts"] = pd.to_datetime(frame["ts"], format="ISO8601", utc=True)
    for column in ("p10", "p50", "p90", "horizon_h"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _actuals_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["zone", "ts", "actual"])
    frame = pd.DataFrame(
        [{"zone": r["zone"], "ts": r["ts"], "actual": float(r["price_eur_mwh"])} for r in rows]
    )
    frame["ts"] = pd.to_datetime(frame["ts"], format="ISO8601", utc=True)
    return frame.drop_duplicates(subset=["zone", "ts"], keep="last")


def _publication_map(timestamps: pd.Series) -> dict:
    """Publication instant per unique target hour, computed in local time.

    Doing this per unique timestamp keeps the DST-correct local arithmetic
    affordable even on a large forecast log.
    """
    out: dict[pd.Timestamp, pd.Timestamp] = {}
    for value in pd.unique(timestamps):
        stamp = pd.Timestamp(value)
        local = stamp.to_pydatetime()
        out[stamp] = pd.Timestamp(to_utc(auction_publication_time(local)))
    return out


def scored_rows(forecasts: list[dict], actuals: list[dict], now: datetime | None = None) -> pd.DataFrame:
    """Forecast rows joined to outcomes, filtered to genuine predictions."""
    now = now or now_local()
    frame = _to_frame(forecasts)
    if frame.empty:
        return frame.assign(actual=[], bucket=[])

    cutoff = pd.Timestamp(to_utc(now - timedelta(days=EVAL_WINDOW_DAYS)))
    frame = frame[frame["ts"] >= cutoff]
    frame = frame[(frame["horizon_h"] >= 0) & (frame["horizon_h"] < HORIZON_HOURS)]
    if frame.empty:
        return frame.assign(actual=[], bucket=[])

    outcomes = _actuals_frame(actuals)
    frame = frame.merge(outcomes, on=["zone", "ts"], how="inner")
    if frame.empty:
        return frame.assign(bucket=[])

    publications = _publication_map(frame["ts"])
    published_at = frame["ts"].map(publications)
    frame = frame[frame["issued_at"] < published_at]
    if frame.empty:
        return frame.assign(bucket=[])

    frame["bucket"] = frame["horizon_h"].astype(int).map(bucket_for_horizon)
    return frame.dropna(subset=["bucket", "p50", "actual"])


def _metrics(group: pd.DataFrame) -> dict:
    error = group["p50"] - group["actual"]
    n = int(len(group))

    mape = None
    significant = group[group["actual"].abs() > MAPE_MIN_ABS_PRICE]
    if len(significant) >= MIN_SAMPLES_FOR_STATS:
        mape = float(
            ((significant["p50"] - significant["actual"]).abs() / significant["actual"].abs()).mean() * 100
        )

    corr = None
    if n >= MIN_SAMPLES_FOR_CORR and group["p50"].std() > 0 and group["actual"].std() > 0:
        value = float(group["p50"].corr(group["actual"]))
        corr = None if math.isnan(value) else value

    inside = ((group["p10"] <= group["actual"]) & (group["actual"] <= group["p90"])).mean()

    return {
        "n": n,
        "mae": r3(error.abs().mean()),
        "rmse": r3(math.sqrt(float((error**2).mean()))),
        "bias": r3(error.mean()),
        "coverage80": r3(float(inside)),
        "mape": r3(mape) if mape is not None else None,
        "p50_corr": r3(corr) if corr is not None else None,
        "enough_data": n >= MIN_SAMPLES_FOR_STATS,
    }


def _bucketed(frame: pd.DataFrame, model_ids: list[str]) -> dict:
    out: dict[str, dict] = {}
    for model_id in model_ids:
        subset = frame[frame["model_id"] == model_id]
        per_bucket = {}
        for bucket in BUCKET_LABELS:
            group = subset[subset["bucket"] == bucket]
            if group.empty:
                continue
            per_bucket[bucket] = _metrics(group)
        if per_bucket:
            out[model_id] = per_bucket
    return out


def _add_skill(per_model: dict, reference_id: str) -> None:
    """skill = 1 - mae_model / mae_reference, per bucket. Reference scores 0."""
    reference = per_model.get(reference_id, {})
    for model_id, buckets in per_model.items():
        for bucket, stats in buckets.items():
            baseline = reference.get(bucket, {}).get("mae")
            if baseline and baseline > 0 and stats.get("mae") is not None:
                stats["skill_vs_naive"] = r3(1.0 - stats["mae"] / baseline)
            else:
                stats["skill_vs_naive"] = None


def evaluate(
    forecasts: list[dict],
    actuals: list[dict],
    model_ids: list[str],
    reference_id: str = "seasonal_naive",
    now: datetime | None = None,
    default_id: str = "ensemble",
) -> dict:
    """Full accuracy payload: per zone, overall, plus the compact MAE table."""
    now = now or now_local()
    frame = scored_rows(forecasts, actuals, now)

    zones: dict[str, dict] = {}
    for zone in ZONES:
        subset = frame[frame["zone"] == zone] if not frame.empty else frame
        per_model = _bucketed(subset, model_ids) if not subset.empty else {}
        _add_skill(per_model, reference_id)
        zones[zone] = per_model

    overall = _bucketed(frame, model_ids) if not frame.empty else {}
    _add_skill(overall, reference_id)

    table = {"ALL": _mae_table(overall, model_ids)}
    for zone, per_model in zones.items():
        table[zone] = _mae_table(per_model, model_ids)

    payload = {
        "generated_at": iso(now),
        "window_days": EVAL_WINDOW_DAYS,
        "unit": CURRENCY,
        "min_samples": MIN_SAMPLES_FOR_STATS,
        "buckets": BUCKET_LABELS,
        "models": model_ids,
        "reference_model": reference_id,
        "default_model": default_id,
        "scored_points": int(len(frame)),
        "zones": zones,
        "overall": overall,
    }
    payload["table"] = table
    log.info("Evaluated %s scored forecast points", len(frame))
    return payload


def _mae_table(per_model: dict, model_ids: list[str]) -> dict:
    """Rows = horizon buckets, columns = models, cell = MAE (null when too thin)."""
    rows: dict[str, dict] = {}
    for bucket in BUCKET_LABELS:
        row = {}
        for model_id in model_ids:
            stats = per_model.get(model_id, {}).get(bucket)
            row[model_id] = stats["mae"] if stats and stats.get("enough_data") else None
        rows[bucket] = row
    return rows


def zone_slice(accuracy: dict, zone: str) -> dict:
    """The per-zone accuracy file served under api/v1/zones/<zone>/accuracy.json."""
    return {
        "zone": zone,
        "zone_name": ZONES[zone]["name"],
        "generated_at": accuracy["generated_at"],
        "window_days": accuracy["window_days"],
        "unit": accuracy["unit"],
        "min_samples": accuracy["min_samples"],
        "buckets": accuracy["buckets"],
        "models": accuracy["models"],
        "reference_model": accuracy["reference_model"],
        "default_model": accuracy["default_model"],
        "metrics": accuracy["zones"].get(zone, {}),
        "table": accuracy["table"].get(zone, {}),
    }
