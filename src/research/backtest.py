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
    unplanned_production_mw: float = 0.0
    unplanned_nuclear_mw: float = 0.0
    unplanned_import_lost_mw: float = 0.0
    d_unplanned_mw: float = 0.0
    # Reservoir fill for the zone, as published before issue time.
    fill_ratio: float = float("nan")
    fill_anomaly: float = float("nan")
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

    # Reservoir readings, keyed per zone and sorted, so each issue time can pick
    # the newest reading that was already published then. ENTSO-E publishes with
    # roughly a two-week lag, which is applied here rather than assumed away.
    reservoirs: dict[str, list] = {}
    try:
        from ..fetch.entsoe_supply import reservoir_features
        from ..store import load_reservoirs

        feats = reservoir_features(load_reservoirs())
        for row in feats.itertuples():
            reservoirs.setdefault(row.zone, []).append(
                (row.ts.to_pydatetime(), row.fill_ratio, row.week_anomaly)
            )
        for zone in reservoirs:
            reservoirs[zone].sort()
        log.info("reservoir zones: %s", len(reservoirs))
    except Exception as exc:  # noqa: BLE001
        log.info("no reservoir data (%s)", exc)

    def reservoir_at(zone: str, when: datetime):
        best = (float("nan"), float("nan"))
        for ts, fill, anomaly in reservoirs.get(zone, []):
            if ts <= when - timedelta(days=14):
                best = (fill, anomaly)
            else:
                break
        return best
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
                    "unplanned_production_mw": getattr(row, "unplanned_production_mw", 0.0),
                    "unplanned_nuclear_mw": getattr(row, "unplanned_nuclear_mw", 0.0),
                    "unplanned_import_lost_mw": getattr(row, "unplanned_import_lost_mw", 0.0),
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
                    "d_unplanned_mw": now_state.get("unplanned_production_mw", 0.0)
                    - then_state.get("unplanned_production_mw", 0.0),
                }
                fill, anomaly = reservoir_at(zone, issue)
                local = weather.get(zone, {}).get(ts, {})
                samples.append(
                    Sample(
                        wind_index_local=float(local.get("wind_index", 1.0) or 1.0),
                        wind_index_north=regional(ts, NORTH_WIND_POINTS, "wind_index"),
                        wind_index_south=regional(ts, SOUTH_WIND_POINTS, "wind_index"),
                        temp_anomaly_local=float(local.get("temp_anomaly", 0.0) or 0.0),
                        solar_index_local=float(local.get("solar_index", 0.0) or 0.0),
                        fill_ratio=fill,
                        fill_anomaly=anomaly,
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

    # Reservoirs. Low storage should mean a scarcer, steeper system.
    frame["fill_dev"] = -(frame["fill_anomaly"].fillna(0.0))
    # The interaction that motivates fetching this at all: with empty reservoirs
    # hydro cannot absorb a windless week cheaply, so the same wind deviation
    # should move the price further. An additive level term cannot express that.
    frame["wind_x_fill"] = frame["wind_dev"] * frame["fill_dev"]
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
def evaluate_band(frame: pd.DataFrame, level: str) -> dict:
    """Does the published p10-p90 band actually contain 80 % of outcomes?

    Two bands are scored. The shipped one widens with the weather scale and
    ignores the horizon entirely, so a seven-day-ahead hour gets the same band
    as an hour tonight. The fitted one takes the empirical 10th and 90th
    percentile of the scale-normalised residual, per forecast day, estimated on
    earlier quarters only.
    """
    from ..models.seasonal_naive import MIN_BAND_BASE
    from ..models.weather_scaled import BASE_WIDTH, HIGH_WIDTH_FACTOR, WIDTH_PER_SCALE

    quarters = sorted(frame["quarter"].unique())
    min_train = max(2000, len(frame) // 20)
    rows: list[pd.DataFrame] = []
    fitted_q: dict[int, tuple[float, float]] = {}

    for q in quarters:
        train = frame[frame["quarter"] < q]
        test = frame[frame["quarter"] == q]
        if len(train) < min_train or test.empty:
            continue

        for part, source in (("train", train), ("test", test)):
            if part == "train":
                continue
        # --- shipped band
        p50 = test[level].to_numpy()
        scale = test["guessed_scale"].to_numpy()
        base = np.maximum(np.abs(p50), MIN_BAND_BASE)
        width = BASE_WIDTH + WIDTH_PER_SCALE * np.abs(scale - 1.0)
        shipped_lo = p50 - width * base
        shipped_hi = p50 + width * HIGH_WIDTH_FACTOR * base

        # --- fitted band: residual quantiles per forecast day
        train_p50 = train[level].to_numpy()
        train_base = np.maximum(np.abs(train_p50), MIN_BAND_BASE)
        train_resid = (train["truth"].to_numpy() - train_p50) / train_base
        fit_lo = np.zeros_like(p50)
        fit_hi = np.zeros_like(p50)
        for day in sorted(frame["bucket"].unique()):
            tr = train["bucket"].to_numpy() == day
            te = test["bucket"].to_numpy() == day
            if tr.sum() < 200 or not te.any():
                continue
            q10 = float(np.quantile(train_resid[tr], 0.10))
            q90 = float(np.quantile(train_resid[tr], 0.90))
            fitted_q[int(day)] = (q10, q90)
            fit_lo[te] = p50[te] + q10 * base[te]
            fit_hi[te] = p50[te] + q90 * base[te]

        truth = test["truth"].to_numpy()
        rows.append(
            pd.DataFrame(
                {
                    "bucket": test["bucket"].to_numpy(),
                    "shipped_in": (shipped_lo <= truth) & (truth <= shipped_hi),
                    "fitted_in": (fit_lo <= truth) & (truth <= fit_hi),
                    "shipped_width": shipped_hi - shipped_lo,
                    "fitted_width": fit_hi - fit_lo,
                }
            )
        )

    if not rows:
        return {}
    everything = pd.concat(rows, ignore_index=True)
    return {
        "shipped_coverage": float(everything["shipped_in"].mean()),
        "fitted_coverage": float(everything["fitted_in"].mean()),
        "shipped_width": float(everything["shipped_width"].mean()),
        "fitted_width": float(everything["fitted_width"].mean()),
        "by_day": {
            int(d): (float(g["shipped_in"].mean()), float(g["fitted_in"].mean()))
            for d, g in everything.groupby("bucket")
        },
        "quantiles": fitted_q,
    }


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
    # Unplanned outages only: a planned revision is priced in weeks ahead, a trip
    # is not, and pooling them buries the rare informative events.
    ("+ unplanned production", "level_weather_guessed", ["unplanned_production_mw"]),
    ("+ unplanned nuclear", "level_weather_guessed", ["unplanned_nuclear_mw"]),
    ("+ unplanned import lost", "level_weather_guessed", ["unplanned_import_lost_mw"]),
    ("+ d unplanned", "level_weather_guessed", ["d_unplanned_mw"]),
    # Reservoirs, additively and as an interaction with wind.
    ("+ reservoir level", "level_weather_guessed", ["fill_dev"]),
    ("+ wind x reservoir", "level_weather_guessed", ["wind_x_fill"]),
    ("+ reservoir + interaction", "level_weather_guessed", ["fill_dev", "wind_x_fill"]),
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

    band = evaluate_band(frame, "level_weather_guessed")
    if band:
        print(f"\n--- osäkerhetsband kring standardmodellen, mål 80 % ---")
        print(f"{'dag':<6}{'skeppat':>10}{'anpassat':>10}")
        for day, (shipped, fit) in sorted(band["by_day"].items()):
            print(f"d{day + 1:<5}{100 * shipped:>9.1f}%{100 * fit:>9.1f}%")
        print(f"{'ALLA':<6}{100 * band['shipped_coverage']:>9.1f}%{100 * band['fitted_coverage']:>9.1f}%")
        print(f"medelbredd EUR/MWh: skeppat {band['shipped_width']:.1f}, anpassat {band['fitted_width']:.1f}")
        print("anpassade residualkvantiler per dag:")
        for day, (lo, hi) in sorted(band["quantiles"].items()):
            print(f"  d{day + 1}: q10={lo:+.3f}  q90={hi:+.3f}")

    best = min(results.items(), key=lambda kv: kv[1]["mae"])
    print(f"\nbest: {best[0]}")
    print(f"{'day':<6}" + "".join(f"{f'd{b+1}':>9}" for b in sorted(best[1]['buckets'])))
    print(f"{'MAE':<6}" + "".join(f"{v:>9.2f}" for _, v in sorted(best[1]["buckets"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
