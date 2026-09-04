"""Score candidate forecasts against four years of outcomes, out of sample.

    python -m src.research.backtest

The one rule that makes the numbers mean anything: a candidate issued at T may
use only what was published before T. Prices are lagged, outages are filtered on
their publication timestamp, and every fitted coefficient is estimated on data
strictly older than the quarter it is applied to. A coefficient that only works
in hindsight cannot flatter the result here.

This exists because guessing has already been wrong twice in this project: a
four-week median looked obviously right and was 8.5 % worse, and a trend
adjustment was 39 % worse. Nothing reaches the site now without passing through
this file first.
"""

from __future__ import annotations

import argparse
import logging
import statistics as st
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..config import HORIZON_HOURS, ZONES
from ..config import ALL_WEATHER_POINTS, NORTH_WIND_POINTS, SOUTH_WIND_POINTS, WEATHER_ARCHIVE_DIR
from ..fetch.nordpool_umm import hourly_outages
from ..store import read_jsonl
from ..store import load_actuals, load_umm
from ..timeutil import now_local, parse_iso

log = logging.getLogger("backtest")

ISSUE_HOUR = 8
WARMUP_DAYS = 40


@dataclass
class Sample:
    """One scored point: what was knowable, and what actually happened."""

    issued: datetime
    ts: datetime
    zone: str
    truth: float
    lag168: float
    median4: float
    nuclear_out_mw: float = 0.0
    production_out_mw: float = 0.0
    import_lost_mw: float = 0.0
    export_lost_mw: float = 0.0
    # Change against the same hour one week earlier — the week the level is
    # copied from. Raw MW out is confounded with season, because planned
    # maintenance is scheduled into low-price months; the change is not.
    d_nuclear_mw: float = 0.0
    d_production_mw: float = 0.0
    d_import_mw: float = 0.0
    d_export_mw: float = 0.0
    wind_index_local: float = 1.0
    wind_index_north: float = 1.0
    wind_index_south: float = 1.0
    temp_anomaly_local: float = 0.0
    solar_index_local: float = 0.0
    horizon_h: int = 0


def load_weather_archive() -> dict:
    """ERA5 history, indexed by (point, hour), with the same normals the live
    pipeline derives: wind against the point's own mean, temperature against the
    mean for that hour of day, solar against 400 W/m2."""
    import numpy as np

    raw: dict[str, dict[datetime, dict]] = {}
    for path in sorted(WEATHER_ARCHIVE_DIR.glob("*.jsonl")):
        point = path.stem
        rows = list(read_jsonl(path))
        if not rows:
            continue
        frame = pd.DataFrame(rows)
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert("Europe/Stockholm")
        for column in ("temp", "wind", "solar"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        wind_normal = frame["wind"].mean() or 5.0
        frame["wind_index"] = (frame["wind"] / wind_normal).clip(0.4, 2.5)

        # Against a seasonal normal, not an all-year hour-of-day mean. The
        # latter makes every September hour look warm and turns the feature
        # into a proxy for the season, which the price lag already carries.
        # Week of year, not day: a day-of-year normal splits four years into
        # groups of three observations and the "anomaly" becomes noise.
        key = frame["ts"].dt.isocalendar().week.astype(int) * 100 + frame["ts"].dt.hour
        seasonal = (
            frame.assign(_k=key)
            .groupby("_k")["temp"]
            .transform(lambda s: s.mean())
        )
        frame["temp_anomaly"] = frame["temp"] - seasonal
        frame["solar_index"] = (frame["solar"] / 400.0).clip(0, 2).fillna(0.0)

        raw[point] = {
            ts.to_pydatetime(): {
                "wind_index": w,
                "temp_anomaly": a,
                "solar_index": s,
            }
            for ts, w, a, s in zip(
                frame["ts"], frame["wind_index"], frame["temp_anomaly"], frame["solar_index"]
            )
        }
    return raw


def build_samples(
    days: int | None = None, issue_every: int = 1, with_outages: bool = True
) -> list[Sample]:
    """Walk the history issuing a forecast each day and recording the outcome."""
    prices: dict[tuple[str, datetime], float] = {}
    for row in load_actuals():
        prices[(row["zone"], parse_iso(row["ts"]))] = float(row["price_eur_mwh"])
    if not prices:
        return []

    times = sorted({t for _, t in prices})
    first, last = times[0], times[-1]
    if days:
        first = max(first, last - timedelta(days=days))
    log.info("prices %s .. %s (%s hours)", first.date(), last.date(), len(prices))

    umm_rows = load_umm() if with_outages else []
    log.info("outage rows: %s", len(umm_rows))
    weather = load_weather_archive()
    log.info("weather points: %s", len(weather))

    def regional(ts: datetime, points: list[str], field: str) -> float:
        values = [weather[p][ts][field] for p in points if p in weather and ts in weather[p]]
        values = [v for v in values if v == v]
        return float(sum(values) / len(values)) if values else (1.0 if "index" in field else 0.0)

    samples: list[Sample] = []
    issue = (first + timedelta(days=WARMUP_DAYS)).replace(
        hour=ISSUE_HOUR, minute=0, second=0, microsecond=0
    )
    while issue < last - timedelta(days=1):
        window_end = issue + timedelta(hours=HORIZON_HOURS)

        outage_lookup: dict[tuple[str, datetime], dict] = {}
        if umm_rows:
            # Reach a week further back so each target hour can be compared with
            # the reference week the level is taken from.
            frame = hourly_outages(
                umm_rows, issue - timedelta(hours=168), window_end, as_of=issue
            )
            for row in frame.itertuples(index=False):
                outage_lookup[(row.zone, row.ts.to_pydatetime())] = {
                    "nuclear_out_mw": row.nuclear_out_mw,
                    "production_out_mw": row.production_out_mw,
                    "import_lost_mw": row.import_lost_mw,
                    "export_lost_mw": row.export_lost_mw,
                }

        for zone in ZONES:
            for h in range(1, HORIZON_HOURS + 1):
                ts = issue + timedelta(hours=h)
                truth = prices.get((zone, ts))
                if truth is None:
                    continue
                lags = [prices.get((zone, ts - timedelta(days=7 * k))) for k in (1, 2, 3, 4)]
                if any(v is None for v in lags):
                    continue
                now_state = outage_lookup.get((zone, ts), {})
                then_state = outage_lookup.get((zone, ts - timedelta(hours=168)), {})
                deltas = {
                    "d_nuclear_mw": now_state.get("nuclear_out_mw", 0.0) - then_state.get("nuclear_out_mw", 0.0),
                    "d_production_mw": now_state.get("production_out_mw", 0.0) - then_state.get("production_out_mw", 0.0),
                    "d_import_mw": now_state.get("import_lost_mw", 0.0) - then_state.get("import_lost_mw", 0.0),
                    "d_export_mw": now_state.get("export_lost_mw", 0.0) - then_state.get("export_lost_mw", 0.0),
                }
                local = weather.get(zone, {}).get(ts, {})
                samples.append(
                    Sample(
                        wind_index_local=float(local.get("wind_index", 1.0) or 1.0),
                        wind_index_north=regional(ts, NORTH_WIND_POINTS, "wind_index"),
                        wind_index_south=regional(ts, SOUTH_WIND_POINTS, "wind_index"),
                        temp_anomaly_local=float(local.get("temp_anomaly", 0.0) or 0.0),
                        solar_index_local=float(local.get("solar_index", 0.0) or 0.0),
                        issued=issue,
                        ts=ts,
                        zone=zone,
                        truth=truth,
                        lag168=lags[0],
                        median4=st.median(lags),
                        horizon_h=h,
                        **{k: float(v) for k, v in now_state.items()},
                        **{k: float(v) for k, v in deltas.items()},
                    )
                )
        issue += timedelta(days=issue_every)

    log.info("samples: %s", len(samples))
    return samples


def to_frame(samples: list[Sample]) -> pd.DataFrame:
    from ..models.weather_scaled import compute_scale

    frame = pd.DataFrame([s.__dict__ for s in samples])
    if frame.empty:
        return frame
    frame["level_naive"] = frame["lag168"]
    frame["level_shrunk"] = 0.70 * frame["lag168"] + 0.30 * frame["median4"]

    # The shipped weather scale, applied exactly as production applies it, so
    # the twenty hand-set coefficients get scored rather than assumed.
    frame["guessed_scale"] = [
        compute_scale(z, wl, wn, ws, ta, si)
        for z, wl, wn, ws, ta, si in zip(
            frame["zone"], frame["wind_index_local"], frame["wind_index_north"],
            frame["wind_index_south"], frame["temp_anomaly_local"], frame["solar_index_local"],
        )
    ]
    frame["level_weather_guessed"] = frame["level_shrunk"] * frame["guessed_scale"]
    # Exactly what weather_scaled ships: the naive level, same scale.
    frame["level_weather_on_naive"] = frame["level_naive"] * frame["guessed_scale"]
    # Ensemble blends, to choose weights by measurement rather than by taste.
    frame["ens_current"] = 0.35 * frame["level_naive"] + 0.65 * frame["level_weather_on_naive"]
    frame["ens_shrunk_only"] = frame["level_weather_guessed"]
    frame["ens_80_20_naive"] = (
        0.80 * frame["level_weather_guessed"] + 0.20 * frame["level_naive"]
    )
    frame["ens_65_35_weather"] = (
        0.65 * frame["level_weather_guessed"] + 0.35 * frame["level_weather_on_naive"]
    )
    frame["ens_thirds"] = (
        frame["level_weather_guessed"] / 3
        + frame["level_weather_on_naive"] / 3
        + frame["level_naive"] / 3
    )
    # Centred drivers, so a fitted coefficient of zero means "ignore this".
    frame["wind_dev"] = frame["wind_index_local"] - 1.0
    frame["wind_north_dev"] = frame["wind_index_north"] - 1.0
    frame["wind_south_dev"] = frame["wind_index_south"] - 1.0
    frame["temp_dev"] = frame["temp_anomaly_local"] / 10.0
    frame["quarter"] = frame["issued"].dt.tz_localize(None).dt.to_period("Q")
    frame["bucket"] = (frame["horizon_h"] - 1) // 24
    return frame


# ------------------------------------------------------------------ candidates


def _fit_scale(base: np.ndarray, truth: np.ndarray, signal: np.ndarray, grid) -> float:
    """Best multiplier on one driver, given whatever adjustment came before it.

    Taking `base` rather than the raw level matters: wind indices for a zone and
    its region are strongly correlated, and fitting each against the unadjusted
    level then applying them all together corrects the same signal several times
    over.
    """
    best_beta, best_mae = 0.0, float("inf")
    for beta in grid:
        mae = float(np.abs(base * (1 + beta * signal) - truth).mean())
        if mae < best_mae:
            best_beta, best_mae = float(beta), mae
    return best_beta


def evaluate(
    frame: pd.DataFrame,
    level: str,
    drivers: list[str] | None = None,
    per_zone: bool = False,
) -> dict:
    """Out-of-sample MAE, fitting any driver coefficients on earlier quarters.

    per_zone fits each coefficient separately for each bidding zone. The shipped
    hand-set weights differ per zone, so comparing them against a single global
    fitted number confuses structure with calibration.
    """
    quarters = sorted(frame["quarter"].unique())
    # Every quarter is scored except those without enough earlier data to fit on.
    min_train = max(2000, len(frame) // 20)
    scored: list[pd.DataFrame] = []
    fitted: dict[str, list[float]] = defaultdict(list)

    for q in quarters:
        train = frame[frame["quarter"] < q]
        test = frame[frame["quarter"] == q]
        if len(train) < min_train or test.empty:
            continue

        prediction = test[level].to_numpy().copy()
        train_running = train[level].to_numpy().copy()
        train_truth = train["truth"].to_numpy()
        groups = sorted(frame["zone"].unique()) if per_zone else [None]

        for driver in drivers or []:
            spread = train[driver].std()
            if not spread or np.isnan(spread):
                continue
            # Search in units of the driver's own spread so one grid fits
            # megawatts of lost interconnection and a unitless wind index alike.
            grid = np.arange(-0.6, 0.601, 0.02) / spread
            for group in groups:
                tr_mask = (
                    np.ones(len(train), dtype=bool) if group is None
                    else (train["zone"].to_numpy() == group)
                )
                te_mask = (
                    np.ones(len(test), dtype=bool) if group is None
                    else (test["zone"].to_numpy() == group)
                )
                if tr_mask.sum() < 500:
                    continue
                beta = _fit_scale(
                    train_running[tr_mask],
                    train_truth[tr_mask],
                    train[driver].to_numpy()[tr_mask],
                    grid,
                )
                label = driver if group is None else f"{driver}[{group}]"
                fitted[label].append(beta * spread)
                train_running[tr_mask] *= 1 + beta * train[driver].to_numpy()[tr_mask]
                prediction[te_mask] *= 1 + beta * test[driver].to_numpy()[te_mask]

        scored.append(
            pd.DataFrame(
                {
                    "error": np.abs(prediction - test["truth"].to_numpy()),
                    "bucket": test["bucket"].to_numpy(),
                    "zone": test["zone"].to_numpy(),
                }
            )
        )

    if not scored:
        return {"mae": float("nan"), "n": 0, "buckets": {}, "zones": {}, "fitted": {}}

    everything = pd.concat(scored, ignore_index=True)
    return {
        "mae": float(everything["error"].mean()),
        "n": int(len(everything)),
        "buckets": {int(b): float(v) for b, v in everything.groupby("bucket")["error"].mean().items()},
        "zones": {str(z): float(v) for z, v in everything.groupby("zone")["error"].mean().items()},
        "fitted": {d: float(np.mean(v)) for d, v in fitted.items()},
    }


# (label, level column, drivers, fit per zone)
CANDIDATES: list[tuple] = [
    ("seasonal_naive", "level_naive", []),
    ("shrunk level", "level_shrunk", []),
    # Raw levels, kept to show the confounding rather than hide it.
    ("+ nuclear out (raw MW)", "level_shrunk", ["nuclear_out_mw"]),
    ("+ import lost (raw MW)", "level_shrunk", ["import_lost_mw"]),
    # Change against the reference week.
    ("+ d nuclear", "level_shrunk", ["d_nuclear_mw"]),
    ("+ d production", "level_shrunk", ["d_production_mw"]),
    ("+ d import", "level_shrunk", ["d_import_mw"]),
    ("+ d export", "level_shrunk", ["d_export_mw"]),
    ("+ d nuclear, d import", "level_shrunk", ["d_nuclear_mw", "d_import_mw"]),
    (
        "+ all deltas",
        "level_shrunk",
        ["d_nuclear_mw", "d_production_mw", "d_import_mw", "d_export_mw"],
    ),
    # The twenty hand-set weather coefficients, against fitted alternatives and
    # against not scaling by weather at all.
    ("ensemble as shipped", "ens_current", []),
    ("ensemble = shrunk_scaled", "ens_shrunk_only", []),
    ("ensemble 80/20 naive", "ens_80_20_naive", []),
    ("ensemble 65/35 weather", "ens_65_35_weather", []),
    ("ensemble equal thirds", "ens_thirds", []),
    ("weather_scaled (shipped)", "level_weather_on_naive", []),
    ("shrunk_scaled (shipped)", "level_weather_guessed", []),
    ("weather, fitted wind", "level_shrunk", ["wind_dev"]),
    ("weather, fitted wind+temp", "level_shrunk", ["wind_dev", "temp_dev"]),
    (
        "weather, fitted all five",
        "level_shrunk",
        ["wind_dev", "wind_north_dev", "wind_south_dev", "temp_dev", "solar_index_local"],
    ),
    # Per zone: the same structure the hand-set weights have, but measured.
    ("per-zone wind", "level_shrunk", ["wind_dev"], True),
    ("per-zone wind+temp", "level_shrunk", ["wind_dev", "temp_dev"], True),
    ("per-zone wind+temp+solar", "level_shrunk", ["wind_dev", "temp_dev", "solar_index_local"], True),
    (
        "per-zone all five",
        "level_shrunk",
        ["wind_dev", "wind_north_dev", "wind_south_dev", "temp_dev", "solar_index_local"],
        True,
    ),
    ("per-zone wind, on guessed", "level_weather_guessed", ["wind_dev"], True),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back-test candidate forecast levels")
    parser.add_argument("--days", type=int, default=None, help="limit the history window")
    parser.add_argument("--issue-every", type=int, default=2, help="days between issues")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    frame = to_frame(build_samples(args.days, args.issue_every))
    if frame.empty:
        print("No samples — is data/actuals populated?")
        return 1

    print(
        f"\n{len(frame):,} scored hours, {frame['issued'].min():%Y-%m-%d} to "
        f"{frame['issued'].max():%Y-%m-%d}, {frame['quarter'].nunique()} quarters"
    )
    outage_hours = int((frame["nuclear_out_mw"] > 0).sum())
    print(f"hours with nuclear capacity out: {outage_hours:,} ({100 * outage_hours / len(frame):.1f} %)\n")

    print(f"{'candidate':<28}{'MAE':>8}{'vs naive':>10}   fitted coefficients")
    baseline = None
    results = {}
    for entry in CANDIDATES:
        name, level, drivers = entry[0], entry[1], entry[2]
        per_zone = entry[3] if len(entry) > 3 else False
        result = evaluate(frame, level, drivers, per_zone)
        results[name] = result
        if baseline is None:
            baseline = result["mae"]
        gain = 100 * (1 - result["mae"] / baseline)
        items = list(result["fitted"].items())
        coefficients = ", ".join(f"{d}={v:+.2f}" for d, v in items[:5])
        if len(items) > 5:
            coefficients += f" (+{len(items) - 5} till)"
        print(f"{name:<28}{result['mae']:>8.2f}{gain:>9.1f}%   {coefficients}")

    best = min(results.items(), key=lambda kv: kv[1]["mae"])
    print(f"\nbest: {best[0]}")
    print(f"{'day':<6}" + "".join(f"{f'd{b+1}':>9}" for b in sorted(best[1]['buckets'])))
    print(f"{'MAE':<6}" + "".join(f"{v:>9.2f}" for _, v in sorted(best[1]["buckets"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
