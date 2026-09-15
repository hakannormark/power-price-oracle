"""Model registry. Adding a model is one new file plus one line in BASE_MODELS."""

from __future__ import annotations

from .ensemble import Ensemble
from .market_scaled import MarketScaled
from .official import Official
from .recency_scaled import RecencyScaled
from .seasonal_naive import SeasonalNaive
from .shrunk_scaled import ShrunkScaled
from .weather_scaled import WeatherScaled

# Models the pipeline runs directly, in order.
BASE_MODELS = [
    SeasonalNaive(),
    WeatherScaled(),
    ShrunkScaled(),
    RecencyScaled(),
]

# Derived models are built from base-model output after the base pass.
DERIVED_MODELS = [
    Ensemble(),
    MarketScaled(),  # measured live from 2026-09; no futures history to back-test
]

MODELS = [*BASE_MODELS, *DERIVED_MODELS]

# Chosen by measurement, not by taste. Over 437 224 out-of-sample hours across
# sixteen quarters (src/research/backtest.py --issue-every 2):
#   seasonal_naive          MAE 29.87    0.0 %
#   weather_scaled          MAE 28.34   +5.1 %
#   ensemble (0.35/0.65)    MAE 28.70   +3.9 %
#   shrunk_scaled           MAE 25.94  +13.2 %   <- default
#
# Live scoring is the only measurement that applies the auction cutoff to real
# issued forecasts, and it is lower everywhere because the back-test feeds models
# ERA5 reanalysis. In September 2026, over 16 724 scored hours, shrunk_scaled
# averaged 41.9 EUR/MWh against 45.2 for the reference — 7.4 % better, not 13 %.
#
# No per-model live table is frozen here on purpose: the last one in this comment
# read 16.8 / 18.9 / 33.8 for the first three horizons and had drifted to
# 39.1 / 40.0 / 41.9 without anyone noticing. api/v1/accuracy.json carries those
# numbers and keeps them current.
#
# recency_scaled was briefly the default on the strength of a back-test that did
# not apply the cutoff, and so credited it for hours whose price the exchange had
# already published. Live it is the worst of the five. See src/research/backtest.py.
DEFAULT_MODEL_ID = "shrunk_scaled"
REFERENCE_MODEL_ID = "seasonal_naive"  # skill is measured against this one

OFFICIAL = Official()


def model_ids() -> list[str]:
    return [model.id for model in MODELS]


def get_model(model_id: str):
    for model in MODELS:
        if model.id == model_id:
            return model
    raise KeyError(model_id)


def describe_models() -> list[dict]:
    """Payload for api/v1/models.json and modeller.html."""
    return [
        {
            "id": model.id,
            "name_sv": model.name_sv,
            "description_sv": model.description_sv,
            "quantiles": bool(model.quantiles),
            "derived": bool(model.derived),
            "is_default": model.id == DEFAULT_MODEL_ID,
            "is_reference": model.id == REFERENCE_MODEL_ID,
        }
        for model in MODELS
    ]
