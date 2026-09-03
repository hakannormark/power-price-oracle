"""The model contract. A new model is one file implementing this plus a registry line."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..config import HORIZON_HOURS, RESOLUTION
from ..store import r3
from ..timeutil import horizon_hours, iso, start_of_day

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

# Prices below this are not physically meaningful for a day-ahead quantile.
MIN_PRICE_EUR_MWH = -50.0


@dataclass
class ForecastPoint:
    ts: datetime
    zone: str
    p10: float
    p50: float
    p90: float

    def as_row(self, model_id: str, issued_at: datetime, run_id: str) -> dict:
        return {
            "issued_at": iso(issued_at),
            "model_id": model_id,
            "zone": self.zone,
            "ts": iso(self.ts),
            "horizon_h": horizon_hours(issued_at, self.ts),
            "p10": r3(self.p10),
            "p50": r3(self.p50),
            "p90": r3(self.p90),
            "resolution": RESOLUTION,
            "run_id": run_id,
        }


@runtime_checkable
class ForecastModel(Protocol):
    id: str
    name_sv: str
    description_sv: str
    quantiles: bool
    derived: bool

    def predict(self, features: "pd.DataFrame", issued_at: datetime) -> list[ForecastPoint]:
        ...


def target_window(issued_at: datetime) -> tuple[datetime, datetime]:
    """Every model forecasts the same span: start of today through +168 h."""
    return start_of_day(issued_at), issued_at + timedelta(hours=HORIZON_HOURS)


def order_quantiles(p10: float, p50: float, p90: float) -> tuple[float, float, float]:
    """Enforce p10 <= p50 <= p90 and the negative-price floor."""
    p50 = max(float(p50), MIN_PRICE_EUR_MWH)
    p10 = max(min(float(p10), p50), MIN_PRICE_EUR_MWH)
    p90 = max(float(p90), p50)
    return p10, p50, p90
