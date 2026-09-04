"""Model registry. Adding a model is one new file plus one line in BASE_MODELS."""

from __future__ import annotations

from .ensemble import Ensemble
from .official import Official
from .seasonal_naive import SeasonalNaive
from .shrunk_scaled import ShrunkScaled
from .weather_scaled import WeatherScaled

# Models the pipeline runs directly, in order.
BASE_MODELS = [
    SeasonalNaive(),
    WeatherScaled(),
    ShrunkScaled(),
]

# Derived models are built from base-model output after the base pass.
DERIVED_MODELS = [
    Ensemble(),
]

MODELS = [*BASE_MODELS, *DERIVED_MODELS]

DEFAULT_MODEL_ID = "ensemble"
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
