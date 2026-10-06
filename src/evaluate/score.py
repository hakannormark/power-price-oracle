"""Score issued forecasts against official outcomes, bucketed by horizon.

The one rule that makes these numbers honest: an hour whose day-ahead price was
already published when the forecast was issued is not scored. Copying the
exchange is not skill.

Two further rules keep one period from outweighing another. Only the first run
per model and schedule slot is scored, and skill against the reference is
measured on the hours both models forecast.
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
from ..schedule import slot_start
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


def _first_run_per_slot(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep one issue per model and schedule slot: the first.

    The schedule issues one forecast per slot, but every push to main runs the
    pipeline too. On development days that meant nineteen runs against a normal
    day's three, and scoring each one weighted those days six times over — one
    badly missed delivery day then dominated whole horizon buckets.
    """
    slots: dict[pd.Timestamp, pd.Timestamp] = {}
    for value in pd.unique(frame["issued_at"]):
        stamp = pd.Timestamp(value)
        slots[stamp] = pd.Timestamp(to_utc(slot_start(stamp.to_pydatetime())))
    slot = frame["issued_at"].map(slots).rename("slot")
    first = frame.groupby([frame["model_id"], slot])["issued_at"].transform("min")
    return frame[frame["issued_at"] == first]


def _drop_earlier_definitions(frame: pd.DataFrame, defined_since: dict[str, str] | None) -> pd.DataFrame:
    """Leave out forecasts a model issued before its current definition.

    A model id is a name, and the thing behind it has changed without the name
    changing: horizon_hybrid went from 100 % market level beyond 72 h to 60 %
    LightGBM on 2026-10-02. Scoring both under one id credits today's model with
    another model's record.
    """
    if not defined_since or frame.empty:
        return frame
    keep = pd.Series(True, index=frame.index)
    for model_id, since in defined_since.items():
        cutoff = pd.Timestamp(since).tz_convert("UTC")
        keep &= ~((frame["model_id"] == model_id) & (frame["issued_at"] < cutoff))
    return frame[keep]


def scored_rows(
    forecasts: list[dict],
    actuals: list[dict],
    now: datetime | None = None,
    defined_since: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Forecast rows joined to outcomes, filtered to genuine predictions."""
    now = now or now_local()
    frame = _to_frame(forecasts)
    if frame.empty:
        return frame.assign(actual=[], bucket=[])
    frame = _drop_earlier_definitions(frame, defined_since)
    if frame.empty:
        return frame.assign(actual=[], bucket=[])

    cutoff = pd.Timestamp(to_utc(now - timedelta(days=EVAL_WINDOW_DAYS)))
    frame = frame[frame["ts"] >= cutoff]
    frame = frame[(frame["horizon_h"] >= 0) & (frame["horizon_h"] < HORIZON_HOURS)]
    if frame.empty:
        return frame.assign(actual=[], bucket=[])
    frame = _first_run_per_slot(frame)

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


def _add_skill(per_model: dict, frame: pd.DataFrame, reference_id: str) -> None:
    """skill = 1 - mae_model / mae_reference, per bucket, on paired hours.

    A model added last week has a shorter record than the reference. Dividing
    its MAE by the reference's full-window MAE compares two different stretches
    of weather, so both are measured on the forecasts they share: same issue,
    zone and delivery hour. The reference scores 0.
    """
    if not per_model:
        return
    keys = ["issued_at", "zone", "ts"]
    reference = frame[frame["model_id"] == reference_id][keys + ["p50"]].rename(
        columns={"p50": "p50_reference"}
    )
    for model_id, buckets in per_model.items():
        if model_id == reference_id:
            for stats in buckets.values():
                stats["skill_vs_naive"] = 0.0 if stats.get("mae") else None
            continue
        paired = frame[frame["model_id"] == model_id].merge(reference, on=keys, how="inner")
        for bucket, stats in buckets.items():
            group = paired[paired["bucket"] == bucket]
            stats["skill_vs_naive"] = None
            if len(group) < MIN_SAMPLES_FOR_STATS:
                continue
            baseline = float((group["p50_reference"] - group["actual"]).abs().mean())
            if baseline > 0:
                mae = float((group["p50"] - group["actual"]).abs().mean())
                stats["skill_vs_naive"] = r3(1.0 - mae / baseline)


# ------------------------------------------------------------------ promotion
#
# The rule a model must pass before it replaces the default. Twice a model was
# made the default on evidence that did not hold: recency_scaled on a back-test
# that scored already-published hours, and horizon_hybrid after five days.
PROMOTION_MIN_DAYS = 21      # delivery days shared with the default
PROMOTION_CONFIDENCE = 0.90  # width of the interval on the difference in MAE
_BOOTSTRAP_ROUNDS = 400


def _day_block_interval(day_sums: pd.DataFrame) -> tuple[float, float] | None:
    """Interval on MAE(candidate) - MAE(default), resampling whole delivery days.

    Hours inside a day share weather and a price level, so they are not
    independent observations; treating them as such makes every interval far
    too narrow. The day is the unit that is resampled.
    """
    if len(day_sums) < 5:
        return None
    import numpy as np

    rng = np.random.default_rng(0)  # fixed: the page must not flicker between runs
    diff = day_sums["diff_sum"].to_numpy()
    n = day_sums["n"].to_numpy()
    picks = rng.integers(0, len(diff), size=(_BOOTSTRAP_ROUNDS, len(diff)))
    samples = diff[picks].sum(axis=1) / n[picks].sum(axis=1)
    tail = (1.0 - PROMOTION_CONFIDENCE) / 2.0
    return float(np.quantile(samples, tail)), float(np.quantile(samples, 1.0 - tail))


def promotion_table(frame: pd.DataFrame, model_ids: list[str], default_id: str) -> dict:
    """Each model against the default, on the forecasts both issued."""
    out: dict = {
        "min_days": PROMOTION_MIN_DAYS,
        "confidence": PROMOTION_CONFIDENCE,
        "default_model": default_id,
        "candidates": {},
    }
    if frame.empty or default_id not in set(frame["model_id"]):
        return out
    keys = ["issued_at", "zone", "ts"]
    default = frame[frame["model_id"] == default_id][keys + ["p50"]].rename(columns={"p50": "p50_default"})
    for model_id in model_ids:
        if model_id == default_id:
            continue
        paired = frame[frame["model_id"] == model_id].merge(default, on=keys, how="inner")
        if paired.empty:
            continue
        paired = paired.assign(
            err=(paired["p50"] - paired["actual"]).abs(),
            err_default=(paired["p50_default"] - paired["actual"]).abs(),
            day=paired["ts"].dt.tz_convert("Europe/Stockholm").dt.date,
        )
        paired["diff_sum"] = paired["err"] - paired["err_default"]

        def block(group: pd.DataFrame) -> dict | None:
            if len(group) < MIN_SAMPLES_FOR_STATS:
                return None
            days = group.groupby("day").agg(diff_sum=("diff_sum", "sum"), n=("diff_sum", "size"))
            interval = _day_block_interval(days)
            return {
                "n": int(len(group)),
                "days": int(len(days)),
                "mae": r3(group["err"].mean()),
                "mae_default": r3(group["err_default"].mean()),
                "diff": r3(group["diff_sum"].mean()),
                "lo": r3(interval[0]) if interval else None,
                "hi": r3(interval[1]) if interval else None,
            }

        overall = block(paired)
        if overall is None:
            continue
        buckets = {}
        for bucket in BUCKET_LABELS:
            stats = block(paired[paired["bucket"] == bucket])
            if stats:
                buckets[bucket] = stats

        # Better overall with the whole interval below zero, on enough days, and
        # not reliably worse at any single horizon.
        enough = overall["days"] >= PROMOTION_MIN_DAYS
        better = overall["hi"] is not None and overall["hi"] < 0
        worse_somewhere = [b for b, st in buckets.items() if st["lo"] is not None and st["lo"] > 0]
        if not enough:
            verdict = "too_early"
        elif better and not worse_somewhere:
            verdict = "eligible"
        elif overall["lo"] is not None and overall["lo"] > 0:
            verdict = "worse"
        else:
            verdict = "not_shown"
        out["candidates"][model_id] = {
            "overall": overall, "buckets": buckets, "verdict": verdict, "worse_at": worse_somewhere,
        }
    return out


def evaluate(
    forecasts: list[dict],
    actuals: list[dict],
    model_ids: list[str],
    reference_id: str = "seasonal_naive",
    now: datetime | None = None,
    default_id: str = "ensemble",
    defined_since: dict[str, str] | None = None,
) -> dict:
    """Full accuracy payload: per zone, overall, plus the compact MAE table."""
    now = now or now_local()
    frame = scored_rows(forecasts, actuals, now, defined_since)

    zones: dict[str, dict] = {}
    for zone in ZONES:
        subset = frame[frame["zone"] == zone] if not frame.empty else frame
        per_model = _bucketed(subset, model_ids) if not subset.empty else {}
        _add_skill(per_model, subset, reference_id)
        zones[zone] = per_model

    overall = _bucketed(frame, model_ids) if not frame.empty else {}
    _add_skill(overall, frame, reference_id)

    table = {"ALL": _mae_table(overall, model_ids)}
    for zone, per_model in zones.items():
        table[zone] = _mae_table(per_model, model_ids)

    # The same table on the hours every horizon forecast — the only fair way to
    # read a horizon curve off these bars.
    fair = comparable_frame(frame)
    fair_table = {"ALL": _mae_table(_bucketed(fair, model_ids) if not fair.empty else {}, model_ids)}
    for zone in ZONES:
        subset = fair[fair["zone"] == zone] if not fair.empty else fair
        fair_table[zone] = _mae_table(
            _bucketed(subset, model_ids) if not subset.empty else {}, model_ids
        )

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
        # The window is a ceiling; what was actually scored starts where the
        # log does. Readers took "90 dygn" to mean ninety days of data.
        "scored_from": iso(frame["ts"].min().to_pydatetime()) if not frame.empty else None,
        "scored_to": iso(frame["ts"].max().to_pydatetime()) if not frame.empty else None,
        "zones": zones,
        "overall": overall,
    }
    payload["table"] = table
    payload["defined_since"] = dict(defined_since or {})
    payload["promotion"] = promotion_table(frame, model_ids, default_id)
    payload["comparable"] = {
        "hours": 0 if fair.empty else int(fair.groupby(["zone", "ts"]).ngroups),
        "table": fair_table,
    }
    log.info("Evaluated %s scored forecast points", len(frame))
    return payload


def comparable_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Only the delivery hours every horizon forecast, so the buckets compare like with like.

    A bucket holds the hours its horizon can reach, and those differ. 0-24h can
    only ever hold the night and morning of tomorrow: the afternoon's price is
    published at 12:45, before a forecast less than a day ahead could be issued,
    and a copy of the exchange is not scored. Those small hours are cheaper and
    calmer than the evening peak every other bucket contains, so comparing
    bucket means straight across compares different hours, not horizons.
    """
    if frame.empty:
        return frame
    counts = frame.groupby(["model_id", "zone", "ts"])["bucket"].nunique()
    keys = counts[counts == len(BUCKET_LABELS)].reset_index()[["model_id", "zone", "ts"]]
    return frame.merge(keys, on=["model_id", "zone", "ts"], how="inner")


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
        "scored_from": accuracy.get("scored_from"),
        "scored_to": accuracy.get("scored_to"),
        "unit": accuracy["unit"],
        "min_samples": accuracy["min_samples"],
        "buckets": accuracy["buckets"],
        "models": accuracy["models"],
        "reference_model": accuracy["reference_model"],
        "default_model": accuracy["default_model"],
        "metrics": accuracy["zones"].get(zone, {}),
        "table": accuracy["table"].get(zone, {}),
        "comparable": {
            "hours": accuracy.get("comparable", {}).get("hours", 0),
            "table": accuracy.get("comparable", {}).get("table", {}).get(zone, {}),
        },
    }
