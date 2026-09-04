"""Model registry. Adding a model is one new file plus one line in BASE_MODELS."""

from __future__ import annotations

from .ensemble import Ensemble
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
]

MODELS = [*BASE_MODELS, *DERIVED_MODELS]

# Chosen by measurement, not by taste. Over 82 576 out-of-sample hours across
# ten quarters (src/research/backtest.py):
#   seasonal_naive          MAE 29.51    0.0 %
#   weather_scaled          MAE 28.04   +5.0 %
#   ensemble (0.35/0.65)    MAE 28.40   +3.8 %
#   shrunk_scaled           MAE 25.68  +13.3 %
#   recency_scaled          MAE 24.07  +18.7 %
# Every blend of the leader with the others scored worse than the leader alone,
# so the default is a single model rather than a mixture.
DEFAULT_MODEL_ID = "recency_scaled"
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
