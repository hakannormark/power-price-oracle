"""Fit the diurnal shape the level cannot see, and score it out of sample.

    python -m src.research.profile_fit --cache /tmp/profile.pkl

Every model on the site takes its level from last week's same hour, damped
toward a four-week median. That level already carries a shape across the day —
last week's shape — and nothing in the model ever stretches or flattens it when
this week's weather says it should be different. The consequence showed up the
moment there was enough live data to look at:

    bias per hour, shrunk_scaled, 5-12 September 2026, all four zones
    00-05   -1.1      09-16  -20.5
    06-08   -4.5      17-21  -22.8      22-23  -3.9

Unbiased overnight and twenty euros too low all through the day, which is not
noise: the forecast produced 95 EUR/MWh of daily swing against 119 actually
delivered, four fifths of the real amplitude. The hand-set weather scale cannot
fix it, because it multiplies the whole day by one number — a windy day is
cheaper at 03 and at 19 by the same proportion, when in truth wind collapses the
evening peak far more than it moves the night.

So the term fitted here is not a level adjustment but a shape adjustment: each
driver is allowed a different effect at each time of day. Two bases are
compared, because the question is how much freedom the shape needs:

    hourly     24 indicators per driver — every hour free, 192 terms a zone
    harmonic   a constant plus two Fourier pairs — 35 terms a zone

The harmonic basis cannot fit a single odd hour, which is the point: a daily
cycle is smooth, and 192 free numbers per zone on four years of data will fit
the sampling noise as happily as the cycle.

Scored exactly as src/research/backtest.py scores everything else — walk-forward
by quarter, every coefficient estimated on quarters strictly earlier than the
one it is applied to, and the auction cutoff applied so no point is credited for
reciting a published price.
"""

from __future__ import annotations

import argparse
import logging
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import build_samples, load_weather_archive, to_frame

log = logging.getLogger("profile-fit")

# Every driver the archive can supply, centred so that zero means "normal".
DRIVERS = [
    "wind_dev",
    "wind_north_dev",
    "wind_south_dev",
    "temp_dev",
    "solar_index_local",
    "wind_de_dev",
    "solar_de",
]

# Ridge penalties to show side by side. The shape terms are strongly correlated
# with each other, so the result should be reported across a range rather than
# at one flattering value.
PENALTIES = (20.0, 100.0, 400.0)


# ------------------------------------------------------------------ the frame


def weather_covered(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop hours the ERA5 archive does not actually cover.

    build_samples defaults a missing wind index to 1.0 and a missing solar index
    to 0.0, which is right for a forecast and wrong for a fit: those hours would
    teach the model that normal weather prevailed when the truth is that nobody
    looked. The archive ends several days behind real time, so without this the
    most recent samples all carry invented weather.
    """
    weather = load_weather_archive()
    if not weather:
        log.warning("no weather archive — nothing to fit")
        return frame.iloc[0:0]

    german = weather.get("DE_NORTH", {})
    keep = np.fromiter(
        (
            ts.to_pydatetime() in weather.get(zone, {}) and ts.to_pydatetime() in german
            for zone, ts in zip(frame["zone"], frame["ts"])
        ),
        dtype=bool,
        count=len(frame),
    )
    log.info("weather-covered hours: %s of %s", int(keep.sum()), len(frame))
    return frame[keep]


def build_frame(days: int | None, issue_every: int, cache: Path | None) -> pd.DataFrame:
    if cache and cache.exists():
        log.info("reading cached frame from %s", cache)
        return pickle.loads(cache.read_bytes())
    # Outages are irrelevant to a diurnal shape and cost minutes to assemble.
    frame = to_frame(build_samples(days, issue_every, with_outages=False))
    frame = weather_covered(frame)
    if cache:
        cache.write_bytes(pickle.dumps(frame))
        log.info("cached %s rows to %s", len(frame), cache)
    return frame


# ------------------------------------------------------------------ the basis


def shape_basis(hours: np.ndarray, kind: str) -> tuple[np.ndarray, list[str]]:
    """How a driver's effect is allowed to vary across the 24 hours."""
    if kind == "hourly":
        return np.eye(24)[hours], [f"h{h:02d}" for h in range(24)]

    angles = 2 * np.pi * hours / 24.0
    terms = [np.ones_like(angles, dtype="float64")]
    names = ["const"]
    for harmonic in (1, 2):
        terms += [np.sin(harmonic * angles), np.cos(harmonic * angles)]
        names += [f"sin{harmonic}", f"cos{harmonic}"]
    return np.column_stack(terms), names


def design(frame: pd.DataFrame, kind: str) -> tuple[np.ndarray, list[str]]:
    """Shape terms, every driver crossed with them, and a weekday offset."""
    hours = frame["hour"].to_numpy().astype(int)
    basis, basis_names = shape_basis(hours, kind)

    blocks = [basis]
    labels = [f"shape:{name}" for name in basis_names]
    for driver in DRIVERS:
        values = frame[driver].to_numpy(dtype="float64")[:, None]
        blocks.append(basis * values)
        labels += [f"{driver}:{name}" for name in basis_names]

    blocks.append(np.eye(7)[frame["dow"].to_numpy().astype(int)])
    labels += [f"dow{index}" for index in range(7)]
    return np.hstack(blocks), labels


# ------------------------------------------------------------------ the fit


def fit_ridge(matrix: np.ndarray, target: np.ndarray, penalty: float) -> dict:
    """Standardised ridge. Returns everything needed to apply it elsewhere."""
    mean = matrix.mean(axis=0)
    spread = matrix.std(axis=0)
    spread[spread == 0] = 1.0
    scaled = (matrix - mean) / spread
    intercept = float(target.mean())
    normal = scaled.T @ scaled + penalty * np.eye(scaled.shape[1])
    coefficients = np.linalg.solve(normal, scaled.T @ (target - intercept))
    return {"mean": mean, "spread": spread, "intercept": intercept, "coef": coefficients}


def apply_ridge(fit: dict, matrix: np.ndarray) -> np.ndarray:
    return ((matrix - fit["mean"]) / fit["spread"]) @ fit["coef"] + fit["intercept"]


def walk_forward(
    frame: pd.DataFrame,
    level: str,
    kind: str,
    penalty: float,
    per_zone: bool = True,
) -> dict:
    """Out-of-sample MAE, refitting the shape on earlier quarters at each step."""
    quarters = sorted(frame["quarter"].unique())
    min_train = max(2000, len(frame) // 20)
    zones = sorted(frame["zone"].unique()) if per_zone else [None]
    scored: list[pd.DataFrame] = []

    for quarter in quarters:
        train_mask = (frame["quarter"] < quarter).to_numpy()
        test_mask = (frame["quarter"] == quarter).to_numpy()
        if train_mask.sum() < min_train or not test_mask.any():
            continue

        train, test = frame[train_mask], frame[test_mask]
        prediction = test[level].to_numpy(dtype="float64").copy()

        for zone in zones:
            in_train = (
                np.ones(len(train), dtype=bool) if zone is None
                else (train["zone"].to_numpy() == zone)
            )
            in_test = (
                np.ones(len(test), dtype=bool) if zone is None
                else (test["zone"].to_numpy() == zone)
            )
            if in_train.sum() < 500 or not in_test.any():
                continue

            train_part = train[in_train]
            matrix, _ = design(train_part, kind)
            residual = (
                train_part["truth"].to_numpy(dtype="float64")
                - train_part[level].to_numpy(dtype="float64")
            )
            fit = fit_ridge(matrix, residual, penalty)

            test_matrix, _ = design(test[in_test], kind)
            prediction[in_test] += apply_ridge(fit, test_matrix)

        scored.append(
            pd.DataFrame(
                {
                    "error": np.abs(prediction - test["truth"].to_numpy(dtype="float64")),
                    "signed": prediction - test["truth"].to_numpy(dtype="float64"),
                    "bucket": test["bucket"].to_numpy(),
                    "zone": test["zone"].to_numpy(),
                    "hour": test["hour"].to_numpy(),
                }
            )
        )

    if not scored:
        return {"mae": float("nan"), "n": 0, "zones": {}, "buckets": {}, "hours": {}}

    everything = pd.concat(scored, ignore_index=True)
    return {
        "mae": float(everything["error"].mean()),
        "bias": float(everything["signed"].mean()),
        "n": int(len(everything)),
        "zones": {str(z): float(v) for z, v in everything.groupby("zone")["error"].mean().items()},
        "buckets": {int(b): float(v) for b, v in everything.groupby("bucket")["error"].mean().items()},
        "hours": {int(h): float(v) for h, v in everything.groupby("hour")["signed"].mean().items()},
    }


def baseline(frame: pd.DataFrame, level: str) -> dict:
    """The same measurement with no shape term, for an honest comparison."""
    quarters = sorted(frame["quarter"].unique())
    min_train = max(2000, len(frame) // 20)
    scored = []
    for quarter in quarters:
        train_mask = (frame["quarter"] < quarter).to_numpy()
        test_mask = (frame["quarter"] == quarter).to_numpy()
        if train_mask.sum() < min_train or not test_mask.any():
            continue
        test = frame[test_mask]
        error = np.abs(test[level].to_numpy(dtype="float64") - test["truth"].to_numpy(dtype="float64"))
        signed = test[level].to_numpy(dtype="float64") - test["truth"].to_numpy(dtype="float64")
        scored.append(
            pd.DataFrame({"error": error, "signed": signed, "bucket": test["bucket"].to_numpy(),
                          "zone": test["zone"].to_numpy(), "hour": test["hour"].to_numpy()})
        )
    everything = pd.concat(scored, ignore_index=True)
    return {
        "mae": float(everything["error"].mean()),
        "bias": float(everything["signed"].mean()),
        "n": int(len(everything)),
        "zones": {str(z): float(v) for z, v in everything.groupby("zone")["error"].mean().items()},
        "buckets": {int(b): float(v) for b, v in everything.groupby("bucket")["error"].mean().items()},
        "hours": {int(h): float(v) for h, v in everything.groupby("hour")["signed"].mean().items()},
    }


def emit_coefficients(frame: pd.DataFrame, kind: str, penalty: float, level: str) -> dict:
    """Fit on everything, for shipping. Reported separately from any score."""
    out: dict[str, dict[str, float]] = {}
    for zone in sorted(frame["zone"].unique()):
        part = frame[frame["zone"] == zone]
        matrix, labels = design(part, kind)
        residual = (
            part["truth"].to_numpy(dtype="float64") - part[level].to_numpy(dtype="float64")
        )
        fit = fit_ridge(matrix, residual, penalty)
        # Fold the standardisation into the coefficients so the shipped model is
        # a plain dot product and needs no stored means.
        weights = fit["coef"] / fit["spread"]
        offset = fit["intercept"] - float(fit["mean"] @ weights)
        out[str(zone)] = {"_offset": round(offset, 6)} | {
            label: round(float(value), 6) for label, value in zip(labels, weights)
        }
    return out


# ------------------------------------------------------------------ reporting


def report(name: str, result: dict, reference: float | None) -> None:
    gain = "" if reference is None else f"{100 * (1 - result['mae'] / reference):>8.1f}%"
    zones = "  ".join(f"{z} {v:5.2f}" for z, v in sorted(result["zones"].items()))
    print(f"{name:<34}{result['mae']:>8.2f}{gain}   {zones}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fit and score the diurnal shape term")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--issue-every", type=int, default=2)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--level", default="level_shrunk")
    parser.add_argument("--emit", action="store_true", help="print coefficients fitted on everything")
    parser.add_argument("--emit-kind", default="harmonic")
    parser.add_argument("--emit-penalty", type=float, default=100.0)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    frame = build_frame(args.days, args.issue_every, args.cache)
    if frame.empty:
        print("No samples — is data/actuals and data/weather/archive populated?")
        return 1

    print(
        f"\n{len(frame):,} scored hours, {frame['issued'].min():%Y-%m-%d} to "
        f"{frame['issued'].max():%Y-%m-%d}, {frame['quarter'].nunique()} quarters"
    )
    print(f"{'candidate':<34}{'MAE':>8}{'vs level':>9}   per zone")

    naive = baseline(frame, "level_naive")
    report("seasonal_naive", naive, None)
    base = baseline(frame, args.level)
    report(f"{args.level} (no shape term)", base, None)
    shipped = baseline(frame, "level_weather_guessed")
    report("shrunk_scaled as shipped", shipped, base["mae"])

    best = (None, float("inf"), None)
    for kind in ("harmonic", "hourly"):
        for penalty in PENALTIES:
            for per_zone in (False, True):
                scope = "per zone" if per_zone else "pooled"
                result = walk_forward(frame, args.level, kind, penalty, per_zone)
                report(f"+ shape {kind}, λ={penalty:g}, {scope}", result, base["mae"])
                if result["mae"] < best[1]:
                    best = (f"{kind}, λ={penalty:g}, {scope}", result["mae"], result)

    print(f"\nbest: {best[0]}  MAE {best[1]:.2f}")
    winner = best[2]
    print(f"{'day':<10}" + "".join(f"{f'd{b + 1}':>8}" for b in sorted(winner["buckets"])))
    print(f"{'shape':<10}" + "".join(f"{v:>8.2f}" for _, v in sorted(winner["buckets"].items())))
    print(f"{'level':<10}" + "".join(f"{v:>8.2f}" for _, v in sorted(base["buckets"].items())))

    print("\nbias per hour — the amplitude problem, before and after")
    print(f"{'hour':<10}" + "".join(f"{h:>6}" for h in range(0, 24, 2)))
    print(f"{'level':<10}" + "".join(f"{base['hours'][h]:>6.1f}" for h in range(0, 24, 2)))
    print(f"{'shape':<10}" + "".join(f"{winner['hours'][h]:>6.1f}" for h in range(0, 24, 2)))

    if args.emit:
        import json

        coefficients = emit_coefficients(frame, args.emit_kind, args.emit_penalty, args.level)
        print(f"\n--- coefficients, {args.emit_kind}, λ={args.emit_penalty:g} ---")
        print(json.dumps(coefficients, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
