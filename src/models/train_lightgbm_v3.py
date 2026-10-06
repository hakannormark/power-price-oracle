"""Train lightgbm_v3: quantile residuals on the weather a forecast really has.

    python -m src.fetch.previous_runs          # the training weather, once
    python -m src.models.train_lightgbm_v3     # walk-forward test, then the artifacts

What differs from lightgbm_v2, and why:

* Weather is the archived forecast of the right age (see fetch/previous_runs.py),
  not ERA5. v2 was trained on the weather that happened and then served a
  forecast; on day 7 that is a different input, and it had never seen it.
* Lead is a real variable. Every hour is a sample at each lead from one to seven
  days, reading only the prices published by then. v2 had two leads, 12 h and
  96 h, and was served 0-168.
* It is tested before it is trusted. The walk-forward below refits on everything
  before a month and scores that month, six times over, and writes the result to
  meta_v3.json next to the model. v2 had no validation at all.
* It says when it was trained and on what, and it is retrained every week.
* Raw temperature, wind and radiation instead of indices against a climatology
  that training and serving computed differently.
* German and Danish weather as their own columns, gas and carbon closes, the
  spread between zones on the last known day; the zone as a category.

The walk-forward also trains the same model on the newest forecast (age 0, the
closest thing to "the weather that happened") and scores it on forecasts of the
right age. That is v2's situation, and the difference between the two rows is
what training on honest weather is worth.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..features.build import _swedish_holidays
from ..fetch import fuels as fuels_store
from ..fetch import previous_runs
from ..store import load_actuals
from ..timeutil import TZ, iso, parse_iso
from . import lgbm3_features as F

log = logging.getLogger("train_lightgbm_v3")

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
QUANTILES = (0.10, 0.50, 0.90)
FOLDS = 6
FOLD_DAYS = 30
HALF_LIFE_DAYS = 365.0   # a year-old hour counts half as much as yesterday's
MAX_ROUNDS = 600
PARAMS = {
    "objective": "quantile", "metric": "quantile", "boosting_type": "gbdt",
    "num_leaves": 63, "learning_rate": 0.05, "min_child_samples": 100,
    "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8,
    "lambda_l2": 1.0, "seed": 42, "verbose": -1, "num_threads": 0,
}


def load_arrays() -> dict:
    """Prices, weather and fuels on one hourly grid."""
    actuals = load_actuals()
    weather_raw = {p: previous_runs.load_point(p) for p in F.POINTS}
    missing = [p for p, v in weather_raw.items() if v is None]
    if missing:
        raise RuntimeError(f"No previous-runs weather for {missing}; run python -m src.fetch.previous_runs")

    first_weather = min(int(v["hours"][0]) for v in weather_raw.values())
    hour0 = first_weather - 24 * 35            # room for the four-week lags
    last_price = max(F.hour_index(parse_iso(r["ts"])) for r in actuals[-20000:])
    n = last_price - hour0 + 1

    prices = {z: np.full(n, np.nan) for z in F.ZONE_ORDER}
    for row in actuals:
        zone, price = row.get("zone"), row.get("price_eur_mwh")
        if zone not in prices or price is None:
            continue
        i = F.hour_index(parse_iso(row["ts"])) - hour0
        if 0 <= i < n:
            prices[zone][i] = float(price)

    weather: dict[str, dict[str, np.ndarray]] = {}
    for point, raw in weather_raw.items():
        idx = raw["hours"] - hour0
        ok = (idx >= 0) & (idx < n)
        weather[point] = {}
        for var in ("temp", "wind", "solar"):
            arr = np.full((n, raw[var].shape[1]), np.nan)
            arr[idx[ok]] = raw[var][ok]
            weather[point][var] = arr

    years = range(datetime.fromtimestamp(hour0 * 3600, timezone.utc).year, datetime.now().year + 2)
    return {
        "hour0": hour0, "n": n, "prices": prices, "weather": weather,
        "fuels": F.fuel_arrays(fuels_store.load_fuels(), hour0, n),
        "holidays": _swedish_holidays(list(years)),
        "first_target": first_weather - hour0 + 24 * F.MAX_LEAD,
    }


def build_samples(data: dict, perfect_weather: bool = False) -> pd.DataFrame:
    """Every priced hour at every lead. `perfect_weather` reads the newest forecast instead."""
    frames = []
    stats = {z: F.daily_stats(data["prices"][z], data["hour0"]) for z in F.ZONE_ORDER}
    for zone in F.ZONE_ORDER:
        own = data["prices"][zone]
        hours = np.arange(data["first_target"], data["n"])
        hours = hours[~np.isnan(own[hours])]
        for lead in range(1, F.MAX_LEAD + 1):
            leads = np.full(len(hours), lead)
            age = np.zeros(len(hours), dtype=int) if perfect_weather else leads
            x, shrunk = F.build_matrix(
                zone, hours, leads, data["prices"], data["weather"], data["fuels"],
                data["hour0"], data["holidays"], age, stats,
            )
            frame = pd.DataFrame(x, columns=F.FEATURE_NAMES)
            frame["y"] = own[hours] - shrunk
            frame["price"] = own[hours]
            frame["target"] = hours
            frames.append(frame)
    samples = pd.concat(frames, ignore_index=True)
    samples = samples[samples["y"].notna() & samples["p_last"].notna() & samples["temp"].notna()]
    samples["zone"] = samples["zone"].astype(int)
    return samples.reset_index(drop=True)


def _weights(target: np.ndarray, newest: int) -> np.ndarray:
    return np.power(0.5, (newest - target) / (24.0 * HALF_LIFE_DAYS))


def fit(train: pd.DataFrame, alpha: float, rounds: int, valid: pd.DataFrame | None = None):
    import lightgbm as lgb

    dtrain = lgb.Dataset(
        train[F.FEATURE_NAMES], label=train["y"], weight=_weights(train["target"].to_numpy(), int(train["target"].max())),
        categorical_feature=F.CATEGORICAL, free_raw_data=False,
    )
    params = dict(PARAMS, alpha=alpha)
    if valid is None:
        return lgb.train(params, dtrain, num_boost_round=rounds)
    dvalid = lgb.Dataset(valid[F.FEATURE_NAMES], label=valid["y"], reference=dtrain, categorical_feature=F.CATEGORICAL)
    return lgb.train(
        params, dtrain, num_boost_round=rounds, valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(40, verbose=False)],
    )


def walk_forward(honest: pd.DataFrame, perfect: pd.DataFrame) -> dict:
    """Refit before each of the last FOLDS months and score that month, by lead.

    A week is left between the training data and the scored month, so that no
    training target shares a delivery day with a feature of a scored sample.
    """
    newest = int(honest["target"].max())
    rows, best_rounds = [], []
    for fold in range(FOLDS, 0, -1):
        test_from = newest - 24 * FOLD_DAYS * fold
        test_to = test_from + 24 * FOLD_DAYS
        train_to = test_from - 24 * 7
        test = honest[(honest["target"] >= test_from) & (honest["target"] < test_to)]
        if test.empty:
            continue
        # Early stopping is tuned on the month before the gap, never on the scored one.
        tune = honest[(honest["target"] >= train_to - 24 * FOLD_DAYS) & (honest["target"] < train_to)]
        core = honest[honest["target"] < train_to - 24 * FOLD_DAYS]
        probe = fit(core, 0.5, MAX_ROUNDS, tune)
        rounds = max(60, int(probe.best_iteration or MAX_ROUNDS))
        best_rounds.append(rounds)

        model = fit(honest[honest["target"] < train_to], 0.5, rounds)
        skewed = fit(perfect[perfect["target"] < train_to], 0.5, rounds)
        q10 = fit(honest[honest["target"] < train_to], 0.1, rounds)
        q90 = fit(honest[honest["target"] < train_to], 0.9, rounds)

        x = test[F.FEATURE_NAMES]
        scored = test[["lead", "zone", "price", "shrunk", "p_lag168", "p_last"]].copy()
        scored["v3"] = scored["shrunk"] + model.predict(x)
        scored["v3_trained_on_newest_weather"] = scored["shrunk"] + skewed.predict(x)
        scored["lo"] = scored["shrunk"] + q10.predict(x)
        scored["hi"] = scored["shrunk"] + q90.predict(x)
        scored["fold"] = fold
        rows.append(scored)
        log.info("fold %s: %s scored samples, %s rounds", fold, len(scored), rounds)

    scored = pd.concat(rows, ignore_index=True)
    err = lambda col: (scored[col] - scored["price"]).abs()  # noqa: E731
    scored = scored.assign(
        e_v3=err("v3"), e_skew=err("v3_trained_on_newest_weather"), e_shrunk=err("shrunk"),
        e_naive=(scored["p_lag168"] - scored["price"]).abs(),
        inside=((scored["lo"] <= scored["price"]) & (scored["price"] <= scored["hi"])).astype(float),
        bias=scored["v3"] - scored["price"],
    )

    def table(group: pd.DataFrame) -> dict:
        return {
            "n": int(len(group)),
            "mae_v3": round(float(group["e_v3"].mean()), 2),
            "mae_trained_on_newest_weather": round(float(group["e_skew"].mean()), 2),
            "mae_shrunk_level": round(float(group["e_shrunk"].mean()), 2),
            "mae_seasonal_naive": round(float(group["e_naive"].mean()), 2),
            "bias_v3": round(float(group["bias"].mean()), 2),
            "coverage80": round(float(group["inside"].mean()), 3),
        }

    # Conformal offsets: how far each bound has to move for the band to have held
    # 80 % of these out-of-sample outcomes, per lead. Quantile trees fitted on
    # the past under-cover a future that has drifted; this is the measured gap.
    conformal = {}
    for lead_value, group in scored.groupby("lead"):
        conformal[str(int(lead_value))] = [
            round(float(np.quantile(group["lo"] - group["price"], 0.90)), 2),
            round(float(np.quantile(group["price"] - group["hi"], 0.90)), 2),
        ]
    widened = scored.assign(
        lo2=scored["lo"] - scored["lead"].map(lambda v: conformal[str(int(v))][0]),
        hi2=scored["hi"] + scored["lead"].map(lambda v: conformal[str(int(v))][1]),
    )
    coverage_after = float(((widened["lo2"] <= widened["price"]) & (widened["price"] <= widened["hi2"])).mean())

    return {
        "conformal_offsets": conformal,
        "coverage80_after_offsets_in_sample": round(coverage_after, 3),
        "folds": FOLDS, "fold_days": FOLD_DAYS, "rounds": int(np.median(best_rounds)),
        "by_lead": {str(int(k)): table(g) for k, g in scored.groupby("lead")},
        "by_zone": {F.ZONE_ORDER[int(k)]: table(g) for k, g in scored.groupby("zone")},
        "overall": table(scored),
        "note": (
            "Hindcast on archived forecasts, issued once a day before the auction. Live scoring "
            "decides; this only says the model is worth logging. mae_trained_on_newest_weather is "
            "the same model fitted on the newest forecast instead of the one of the right age: if "
            "it is not worse, training on honest weather has bought nothing in this test."
        ),
    }


def train_and_save() -> dict:
    data = load_arrays()
    honest = build_samples(data)
    perfect = build_samples(data, perfect_weather=True)
    log.info("samples: %s", len(honest))
    report = walk_forward(honest, perfect)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    for alpha in QUANTILES:
        booster = fit(honest, alpha, report["rounds"])
        booster.save_model(str(ARTIFACTS_DIR / f"lightgbm_v3_q{int(alpha * 100)}.txt"))

    stamp = lambda i: iso(datetime.fromtimestamp((data["hour0"] + int(i)) * 3600, timezone.utc).astimezone(TZ))  # noqa: E731
    meta = {
        "trained_at": iso(datetime.now(TZ)),
        "targets_from": stamp(honest["target"].min()),
        "targets_to": stamp(honest["target"].max()),
        "samples": int(len(honest)),
        "features": F.FEATURE_NAMES,
        "categorical": F.CATEGORICAL,
        "quantiles": list(QUANTILES),
        "target": "price - shrunk level",
        "weather": "Open-Meteo Previous Runs: the forecast made `lead` days before the hour",
        "half_life_days": HALF_LIFE_DAYS,
        "params": {k: v for k, v in PARAMS.items() if k not in ("verbose", "num_threads")},
        "walk_forward": report,
    }
    (ARTIFACTS_DIR / "meta_v3.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = train_and_save()
    print(json.dumps(result["walk_forward"], indent=1, ensure_ascii=False))
