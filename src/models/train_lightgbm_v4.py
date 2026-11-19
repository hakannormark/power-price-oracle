"""Train lightgbm_v4: level-blind quantile residuals, one model per horizon regime.

    python -m src.fetch.previous_runs               # the training weather, once
    python -m src.models.train_lightgbm_v4          # walk-forward test, then the artifacts
    python -m src.models.train_lightgbm_v4 --compare-v3   # also refit v3 on the same folds

What differs from lightgbm_v3, and why (see lgbm4_features.py for the first):

* The target and every price feature are divided by the week's level, and no
  raw price level is a feature. v2 and v3 learned that a high level reverts,
  and kept forecasting that through the autumn 2026 shortage in SE3 and SE4.
* Two models, not one. Lead 1 — the day after the last published day — is a
  different problem: yesterday's price at the same hour is the strongest input
  there is. Pooled with leads 2-7, v3 gave it 3.8 % of the gain and moved its
  lead-1 forecast barely more than its lead-5 one; live, v3 was 3-6 EUR/MWh
  worse than v2 on days 1-2. Here lead 1 is fitted on its own.
* Each quantile finds its own number of trees. In v3 the median's early stop
  set it for all three.
* The band is widened per zone and lead, in units of the scale, so it grows
  with the price level instead of adding the same EUR/MWh in SE1 and SE4.
* It trains on the newest weather forecast for every hour, and is scored on
  forecasts of the right age. v3 trains on the right age; in six weekly
  retrainings that was 0.5-1.0 EUR/MWh worse, and in v4's first walk-forward
  (2026-11-19) 28.79 against 28.33. The walk-forward keeps fitting both and
  reports the difference as mae_trained_on_newest_weather, so a reversal is
  seen; the choice is fixed rather than re-made weekly, so the model does not
  change kind from one week to the next.

First walk-forward, 2026-11-19, six folds to 2026-11-18, 120 960 samples, v3
refitted on the same folds and scored on the same samples:

    lead       1      2      3      4      5      6      7    all
    v4      23.2   26.5   27.7   29.3   30.3   31.8   32.6   28.8
    v3      28.7   29.8   30.6   31.9   32.5   33.8   34.8   31.7

Better at every lead and in every zone; SE4 over the last 60 days 59.4 against
69.0. Bias over all folds -7.4 EUR/MWh (SE4 -15.9). These figures use weather of
the right age for training, as v3 does; the shipped model's newest-weather
training scored 28.3.

The walk-forward is v3's: six 30-day folds, a week's gap, everything before
the gap used for training, issued once a day before the auction. It is a
hindcast on archived forecasts — live scoring decides.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..timeutil import TZ, iso
from . import lgbm3_features as F3
from . import lgbm4_features as F
from .train_lightgbm_v3 import FOLD_DAYS, FOLDS, HALF_LIFE_DAYS, MAX_ROUNDS, PARAMS, load_arrays

log = logging.getLogger("train_lightgbm_v4")

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
QUANTILES = (0.10, 0.50, 0.90)
REGIMES = {"short": lambda lead: lead == F.SHORT_LEAD, "long": lambda lead: lead > F.SHORT_LEAD}
MIN_ROUNDS = 60


def regime_of(lead) -> np.ndarray:
    return np.where(np.asarray(lead) == F.SHORT_LEAD, "short", "long")


def build_samples(data: dict, newest_weather: bool = False) -> pd.DataFrame:
    """Every priced hour at every lead, with the v4 features and the scaled target."""
    frames = []
    stats = {z: F3.daily_stats(data["prices"][z], data["hour0"]) for z in F3.ZONE_ORDER}
    for zone in F3.ZONE_ORDER:
        own = data["prices"][zone]
        hours = np.arange(data["first_target"], data["n"])
        hours = hours[~np.isnan(own[hours])]
        for lead in range(1, F3.MAX_LEAD + 1):
            leads = np.full(len(hours), lead)
            age = np.zeros(len(hours), dtype=int) if newest_weather else leads
            x, shrunk, scale = F.build_matrix(
                zone, hours, leads, data["prices"], data["weather"], data["fuels"],
                data["hour0"], data["holidays"], age, stats,
            )
            frame = pd.DataFrame(x, columns=F.FEATURE_NAMES)
            frame["shrunk"] = shrunk
            frame["scale"] = scale
            frame["price"] = own[hours]
            frame["y"] = (own[hours] - shrunk) / scale
            frame["target"] = hours
            frames.append(frame)
    samples = pd.concat(frames, ignore_index=True)
    samples = samples[samples["y"].notna() & samples["r_last"].notna() & samples["temp"].notna()]
    samples["zone"] = samples["zone"].astype(int)
    samples["lead"] = samples["lead"].astype(int)
    return samples.reset_index(drop=True)


def _weights(target: np.ndarray, newest: int) -> np.ndarray:
    return np.power(0.5, (newest - target) / (24.0 * HALF_LIFE_DAYS))


def fit(train: pd.DataFrame, alpha: float, rounds: int, valid: pd.DataFrame | None = None):
    import lightgbm as lgb

    dtrain = lgb.Dataset(
        train[F.FEATURE_NAMES], label=train["y"],
        weight=_weights(train["target"].to_numpy(), int(train["target"].max())),
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


def tuned_rounds(train: pd.DataFrame, alpha: float, tune_from: int) -> int:
    """Trees for one quantile, early-stopped on the last FOLD_DAYS of `train`."""
    core, tune = train[train["target"] < tune_from], train[train["target"] >= tune_from]
    if core.empty or tune.empty:
        return MAX_ROUNDS // 2
    probe = fit(core, alpha, MAX_ROUNDS, tune)
    return max(MIN_ROUNDS, int(probe.best_iteration or MAX_ROUNDS))


def fit_regimes(train: pd.DataFrame, newest: pd.DataFrame | None, train_to: int) -> dict:
    """Per regime: rounds per quantile, the three boosters, and optionally a median on newest weather."""
    tune_from = train_to - 24 * FOLD_DAYS
    out = {}
    for regime, pick in REGIMES.items():
        part = train[pick(train["lead"])]
        rounds = {alpha: tuned_rounds(part, alpha, tune_from) for alpha in QUANTILES}
        models = {alpha: fit(part, alpha, rounds[alpha]) for alpha in QUANTILES}
        entry = {"rounds": rounds, "models": models}
        if newest is not None:
            entry["newest"] = fit(newest[pick(newest["lead"])], 0.5, rounds[0.5])
        out[regime] = entry
    return out


def predict_scaled(fitted: dict, frame: pd.DataFrame, alpha: float, key: str = "models") -> np.ndarray:
    out = np.full(len(frame), np.nan)
    regimes = regime_of(frame["lead"])
    for regime, entry in fitted.items():
        mask = regimes == regime
        if mask.any():
            model = entry[key] if key == "newest" else entry[key][alpha]
            out[mask] = model.predict(frame.loc[mask, F.FEATURE_NAMES])
    return out


def _v3_samples(data: dict) -> pd.DataFrame:
    from .train_lightgbm_v3 import build_samples as v3_build

    return v3_build(data)


def walk_forward(honest: pd.DataFrame, newest: pd.DataFrame, v3: pd.DataFrame | None = None) -> dict:
    """Refit before each of the last FOLDS months and score that month."""
    last = int(honest["target"].max())
    rows, rounds_seen = [], []
    for fold in range(FOLDS, 0, -1):
        test_from = last - 24 * FOLD_DAYS * fold
        test_to = test_from + 24 * FOLD_DAYS
        train_to = test_from - 24 * 7
        test = honest[(honest["target"] >= test_from) & (honest["target"] < test_to)]
        if test.empty:
            continue
        fitted = fit_regimes(
            honest[honest["target"] < train_to], newest[newest["target"] < train_to], train_to
        )
        rounds_seen.append({r: {str(a): n for a, n in e["rounds"].items()} for r, e in fitted.items()})

        scored = test[["lead", "zone", "target", "price", "shrunk", "scale", "r_lag168"]].copy()
        s = scored["scale"].to_numpy()
        base = scored["shrunk"].to_numpy()
        scored["v4"] = base + s * predict_scaled(fitted, test, 0.5)
        scored["v4_newest"] = base + s * predict_scaled(fitted, test, 0.5, key="newest")
        scored["lo"] = base + s * predict_scaled(fitted, test, 0.1)
        scored["hi"] = base + s * predict_scaled(fitted, test, 0.9)
        scored["p_lag168"] = base + s * scored["r_lag168"]
        if v3 is not None:
            scored = _add_v3(scored, v3, train_to, test_from, test_to)
        scored["fold"] = fold
        rows.append(scored)
        log.info("fold %s: %s scored samples", fold, len(scored))

    scored = pd.concat(rows, ignore_index=True)
    return summarise(scored, rounds_seen)


def _add_v3(scored: pd.DataFrame, v3: pd.DataFrame, train_to: int, test_from: int, test_to: int) -> pd.DataFrame:
    """v3 refitted on the same fold, its median joined onto the same samples."""
    from .train_lightgbm_v3 import fit as v3_fit

    train = v3[v3["target"] < train_to]
    tune = train[train["target"] >= train_to - 24 * FOLD_DAYS]
    core = train[train["target"] < train_to - 24 * FOLD_DAYS]
    probe = v3_fit(core, 0.5, MAX_ROUNDS, tune)
    model = v3_fit(train, 0.5, max(MIN_ROUNDS, int(probe.best_iteration or MAX_ROUNDS)))
    test = v3[(v3["target"] >= test_from) & (v3["target"] < test_to)].copy()
    test["v3"] = test["shrunk"] + model.predict(test[F3.FEATURE_NAMES])
    test["lead"] = test["lead"].astype(int)
    keys = ["zone", "target", "lead"]
    return scored.merge(test[keys + ["v3"]], on=keys, how="left")


def summarise(scored: pd.DataFrame, rounds_seen: list) -> dict:
    err = lambda col: (scored[col] - scored["price"]).abs()  # noqa: E731
    scored = scored.assign(
        e_v4=err("v4"), e_newest=err("v4_newest"), e_shrunk=err("shrunk"), e_naive=err("p_lag168"),
        bias=scored["v4"] - scored["price"],
        inside=((scored["lo"] <= scored["price"]) & (scored["price"] <= scored["hi"])).astype(float),
    )
    has_v3 = "v3" in scored
    if has_v3:
        scored["e_v3"] = err("v3")

    def table(group: pd.DataFrame) -> dict:
        row = {
            "n": int(len(group)),
            "mae_v4": round(float(group["e_v4"].mean()), 2),
            "mae_trained_on_newest_weather": round(float(group["e_newest"].mean()), 2),
            "mae_shrunk_level": round(float(group["e_shrunk"].mean()), 2),
            "mae_seasonal_naive": round(float(group["e_naive"].mean()), 2),
            "bias_v4": round(float(group["bias"].mean()), 2),
            "coverage80": round(float(group["inside"].mean()), 3),
        }
        if has_v3:
            both = group[group["e_v3"].notna()]
            row["mae_v3_same_hours"] = round(float(both["e_v3"].mean()), 2) if len(both) else None
            row["mae_v4_same_hours"] = round(float(both["e_v4"].mean()), 2) if len(both) else None
        return row

    # Band widening per zone and lead, in units of the scale: how far each bound
    # must move for the band to have held 80 % of these out-of-sample outcomes.
    conformal: dict[str, dict[str, list[float]]] = {}
    for (zone, lead), group in scored.groupby(["zone", "lead"]):
        s = group["scale"]
        conformal.setdefault(F3.ZONE_ORDER[int(zone)], {})[str(int(lead))] = [
            round(float(np.quantile((group["lo"] - group["price"]) / s, 0.90)), 4),
            round(float(np.quantile((group["price"] - group["hi"]) / s, 0.90)), 4),
        ]
    lo_off = [conformal[F3.ZONE_ORDER[int(z)]][str(int(k))][0] for z, k in zip(scored["zone"], scored["lead"])]
    hi_off = [conformal[F3.ZONE_ORDER[int(z)]][str(int(k))][1] for z, k in zip(scored["zone"], scored["lead"])]
    lo2 = scored["lo"] - np.array(lo_off) * scored["scale"]
    hi2 = scored["hi"] + np.array(hi_off) * scored["scale"]
    widened = float(((lo2 <= scored["price"]) & (scored["price"] <= hi2)).mean())

    local = pd.to_datetime(scored["target"] * 3600, unit="s", utc=True).dt.tz_convert(TZ)
    recent = scored[local >= local.max() - pd.Timedelta(days=60)]

    newest_wins = float(scored["e_newest"].mean()) < float(scored["e_v4"].mean())
    return {
        "conformal_offsets_scaled": conformal,
        "coverage80_after_offsets_in_sample": round(widened, 3),
        "folds": FOLDS, "fold_days": FOLD_DAYS, "rounds": rounds_seen[-1] if rounds_seen else {},
        "by_lead": {str(int(k)): table(g) for k, g in scored.groupby("lead")},
        "by_zone": {F3.ZONE_ORDER[int(k)]: table(g) for k, g in scored.groupby("zone")},
        "by_zone_last_60_days": {F3.ZONE_ORDER[int(k)]: table(g) for k, g in recent.groupby("zone")},
        "overall": table(scored),
        "newest_weather_better": newest_wins,
        "note": (
            "Hindcast on archived forecasts, issued once a day before the auction. Live scoring "
            "decides. mae_trained_on_newest_weather is the same model fitted on the newest "
            "forecast and scored on forecasts of the right age. mae_v3_same_hours is v3 refitted "
            "on the same folds and scored on the same samples (only with --compare-v3)."
        ),
    }


TRAIN_ON_NEWEST_WEATHER = True


def train_and_save(compare_v3: bool = False, newest_weather: bool | None = TRAIN_ON_NEWEST_WEATHER) -> dict:
    """Walk-forward, then fit on everything. `newest_weather` None lets the test decide."""
    data = load_arrays()
    honest = build_samples(data)
    newest = build_samples(data, newest_weather=True)
    log.info("samples: %s", len(honest))
    report = walk_forward(honest, newest, _v3_samples(data) if compare_v3 else None)

    use_newest = report["newest_weather_better"] if newest_weather is None else newest_weather
    train = newest if use_newest else honest
    last = int(train["target"].max()) + 1
    fitted = fit_regimes(train, None, last)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    for regime, entry in fitted.items():
        for alpha, booster in entry["models"].items():
            booster.save_model(str(ARTIFACTS_DIR / f"lightgbm_v4_{regime}_q{int(alpha * 100)}.txt"))

    stamp = lambda i: iso(datetime.fromtimestamp((data["hour0"] + int(i)) * 3600, timezone.utc).astimezone(TZ))  # noqa: E731
    meta = {
        "trained_at": iso(datetime.now(TZ)),
        "targets_from": stamp(train["target"].min()),
        "targets_to": stamp(train["target"].max()),
        "samples": int(len(train)),
        "features": F.FEATURE_NAMES,
        "categorical": F.CATEGORICAL,
        "quantiles": list(QUANTILES),
        "regimes": {"short": f"lead == {F.SHORT_LEAD}", "long": f"lead > {F.SHORT_LEAD}"},
        "rounds": {r: {str(a): n for a, n in e["rounds"].items()} for r, e in fitted.items()},
        "target": "(price - shrunk level) / scale; scale = last week's mean, at least "
                  f"{F.SCALE_FLOOR:g} EUR/MWh",
        "weather": (
            "Open-Meteo Previous Runs, newest forecast for every hour"
            if use_newest else
            "Open-Meteo Previous Runs: the forecast made `lead` days before the hour"
        ),
        "half_life_days": HALF_LIFE_DAYS,
        "params": {k: v for k, v in PARAMS.items() if k not in ("verbose", "num_threads")},
        "walk_forward": report,
    }
    (ARTIFACTS_DIR / "meta_v4.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compare-v3", action="store_true", help="refit v3 on the same folds")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = train_and_save(compare_v3=args.compare_v3)
    wf = result["walk_forward"]
    print(json.dumps({k: wf[k] for k in ("overall", "by_lead", "by_zone", "by_zone_last_60_days", "newest_weather_better")}, indent=1))
