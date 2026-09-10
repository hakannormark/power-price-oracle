"""Walk-forward backtest of the monthly models, and the live forecast it trains.

    python -m src.longterm.backtest

Issues a forecast every Monday morning from January 2023 onward, for the next
three calendar months, using only what was known that Monday (see data.py).
Every fitted quantity — the damping weights, the ridge coefficients — is
refitted at each issue on samples whose target month had already ended.
Nothing a model is scored on was available to its fit.

The same machinery produces the live forecast: the current issue is one more
row, fitted on every sample whose outcome is now known.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ..config import LONGTERM_HORIZON_MONTHS, ZONES
from ..timeutil import TZ, now_local, to_local
from . import data as d
from .models import (
    BACKTESTED,
    DAMPED,
    FEATURES,
    FUNDAMENTAL,
    MIN_TRAIN_SAMPLES,
    PERSISTENCE,
    baseline_predictions,
    damped_prediction,
    feature_vector,
    fit_damping,
    fit_ridge,
    fundamental_prediction,
    number,
)

log = logging.getLogger(__name__)

FIRST_ISSUE = date(2023, 1, 2)
HORIZONS = tuple(range(1, LONGTERM_HORIZON_MONTHS + 1))
ISSUE_HOUR = 6
YEAR_DAYS = 365


def issue_dates(first: date, until: datetime) -> list[datetime]:
    """Every Monday at 06:00 local from `first` up to `until`."""
    day = first + timedelta(days=(7 - first.weekday()) % 7)
    out = []
    while True:
        moment = datetime(day.year, day.month, day.day, ISSUE_HOUR, tzinfo=TZ)
        if moment > until:
            return out
        out.append(moment)
        day += timedelta(days=7)


class Context:
    """Everything the models read, prepared once and sliced per issue."""

    def __init__(self, actuals, reservoirs, umm_rows, fuel_rows):
        self.daily = d.daily_prices(actuals)
        self.monthly = d.monthly_prices(self.daily)
        self.national = d.national_reservoirs(reservoirs)
        self.nuclear = d.nuclear_rows(umm_rows)
        self.fuels = d.fuel_series(fuel_rows)

    def samples(self, issue: datetime) -> list[dict]:
        """One row per zone and horizon, with features as known at `issue`."""
        clim = d.climatology(self.monthly, issue)
        recent = d.recent_mean(self.daily, issue)
        year = d.recent_mean(self.daily, issue, days=YEAR_DAYS)
        hydro = d.hydro_anomaly(self.national, issue)
        intervals = d.nuclear_intervals(self.nuclear, issue)
        out_recent = d.nuclear_out_mw(intervals, issue - timedelta(days=d.RECENT_DAYS), issue)
        ttf = d.fuel_close(self.fuels, "ttf", issue)
        eua = d.fuel_close(self.fuels, "eua", issue)
        gas = d.gas_plant_cost(ttf, eua)

        rows = []
        for horizon, target in zip(HORIZONS, d.target_months(issue, HORIZONS)):
            out_target = d.nuclear_out_mw(intervals, d.month_start(target), d.month_end(target))
            for zone in ZONES:
                actual = None
                if target in self.monthly.index and zone in self.monthly:
                    value = self.monthly.at[target, zone]
                    actual = None if pd.isna(value) else float(value)
                level = recent[zone]
                rows.append(
                    {
                        "issue": issue,
                        "zone": zone,
                        "horizon": horizon,
                        "target": str(target),
                        "target_end": d.month_end(target),
                        "recent": level,
                        "year_mean": year[zone],
                        "actual": actual,
                        "seasonal_diff": d.seasonal_diff(clim, issue, target, zone),
                        "hydro_anomaly": hydro,
                        "nuclear_out_mw": out_target,
                        "nuclear_recent_mw": out_recent,
                        "nuclear_delta_gw": (out_target - out_recent) / 1000.0,
                        "ttf": ttf,
                        "eua": eua,
                        "gas_cost": gas,
                        "gas_gap": (gas - level) if gas is not None and level is not None else None,
                    }
                )
        return rows


def _fit_ridge(train: list[dict]):
    rows = [r for r in train if number(r.get("recent")) is not None and number(r.get("actual")) is not None]
    if len(rows) < MIN_TRAIN_SAMPLES:
        return None
    x = np.vstack([feature_vector(r) for r in rows])
    y = np.array([r["actual"] - r["recent"] for r in rows])
    return fit_ridge(x, y)


def _predict(rows: list[dict], known: list[dict]) -> None:
    """Fill every model's prediction into `rows`, fitted only on `known`."""
    weights = fit_damping(known)
    for zone in ZONES:
        ridge = _fit_ridge([r for r in known if r["zone"] == zone])
        for row in (r for r in rows if r["zone"] == zone):
            row.update(baseline_predictions(row))
            row[DAMPED] = damped_prediction(row, weights)
            row[FUNDAMENTAL] = fundamental_prediction(ridge, row)
            row["damping"] = weights
            row["train_n"] = ridge.n if ridge else 0
            if ridge is not None:
                row["coefficients"] = dict(zip(FEATURES, (round(float(b), 3) for b in ridge.beta)))


def walk_forward(context: Context, issues: list[datetime]) -> pd.DataFrame:
    """Predictions for every issue, each fitted only on outcomes known by then."""
    history: list[dict] = []
    for issue in issues:
        rows = context.samples(issue)
        _predict(rows, [r for r in history if r["target_end"] <= issue])
        history.extend(rows)
    return pd.DataFrame(history)


def summarize(frame: pd.DataFrame) -> dict:
    """MAE per zone, model and horizon on rows every model predicted."""
    out: dict = {"window": None, "zones": {}}
    if frame.empty:
        return out
    done = frame.dropna(subset=["actual", *BACKTESTED])
    if done.empty:
        return out
    out["window"] = {
        "first_issue": to_local(done["issue"].min()).date().isoformat(),
        "last_issue": to_local(done["issue"].max()).date().isoformat(),
        "issues": int(done["issue"].nunique()),
    }
    for zone in [*ZONES, "ALL"]:
        subset = done if zone == "ALL" else done[done["zone"] == zone]
        out["zones"][zone] = {
            model: {
                str(h): {
                    "mae": round(float((g[model] - g["actual"]).abs().mean()), 2),
                    "bias": round(float((g[model] - g["actual"]).mean()), 2),
                    "n": int(len(g)),
                }
                for h, g in subset.groupby("horizon")
            }
            for model in BACKTESTED
        }
    return out


def residual_band(frame: pd.DataFrame, model: str) -> dict[str, dict[str, tuple[float, float]]]:
    """10th and 90th percentile of (actual - prediction) per zone and horizon."""
    band: dict[str, dict[str, tuple[float, float]]] = {}
    if frame.empty:
        return band
    done = frame.dropna(subset=["actual", model])
    for (zone, horizon), group in done.groupby(["zone", "horizon"]):
        error = group["actual"] - group[model]
        band.setdefault(zone, {})[str(horizon)] = (
            round(float(error.quantile(0.10)), 2),
            round(float(error.quantile(0.90)), 2),
        )
    return band


def choose_default(summary: dict) -> str:
    """The backtested model with the lowest mean MAE over zones and horizons.

    The persistence reference is kept unless a challenger beats it by 3 % —
    a smaller gap on this little data is noise, not skill.
    """
    cells = summary.get("zones", {}).get("ALL")
    if not cells:
        return PERSISTENCE
    mean_mae = {model: float(np.mean([c["mae"] for c in cells[model].values()])) for model in BACKTESTED}
    best = min(mean_mae, key=mean_mae.get)
    if best != PERSISTENCE and mean_mae[best] > mean_mae[PERSISTENCE] * 0.97:
        return PERSISTENCE
    return best


def run(actuals, reservoirs, umm_rows, fuel_rows, now: datetime | None = None) -> dict:
    """Backtest up to now, then the live issue: predictions, band and summary."""
    now = now or now_local()
    context = Context(actuals, reservoirs, umm_rows, fuel_rows)
    frame = walk_forward(context, issue_dates(FIRST_ISSUE, now))
    summary = summarize(frame)

    live_rows = context.samples(now)
    known = [r for r in frame.to_dict("records") if r["target_end"] <= now] if not frame.empty else []
    _predict(live_rows, known)

    return {
        "frame": frame,
        "summary": summary,
        "default_model": choose_default(summary),
        "band": {model: residual_band(frame, model) for model in BACKTESTED},
        "live": live_rows,
        "context": context,
    }


def main() -> int:
    from ..fetch.fuels import load_fuels
    from ..store import load_actuals, load_reservoirs, load_umm

    logging.basicConfig(level=logging.WARNING)
    result = run(load_actuals(), load_reservoirs(), load_umm(), load_fuels())
    summary = result["summary"]
    print(f"window: {summary['window']}")
    for zone, per_model in summary["zones"].items():
        print(f"\n{zone}  MAE EUR/MWh by months ahead")
        for model, cells in per_model.items():
            line = "  ".join(
                f"h{h}: {c['mae']:6.2f} (bias {c['bias']:+6.2f}, n={c['n']})" for h, c in cells.items()
            )
            print(f"  {model:<16} {line}")
    print(f"\ndefault: {result['default_model']}")
    for row in result["live"]:
        if row["zone"] == "SE3":
            print({k: row.get(k) for k in ("target", "recent", "year_mean", PERSISTENCE, DAMPED, FUNDAMENTAL, "damping", "coefficients")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
